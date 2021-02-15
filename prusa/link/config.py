"""Config class definition."""
import logging
from logging import Formatter, StreamHandler
from logging.handlers import SysLogHandler
from os import getuid
from os.path import abspath, join
from pwd import getpwnam, getpwuid
from typing import Iterable

from extendparser.get import Get

CONNECT = 'test.connect.prusa3d.com'

LOG_FORMAT_FOREGROUND = \
    "%(asctime)s %(levelname)s {%(module)s.%(funcName)s():%(lineno)d} "\
    "[%(threadName)s]: %(message)s "
LOG_FORMAT_SYSLOG = \
    "%(name)s[%(process)d]: "\
    "%(levelname)s: %(message)s {%(funcName)s():%(lineno)d}"

# pylint: disable=too-many-ancestors
# pylint: disable=too-many-instance-attributes


def get_log_level_dict(log_levels: Iterable[str]):
    log_level_dict = {}
    for log_config in log_levels:
        parts = log_config.split("=")
        if len(parts) != 2:
            raise ValueError("Log level settings needs to contain exactly one "
                             "\"=\"")
        name, loglevel = parts
        log_level_dict[name] = loglevel
    return log_level_dict

def check_log_level(value):
    """Check valid log level."""
    if value not in ("CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"):
        raise ValueError("Invalid value %s" % value)


def check_server_type(value):
    """Check valid server class"""
    if value not in ("single", "threading", "forking"):
        raise ValueError("Invalid value %s" % value)


class Model(dict):
    """Config model based on dictionary.

    It simple implement set and get attr methods.
    """
    def __getattr__(self, key):
        try:
            return self[key]
        except KeyError as err:
            raise AttributeError(err) from err

    def __setattr__(self, key, val):
        self[key] = val

    @staticmethod
    def get(cfg, name, options):
        return Model(cfg.get_section(name, options))


