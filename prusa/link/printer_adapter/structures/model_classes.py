"""
Contains models that were originally intended for sending to the connect.
Pydantic makes a great tool for cleanly serializing simple python objects,
while enforcing their type
"""
from enum import Enum
from typing import Optional, List

from pydantic import BaseModel

from prusa.connect.printer.const import State

from ... import __version__


class Telemetry(BaseModel):
    """The Telemetry model"""

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


class FileTree(BaseModel):
    """The File Tree model"""

    type: Optional[str] = None
    name: Optional[str] = None
    ro: Optional[bool] = None
    size: Optional[int] = None
    m_date: Optional[int] = None
    m_time: Optional[int] = None
    children: Optional[List["FileTree"]] = None


FileTree.update_forward_refs()


class PrinterInfo(BaseModel):
    """The printer Info model"""

    type: Optional[int] = None
    version: Optional[int] = None
    subversion: Optional[int] = None
    firmware: Optional[str] = None
    wui: Optional[str] = __version__
    network_info: Optional[NetworkInfo] = None
    sn: Optional[str] = None
    uuid: Optional[str] = None
    appendix: Optional[bool] = None
    state: Optional[str] = None
    files: Optional[FileTree] = None
    nozzle_diameter: Optional[float] = None

    def set_printer_model_info(self, data):
        """Setter expanding a tuple into model fields"""
        self.type, self.version, self.subversion = data


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
