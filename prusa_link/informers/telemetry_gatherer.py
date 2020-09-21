"""Functions for gathering telemetry"""

import logging
import re

from blinker import Signal

from prusa_link.input_output.serial.serial_reader import SerialReader
from prusa_link.structures.model_classes import Telemetry
from prusa_link.input_output.serial.serial_queue import SerialQueue
from prusa_link.input_output.serial.helpers import wait_for_instruction, \
    enqueue_instruction
from prusa_link.default_settings import get_settings
from prusa_link.structures.regular_expressions import TEMPERATURE_REGEX, \
    POSITION_REGEX, E_FAN_REGEX, P_FAN_REGEX, PRINT_TIME_REGEX, \
    PROGRESS_REGEX, TIME_REMAINING_REGEX, HEATING_REGEX, HEATING_HOTEND_REGEX
from prusa_link.updatable import ThreadedUpdatable

# XXX:  "M221", "M220
TELEMETRY_GCODES = ["M105", "M114", "PRUSA FAN", "M27", "M73"]

TIME = get_settings().TIME
LOG = get_settings().LOG


log = logging.getLogger(__name__)
log.setLevel(LOG.TELEMETRY_GATHERER_LOG_LEVEL)


class TelemetryGatherer(ThreadedUpdatable):
    thread_name = "telemetry"
    update_interval = TIME.TELEMETRY_INTERVAL

    def __init__(self, serial_reader: SerialReader, serial_queue: SerialQueue):
        self.updated_signal = Signal()  # kwargs: telemetry: Telemetry

        self.serial_reader = serial_reader
        self.serial_queue = serial_queue

        regex_handlers = {
            TEMPERATURE_REGEX: self.temperature_handler,
            POSITION_REGEX: self.position_handler,
            E_FAN_REGEX: self.fan_extruder_handler,
            P_FAN_REGEX: self.fan_print_handler,
            PRINT_TIME_REGEX: self.print_time_handler,
            PROGRESS_REGEX: self.progress_handler,
            TIME_REMAINING_REGEX: self.time_remaining_handler,
            HEATING_REGEX: self.heating_handler,
            HEATING_HOTEND_REGEX: self.heating_hotend_handler
        }

        for regex, handler in regex_handlers.items():
            self.serial_reader.add_handler(regex, handler)

        self.current_telemetry = Telemetry()

        super().__init__()

    def _update(self):

        # Only ask for telemetry again, when the previous is confirmed
        for gcode in TELEMETRY_GCODES:
            # Wait indefinitely, if the queue got stuck
            # we aren't the ones who should handle that
            instruction = enqueue_instruction(self.serial_queue, gcode)
            wait_for_instruction(instruction, lambda: self.running)

        self.current_telemetry = Telemetry()

    def telemetry_updated(self):
        self.updated_signal.send(self, telemetry=self.current_telemetry)

    def temperature_handler(self, sender, match: re.Match):
        groups = match.groups()
        self.current_telemetry.temp_nozzle = float(groups[0])
        self.current_telemetry.target_nozzle = float(groups[1])
        self.current_telemetry.temp_bed = float(groups[2])
        self.current_telemetry.target_bed = float(groups[3])
        self.telemetry_updated()

    def position_handler(self, sender, match: re.Match):
        groups = match.groups()
        self.current_telemetry.axis_x = float(groups[4])
        self.current_telemetry.axis_y = float(groups[5])
        self.current_telemetry.axis_z = float(groups[6])
        self.telemetry_updated()

    def fan_extruder_handler(self, sender, match: re.Match):
        self.current_telemetry.fan_extruder = float(match.groups()[0])
        self.telemetry_updated()

    def fan_print_handler(self, sender, match: re.Match):
        self.current_telemetry.fan_print = float(match.groups()[0])
        self.telemetry_updated()

    def print_time_handler(self, sender, match: re.Match):
        groups = match.groups()
        if groups[1] != "" and groups[1] is not None:
            printing_time_hours = int(groups[2])
            printing_time_mins = int(groups[3])
            hours_in_sec = printing_time_hours * 60 * 60
            mins_in_sec = printing_time_mins * 60
            printing_time_sec = mins_in_sec + hours_in_sec
            self.current_telemetry.time_printing = printing_time_sec
            self.telemetry_updated()

    def progress_handler(self, sender, match: re.Match):
        groups = match.groups()
        progress = int(groups[0])
        if 0 <= progress <= 100:
            self.current_telemetry.progress = progress
            self.telemetry_updated()

    def time_remaining_handler(self, sender, match: re.Match):
        # FIXME: Using the more conservative values from silent mode,
        #  need to know in which mode we are
        groups = match.groups()
        mins_remaining = int(groups[1])
        secs_remaining = mins_remaining * 60
        if mins_remaining >= 0:
            self.current_telemetry.time_estimated = secs_remaining
            self.telemetry_updated()

    def flow_rate_handler(self, sender, match: re.Match):
        groups = match.groups()
        flow = int(groups[0])
        if 0 <= flow <= 100:
            self.current_telemetry.flow = flow
            self.telemetry_updated()

    def speed_multiplier_handler(self, sender, match: re.Match):
        groups = match.groups()
        speed = int(groups[0])
        if 0 <= speed <= 100:
            self.current_telemetry.speed = speed
            self.telemetry_updated()

    def heating_handler(self, sender, match: re.Match):
        groups = match.groups()

        self.current_telemetry.temp_nozzle = float(groups[0])
        self.current_telemetry.temp_bed = float(groups[1])
        self.telemetry_updated()

    def heating_hotend_handler(self, sender, match: re.Match):
        groups = match.groups()

        self.current_telemetry.temp_nozzle = float(groups[0])
        self.telemetry_updated()
