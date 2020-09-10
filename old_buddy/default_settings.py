import os
from os.path import expanduser

from appdirs import user_config_dir
from pydantic import BaseModel

from old_buddy.settings import Settings

HOME = expanduser("~")

instance = None


class SerialSettings(BaseModel):
    PRINTER_PORT = "/dev/ttyAMA0"
    PRINTER_BAUDRATE = 115200


class ConnectSettings(BaseModel):
    CONNECT_CONFIG_PATH = "/boot/lan_settings.ini"


class IntervalSettings(BaseModel):
    # Times are in seconds
    TELEMETRY_INTERVAL = 1
    TELEMETRY_SEND_INTERVAL = 0.5
    STATUS_UPDATE_INTERVAL = 2
    QUIT_INTERVAL = 0.5
    SD_INTERVAL = 5
    STORAGE_INTERVAL = 15
    SHOW_IP_INTERVAL = 60
    SERIAL_REOPEN_INTERVAL = 1


class LCDQueueSettings(BaseModel):
    LCD_QUEUE_SIZE = 20


class SerialQueueSettings(BaseModel):
    RX_SIZE = 128
    SERIAL_QUEUE_TIMEOUT = 10
    SERIAL_QUEUE_MONITOR_INTERVAL = 1


class LogLevelSettings(BaseModel):
    OLD_BUDDY_LOG_LEVEL = "DEBUG"
    SERIAL_LOG_LEVEL = "DEBUG"
    CONNECT_API_LOG_LEVEL = "DEBUG"
    STATE_MANAGER_LOG_LEVEL = "DEBUG"
    COMMANDS_LOG_LEVEL = "DEBUG"
    LCD_PRINTER_LOG_LEVEL = "DEBUG"
    SD_CARD_LOG_LEVEL = "DEBUG"
    IP_UPDATER_LOG_LEVEL = "DEBUG"
    TELEMETRY_GATHERER_LOG_LEVEL = "DEBUG"
    INFO_SENDER_LOG_LEVEL = "DEBUG"
    SERIAL_QUEUE_LOG_LEVEL = "DEBUG"
    MOUNTPOINT_LOG_LEVEL = "DEBUG"
    LINUX_FILESYSTEM_LOG_LEVEL = "DEBUG"
    STORAGE_LOG_LEVEL = "DEBUG"
    FILE_PRINTER_LOG_LEVEL = "DEBUG"
    JOB_ID_LOG_LEVEL = "DEBUG"


class MountPointSettings(BaseModel):
    # Can be used for USB sticks and network attached storage
    MOUNTPOINTS = [
    ]
    # Just directories
    DIRECTORIES = [
        "/home/pi/Old Buddy gcodes"
    ]
    BLACKLISTED_TYPES = [
    ]
    BLACKLISTED_PATHS = [
        "/dev",
        "/sys",
        "/proc",
        "/tmp",
    ]
    BLACKLISTED_NAMES = [
        "SD Card"
    ]


class FilePrinterSettings(BaseModel):
    TMP_FILE = "/var/tmp/Old Buddy/currently_printing.gcode"
    PP_FILE = "/var/tmp/Old Buddy/power_panic"


class JobIDSettings(BaseModel):
    JOB_FILE = "/var/tmp/Old Buddy/job_id_data"


class SettingsData(BaseModel):
    """ Object supposed to hold all settings """
    CONN: ConnectSettings = ConnectSettings()
    SERIAL: SerialSettings = SerialSettings()
    TIME: IntervalSettings = IntervalSettings()
    LCDQ: LCDQueueSettings = LCDQueueSettings()
    SQ: SerialQueueSettings = SerialQueueSettings()
    LOG: LogLevelSettings = LogLevelSettings()
    MOUNT: MountPointSettings = MountPointSettings()
    PRINT: FilePrinterSettings = FilePrinterSettings()
    JOB: JobIDSettings = JobIDSettings()


def get_settings() -> SettingsData:
    global instance
    if instance is None:
        config_dir = user_config_dir("Old-Buddy", "PrusaResearch")
        path = os.path.join(config_dir, "config.yaml")
        instance = Settings(SettingsData, path)
    return instance.settings

