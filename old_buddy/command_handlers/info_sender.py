import logging

from getmac import get_mac_address
from requests import RequestException

from old_buddy.command import Command
from old_buddy.default_settings import get_settings
from old_buddy.informers.ip_updater import NO_IP
from old_buddy.structures.model_classes import PrinterInfo, \
    NetworkInfo, EmitEvents, Event
from old_buddy.structures.regular_expressions import FW_REGEX, \
    PRINTER_TYPE_REGEX, NOZZLE_REGEX

LOG = get_settings().LOG


log = logging.getLogger(__name__)
log.setLevel(LOG.INFO_SENDER_LOG_LEVEL)

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


class RespondWithInfo(Command):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.printer_info = PrinterInfo()

    def _run_command(self):
        self.insert_type_and_version()
        self.insert_firmware_version()
        self.insert_nozzle_diameter()
        self.insert_network_info()
        self.insert_additional_info()

        event_object = Event()
        event_object.event = EmitEvents.INFO.value
        event_object.command_id = self.command_id
        event_object.values = self.printer_info

        try:
            self.connect_api.send_model("/p/events", event_object)
        except RequestException:
            log.exception("Sending info failed!")

    def insert_type_and_version(self):
        instruction = self.do_matchable("M862.2 Q")

        match = instruction.match(PRINTER_TYPE_REGEX)
        if match is None:
            self.failed("Printer responded with something unexpected")

        code = int(match.groups()[0])

        try:
            self.printer_info.set_printer_model_info(PRINTER_TYPES[code])
        except KeyError:
            log.exception("The printer version has not been found"
                          "in the list of printers")
            self.failed(f"Unsupported printer model '{code}'")

    def insert_firmware_version(self):
        instruction = self.do_matchable("M115")

        match = instruction.match(FW_REGEX)
        if match is None:
            self.failed("Printer responded with something unexpected")

        self.printer_info.firmware = match.groups()[0]

    def insert_nozzle_diameter(self):
        instruction = self.do_matchable("M862.1 Q")

        match = instruction.match(NOZZLE_REGEX)
        if match is None:
            self.failed("Printer responded with something unexpected")

        self.printer_info.nozzle_diameter = float(match.groups()[0])

    def insert_additional_info(self):
        self.printer_info.state = self.model.state.name
        self.printer_info.sn = "4206942069"  # TODO: implement real getters
        self.printer_info.uuid = "00000000-0000-0000-0000-000000000000"
        self.printer_info.appendix = False
        self.printer_info.files = self.model.api_file_tree

    def insert_network_info(self):
        network_info = NetworkInfo()

        if self.model.local_ip != NO_IP:
            network_info.wifi_ipv4 = self.model.local_ip

        network_info.wifi_mac = get_mac_address()

        self.printer_info.network_info = network_info
