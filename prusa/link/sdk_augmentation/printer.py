"""Contains implementation of the augmented Printer class from the SDK"""

from logging import getLogger
from pathlib import Path
from time import sleep
from typing import Any, Dict, Optional

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
from .file_tree import file_to_dict_legacy

log = getLogger("connect-printer")


class MyPrinter(SDKPrinter, metaclass=MCSingleton):
    """
    Overrides some methods of the SDK Printer to provide better support for
    PrusaLink
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.lcd_printer = LCDPrinter.get_instance()
        self.keepalive = Keepalive.get_instance()
        self.download_thread = Thread(target=self.download_loop,
                                      name="download")
        self.model = Model.get_instance()
        self.nozzle_diameter = None
        self.command_handler = CommandHandler(self.command)
        self.loop_thread = Thread(target=self.loop, name="loop")
        self.__inotify_running = False
        self.inotify_thread = Thread(target=self.inotify_loop, name="inotify")
        self.snapshot_thread = Thread(target=self.snapshot_loop,
                                      name="snapshot_sender",
                                      daemon=True)

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
        info["prusa_link"] = __version__  # TODO: remove later
        info["prusalink"] = __version__
        return info

    def connection_from_settings(self, settings):
        """Loads connection details from the Settings class."""
        self.api_key = settings.service_local.api_key
        server = SDKPrinter.connect_url(settings.service_connect.hostname,
                                        settings.service_connect.tls,
                                        settings.service_connect.port)
        token = settings.service_connect.token

        self.set_connection(server, token)
        use_connect = settings.use_connect()
        self.keepalive.set_use_connect(use_connect)
        use_connect_errors(use_connect)

    def get_file_info(self, caller: Command) -> Dict[str, Any]:
        """Return file info for a given file or folder

        A path which does not exist is rejected with the exact reason
        (raised as CommandRejected by the SDK filesystem)"""
        # pylint: disable=unused-argument
        if not caller.kwargs:
            raise ValueError("SEND_FILE_INFO requires kwargs")

        file_path_string = caller.kwargs['path']
        path: Path = Path(file_path_string)
        log.info("FILE_INFO for: %s", path)
        parts = path.parts

        if file_is_on_sd(parts):
            data = self.sd_file_info(path)
        else:
            data = super().get_file_info(caller)
        log.info("FILE_INFO: %s", data)
        return data

    def sd_file_info(self, path: Path) -> Dict[str, Any]:
        """Return FILE_INFO of a file or a folder on the SD card

        The SD card has no path on the disk, so the SDK answers from the tree
        of the card. A folder is described by its direct children, a file
        gets the metadata parsed from its name."""
        string_path = str(path)
        info = self.fs.file_info(string_path)
        if "children" in info:
            return {
                "source": Source.CONNECT,
                "event": const.Event.FILE_INFO,
                **info,
            }
        return self.from_path(path, info)

    def replace_sd_tree(self, name: str, tree: File) -> None:
        """Make the SDK use a new tree of the SD card

        The SDK answers SEND_FILE_INFO of the SD card from the tree it was
        given when the storage was attached. Connect has no tree of its own
        anymore, so after the swap it is told to forget the card and ask
        again. The event has to follow the swap, otherwise Connect would get
        the old data."""
        storage = self.fs.storage_dict.get(name)
        if storage is None or storage.tree is tree:
            return
        unchanged = (file_to_dict_legacy(storage.tree)
                     == file_to_dict_legacy(tree))
        storage.tree = tree
        if unchanged:  # a repeated listing, Connect knows it all already
            return
        self.fs.connect_event(const.Event.FILE_CHANGED, {
            "old_path": None,
            "new_path": f"{self.fs.sep}{name}",
            "rescan": True,
        })

    @staticmethod
    def from_path(path: Path, info: Optional[Dict[str, Any]] = None):
        """Parses SD file metadata from its name only

        :param info: Data of the file from the SDK filesystem"""
        string_path = str(path)

        meta = FDMMetaData(string_path)
        meta.load_from_path(string_path)
        log.info(meta.data)

        data = {
            "source": Source.CONNECT,
            "event": const.Event.FILE_INFO,
            "path": string_path,
        }
        if info:
            data.update(info)
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
        self.download_thread.start()
        self.snapshot_thread.start()

    def indicate_stop(self):
        """Passes the stop request to all SDK related threads.

        * command handler
        * loop
        * inotify
        """
        self.__inotify_running = False
        self.download_mgr.stop_loop()
        self.stop_loop()
        self.queue.put(None)  # Trick the SDK into quitting fast
        self.command_handler.stop()
        self.camera_controller.stop()

    def wait_stopped(self):
        """Waits for the SDK threads to join

        * command handler
        * loop
        * inotify
        """
        self.inotify_thread.join()
        self.loop_thread.join()
        self.download_thread.join()
        self.snapshot_thread.join()

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

    def snapshot_loop(self):
        """Gives snapshot loop a consistent name with the rest of the app"""
        prctl_name()
        self.camera_controller.snapshot_loop()

    @property
    def type_string(self):
        """Gets the string version of the printer type"""
        if self.type is not None:
            return PRINTER_CONF_TYPES.inverse[self.type]
        return None
