import os

from pydantic import BaseModel

from prusa.link.printer_adapter.settings import Settings

instance = None

class MountPointSettings(BaseModel):
    # Can be used for USB sticks and network attached storage
    MOUNTPOINTS = [  # TODO: Hide into constants, maybe in the future...
    ]
    # Just directories


class PathSettings(BaseModel):
    BASE_TMP_PATH = "/var/tmp/Prusa-Link/"
    TMP_FILE = os.path.join(BASE_TMP_PATH, "currently_printing.gcode")
    PP_FILE = os.path.join(BASE_TMP_PATH, "power_panic")
    JOB_FILE = os.path.join(BASE_TMP_PATH, "job_id_data.json")
    # TODO: Don't save this, the default from constants is as good as this
    THRESHOLD_FILE = os.path.join(BASE_TMP_PATH, "threshold_data")
    CROTITEL_CRONU = os.path.join(BASE_TMP_PATH, "cancel_daily_cron")


class PiSetteings(BaseModel):

    RESET_PIN = 22  # TODO: is a constant!


class SettingsData(BaseModel):

    """ Object supposed to hold all settings """
    MOUNT: MountPointSettings = MountPointSettings()
    PATH: PathSettings = PathSettings()
    PI: PiSetteings = PiSetteings()


def get_settings() -> SettingsData:
    global instance
    if instance is None:
        path = os.path.join('/var/tmp/Prusa-Link', "config.yaml")
        instance = Settings(SettingsData, path)
    return instance.settings
