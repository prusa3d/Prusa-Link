"""Enum classes in a module"""
from enum import Enum


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
