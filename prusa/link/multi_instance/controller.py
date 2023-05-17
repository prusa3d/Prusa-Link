"""A module implementing the controller of the PrusaLink Instance Manager"""

import queue
from threading import Thread
from time import sleep

import select
import logging
import os

from .config_component import ConfigComponent, MultiInstanceConfig
from .const import COMMS_PIPE_PATH
from .runner_component import RunnerComponent
from ..const import QUIT_INTERVAL
from ..util import prctl_name

log = logging.getLogger(__name__)


class Controller:
    """Glue between the multi instance components"""

    def __init__(self, user_info):
        self.user_info = user_info

        self.multi_instance_config = MultiInstanceConfig()
        self.running = True
        self.is_open = False
        self.command_queue = queue.Queue()
        self.command_handlers = {
            "rescan": self.rescan
        }

        self.config_component = ConfigComponent(
            self.multi_instance_config, self.user_info)
        self.runner_component = RunnerComponent(
            self.multi_instance_config, self.user_info)

        self.command_reading_thread = Thread(
            target=self._read_commands, name="mi_cmd_reader")
        self.command_executing_thread = Thread(
            target=self._do_commands, name="mi_cmd_executor")

    def run(self):
        """Starts the controller"""
        self.runner_component.start_configured()

        self._setup_pipe()

        self.command_reading_thread.start()
        self.command_executing_thread.start()

        # Wait for the pipe to be opened
        while not self.is_open and self.running:
            sleep(QUIT_INTERVAL)

        self.config_component.setup_connected_trigger()

        self.command_reading_thread.join()
        self.command_executing_thread.join()

        log.info("Multi Instance Controller stopped")

    def _setup_pipe(self):
        """Creates the pipe and sets the correct permissions"""
        if os.path.exists(COMMS_PIPE_PATH):
            os.remove(COMMS_PIPE_PATH)

        os.mkfifo(COMMS_PIPE_PATH)
        os.chown(COMMS_PIPE_PATH,
                 uid=self.user_info.pw_uid,
                 gid=self.user_info.pw_gid)

    def _read_commands(self):
        """Reads commands from the pipe and puts them into the command queue"""
        prctl_name()
        while self.running:
            try:
                file_descriptor = os.open(path=COMMS_PIPE_PATH,
                                          flags=os.O_RDONLY | os.O_NONBLOCK)
                self.is_open = True
                while self.running:
                    log.info("Spin - waiting for communication")
                    select_result = select.select(
                        [file_descriptor], [], [], QUIT_INTERVAL
                    )
                    if not select_result[0]:
                        continue

                    with open(file_descriptor, "r", encoding="UTF-8") as pipe:
                        command = pipe.read()
                        log.debug("read: '%s' from pipe", command)
                        self.command_queue.put(command)

                    break

            except Exception:  # pylint: disable=broad-except
                log.exception("Exception occurred while multi-instancing "
                              "synergy and stuff")

    def _do_commands(self):
        """Executes commands from the command queue"""
        prctl_name()
        while self.running:
            try:
                log.info("Spin - waiting for command")
                command = self.command_queue.get(timeout=QUIT_INTERVAL)
                log.debug("Executing command '%s'", command)
                if command in self.command_handlers:
                    self.command_handlers[command]()
            except queue.Empty:
                continue

    def rescan(self):
        """Handles the rescan notification by attempting to configure
        all not configured printers and starting instances for them"""
        log.debug("Rescanning printers")
        configured = self.config_component.configure_new()
        for printer in self.multi_instance_config.printers:
            if printer.serial_number not in configured:
                continue
            self.runner_component.load_instance(printer.config_path)

    def stop(self):
        """Stops the controller"""
        self.config_component.teardown_connected_trigger()
        self.running = False
