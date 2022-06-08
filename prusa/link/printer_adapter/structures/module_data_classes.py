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
    file_path: str
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
    awaiting_error_reason: bool


class JobData(BaseModel):
    """Data of the Job class"""
    job_id: Optional[int]
    job_id_offset: int
    already_sent: Optional[bool]
    job_start_cmd_id: Optional[int]
    selected_file_path: Optional[str]
    selected_file_m_timestamp: Optional[int]
    selected_file_size: Optional[str]
    printing_file_byte: Optional[int]
    path_incomplete: Optional[bool]
    from_sd: Optional[bool]
    inbuilt_reporting: Optional[bool]

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
    local_ip: Optional[str]
    local_ip6: Optional[str]
    mac: Optional[str]
    is_wireless: bool
    update_ip_on: float
    ssid: Optional[str]
    hostname: Optional[str]
    username: Optional[str]
    digest: Optional[str]


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


class StorageData(BaseModel):
    """Data of the Storage class"""
    blacklisted_paths: List[str]
    blacklisted_names: List[str]
    configured_storage: Set[str]
    attached_set: Set[str]


class PrintStatsData(BaseModel):
    """Data of the PrintStats class"""
    print_time: float
    segment_start: float
    has_inbuilt_stats: bool
    total_gcode_count: int  # is not computed for files containg reporting
    #                         to speed stuff up


class Sheet(BaseModel):
    """Data available for sheets in the printer EEPROM"""
    name: str = ""
    z_offset: float = 0.0
    # temps at the time of calibration
    bed_temp: int = 0
    pinda_temp: int = 0
