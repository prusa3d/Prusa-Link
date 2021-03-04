import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Dict

from blinker import Signal  # type: ignore
from prusa.connect.printer import Printer

from ...config import Config
from ..input_output.serial.serial_reader import SerialReader
from ..model import Model
from ..const import PRINTING_STATES, \
    JOB_ENDING_STATES, BASE_STATES, JOB_ONGOING_STATES, SD_MOUNT_NAME
from ..structures.mc_singleton import MCSingleton
from ..structures.model_classes import JobState
from ..structures.regular_expressions import \
    FILE_OPEN_REGEX
from ..util import get_clean_path, ensure_directory

log = logging.getLogger(__name__)


class Job(metaclass=MCSingleton):
    """Keeps track of print jobs and their properties"""
    def __init__(self, serial_reader: SerialReader, model: Model, cfg: Config,
                 printer: Printer):
        # Sent every time the job id should disappear, appear or update
        self.printer = printer
        self.serial_reader = serial_reader
        self.serial_reader.add_handler(FILE_OPEN_REGEX, self.file_opened)

        self.model: Model = model
        self.data = self.model.job

        # Unused
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
        self.data.printing_file_m_time = None
        self.data.printing_file_size = None

        self.data.filename_only = None
        self.data.from_sd = None
        self.data.inbuilt_reporting = None

        self.data.job_id = int(loaded_data.get("job_id", 0))
        self.data.job_state = JobState.IDLE

        self.job_id_updated_signal.send(self,
                                        job_id=self.data.get_job_id_for_api())

    def file_opened(self, sender, match: re.Match):
        """Called if the file name, but not the whole path is reported"""
        # This solves the issue, where the print is started from Connect, but
        # the printer responds the same way as if user started it from the
        # screen. We rely on file_name being populated sooner when Connect
        # starts the print. A flag would be arguably more obvious
        # oh, we don't rely on that, I do :D TODO: stop doing that
        if self.data.printing_file_path is not None:
            return
        if match is not None and match.group("sfn") != "":
            # TODO: fix when the fw support for full paths arrives
            filename = match.groups()[0]
            self.set_file_path(filename,
                               filename_only=True,
                               prepend_sd_mountpoint=True)

    def job_started(self, command_id=None):
        """
        Reacts to a new job happening, increments job_id and fills out
        as much info as possible about the print job

        Also writes the new job_id to a file, so there aren't two jobs with
        the same id
        """
        self.data.from_sd = not self.model.file_printer.printing
        self.data.job_id += 1
        self.data.job_start_cmd_id = command_id
        # If we don't print from sd, we know this immediately
        # If not, let's leave it None, it will get filled later
        if not self.data.from_sd:
            self.data.inbuilt_reporting = \
                self.model.print_stats.has_inbuilt_stats
        self.change_state(JobState.IN_PROGRESS)
        self.write()
        log.debug(f"New job started, id = {self.data.job_id}")
        self.job_id_updated_signal.send(self,
                                        job_id=self.data.get_job_id_for_api())

    def job_ended(self):
        """Resets the job info """
        self.data.job_start_cmd_id = None
        self.data.printing_file_path = None
        self.data.filename_only = None
        self.data.from_sd = None
        self.data.inbuilt_reporting = None
        self.change_state(JobState.IDLE)
        log.debug("Job ended")
        self.job_id_updated_signal.send(self,
                                        job_id=self.data.get_job_id_for_api())

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
        """
        Previously wrote the state into a file, now only logs the state change
        """
        log.debug(f"Job changed state to {state}")
        self.data.job_state = state

    def write(self):
        """
        This one was writing everything job related,
        now it only keeps track of the job_id
        """
        data = dict(job_id=self.data.job_id)

        with open(self.job_path, "w") as job_file:
            job_file.write(json.dumps(data))
            job_file.flush()
            os.fsync(job_file.fileno())

    def get_job_id(self):
        """Only return job_id if a job is in progress, otherwise return None"""
        log.debug(f"job_id requested, we are {self.data.job_state.name}")
        if self.data.job_state != JobState.IDLE:
            return self.data.job_id

    def set_file_path(self, path, filename_only, prepend_sd_mountpoint):
        """
        Decides if the supplied file path is better, than what we had
        previously, and updates the job info file parameters accordingly
        :param path: the path/file name to assign to the job
        :param filename_only: flag for distinguishing between filenames and
        paths
        :param prepend_sd_mountpoint: Whether to prepend the SD Card
        mountpoint name
        """
        # If we have a full path, don't overwrite it with just a filename
        if (not filename_only and not self.data.filename_only) \
                or self.data.printing_file_path is None:
            # If asked to, prepend SD mount name
            if prepend_sd_mountpoint:
                path = str(Path(f"/{SD_MOUNT_NAME}").joinpath(path))

            log.debug(
                f"Overwriting file {'name' if filename_only else 'path'} "
                f"with {path}")
            self.data.printing_file_path = path
            self.data.filename_only = filename_only

        if not filename_only:
            file_obj = self.printer.fs.get(self.data.printing_file_path)
            if file_obj:
                if "m_time" in file_obj.attrs:
                    self.data.printing_file_m_time = file_obj.attrs["m_time"]
                if 'size' in file_obj.attrs:
                    self.data.printing_file_size = file_obj.attrs["size"]

    def get_job_info_data(self):
        """Compiles the job info data into a dict"""
        # TODO: consider moving to module data classes
        data = dict()

        if self.data.filename_only:
            data["filename_only"] = self.data.filename_only
        if self.data.job_start_cmd_id is not None:
            data["start_cmd_id"] = self.data.job_start_cmd_id
        if self.data.printing_file_path is not None:
            data["file_path"] = self.data.printing_file_path
        if self.data.printing_file_m_time is not None:
            data["m_time"] = self.data.printing_file_m_time
        if self.data.printing_file_size is not None:
            data["size"] = self.data.printing_file_size
        if self.data.from_sd is not None:
            data["from_sd"] = self.data.from_sd

        return data

    def progress_broken(self, progress_broken):
        """Uses the info about whether the progress percentage reported by
        the printer is broken, to deduce, whether the gcode has inbuilt
        percentage reporting for sd prints.
        """
        if self.data.from_sd:
            if self.data.inbuilt_reporting is None and progress_broken:
                self.data.inbuilt_reporting = False
            elif not progress_broken:
                self.data.inbuilt_reporting = True
