import configparser
import logging
import re
import threading
from distutils.util import strtobool
from json import JSONDecodeError
from threading import Thread
from time import sleep, time
from typing import List, Callable, Any

from getmac import get_mac_address
from requests import RequestException
from serial import SerialException

from old_buddy.modules.connect_api import ConnectAPI
from old_buddy.modules.connect_api import Telemetry, PrinterInfo, Dictable, EmitEvents, Event, \
    Sources
from old_buddy.modules.commands import Commands
from old_buddy.modules.inserters import telemetry_inserters, info_inserters
from old_buddy.modules.lcd_printer import LCDPrinter
# from old_buddy.modules.sd_card import SDCard
from old_buddy.modules.state_manager import StateManager, States, PRINTING_STATES, StateChange
from old_buddy.modules.serial import Serial
from old_buddy.settings import CONNECT_CONFIG_PATH, PRINTER_PORT, PRINTER_BAUDRATE, PRINTER_RESPONSE_TIMEOUT, \
    SHOW_IP_INTERVAL
from old_buddy.settings import QUIT_INTERVAL, STATUS_UPDATE_INTERVAL_SEC, TELEMETRY_INTERVAL, OLD_BUDDY_LOG_LEVEL
from old_buddy.util import get_local_ip, run_slowly_die_fast, get_command_id

TELEMETRY_GETTERS: List[Callable[[Serial, Telemetry], Telemetry]]
TELEMETRY_GETTERS = [telemetry_inserters.insert_temperatures,
                     telemetry_inserters.insert_positions,
                     telemetry_inserters.insert_fans,
                     telemetry_inserters.insert_printing_time,
                     telemetry_inserters.insert_progress,
                     telemetry_inserters.insert_time_remaining
                     ]

INFO_GETTERS = [info_inserters.insert_firmware_version,
                info_inserters.insert_type_and_version,
                info_inserters.insert_local_ip
                ]

HEATING_REGEX = re.compile(r"^T:(\d+\.\d+) E:\d+ B:(\d+\.\d+)$")
HEATING_HOTEND_REGEX = re.compile(r"^T:(\d+\.\d+) E:([\?]|\d+) W:([\?]|\d+)$")

log = logging.getLogger(__name__)
log.setLevel(OLD_BUDDY_LOG_LEVEL)


