import logging

from prusa.connect.printer.const import State, Source
from prusa.link.printer_adapter.command import ResponseCommand
from prusa.link.printer_adapter.informers.state_manager import StateChange
from prusa.link.printer_adapter.input_output.serial.helpers import \
    enqueue_list_from_str
from prusa.link.printer_adapter.structures.regular_expressions import \
    REJECTION_REGEX

log = logging.getLogger(__name__)


class ExecuteGcode(ResponseCommand):
    command_name = "execute_gcode"

    def _run_command(self):

        gcode = self.caller.args[0]
        force = self.caller.force

        if force:
            log.debug(f"Force sending gcode: '{gcode}'")

        is_printing = self.model.state_manager.printing_state == State.PRINTING
        error_exists = self.model.state_manager.override_state == State.ERROR
        if (is_printing or error_exists) and not force:
            if is_printing:
                self.failed("I'm sorry Dave but I'm afraid, "
                            f"I can't run '{gcode}' while printing.")
            elif error_exists:
                self.failed("Printer is in an error state, "
                            "cannot execute commands")
            return

        self.state_manager.expect_change(
            StateChange(default_source=Source.CONNECT))

        # Get only non-empty lines
        line_list = [line for line in gcode.split("\n") if line.strip()]

        # try running every line
        # Do this manually as it's the only place where a list
        # has to be enqueued
        instruction_list = enqueue_list_from_str(self.serial_queue, line_list,
                                                 REJECTION_REGEX,
                                                 front=True)

        for instruction in instruction_list:
            self.wait_while_running(instruction)

            if not instruction.is_confirmed():
                self.failed(f"Command interrupted")
            if instruction.match():
                self.failed(f"Unknown command '{gcode}')")

        # If the gcode execution did not cause a state change
        # stop expecting it
        self.state_manager.stop_expecting_change()
