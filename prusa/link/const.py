"""
Contains almost every constant for the printer communication part of
PrusaLink
"""
import uuid
from importlib.resources import files  # type: ignore
from os import path
from typing import List

from bidict import bidict
from packaging.version import Version

from prusa.connect.printer.const import PrinterType, State

from .printer_adapter.structures.model_classes import PrintMode, PrintState

instance_id = uuid.uuid4()

# e.g. Mon, 07 Nov 2022 13:52:49 GMT
HEADER_DATETIME_FORMAT = "%a, %d %b %Y %X GMT"

PRINTER_TYPES = {
    250: PrinterType.I3MK25,
    252: PrinterType.I3MK25S,
    20250: PrinterType.I3MK25,
    20252: PrinterType.I3MK25S,
    300: PrinterType.I3MK3,
    20300: PrinterType.I3MK3,
    302: PrinterType.I3MK3S,
    20302: PrinterType.I3MK3S,
    30302: PrinterType.I3MK3S,
}

MMU3_TYPE_CODE = 30302

PRINTER_CONF_TYPES = bidict({
    "MK2.5": PrinterType.I3MK25,
    "MK2.5S": PrinterType.I3MK25S,
    "MK3": PrinterType.I3MK3,
    "MK3S": PrinterType.I3MK3S,
})

DATA_PATH = path.abspath(path.join(str(files('prusa.link')), 'data'))

BASE_STATES = {State.IDLE, State.BUSY, State.READY}
PRINTING_STATES = {State.PRINTING, State.PAUSED, State.FINISHED, State.STOPPED}

MK25_PRINTERS = {PrinterType.I3MK25.value, PrinterType.I3MK25S.value}

JOB_STARTING_STATES = {State.PRINTING, State.PAUSED}
JOB_ENDING_STATES = {
    State.FINISHED,
    State.STOPPED,
}
JOB_DESTROYING_STATES = {
     State.ERROR,
     State.IDLE,  # These are needed for the job to end through ATTENTION
     State.BUSY,
     }

JITTER_THRESHOLD = 0.5
PRUSA_VENDOR_ID = "2c99"

# --- Intervals ---
# Values are in seconds

TELEMETRY_IDLE_INTERVAL = 0.25
TELEMETRY_PRINTING_INTERVAL = 1
TELEMETRY_SLEEPING_INTERVAL = 4  # can be sleeping in any state
TELEMETRY_SLEEP_AFTER = 3 * 60
TELEMETRY_REFRESH_INTERVAL = 5 * 60  # full telemetry re-send

FAST_POLL_INTERVAL = 1
SLOW_POLL_INTERVAL = 10  # for values, that aren't that important
VERY_SLOW_POLL_INTERVAL = 30
IP_UPDATE_INTERVAL = 5
QUIT_INTERVAL = 0.2
SD_INTERVAL = 0.2
SD_FILESCAN_INTERVAL = 60
DIR_RESCAN_INTERVAL = 1
PRINTER_BOOT_WAIT = 8
SEND_INFO_RETRY = 5
SERIAL_REOPEN_TIMEOUT = 2
REPORTING_TIMEOUT = 60
FW_MESSAGE_TIMEOUT = 10
STATE_CHANGE_TIMEOUT = 15
IP_WRITE_TIMEOUT = 5
SN_OBTAIN_INTERVAL = 5
EXIT_TIMEOUT = 15
ERROR_REASON_TIMEOUT = 2
PATH_WAIT_TIMEOUT = 10
SLEEP_SCREEN_TIMEOUT = 20
SELF_PING_TIMEOUT = 5
SELF_PING_RETRY_INTERVAL = 10
ATTENTION_CLEAR_INTERVAL = 5
CAMERA_INIT_DELAY = 2
CAMERA_SCAN_INTERVAL = 30
CAMERA_REGISTER_TIMEOUT = 5
TIME_FOR_SNAPSHOT = 1
PRINT_END_TIMEOUT = 11
KEEPALIVE_INTERVAL = 12

PP_MOVES_DELAY = 20

# --- Lcd queue ---
LCD_QUEUE_SIZE = 30

# --- Serial queue ---
RX_SIZE = 128  # Not used much, limits the max serial message size
SERIAL_QUEUE_TIMEOUT = 25
SERIAL_QUEUE_MONITOR_INTERVAL = 1
HISTORY_LENGTH = 100  # How many messages to remember for Resends

# --- Is planner fed ---
QUEUE_SIZE = 10000  # From how many messages to compute the percentile
HEAP_RATIO = 0.95  # What percentile to compute
IGNORE_ABOVE = 1.0  # Ignore instructions, that take longer than x sec
DEFAULT_THRESHOLD = 0.13  # Percentile for uninitialised component
USE_DYNAMIC_THRESHOLD = True  # Compute the percentile or use a fixed value?

