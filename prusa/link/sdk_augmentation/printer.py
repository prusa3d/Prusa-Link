from logging import getLogger
from typing import Dict, Any

from prusa.connect.printer import Printer as SDKPrinter

from prusa.link.printer_adapter.input_output.lcd_printer import LCDPrinter
from prusa.link.printer_adapter.model import Model
from prusa.link.printer_adapter.structures.mc_singleton import MCSingleton
from prusa.link.sdk_augmentation.command_handler import CommandHandler

log = getLogger("connect-printer")


# TODO: rename, it is using the same name just because double underscores
#  break otherwise
class MyPrinter(SDKPrinter, metaclass=MCSingleton):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.lcd_printer = LCDPrinter.get_instance()
        self.model = Model.get_instance()
        self.nozzle_diameter = None
        self.command_handler = CommandHandler(self.command)

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

    def set_connect(self, settings):
        """Set server and token from Settings class."""
        self.api_key = settings.service_local.api_key
        self.server = SDKPrinter.connect_url(settings.service_connect.hostname,
                                             settings.service_connect.tls,
                                             settings.service_connect.port)
        self.token = settings.service_connect.token
