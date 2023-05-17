"""The module for starting PrusaLink Instance Manager components"""
import argparse
import logging
import os
import signal
from logging.handlers import SysLogHandler
from pathlib import Path
from threading import Thread
from time import monotonic, sleep

import pwd
import stat
import sys

from daemon import DaemonContext  # type: ignore
from lockfile.pidlockfile import PIDLockFile  # type: ignore

from .config_component import MultiInstanceConfig, ConfigComponent
from .const import MANAGER_PID_PATH, DEFAULT_UID, \
    SERVER_PID_PATH, RUN_DIRECTORY, COMMS_PIPE_PATH, COMMUNICATION_TIMEOUT
from .controller import Controller
from .web import get_server_instance
from ..__main__ import check_process
from ..__main__ import stop as stop_process
from ..config import LOG_FORMAT_SYSLOG, Config, FakeArgs
from ..util import ensure_directory, prctl_name
from ..web import run_server

log = logging.getLogger(__name__)


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

    def __init__(self, user_info):
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
            detach_process=True
        )

        with context:
            self.controller = Controller(user_info=self.user_info)
            self.controller.run()

    def _sigterm_handler(self, *_):
        """Stops the controller. Has to return as fast as possible"""

        def inner():
            prctl_name()
            self.controller.stop()
            self.controller = None

        log.info("Received SIGTERM. Stopping Multi Instance Manager")
        Thread(target=inner, name="mi_stopper", daemon=False).start()


class Server:
    """This class represents the process that runs the web server"""

    pid_file = PIDLockFile(SERVER_PID_PATH)

    def __init__(self, user_info):
        self.user_info = user_info

        self.httpd = None

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
            detach_process=True
        )

        with context:
            config = MultiInstanceConfig()
            self.httpd = get_server_instance(config.web.port_range_start)

            while True:
                try:
                    run_server(self.httpd, False)
                except Exception:  # pylint: disable=broad-except
                    log.exception("Error while running web server. "
                                  "Restarting")
                else:
                    log.info("Multi Instance Server stopped")
                    break

    def _sigterm_handler(self, *_):
        """Stop the web server. Has to return as fast as possible"""

        def inner():
            prctl_name()
            self.httpd.shutdown()
            self.httpd = None

        log.info("Received SIGTERM. Stopping Multi Instance Web Server")
        Thread(target=inner, daemon=False, name="mi_srv_stopper").start()


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


def start(user_info):
    """Starts the instance manager processes"""
    if os.fork() == 0:
        Manager(user_info)
        sys.exit(0)
    if os.fork() == 0:
        Server(user_info)
        sys.exit(0)


def handle_process_stop(pid_file, name=None, quiet=False):
    """Stops a process handling pid file edge cases"""
    pid = pid_file.read_pid()
    if name is None:
        name = f"Process PID {pid}"
    if pid_file.is_locked():
        if check_process(pid):
            stop_process(pid)
        else:
            if not quiet:
                print(f"{name} not running, but PID file locked. "
                      "Breaking lock.")
                log.warning("%s not running, but PID file locked. "
                            "Breaking lock.", name)
            pid_file.break_lock()
    else:
        if not quiet:
            print(f"{name} not running")
            log.warning("%s not running", name)


def stop(quiet=False):
    """Stops the instance manager and all PrusaLink instances"""
    threads = [
        Thread(target=handle_process_stop,
               args=(Manager.pid_file, "Instance Manager", quiet)),
        Thread(target=handle_process_stop,
               args=(Server.pid_file, "Multi Instance Server", quiet))
    ]

    multi_instance_config = MultiInstanceConfig()

    for printer in multi_instance_config.printers:
        config = Config(FakeArgs(path=printer.config_path))
        pid_file = PIDLockFile(Path(config.daemon.data_dir,
                                    config.daemon.pid_file))
        pid = pid_file.read_pid()
        threads.append(
            Thread(target=handle_process_stop,
                   args=(pid_file, f"PrusaLink [{pid}]", quiet))
        )

    for thread in threads:
        thread.start()

    for thread in threads:
        thread.join()


def rescan():
    """Notify the manager that a connection has been established
    by writing "connected" to the communication pipe."""
    if not stat.S_ISFIFO(os.stat(COMMS_PIPE_PATH).st_mode):
        log.error("Cannot communicate to manager. Missing named pipe")
        raise FileNotFoundError("Missing named pipe")

    timeout_at = monotonic() + COMMUNICATION_TIMEOUT
    while True:
        try:
            file_descriptor = os.open(path=COMMS_PIPE_PATH,
                                      flags=os.O_WRONLY | os.O_NONBLOCK)
            with open(file_descriptor, "w", encoding="UTF-8") as file:
                file.write("rescan")

        except Exception:  # pylint: disable=broad-except
            if monotonic() < timeout_at:
                sleep(0.1)
                continue

            log.exception("Cannot talk to manager")
            raise

        break


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

    parser.add_argument(
        "-u", "--username", required=False,
        help="Which users to use for running and storing everything")

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
        format=LOG_FORMAT_SYSLOG,
        handlers=[SysLogHandler(address='/dev/log')]
    )

    if not hasattr(args, "username"):
        args.username = None
    safe_username = get_username(args.username)
    user_info = pwd.getpwnam(safe_username)

    ensure_directory(RUN_DIRECTORY, chown_username=safe_username)

    if args.command == "start":
        start(user_info)
    elif args.command == "stop":
        stop()
    elif args.command == "clean":
        stop(quiet=True)
        ConfigComponent.clear_configuration()
    elif args.command == "rescan":
        rescan()
    else:
        parser.print_help()
