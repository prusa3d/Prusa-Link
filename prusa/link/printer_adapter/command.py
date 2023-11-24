"""Contains implementation of the Command class"""
import abc
import logging
import re
from threading import Event
from typing import Any, Dict

from prusa.connect.printer.const import Source

from ..sdk_augmentation.printer import MyPrinter
from ..serial.helpers import (
    enqueue_instruction,
    enqueue_matchable,
    wait_for_instruction,
)
from ..serial.serial_adapter import SerialAdapter
from ..serial.serial_parser import ThreadedSerialParser
from ..serial.serial_queue import MonitoredSerialQueue
from .file_printer import FilePrinter
from .job import Job
from .model import Model
from .state_manager import StateManager

log = logging.getLogger(__name__)


class CommandFailed(Exception):
    """Exception class for signalling that a command has failed"""


class NotStateToPrint(CommandFailed):
    """Exception class for signalling that printer is not in state to print"""


class FileNotFound(CommandFailed):
    """A specific error for files that have not been found and the command
    failing because of that"""


class Command:
    """Commands are like controllers, they do stuff and need a lot of info to
    do it. This class provides most of the components a command could want to
    access or use."""
    # pylint: disable=too-many-instance-attributes
    command_name = "command"

    def __init__(self, command_id=None, source=Source.CONNECT) -> None:
        self.serial_queue: MonitoredSerialQueue = \
            MonitoredSerialQueue.get_instance()
        self.serial_adapter: SerialAdapter = SerialAdapter.get_instance()
        self.serial_parser: ThreadedSerialParser = \
            ThreadedSerialParser.get_instance()
        self.model: Model = Model.get_instance()
        self.printer: MyPrinter = MyPrinter.get_instance()
        self.state_manager: StateManager = StateManager.get_instance()
        self.file_printer: FilePrinter = FilePrinter.get_instance()
        self.job: Job = Job.get_instance()

        self.command_id = command_id
        self.source = source

        self.quit_evt = Event()

    def wait_while_running(self, instruction):
        """Wait until the instruction is done, or we quit"""
        wait_for_instruction(instruction, should_wait_evt=self.quit_evt)

    def do_instruction(self, message):
        """Shorthand for enqueueing and waiting for an instruction
        Enqueues everything to front as commands have a higher priority"""
        instruction = enqueue_instruction(self.serial_queue,
                                          message,
                                          to_front=True)
        self.wait_for_instruction(instruction)
        return instruction

    def do_matchable(self, message, regexp: re.Pattern):
        """Shorthand for enqueueing an waiting for a matchable instruction
        Enqueues everything to front as commands have a higher priority"""
        instruction = enqueue_matchable(self.serial_queue,
                                        message,
                                        regexp,
                                        to_front=True)
        self.wait_for_instruction(instruction)
        return instruction

    def wait_for_instruction(self, instruction):
        """Waits for instruction until it gets confirmed or we quit"""
        self.wait_while_running(instruction)

        if not instruction.is_confirmed():
            raise CommandFailed("Command interrupted")

    def run_command(self) -> Dict[str, Any]:
        """Encapsulates the run command, provides default data for
        returning"""
        data = self._run_command()
        default_data = {"source": self.source}
        if data is not None:
            default_data.update(data)
        return default_data

    @abc.abstractmethod
    def _run_command(self):
        """Put implementation here"""

    def stop(self):
        """Stops the command"""
        self.quit_evt.set()
