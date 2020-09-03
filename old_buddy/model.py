from threading import Lock
from typing import Optional

from old_buddy.informers.filesystem.models import InternalFileTree, SDState
from old_buddy.informers.state_manager import PRINTING_STATES
from old_buddy.structures.model_classes import FileTree, States, Telemetry, \
    PrinterInfo


class Model:
    """
    This class should collect every bit of info from all the informer classes
    Some values are reset upon reading, other, more state oriented should stay
    """

    def __init__(self):
        # Make only one thread be able to write or read our variables
        self.lock = Lock()

        # Telemetry is supposed to report only stuff that has been actually
        # retrieved from the printer (except printer state)
        # so it resets upon being read
        self._telemetry: Telemetry = Telemetry()
        self._state: Optional[States] = None
        self._local_ip: Optional[str] = None
        self._file_tree: Optional[InternalFileTree] = None
        self._sd_state: Optional[SDState] = None
        self._printer_info: Optional[PrinterInfo] = None

    @property
    def telemetry(self):
        with self.lock:
            self._telemetry.state = self._state.name

            # Make sure that even if the printer tells us print specific values,
            # nothing will be sent out while not printing
            if self._state not in PRINTING_STATES:
                self._telemetry.time_printing = None
                self._telemetry.time_estimated = None
                self._telemetry.progress = None
            if self._state == States.PRINTING:
                self._telemetry.axis_x = None
                self._telemetry.axis_y = None

            to_return = self._telemetry
            self._telemetry = Telemetry()
            return to_return

    @telemetry.setter
    def telemetry(self, new_telemetry: Telemetry):
        with self.lock:
            # let's merge them, instead of overwriting
            merge = self._telemetry.dict()
            merge.update(new_telemetry.dict())
            self._telemetry = Telemetry(**merge)

    @property
    def state(self):
        with self.lock:
            return self._state

    @state.setter
    def state(self, new_state):
        with self.lock:
            self._state = new_state

            if (new_state == States.PRINTING and
                    self._state in {States.READY, States.BUSY}):
                self._telemetry.progress = 0
                self._telemetry.printing_time = 0

    @property
    def local_ip(self):
        with self.lock:
            assert self._local_ip is not None, \
                "You read ip too soon. No ip is known yet."
            return self._local_ip

    @local_ip.setter
    def local_ip(self, new_ip):
        with self.lock:
            self._local_ip = new_ip

    @property
    def file_tree(self):
        with self.lock:
            return self._file_tree

    @property
    def api_file_tree(self):
        with self.lock:
            return self._file_tree.to_api_file_tree()

    @file_tree.setter
    def file_tree(self, new_tree):
        with self.lock:
            self._file_tree = new_tree

    @property
    def sd_state(self):
        with self.lock:
            return self._sd_state

    @sd_state.setter
    def sd_state(self, new_sd_state):
        with self.lock:
            self._sd_state = new_sd_state

        

