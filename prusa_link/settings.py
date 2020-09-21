import logging
import os
from typing import Type

from pydantic import BaseModel
from yaml import Loader, Dumper
from yaml import load, dump

log = logging.getLogger(__name__)


class Settings:
    """
    Loads a yaml file specified in the constructor parameter,

    To use, make a new instance of the Settings, then access your values
     through settings field, feel free to alias. For example:

    SETTINGS = get_settings()
    FOO = SETTINGS.FOO
    """

    def __init__(self, settings_data_class: Type[BaseModel], path):
        self.path = path

        if not os.path.exists(self.path):
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            open(self.path, 'a').close()

        with open(self.path, "r") as settings_file:
            settings_yaml = settings_file.read()
        settings_dict = load(settings_yaml, Loader=Loader)
        log.debug(f"Loaded settings from {os.path.abspath(self.path)}")
        if settings_dict is None:
            log.debug(f"No previous settings found")
            self.settings = settings_data_class()
        else:
            log.debug(f"Read dict {settings_dict}")
            self.settings = settings_data_class(**settings_dict)
            log.debug(f"Settings dict {self.settings.dict()}")
        self.save()

    def save(self):
        settings_dict = self.settings.dict()
        settings_yaml = dump(settings_dict, Dumper=Dumper)

        with open(self.path, 'w') as settings_file:
            settings_file.write(settings_yaml)
