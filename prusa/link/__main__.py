"""main() command line function."""
import logging
from argparse import ArgumentParser, ArgumentTypeError
from traceback import format_exc
from os import kill, geteuid, path, mkdir, chmod
from grp import getgrnam
from pwd import getpwnam
from signal import SIGTERM

from daemon import DaemonContext
from lockfile.pidlockfile import PIDLockFile

from prusa.link.printer_adapter.util import get_syslog_handler_fileno
from .config import Config
from .daemon import Daemon

log = logging.getLogger(__name__)


# pylint: disable=too-many-return-statements
# pylint: disable=too-many-statements
CONFIG_FILE = '/etc/Prusa-Link/prusa-link.ini'

def set_log_levels(config: Config):
    for module, log_level in config.log_settings.items():
        logging.getLogger(module).setLevel(log_level)

def log_level(log_level):
    if len(log_level.split("=")) != 2:
        raise ArgumentTypeError("log level needs to be specified in format"
                                "<module_path>=<log_level>")
    return log_level

def check_process(pid):
    """Check if process with pid is alive."""
    try:
        kill(pid, 0)
        return True
    except OSError:
        return False

def main():
    """Standard main function."""
    parser = ArgumentParser(
        prog="prusa-link",
        description="Prusa Link daemon.")
    parser.add_argument(
        "command", nargs='?', default="start", type=str,
        help="daemon action (start|stop|restart|status) (default: start)")
    parser.add_argument(
        "-f", "--foreground", action="store_true",
        help="run as script on foreground")
    parser.add_argument(
        "-c", "--config", default=CONFIG_FILE, type=str,
        help="path to config file (default: %s)" % CONFIG_FILE,
        metavar="<file>")
    parser.add_argument(
        "-p", "--pidfile", type=str,
        help="path to pid file", metavar="<FILE>")
    parser.add_argument(
        "-a", "--address", type=str,
        help="IP listening address (host or IP)", metavar="<ADDRESS>")
    parser.add_argument(
        "-t", "--tcp-port", type=int,
        help="TCP/IP listening port", metavar="<PORT>")
    parser.add_argument(
        "-s", "--serial-port", type=str,
        help="Serial (printer's) port", metavar="<PORT>")
    parser.add_argument(
        "-i", "--info", action="store_true",
        help="more verbose logging level INFO is set")
    parser.add_argument(
        "-d", "--debug", action="store_true",
        help="DEBUG logging level is set")
    parser.add_argument(
        "-l", "--module-log-level", action="append",
        help="sets the log level of any submodule(s). ",
        type=log_level)

    args = parser.parse_args()

    try:
        config = Config(args)

        set_log_levels(config)

        pid_file = PIDLockFile(config.daemon.pid_file)
        pid = pid_file.read_pid() if pid_file.is_locked() else None

        if args.command == "stop":
            if pid and check_process(pid):
                log.info("Stopping service with pid", pid)
                kill(pid, SIGTERM)
            else:
                log.info("Service not running")
            return 0

        if args.command == "status":
            if pid and check_process(pid):
                log.info("Service running with pid", pid)
                return 0
            log.info("Service not running")
            return 1

        if args.command == "restart":
            if pid and check_process(pid):
                log.info("Restarting service with pid", pid)
                kill(pid, SIGTERM)
        elif args.command == "start":
            pass
        elif not args.foreground:
            parser.error("Unknown command %s")
            return 1

        daemon = Daemon(config)
        if args.foreground:
            log.info("Starting service on foreground.")
            return daemon.run(False)

        if pid:
            if not check_process(pid):
                pid_file.break_lock()
            else:
                log.info("Service is already running")
                return 1

        context = DaemonContext(
            pidfile=pid_file,
            stdout=daemon.stdout,
            stderr=daemon.stderr,
            files_preserve=[get_syslog_handler_fileno(config)],  # Redundant IMO
            signal_map={SIGTERM: daemon.sigterm})

        pid_dir = path.dirname(config.daemon.pid_file)
        if pid_dir == '/var/run/prusa-link' and not path.exists(pid_dir):
            mkdir(pid_dir)
            chmod(pid_dir, 0o777)

        if geteuid() == 0:
            context.initgroups = True  # need only for RPi, don't know why
            context.uid = getpwnam(config.daemon.user).pw_uid
            context.gid = getgrnam(config.daemon.group).gr_gid

        with context:
            log.info(
                "Starting service with pid %d", pid_file.read_pid())
            retval = daemon.run()
            log.info("Shutdown")
            return retval

    except Exception as exc:  # pylint: disable=broad-except
        log.info("%s", args)
        log.debug("%s", format_exc())
        log.exception("%s", exc)
        parser.error("%s" % exc)
        return 1


if __name__ == "__main__":
    import sys

    sys.exit(main())
