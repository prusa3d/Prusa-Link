import logging

from prusa.connect.printer.const import State
from prusa.link.printer_adapter.command_handlers.try_until_state import \
    TryUntilState
from prusa.link.printer_adapter.default_settings import get_settings

LOG = get_settings().LOG


log = logging.getLogger(__name__)
log.setLevel(LOG.COMMANDS)


class StopPrint(TryUntilState):
    command_name = "stop print"

    def _run_command(self):
        if self.file_printer.printing:
            self.file_printer.stop_print()

        self._try_until_state(gcode="M603", desired_state=State.READY)
