"""Functions for gathering telemetry"""

import logging
import re

from blinker import Signal

from prusa.link.printer_adapter.input_output.serial.instruction import \
    MandatoryMatchableInstruction
from prusa.link.printer_adapter.input_output.serial.serial_queue import \
    SerialQueue
from prusa.link.printer_adapter.input_output.serial.serial_reader import \
    SerialReader
from prusa.link.printer_adapter.input_output.serial.helpers import \
    wait_for_instruction, enqueue_matchable
from prusa.link.printer_adapter.model import Model
from prusa.link.printer_adapter.structures.regular_expressions import \
    TEMPERATURE_REGEX, POSITION_REGEX, PRINT_TIME_REGEX, PRINT_INFO_REGEX, \
    HEATING_REGEX, HEATING_HOTEND_REGEX, FAN_RPM_REGEX, PERCENT_REGEX, FAN_REGEX
from prusa.link.printer_adapter.structures.model_classes import Telemetry
from prusa.link.printer_adapter.const import PRINTING_STATES, \
    TELEMETRY_INTERVAL, SLOW_TELEMETRY
from prusa.link.printer_adapter.structures.ticker import Ticker
from prusa.link.printer_adapter.updatable import ThreadedUpdatable

log = logging.getLogger(__name__)


class TelemetryGatherer(ThreadedUpdatable):
    thread_name = "telemetry"
    update_interval = TELEMETRY_INTERVAL

    def __init__(self, serial_reader: SerialReader, serial_queue: SerialQueue,
                 model: Model):

        self.updated_signal = Signal()  # kwargs: telemetry: Telemetry

        self.model = model
        self.serial_reader = serial_reader
        self.serial_queue = serial_queue

        self.slow_ticker = Ticker(SLOW_TELEMETRY)

        # G-code, match regexp, handler, to_execute()
        self.telemetry_instructions = [
            # State_manager depends on this one for detecting printing when
            # we start after the print has been started.
            ("M27", PRINT_TIME_REGEX, self.print_time_result, lambda: True),

            ("M221", PERCENT_REGEX, self.flow_rate_result,
             self.slow_ticker.output),
            ("M220", PERCENT_REGEX, self.speed_multiplier_result,
             self.slow_ticker.output),

            ("M73", PRINT_INFO_REGEX, self.print_info_result,
             self.ask_for_print_info),
            ]

        regex_handlers = {
            PRINT_INFO_REGEX: self.print_info_handler,
            HEATING_REGEX: self.heating_handler,
            HEATING_HOTEND_REGEX: self.heating_hotend_handler,
            TEMPERATURE_REGEX: self.temperature_handler,
            POSITION_REGEX: self.position_handler,
            FAN_REGEX: self.new_fan_handler
        }

        for regex, handler in regex_handlers.items():
            self.serial_reader.add_handler(regex, handler)

        self.current_telemetry = Telemetry()

        super().__init__()

    def ask_for_print_info(self):
        return self.model.state_manager.current_state in PRINTING_STATES and\
               self.slow_ticker.output()

    def ask_for_positions(self):
        return self.model.state_manager.current_state not in PRINTING_STATES or\
               self.slow_ticker.output()

    def update(self):
        for gcode, regexp, result_handler, to_execute \
                in self.telemetry_instructions:
            if to_execute():
                instruction = enqueue_matchable(self.serial_queue, gcode,
                                                regexp)
                wait_for_instruction(instruction, lambda: self.running)
                result_handler(instruction)

        self.current_telemetry = Telemetry()
        self.slow_ticker.update()

    def telemetry_updated(self):
        self.updated_signal.send(self, telemetry=self.current_telemetry)

    def temperature_handler(self, sender, match: re.Match):
        if match:
            groups = match.groups()
            self.current_telemetry.temp_nozzle = float(groups[0])
            self.current_telemetry.target_nozzle = float(groups[1])
            self.current_telemetry.temp_bed = float(groups[2])
            self.current_telemetry.target_bed = float(groups[3])
            self.telemetry_updated()

    def temperature_result(self, instruction: MandatoryMatchableInstruction):
        match = instruction.match()
        if match:
            self.temperature_handler(None, match)

    def position_result(self, instruction: MandatoryMatchableInstruction):
        match = instruction.match()
        if match:
            self.position_handler(None, match)

    def position_handler(self, sender, match: re.Match):
        if match:
            groups = match.groups()
            self.current_telemetry.axis_x = float(groups[4])
            self.current_telemetry.axis_y = float(groups[5])
            self.current_telemetry.axis_z = float(groups[6])
            self.telemetry_updated()

    def fan_result(self, instruction: MandatoryMatchableInstruction):
        for match in instruction.get_matches():
            extruder_fan_rpm, print_fan_rpm = match.groups()
            if extruder_fan_rpm:
                self.current_telemetry.fan_extruder = float(extruder_fan_rpm)
            if print_fan_rpm:
                self.current_telemetry.fan_print = float(print_fan_rpm)
        self.telemetry_updated()

    def new_fan_handler(self, sender, match: re.Match):
        if match:
            groups = match.groups()
            self.current_telemetry.fan_extruder = int(groups[0])
            self.current_telemetry.fan_print = int(groups[1])
            self.current_telemetry.target_fan_extruder = int(groups[2])
            self.current_telemetry.target_fan_print = int(groups[3])
            self.telemetry_updated()

    def print_time_result(self, instruction: MandatoryMatchableInstruction):
        match = instruction.match()
        if match and match.groups()[1]:
            groups = match.groups()
            printing_time_hours = int(groups[2])
            printing_time_mins = int(groups[3])
            hours_in_sec = printing_time_hours * 60 * 60
            mins_in_sec = printing_time_mins * 60
            printing_time_sec = mins_in_sec + hours_in_sec
            self.current_telemetry.time_printing = printing_time_sec
            self.telemetry_updated()

    def flow_rate_result(self, instruction: MandatoryMatchableInstruction):
        match = instruction.match()
        if match:
            groups = match.groups()
            flow = int(groups[0])
            if 0 <= flow <= 999:
                self.current_telemetry.flow = flow
                self.telemetry_updated()

    def speed_multiplier_result(self, instruction: MandatoryMatchableInstruction):
        match = instruction.match()
        if match:
            groups = match.groups()
            speed = int(groups[0])
            log.debug(f"Speed is {speed}%")
            if 0 <= speed <= 999:
                self.current_telemetry.speed = speed
                self.telemetry_updated()

    def print_info_result(self, instruction: MandatoryMatchableInstruction):
        match = instruction.match()
        self.print_info_handler(None, match)

    def print_info_handler(self, sender, match: re.Match):
        if match:
            groups = match.groups()
            progress = int(groups[0])
            speed_agnostic_mins_remaining = int(groups[1])

            if self.model.last_telemetry.speed is not None:
                speed_multiplier = self.model.last_telemetry.speed / 100
            else:
                speed_multiplier = 1
            inverse_speed_multiplier = speed_multiplier ** -1

            mins_remaining = int(speed_agnostic_mins_remaining *
                                 inverse_speed_multiplier)
            log.debug(f"Mins without speed considering "
                      f"{speed_agnostic_mins_remaining}, mins otherwise "
                      f"{mins_remaining}")
            secs_remaining = mins_remaining * 60
            if 0 <= progress <= 100:
                self.current_telemetry.progress = progress
                self.telemetry_updated()
            if mins_remaining >= 0:
                self.current_telemetry.time_estimated = secs_remaining
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
