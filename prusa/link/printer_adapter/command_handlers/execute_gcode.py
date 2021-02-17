import logging

from prusa.connect.printer.const import State

from ..command import Command
from prusa.link.printer_adapter.informers.state_manager import StateChange
from prusa.link.printer_adapter.input_output.serial.helpers import \
    enqueue_list_from_str
from prusa.link.printer_adapter.structures.regular_expressions import \
    REJECTION_REGEX

log = logging.getLogger(__name__)


class ExecuteGcode(Command):
    command_name = "execute_gcode"

    def __init__(self, gcode, force=False, **kwargs):
        super().__init__(**kwargs)
        self.gcode = gcode
        self.force = force

    def _run_command(self):
        if self.force:
            log.debug(f"Force sending gcode: '{self.gcode}'")

        is_printing = self.model.state_manager.printing_state == State.PRINTING
        error_exists = self.model.state_manager.override_state == State.ERROR
        if (is_printing or error_exists) and not self.force:
            if is_printing:
                self.failed("I'm sorry Dave but I'm afraid "
                            f"I can't run '{self.gcode}' while printing.")
            elif error_exists:
                self.failed("Printer is in an error state, "
                            "cannot execute commands")
            return

        self.state_manager.expect_change(
            StateChange(command_id=self.command_id,
                        default_source=self.source))

        # Get only non-empty lines
        line_list = [line for line in self.gcode.split("\n") if line.strip()]

        # try running every line
        # Do this manually as it's the only place where a list
        # has to be enqueued
        instruction_list = enqueue_list_from_str(self.serial_queue,
                                                 line_list,
                                                 REJECTION_REGEX,
                                                 front=True)

        for instruction in instruction_list:
            self.wait_while_running(instruction)

            if not instruction.is_confirmed():
                self.failed("Command interrupted")
            if instruction.match():
                self.failed(f"Unknown command '{self.gcode}')")

        # If the gcode execution did not cause a state change
        # stop expecting it
        self.state_manager.stop_expecting_change()

    def _get_state_change(self, default_source):
        return StateChange(default_source=default_source)
