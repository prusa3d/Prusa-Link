import configparser
import logging
import threading
from distutils.util import strtobool
from json import JSONDecodeError
from time import time

from requests import RequestException
from serial import SerialException

from old_buddy.modules.commands import Commands
from old_buddy.modules.connect_api import ConnectAPI
from old_buddy.modules.connect_api import Telemetry, PrinterInfo, EmitEvents, \
    Sources
from old_buddy.modules.info_sender import InfoSender
from old_buddy.modules.ip_updater import IPUpdater, NO_IP
from old_buddy.modules.lcd_printer import LCDPrinter
from old_buddy.modules.serial import Serial
# from old_buddy.modules.sd_card import SDCard
from old_buddy.modules.state_manager import StateManager, States, StateChange
from old_buddy.modules.telemetry_gatherer import TelemetryGatherer
from old_buddy.settings import CONNECT_CONFIG_PATH, PRINTER_PORT, \
    PRINTER_BAUDRATE, PRINTER_RESPONSE_TIMEOUT
from old_buddy.settings import OLD_BUDDY_LOG_LEVEL
from old_buddy.util import get_command_id

log = logging.getLogger(__name__)
log.setLevel(OLD_BUDDY_LOG_LEVEL)


class OldBuddy:

    def __init__(self):
        self.stopped_event = threading.Event()

        try:
            self.serial = Serial(port=PRINTER_PORT, baudrate=PRINTER_BAUDRATE,
                                 default_timeout=PRINTER_RESPONSE_TIMEOUT)
        except SerialException:
            log.exception(
                "Cannot talk to the printer using the RPi port, is it enabled? "
                "Is the Pi configured correctly?")
            raise

        Serial.serial_timed_out.connect(self.serial_timed_out)

        self.config = configparser.ConfigParser()
        self.config.read(CONNECT_CONFIG_PATH)

        try:
            connect_config = self.config["connect"]
            address = connect_config["address"]
            port = connect_config["port"]
            token = connect_config["token"]
            try:
                tls = strtobool(connect_config["tls"])
            except KeyError:
                tls = False
        except KeyError:
            self.serial.write("M117 Bad Old Buddy config")
            log.exception(
                "Config load failed, lan_settings.ini missing or invalid.")
            raise

        self.connect_api = ConnectAPI(address=address, port=port, token=token,
                                      tls=tls)
        ConnectAPI.connection_error.connect(self.connection_error)

        self.state_manager = StateManager(self.serial)
        StateManager.state_changed.connect(self.state_changed)

        self.telemetry_gatherer = TelemetryGatherer(self.serial,
                                                    self.state_manager)
        TelemetryGatherer.send_telemetry_signal.connect(self.send_telemetry)

        self.commands = Commands(self.serial, self.connect_api,
                                 self.state_manager)
        self.lcd_printer = LCDPrinter(self.serial, self.state_manager)
        # self.sd_card = SDCard(self.serial, self.state_manager)

        self.local_ip = ""
        self.last_showed_ip = time()
        self.additional_telemetry = Telemetry()
        self.printer_info = PrinterInfo()

        # Greet the user
        self.lcd_printer.enqueue_message(f"Old Buddy says: Hi")
        self.lcd_printer.enqueue_message(f"RPi is operational")
        self.lcd_printer.enqueue_message(f"Its IP address is:")

        # Start the ip updater after we enqueued the correct IP report message
        self.ip_updater = IPUpdater(self.lcd_printer)

        self.info_sender = InfoSender(self.serial, self.state_manager,
                                      self.connect_api, self.ip_updater)
        # , self.sd_card)

    def stop(self):
        # self.sd_card.stop()
        self.lcd_printer.stop()
        self.commands.stop_command_thread()
        self.state_manager.stop()
        self.telemetry_gatherer.stop()
        self.ip_updater.stop()
        self.serial.stop()
        self.connect_api.stop()

        log.debug("Remaining threads, that could prevent us from quitting:")
        for thread in threading.enumerate():
            log.debug(thread)
        self.stopped_event.set()

    # --- API response handlers ---

    def handle_telemetry_response(self, api_response):
        if api_response.status_code == 200:
            log.debug(f"Command id -> {get_command_id(api_response)}")
            if api_response.headers["Content-Type"] == "text/x.gcode":
                self.state_manager.expect_change(
                    StateChange(api_response, default_source=Sources.CONNECT))
                self.commands.execute_gcode(api_response)
                # If the gcode execution did not cause a state change
                # stop expecting it
                self.state_manager.stop_expecting_change()
            else:
                try:
                    data = api_response.json()
                    if data["command"] == "SEND_INFO":
                        self.info_sender.respond_with_info(api_response)
                    if data["command"] == "START_PRINT":
                        self.commands.start_print(api_response)
                    if data["command"] == "STOP_PRINT":
                        self.commands.stop_print(api_response)
                    if data["command"] == "PAUSE_PRINT":
                        self.commands.pause_print(api_response)
                    if data["command"] == "RESUME_PRINT":
                        self.commands.resume_print(api_response)
                except JSONDecodeError:
                    log.exception(f"Failed to decode a response {api_response}")
        elif api_response.status_code >= 300:
            code = api_response.status_code
            log.error(f"Connect responded with code {code}")

            if code == 403:
                self.lcd_printer.enqueue_message("400 Bad Request")
                self.lcd_printer.enqueue_message("400 May be a bug")
                self.lcd_printer.enqueue_message("400 But most likely")
                self.lcd_printer.enqueue_message("400 Outdated client")
            if code == 403:
                self.lcd_printer.enqueue_message("403 Forbidden")
                self.lcd_printer.enqueue_message("403 Expired token")
                self.lcd_printer.enqueue_message("403 Or invalid one")
                self.lcd_printer.enqueue_message("403 Bad lan_settings")
            if code == 401:
                self.lcd_printer.enqueue_message("401 Unauthorized")
                self.lcd_printer.enqueue_message("401 Missing token")
                self.lcd_printer.enqueue_message("401 Or invalid one")
                self.lcd_printer.enqueue_message("401 Bad lan_settings")
            if code == 501:
                self.lcd_printer.enqueue_message("501 Service Unavail")
                self.lcd_printer.enqueue_message("501 You cold try")
                self.lcd_printer.enqueue_message("501 re-downloading")
                self.lcd_printer.enqueue_message("501 lan_settings")
                self.lcd_printer.enqueue_message("501 But most likely")
                self.lcd_printer.enqueue_message("501 Connect is down")

    # --- Signal handlers ---

    def state_changed(self, sender, command_id=None, source=None):
        state = self.state_manager.current_state
        # Some state changes can imply telemetry data.
        # For example, if we were not printing and now we are,
        # we have been printing for 0 min and we have 0% done
        if (state == States.PRINTING and
                self.state_manager.last_state in {States.READY, States.BUSY}):
            self.additional_telemetry.progress = 0
            self.additional_telemetry.printing_time = 0

        self.connect_api.emit_event(EmitEvents.STATE_CHANGED, state=state.name,
                                    command_id=command_id, source=source)

    def connection_error(self, sender, path, json_dict):
        log.debug(f"Connection failed while sending data to the api point "
                  f"{path}. Data: {json_dict}")
        self.lcd_printer.enqueue_message("Failed when talking")
        self.lcd_printer.enqueue_message("to the Connect API.")
        if self.local_ip == NO_IP:
            self.lcd_printer.enqueue_message("Could be")
            self.lcd_printer.enqueue_message("bad WiFi settings")
            self.lcd_printer.enqueue_message("because there's")
            self.lcd_printer.enqueue_message("No WiFi connection")
        else:
            self.lcd_printer.enqueue_message("Maybe no Internet")
            self.lcd_printer.enqueue_message("or it's our fault")
            self.lcd_printer.enqueue_message("Connect seems down")

    def serial_timed_out(self, sender):
        self.state_manager.busy()

    def send_telemetry(self, sender, telemetry: Telemetry):
        try:
            api_response = self.connect_api.send_dictable("/p/telemetry",
                                                          telemetry)
        except RequestException:
            pass
        else:
            self.handle_telemetry_response(api_response)
