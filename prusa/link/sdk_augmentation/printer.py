from logging import getLogger
from pathlib import Path
from typing import Dict, Any

from prusa.connect.printer.const import Source

from prusa.connect.printer.metadata import FDMMetaData
from prusa.connect.printer.files import File

from prusa.connect.printer import Printer as SDKPrinter, const

from prusa.connect.printer import Command
from prusa.link.printer_adapter.input_output.lcd_printer import LCDPrinter
from prusa.link.printer_adapter.model import Model
from prusa.link.printer_adapter.structures.mc_singleton import MCSingleton
from prusa.link.printer_adapter.util import file_is_on_sd
from prusa.link.sdk_augmentation.command_handler import CommandHandler
from prusa.link import errors

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

        if 500 > res.status_code >= 400:
            errors.API.ok = False
        elif res.status_code == 503:
            errors.HTTP.ok = False

        res = super().parse_command(res)
        errors.API.ok = True   # already done in SDK but lets be double sure
        errors.VALID_SN.ok = True    # XXX really?

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

    def get_file_info(self, caller: Command) -> Dict[str, Any]:
        """Return file info for a given file, if it exists."""
        # pylint: disable=unused-argument
        if not caller.args:
            raise ValueError("SEND_FILE_INFO requires args")

        file_path_string = caller.args[0]
        path: Path = Path(file_path_string)
        log.warning(f"FILE_INFO for: {path}")
        parts = path.parts

        if file_is_on_sd(parts):
            data = self.from_path(path)
        else:
            data = super().get_file_info(caller)
        log.warning(f"FILE_INFO: {data}")
        return data

    def from_path(self, path: Path):
        string_path = str(path)
        file: File = self.fs.get(string_path)

        meta = FDMMetaData(string_path)
        meta.load_from_path(string_path)
        log.warning(meta.data)

        data = dict(
            source=Source.CONNECT,
            event=const.Event.FILE_INFO,
            path=string_path)

        data.update(file.attrs)
        data.update(meta.data)

        return data