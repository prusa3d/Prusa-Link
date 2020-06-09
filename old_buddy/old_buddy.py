import configparser
import logging
import re
import socket
import threading
from json import JSONDecodeError
from threading import Thread, Timer

from getmac import get_mac_address

from requests import RequestException

from old_buddy.connect_communication import ConnectCommunication, Telemetry, Event, PrinterInfo, EmitEvents
from old_buddy.printer_communication import PrinterCommunication, UnknownCommandException, OK_PATTERN
from old_buddy.telemetry_gatherer import TelemetryGatherer
from old_buddy.util import run_slowly_die_fast, get_command_id

CONNECT_CONFIG_PATH = "/boot/lan_settings.ini"
PRINTER_PORT = "/dev/ttyAMA0"
PRINTER_BAUDRATE = 115200

PRINTER_RESPONSE_TIMEOUT = 1
LONG_GCODE_TIMEOUT = 120

TELEMETRY_INTERVAL = 1
QUIT_INTERVAL = 0.5

TEMPERATURE_REGEX = re.compile(r"^ok ?T: ?(\d+\.\d+) ?/(\d+\.\d+) ?B: ?(\d+\.\d+) ?/(\d+\.\d+) ?"
                               r"T0: ?(\d+\.\d+) ?/(\d+\.\d+) ?@: ?(\d+) ?B@: ?(\d+) ?P: ?(\d+\.\d+) ?A: ?(\d+\.\d+)$")

POSITION_REGEX = re.compile(r"^X: ?(\d+\.\d+) ?Y: ?(\d+\.\d+) ?Z: ?(\d+\.\d+) ?E: ?(\d+\.\d+) ?"
                            r"Count ?X: ?(\d+\.\d+) ?Y: ?(\d+\.\d+) ?Z: ?(\d+\.\d+) ?E: ?(\d+\.\d+)$")

INT_REGEX = re.compile(r"^(\d+)$")

FW_REGEX = re.compile(r"^FIRMWARE_NAME:Prusa-Firmware ?((\d+\.)*\d).*$")

E_FAN_REGEX = re.compile(r"^E0:(\d+) ?RPM$")
P_FAN_REGEX = re.compile(r"^PRN0:(\d+) ?RPM$")

PRINT_TIME_REGEX = re.compile(r"^(Not SD printing)|((\d+):(\d{2}))$")
PROGRESS_REGEX = re.compile(r"^NORMAL MODE: Percent done: (\d+);.*")

PRINTER_TYPES = {
     300: (1, 3),
     200: (1, 2),
}

log = logging.getLogger(__name__)


class OldBuddy:

    def __init__(self):

        self.config = configparser.ConfigParser()
        self.config.read(CONNECT_CONFIG_PATH)

        connect_config = self.config["connect"]
        address = connect_config["address"]
        port = connect_config["port"]
        token = connect_config["token"]

        self.connect_communication = ConnectCommunication(address=address, port=port, token=token)

        self.printer_communication = PrinterCommunication(port=PRINTER_PORT, baudrate=PRINTER_BAUDRATE,
                                                          default_response_timeout=PRINTER_RESPONSE_TIMEOUT)

        self.state_gatherer = TelemetryGatherer(printer_communication=self.printer_communication)

        self.running = True
        self.telemetry_thread = Thread(target=self._keep_updating_telemetry, name="telemetry_thread")
        self.telemetry_thread.start()

    def _keep_updating_telemetry(self):
        run_slowly_die_fast(lambda: self.running, QUIT_INTERVAL, TELEMETRY_INTERVAL, self.update_telemetry)

    def stop(self):
        self.running = False
        self.printer_communication.stop()
        log.debug("printer_communication stopped")
        self.connect_communication.stop()
        self.telemetry_thread.join()
        log.debug("old_buddy should be stopped")

        log.debug("Remaining threads, that could prevent us from quitting:")
        for thread in threading.enumerate():
            log.debug(thread)

    def update_telemetry(self):
        self.send_telemetry(self.state_gatherer.gather_telemetry())

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
            api_response = self.connect_communication.send_event(event)
            self.handle_event_response(api_response)
        except RequestException:
            log.exception("Exception while sending an event")

    def emit_event(self, emit_event: EmitEvents, command_id: int, reason: str = None):
        event = Event()
        event.command_id = command_id
        event.event = emit_event.value
        if reason is not None:
            event.reason = reason
        self.send_event(event)

