import logging

from getmac import get_mac_address
from requests import RequestException

from prusa_link.command import Command, ResponseCommand
from prusa_link.default_settings import get_settings
from prusa_link.informers.ip_updater import NO_IP
from prusa_link.input_output.connect_api import ConnectAPI
from prusa_link.input_output.serial.serial_queue import SerialQueue
from prusa_link.model import Model
from prusa_link.structures.model_classes import PrinterInfo, \
    NetworkInfo, EmitEvents, Event
from prusa_link.structures.regular_expressions import FW_REGEX, \
    PRINTER_TYPE_REGEX, NOZZLE_REGEX

LOG = get_settings().LOG


log = logging.getLogger(__name__)
log.setLevel(LOG.COMMANDS)

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


class SendInfo(Command):

    def __init__(self, serial_queue: SerialQueue, connect_api: ConnectAPI,
                 model: Model, **kwargs):
        super().__init__(serial_queue)
        self.model = model
        self.connect_api = connect_api
        self.printer_info = PrinterInfo()

    def _run_command(self):
        self.insert_type_and_version()
        self.insert_firmware_version()
        self.insert_nozzle_diameter()
        self.insert_network_info()
        self.insert_additional_info()

        event_object = self.create_event()

        try:
            self.connect_api.send_model("/p/events", event_object)
        except RequestException:
            log.exception("Sending info failed!")

    def create_event(self):
        event_object = Event()
        event_object.event = EmitEvents.INFO.value
        event_object.values = self.printer_info
        return event_object

    def insert_type_and_version(self):
        instruction = self.do_matchable("M862.2 Q", PRINTER_TYPE_REGEX)

        match = instruction.match()
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
        instruction = self.do_matchable("M115", FW_REGEX)

        match = instruction.match()
        if match is None:
            self.failed("Printer responded with something unexpected")

        self.printer_info.firmware = match.groups()[0]

    def insert_nozzle_diameter(self):
        instruction = self.do_matchable("M862.1 Q", NOZZLE_REGEX)

        match = instruction.match()
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


class SendInfoResponse(ResponseCommand, SendInfo):
    # This is a diamond, hopefully no overridden methods will conflict here
    # The instantiation should be
    # SendInfoResponse -> ResponseCommand -> SendInfo -> Command
    # Yes, ResponseCommand has to pass more keyword arguments and SendInfo
    # picks the relevant ones, but it works, hurray!
    def __init__(self, **kwargs):
        super().__init__(**kwargs)

    def create_event(self):
        event_object = super().create_event()
        event_object.command_id = self.command_id
        return event_object
