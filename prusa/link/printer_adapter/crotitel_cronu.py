import os
from pathlib import Path

from prusa.link.printer_adapter.default_settings import get_settings
from prusa.link.printer_adapter.const import PRINTING_STATES

PATH = get_settings().PATH


class CrotitelCronu:

    def __init__(self):
        self.path = Path(PATH.CROTITEL_CRONU)

    def state_changed(self, to_state):
        state = to_state
        if state in PRINTING_STATES and not self.path.exists():
            self.path.touch()
        elif state not in PRINTING_STATES and self.path.exists():
            os.remove(self.path)
