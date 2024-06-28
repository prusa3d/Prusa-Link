"""The module for starting PrusaLink Instance Manager components"""
import argparse
import logging
import os
import pwd
import signal
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from logging.handlers import SysLogHandler
from pathlib import Path

from daemon import DaemonContext  # type: ignore
from lockfile.pidlockfile import PIDLockFile  # type: ignore

from ..__main__ import check_process
from ..__main__ import stop as stop_process
from ..config import LOG_FORMAT_SYSLOG, Config, FakeArgs
from ..util import ensure_directory
from .config_component import MultiInstanceConfig
from .const import (
    DEFAULT_UID,
    MANAGER_PID_PATH,
    MULTI_INSTANCE_CONFIG_PATH,
    RUN_DIRECTORY,
    SERVER_PID_PATH,
    UDEV_REFRESH_QUEUE_NAME,
)
from .controller import Controller
from .ipc_queue_adapter import IPCSender
from .web import get_web_server

log = logging.getLogger(__name__)


def main_thread_exception(exc_type, exc_value, exc_traceback):
    """Log unhandled exceptions"""
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc_value, exc_traceback)
        return

    log.exception("Unhandled exception reached top level",
                  exc_info=(exc_type, exc_value, exc_traceback))


def thread_exception(_):
    """Re-raise unhandled exceptions in threads to call sys.excepthook"""
    # ruff: noqa: PLE0704
    raise  # pylint: disable=misplaced-bare-raise


threading.excepthook = thread_exception
sys.excepthook = main_thread_exception


def get_logger_file_descriptors():
    """Get the file descriptors for all loggers"""
    file_descriptors = []
    for handler in logging.root.handlers:
        if hasattr(handler, "socket"):
            file_descriptors.append(handler.socket.fileno())
        if hasattr(handler, "stream"):
            file_descriptors.append(handler.stream.fileno())
    return file_descriptors


class Manager:
    """This class represents the process that runs the controller"""

    pid_file = PIDLockFile(MANAGER_PID_PATH)

    def __init__(self, user_info, prepend_executables_with):
        self.user_info = user_info

        if self.pid_file.is_locked():
            if check_process(self.pid_file.read_pid()):
                print("Manager already running")
                log.error("Manager already running")
                sys.exit(1)

            self.pid_file.break_lock()

        context = DaemonContext(
            pidfile=self.pid_file,
            files_preserve=get_logger_file_descriptors(),
            signal_map={signal.SIGTERM: self._sigterm_handler},
            detach_process=True,
        )

        with context:
            self.controller = Controller(
                user_info=self.user_info,
                prepend_executables_with=prepend_executables_with)
            self.controller.run()

    def _sigterm_handler(self, *_):
        """Stops the controller. Has to return as fast as possible"""
        log.info("Received SIGTERM. Stopping Multi Instance Manager")
        self.controller.stop()


class Server:
    """This class represents the process that runs the web server"""

    pid_file = PIDLockFile(SERVER_PID_PATH)

    def __init__(self, user_info):
        self.user_info = user_info

        self.web_server = None

        if self.pid_file.is_locked():
            if check_process(self.pid_file.read_pid()):
                stop_process(self.pid_file.read_pid())

            self.pid_file.break_lock()

        context = DaemonContext(
            uid=self.user_info.pw_uid,
            gid=self.user_info.pw_gid,
            files_preserve=get_logger_file_descriptors(),
            pidfile=self.pid_file,
            signal_map={signal.SIGTERM: self._sigterm_handler},
            detach_process=True,
        )

        with context:
            config = MultiInstanceConfig()
            self.web_server = get_web_server(config.web.port_range_start)
            self.web_server.start()
            self.web_server.thread.join()

    def _sigterm_handler(self, *_):
        """Stop the web server. Has to return as fast as possible"""

        log.info("Received SIGTERM. Stopping Multi Instance Web Server")
        self.web_server.stop()


