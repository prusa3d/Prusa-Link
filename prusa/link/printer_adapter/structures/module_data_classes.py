"""
Decided that keeping module data externally will aid with gathering them for
the api, definitions of which is what this module contains
"""
from typing import Deque, Optional, Set, List, Any, Dict

from pydantic import BaseModel

from prusa.connect.printer.const import State

from .model_classes import JobState, SDState

# pylint: disable=too-few-public-methods


class FilePrinterData(BaseModel):
    """Data of the FilePrinter class"""
    tmp_file_path: str
    pp_file_path: str
    printing: bool
    paused: bool
    stopped_forcefully: bool
    line_number: int

    # In reality Deque[Instruction] but that cannot be validated by pydantic
    enqueued: Deque[Any]
    gcode_number: int


class StateManagerData(BaseModel):
    """Data of the StateManager class"""
    # The ACTUAL states considered when reporting
    base_state: State
    printing_state: Optional[State]
    override_state: Optional[State]

    # Reported state history
    last_state: State
    current_state: State
    state_history: Deque[State]
    error_count: int


class JobData(BaseModel):
    """Data of the Job class"""
    already_sent: Optional[bool]
    job_start_cmd_id: Optional[int]
    selected_file_path: Optional[str]
    selected_file_m_time: Optional[str]
    selected_file_size: Optional[str]
    printing_file_byte: Optional[int]
    path_incomplete: Optional[bool]
    from_sd: Optional[bool]
    inbuilt_reporting: Optional[bool]

    job_id: int
    job_state: JobState

    def get_job_id_for_api(self):
        """
        The API does not send None values. This function returns None when
        no job is running, otherwise it gives the job_id
        """
        if self.job_state == JobState.IDLE:
            return None
        return self.job_id


class IPUpdaterData(BaseModel):
    """Data of the IpUpdater class"""
    local_ip: str
    update_ip_on: float


class SDCardData(BaseModel):
    """Data of the SDCard class"""
    expecting_insertion: bool
    invalidated: bool
    is_flash_air: bool
    last_updated: float
    last_checked_flash_air: float
    sd_state: SDState
    files: Any  # We cannot type-check SDFile, only basic ones
    sfn_to_lfn_paths: Dict[str, str]
    lfn_to_sfn_paths: Dict[str, str]
    mixed_to_lfn_paths: Dict[str, str]


class MountsData(BaseModel):
    """Data of the Mounts class"""
    blacklisted_paths: List[str]
    blacklisted_names: List[str]
    configured_mounts: Set[str]
    mounted_set: Set[str]


class PrintStatsData(BaseModel):
    """Data of the PrintStats class"""
    print_time: float
    segment_start: float
    has_inbuilt_stats: bool
    total_gcode_count: int  # is not computed for files containg reporting
    #                         to speed stuff up
