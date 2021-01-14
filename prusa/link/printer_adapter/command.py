import logging
import re
from typing import Any, Dict

from prusa.connect.printer.const import Source
from prusa.link.printer_adapter.default_settings import get_settings
from prusa.link.printer_adapter.file_printer import FilePrinter
from prusa.link.printer_adapter.informers.state_manager import StateManager
from prusa.link.printer_adapter.input_output.serial.serial import Serial
from prusa.link.printer_adapter.input_output.serial.serial_queue import \
    MonitoredSerialQueue
from prusa.link.printer_adapter.input_output.serial.serial_reader import \
    SerialReader
from prusa.link.printer_adapter.input_output.serial.helpers import \
    wait_for_instruction, enqueue_matchable, enqueue_instruction
from prusa.link.printer_adapter.model import Model
from prusa.link.sdk_augmentation.printer import MyPrinter
from prusa.connect.printer.command import Command as SDKCommand

LOG = get_settings().LOG


log = logging.getLogger(__name__)
log.setLevel(LOG.COMMANDS)


class CommandFailed(Exception):
    ...


class Command:
    command_name = "command"

    def __init__(self):
        self.serial_queue = MonitoredSerialQueue.get_instance()
        self.serial = Serial.get_instance()
        self.serial_reader = SerialReader.get_instance()
        self.model = Model.get_instance()
        self.printer = MyPrinter.get_instance()
        self.state_manager = StateManager.get_instance()
        self.file_printer = FilePrinter.get_instance()

        self.running = True

    def failed(self, message):
        raise CommandFailed(message)

    def wait_while_running(self, instruction):
        """Wait until the instruction is done, or we quit"""
        wait_for_instruction(instruction, lambda: self.running)

    def do_instruction(self, message):
        instruction = enqueue_instruction(self.serial_queue, message,
                                          to_front=True)
        self.wait_for_instruction(instruction)
        return instruction

    def do_matchable(self, message, regexp: re.Pattern):
        """Enqueues everything to front as commands have a higher priority"""
        instruction = enqueue_matchable(self.serial_queue, message, regexp,
                                        to_front=True)
        self.wait_for_instruction(instruction)
        return instruction

    def wait_for_instruction(self, instruction):
        self.wait_while_running(instruction)

        if not instruction.is_confirmed():
            self.failed(f"Command interrupted")

    def run_command(self) -> Dict[str, Any]:
        data = self._run_command()
        default_data = dict(source=Source.CONNECT)
        if data is not None:
            default_data.update(data)
        return default_data

    def _run_command(self):
        """Put implementation here"""
        ...

    def stop(self):
        self.running = False


class ResponseCommand(Command):

    def __init__(self, caller: SDKCommand):
        self.caller = caller
        super().__init__()