# --- File printer ---
STATS_EVERY = 100
TAIL_COMMANDS = 10  # how many commands after the last progress report
PRINT_QUEUE_SIZE = 4

# --- Storage ---
MAX_FILENAME_LENGTH = 52
SD_STORAGE_NAME = "SD Card"
BLACKLISTED_TYPES: List[str] = []
BLACKLISTED_PATHS = [
    "/dev",
    "/sys",
    "/proc",
    "/tmp",
]
BLACKLISTED_NAMES = [SD_STORAGE_NAME]
SFN_TO_LFN_EXTENSIONS = {"GCO": "gcode", "G": "g", "GC": "gc"}

RESET_PIN = 22  # RPi gpio pin for resetting printer
SUPPORTED_FIRMWARE = "3.14.0"
MINIMAL_FIRMWARE = Version(SUPPORTED_FIRMWARE)
MAX_INT = (2**31) - 1
STATE_HISTORY_SIZE = 10

# --- Interesting_Logger ---
LOG_BUFFER_SIZE = 200
AFTERMATH_LOG_SIZE = 100

# --- Selected log files---
GZ_SUFFIX = ".gz"
LOGS_PATH = "/var/log"
LOGS_FILES = ("auth.log", "daemon.log", "kern.log", "messages", "syslog",
              "user.log")


# --- Hardware limits for commands ---
class LimitsFDM:
    """Generic FDM Limits object"""

    # --- Printer Object info ---
    id: str
    name: str
    type: int
    version: int
    subversion: int

    # --- Hardware limits ---
    extrusion_min = -10
    extrusion_max = 100
    feedrate_e_min = 0
    feedrate_e_max = 100
    feedrate_x_min = 0
    feedrate_x_max = 2700
    feedrate_y_min = 0
    feedrate_y_max = 2700
    feedrate_z_min = 0
    feedrate_z_max = 1000
    min_temp_nozzle_e = 170
    position_x_min = 0
    position_x_max = 255
    position_y_min = -4
    position_y_max = 212.5
    position_z_min = 0.15
    position_z_max = 210
    print_flow_min = 10
    print_flow_max = 999
    print_speed_min = 10
    print_speed_max = 999
    temp_bed_min = 0
    temp_bed_max = 125
    temp_nozzle_min = 0
    temp_nozzle_max = 305


class LimitsMK25(LimitsFDM):
    """Printer MK2.5 Limits object"""
    id = '1.2.5'
    name = 'Original Prusa i3 MK2.5'
    type = 1
    version = 2
    subversion = 5


class LimitsMK25S(LimitsFDM):
    """Printer MK2.5S Limits object"""
    id = '1.2.6'
    name = 'Original Prusa i3 MK2.5S'
    type = 1
    version = 2
    subversion = 6


class LimitsMK3(LimitsFDM):
    """Printer MK3 Limits object"""
    id = '1.3.0'
    name = 'Original Prusa i3 MK3'
    type = 1
    version = 3
    subversion = 0


class LimitsMK3S(LimitsFDM):
    """Printer MK3S Limits object"""
    id = '1.3.1'
    name = 'Original Prusa i3 MK3S'
    type = 1
    version = 3
    subversion = 1


PRINT_STATE_PAIRING = {
    "sdn_lfn": PrintState.SD_PRINTING,
    "sd_paused": PrintState.SD_PAUSED,
    "serial_paused": PrintState.SERIAL_PAUSED,
    "no_print": PrintState.NOT_SD_PRINTING,
}

PRINT_MODE_PAIRING = {"SILENT": PrintMode.SILENT, "NORMAL": PrintMode.NORMAL}

PRINT_MODE_ID_PAIRING = {
    0: PrintMode.NORMAL,
    1: PrintMode.SILENT,
    2: PrintMode.AUTO,
}

# keys are the manufacturer ids, values are supported models
SUPPORTED_PRINTERS = {
    "2c99": {"0001", "0002"},
}

MMU_SLOTS = 5

MMU_PROGRESS_MAP = {
    "OK": 0,

    "Engaging idler": 1,
    "Disengaging idler": 2,
    "Unloading to FINDA": 3,
    "Unloading to pulley": 4,
    "Feeding to FINDA": 5,
    "Feeding to extruder": 6,
    "Feeding to nozzle": 7,
    "Avoiding grind": 8,
    "ERR Disengaging idler": 10,
    "ERR Engaging idler": 11,
    "ERR Wait for User": 12,
    "ERR Internal": 13,
    "ERR Help filament": 14,
    "ERR TMC failed": 15,
    "Selecting fil. slot": 18,
    "Preparing blade": 19,
    "Pushing filament": 20,
    "Performing cut": 21,
    "Returning selector": 22,
    "Ejecting filament": 24,
    "Parking selector": 23,
    "Retract from FINDA": 25,
    "Homing": 26,
    "Moving selector": 27,
    "Feeding to FSensor": 28,
}

