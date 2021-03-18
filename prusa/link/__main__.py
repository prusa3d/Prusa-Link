"""main() command line function."""
import logging
import subprocess
import threading
import sys
from argparse import ArgumentParser, ArgumentTypeError
from os import kill, geteuid, path, mkdir, chmod
from grp import getgrnam
from pwd import getpwnam
from signal import SIGTERM, SIGKILL
from time import time, sleep

from daemon import DaemonContext  # type: ignore
from lockfile.pidlockfile import PIDLockFile  # type: ignore

from .printer_adapter.const import EXIT_TIMEOUT, QUIT_INTERVAL
from .config import Config
from .daemon import Daemon

log = logging.getLogger(__name__)

# pylint: disable=too-many-return-statements
# pylint: disable=too-many-statements
CONFIG_FILE = '/etc/Prusa-Link/prusa-link.ini'


def excepthook(exception_arguments, args):
    """If running as a daemon, restarts the app on unhandled exceptions"""
    log.error(exception_arguments.exc_value)
    if args is None:
        log.fatal("Exception during startup, cannot restart")
    if args.foreground:
        log.fatal("This instance is now broken. Will not restart "
                  "because we're running in the foreground mode")
    else:
        log.warning("Caught unhandled exception, restarting Prusa Link")
        subprocess.Popen(["prusa-link", "restart"], stdin=sys.stdin)


def set_log_levels(config: Config):
    """Set log level for each defined module."""
    for module, level in config.log_settings.items():
        logging.getLogger(module).setLevel(level)


class LogLevel(str):
    """Log level type with __call__ checker method."""
    def __new__(cls, level):
        if len(level.split("=")) != 2:
            raise ArgumentTypeError("log level needs to be specified in format"
                                    "<module_path>=<log_level>")
        return super().__new__(cls, level)


def check_process(pid):
    """Check if process with pid is alive."""
    try:
        kill(pid, 0)
        return True
    except OSError:
        return False


def main():
    """Standard main function."""
    # pylint: disable=too-many-branches
    parser = ArgumentParser(prog="prusa-link",
                            description="Prusa Link daemon.")
    parser.add_argument(
        "command",
        nargs='?',
        default="start",
        type=str,
        help="daemon action (start|stop|restart|status) (default: start)")
    parser.add_argument("-f",
                        "--foreground",
                        action="store_true",
                        help="run as script on foreground")
    parser.add_argument("-c",
                        "--config",
                        default=CONFIG_FILE,
                        type=str,
                        help="path to config file (default: %s)" % CONFIG_FILE,
                        metavar="<file>")
    parser.add_argument("-p",
                        "--pidfile",
                        type=str,
                        help="path to pid file",
                        metavar="<FILE>")
    parser.add_argument("-a",
                        "--address",
                        type=str,
                        help="IP listening address (host or IP)",
                        metavar="<ADDRESS>")
    parser.add_argument("-t",
                        "--tcp-port",
                        type=int,
                        help="TCP/IP listening port",
                        metavar="<PORT>")
    parser.add_argument("-I",
                        "--link-info",
                        action="store_true",
                        help="/link-info debug page")
    parser.add_argument("-s",
                        "--serial-port",
                        type=str,
                        help="Serial (printer's) port",
                        metavar="<PORT>")
    parser.add_argument("-i",
                        "--info",
                        action="store_true",
                        help="more verbose logging level INFO is set")
    parser.add_argument("-d",
                        "--debug",
                        action="store_true",
                        help="DEBUG logging level is set")
    parser.add_argument("-l",
                        "--module-log-level",
                        action="append",
                        help="sets the log level of any submodule(s). "
                        "use <module_path>=<log_level>",
                        type=LogLevel)

    args = parser.parse_args()

    # Restart on thread exceptions
    threading.excepthook = lambda exc_args: excepthook(exc_args, args)

    try:
        config = Config(args)

        set_log_levels(config)

        pid_file = PIDLockFile(config.daemon.pid_file)
        pid = pid_file.read_pid() if pid_file.is_locked() else None

        if args.command == "stop":
            if pid and check_process(pid):
                print("Stopping service with pid", pid)
                kill(pid, SIGTERM)
            else:
                print("Service not running")
            return 0

        if args.command == "status":
            if pid and check_process(pid):
                print("Service running with pid", pid)
                return 0
            print("Service not running")
            return 1

        if args.command == "restart":
            if pid and check_process(pid):
                print("Restarting service with pid", pid)
                kill(pid, SIGTERM)
                timeout_at = time() + EXIT_TIMEOUT
                while time() <= timeout_at:
                    if not check_process(pid):
                        break
                    sleep(QUIT_INTERVAL)

                # If we timed out, kill the process
                if time() >= timeout_at:
                    log.warning("Failed to stop - SIGKIL will be used!")
                    try:
                        kill(pid, SIGKILL)
                    except ProcessLookupError:
                        log.warning(
                            "Could not find a prcess with pid %s "
                            "to kill", pid)
                    else:
                        # Give the OS some time
                        sleep(1)

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
                print("Service is already running")
                return 1

        context = DaemonContext(pidfile=pid_file,
                                stdout=daemon.stdout,
                                stderr=daemon.stderr,
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
            log.info("Starting service with pid %d", pid_file.read_pid())
            retval = daemon.run()
            log.info("Shutdown")
            return retval

    except Exception as exc:  # pylint: disable=broad-except
        log.info("%s", args)
        log.exception("Unhandled exception reached the top level")
        parser.error("%s" % exc)
        return 1


if __name__ == "__main__":

    sys.exit(main())