class OldBuddy:

    def __init__(self):
        self.stopped_event = threading.Event()

        try:
            self.serial = Serial(port=PRINTER_PORT, baudrate=PRINTER_BAUDRATE,
                                 default_response_timeout=PRINTER_RESPONSE_TIMEOUT)
        except SerialException:
            log.exception("Cannot talk to the printer using the RPi port, is it enabled? "
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
            log.exception("Config load failed, lan_settings.ini missing or invalid.")
            raise

        self.connect_api = ConnectAPI(address=address, port=port, token=token, tls=tls)
        ConnectAPI.connection_error.connect(self.connection_error)

        self.state_manager = StateManager(self.serial)
        StateManager.state_changed.connect(self.state_changed)

        self.commands = Commands(self.serial, self.connect_api, self.state_manager)
        self.lcd_printer = LCDPrinter(self.serial, self.state_manager)
        # self.sd_card = SDCard(self.serial, self.state_manager)

        self.local_ip = ""
        self.last_showed_ip = time()
        self.additional_telemetry = Telemetry()
        self.printer_info = PrinterInfo()

        self.serial.register_output_handler(HEATING_REGEX, self.heating_handler)
        self.serial.register_output_handler(HEATING_HOTEND_REGEX, self.heating_hotend_handler)

        self.running = True
        self.telemetry_thread = Thread(target=self._send_telemetry, name="telemetry_thread")
        self.telemetry_thread.start()

        # Greet the user
        self.lcd_printer.enqueue_message(f"Old Buddy says: Hi")
        self.lcd_printer.enqueue_message(f"RPi is operational")
        self.lcd_printer.enqueue_message(f"Its IP address is:")
        self.update_local_ip()  # Guaranteed to print the IP first time it's called.

        # Start the ip updater after we enqueued the correct IP reporting message
        self.ip_thread = Thread(target=self._keep_updating_ip, name="IP updater")
        self.ip_thread.start()

    def stop(self):
        self.running = False
        # self.sd_card.stop()
        self.lcd_printer.stop()
        self.commands.stop()
        self.state_manager.stop()
        self.ip_thread.join()
        self.telemetry_thread.join()
        self.serial.stop()
        self.connect_api.stop()

        log.debug("Remaining threads, that could prevent us from quitting:")
        for thread in threading.enumerate():
            log.debug(thread)
        self.stopped_event.set()

    def _send_telemetry(self):
        run_slowly_die_fast(lambda: self.running, QUIT_INTERVAL, TELEMETRY_INTERVAL, self.update_telemetry)

    def _keep_updating_ip(self):
        run_slowly_die_fast(lambda: self.running, QUIT_INTERVAL, STATUS_UPDATE_INTERVAL_SEC, self.update_local_ip)

    def update_telemetry(self):
        telemetry = self.gather_telemetry()
        try:
            api_response = self.connect_api.send_dictable("/p/telemetry", telemetry)
        except RequestException:
            pass
        else:
            self.handle_telemetry_response(api_response)

    def update_local_ip(self):
        try:
            local_ip = get_local_ip()
        except:
            log.error("Failed getting the local IP, are we connected to LAN?")
            self.local_ip = ""  # Yeah empty string means disconnected :/ sorry
            # FIXME: ip module with separate flag for no IP
            self.show_ip()
        else:
            # Show the IP at least once every minute, so any errors printed won't stay forever displayed
            # FIXME: Can be done cleaner
            if self.local_ip != local_ip or time() - self.last_showed_ip > SHOW_IP_INTERVAL:
                self.last_showed_ip = time()

                if self.local_ip != local_ip:
                    log.debug(f"Ip has changed, or we reconnected. The new one is {local_ip}")
                self.local_ip = local_ip
                self.show_ip()

    # --- API response handlers ---

    def handle_telemetry_response(self, api_response):
        if api_response.status_code == 200:
            log.debug(f"Command id -> {get_command_id(api_response)}")
            if api_response.headers["Content-Type"] == "text/x.gcode":
                self.state_manager.expect_change(StateChange(api_response, default_source=Sources.CONNECT))
                self.commands.execute_gcode(api_response)
                # If the gcode execution did not cause a state change, stop expecting it
                self.state_manager.stop_expecting_change()
            else:
                try:
                    data = api_response.json()
                    if data["command"] == "SEND_INFO":
                        self.respond_with_info(api_response)
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

    def respond_with_info(self, api_response):
        command_id = get_command_id(api_response)

        if self.state_manager.is_busy():
            log.debug("The printer is busy at the moment, ignoring info request")
            self.connect_api.emit_event(EmitEvents.REJECTED, command_id, reason="The printer is busy")
            return

        event = "INFO"

        printer_info = self.gather_info()

        event_object = Event()
        event_object.event = event
        event_object.command_id = command_id
        event_object.values = printer_info

        try:
            self.connect_api.send_dictable("/p/events", event_object)
            self.connect_api.emit_event(EmitEvents.FINISHED, command_id)
        except RequestException:
            pass

    # --- Gatherers ---

    def fill(self, to_fill: Dictable,
             functions: List[Callable[[Serial, Any], Any]]):
        for getter in functions:
            if self.state_manager.is_busy():  # Do not disturb, when the printer is busy
                break

            try:
                to_fill = getter(self.serial, to_fill)
            except TimeoutError:
                log.debug(f"Function {getter.__name__} timed out waiting for serial.")
        return to_fill

    def gather_telemetry(self):
        # start with telemetry gathered by listening to the printer
        telemetry: Telemetry = self.additional_telemetry
        self.additional_telemetry = Telemetry()  # reset it

        # Do not poll the printer, when it's busy, no point
        if not self.state_manager.is_busy():
            # poll the majority of telemetry data
            # yes, the assign is redundant, but I want to make it obvious, the values is being changed
            telemetry = self.fill(telemetry, TELEMETRY_GETTERS)
        else:
            log.debug("Not bothering with telemetry, printer looks busy anyway.")

        state = self.state_manager.get_state()
        telemetry.state = state.name

        # Make sure that even if the printer tells us print specific values, nothing will be sent out while not printing
        if state not in PRINTING_STATES:
            telemetry.printing_time = None
            telemetry.estimated_time = None
            telemetry.progress = None

        return telemetry

    def gather_info(self):
        # At this time, no info is observed without polling, so start with a clean info object
        printer_info: PrinterInfo = PrinterInfo()

        # yes, the assign is redundant, but i want to hammer home the point that the variable is being modified
        printer_info = self.fill(printer_info, INFO_GETTERS)

        printer_info.state = self.state_manager.get_state().name
        printer_info.sn = "4206942069"
        printer_info.uuid = "00000000-0000-0000-0000-000000000000"
        printer_info.appendix = False
        printer_info.mac = get_mac_address()
        #printer_info.files = self.sd_card.get_api_file_tree()
        return printer_info

    def heating_handler(self, match: re.Match):
        groups = match.groups()

        self.additional_telemetry.temp_nozzle = float(groups[0])
        self.additional_telemetry.temp_bed = float(groups[1])

    def heating_hotend_handler(self, match: re.Match):
        groups = match.groups()

        self.additional_telemetry.temp_nozzle = float(groups[0])

    # --- Other ---

    def show_ip(self):
        if self.local_ip is not "":
            self.lcd_printer.enqueue_message(f"{self.local_ip}", duration=0)
        else:
            self.lcd_printer.enqueue_message(f"WiFi disconnected", duration=0)

    def state_changed(self, sender, command_id=None, source=None):
        state = self.state_manager.current_state
        # Some state changes imply telemetry data.
        # For example, if we were not printing and now we are, we have been printing for 0 min and we have 0% done
        if state == States.PRINTING and self.state_manager.last_state in {States.READY, States.BUSY}:
            self.additional_telemetry.progress = 0
            self.additional_telemetry.printing_time = 0

        self.connect_api.emit_event(EmitEvents.STATE_CHANGED, state=state.name, command_id=command_id,source=source)

    def connection_error(self, sender, path, json_dict):
        log.debug(f"Connection failed while sending data to the api point {path}. Data: {json_dict}")
        self.lcd_printer.enqueue_message("Failed when talking")
        self.lcd_printer.enqueue_message("to the Connect API.")
        if self.local_ip != "":
            self.lcd_printer.enqueue_message("Could be")
            self.lcd_printer.enqueue_message("bad WiFi settings")
            self.lcd_printer.enqueue_message("because there's")
            self.lcd_printer.enqueue_message("No WiFi connection")
        else:
            self.lcd_printer.enqueue_message("Maybe no Internet")
            self.lcd_printer.enqueue_message("or our servers.")
            self.lcd_printer.enqueue_message("Connect seems down")

    def serial_timed_out(self, sender):
        self.state_manager.busy()