MMU_ERROR_MAP = {
    0x8001: 101,  # FINDA didn't switch on -> FINDA didn't trigger
    0x8002: 102,  # FINDA didn't switch off -> FINDA filament stuck
    0x8003: 103,  # Filament sensor didn't switch on -> FSENSOR didn't trigger
    0x8004: 104,  # Filament sensor didn't switch off -> FSENSOR filament stuck
    0x800b: 105,  # MOVE_PULLEY_FAILED -> MECHANICAL pulley cannot move
    0x8009: 106,  # FSensor triggered too early -> MECHANICAL FSENSOR too early
    0x800a: 107,  # FINDA flickers -> MECHANICAL inspect FINDA
    0x802a: 108,  # LOAD_TO_EXTRUDER_FAILED -> Loading to extruder failed.
    #               Inspect the filament tip shape. Refine the sensor
    #               calibration, if needed

    0x8007 | 0x0080: 115,  # Selector homing failed
    0x800b | 0x0080: 116,  # Selector move failed
    0x8007 | 0x0100: 125,  # Idler homing failed
    0x800b | 0x0100: 126,  # Idler move failed

    0xA000: 201,  # TMC_OVER_TEMPERATURE_WARN -> Temperature warning TMC pulley
    #               too hot
    0xC000: 202,  # TMC_OVER_TEMPERATURE_ERROR -> Temperature TMC pulley
    #               overheat error

    # Temperature errors for the selector driver
    0xA000 | 0x0080: 211,  # Temperature warning TMC selector too hot
    0xC000 | 0x0080: 212,  # Temperature TMC selector overheat error

    # Temperature errors for the idler driver
    0xA000 | 0x0100: 221,  # Temperature warning TMC idler too hot
    0xC000 | 0x0100: 222,  # Temperature TMC idler overheat error

    # Electrical errors
    0x8200: 301,  # TMC_IOIN_MISMATCH -> Electrical TMC pulley driver error
    0x8400: 302,  # TMC_RESET -> Electrical TMC pulley driver reset
    0x8800: 303,  # TMC_UNDERVOLTAGE_ON_CHARGE_PUMP -> Electrical TMC
    #               pulley undervoltage error
    0x9000: 304,  # TMC_SHORT_TO_GROUND -> Electrical TMC pulley driver shorted
    0xC200: 305,  # ERR_ELECTRICAL_MMU_PULLEY_SELFTEST_FAILED -> Electrical
    #               TMC pulley selftest failed

    # Electrical errors for the selector driver
    0x8200 | 0x0080: 311,  # Electrical TMC selector driver error
    0x8400 | 0x0080: 312,  # Electrical TMC selector driver reset
    0x8800 | 0x0080: 313,  # Electrical TMC selector undervoltage error
    0x9000 | 0x0080: 314,  # Electrical TMC selector driver shorted
    0xC200 | 0x0080: 315,  # Electrical TMC selector selftest failed

    # Electrical errors for the idler driver
    0x8200 | 0x0100: 321,  # Electrical TMC idler driver error
    0x8400 | 0x0100: 322,  # Electrical TMC idler driver reset
    0x8800 | 0x0100: 323,  # Electrical TMC idler undervoltage error
    0x9000 | 0x0100: 324,  # Electrical TMC idler driver shorted
    0xC200 | 0x0100: 325,  # Electrical TMC idler selftest failed

    0x0800d: 306,  # MMU MCU detected a 5V undervoltage. There might be an
    #                issue with the electronics. Check the wiring and
    #                connectors

    # Connectivity errors
    0x802e: 401,  # MMU not responding -> CONNECT MMU not responding
    0x802d: 402,  # MMU not responding correctly. Check the wiring and
                  # connectors

    # System errors
    0x8005: 501,  # Filament already loaded -> SYSTEM filament already loaded
    0x8006: 502,  # Invalid tool -> SYSTEM invalid tool
    0x802b: 503,  # QUEUE_FULL -> MMU Firmware internal error, please reset
    #               the MMU
    0x802c: 504,  # VERSION_MISMATCH -> The MMU firmware version is
    #               incompatible with the printer's FW. Update to compatible
    #               version
    0x802f: 505,  # PROTOCOL_ERROR -> Internal runtime error. Try resetting
    #               the MMU or updating the firmware
    0x8008: 506,  # FINDA_VS_EEPROM_DISCREPANCY -> Unload manually
    0x800c: 507,  # Filament was ejected -> SYSTEM filament ejected
    0x8029: 508,  # FILAMENT_CHANGE -> SYSTEM filament change

}
