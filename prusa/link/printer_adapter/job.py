"""Contains implementation of the Job class"""
import logging
import os
import re

from blinker import Signal  # type: ignore

from prusa.connect.printer import Printer

from ..const import (
    JOB_DESTROYING_STATES,
    JOB_ENDING_STATES,
    JOB_STARTING_STATES,
    SD_STORAGE_NAME,
)
from ..serial.helpers import enqueue_instruction
from ..serial.serial_queue import SerialQueue
from .model import Model
from .structures.mc_singleton import MCSingleton
from .structures.model_classes import JobState
from .structures.module_data_classes import JobData

log = logging.getLogger(__name__)


class Job(metaclass=MCSingleton):
    """Keeps track of print jobs and their properties"""

    # pylint: disable=too-many-arguments
    def __init__(self,
                 serial_queue: SerialQueue,
                 model: Model, printer: Printer):
        # Sent every time the job id should disappear, appear or update
        self.printer = printer
        self.serial_queue = serial_queue

        # Unused
        self.job_id_updated_signal = Signal()  # kwargs: job_id: int
        self.job_info_updated_signal = Signal()

        self.model: Model = model
        self.model.job = JobData(already_sent=False,
                                 job_start_cmd_id=None,
                                 path_incomplete=True,
                                 from_sd=None,
                                 inbuilt_reporting=None,
                                 selected_file_path=None,
                                 selected_file_m_timestamp=None,
                                 selected_file_size=None,
                                 printing_file_byte=None,
                                 job_state=JobState.IDLE,
                                 job_id=None,
                                 job_id_offset=0,
                                 last_job_path=None)
        self.data = self.model.job

        self.job_id_updated_signal.send(self,
                                        job_id=self.data.get_job_id_for_api())

    def file_opened(self, _, match: re.Match):
        """Handles the M23 output by extracting the mixed path and sends it
        for parsing"""
        if match is not None and match.group("sdn_lfn") != "":
            mixed_path = match.group("sdn_lfn")
            self.process_mixed_path(mixed_path)

    def process_mixed_path(self, mixed_path):
        """Takes the mixed path and tries translating it into the long format
        Sends the result to set_file_path
        :param mixed_path: the path in SDR_LFN format
        (short dir name, long file name)"""
        log.debug("Processing %s", mixed_path)
        if mixed_path.lower() in self.model.sd_card.mixed_to_lfn_paths:
            log.debug("It has been found in the SD card file tree")
            self.set_file_path(
                self.model.sd_card.mixed_to_lfn_paths[mixed_path.lower()],
                path_incomplete=False,
                prepend_sd_storage=True)
        else:
            log.debug("It has not been found in the SD card file tree.")
            self.set_file_path(mixed_path,
                               path_incomplete=True,
                               prepend_sd_storage=True)

    def job_started(self, command_id=None):
        """Reacts to a new job happening, increments job_id and fills out
        as much info as possible about the print job

        Also writes the new job_id to a file, so there aren't two jobs with
        the same id"""
        self.data.already_sent = False
        # Try to not increment the job id on PP recovery
        if not self.model.file_printer.recovering:
            if self.data.job_id is None:
                self.data.job_id_offset += 1
            else:
                self.data.job_id += 1
        self.data.job_start_cmd_id = command_id
        # If we don't print from sd, we know this immediately
        # If not, let's leave it None, it will get filled later
        if not self.data.from_sd:
            self.data.inbuilt_reporting = \
                self.model.print_stats.has_inbuilt_stats
        self.change_state(JobState.IN_PROGRESS)
        self.write()
        self.update_last_job_path()
        log.debug("New job started, id = %s", self.data.job_id)
        self.job_id_updated_signal.send(self,
                                        job_id=self.data.get_job_id_for_api())

    def job_ended(self):
        """Resets the job info """
        self.data.already_sent = False
        self.data.job_start_cmd_id = None
        self.data.path_incomplete = True
        self.data.inbuilt_reporting = None
        self.change_state(JobState.IDLE)
        log.info("Job ended")
        self.job_id_updated_signal.send(self,
                                        job_id=self.data.get_job_id_for_api())

    def state_changed(self, command_id=None):
        """Called before anything regarding state is sent"""
        to_state = self.model.state_manager.current_state
        if to_state in JOB_STARTING_STATES and \
                self.data.job_state == JobState.IDLE:
            self.job_started(command_id)
        if to_state in JOB_ENDING_STATES and \
                self.data.job_state is JobState.IN_PROGRESS:
            self.change_state(JobState.ENDING)
        if to_state in JOB_DESTROYING_STATES and \
                self.data.job_state is JobState.IN_PROGRESS:
            self.job_ended()

    def tick(self):
        """Called after sending, if the job was ending, it ends now"""
        if self.data.job_state == JobState.ENDING:
            self.job_ended()

    def change_state(self, state: JobState):
        """
        Previously wrote the state into a file, now only logs the state change
        """
        log.debug("Job changed state to %s", state)
        self.data.job_state = state

    def write(self):
        """Writes_the job_id into the printer EEPROM"""
        # TODO: prime candidate for refactoring, it's awful
        # Cannot block
        if self.data.job_id is None:
            return

        enqueue_instruction(self.serial_queue,
                            f"D3 Ax0D05 X{self.data.job_id:08x}",
                            to_front=True)

    def set_file_path(self, path, path_incomplete, prepend_sd_storage):
        """Decides if the supplied file path is better, than what we had
        previously, and updates the job info file parameters accordingly
        :param path: the path/file name to assign to the job
        :param path_incomplete: flag for distinguishing between paths which
        could not be linked to an SD file and those which could
        :param prepend_sd_storage: Whether to prepend the SD Card
        storage name"""
        # If asked to, prepend the SD storage name
        if prepend_sd_storage:
            # Path joins don't work on paths with leading slashes
            if path.startswith("/"):
                path = path[1:]
            log.debug("prepending %s, result = %s", SD_STORAGE_NAME,
                      os.path.join(f"/{SD_STORAGE_NAME}", path))
            path = os.path.join(f"/{SD_STORAGE_NAME}", path)

        log.debug(
            "Processing a file path: %s, incomplete path=%s, "
            "already known path is incomplete=%s, job state=%s, "
            "known path=%s", path, path_incomplete, self.data.path_incomplete,
            self.data.job_state, self.data.selected_file_path)

        # If we have a full path, don't overwrite it with an incomplete one
        if path_incomplete and not self.data.path_incomplete:
            return

        log.debug("Overwriting file path with %s", path)
        self.data.selected_file_path = path
        self.data.path_incomplete = path_incomplete

        if not path_incomplete:
            file_obj = self.printer.fs.get(self.data.selected_file_path)
            if file_obj:
                if "m_timestamp" in file_obj.attrs:
                    self.data.selected_file_m_timestamp = file_obj.attrs[
                        "m_timestamp"]
                if 'size' in file_obj.attrs:
                    self.data.selected_file_size = file_obj.attrs["size"]
        self.model.job.from_sd = path.startswith(
            os.path.join("/", SD_STORAGE_NAME))
        self.update_last_job_path()
        self.job_info_updated()

    def update_last_job_path(self):
        """Updates the last job path to be used for the re-print menu item"""
        if self.data.job_state != JobState.IN_PROGRESS:
            return
        self.data.last_job_path = self.data.selected_file_path

    def get_job_info_data(self, for_connect=False):
        """Compiles the job info data into a dict"""
        if for_connect:
            self.data.already_sent = True

        data = {}

        if self.data.path_incomplete:
            data["path_incomplete"] = self.data.path_incomplete
        if self.data.job_start_cmd_id is not None:
            data["start_cmd_id"] = self.data.job_start_cmd_id
        if self.data.selected_file_path is not None:
            data["path"] = self.data.selected_file_path
        if self.data.selected_file_m_timestamp is not None:
            data["m_timestamp"] = self.data.selected_file_m_timestamp
        if self.data.selected_file_size is not None:
            data["size"] = self.data.selected_file_size
        if self.data.from_sd is not None:
            data["from_sd"] = self.data.from_sd
        if self.printer.mbl is not None:
            data["mbl"] = self.printer.mbl

        return data

    def progress_broken(self, progress_broken):
        """Uses the info about whether the progress percentage reported by
        the printer is broken, to deduce, whether the gcode has inbuilt
        percentage reporting for sd prints."""
        if self.data.from_sd:
            old_inbuilt_reporting = self.data.inbuilt_reporting
            if self.data.inbuilt_reporting is None and progress_broken:
                self.data.inbuilt_reporting = False
            elif not progress_broken:
                self.data.inbuilt_reporting = True

            if old_inbuilt_reporting != self.data.inbuilt_reporting:
                self.job_info_updated()

    def file_position(self, current, total):
        """Call to report a position in a file that's being printed
        :param current: The byte number being printed
        :param total: The file size"""
        self.data.printing_file_byte = current
        if self.data.selected_file_size is not None and \
                self.data.selected_file_size != total:
            log.warning("Reported file sizes differ %s vs %s",
                        self.data.selected_file_size, total)
        if self.data.selected_file_size is None:
            # In the future, this should be pointless, now it may get used
            self.data.selected_file_size = total
            self.job_info_updated()

    def job_info_updated(self):
        """If a job is in progress, a signal about an update will be sent"""
        # The same check as in the job info command, se we aren't trying
        # to send the job info, when it'll just fail instantly
        if self.data.job_state == JobState.IN_PROGRESS \
                and self.data.selected_file_path is not None \
                and self.data.already_sent \
                and self.data.job_id is not None:
            self.job_info_updated_signal.send(self)

    def select_file(self, path):
        """For Octoprint API to select a file to print
        supply only existing file paths

        :param path: The connect path to a file, including the storage name"""
        if self.printer.fs.get(path) is None:
            raise RuntimeError(f"Cannot select a non existing file {path}")
        self.set_file_path(path,
                           path_incomplete=False,
                           prepend_sd_storage=False)

    def deselect_file(self):
        """For Octoprint API to deselect a file
        Only works when IDLE"""
        if self.data.job_state != JobState.IDLE:
            raise RuntimeError("Cannot deselect a file while printing it")
        self.data.selected_file_path = None
        self.model.job.from_sd = None

    def job_id_from_eeprom(self, job_id):
        """Sets the job id read from the printer EEPROM"""
        if self.data.job_id is not None:
            return

        self.data.job_id = job_id
        if self.data.job_id_offset > 0:
            self.data.job_id += self.data.job_id_offset
            self.data.job_id_offset = 0
            self.write()
            self.job_info_updated()
