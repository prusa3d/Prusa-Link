import logging

from prusa.connect.printer.const import State
from prusa.link.printer_adapter.command_handlers.try_until_state import \
    TryUntilState

log = logging.getLogger(__name__)


class ResumePrint(TryUntilState):
    command_name = "resume print"

    def _run_command(self):
        if self.state_manager.get_state() != State.PAUSED:
            self.failed("Cannot resume when not paused.")

        self._try_until_state(gcode="M602", desired_state=State.PRINTING)

        # If we were file printing, the module itself will recognize
        # it should resume from serial
        # if self.file_printer.printing:
        #     self.file_printer.resume()
