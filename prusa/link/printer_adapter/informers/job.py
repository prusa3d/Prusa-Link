import logging
import os
from enum import Enum

from blinker import Signal

from prusa.link.printer_adapter.default_settings import get_settings
from prusa.link.printer_adapter.structures.constants import PRINTING_STATES, \
    JOB_ENDING_STATES, BASE_STATES, JOB_ONGOING_STATES
from prusa.link.printer_adapter.util import get_clean_path, ensure_directory

LOG = get_settings().LOG
TIME = get_settings().TIME
PATH = get_settings().PATH

log = logging.getLogger(__name__)
log.setLevel(LOG.JOB_ID)


class JobState(Enum):
    IDLE = "IDLE"
    IN_PROGRESS = "IN_PROGRESS"
    ENDING = "ENDING"


class Job:
    """This is a subcomponent of the state manager"""

    def __init__(self):
        # Sent every time the job id should disappear, appear or update
        self.job_id_updated_signal = Signal()  # kwargs: job_id: int

        self.job_path = get_clean_path(PATH.JOB_FILE)
        ensure_directory(os.path.dirname(self.job_path))

        self.job_start_cmd_id = None
        self.printing_file_path = None

        if os.path.exists(self.job_path):
            with open(self.job_path, "r") as job_file:
                data_parts = job_file.read().split(" ", 3)
                self.job_id = int(data_parts[0])
                self.job_state = JobState(data_parts[1])
                if len(data_parts) == 4:
                    if data_parts[2] != "":
                        self.job_start_cmd_id = int(data_parts[2])
                    if data_parts[3] != "":
                        self.printing_file_path = data_parts[3]
        else:
            self.job_id = 0
            self.job_state = JobState.IDLE

        self.job_id_updated_signal.send(self, job_id=self.get_job_id())

    def job_started(self, command_id=None):
        self.job_id += 1
        self.job_start_cmd_id = command_id
        self.change_state(JobState.IN_PROGRESS)
        self.write()
        log.debug(f"New job started, id = {self.job_id}")
        self.job_id_updated_signal.send(self, job_id=self.get_job_id())

    def job_ended(self):
        self.job_start_cmd_id = None
        self.printing_file_path = None
        self.change_state(JobState.IDLE)
        log.debug(f"Job ended")
        self.job_id_updated_signal.send(self, job_id=self.get_job_id())

    def state_changed(self, from_state, to_state, command_id=None):
        """Called before anything regarding state is sent"""
        if from_state in BASE_STATES and to_state in PRINTING_STATES \
                and self.job_state == JobState.IDLE:
            self.job_started(command_id)
        if from_state in JOB_ONGOING_STATES and to_state in JOB_ENDING_STATES \
                and self.job_state == JobState.IN_PROGRESS:
            self.change_state(JobState.ENDING)

    def tick(self):
        """Called after sending, if the job was ending, it ends now"""
        if self.job_state == JobState.ENDING:
            self.job_ended()

    def change_state(self, state: JobState):
        log.debug(f"Job changed state to {state}")
        self.job_state = state
        self.write()

    def write(self):
        job_start_cmd_id = ""
        if self.job_start_cmd_id is not None:
            job_start_cmd_id = f" {self.job_start_cmd_id}"

        printing_file_path = ""
        if self.printing_file_path is not None:
            printing_file_path = f" {self.printing_file_path}"

        with open(self.job_path, "w") as job_file:
            job_file.write(" ".join([str(self.job_id),
                                     self.job_state.value,
                                     job_start_cmd_id,
                                     printing_file_path]))
            job_file.flush()
            os.fsync(job_file.fileno())

    def get_job_id(self):
        """Only return job_id if a job is in progress, otherwise return None"""
        log.debug(f"job_id requested, we are {self.job_state.name}")
        if self.job_state != JobState.IDLE:
            return self.job_id

    def get_state(self):
        return self.job_state

    def get_file_path(self):
        return self.printing_file_path

    def get_start_cmd_id(self):
        return self.job_start_cmd_id

    def set_file_path(self, path):
        self.printing_file_path = path
