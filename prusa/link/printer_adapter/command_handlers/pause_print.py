import logging

from prusa.connect.printer.const import State
from prusa.link.printer_adapter.command_handlers.try_until_state import \
    TryUntilState
from prusa.link.printer_adapter.default_settings import get_settings

LOG = get_settings().LOG

log = logging.getLogger(__name__)
log.setLevel(LOG.COMMANDS)


class PausePrint(TryUntilState):
    command_name = "pause print"

    def _run_command(self):
        if self.file_printer.printing:
            self.file_printer.pause()

        self._try_until_state(gcode="M601", desired_state=State.PAUSED)
