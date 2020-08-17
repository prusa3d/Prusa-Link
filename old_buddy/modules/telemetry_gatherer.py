"""Functions for gathering telemetry"""

import logging
import re
from threading import Thread

from blinker import Signal

from old_buddy.modules.connect_api import Telemetry, States
from old_buddy.modules.serial import Serial, WriteIgnored
from old_buddy.modules.state_manager import StateManager, PRINTING_STATES
from old_buddy.settings import QUIT_INTERVAL, TELEMETRY_INTERVAL, \
    TELEMETRY_GATHERER_LOG_LEVEL
from old_buddy.util import run_slowly_die_fast

TEMPERATURE_REGEX = re.compile(
    r"^ok ?T: ?(-?\d+\.\d+) ?/(-?\d+\.\d+) ?B: ?(-?\d+\.\d+) ?/(-?\d+\.\d+) ?"
    r"T0: ?(-?\d+\.\d+) ?/(-?\d+\.\d+) ?@: ?(-?\d+) ?B@: ?(-?\d+) ?"
    r"P: ?(-?\d+\.\d+) ?A: ?(-?\d+\.\d+)$")
POSITION_REGEX = re.compile(
    r"^X: ?(-?\d+\.\d+) ?Y: ?(-?\d+\.\d+) ?Z: ?(-?\d+\.\d+) ?"
    r"E: ?(-?\d+\.\d+) ?Count ?X: ?(-?\d+\.\d+) ?Y: ?(-?\d+\.\d+) ?"
    r"Z: ?(-?\d+\.\d+) ?E: ?(-?\d+\.\d+)$")
E_FAN_REGEX = re.compile(r"^E0:(\d+) ?RPM$")
P_FAN_REGEX = re.compile(r"^PRN0:(\d+) ?RPM$")
PRINT_TIME_REGEX = re.compile(r"^(Not SD printing)$|^((\d+):(\d{2}))$")
PROGRESS_REGEX = re.compile(r"^NORMAL MODE: Percent done: (\d+);.*")
TIME_REMAINING_REGEX = re.compile(r"^SILENT MODE: Percent done: (\d+); "
                                  r"print time remaining in mins: (-?\d+) ?$")
HEATING_REGEX = re.compile(r"^T:(\d+\.\d+) E:\d+ B:(\d+\.\d+)$")
HEATING_HOTEND_REGEX = re.compile(r"^T:(\d+\.\d+) E:([?]|\d+) W:([?]|\d+)$")

# XXX:  "M221", "M220
TELEMETRY_GCODES = ["M105", "M114", "PRUSA FAN", "M27", "M73"]

log = logging.getLogger(__name__)
log.setLevel(TELEMETRY_GATHERER_LOG_LEVEL)


