import configparser
import logging
import re
from json import JSONDecodeError
from threading import Thread
from typing import Union
from getmac import get_mac_address

from requests import RequestException

from prusa_connect_mk3.connect_communication import ConnectCommunication, Telemetry, Event, PrinterInfo
from prusa_connect_mk3.printer_communication import PrinterCommunication
from prusa_connect_mk3.util import run_slowly_die_fast

CONNECT_CONFIG_PATH = "/boot/lan_settings.ini"
PRINTER_PORT = "/dev/ttyAMA0"
PRINTER_BAUDRATE = 115200

PRINTER_RESPONSE_TIMEOUT = 1

TELEMETRY_INTERVAL = 1
QUIT_INTERVAL = 0.5

TEMPERATURE_REGEX = re.compile(r"^ok ?T: ?(\d+\.\d+) ?/(\d+\.\d+) ?B: ?(\d+\.\d+) ?/(\d+\.\d+) ?"
                               r"T0: ?(\d+\.\d+) ?/(\d+\.\d+) ?@: ?(\d+) ?B@: ?(\d+) ?P: ?(\d+\.\d+) ?A: ?(\d+\.\d+)$")

POSITION_REGEX = re.compile(r"^X: ?(\d+\.\d+) ?Y: ?(\d+\.\d+) ?Z: ?(\d+\.\d+) ?E: ?(\d+\.\d+) ?"
                            r"Count ?X: ?(\d+\.\d+) ?Y: ?(\d+\.\d+) ?Z: ?(\d+\.\d+) ?E: ?(\d+\.\d+)$")

INT_REGEX = re.compile(r"^(\d+)$")

FW_REGEX = re.compile(r"^FIRMWARE_NAME:Prusa-Firmware ?((\d+\.)*\d).*$")

PRINTER_TYPES = {
     300: (1, 3),
     200: (1, 2),
}

log = logging.getLogger(__name__)


class PrusaConnectMK3:

    def __init__(self):

        self.config = configparser.ConfigParser()
        self.config.read(CONNECT_CONFIG_PATH)

        connect_config = self.config["connect"]
        address = connect_config["address"]
        port = connect_config["port"]
        token = connect_config["token"]

        self.connect_communication = ConnectCommunication(address=address, port=port, token=token)

        self.printer_communication = PrinterCommunication(port=PRINTER_PORT, baudrate=PRINTER_BAUDRATE)

        self.running = True
        self.telemetry_trhead = Thread(target=self._keep_updating_telemetry, name="telemetry_thread")
        self.telemetry_trhead.start()

    def _keep_updating_telemetry(self):
        run_slowly_die_fast(lambda: self.running, QUIT_INTERVAL, TELEMETRY_INTERVAL, self.update_telemetry)

    def stop(self):
        self.running = False
        self.printer_communication.stop()
        self.telemetry_trhead.join()

    def update_telemetry(self):
        self.send_telemetry(self.get_telemetry())

# --- API calls ---

    def send_telemetry(self, telemetry: Telemetry):
        try:
            # Report printer telemetry
            api_response = self.connect_communication.send_telemetry(telemetry)
            self.handle_telemetry_response(api_response)
        except RequestException:
            log.exception("Exception when calling sending telemetry")

    def send_event(self, event: Event):
        try:
            # Report printer telemetry
            api_response = self.connect_communication.send_event(event)
            self.handle_event_response(api_response)
        except RequestException:
            log.exception("Exception while sending an event")

# --- API response handlers ---

    def handle_telemetry_response(self, api_response):
        if api_response.status_code != 204:
            try:
                data = api_response.json()
                if data["command"] == "SEND_INFO":
                    self.respond_with_info(api_response)
            except JSONDecodeError:
                log.exception(f"Failed to decode a response {api_response}")

    def handle_event_response(self, api_response):
        ...

    def respond_with_info(self, api_response):

        event = "INFO"
        command_id = int(api_response.headers["Command-Id"])

        mac_address = get_mac_address()
        printer_type, printer_version = self.get_type_and_version()
        firmware_version = self.get_firmware_version()
        printer_status = "UNKNOWN"
        serial_number = "4206942069"

        printer_info = PrinterInfo()
        printer_info.printer_type = printer_type
        printer_info.printer_version = printer_version
        printer_info.state = printer_status
        printer_info.sn = serial_number
        printer_info.firmware = firmware_version
        printer_info.mac = mac_address

        event_object = Event()
        event_object.event = event
        event_object.command_id = command_id
        event_object.data = printer_info

        self.send_event(event_object)

# --- printer info getters ---

    def get_telemetry(self):
        telemetry = Telemetry()
        telemetry = self.get_temperatures(telemetry)
        telemetry = self.get_positions(telemetry)

        return telemetry

    def get_temperatures(self, telemetry):
        try:
            match = self.printer_communication.write("M105", TEMPERATURE_REGEX, PRINTER_RESPONSE_TIMEOUT)
        except TimeoutError:
            log.exception("Printer failed to report temperatures in time")
        else:
            groups = match.groups()
            telemetry.temp_nozzle = float(groups[0])
            telemetry.target_nozzle = float(groups[1])
            telemetry.temp_bed = float(groups[2])
            telemetry.target_bed = float(groups[3])
            return telemetry

    def get_positions(self, telemetry):
        try:
            match = self.printer_communication.write("M114", POSITION_REGEX, PRINTER_RESPONSE_TIMEOUT)
        except TimeoutError:
            log.exception("Printer failed to report positions in time")
        else:
            groups = match.groups()
            telemetry.x_axis = float(groups[4])
            telemetry.y_axis = float(groups[5])
            telemetry.z_axis = float(groups[6])
            return telemetry

    def get_type_and_version(self):
        match = self.printer_communication.write("M862.2 Q", wait_for_regex=INT_REGEX, timeout=PRINTER_RESPONSE_TIMEOUT)
        code = int(match.groups()[0])
        return PRINTER_TYPES[code]

    def get_firmware_version(self):
        match = self.printer_communication.write("M115", wait_for_regex=FW_REGEX, timeout=PRINTER_RESPONSE_TIMEOUT)
        fw_version = match.groups()[0]
        return fw_version



