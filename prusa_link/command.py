import logging
import re
from enum import Enum

from blinker import Signal

from prusa_link.default_settings import get_settings
from prusa_link.file_printer import FilePrinter
from prusa_link.informers.state_manager import StateManager
from prusa_link.input_output.connect_api import ConnectAPI
from prusa_link.input_output.serial.helpers import wait_for_instruction, \
    enqueue_matchable, enqueue_instruction
from prusa_link.input_output.serial.serial import Serial
from prusa_link.input_output.serial.serial_queue import SerialQueue
from prusa_link.input_output.serial.serial_reader import SerialReader
from prusa_link.model import Model
from prusa_link.structures.model_classes import EmitEvents
from prusa_link.util import get_command_id

LOG = get_settings().LOG


log = logging.getLogger(__name__)
log.setLevel(LOG.COMMANDS)


class CommandFailed(Exception):
    ...


class CommandState(Enum):
    HAS_NOT_FAILED = "HAS_NOT_FAILED"
    FAILED = "FAILED"


class Command:
    command_name = "command"

    def __init__(self, serial_queue: SerialQueue, **kwargs):
        self.serial_queue = serial_queue

        self.running = True

        self.state = CommandState.HAS_NOT_FAILED
        self.reason_failed = ""

    def failed(self, message):
        self.set_failed_info(message)
        raise CommandFailed(self.reason_failed)

    def set_failed_info(self, message):
        self.state = CommandState.FAILED
        self.reason_failed = message

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

    def run_command(self):
        """
        Internal, wraps your actual command
        Makes it so if no failed is called and no exceptions are raised
        and not caught, the command automatically responds with FINISHED

        """
        try:
            self._run_command()
        except CommandFailed:
            log.debug(f"Command failed: {self.reason_failed}.")
            raise
        except Exception as e:
            self.set_failed_info(e.args[0])
            raise

    def _run_command(self, **kwargs):
        """Whatever it is, we need to accomplish"""
        ...

    def stop(self):
        self.running = False


class ResponseCommand(Command):

    def __init__(self, api_response, serial: Serial,
                 serial_reader: SerialReader,
                 serial_queue: SerialQueue,
                 connect_api: ConnectAPI, state_manager: StateManager,
                 file_printer: FilePrinter, model: Model):

        super(ResponseCommand, self).__init__(serial=serial,
                                              serial_reader=serial_reader,
                                              serial_queue=serial_queue,
                                              connect_api=connect_api,
                                              state_manager=state_manager,
                                              file_printer=file_printer,
                                              model=model)
        self.serial = serial
        self.serial_reader = serial_reader
        self.model = model
        self.connect_api = connect_api
        self.state_manager = state_manager
        self.file_printer = file_printer

        if api_response is None:
            raise AttributeError("Cannot instantiate ResponseCommand without "
                                 "the api_response")

        self.finished_signal = Signal()
        self.api_response = api_response
        self.command_id = get_command_id(self.api_response)

    @property
    def is_forced(self):
        if self.api_response is None:
            return False
        else:
            return ("Force" in self.api_response.headers and
                    self.api_response.headers["Force"] == "1")

    def reject(self, message=None):
        if message is None:
            message = "Command has been rejected without a message."
        self.connect_api.emit_event(EmitEvents.REJECTED, self.command_id,
                                    message)

    def accept(self):
        self.connect_api.emit_event(EmitEvents.ACCEPTED, self.command_id)

    def finish(self):
        self.connect_api.emit_event(EmitEvents.FINISHED, self.command_id)

    def run_command(self):
        try:
            super().run_command()
        except:
            # We already log it, but in the case of response commands,
            # we need to reject it too
            pass
        finally:
            if self.state == CommandState.HAS_NOT_FAILED:
                self.finish()
            else:
                self.reject(self.reason_failed)
            self.finished_signal.send(self)
