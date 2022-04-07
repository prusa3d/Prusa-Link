"""Contains implementation of the Model class"""
from threading import Lock

from prusa.connect.printer.const import State

from .const import PRINTING_STATES
from .structures.mc_singleton import MCSingleton
from .structures.model_classes import Telemetry
from .structures.module_data_classes import \
    FilePrinterData, StateManagerData, JobData, IPUpdaterData, SDCardData, \
    MountsData, PrintStatsData


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

        # Let's try and share inner module states for cooperation
        # The idea is, every module will get the model.
        # Every component HAS TO write its OWN INFO ONLY but can read
        # everything
        self.file_printer: FilePrinterData
        self.print_stats: PrintStatsData
        self.state_manager: StateManagerData
        self.job: JobData
        self.ip_updater: IPUpdaterData
        self.sd_card: SDCardData
        self.dir_mounts: MountsData
        self.fs_mounts: MountsData

    def get_and_reset_telemetry(self):
        """
        Telemetry is special, to report only the most recent values,
        each read it gets reset
        """
        with self.lock:
            self._telemetry.state = self.state_manager.current_state

            # Make sure that even if the printer tells us print specific
            # values, nothing will be sent out while not printing

            # time_estimated is deprecated, kept for compatibility
            if self.state_manager.current_state not in PRINTING_STATES:
                self._telemetry.time_printing = None
                self._telemetry.time_estimated = None
                self._telemetry.time_remaining = None
                self._telemetry.progress = None
            if self.state_manager.current_state == State.PRINTING:
                self._telemetry.axis_x = None
                self._telemetry.axis_y = None

            to_return = self._telemetry
            self._telemetry = Telemetry()
            return to_return

    def set_telemetry(self, new_telemetry: Telemetry):
        """
        Merges the new data into the cumulative telemetry.
        The second telemetry is not being reset. It is useful for monitoring
        """
        with self.lock:
            # let's merge them, instead of overwriting
            merge = self._telemetry.dict()
            merge.update(new_telemetry.dict(exclude_none=True))
            self._telemetry = Telemetry(**merge)

            second_merge = self._last_telemetry.dict()
            second_merge.update(new_telemetry.dict(exclude_none=True))
            self._last_telemetry = Telemetry(**second_merge)

    @property
    def last_telemetry(self):
        """
        Returns telemetry values without resetting to None.
        Adds the current job id even though "oficially" the SDK adds it
        into the telemetry being sent.
        """
        with self.lock:
            self._last_telemetry.state = self.state_manager.current_state
            self._last_telemetry.job_id = self.job.get_job_id_for_api()
            return self._last_telemetry
