import os
import configparser
from hashlib import sha256
from logging import getLogger
from queue import Queue
from typing import Dict, Any, Optional

from requests import Session

from prusa.connect.printer import Printer as SDKPrinter
from prusa.connect.printer import const, Filesystem, InotifyHandler, \
    CommandArgs
from prusa.link.printer_adapter.input_output.lcd_printer import LCDPrinter
from prusa.link.printer_adapter.model import Model
from prusa.link.printer_adapter.structures.mc_singleton import MCSingleton
from prusa.link.sdk_augmentation.command import MyCommand

log = getLogger("connect-printer")


# TODO: rename, it is using the same name just because double underscores
#  break otherwise
class MyPrinter(SDKPrinter, metaclass=MCSingleton):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.lcd_printer = LCDPrinter.get_instance()
        self.model = Model.get_instance()
        self.nozzle_diameter = None

    @classmethod
    def from_config_2(cls, path: str, type_: const.PrinterType, sn: str):
        """Load lan_settings.ini config from `path` and create Printer instance
           from it.
        """
        if not os.path.exists(path):
            raise FileNotFoundError(f"ini file: `{path}` doesn't exist")
        config = configparser.ConfigParser()
        config.read(path)
        connect_host = config['connect']['address']
        connect_port = config['connect'].getint('port')
        token = config['connect']['token']
        protocol = "http"
        if config['connect'].getboolean('tls'):
            protocol = "https"
        server = f"{protocol}://{connect_host}:{connect_port}"
        printer = cls(type_, sn, server, token, MyCommand)
        return printer

    def parse_command(self, res):
        """Parse telemetry response.

        When response from connect is command (HTTP Status: 200 OK), it
        will set command object.
        """

        if res.status_code == 400:
            self.lcd_printer.enqueue_400()
        elif res.status_code == 401:
            self.lcd_printer.enqueue_401()
        elif res.status_code == 403:
            self.lcd_printer.enqueue_403()
        elif res.status_code == 503:
            self.lcd_printer.enqueue_503()

        res = super().parse_command(res)

        return res

    def get_info(self) -> Dict[str, Any]:
        info = super().get_info()
        info["nozzle_diameter"] = self.nozzle_diameter
        info["files"] = self.fs.to_dict()
        return info
