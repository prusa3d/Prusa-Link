"""Contains implementation of the augmented Printer class from the SDK"""
from logging import getLogger
from pathlib import Path
from typing import Dict, Any
from time import sleep

from prusa.connect.printer.const import Source
from prusa.connect.printer.metadata import FDMMetaData
from prusa.connect.printer.files import File
from prusa.connect.printer import Printer as SDKPrinter, const
from prusa.connect.printer import Command

from ..printer_adapter.input_output.lcd_printer import LCDPrinter
from ..printer_adapter.model import Model
from ..printer_adapter.structures.mc_singleton import MCSingleton
from ..printer_adapter.util import file_is_on_sd
from ..printer_adapter.updatable import prctl_name, Thread
from .command_handler import CommandHandler
from .. import errors, __version__

log = getLogger("connect-printer")


class MyPrinter(SDKPrinter, metaclass=MCSingleton):
    """
    Overrides some methods of the SDK Printer to provide better support for
    Prusa Link
    """
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.lcd_printer = LCDPrinter.get_instance()
        self.model = Model.get_instance()
        self.nozzle_diameter = None
        self.command_handler = CommandHandler(self.command)
        self.loop_thread = Thread(target=self.loop, name="loop")
        self.__inotify_running = False
        self.inotify_thread = Thread(target=self.inotify_loop, name="inotify")

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
        errors.API.ok = True  # already done in SDK but lets be double sure

        return res

    def get_info(self) -> Dict[str, Any]:
        """Returns a dictionary containing the printers info."""
        info = super().get_info()
        info["nozzle_diameter"] = self.nozzle_diameter
        info["files"] = self.fs.to_dict()
        info["prusa_link"] = __version__
        return info

    def set_connect(self, settings):
        """Set server and token from Settings class."""
        self.api_key = settings.service_local.api_key
        self.server = SDKPrinter.connect_url(settings.service_connect.hostname,
                                             settings.service_connect.tls,
                                             settings.service_connect.port)
        self.token = settings.service_connect.token
        errors.TOKEN.ok = True

    def get_file_info(self, caller: Command) -> Dict[str, Any]:
        """Return file info for a given file, if it exists."""
        # pylint: disable=unused-argument
        if not caller.args:
            raise ValueError("SEND_FILE_INFO requires args")

        file_path_string = caller.args[0]
        path: Path = Path(file_path_string)
        log.info("FILE_INFO for: %s", path)
        parts = path.parts

        if file_is_on_sd(parts):
            data = self.from_path(path)
        else:
            data = super().get_file_info(caller)
        log.info("FILE_INFO: %s", data)
        return data

    def from_path(self, path: Path):
        """Parses SD file metadata from its name only"""
        string_path = str(path)
        file: File = self.fs.get(string_path)

        meta = FDMMetaData(string_path)
        meta.load_from_path(string_path)
        log.info(meta.data)

        data = dict(source=Source.CONNECT,
                    event=const.Event.FILE_INFO,
                    path=string_path)

        data.update(file.attrs)
        data.update(meta.data)

        return data

    def start(self):
        """Start SDK related threads.

        * loop
        * inotify
        """
        self.__inotify_running = True
        self.loop_thread.start()
        self.inotify_thread.start()

    def stop(self):
        """Passes the stop request to all SDK related threads.

        * command handler
        * loop
        * inotify
        """
        self.__inotify_running = False
        self.stop_loop()
        self.command_handler.stop()
        self.inotify_thread.join()
        self.loop_thread.join()

    def loop(self):
        """SDKPrinter.loop with thread name."""
        prctl_name()
        super().loop()

    def inotify_loop(self):
        """Inotify_handler in loop."""
        prctl_name()
        while self.__inotify_running:
            try:
                self.inotify_handler()
                sleep(0.2)
            except Exception:  # pylint: disable=broad-except
                log.exception('Unhadled exception')
