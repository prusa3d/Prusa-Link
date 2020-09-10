import logging
from enum import Enum

from blinker import Signal

from old_buddy.file_printer import FilePrinter
from old_buddy.informers.state_manager import StateManager
from old_buddy.input_output.connect_api import ConnectAPI
from old_buddy.input_output.serial import Serial
from old_buddy.input_output.serial_queue.helpers import wait_for_instruction, \
    enqueue_matchable
from old_buddy.input_output.serial_queue.serial_queue import SerialQueue
from old_buddy.model import Model
from old_buddy.default_settings import get_settings
from old_buddy.structures.model_classes import EmitEvents
from old_buddy.util import get_command_id

LOG = get_settings().LOG


log = logging.getLogger(__name__)
log.setLevel(LOG.COMMANDS_LOG_LEVEL)


class CommandFailed(Exception):
    ...


class CommandState(Enum):
    HAS_NOT_FAILED = "HAS_NOT_FAILED"
    FAILED = "FAILED"


class Command:
    command_name = "command"

    def __init__(self, api_response, serial: Serial,
                 serial_queue: SerialQueue,
                 connect_api: ConnectAPI, state_manager: StateManager,
                 file_printer: FilePrinter, model: Model, **kwargs):
        self.serial = serial
        self.serial_queue = serial_queue
        self.connect_api = connect_api
        self.state_manager = state_manager
        self.file_printer = file_printer
        self.model = model

        self._kwargs = kwargs

        self.api_response = api_response
        self.command_id = get_command_id(self.api_response)

        self.running = True
        self.finished_signal = Signal()

        self.state = CommandState.HAS_NOT_FAILED
        self.reason_failed = ""


    @property
    def is_forced(self):
        return ("Force" in self.api_response.headers and
                self.api_response.headers["Force"] == "1")

    def failed(self, message):
        self.set_failed_info(message)
        raise CommandFailed(self.reason_failed)

    def set_failed_info(self, message):
        self.state = CommandState.FAILED
        self.reason_failed = message

    def reject(self, message=None):
        if message is None:
            message = "Command has been rejected without a message."
        self.connect_api.emit_event(EmitEvents.REJECTED, self.command_id,
                                    message)

    def accept(self):
        self.connect_api.emit_event(EmitEvents.ACCEPTED, self.command_id)

    def finish(self):
        self.connect_api.emit_event(EmitEvents.FINISHED, self.command_id)

    def wait_while_running(self, instruction):
        """Wait until the instruction is done, or we quit"""
        wait_for_instruction(instruction, lambda: self.running)

    def do_matchable(self, gcode):
        """Enqueues everything to front as commands have a higher priority"""
        instruction = enqueue_matchable(self.serial_queue, gcode, front=True)
        self.wait_while_running(instruction)

        if not instruction.is_confirmed():
            self.failed(f"Command interrupted")

        return instruction

    def run_command(self):
        """
        Internal, wraps your actual command
        Makes it so if no failed is called and no exceptions are raised
        and not catched, the command automatically responds with FINISHED

        """
        try:
            self._run_command(**self._kwargs)
        except CommandFailed:
            log.debug(f"Command failed: {self.reason_failed}.")
        except Exception as e:
            log.exception("Command failed unexpectedly, "
                          "captured to stay alive.")
            self.set_failed_info(e.args[0])

        self.finished_signal.send(self)

        if self.state == CommandState.HAS_NOT_FAILED:
            self.finish()
        else:
            self.reject(self.reason_failed)

    def _run_command(self, **kwargs):
        """Whatever it is, we need to accomplish"""
        ...

    def stop(self):
        self.running = False