def get_username(username=None):
    """Return a valid username, if possible"""
    if username is not None:
        try:
            return pwd.getpwnam(username).pw_name
        except KeyError:
            log.error("Could not find configured user %s. Exiting..",
                      username)
            raise
    else:
        try:
            return pwd.getpwuid(DEFAULT_UID).pw_name
        except KeyError:
            log.error("Could not get user for uid %s. Exiting...",
                      DEFAULT_UID)
            raise


def start(user_info, prepend_executables_with):
    """Starts the instance manager processes"""
    if os.fork() == 0:
        Manager(user_info, prepend_executables_with)
        sys.exit(0)
    if os.fork() == 0:
        Server(user_info)
        sys.exit(0)


def handle_process_stop(pid_file, name="Process", quiet=False):
    """Stops a process handling pid file edge cases"""
    pid = pid_file.read_pid()
    if pid is not None:
        name = f"{name} PID {pid}"

    if pid_file.is_locked() and check_process(pid):
        stop_process(pid)
    else:
        if not quiet:
            print(f"{name} not running")
        log.warning("%s not running", name)


def stop(quiet=False):
    """Stops the instance manager and all PrusaLink instances"""
    multi_instance_config = MultiInstanceConfig()

    stop_thread_count = len(multi_instance_config.printers) + 2

    with ThreadPoolExecutor(max_workers=stop_thread_count) as executor:
        executor.submit(handle_process_stop,
                        Manager.pid_file,
                        "Instance Manager",
                        quiet)
        executor.submit(handle_process_stop,
                        Server.pid_file,
                        "Multi Instance Server",
                        quiet)
        for printer in multi_instance_config.printers:
            config = Config(FakeArgs(path=printer.config_path))
            pid_file = PIDLockFile(Path(config.daemon.data_dir,
                                        config.daemon.pid_file))
            executor.submit(handle_process_stop,
                            pid_file,
                            "PrusaLink instance",
                            quiet)


def clean(user_info, prepend_executables_with):
    """Stops the MultiInstance Manager and removes all printers"""
    stop(quiet=True)
    controller = Controller(user_info, prepend_executables_with)
    controller.remove_all_printers()


def rescan():
    """Notify the manager that a connection has been established
    by writing "connected" to the communication pipe."""
    try:
        IPCSender.send_and_close(UDEV_REFRESH_QUEUE_NAME, "rescan")
    except FileNotFoundError:
        log.error("Cannot communicate to manager. Missing queue")


def main():
    """The main function for the PrusaLink instance manager.
    Parses command-line arguments and runs the instance controller"""
    parser = argparse.ArgumentParser(
        description="Multi-instance suite for PrusaLink")

    parser.add_argument("-i",
                        "--info",
                        action="store_true",
                        help="include log messages up to the INFO level")
    parser.add_argument("-d",
                        "--debug",
                        action="store_true",
                        help="include log messages up to the INFO level")

    parser.add_argument(
        "-u", "--username", required=False,
        help="Which users to use for running and storing everything")
    parser.add_argument(
        "-p", "--prepend-executables-with", required=False,
        help="Environment variables and path to the executables directory")

    subparsers = parser.add_subparsers(dest="command",
                                       help="Available commands")

    # Create a subparser for the start_daemon command
    subparsers.add_parser(
        "start",
        help="Start the instance managing daemon (needs root privileges)")

    subparsers.add_parser(
        "stop",
        help="Stop any manager daemon running (needs root privileges)")

    subparsers.add_parser(
        "clean",
        help="Danger! cleans all PrusaLink multi-instance configuration")

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
        format=LOG_FORMAT_SYSLOG,
        handlers=[SysLogHandler(address='/dev/log')],
    )

    safe_username = get_username(args.username)
    user_info = pwd.getpwnam(safe_username)
    prepend_executables_with = args.prepend_executables_with or ""

    ensure_directory(RUN_DIRECTORY, chown_username=safe_username)
    ensure_directory(Path(MULTI_INSTANCE_CONFIG_PATH).parent)

    if args.command == "start":
        start(user_info, prepend_executables_with)
    elif args.command == "stop":
        stop()
    elif args.command == "clean":
        clean(user_info, prepend_executables_with)
    elif args.command == "rescan":
        rescan()
    else:
        parser.print_help()