class Config(Get):
    """This class handles prusa-link.ini configuration file."""
    instance = None

    def __init__(self, args):
        if Config.instance is not None:
            raise RuntimeError('Config is singleton')

        super().__init__()

        self.read(args.config)
        self.debug = args.debug

        # [daemon]
        self.daemon = Model(self.get_section(
            "daemon",
            (
                ("data_dir", str, ''),  # user home by default
                ("pid_file", str, "./prusa-link.pid"),
                ("current_file", str, "./currently_printing.gcode"),
                ("power_panic_file", str, "./power_panic"),
                ("job_file", str, "./job_data.json"),
                ("threshold_file", str, "./threshold.data"),
                ("user", str, "pi"),
                ("group", str, "pi"),
            )))
        if args.foreground or getuid() != 0:
            pwd = getpwuid(getuid())
            self.daemon.user = pwd.pw_name
            self.daemon.home = pwd.pw_dir
        else:
            self.daemon.home = getpwnam(self.daemon.user).pw_dir

        if not self.daemon.data_dir:
            self.daemon.data_dir = self.daemon.home

        if args.pidfile:
            self.daemon.pid_file = abspath(args.pidfile)

        for file_ in ('pid_file', 'current_file', 'power_panic_file',
                'job_file', 'threshold_file'):
            setattr(self.daemon, file_,
                    abspath(join(self.daemon.data_dir,
                                 getattr(self.daemon, file_))))

        # [logging]
        self.set_global_log_level(args)

        # Let's combine the config log setting and cmd args
        # with cmd args overriding config values
        self.log_settings = {}
        if "log" in self:
            for module_name, log_level in self["log"].items():
                check_log_level(log_level)
                self.log_settings[module_name] = log_level

        if args.module_log_level is not None:
            override_log_settings = get_log_level_dict(args.module_log_level)
            self.log_settings.update(override_log_settings)

        # Let's save the handler we've configured for later use
        self.configured_handler = self.get_log_handler(args)

        # [http]
        self.http = Model(self.get_section(
            "http",
            (
                ("address", str, "0.0.0.0"),
                ("port", int, 8080),
                ("link_info", bool, False),
            )))

        if args.address:
            self.http.address = args.address
        if args.tcp_port:
            self.http.port = args.tcp_port
        if args.link_info:
            self.http.link_info = args.link_info

        # [printer]
        self.printer = Model(self.get_section(
            "printer",
            (
                ("port", str, "/dev/ttyAMA0"),
                ("baudrate", int, 115200),
                ("serial_file", str, "./serial_file"),
                ("settings", str, "./prusa_printer_settings.ini"),
                ("mountpoints", tuple, [], ':'),
                # relative to HOME
                ("directories", tuple, ("./Prusa Link gcodes",), ':'),
            )))
        if args.serial_port:
            self.printer.port = args.serial_port

        self.printer.serial_file = abspath(join(self.daemon.data_dir,
                                                self.printer.serial_file))
        self.printer.settings = abspath(join(self.daemon.data_dir,
                                             self.printer.settings))
        self.printer.directories = tuple(
            abspath(join(self.daemon.data_dir, item))
            for item in self.printer.directories)

        Config.instance = self

    def set_global_log_level(self, args):
        if args.debug:
            log_level = "DEBUG"
        elif args.info:
            log_level = "INFO"
        else:
            log_level = logging.root.level

        logging.root.setLevel(log_level)
        logging.getLogger("urllib3").setLevel(log_level)
        logging.getLogger("connect-printer").setLevel(log_level)  # FIXME

    def get_log_handler(self, args):
        """Logger setting are more complex."""

        if args.foreground:
            log_format = LOG_FORMAT_FOREGROUND
            configured_handler = StreamHandler()
        else:
            log_format = LOG_FORMAT_SYSLOG
            log_syslog = self.get("logging", "syslog", fallback="/dev/log")
            configured_handler = SysLogHandler(log_syslog,
                                               SysLogHandler.LOG_DAEMON)

        log_format = self.get("logging", "format", fallback=log_format)

        for handler in logging.root.handlers:  # reset root logger handlers
            logging.root.removeHandler(handler)
        logging.root.addHandler(configured_handler)
        formatter = Formatter(log_format)
        configured_handler.setFormatter(formatter)
        return configured_handler


class Settings(Get):
    """This class handles prusa_printer_settings.ini configuration file.

    File prusa_printer_settings.ini is official Prusa settings file, which has
    shared format between all printers, and Prusa Connect can generate it.
    """
    instance = None

    def __init__(self, settings_file):
        if Settings.instance is not None:
            raise RuntimeError('Config is singleton')

        super().__init__()

        self.read(settings_file)

        # [printer]
        self.printer = Model(self.get_section(
            'printer',
            (
                ('type', str, 'MK3'),
                ('name', str, ''),
                ('location', str, '')
            )))
        if self.printer.type != 'MK3':
            raise ValueError("Settings file for different printer!")

        # [network]
        self.network = Model(self.get_section(
            'network',
            (
                ('hostname', str, ''),
            )))

        # [service::connect]
        self.service_connect = Model(self.get_section(
            'service::connect',
            (
                ('hostname', str, CONNECT),
                ('tls', int, 1),
                ('port', int, 0),  # 0 means 443 with tls, or 80 without tls
                ('token', str, '')
            )))

        # [service::local]
        self.service_local = Model(self.get_section(
            'service::local',
            (
                ('enable', int, 1),
                ('username', str, ''),
                ('password', str, ''),
                ('api_key', str, '')
            )))

        Settings.instance = self

    def set_section(self, name, model):
        """Set section from model"""
        if name not in self:
            self.add_section(name)
        for key, val in model.items():
            self.set(name, key, str(val))

    def update(self):
        self.set_section('printer', self.printer)
        self.set_section('network', self.network)
        self.set_section('service::connect', self.service_connect)
        self.set_section('service::local', self.service_local)
