import logging
from typing import List, Callable

from getmac import get_mac_address
from requests import RequestException

from old_buddy.informers.ip_updater import NO_IP
from old_buddy.input_output.connect_api import ConnectAPI
from old_buddy.model import Model
from old_buddy.structures.model_classes import PrinterInfo, \
    NetworkInfo, EmitEvents, Event, Sources
from old_buddy.input_output.serial_queue.serial_queue import SerialQueue
from old_buddy.input_output.serial_queue.helpers import wait_for_instruction, \
    enqueue_matchable
from old_buddy.settings import INFO_SENDER_LOG_LEVEL
from old_buddy.structures.regular_expressions import FW_REGEX, \
    PRINTER_TYPE_REGEX
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
    def __init__(self, serial_queue: SerialQueue, connect_api: ConnectAPI,
                 model: Model):
        self.info_inserters: List[Callable[[PrinterInfo], PrinterInfo]]
        self.info_inserters = [self.insert_type_and_version,
                               self.insert_firmware_version,
                               self.insert_additional_info]

        self.model = model
        self.connect_api = connect_api
        self.serial_queue = serial_queue

        self.getting_info = False

    def respond_with_info(self, api_response):
        self.getting_info = True
        command_id = get_command_id(api_response)

        try:
            printer_info = self.get_printer_info()
        except InfoError as e:
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
                self.connect_api.send_model("/p/events", event_object)
                self.connect_api.emit_event(EmitEvents.FINISHED, command_id)
            except RequestException:
                pass
        finally:
            self.getting_info = False

    def get_printer_info(self):
        printer_info: PrinterInfo = PrinterInfo()
        for inserter in self.info_inserters:
            # Give this a type because pycharm cannot deduce for some reason
            inserter: Callable[[PrinterInfo], PrinterInfo]

            printer_info = inserter(printer_info)
        return printer_info

    def insert_type_and_version(self,
                                printer_info: PrinterInfo) -> PrinterInfo:
        instruction = enqueue_matchable(self.serial_queue, "M862.2 Q")
        wait_for_instruction(instruction, lambda: self.getting_info)
        match = instruction.match(PRINTER_TYPE_REGEX)

        if not instruction.is_confirmed():
            raise InfoError("Command interrupted")
        elif match is not None:
            code = int(match.groups()[0])
            try:
                printer_info.set_printer_model_info(PRINTER_TYPES[code])
            except KeyError:
                log.exception("The printer version has not been found"
                              "in the list of printers")
                raise InfoError(f"Unsupported printer model '{code}'")

        return printer_info

    def insert_firmware_version(self,
                                printer_info: PrinterInfo) -> PrinterInfo:
        instruction = enqueue_matchable(self.serial_queue, "M115")
        wait_for_instruction(instruction, lambda: self.getting_info)
        match = instruction.match(FW_REGEX)

        if not instruction.is_confirmed():
            raise InfoError("Command interrupted")
        elif match is not None:
            printer_info.firmware = match.groups()[0]

        return printer_info

    def insert_additional_info(self, printer_info: PrinterInfo) -> PrinterInfo:
        printer_info.state = self.model.state.name
        printer_info.sn = "4206942069"  # TODO: implement real getters
        printer_info.uuid = "00000000-0000-0000-0000-000000000000"
        printer_info.appendix = False
        printer_info.files = self.model.file_tree

        return printer_info

    def insert_network_info(self, printer_info: PrinterInfo) -> PrinterInfo:
        network_info = NetworkInfo()

        if self.model.local_ip != NO_IP:
            network_info.wifi_ipv4 = self.model.local_ip

        network_info.wifi_mac = get_mac_address()

        printer_info.network_info = network_info
        return printer_info

    def stop(self):
        self.getting_info = False
