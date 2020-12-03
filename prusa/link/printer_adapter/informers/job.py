import json
import logging
import os
import re
from enum import Enum
from typing import Any, Dict

from blinker import Signal

from prusa.link.printer_adapter.default_settings import get_settings
from prusa.link.printer_adapter.input_output.serial.serial_reader import \
    SerialReader
from prusa.link.printer_adapter.structures.constants import PRINTING_STATES, \
    JOB_ENDING_STATES, BASE_STATES, JOB_ONGOING_STATES
from prusa.link.printer_adapter.structures.regular_expressions import \
    FILE_OPEN_REGEX
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

    def __init__(self, serial_reader: SerialReader):
        # Sent every time the job id should disappear, appear or update
        self.serial_reader = serial_reader
        self.serial_reader.add_handler(FILE_OPEN_REGEX, self.file_opened)
        self.job_id_updated_signal = Signal()  # kwargs: job_id: int

        self.job_path = get_clean_path(PATH.JOB_FILE)
        ensure_directory(os.path.dirname(self.job_path))

        data: Dict[Any] = dict()

        # ok fine, this is getting complicated, you get a json
        if os.path.exists(self.job_path):
            with open(self.job_path, "r") as job_file:
                data = json.loads(job_file.read())

        self.job_start_cmd_id = None
        self.printing_file_path = None
        self.filename_only = False

        self.job_id = int(data.get("job_id", 0))
        self.job_state = JobState(data.get("job_state", "IDLE"))
        self.filename_only = bool(data.get("filename_only", False))
        if "job_start_cmd_id" in data:
            job_start_cmd_id = data.get("job_start_cmd_id")
            if job_start_cmd_id is not None:
                self.job_start_cmd_id = int(job_start_cmd_id)
        if "printing_file_path" in data:
            self.printing_file_path = data.get("job_start_cmd_id")

        self.job_id_updated_signal.send(self, job_id=self.get_job_id())

    def file_opened(self, sender, match: re.Match):
        # This solves the issue, where the print is started from Connect, but
        # the printer responds the same way as if user started in from the
        # screen. We rely on file_name being populated sooner when Connect
        # starts the print. A flag would be arguably more obvious
        if self.printing_file_path is not None:
            return
        if match is not None and match.groups()[0] != "":
            self.set_file_path(match.groups()[0], filename_only=True)

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
        self.filename_only = False
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
        data = dict(job_id=self.job_id,
                    job_state=self.job_state.value,
                    filename_only=self.filename_only,
                    job_start_cmd_id=self.job_start_cmd_id,
                    printing_file_path=self.printing_file_path)

        with open(self.job_path, "w") as job_file:
            job_file.write(json.dumps(data))
            job_file.flush()
            os.fsync(job_file.fileno())

    def get_job_id(self):
        """Only return job_id if a job is in progress, otherwise return None"""
        log.debug(f"job_id requested, we are {self.job_state.name}")
        if self.job_state != JobState.IDLE:
            return self.job_id

    def set_file_path(self, path, filename_only):
        # If we have a full path, don't overwrite it with just a filename
        if (not filename_only and not self.filename_only) \
                or self.printing_file_path is None:
            log.debug(f"Overwriting file {'name' if filename_only else 'path'} "
                      f"with {path}")
            self.printing_file_path = path
            self.filename_only = filename_only

    def get_state(self):
        return self.job_state

    def get_job_info_data(self):
        data = dict()

        if self.filename_only:
            data["filename_only"] = self.filename_only

        if self.job_start_cmd_id is not None:
            data["start_cmd_id"] = self.job_start_cmd_id

        if self.printing_file_path is not None:
            data["file_path"] = self.printing_file_path

        return data
