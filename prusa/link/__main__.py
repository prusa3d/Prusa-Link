"""main() command line function."""
import logging
import sys
import threading
from argparse import ArgumentParser, ArgumentTypeError
from cProfile import Profile
from grp import getgrnam
from os import chmod, geteuid, kill, mkdir, path
from pwd import getpwnam
from signal import SIGKILL, SIGTERM
from time import sleep

from daemon import DaemonContext  # type: ignore
from lockfile.pidlockfile import PIDLockFile  # type: ignore

from prusa.connect.printer import __version__ as sdk_version

from . import __version__ as link_version
from .config import Config
from .const import EXIT_TIMEOUT
from .interesting_logger import InterestingLogger, InterestingLogRotator
from .printer_adapter.updatable import Thread

# pylint: disable=wrong-import-position, wrong-import-order
# Pop this singleton into existence before importing prusalink
InterestingLogRotator()
logging.setLoggerClass(InterestingLogger)

from .daemon import Daemon  # noqa: E402

log = logging.getLogger(__name__)

# pylint: disable=too-many-return-statements
# pylint: disable=too-many-statements
CONFIG_FILE = '/etc/prusalink/prusalink.ini'


def excepthook(exception_arguments, args, argv):
    """If running as a daemon, restarts the app on unhandled exceptions"""
    assert exception_arguments is not None
    InterestingLogRotator.trigger("exception in a thread")
    log.exception("Caught an exception at top level!")
    if args is None:
        log.fatal("Exception during startup, cannot restart")
    if args.foreground:
        log.fatal("This instance is now broken. Will not restart "
                  "because we're running in the foreground mode")
    else:
        log.warning("Caught unhandled exception, restarting PrusaLink")
        Daemon.restart(argv)
    # excepthook has the global exception set, besides even if we failed
    # here, it will literally affect nothing
    # pylint: disable=misplaced-bare-raise
    # ruff: noqa: PLE0704
    raise


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


def wait_process(pid, timeout=1):
    """Wait for process with timeout. Return True if process was terminated."""
    sleep_amount = 0.1
    for _ in range(int(timeout / sleep_amount)):
        if not check_process(pid):
            return True
        sleep(sleep_amount)
    return False


def stop(pid):
    """Tries to stop PrusaLink nicely, if it times out, uses SIGKILL"""
    kill(pid, SIGTERM)
    if wait_process(pid, EXIT_TIMEOUT):
        return

    log.warning("Failed to stop - SIGKIL will be used!")
    try:
        kill(pid, SIGKILL)
    except ProcessLookupError:
        log.warning("Could not find a process with pid %s to kill", pid)
    wait_process(pid, EXIT_TIMEOUT)


def main():
    """Standard main function."""
    # pylint: disable=too-many-branches
    parser = ArgumentParser(prog="prusalink", description="PrusaLink daemon.")
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
                        help=f"path to config file (default: {CONFIG_FILE})",
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
                        help="Serial (printer's) port or 'auto'",
                        metavar="<PORT>")
    parser.add_argument("-n",
                        "--printer-number",
                        type=int,
                        help="Multi-instance printer number to show in wizard")
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
    parser.add_argument("--profile",
                        action="store_true",
                        help="Use cProfile for profiling application.")
    parser.add_argument("--version",
                        action="store_true",
                        help="Print out version info and exit")

    argv = list(arg for arg in sys.argv[1:] if arg not in ('start', 'restart'))
    args = parser.parse_args()

    if args.version:
        print("PrusaLink version:", link_version)
        print("PrusaConnect-SDK version:", sdk_version)
        return 0

    profile = None
    if args.profile:
        profile = Profile()
        profile.enable()
        Thread.enable_profiling()

    # Restart on thread exceptions
    threading.excepthook = lambda exc_args: excepthook(exc_args, args, argv)

    try:
        config = Config(args)

        set_log_levels(config)

        pid_file = PIDLockFile(config.daemon.pid_file)
        pid = pid_file.read_pid() if pid_file.is_locked() else None

        if args.command == "stop":
            if pid and check_process(pid):
                print("Stopping service with pid", pid)
                stop(pid)
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
                stop(pid)

        elif args.command == "start":
            pass
        elif not args.foreground:
            parser.error("Unknown command %s")
            return 1

        daemon = Daemon(config, argv)
        if args.foreground:
            log.info("Starting service on foreground.")
            return daemon.run(False)

        if pid:
            if not check_process(pid):
                pid_file.break_lock()
            else:
                print("Service is already running")
                return 1

        files_preserve = []
        for handler in logging.root.handlers:
            if hasattr(handler, "socket"):
                files_preserve.append(handler.socket.fileno())
        context = DaemonContext(pidfile=pid_file,
                                files_preserve=files_preserve,
                                signal_map={SIGTERM: daemon.sigterm})

        pid_dir = path.dirname(config.daemon.pid_file)
        if pid_dir == '/var/run/prusalink' and not path.exists(pid_dir):
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
        parser.error(f"{exc}")
        return 1

    finally:
        if profile:
            profile.disable()
            profile.dump_stats("prusalink-__main__.profile")


if __name__ == "__main__":
    sys.exit(main())
