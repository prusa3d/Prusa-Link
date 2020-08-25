import configparser
import logging
import threading
from distutils.util import strtobool
from json import JSONDecodeError
from time import time

from requests import RequestException
from serial import SerialException

from old_buddy.command_handlers.commands import InfoSender
from old_buddy.command_handlers.commands import Commands
from old_buddy.informers.telemetry_gatherer import TelemetryGatherer
from old_buddy.informers.ip_updater import IPUpdater, NO_IP
from old_buddy.informers.sd_card import SDCard
from old_buddy.informers.state_manager import StateManager
from old_buddy.input_output.connect_api import ConnectAPI
from old_buddy.input_output.lcd_printer import LCDPrinter
from old_buddy.input_output.serial import Serial
from old_buddy.input_output.serial_queue.serial_queue \
    import MonitoredSerialQueue
from old_buddy.input_output.serial_queue.helpers import enqueue_instrucion
from old_buddy.model import Model
from old_buddy.settings import CONNECT_CONFIG_PATH, PRINTER_PORT, \
    PRINTER_BAUDRATE, PRINTER_RESPONSE_TIMEOUT, TELEMETRY_INTERVAL, \
    QUIT_INTERVAL
from old_buddy.settings import OLD_BUDDY_LOG_LEVEL
from old_buddy.structures.model_classes import EmitEvents
from old_buddy.util import get_command_id, run_slowly_die_fast

log = logging.getLogger(__name__)
log.setLevel(OLD_BUDDY_LOG_LEVEL)


class OldBuddy:

    def __init__(self):
        self.running = True
        self.stopped_event = threading.Event()

        self.model = Model()

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

        # Write the initial state to the model
        self.model.state = self.state_manager.get_state()

        self.telemetry_gatherer = TelemetryGatherer(self.serial,
                                                    self.serial_queue)
        self.telemetry_gatherer.updated_signal.connect(self.telemetry_gathered)
        # let's do this manually, for the telemetry to be known to the model
        # before connect can ask stuff
        self.telemetry_gatherer.poll_telemetry()

        self.lcd_printer = LCDPrinter(self.serial_queue)

        self.sd_card = SDCard(self.serial_queue, self.serial)
        self.sd_card.updated_signal.connect(self.sd_updated)
        self.sd_card.inserted_signal.connect(self.media_inserted)
        self.sd_card.ejected_signal.connect(self.media_ejected)

        # again, init the model data, before connect can ask for non-existing
        # data
        self.sd_card.update_sd()

        # Greet the user
        self.lcd_printer.enqueue_greet()

        # Start the local_ip updater after we enqueued the greetings
        self.ip_updater = IPUpdater()
        self.ip_updater.updated_signal.connect(self.ip_updated)

        # again, let's do the first one manually
        self.ip_updater.update_local_ip()

        self.info_sender = InfoSender(self.serial_queue, self.connect_api,
                                      self.model)

        self.commands = Commands(self.serial_queue, self.connect_api,
                                 self.state_manager, self.info_sender)

        self.last_sent_telemetry = time()

        # After the initial states are distributed throughout the model,
        # let's open ourselves to some commands from connect
        self.connect_thread = threading.Thread(
            target=self.keep_sending_telemetry, name="connect_thread")
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

    def telemetry_gathered(self, sender, telemetry):
        self.model.telemetry = telemetry

    def ip_updated(self, sender, local_ip):
        self.model.local_ip = local_ip

        if local_ip is not NO_IP:
            self.lcd_printer.enqueue_message(f"{local_ip}", duration=0)
        else:
            self.lcd_printer.enqueue_message(f"WiFi disconnected", duration=0)

    def sd_updated(self, sender, tree, sd_state):
        self.model.file_tree = tree
        self.model.sd_state = sd_state

    def state_changed(self, sender, command_id=None, source=None):
        state = sender.current_state
        self.model.state = state
        self.connect_api.emit_event(EmitEvents.STATE_CHANGED, state=state.name,
                                    command_id=command_id, source=source)

    def connection_error(self, sender, path, json_dict):
        log.debug(f"Connection failed while sending data to the api point "
                  f"{path}. Data: {json_dict}")
        self.lcd_printer.enqueue_connection_failed(
            self.ip_updater.local_ip == NO_IP)

    def media_inserted(self, sender, root, files):
        self.connect_api.emit_event(EmitEvents.MEDIUM_INSERTED, root=root,
                                    files=files)

    def media_ejected(self, sender, root):
        self.connect_api.emit_event(EmitEvents.MEDIUM_EJECTED, root=root)

    # --- Telemetry sending ---

    def keep_sending_telemetry(self):
        run_slowly_die_fast(lambda: self.running, QUIT_INTERVAL,
                            TELEMETRY_INTERVAL, self.send_telemetry)

    def send_telemetry(self):
        if (delay := time() - self.last_sent_telemetry) > 2:
            log.error(f"Something blocked telemetry sending for {delay}")
        self.last_sent_telemetry = time()
        telemetry = self.model.telemetry

        try:
            api_response = self.connect_api.send_model("/p/telemetry",
                                                       telemetry)
        except RequestException:
            log.debug("Failed sending telemetry")
            pass
        else:
            self.handle_telemetry_response(api_response)
