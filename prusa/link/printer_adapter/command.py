import logging
import re
from typing import Any, Dict

from prusa.connect.printer import Printer
from prusa.connect.printer.const import Source
from prusa.link.printer_adapter.default_settings import get_settings
from prusa.link.printer_adapter.file_printer import FilePrinter
from prusa.link.printer_adapter.informers.state_manager import StateManager
from prusa.link.printer_adapter.input_output.serial.serial import Serial
from prusa.link.printer_adapter.input_output.serial.serial_queue import \
    SerialQueue
from prusa.link.printer_adapter.input_output.serial.serial_reader import \
    SerialReader
from prusa.link.printer_adapter.input_output.serial.helpers import \
    wait_for_instruction, enqueue_matchable, enqueue_instruction
from prusa.link.printer_adapter.model import Model

LOG = get_settings().LOG


log = logging.getLogger(__name__)
log.setLevel(LOG.COMMANDS)


class CommandFailed(Exception):
    ...


class Command:
    command_name = "command"

    def __init__(self, serial_queue: SerialQueue, args=None,
                 **kwargs):
        self.serial_queue = serial_queue
        self.args = args if args is not None else []

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
        self._run_command()
        return dict(source=Source.CONNECT)

    def _run_command(self):
        """Put implementation here"""
        ...

    def stop(self):
        self.running = False


class ResponseCommand(Command):

    def __init__(self, args, serial: Serial,
                 serial_reader: SerialReader,
                 serial_queue: SerialQueue,
                 printer: Printer, state_manager: StateManager,
                 file_printer: FilePrinter, model: Model):

        super(ResponseCommand, self).__init__(serial=serial,
                                              serial_reader=serial_reader,
                                              serial_queue=serial_queue,
                                              printer=printer,
                                              state_manager=state_manager,
                                              file_printer=file_printer,
                                              model=model,
                                              args=args)
        self.serial = serial
        self.serial_reader = serial_reader
        self.model = model
        self.printer = printer
        self.state_manager = state_manager
        self.file_printer = file_printer
