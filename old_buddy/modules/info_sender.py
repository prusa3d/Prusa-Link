import logging
import re
from typing import List, Callable

from getmac import get_mac_address
from requests import RequestException

from old_buddy.modules.connect_api import ConnectAPI, States, PrinterInfo, \
    EmitEvents, Event, Sources
from old_buddy.modules.ip_updater import IPUpdater, NO_IP
from old_buddy.modules.serial import Serial, WriteIgnored
from old_buddy.modules.state_manager import StateManager
from old_buddy.settings import INFO_SENDER_LOG_LEVEL, PRINTER_INFO_TIMEOUT
from old_buddy.util import get_command_id

log = logging.getLogger(__name__)
log.setLevel(INFO_SENDER_LOG_LEVEL)

PRINTER_TYPE_REGEX = re.compile(r"^(\d{3,5})$")
FW_REGEX = re.compile(r"^FIRMWARE_NAME:Prusa-Firmware ?((\d+\.)*\d).*$")

PRINTER_TYPES = {
    100: (1, 1, 0),
    200: (1, 2, 0),
    201: (1, 2, 0),
    202: (1, 2, 1),
    203: (1, 2, 1),
    250: (1, 2, 5),
    20250: (1, 2, 5),
    252: (1, 2, 6),
    20252: (1, 2, 6),
    300: (1, 3, 0),
    20300: (1, 3, 0),
    302: (1, 3, 1),
    20302: (1, 3, 1),
}


class InfoError(Exception):
    ...


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
            log.debug("The printer is busy, ignoring info request")
            self.connect_api.emit_event(EmitEvents.REJECTED, command_id,
                                        reason="The printer is busy",
                                        source=Sources.WUI.name)
            return

        try:
            printer_info = self.get_printer_info()
        except (TimeoutError, WriteIgnored, InfoError) as e:
            log.exception("Error while getting info")
            self.connect_api.emit_event(EmitEvents.REJECTED, command_id,
                                        reason=e.args[0],
                                        source=Sources.WUI.value)
        else:
            event_object = Event()
            event_object.event = EmitEvents.INFO.value
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

            printer_info = inserter(printer_info)
        return printer_info

    def insert_type_and_version(self, printer_info: PrinterInfo) -> PrinterInfo:
        match = self.serial.write_and_wait("M862.2 Q", PRINTER_TYPE_REGEX,
                                           timeout=PRINTER_INFO_TIMEOUT)
        if match is not None:
            code = int(match.groups()[0])
            try:
                printer_info.set_printer_model_info(PRINTER_TYPES[code])
            except KeyError:
                log.exception("The printer version has not been found"
                              "in the list of printers")
                raise InfoError(f"Unsupported printer model '{code}'")
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
