from enum import Enum
from typing import Optional, List, Dict, Any

from pydantic import BaseModel

from old_buddy import __version__


class Telemetry(BaseModel):

    temp_nozzle: Optional[float] = None
    temp_bed: Optional[float] = None
    target_nozzle: Optional[float] = None
    target_bed: Optional[float] = None
    axis_x: Optional[float] = None
    axis_y: Optional[float] = None
    axis_z: Optional[float] = None
    fan_extruder: Optional[int] = None
    fan_print: Optional[int] = None
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
    state: str = None


class NetworkInfo(BaseModel):

    lan_ipv4: Optional[str] = None    # not implemented yet
    lan_ipv6: Optional[str] = None    # not implemented yet
    lan_mac: Optional[str] = None     # not implemented yet
    wifi_ipv4: Optional[str] = None
    wifi_ipv6: Optional[str] = None   # not implemented yet
    wifi_mac: str = None
    wifi_ssid: Optional[str] = None   # not implemented yet


class FileTree(BaseModel):

    type: str = None
    path: str = None
    ro: Optional[bool] = None
    size: int = None
    m_date: Optional[int] = None
    m_time: Optional[int] = None
    children: List["FileTree"] = None


FileTree.update_forward_refs()


class Event(BaseModel):

    event: str = None
    source: Optional[str] = None
    values: Optional[Dict[str, Any]] = None
    command_id: Optional[int] = None
    command: Optional[str] = None
    reason: Optional[str] = None
    root: Optional[str] = None
    files: Optional[FileTree] = None


class PrinterInfo(BaseModel):

    type: int = None
    version: int = None
    subversion: int = None
    firmware: str = None
    wui: str = __version__
    network_info: NetworkInfo = None
    sn: str = None
    uuid: str = None
    appendix: bool = None
    state: str = None
    files: FileTree = None

    def set_printer_model_info(self, data):
        self.type, self.version, self.subversion = data


class EmitEvents(Enum):
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    FINISHED = "FINISHED"
    INFO = "INFO"
    STATE_CHANGED = "STATE_CHANGED"
    MEDIUM_EJECTED = "MEDIUM_EJECTED"
    MEDIUM_INSERTED = "MEDIUM_INSERTED"


class Sources(Enum):
    WUI = "WUI"
    MARLIN = "MARLIN"
    USER = "USER"
    CONNECT = "CONNECT"


class States(Enum):
    READY = "READY"
    BUSY = "BUSY"
    PRINTING = "PRINTING"
    PAUSED = "PAUSED"
    FINISHED = "FINISHED"
    ERROR = "ERROR"
    ATTENTION = "ATTENTION"


class FileType(Enum):
    FILE = "FILE"
    DIR = "DIR"
    MOUNT = "MOUNT"
