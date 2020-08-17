import logging
from time import time
from typing import List, Callable

from getmac import get_mac_address
from requests import RequestException

from old_buddy.modules.connect_api import ConnectAPI, PrinterInfo, \
    NetworkInfo, EmitEvents, Event, Sources
from old_buddy.modules.ip_updater import IPUpdater, NO_IP
from old_buddy.modules.regular_expressions import FW_REGEX, PRINTER_TYPE_REGEX
from old_buddy.modules.serial_queue.helpers import enqueue_one_from_str, \
    wait_for_instruction, enqueue_matchable_from_str
from old_buddy.modules.serial_queue.serial_queue import SerialQueue
from old_buddy.modules.state_manager import StateManager
from old_buddy.settings import INFO_SENDER_LOG_LEVEL, PRINTER_INFO_TIMEOUT
from old_buddy.util import get_command_id

log = logging.getLogger(__name__)
log.setLevel(INFO_SENDER_LOG_LEVEL)

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
    def __init__(self, serial_queue: SerialQueue, state_manager: StateManager,
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
        self.serial_queue = serial_queue

    def respond_with_info(self, api_response):
        command_id = get_command_id(api_response)

        try:
            printer_info = self.get_printer_info()
        except (TimeoutError, InfoError) as e:
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

    def insert_type_and_version(self,
                                printer_info: PrinterInfo) -> PrinterInfo:
        instruction = enqueue_matchable_from_str(self.serial_queue, "M862.2 Q")
        timeout_on = time() + PRINTER_INFO_TIMEOUT
        wait_for_instruction(instruction, lambda: time() < timeout_on)

        if instruction.is_confirmed():
            match = instruction.match(PRINTER_TYPE_REGEX)
            if match is not None:
                code = int(match.groups()[0])
                try:
                    printer_info.set_printer_model_info(PRINTER_TYPES[code])
                except KeyError:
                    log.exception("The printer version has not been found"
                                  "in the list of printers")
                    raise InfoError(f"Unsupported printer model '{code}'")
        else:
            raise TimeoutError("Cannot get type and version at the moment")
        return printer_info

    def insert_firmware_version(self,
                                printer_info: PrinterInfo) -> PrinterInfo:
        instruction = enqueue_matchable_from_str(self.serial_queue, "M115")
        timeout_on = time() + PRINTER_INFO_TIMEOUT
        wait_for_instruction(instruction, lambda: time() < timeout_on)

        if instruction.is_confirmed():
            match = instruction.match(FW_REGEX)
            if match is not None:
                printer_info.firmware = match.groups()[0]

        else:
            raise TimeoutError("Cannot get fw version at the moment")
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

    def insert_network_info(self, printer_info: PrinterInfo) -> PrinterInfo:
        network_info = NetworkInfo()

        self.ip_updater.update_local_ip()
        if self.ip_updater.local_ip != NO_IP:
            network_info.wifi_ipv4 = self.ip_updater.local_ip

        network_info.wifi_mac = get_mac_address()

        printer_info.network_info = network_info
        return printer_info
