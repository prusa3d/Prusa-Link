import logging

from prusa_link.command import Command
from prusa_link.informers.state_manager import StateChange
from prusa_link.default_settings import get_settings
from prusa_link.input_output.serial.helpers import enqueue_list_from_str
from prusa_link.structures.model_classes import Sources, States
from prusa_link.structures.regular_expressions import REJECTION_REGEX


LOG = get_settings().LOG

log = logging.getLogger(__name__)
log.setLevel(LOG.COMMANDS_LOG_LEVEL)


class ExecuteGcode(Command):
    command_name = "execute_gcode"

    def _run_command(self):
        gcode = self.api_response.text

        if self.is_forced:
            log.debug(f"Force sending gcode: '{gcode}'")

        is_printing = self.state_manager.printing_state == States.PRINTING
        error_exists = self.state_manager.override_state == States.ERROR
        if (is_printing or error_exists) and not self.is_forced:
            if is_printing:
                self.failed("I'm sorry Dave but I'm afraid, "
                            f"I can't run '{gcode}' while printing.")
            elif error_exists:
                self.failed("Printer is in an error state, "
                            "cannot execute commands")
            return

        self.state_manager.expect_change(
            StateChange(self.api_response, default_source=Sources.CONNECT))

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
