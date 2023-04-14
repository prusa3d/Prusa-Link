"""The module for starting the PrusaLink instance manager components"""
import argparse
import logging
import os

import pwd
import stat
import sys

from daemon import DaemonContext  # type: ignore
from lockfile import AlreadyLocked  # type: ignore
from lockfile.pidlockfile import PIDLockFile  # type: ignore

from .multi_instance import ConfigComponent, InstanceController, \
    COMMS_PIPE_PATH, MultiInstanceConfig
from .web import run_multi_instance_server
from ..__main__ import stop

log = logging.getLogger(__name__)


DEFAULT_UID = 1000  # Default user UID
PID_FILE_PATH = "/var/run/prusalink-manager-web.pid"


def get_user_info(username=None):
    """Gets user info either using the default UID or the suplied username"""
    if username is not None:
        try:
            return pwd.getpwnam(username)
        except KeyError:
            log.error("Could not get user info for %s. Exiting...",
                      username)
            sys.exit(1)
    else:
        try:
            return pwd.getpwuid(DEFAULT_UID)
        except KeyError:
            log.error("Could not get user info for uid %s. Exiting...",
                      DEFAULT_UID)
            sys.exit(1)


def main():
    """The main function for the PrusaLink instance manager.
    Parses command-line arguments and runs the instance controller"""
    parser = argparse.ArgumentParser(
        description="Multi instance suite for PrusaLink")

    parser.add_argument("-i",
                        "--info",
                        action="store_true",
                        help="include log messages up to the INFO level")
    parser.add_argument("-d",
                        "--debug",
                        action="store_true",
                        help="include log messages up to the INFO level")

    subparsers = parser.add_subparsers(dest="command",
                                       help="Available commands")

    # Create a subparser for the start_daemon command
    start_parser = subparsers.add_parser(
        "start",
        help="Start the instance managing daemon (needs root privileges)")
    start_parser.add_argument(
        "-u", "--username", required=False,
        help="Which users to use for running and storing everything")

    subparsers.add_parser(
        "stop",
        help="Stop any manager daemon running (needs root privileges)")

    subparsers.add_parser(
        "clean",
        help="Danger! cleans all PrusaLink multi instance configuration")

    # Create a subparser for the printer_connected command
    subparsers.add_parser(
        "rescan",
        help="Notify the daemon a printer has been connected")

    args = parser.parse_args()

    log_level = logging.WARNING
    if args.info:
        log_level = logging.INFO
    if args.debug:
        log_level = logging.DEBUG

    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)]
    )

    if args.command == "start":
        run_manager(args.username)
    if args.command == "stop":
        stop_manager()
    elif args.command == "clean":
        ConfigComponent.clear_configuration()
    elif args.command == "rescan":
        rescan()
    else:
        parser.print_help()


def rescan():
    """Notify the manager that a connection has been established
    by writing "connected" to the communication pipe."""
    if not stat.S_ISFIFO(os.stat(COMMS_PIPE_PATH).st_mode):
        print("Cannot communicate to manager. Missing named pipe")
        sys.exit(1)
    try:
        file_descriptor = os.open(path=COMMS_PIPE_PATH,
                                  flags=os.O_WRONLY | os.O_NONBLOCK)
        with open(file_descriptor, "w", encoding="UTF-8") as file:
            file.write("rescan")
    except BlockingIOError:
        log.exception("No one is reading from the pipe, exiting.")
        sys.exit(1)
    except OSError:
        log.exception("An error occurred trying to write to the pipe.")
        sys.exit(1)


def run_manager(username):
    """Runs the instance manager"""
    pid_file = PIDLockFile(PID_FILE_PATH)
    if pid_file.is_locked():
        log.error("Error - manager already running")
        sys.exit(1)

    user_info = get_user_info(username)

    child_pid = start_web_server(user_info)

    instance_controller = InstanceController(user_info=user_info)
    instance_controller.load_all()
    instance_controller.run()

    if child_pid is not None:
        # Kill the web server if the manager exits
        stop(child_pid)


def stop_manager():
    """Stops the instance manager"""
    pid_file = PIDLockFile(PID_FILE_PATH)
    if not pid_file.is_locked():
        log.warning("Manager not running")
        sys.exit(0)
    stop(pid_file.pid)


def get_logger_file_descriptors():
    """Get the file descriptors for all loggers"""
    file_descriptors = []
    for handler in logging.root.handlers:
        if hasattr(handler, "socket"):
            file_descriptors.append(handler.socket.fileno())
        if hasattr(handler, "stream"):
            file_descriptors.append(handler.stream.fileno())
    return file_descriptors


def start_web_server(user_info):
    """Start the web server as a daemon"""
    child_pid = os.fork()
    if child_pid > 0:
        return child_pid

    context = DaemonContext(
        uid=user_info.pw_uid,
        gid=user_info.pw_gid,
        # pidfile=PIDLockFile(PID_FILE_PATH),  # does break stuff
        files_preserve=get_logger_file_descriptors()
    )

    try:
        with context:
            config = MultiInstanceConfig()
            run_multi_instance_server(config.web.port_range_start)
    except AlreadyLocked:
        log.exception("Error - web server already running")
    except Exception:  # pylint: disable=broad-except
        log.exception("Error - could not start web server")

    return None
