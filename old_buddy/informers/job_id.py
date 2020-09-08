import logging
import os
from enum import Enum

from blinker import Signal

from old_buddy.default_settings import get_settings
from old_buddy.file_printer import FilePrinter
from old_buddy.informers.state_manager import StateManager, PRINTING_STATES
from old_buddy.input_output.serial import Serial
from old_buddy.structures.model_classes import States
from old_buddy.structures.regular_expressions import FILE_OPEN_REGEX, \
    CANCEL_REGEX, RESUMED_REGEX, PAUSED_REGEX, PRINT_DONE_REGEX, \
    START_PRINT_REGEX
from old_buddy.util import get_clean_path, ensure_directory

LOG = get_settings().LOG
TIME = get_settings().TIME
JOB = get_settings().JOB

log = logging.getLogger(__name__)
log.setLevel(LOG.JOB_ID_LOG_LEVEL)


class JobState(Enum):
    NONE = "NONE"
    READY_TO_START = "READY_TO_START"
    IN_PROGRESS = "IN_PROGRESS"
    PAUSED = "PAUSED"


class JobID:

    def __init__(self, serial: Serial, file_printer: FilePrinter,
                 state_manager: StateManager):
        self.job_started_signal = Signal()
        self.job_ended_signal = Signal()

        self.serial = serial
        self.file_printer = file_printer
        self.state_manager = state_manager

        self.job_path = get_clean_path(JOB.JOB_FILE)
        ensure_directory(os.path.dirname(self.job_path))

        if os.path.exists(self.job_path):
            with open(self.job_path, "r") as job_file:
                data_parts = job_file.read().split(" ")
                self.job_id = int(data_parts[0])
                self.job_state = JobState(data_parts[1])
        else:
            self.job_id = 0
            self.job_state = JobState.NONE

        self.serial.add_output_handler(FILE_OPEN_REGEX,
                                       lambda match: self.file_opened())
        self.serial.add_output_handler(START_PRINT_REGEX,
                                       lambda match: self.print_started())
        self.serial.add_output_handler(PRINT_DONE_REGEX,
                                       lambda match: self.print_end())
        self.serial.add_output_handler(CANCEL_REGEX,
                                       lambda match: self.print_end())
        self.serial.add_output_handler(PAUSED_REGEX,
                                       lambda match: self.print_paused())
        self.serial.add_output_handler(RESUMED_REGEX,
                                       lambda match: self.print_resumed())
        self.state_manager.state_changed_signal.connect(
            lambda sender, command_id, source: self.printer_state_changed(),
            weak=False)

        self.file_printer.new_print_started_signal.connect(
            lambda sender: self.job_started(),
            weak=False)
        self.file_printer.print_ended_signal.connect(
            lambda sender: self.job_ended(),
            weak=False)

    def job_started(self):
        self.job_id += 1
        log.debug(f"New job started, id = {self.job_id}")
        self.job_started_signal.send(self, job_id=self.job_id)
        self.write()

    def job_ended(self):
        log.debug(f"Job ended")
        self.job_ended_signal.send(self)

    def file_opened(self):
        if self.job_state in {JobState.NONE, JobState.PAUSED}:
            if self.job_state == JobState.PAUSED:
                self.job_ended()
            self.change_state(JobState.READY_TO_START)

    def print_started(self):
        if self.job_state == JobState.READY_TO_START:
            self.job_started()
            self.change_state(JobState.IN_PROGRESS)

    def print_end(self):
        if self.job_state in {JobState.IN_PROGRESS, JobState.PAUSED}:
            self.job_ended()
        self.change_state(JobState.NONE)

    def print_paused(self):
        if self.job_state == JobState.IN_PROGRESS:
            self.change_state(JobState.PAUSED)

    def print_resumed(self):
        if self.job_state == JobState.PAUSED:
            self.change_state(JobState.IN_PROGRESS)

    def printer_state_changed(self):
        if self.state_manager.last_state in PRINTING_STATES:
            if self.state_manager.current_state in {States.READY, States.BUSY,
                                                    States.ERROR}:
                self.print_end()

    def change_state(self, state: JobState):
        log.debug(f"Job changed state to {state}")
        self.job_state = state
        self.write()

    def write(self):
        with open(self.job_path, "w") as job_file:
            job_file.write(f"{self.job_id} {self.job_state.value}")
