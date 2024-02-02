"""Contains implementation of the augmented Printer class from the SDK"""
from logging import getLogger
from pathlib import Path
from threading import Event
from time import sleep
from typing import Any, Dict

from gcode_metadata import FDMMetaData

from prusa.connect.printer import Printer as SDKPrinter
from prusa.connect.printer import const
from prusa.connect.printer.command import Command
from prusa.connect.printer.conditions import API, HTTP, CondState
from prusa.connect.printer.const import Source
from prusa.connect.printer.files import File

from .. import __version__
from ..conditions import use_connect_errors
from ..const import PRINTER_CONF_TYPES
from ..printer_adapter.keepalive import Keepalive
from ..printer_adapter.lcd_printer import LCDPrinter
from ..printer_adapter.model import Model
from ..printer_adapter.structures.mc_singleton import MCSingleton
from ..printer_adapter.updatable import Thread
from ..util import file_is_on_sd, prctl_name
from .command_handler import CommandHandler

log = getLogger("connect-printer")


class Base(SDKPrinter):
    """An SDK Printer class, but only the base that sends snapshots to connect
    This is further forked to a fully fledged Printer, or just a camera
    triggering class"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.command_handler = CommandHandler(self.command)
        self.snapshot_thread = Thread(target=self.snapshot_loop,
                                      name="snapshot_sender",
                                      daemon=True)

    def start(self):
        """Start SDK related threads.

        * loop
        """
        self.snapshot_thread.start()

    def indicate_stop(self):
        """Passes the stop request to all SDK related threads.

        * command handler
        * loop
        """
        self.command_handler.stop()
        self.camera_controller.stop()

    def wait_stopped(self):
        """Waits for the SDK threads to join

        * loop
        * snapshot_loop
        """
        self.snapshot_thread.join()

    def connection_from_settings(self, settings):
        """Loads connection details from the Settings class."""
        self.api_key = settings.service_local.api_key
        server = SDKPrinter.connect_url(settings.service_connect.hostname,
                                        settings.service_connect.tls,
                                        settings.service_connect.port)
        token = settings.service_connect.token

        self.set_connection(server, token)
        use_connect_errors(settings.use_connect())

    def snapshot_loop(self):
        """Gives snapshot loop a consistent name with the rest of the app"""
        prctl_name()
        self.camera_controller.snapshot_loop()


class CameraOnly(Base):
    """An SDK Printer class but only for cameras, will probably need renaming
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._quit_evt = Event()
        self.trigger_thread = Thread(target=self.trigger_loop, daemon=True)

    def start(self):
        """Start SDK related threads.
        """
        super().start()
        self.trigger_thread.start()

    def trigger_loop(self):
        """Gives trigger loop a consistent name with the rest of the app"""
        while not self._quit_evt.is_set():
            self.camera_controller.tick()
            self._quit_evt.wait(0.2)

    def wait_stopped(self):
        """Waits for the SDK threads to join
        Waits for the additional trigger thread
        """
        super().wait_stopped()
        self.trigger_thread.join()


class MyPrinter(Base, metaclass=MCSingleton):
    """
    Overrides some methods of the SDK Printer to provide better support for
    PrusaLink
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.lcd_printer = LCDPrinter.get_instance()
        self.keepalive = Keepalive.get_instance()
        self.loop_thread = Thread(target=self.loop, name="loop")
        self.download_thread = Thread(target=self.download_loop,
                                      name="download")
        self.model = Model.get_instance()
        self.nozzle_diameter = None
        self.__inotify_running = False
        self.inotify_thread = Thread(target=self.inotify_loop, name="inotify")

    def parse_command(self, res):
        """Parse telemetry response.

        When response from connect is command (HTTP Status: 200 OK), it
        will set command object.
        """

        if 500 > res.status_code >= 400:
            API.state = CondState.NOK
        elif res.status_code == 503:
            HTTP.state = CondState.NOK

        res = super().parse_command(res)

        return res

    def get_info(self) -> Dict[str, Any]:
        """Returns a dictionary containing the printers info."""
        info = super().get_info()
        info["nozzle_diameter"] = self.nozzle_diameter
        info["files"] = self.fs.to_dict_legacy()
        info["prusa_link"] = __version__  # TODO: remove later
        info["prusalink"] = __version__
        return info

    def get_file_info(self, caller: Command) -> Dict[str, Any]:
        """Return file info for a given file
        sometimes only when it exists"""
        # pylint: disable=unused-argument
        if not caller.kwargs:
            raise ValueError("SEND_FILE_INFO requires kwargs")

        file_path_string = caller.kwargs['path']
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

        meta = FDMMetaData(string_path)
        meta.load_from_path(string_path)
        log.info(meta.data)

        data = {
            "source": Source.CONNECT,
            "event": const.Event.FILE_INFO,
            "path": string_path,
        }

        file: File = self.fs.get(string_path)
        if file is not None:
            data.update(file.attrs)
        data.update(meta.data)
        return data

    def start(self):
        """Start SDK related threads.

        * loop
        * inotify
        """
        self.__inotify_running = True
        super().start()
        self.loop_thread.start()
        self.inotify_thread.start()
        self.download_thread.start()

    def indicate_stop(self):
        """Passes the stop request to all SDK related threads.

        * command handler
        * loop
        * inotify
        * snapshot_loop
        """
        self.__inotify_running = False
        super().indicate_stop()
        self.stop_loop()
        self.queue.put(None)  # Trick the SDK into quitting fast
        self.download_mgr.stop_loop()

    def wait_stopped(self):
        """Waits for the SDK threads to join

        * command handler
        * loop
        * inotify
        * snapshot_loop
        """
        super().wait_stopped()
        self.loop_thread.join()
        self.inotify_thread.join()
        self.download_thread.join()

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
                log.exception('Unhandled exception')

    def download_loop(self):
        """Handler for download loop"""
        prctl_name()
        self.download_mgr.loop()

    @property
    def type_string(self):
        """Gets the string version of the printer type"""
        if self.type is not None:
            return PRINTER_CONF_TYPES.inverse[self.type]
        return None
