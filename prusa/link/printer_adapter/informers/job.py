import json
import logging
import os
import re
from typing import Any, Dict

from blinker import Signal

from prusa.link.config import Config
from prusa.link.printer_adapter.input_output.serial.serial_reader import \
    SerialReader
from prusa.link.printer_adapter.model import Model
from prusa.link.printer_adapter.const import PRINTING_STATES, \
    JOB_ENDING_STATES, BASE_STATES, JOB_ONGOING_STATES, SD_MOUNT_NAME
from prusa.link.printer_adapter.structures.mc_singleton import MCSingleton
from prusa.link.printer_adapter.structures.model_classes import JobState
from prusa.link.printer_adapter.structures.regular_expressions import \
    FILE_OPEN_REGEX
from prusa.link.printer_adapter.util import get_clean_path, ensure_directory

log = logging.getLogger(__name__)


class Job(metaclass=MCSingleton):
    """This is a subcomponent of the state manager"""

    def __init__(self, serial_reader: SerialReader, model: Model, cfg: Config):
        # Sent every time the job id should disappear, appear or update
        self.serial_reader = serial_reader
        self.serial_reader.add_handler(FILE_OPEN_REGEX, self.file_opened)

        self.model: Model = model
        self.data = self.model.job

        self.job_id_updated_signal = Signal()  # kwargs: job_id: int

        self.job_path = get_clean_path(cfg.daemon.job_file)
        ensure_directory(os.path.dirname(self.job_path))

        loaded_data: Dict[Any] = dict()

        # ok fine, this is getting complicated, you get a json
        if os.path.exists(self.job_path):
            with open(self.job_path, "r") as job_file:
                loaded_data = json.loads(job_file.read())

        self.data.job_start_cmd_id = None
        self.data.printing_file_path = None
        self.data.filename_only = None
        self.data.from_sd = None

        self.data.job_id = int(loaded_data.get("job_id", 0))
        self.data.job_state = JobState(loaded_data.get("job_state", "IDLE"))
        self.data.api_job_id = None
        if self.data.job_state != JobState.IDLE:
            self.data.api_job_id = self.data.job_id
        self.data.filename_only = bool(loaded_data.get("filename_only", False))
        if "job_start_cmd_id" in loaded_data:
            job_start_cmd_id = loaded_data.get("job_start_cmd_id")
            if job_start_cmd_id is not None:
                self.data.job_start_cmd_id = int(job_start_cmd_id)
        if "printing_file_path" in loaded_data:
            self.data.printing_file_path = loaded_data.get("job_start_cmd_id")

        self.job_id_updated_signal.send(self, job_id=self.data.api_job_id)

    def file_opened(self, sender, match: re.Match):
        # This solves the issue, where the print is started from Connect, but
        # the printer responds the same way as if user started it from the
        # screen. We rely on file_name being populated sooner when Connect
        # starts the print. A flag would be arguably more obvious
        if self.data.printing_file_path is not None:
            return
        if match is not None and match.groups()[0] != "":
            # TODO: fix when the fw support for full paths arrives
            pseudo_path = os.path.join(SD_MOUNT_NAME, match.groups()[0])
            self.set_file_path(pseudo_path, filename_only=True)

    def job_started(self, command_id=None):
        self.data.from_sd = not self.model.file_printer.printing
        self.data.job_id += 1
        self.data.api_job_id = self.data.job_id
        self.data.job_start_cmd_id = command_id
        self.change_state(JobState.IN_PROGRESS)
        self.write()
        log.debug(f"New job started, id = {self.data.job_id}")
        self.job_id_updated_signal.send(self, job_id=self.data.api_job_id)

    def job_ended(self):
        self.data.job_start_cmd_id = None
        self.data.printing_file_path = None
        self.data.filename_only = None
        self.data.api_job_id = None
        self.data.from_sd = None
        self.change_state(JobState.IDLE)
        log.debug(f"Job ended")
        self.job_id_updated_signal.send(self, job_id=self.data.api_job_id)

    def state_changed(self, command_id=None):
        """Called before anything regarding state is sent"""
        to_state = self.model.state_manager.current_state
        from_state = self.model.state_manager.last_state
        if from_state in BASE_STATES and to_state in PRINTING_STATES \
                and self.data.job_state == JobState.IDLE:
            self.job_started(command_id)
        if from_state in JOB_ONGOING_STATES and to_state in JOB_ENDING_STATES \
                and self.data.job_state == JobState.IN_PROGRESS:
            self.change_state(JobState.ENDING)

    def tick(self):
        """Called after sending, if the job was ending, it ends now"""
        if self.data.job_state == JobState.ENDING:
            self.job_ended()

    def change_state(self, state: JobState):
        log.debug(f"Job changed state to {state}")
        self.data.job_state = state
        self.write()

    def write(self):
        data = dict(job_id=self.data.job_id,
                    job_state=self.data.job_state.value,
                    filename_only=self.data.filename_only,
                    job_start_cmd_id=self.data.job_start_cmd_id,
                    printing_file_path=self.data.printing_file_path)

        with open(self.job_path, "w") as job_file:
            job_file.write(json.dumps(data))
            job_file.flush()
            os.fsync(job_file.fileno())

    def get_job_id(self):
        """Only return job_id if a job is in progress, otherwise return None"""
        log.debug(f"job_id requested, we are {self.data.job_state.name}")
        if self.data.job_state != JobState.IDLE:
            return self.data.job_id

    def set_file_path(self, path, filename_only):
        # If we have a full path, don't overwrite it with just a filename
        if (not filename_only and not self.data.filename_only) \
                or self.data.printing_file_path is None:
            log.debug(f"Overwriting file {'name' if filename_only else 'path'} "
                      f"with {path}")
            self.data.printing_file_path = path
            self.data.filename_only = filename_only

    def get_state(self):
        return self.data.job_state

    def get_job_info_data(self):
        data = dict()

        if self.data.filename_only:
            data["filename_only"] = self.data.filename_only

        if self.data.job_start_cmd_id is not None:
            data["start_cmd_id"] = self.data.job_start_cmd_id

        if self.data.printing_file_path is not None:
            data["file_path"] = self.data.printing_file_path

        if self.data.from_sd is not None:
            data["from_sd"] = self.data.from_sd

        return data
