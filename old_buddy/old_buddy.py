import configparser
import logging
import threading
from distutils.util import strtobool
from json import JSONDecodeError
from time import time, sleep

from requests import RequestException
from serial import SerialException

from old_buddy.modules.commands import Commands
from old_buddy.modules.connect_api import ConnectAPI, EmitEvents
from old_buddy.modules.info_sender import InfoSender
from old_buddy.modules.ip_updater import IPUpdater, NO_IP
from old_buddy.modules.lcd_printer import LCDPrinter
from old_buddy.modules.serial import Serial
from old_buddy.modules.sd_card import SDCard
from old_buddy.modules.serial_queue.helpers import enqueue_instrucion
from old_buddy.modules.serial_queue.serial_queue import MonitoredSerialQueue
from old_buddy.modules.state_manager import StateManager, States,\
    PRINTING_STATES
from old_buddy.modules.telemetry_gatherer import TelemetryGatherer
from old_buddy.settings import CONNECT_CONFIG_PATH, PRINTER_PORT, \
    PRINTER_BAUDRATE, PRINTER_RESPONSE_TIMEOUT, RX_SIZE, TELEMETRY_INTERVAL, \
    QUIT_INTERVAL
from old_buddy.settings import OLD_BUDDY_LOG_LEVEL
from old_buddy.util import get_command_id, run_slowly_die_fast

log = logging.getLogger(__name__)
log.setLevel(OLD_BUDDY_LOG_LEVEL)


class OldBuddy:

    def __init__(self):
        self.running = True
        self.stopped_event = threading.Event()

        try:
            self.serial = Serial(port=PRINTER_PORT, baudrate=PRINTER_BAUDRATE,
                                 default_timeout=PRINTER_RESPONSE_TIMEOUT)
        except SerialException:
            log.exception(
                "Cannot talk to the printer using the RPi port, "
                "is it enabled? Is the Pi configured correctly?")
            raise

        self.serial_queue = MonitoredSerialQueue(self.serial)

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
            enqueue_instrucion(self.serial_queue, "M117 Bad Old Buddy config")
            log.exception(
                "Config load failed, lan_settings.ini missing or invalid.")
            raise

        self.connect_api = ConnectAPI(address=address, port=port, token=token,
                                      tls=tls)
        ConnectAPI.connection_error.connect(self.connection_error)

        self.state_manager = StateManager(self.serial)
        StateManager.state_changed.connect(self.state_changed)

        self.telemetry_gatherer = TelemetryGatherer(self.serial,
                                                    self.serial_queue)

        self.lcd_printer = LCDPrinter(self.serial_queue, self.state_manager)
        self.sd_card = SDCard(self.serial_queue, self.serial,
                              self.state_manager, self.connect_api)

        # Greet the user
        self.lcd_printer.enqueue_greet()

        # Start the ip updater after we enqueued the correct IP report message
        self.ip_updater = IPUpdater(self.lcd_printer)

        self.info_sender = InfoSender(self.serial_queue, self.state_manager,
                                      self.connect_api, self.ip_updater,
                                      self.sd_card)

        self.commands = Commands(self.serial_queue, self.connect_api,
                                 self.state_manager, self.info_sender)

        self.last_sent_telemetry = time()

        self.connect_thread = threading.Thread(
            target=self.keep_sending_telemetry, name="telemetry_sending_thread")
        self.connect_thread.start()

    def stop(self):
        self.running = False
        self.connect_thread.join()
        self.sd_card.stop()
        self.lcd_printer.stop()
        self.commands.stop_command_thread()
        self.state_manager.stop()
        self.telemetry_gatherer.stop()
        self.ip_updater.stop()
        self.serial_queue.stop()
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
                self.commands.execute_gcode(api_response)
            else:
                try:
                    data = api_response.json()
                    if data["command"] == "SEND_INFO":
                        self.commands.respond_with_info(api_response)
                    elif data["command"] == "START_PRINT":
                        self.commands.start_print(api_response)
                    elif data["command"] == "STOP_PRINT":
                        self.commands.stop_print(api_response)
                    elif data["command"] == "PAUSE_PRINT":
                        self.commands.pause_print(api_response)
                    elif data["command"] == "RESUME_PRINT":
                        self.commands.resume_print(api_response)
                    else:
                        command_id = get_command_id(api_response)
                        self.connect_api.emit_event(EmitEvents.REJECTED,
                                                    command_id,
                                                    "Unknown command")

                except JSONDecodeError:
                    log.exception(
                        f"Failed to decode a response {api_response}")
        elif api_response.status_code >= 300:
            code = api_response.status_code
            log.error(f"Connect responded with code {code}")

            if code == 400:
                self.lcd_printer.enqueue_400()
            elif code == 401:
                self.lcd_printer.enqueue_401()
            elif code == 403:
                self.lcd_printer.enqueue_403()
            elif code == 501:
                self.lcd_printer.enqueue_501()

    # --- Signal handlers ---

    def state_changed(self, sender, command_id=None, source=None):
        state = sender.current_state
        self.connect_api.emit_event(EmitEvents.STATE_CHANGED, state=state.name,
                                    command_id=command_id, source=source)

    def connection_error(self, sender, path, json_dict):
        log.debug(f"Connection failed while sending data to the api point "
                  f"{path}. Data: {json_dict}")
        self.lcd_printer.enqueue_connection_failed(
            self.ip_updater.local_ip == NO_IP)

    # --- Telemetry sending ---

    def keep_sending_telemetry(self):
        run_slowly_die_fast(lambda: self.running, QUIT_INTERVAL,
                            TELEMETRY_INTERVAL, self.send_telemetry)

    def send_telemetry(self):
        if (delay := time() - self.last_sent_telemetry) > 2:
            log.error(f"Something blocked telemetry sending for {delay}")
        self.last_sent_telemetry = time()

        telemetry = self.telemetry_gatherer.get_telemetry()

        state = self.state_manager.get_state()
        telemetry.state = state.name

        # Make sure that even if the printer tells us print specific values,
        # nothing will be sent out while not printing
        if state not in PRINTING_STATES:
            telemetry.time_printing = None
            telemetry.time_estimated = None
            telemetry.progress = None
        if state == States.PRINTING:
            telemetry.axis_x = None
            telemetry.axis_y = None

        try:
            api_response = self.connect_api.send_model("/p/telemetry",
                                                       telemetry)
        except RequestException:
            log.debug("Failed sending telemetry")
            pass
        else:
            self.handle_telemetry_response(api_response)
