import logging
import re
from json import JSONDecodeError
from threading import Thread
from typing import List, Callable, Any

from getmac import get_mac_address

from old_buddy.connect_communication import Telemetry, PrinterInfo, Dictable, ConnectCommunication, EmitEvents, Event, \
    Sources
from old_buddy.printer.commands import Commands
from old_buddy.printer.state_manager import StateManager, States, PRINTING_STATES, StateChange
from old_buddy.printer_communication import PrinterCommunication
from old_buddy.printer.inserters import telemetry_inserters, info_inserters
from old_buddy.settings import QUIT_INTERVAL, STATUS_UPDATE_INTERVAL_SEC, TELEMETRY_INTERVAL
from old_buddy.util import get_local_ip, run_slowly_die_fast, get_command_id

TELEMETRY_GETTERS: List[Callable[[PrinterCommunication, Telemetry], Telemetry]]
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

log = logging.getLogger(__name__)


class Printer:

    def __init__(self, printer_communication: PrinterCommunication, connect_communication: ConnectCommunication):
        self.connect_communication = connect_communication
        self.printer_communication: PrinterCommunication = printer_communication

        self.state_manager = StateManager(self.printer_communication, self.state_changed)

        self.commands = Commands(self.printer_communication, self.connect_communication, self.state_manager)

        self.local_ip = ""
        self.additional_telemetry = Telemetry()

        self.printer_communication.register_output_handler(HEATING_REGEX, self.temperature_handler)

        self.running = True
        self.ip_thread = Thread(target=self._keep_updating_state, name="IP updater")
        self.telemetry_thread = Thread(target=self._send_telemetry, name="telemetry_thread")
        self.ip_thread.start()
        self.telemetry_thread.start()

    def _send_telemetry(self):
        run_slowly_die_fast(lambda: self.running, QUIT_INTERVAL, TELEMETRY_INTERVAL, self.update_telemetry)

    def _keep_updating_state(self):
        run_slowly_die_fast(lambda: self.running, QUIT_INTERVAL, STATUS_UPDATE_INTERVAL_SEC, self.update_local_ip)

    def stop(self):
        self.running = False
        self.state_manager.stop()
        self.commands.stop()
        log.debug("State manager should be stopped")
        self.ip_thread.join()
        self.telemetry_thread.join()

    def update_telemetry(self):
        api_response = self.connect_communication.send_telemetry(self.gather_telemetry())
        self.handle_telemetry_response(api_response)

    def update_local_ip(self):
        try:
            local_ip = get_local_ip()
        except:
            log.error("Failed getting the local IP, are we connected to LAN?")
            self.show_ip()
        else:
            if self.local_ip != local_ip:
                log.debug(f"ip has changed. The new one is {local_ip}")
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

    def respond_with_info(self, api_response):

        event = "INFO"
        command_id = get_command_id(api_response)

        printer_info = self.gather_info()

        event_object = Event()
        event_object.event = event
        event_object.command_id = command_id
        event_object.values = printer_info

        self.connect_communication.send_event(event_object)
        self.connect_communication.emit_event(EmitEvents.FINISHED, command_id)

    # --- Gatherers ---

    def fill(self, to_fill: Dictable,
             functions: List[Callable[[PrinterCommunication, Any], Any]]):
        for getter in functions:
            try:
                to_fill = getter(self.printer_communication, to_fill)
            except TimeoutError:
                log.debug(f"Function {getter.__name__} timed out waiting for printer response.")
                self.state_manager.busy()
        return to_fill

    def gather_telemetry(self):
        # start with telemetry gathered by listening to the printer
        telemetry: Telemetry = self.additional_telemetry
        self.additional_telemetry = Telemetry()  # reset it

        # Do not poll the printer, when it's busy, no point
        if self.state_manager.base_state != States.BUSY:
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
        return printer_info

    def temperature_handler(self, match: re.Match):
        groups = match.groups()

        self.additional_telemetry.temp_nozzle = float(groups[0])
        self.additional_telemetry.temp_bed = float(groups[1])

    # --- Other ---

    def show_ip(self):
        if self.local_ip is not "":
            self.printer_communication.write(f"M117 {self.local_ip}")
        else:
            self.printer_communication.write(f"M117 WiFi disconnected")

    def state_changed(self, command_id=None, source=None):
        state = self.state_manager.current_state
        # Some state changes imply telemetry data.
        # For example, if we were not printing and now we are, we have been printing for 0 min and we have 0% done
        if state == States.PRINTING and self.state_manager.last_state in {States.READY, States.BUSY}:
            self.additional_telemetry.progress = 0
            self.additional_telemetry.printing_time = 0

        self.connect_communication.emit_event(EmitEvents.STATE_CHANGED, state=state.name, command_id=command_id,
                                              source=source)
