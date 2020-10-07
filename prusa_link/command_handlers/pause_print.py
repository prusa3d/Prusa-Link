import logging

from prusa_link.command_handlers.try_until_state import TryUntilState
from prusa_link.default_settings import get_settings
from prusa_link.structures.model_classes import States

LOG = get_settings().LOG

log = logging.getLogger(__name__)
log.setLevel(LOG.COMMANDS)


class PausePrint(TryUntilState):
    command_name = "pause print"

    def _run_command(self):
        if self.file_printer.printing:
            self.file_printer.pause()

        self._try_until_state(gcode="M601", desired_state=States.PAUSED)
