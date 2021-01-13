from threading import Lock
from typing import Optional

from prusa.connect.printer.const import State
from prusa.link.printer_adapter.informers.ip_updater import NO_IP
from prusa.link.printer_adapter.structures.mc_singleton import MCSingleton
from prusa.link.printer_adapter.structures.model_classes import Telemetry, \
    PrinterInfo
from prusa.link.printer_adapter.structures.constants import PRINTING_STATES


class Model(metaclass=MCSingleton):
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
        self._last_telemetry: Telemetry = Telemetry()
        self._state: Optional[State] = None
        self._local_ip: Optional[str] = NO_IP
        self._job_id: Optional[int] = None
        self._printer_info: Optional[PrinterInfo] = None

    def get_and_reset_telemetry(self):
        with self.lock:
            self._telemetry.state = self._state
            self._telemetry.job_id = self._job_id

            # Make sure that even if the printer tells us print specific values,
            # nothing will be sent out while not printing
            if self._state not in PRINTING_STATES:
                self._telemetry.time_printing = None
                self._telemetry.time_estimated = None
                self._telemetry.progress = None
            if self._state == State.PRINTING:
                self._telemetry.axis_x = None
                self._telemetry.axis_y = None

            to_return = self._telemetry
            self._telemetry = Telemetry()
            return to_return

    def set_telemetry(self, new_telemetry: Telemetry):
        with self.lock:
            # let's merge them, instead of overwriting
            merge = self._telemetry.dict()
            merge.update(new_telemetry.dict())
            self._telemetry = Telemetry(**merge)

            second_merge = self._last_telemetry.dict()
            second_merge.update(new_telemetry.dict())
            self._last_telemetry = Telemetry(**second_merge)

    @property
    def last_telemetry(self):
        """Returns telemetry values without resetting to None."""
        with self.lock:
            return self._last_telemetry

    @property
    def state(self):
        with self.lock:
            return self._state

    @state.setter
    def state(self, new_state):
        with self.lock:
            self._state = new_state

            if (new_state == State.PRINTING and
                    self._state in {State.READY, State.BUSY}):
                self._telemetry.progress = 0
                self._telemetry.time_printing = 0

    @property
    def local_ip(self):
        with self.lock:
            return self._local_ip

    @local_ip.setter
    def local_ip(self, new_ip):
        with self.lock:
            self._local_ip = new_ip

    @property
    def job_id(self):
        with self.lock:
            return self._job_id

    @job_id.setter
    def job_id(self, new_job_id):
        with self.lock:
            self._job_id = new_job_id