# --- API response handlers ---

    def handle_telemetry_response(self, api_response):
        if api_response.status_code != 204:
            if api_response.headers["Content-Type"] == "text/x.gcode":
                self.execute_gcode(api_response)
            else:
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
        command_id = get_command_id(api_response)

        printer_info = PrinterInfo()
        printer_info = self.get_type_and_version(printer_info)
        printer_info = self.get_firmware_version(printer_info)
        printer_info = self.get_local_ip(printer_info)
        printer_info.state = "READY"
        printer_info.sn = "4206942069"
        printer_info.uuid = "00000000-0000-0000-0000-000000000000"
        printer_info.appendix = False
        printer_info.mac = get_mac_address()

        event_object = Event()
        event_object.event = event
        event_object.command_id = command_id
        event_object.values = printer_info

        self.send_event(event_object)
        self.emit_event(EmitEvents.FINISHED, command_id)

    def execute_gcode(self, api_response):
        """
        Send a gcode to a printer, on Unknown command send REJECT
        if the printer answers OK in a timely manner, send FINISHED right away
        if not, send ACCEPTED and wait for the gcode to finish. Send FINISHED after that

        :param api_response: which response are we responding to. (yes, responding to a response)
        """

        command_id = get_command_id(api_response)

        gcode = api_response.text

        try:
            self.printer_communication.write_wait_ok(gcode)
        except UnknownCommandException as e:
            self.emit_event(EmitEvents.REJECTED, command_id, f"Unknown command '{e.command}')")
        except TimeoutError:
            self.emit_event(EmitEvents.ACCEPTED, command_id)
            timeout_timer = Timer(LONG_GCODE_TIMEOUT, lambda: ...)
            timeout_timer.start()

            output_collector = self.printer_communication.get_output_collector(OK_PATTERN, QUIT_INTERVAL)
            try:
                # be ready to quit in a timely manner
                output_collector.wait_until(lambda: self.running and timeout_timer.is_alive())
            except TimeoutError:
                if self.running:
                    log.exception(f"Timed out waiting for printer to return ok after gcode '{gcode}'")
            else:
                self.emit_event(EmitEvents.FINISHED, command_id)
                if timeout_timer.is_alive():
                    timeout_timer.cancel()
        else:
            self.emit_event(EmitEvents.FINISHED, command_id)

    # --- printer info getters ---

    def get_type_and_version(self, printer_info: PrinterInfo):
        try:
            match = self.printer_communication.write("M862.2 Q", wait_for_regex=INT_REGEX,
                                                     timeout=PRINTER_RESPONSE_TIMEOUT)
        except TimeoutError:
            log.exception("Printer failed to report printer type and version in time")
        else:
            code = int(match.groups()[0])
            printer_info.type, printer_info.version = PRINTER_TYPES[code]
        finally:
            return printer_info

    def get_firmware_version(self, printer_info: PrinterInfo):
        try:
            match = self.printer_communication.write("M115", wait_for_regex=FW_REGEX,
                                                     timeout=PRINTER_RESPONSE_TIMEOUT)
        except TimeoutError:
            log.exception("Printer failed to report fw version and version in time")
        else:
            printer_info.firmware = match.groups()[0]
        finally:
            return printer_info

    def get_local_ip(self, printer_info: PrinterInfo):
        """
        Gets the local ip used for connecting to MQTT_HOSTNAME
        Code from https://stackoverflow.com/a/166589
        """
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        # does not matter if host is reachable or not, any client interface that is UP should suffice
        s.connect(("8.8.8.8", 1))
        printer_info.ip = s.getsockname()[0]
        s.close()
        return printer_info
