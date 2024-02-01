"""
Contains models that were originally intended for sending to the connect.
Pydantic makes a great tool for cleanly serializing simple python objects,
while enforcing their type
"""
from enum import Enum
from typing import Dict, Optional

from pydantic import BaseModel


class IndividualSlot(BaseModel):
    """Support the slot number specific telemetry structure"""
    material: Optional[str] = None
    temp: Optional[float] = None
    fan_hotend: Optional[int] = None
    fan_print: Optional[int] = None


class Slot(BaseModel):
    """Support the telemetry item described here:
    https://connect.prusa3d.com/docs/mmu (Internal doc)"""

    active: Optional[int] = None
    state: Optional[int] = None
    progress: Optional[int] = None
    command: Optional[str] = None
    slots: Optional[Dict[str, IndividualSlot]] = None

    def dict(self, **kwargs) -> Dict:
        """Override the dict method to respect the Connect telemetry API"""
        data = super().dict(**kwargs)
        if "slots" in data and data["slots"] is not None:
            slots = data.pop("slots")
            data.update(slots)
        return data


class Telemetry(BaseModel):
    """The Telemetry model"""
    # time_remaining is deprecated, kept for compatibility

    temp_nozzle: Optional[float] = None
    temp_bed: Optional[float] = None
    target_nozzle: Optional[float] = None
    target_bed: Optional[float] = None
    axis_x: Optional[float] = None
    axis_y: Optional[float] = None
    axis_z: Optional[float] = None
    fan_extruder: Optional[int] = None
    fan_hotend: Optional[int] = None
    fan_print: Optional[int] = None
    target_fan_extruder: Optional[int] = None
    target_fan_hotend: Optional[int] = None
    target_fan_print: Optional[int] = None
    progress: Optional[int] = None
    filament: Optional[str] = None
    flow: Optional[int] = None
    speed: Optional[int] = None
    time_printing: Optional[int] = None
    time_transferring: Optional[int] = None
    time_remaining: Optional[int] = None
    odometer_x: Optional[int] = None
    odometer_y: Optional[int] = None
    odometer_z: Optional[int] = None
    odometer_e: Optional[int] = None
    material: Optional[str] = None
    total_filament: Optional[int] = None
    total_print_time: Optional[int] = None
    filament_change_in: Optional[int] = None
    inaccurate_estimates: Optional[bool] = None
    slot: Optional[Slot] = None

    def dict(self, **kwargs) -> Dict:
        data = super().dict(**kwargs)
        if self.slot is not None:
            data['slot'] = self.slot.dict(**kwargs)
        return data


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
    FOLDER = "FOLDER"
    STORAGE = "STORAGE"


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


class EEPROMParams(Enum):
    """List of EEPROM addresses read by PrusaLink"""
    JOB_ID = 0x0D05, 4
    FLASH_AIR = 0x0FBB, 1
    PRINT_MODE = 0x0FFF, 1
    SHEET_SETTINGS = 0x0D49, 88
    ACTIVE_SHEET = 0x0DA1, 1
    TOTAL_FILAMENT = 0x0FF1, 4
    TOTAL_PRINT_TIME = 0x0FED, 4
    EEPROM_FILE_POSITION = 0x0F91, 4


class PPData(BaseModel):
    """Not things like length or diameter,
    just path and the command number -> gcode command number"""
    file_path: str
    connect_path: str
    message_number: int  # N number on the printer
    gcode_number: int  # From file printer
    using_rip_port: bool = False
