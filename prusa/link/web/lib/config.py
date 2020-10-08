"""Config class definition."""

from configparser import ConfigParser
from logging import getLogger, Formatter, StreamHandler
from logging.handlers import SysLogHandler
from os.path import abspath

from . import classes
from .. import __package__ as package

LOG_FORMAT_FOREGROUND = \
    "%(asctime)s %(levelname)s: %(name)s: %(message)s "\
    "{%(funcName)s():%(lineno)d}"
LOG_FORMAT_SYSLOG = \
    "%(name)s[%(process)d]: %(levelname)s: %(message)s {%(funcName)s():%(lineno)d}"

logger = getLogger(package)

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


class Config(ConfigParser):
    """Prusa Link Web Config."""

    def __init__(self, args):
        super().__init__()

        if args.config:
            self.read(args.config)

        # [daemon]
        self.data_dir = self.get(
            "daemon", "data_dir", fallback="/var/lib/prusa-link")

        if args.pidfile:
            self.pid_file = abspath(args.pidfile)
        else:
            self.pid_file = self.get(
                "daemon", "pid_file", fallback="/var/run/prusa-link/web.pid")
        self.user = self.get("daemon", "user", fallback="nobody")
        self.group = self.get("daemon", "group", fallback="nogroup")

        # [logging]
        if args.debug:
            self.log_level = "DEBUG"
        elif args.info:
            self.log_level = "INFO"
        else:
            self.log_level = self.get("logging", "level", fallback="WARNING")
            check_log_level(self.log_level)

        logger.setLevel(self.log_level)

        if args.foreground:
            log_format=LOG_FORMAT_FOREGROUND
            handler = StreamHandler()
        else:
            log_format=LOG_FORMAT_SYSLOG
            self.log_syslog = self.get("logging", "syslog", fallback="/dev/log")
            handler = SysLogHandler(self.log_syslog, SysLogHandler.LOG_DAEMON)

        self.log_format = self.get("logging", "format",fallback=log_format)

        formatter = Formatter(self.log_format)
        handler.setFormatter(formatter)
        logger.addHandler(handler)

        # [http]
        if args.address:
            self.address = args.address
        else:
            self.address = self.get("http", "address", fallback="127.0.0.1")
        if args.port:
            self.port = args.port
        else:
            self.port = self.getint("http", "port", fallback=8080)

        self.type = self.get("http", "type", fallback="threading")
        check_server_type(self.type)

        if self.type == "single":
            self.klass = classes.SingleServer
        elif self.type == "forking":
            self.klass = classes.ForkingServer
        elif self.type == "threading":
            self.klass = classes.ThreadingServer
        else:
            raise ValueError("Bad http type: %s" % self.type)
