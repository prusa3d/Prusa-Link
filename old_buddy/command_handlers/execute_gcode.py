import logging

from old_buddy.command import Command
from old_buddy.informers.state_manager import StateChange
from old_buddy.settings import COMMANDS_LOG_LEVEL
from old_buddy.structures.model_classes import Sources, States
from old_buddy.structures.regular_expressions import REJECTION_REGEX

log = logging.getLogger(__name__)
log.setLevel(COMMANDS_LOG_LEVEL)


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
        for line in line_list:
            instruction = self.do_matchable(line)

            if instruction.match(REJECTION_REGEX):
                self.failed(f"Unknown command '{gcode}')")

        # If the gcode execution did not cause a state change
        # stop expecting it
        self.state_manager.stop_expecting_change()
