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
class Printer(SDKPrinter, metaclass=MCSingleton):

    def __init__(self,
                 lcd_printer: LCDPrinter,
                 model: Model,
                 type_: const.PrinterType,
                 sn: str,
                 server: str,
                 token: str = None,):
        self.type = type_
        self.__sn = sn
        self.__fingerprint = sha256(sn.encode()).hexdigest()
        self.firmware = None
        self.nozzle_diameter = None
        self.network_info = {
            "lan_mac": None,
            "lan_ipv4": None,
            "lan_ipv6": None,
            "wifi_mac": None,
            "wifi_ipv4": None,
            "wifi_ipv6": None,
            "wifi_ssid": None,
        }

        self.__state = const.State.BUSY
        self.job_id = None

        self.server = server
        self.token = token
        self.conn = Session()
        self.queue = Queue()

        self.command = MyCommand(self.event_cb)
        self.set_handler(const.Command.SEND_INFO, self.get_info)
        self.set_handler(const.Command.SEND_FILE_INFO, self.get_file_info)

        self.fs = Filesystem(sep=os.sep, event_cb=self.event_cb)
        self.inotify_handler = InotifyHandler(self.fs)

        self.lcd_printer = lcd_printer
        self.model = model

    @classmethod
    def from_config_2(cls, lcd_printer: LCDPrinter, model: Model,
                      path: str, type_: const.PrinterType, sn: str):
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
        printer = cls(lcd_printer, model, type_, sn, server, token)
        return printer

    def parse_command(self, res):
        """Parse telemetry response.

        When response from connect is command (HTTP Status: 200 OK), it
        will set command object.
        """
        # TODO: disgraceful, workaround as phu
        forced = ("Force" in res.headers and res.headers["Force"] == "1")

        if res.status_code == 200:
            command_id: Optional[int] = None
            try:
                command_id = int(res.headers.get("Command-Id"))
            except (TypeError, ValueError):
                log.error("Invalid Command-Id header: %s",
                          res.headers.get("Command-Id"))
                self.event_cb(const.Event.REJECTED,
                              const.Source.CONNECT,
                              reason="Invalid Command-Id header")
                return res

            content_type = res.headers.get("content-type")
            log.debug("parse_command res: %s", res.text)
            try:
                if content_type == "application/json":
                    data = res.json()
                    if self.command.check_state(command_id):
                        self.command.accept(command_id,
                                            data.get("command", ""),
                                            data.get("args"))
                elif content_type == "text/x.gcode":
                    if self.command.check_state(command_id):
                        self.command.accept(command_id,
                                            const.Command.GCODE.value,
                                            [res.text, forced])
                else:
                    raise ValueError("Invalid command content type")
            except Exception as e:  # pylint: disable=broad-except
                log.exception("")
                self.event_cb(const.Event.REJECTED,
                              const.Source.CONNECT,
                              command_id=command_id,
                              reason=str(e))

        if res.status_code == 400:
            self.lcd_printer.enqueue_400()
        elif res.status_code == 401:
            self.lcd_printer.enqueue_401()
        elif res.status_code == 403:
            self.lcd_printer.enqueue_403()
        elif res.status_code == 503:
            self.lcd_printer.enqueue_503()

        return res

    def get_info(self, args: CommandArgs) -> Dict[str, Any]:
        info = super().get_info(args)
        info["files"] = self.model.file_tree.to_api_file_tree().dict(
            exclude_none=True)
        info["nozzle_diameter"] = self.nozzle_diameter
        return info
