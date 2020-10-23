import os
from pathlib import Path

from prusa.link.printer_adapter.default_settings import get_settings
from prusa.link.printer_adapter.informers.state_manager import StateManager
from prusa.link.printer_adapter.structures.constants import PRINTING_STATES

PATH = get_settings().PATH


class CrotitelCronu:

    def __init__(self, state_manager: StateManager):
        self.state_manager = state_manager
        self.state_manager.state_changed_signal.connect(self.state_changed)
        self.path = Path(PATH.CROTITEL_CRONU)

    def state_changed(self, sender, command_id, source):
        state = self.state_manager.get_state()
        if state in PRINTING_STATES and not self.path.exists():
            self.path.touch()
        elif state not in PRINTING_STATES and self.path.exists():
            os.remove(self.path)
