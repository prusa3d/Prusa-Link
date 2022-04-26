"""
Contains models that were originally intended for sending to the connect.
Pydantic makes a great tool for cleanly serializing simple python objects,
while enforcing their type
"""
from enum import Enum
from typing import Optional

from pydantic import BaseModel

from prusa.connect.printer.const import State


JITTER_FILTERED = {"temp_nozzle", "temp_bed"}  # have to have float values


class Telemetry(BaseModel):
    """The Telemetry model"""
    # time_estimated is deprecated, kept for compatibility

    temp_nozzle: Optional[float] = None
    temp_bed: Optional[float] = None
    target_nozzle: Optional[float] = None
    target_bed: Optional[float] = None
    axis_x: Optional[float] = None
    axis_y: Optional[float] = None
    axis_z: Optional[float] = None
    fan_extruder: Optional[int] = None
    fan_print: Optional[int] = None
    target_fan_extruder: Optional[int] = None
    target_fan_print: Optional[int] = None
    progress: Optional[int] = None
    filament: Optional[str] = None
    flow: Optional[int] = None
    speed: Optional[int] = None
    time_printing: Optional[int] = None
    time_estimated: Optional[int] = None
    time_remaining: Optional[int] = None
    odometer_x: Optional[int] = None
    odometer_y: Optional[int] = None
    odometer_z: Optional[int] = None
    odometer_e: Optional[int] = None
    material: Optional[str] = None
    state: Optional[State] = None
    job_id: Optional[int] = None


class NetworkInfo(BaseModel):
    """The Network Info model"""

    lan_ipv4: Optional[str] = None  # not implemented yet
    lan_ipv6: Optional[str] = None  # not implemented yet
    lan_mac: Optional[str] = None  # not implemented yet
    wifi_ipv4: Optional[str] = None
    wifi_ipv6: Optional[str] = None  # not implemented yet
    wifi_mac: Optional[str] = None
    wifi_ssid: Optional[str] = None  # not implemented yet
    hostname: Optional[str] = None
    username: Optional[str] = None
    digest: Optional[str] = None


class FileType(Enum):
    """File type enum"""
    FILE = "FILE"
    DIR = "DIR"
    MOUNT = "MOUNT"


class JobState(Enum):
    """Job state enum"""
    IDLE = "IDLE"
    IN_PROGRESS = "IN_PROGRESS"
    ENDING = "ENDING"


class SDState(Enum):
    """SD State enum"""
    PRESENT = "PRESENT"
    INITIALISING = "INITIALISING"
    UNSURE = "UNSURE"
    ABSENT = "ABSENT"


class PrintState(Enum):
    """States which the printer can report on its own"""
    SD_PRINTING = "SD_PRINTING"
    SD_PAUSED = "SD_PAUSED"
    SERIAL_PAUSED = "SERIAL_PAUSED"
    NOT_SD_PRINTING = "NOT_SD_PRINTING"


class PrintMode(Enum):
    """The "Mode" from the printer LCD settings"""
    SILENT = "SILENT"
    NORMAL = "NORMAL"
    AUTO = "AUTO"
