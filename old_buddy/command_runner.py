import logging
from threading import Thread, Event
from typing import Type, Optional

from old_buddy.command import Command
from old_buddy.informers.state_manager import StateManager
from old_buddy.input_output.connect_api import ConnectAPI
from old_buddy.input_output.serial_queue.serial_queue import SerialQueue
from old_buddy.model import Model
from old_buddy.default_settings import get_settings

LOG = get_settings().LOG
TIME = get_settings().TIME


log = logging.getLogger(__name__)
log.setLevel(LOG.COMMANDS_LOG_LEVEL)


class CommandRunner:

    def __init__(self, serial_queue: SerialQueue, connect_api: ConnectAPI,
                  state_manager: StateManager, model: Model):
        self.serial_queue = serial_queue
        self.state_manager = state_manager
        self.connect_api = connect_api
        self.model = model

        self.running = True
        self.running_command: Optional[Command] = None
        self.new_command_event = Event()

        # Can't start a new thread for every command.
        # So let's recycle one in here
        self.command_thread = Thread(target=self.handle_commands,
                                     name="command_runnaer")
        self.command_thread.start()

    def handle_commands(self):
        while self.running:
            if self.new_command_event.wait(timeout=TIME.QUIT_INTERVAL):
                self.new_command_event.clear()
                self.running_command.run_command()

    def run(self, command_class: Type[Command], api_response, **kwargs):
        """
        Used to pass additional context (as a factory?) so the command
        itself can be quite light in arguments
        """
        command = command_class(api_response, self.serial_queue,
                                self.connect_api, self.state_manager,
                                self.model, **kwargs)
        self._run(command)

    def _run(self, command: Command):
        if self.running_command is not None:
            if self.running_command.command_id == command.command_id:
                log.warning("Tried to run already running command")
                command.accept()
            else:
                command.reject("Another command is running")
        else:
            command.accept()
            command.finished_signal.connect(self.command_finished)
            self.running_command = command
            self.new_command_event.set()

    def command_finished(self, sender):
        self.running_command = None

    def stop(self):
        if self.running_command is not None:
            self.running_command.stop()
        self.running = False
        self.command_thread.join()
