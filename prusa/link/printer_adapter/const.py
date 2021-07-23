"""
Contains almost every constant for the printer communication part of
Prusa Link
"""
from typing import List

from prusa.connect.printer.const import State

BASE_STATES = {State.READY, State.BUSY}
PRINTING_STATES = {State.PRINTING, State.PAUSED}

JOB_ONGOING_STATES = {State.PRINTING, State.PAUSED}
JOB_ENDING_STATES = BASE_STATES.union(
    {State.FINISHED, State.STOPPED, State.ERROR})

# --- Intervals ---
# Values are in seconds

TELEMETRY_INTERVAL = 1
TELEMETRY_IDLE_INTERVAL = 0.25
TELEMETRY_PRINTING_INTERVAL = 1
SLOW_TELEMETRY = 10  # for values, that aren't that important
IP_UPDATE_INTERVAL = 2
QUIT_INTERVAL = 0.2
SD_INTERVAL = 0.2
SD_FILESCAN_INTERVAL = 60
FLASH_AIR_INTERVAL = 30
DIR_RESCAN_INTERVAL = 1
PRINTER_BOOT_WAIT = 8
SEND_INFO_RETRY = 5
SERIAL_REOPEN_TIMEOUT = 10
REPORTING_TIMEOUT = 60
FW_MESSAGE_TIMEOUT = 5
STATE_CHANGE_TIMEOUT = 15
IP_WRITE_TIMEOUT = 5
SN_INITIAL_TIMEOUT = 5
SN_OBTAIN_INTERVAL = 5
EXIT_TIMEOUT = 15
ERROR_REASON_TIMEOUT = 2

# --- Lcd queue ---
LCD_QUEUE_SIZE = 30

# --- Serial queue ---
RX_SIZE = 128  # Not used much, limits the max serial message size
SERIAL_QUEUE_TIMEOUT = 25
SERIAL_QUEUE_MONITOR_INTERVAL = 1
HISTORY_LENGTH = 30  # How many messages to remember for Resends

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

# --- Mountpoints ---
MAX_FILENAME_LENGTH = 52
SD_MOUNT_NAME = "SD Card"
BLACKLISTED_TYPES: List[str] = []
BLACKLISTED_PATHS = [
    "/dev",
    "/sys",
    "/proc",
    "/tmp",
]
BLACKLISTED_NAMES = [SD_MOUNT_NAME]
SFN_TO_LFN_EXTENSIONS = {"GCO": "gcode", "G": "g"}

RESET_PIN = 22  # RPi gpio pin for resetting printer
SUPPORTED_FIRMWARE = "3.10.0"
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

# Speed - %
SPEED = {'MIN': 10, 'MAX': 999}
# Feedrate - mm/min
FEEDRATE = {'MIN': 0, 'MAX': 2700}

COORDINATES = {'MIN': 0, 'MAX_X': 250, 'MAX_Y': 210, 'MAX_Z': 210}
