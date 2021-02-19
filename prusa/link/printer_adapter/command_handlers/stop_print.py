import logging

from prusa.connect.printer.const import State
from prusa.link.printer_adapter.command_handlers.try_until_state import \
    TryUntilState

log = logging.getLogger(__name__)


class StopPrint(TryUntilState):
    command_name = "stop print"

    def _run_command(self):
        if self.model.file_printer.printing:
            self.file_printer.stop_print()

        # There might be an edge case with FINISHED, so let's wait for READY
        self._try_until_state(gcode="M603", desired_state=State.READY)
