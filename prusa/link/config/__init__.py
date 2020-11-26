"""Config class definition."""

from logging import getLogger, Formatter, StreamHandler
from logging.handlers import SysLogHandler
from os import getuid
from os.path import abspath, join
from pwd import getpwnam, getpwuid

from extendparser.get import Get

from .. import __application__

LOG_FORMAT_FOREGROUND = \
    "%(asctime)s %(levelname)s {%(module)s.%(funcName)s():%(lineno)d} "\
    "[%(threadName)s]: %(message)s "
LOG_FORMAT_SYSLOG = \
    "%(name)s[%(process)d]: "\
    "%(levelname)s: %(message)s {%(funcName)s():%(lineno)d}"

logger = getLogger('prusa-link')
log_adapter = getLogger('prusa-link.adapter')
log_http = getLogger('prusa-link.http')

# pylint: disable=too-many-ancestors
# pylint: disable=too-many-instance-attributes


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
    """Prusa Link Web Config."""
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
                ("data_dir", str, ''),  # user_data_dir by default
                ("pid_file", str, "/var/run/prusa-link/prusa-link.pid"),
                ("user", str, "pi"),
                ("group", str, "pi"),
            )))
        if args.foreground:
            pwd = getpwuid(getuid())
            self.daemon.user = pwd.pw_name
            self.daemon.home = pwd.pw_dir
        else:
            self.daemon.home = getpwnam(self.daemon.user).pw_dir

        if not self.daemon.data_dir:
            self.daemon.data_dir = join(self.daemon.home,
                                        f'.local/share/{__application__}')

        if args.pidfile:
            self.daemon.pid_file = abspath(args.pidfile)
        self.daemon.pid_file = abspath(join(self.daemon.data_dir,
                                            self.daemon.pid_file))

        # [logging]
        self.set_logger(args)

        # [http]
        self.http = Model(self.get_section(
            "http",
            (
                ("address", str, "0.0.0.0"),
                ("port", int, 8080),
                ("type", str, "threading"),
                # relative to user_conf_dir
                ("digest", str, "./passwd.digest"),
                ("api_keys", str, "./api_keys")
            )))

        if args.address:
            self.http.address = args.address
        if args.port:
            self.http.port = args.port
        self.http.digest = abspath(join(self.daemon.home,
                                        f'.config/{__application__}',
                                        self.http.digest))
        self.http.api_keys = abspath(join(self.daemon.home,
                                          f'.config/{__application__}',
                                          self.http.api_keys))

        # [printer]
        self.printer = Model(self.get_section(
            "printer",
            (
                ("port", str, "/dev/ttyAMA0"),
                ("baudrate", int, 115200),
                ("serial_file", str, "./serial_file")
            )))

        self.printer.serial_file = abspath(join(self.daemon.home,
                                                f'.config/{__application__}',
                                                self.printer.serial_file))

        # [connect]
        self.connect = Model.get(
            self, "connect",
            (
                ("config", str, "/boot/lan_settings.ini"),
                ("mountpoints", tuple, [], ':'),
                # relative to HOME
                ("directories", tuple, ("./Prusa Link gcodes",), ':')
            ))
        self.connect.config = abspath(self.connect.config)
        self.connect.directories = tuple(
            abspath(join(self.daemon.home, item))
            for item in self.connect.directories)

        Config.instance = self

    def get_logger(self, name, args, fallback="WARNING"):
        """Set specific logger value"""
        if args.debug:
            log_level = "DEBUG"
        elif args.info:
            log_level = "INFO"
        else:
            log_level = self.get("logging", name, fallback=fallback)
            check_log_level(log_level)

        if name == 'main':
            logger_ = getLogger('prusa-link')
            logger_.setLevel(log_level)
        else:
            getLogger(f'prusa-link.{name}').setLevel(log_level)

    def set_logger(self, args):
        """Logger setting is more complex."""

        self.get_logger('main', args)
        self.get_logger('adapter', args)
        self.get_logger('http', args, 'INFO')  # http requests

        if args.foreground:
            log_format = LOG_FORMAT_FOREGROUND
            handler = StreamHandler()
        else:
            log_format = LOG_FORMAT_SYSLOG
            log_syslog = self.get("logging", "syslog", fallback="/dev/log")
            handler = SysLogHandler(log_syslog, SysLogHandler.LOG_DAEMON)

        log_format = self.get("logging", "format", fallback=log_format)

        for hdlr in logger.root.handlers:  # reset root logger handlers
            logger.root.removeHandler(hdlr)
        logger.root.addHandler(handler)
        formatter = Formatter(log_format)
        handler.setFormatter(formatter)