class TelemetryGatherer:
    send_telemetry_signal = Signal()  # kwargs: telemetry: Telemetry

    # Just checks if there is not more than one instance in existence,
    # This is not a singleton!
    instance = None

    def __init__(self, serial: Serial, state_manager: StateManager):
        assert self.instance is None, "If running more than one instance" \
                                      "is required, consider moving the " \
                                      "signals from class to instance " \
                                      "variables."
        self.instance = self

        self.state_manager = state_manager
        self.serial = serial

        # Looked better wrapped to 120 characters. Just saying
        self.serial.register_output_handler(TEMPERATURE_REGEX,
                                            self.temperature_handler)
        self.serial.register_output_handler(POSITION_REGEX,
                                            self.position_handler)
        self.serial.register_output_handler(E_FAN_REGEX,
                                            self.fan_extruder_handler)
        self.serial.register_output_handler(P_FAN_REGEX,
                                            self.fan_print_handler)
        self.serial.register_output_handler(PRINT_TIME_REGEX,
                                            self.print_time_handler)
        self.serial.register_output_handler(PROGRESS_REGEX,
                                            self.progress_handler)
        self.serial.register_output_handler(TIME_REMAINING_REGEX,
                                            self.time_remaining_handler)
        self.serial.register_output_handler(HEATING_REGEX,
                                            self.heating_handler)
        self.serial.register_output_handler(HEATING_HOTEND_REGEX,
                                            self.heating_hotend_handler)

        self.current_telemetry = Telemetry()
        self.last_telemetry = self.current_telemetry
        self.running = True
        self.telemetry_thread = Thread(target=self.keep_updating_telemetry,
                                       name="telemetry_thread")
        self.telemetry_thread.start()

    def keep_updating_telemetry(self):
        run_slowly_die_fast(lambda: self.running, QUIT_INTERVAL,
                            TELEMETRY_INTERVAL, self.update_telemetry)

    def send_telemetry(self):
        state = self.state_manager.get_state()
        self.current_telemetry.state = state.name

        # Make sure that even if the printer tells us print specific values,
        # nothing will be sent out while not printing
        if state not in PRINTING_STATES:
            self.current_telemetry.time_printing = None
            self.current_telemetry.time_estimated = None
            self.current_telemetry.progress = None
        if state == States.PRINTING:
            self.current_telemetry.axis_x = None
            self.current_telemetry.axis_y = None

        TelemetryGatherer.send_telemetry_signal.send(
            self, telemetry=self.current_telemetry)

    def update_telemetry(self):
        self.send_telemetry()

        self.last_telemetry = self.current_telemetry
        self.current_telemetry = Telemetry()

        if self.state_manager.base_state == States.BUSY:
            log.debug("Printer seems busy, not asking for telemetry")
            self.ping_printer()

        for gcode in TELEMETRY_GCODES:
            if self.state_manager.base_state == States.BUSY:
                # Do not disturb, when the printer is busy
                break

            try:
                self.serial.write(gcode)
            except WriteIgnored:
                log.debug("Telemetry request got ignored,"
                          "serial is exclusive for someone else")

    def ping_printer(self):
        try:
            self.serial.write("PRUSA PING")
        except WriteIgnored:
            pass
        return

    def temperature_handler(self, match: re.Match):
        groups = match.groups()
        self.current_telemetry.temp_nozzle = float(groups[0])
        self.current_telemetry.target_nozzle = float(groups[1])
        self.current_telemetry.temp_bed = float(groups[2])
        self.current_telemetry.target_bed = float(groups[3])

    def position_handler(self, match: re.Match):
        groups = match.groups()
        self.current_telemetry.axis_x = float(groups[4])
        self.current_telemetry.axis_y = float(groups[5])
        self.current_telemetry.axis_z = float(groups[6])

    def fan_extruder_handler(self, match: re.Match):
        self.current_telemetry.fan_extruder = float(match.groups()[0])

    def fan_print_handler(self, match: re.Match):
        self.current_telemetry.fan_print = float(match.groups()[0])

    def print_time_handler(self, match: re.Match):
        groups = match.groups()
        if groups[1] != "" and groups[1] is not None:
            printing_time_hours = int(groups[2])
            printing_time_mins = int(groups[3])
            hours_in_sec = printing_time_hours * 60 * 60
            mins_in_sec = printing_time_mins * 60
            printing_time_sec = mins_in_sec + hours_in_sec
            self.current_telemetry.time_printing = printing_time_sec

    def progress_handler(self, match: re.Match):
        groups = match.groups()
        progress = int(groups[0])
        if 0 <= progress <= 100:
            self.current_telemetry.progress = progress

    def time_remaining_handler(self, match: re.Match):
        # FIXME: Using the more conservative values from silent mode,
        #  need to know in which mode we are
        groups = match.groups()
        mins_remaining = int(groups[1])
        secs_remaining = mins_remaining * 60
        if mins_remaining >= 0:
            self.current_telemetry.time_estimated = secs_remaining

    def flow_rate_handler(self, match: re.Match):
        groups = match.groups()
        flow = int(groups[0])
        if 0 <= flow <= 100:
            self.current_telemetry.flow = flow

    def speed_multiplier_handler(self, match: re.Match):
        groups = match.groups()
        speed = int(groups[0])
        if 0 <= speed <= 100:
            self.current_telemetry.speed = speed

    def heating_handler(self, match: re.Match):
        groups = match.groups()

        self.current_telemetry.temp_nozzle = float(groups[0])
        self.current_telemetry.temp_bed = float(groups[1])

    def heating_hotend_handler(self, match: re.Match):
        groups = match.groups()

        self.current_telemetry.temp_nozzle = float(groups[0])

    def stop(self):
        self.running = False
        self.telemetry_thread.join()
