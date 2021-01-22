from typing import Deque, Optional, Set, List, Any

from pydantic import BaseModel

from prusa.connect.printer.const import State
from prusa.link.printer_adapter.structures.model_classes import JobState, \
    SDState


class FilePrinterData(BaseModel):
    tmp_file_path: Optional[str]
    pp_file_path: Optional[str]
    printing: Optional[bool]
    paused: Optional[bool]
    line_number: Optional[int]
    enqueued: Optional[Deque]
    gcode_number: Optional[int]


class StateManagerData(BaseModel):
    # The ACTUAL states considered when reporting
    base_state: Optional[State] = State.READY
    printing_state: Optional[State] = None
    override_state: Optional[State] = None

    # Reported state history
    last_state: Optional[State]
    current_state: Optional[State]

    # Non ideal, we are expecting for someone to ask for progress or
    # to tell us without us asking. Cannot take it from telemetry
    # as it depends on us
    progress: Optional[int]


class JobData(BaseModel):
    job_start_cmd_id: Optional[int]
    printing_file_path: Optional[str]
    filename_only: Optional[bool]
    from_sd: Optional[bool]

    job_id: Optional[int]
    api_job_id: Optional[int]
    job_state: Optional[JobState]
    filename_only: Optional[bool]


class IpUpdaterData(BaseModel):
    local_ip: Optional[str]
    update_ip_on: Optional[float]


class SDCardData(BaseModel):
    expecting_insertion: Optional[bool]
    invalidated: Optional[bool]
    last_updated: Optional[float]
    sd_state: Optional[SDState]
    files: Optional[Any]  # We cannot type-check SDFile, only basic ones


class MountsData(BaseModel):
    blacklisted_paths: Optional[List[str]]
    blacklisted_names: Optional[List[str]]
    configured_mounts: Optional[Set[str]]
    mounted_set: Optional[Set[str]]