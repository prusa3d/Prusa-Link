import logging
import re
from typing import List, Callable

from getmac import get_mac_address
from requests import RequestException

from old_buddy.modules.connect_api import ConnectAPI, States, PrinterInfo, \
    EmitEvents, Event
from old_buddy.modules.ip_updater import IPUpdater, NO_IP
from old_buddy.modules.serial import Serial, WriteIgnored
from old_buddy.modules.state_manager import StateManager
from old_buddy.settings import INFO_SENDER_LOG_LEVEL, PRINTER_INFO_TIMEOUT
from old_buddy.util import get_command_id

log = logging.getLogger(__name__)
log.setLevel(INFO_SENDER_LOG_LEVEL)

PRINTER_TYPE_REGEX = re.compile(r"^(-?\d{3})$")
FW_REGEX = re.compile(r"^FIRMWARE_NAME:Prusa-Firmware ?((\d+\.)*\d).*$")

PRINTER_TYPES = {
    300: (1, 3),
    302: (1, 3),
    200: (1, 2),
}


class InfoSender:
    def __init__(self, serial: Serial, state_manager: StateManager,
                 connect_api: ConnectAPI, ip_updater: IPUpdater):
        # , sd_card: SDCard):
        self.info_inserters: List[Callable[[PrinterInfo], PrinterInfo]]
        self.info_inserters = [self.insert_type_and_version,
                               self.insert_firmware_version,
                               self.insert_additional_info]

        # self.sd_card = sd_card
        self.ip_updater = ip_updater
        self.connect_api = connect_api
        self.state_manager = state_manager
        self.serial = serial

    def respond_with_info(self, api_response):
        command_id = get_command_id(api_response)

        if self.state_manager.base_state == States.BUSY:
            log.debug(
                "The printer is busy at the moment, ignoring info request")
            self.connect_api.emit_event(EmitEvents.REJECTED, command_id,
                                        reason="The printer is busy")
            return

        event = "INFO"

        printer_info = self.get_printer_info()

        event_object = Event()
        event_object.event = event
        event_object.command_id = command_id
        event_object.values = printer_info

        try:
            self.connect_api.send_dictable("/p/events", event_object)
            self.connect_api.emit_event(EmitEvents.FINISHED, command_id)
        except RequestException:
            pass

    def get_printer_info(self):
        printer_info: PrinterInfo = PrinterInfo()
        for inserter in self.info_inserters:
            # Give this a type because pycharm cannot deduce for some reason
            inserter: Callable[[PrinterInfo], PrinterInfo]
            if self.state_manager.base_state == States.BUSY:
                # Do not disturb, when the printer is busy
                log.debug("Printer seems busy, not asking for telemetry")
                break

            try:
                printer_info = inserter(printer_info)
            except TimeoutError:
                log.debug(
                    f"Function {inserter.__name__} timed out "
                    f"waiting for serial.")
            except WriteIgnored:
                log.debug(
                    f"Function {inserter.__name__} got ignored, "
                    f"serial rejected writing to it. "
                    f"Something else must be requiring serial exclusivity")
        return printer_info

    def insert_type_and_version(self, printer_info: PrinterInfo) -> PrinterInfo:
        match = self.serial.write_and_wait("M862.2 Q", PRINTER_TYPE_REGEX,
                                           timeout=PRINTER_INFO_TIMEOUT)
        if match is not None:
            code = int(match.groups()[0])
            try:
                printer_info.type, printer_info.version = PRINTER_TYPES[code]
            except KeyError:
                log.exception("The printer version has not been found"
                              "in the list of printers")
        return printer_info

    def insert_firmware_version(self, printer_info: PrinterInfo) -> PrinterInfo:
        match = self.serial.write_and_wait("M115", FW_REGEX,
                                           timeout=PRINTER_INFO_TIMEOUT)
        if match is not None:
            printer_info.firmware = match.groups()[0]
        return printer_info

    def insert_additional_info(self, printer_info: PrinterInfo) -> PrinterInfo:
        self.ip_updater.update_local_ip()
        if self.ip_updater.local_ip != NO_IP:
            printer_info.ip = self.ip_updater.local_ip

        printer_info.state = self.state_manager.get_state().name
        printer_info.sn = "4206942069"  # TODO: implement real getters
        printer_info.uuid = "00000000-0000-0000-0000-000000000000"
        printer_info.appendix = False
        printer_info.mac = get_mac_address()
        # printer_info.files = self.sd_card.get_api_file_tree()

        return printer_info
