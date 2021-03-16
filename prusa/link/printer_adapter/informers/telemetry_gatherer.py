"""Contains implementation of the TelemetryGatherer class"""

import logging
import re
from pathlib import Path

from blinker import Signal  # type: ignore

from ..const import PRINTING_STATES, \
    TELEMETRY_INTERVAL, SLOW_TELEMETRY
from ..input_output.serial.instruction import MandatoryMatchableInstruction
from ..input_output.serial.serial_queue import SerialQueue
from ..input_output.serial.serial_reader import SerialReader
from ..input_output.serial.helpers import \
    wait_for_instruction, enqueue_matchable
from ..model import Model
from ..structures.regular_expressions import \
    TEMPERATURE_REGEX, POSITION_REGEX, M27_OUTPUT_REGEX, PRINT_INFO_REGEX, \
    HEATING_REGEX, HEATING_HOTEND_REGEX, PERCENT_REGEX, FAN_REGEX
from ..structures.model_classes import Telemetry
from ..structures.ticker import Ticker
from ..updatable import ThreadedUpdatable

log = logging.getLogger(__name__)


class TelemetryGatherer(ThreadedUpdatable):
    """Compiles telemetry data from any source into Telemetry"""

    thread_name = "telemetry"
    update_interval = TELEMETRY_INTERVAL

    def __init__(self, serial_reader: SerialReader, serial_queue: SerialQueue,
                 model: Model):

        self.updated_signal = Signal()  # kwargs: telemetry: Telemetry

        # The telemetry module has some extra data about the printers state
        # let's use them
        self.printing_signal = Signal()
        self.paused_serial_signal = Signal()
        self.paused_sd_signal = Signal()
        self.not_printing_signal = Signal()

        # Additionally, two for the job module
        self.progress_broken_signal = Signal()  # kwargs: progress_broken: bool
        self.file_path_signal = Signal()  # kwargs: path: str,
        #                                           filename_only: bool
        self.byte_position_signal = Signal()  # kwargs: current: int
        #                                               total: int

        self.model = model
        self.serial_reader = serial_reader
        self.serial_queue = serial_queue
        self.current_telemetry = Telemetry()

        self.slow_ticker = Ticker(SLOW_TELEMETRY)

        # G-code, match regexp, handler, to_execute()
        self.telemetry_instructions = [
            # State_manager depends on this one for detecting printing when
            # we start after the print has been started.
            ("M27 P", M27_OUTPUT_REGEX, self.m27_result, lambda: True),
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

        super().__init__()

    def ask_for_print_info(self):
        """
        Returns True, when the state is right for getting print info again
        """
        return self.model.state_manager.current_state in PRINTING_STATES \
            and self.slow_ticker.output()

    def update(self):
        """
        Goes through things to poll, for each one calls to_execute to know
        whether to poll this time, sends appropriate gcode and handles the
        result.
        :return:
        """
        for gcode, regexp, result_handler, to_execute \
                in self.telemetry_instructions:
            if to_execute():
                instruction = enqueue_matchable(self.serial_queue, gcode,
                                                regexp)
                wait_for_instruction(instruction, lambda: self.running)
                result_handler(instruction)

        self.slow_ticker.update()

    def telemetry_updated(self):
        """Notifies about new telemetry data being available"""
        self.updated_signal.send(self, telemetry=self.current_telemetry)
        # Reset every model update, so if the model resets,
        # the old data does not get sent twice
        self.current_telemetry = Telemetry()

    def temperature_result(self, instruction: MandatoryMatchableInstruction):
        """
        Temperature poll result handler, uses autoreport handler for parsing,
        as the outputs are identical

        Temperatures aren't polled anymore.
        Keeping this around for a rainy day
        """
        match = instruction.match()
        if match:
            self.temperature_handler(self, match)

    def temperature_handler(self, sender, match: re.Match):
        """
        Parses the temperature autoreport data,
        is called by poll result handler
        """
        assert sender is not None
        if match:
            groups = match.groupdict()
            self.current_telemetry.temp_nozzle = float(groups["ntemp"])
            self.current_telemetry.target_nozzle = float(groups["set_ntemp"])
            self.current_telemetry.temp_bed = float(groups["btemp"])
            self.current_telemetry.target_bed = float(groups["set_btemp"])
            self.telemetry_updated()

    def position_result(self, instruction: MandatoryMatchableInstruction):
        """
        Position poll result handler, uses autoreport handler for parsing,
        as the outputs are identical

        Positions aren't polled anymore.
        Keeping this around for a rainy day
        """
        match = instruction.match()
        if match:
            self.position_handler(self, match)

    def position_handler(self, sender, match: re.Match):
        """
        Parses the position autoreport data,
        is called by poll result handler
        """
        assert sender is not None
        if match:
            groups = match.groupdict()
            self.current_telemetry.axis_x = float(groups["x"])
            self.current_telemetry.axis_y = float(groups["y"])
            self.current_telemetry.axis_z = float(groups["z"])
            self.telemetry_updated()

    def fan_result(self, instruction: MandatoryMatchableInstruction):
        """
        Fan poll result handler, uses autoreport handler for parsing,
        as the outputs are identical

        Fans aren't polled anymore.
        Keeping this around for a rainy day
        """
        for match in instruction.get_matches():
            extruder_fan_rpm, print_fan_rpm = match.groups()
            if extruder_fan_rpm:
                self.current_telemetry.fan_extruder = int(extruder_fan_rpm)
            if print_fan_rpm:
                self.current_telemetry.fan_print = int(print_fan_rpm)
        self.telemetry_updated()

    def new_fan_handler(self, sender, match: re.Match):
        """
        Parses the fan autoreport. The data here is not the same as when
        we were polling them
        """
        assert sender is not None
        if match:
            groups = match.groupdict()
            self.current_telemetry.fan_extruder = int(groups["extruder_rpm"])
            self.current_telemetry.fan_print = int(groups["print_rpm"])
            self.current_telemetry.target_fan_extruder = \
                int(groups["extruder_power"])
            self.current_telemetry.target_fan_print = \
                int(groups["print_power"])
            self.telemetry_updated()

    # pylint: disable=too-many-locals
    # TODO: Figure out a way to break this up
    def m27_result(self, instruction: MandatoryMatchableInstruction):
        """
        Parses the M27 P polling result

        The output varies a lot between states. Usually, only a state is
        reported, unless the printer is SD printing.

        During which the printer reports the file name, byte position in the
        file and its size and the time it has been printing this file
        """
        file_or_status_match = instruction.match()
        if not file_or_status_match:
            return

        # Are we printing?
        if file_or_status_match.group("sdn_lfn"):

            byte_position_match: re.Match = instruction.match(1)
            if byte_position_match:
                groups = byte_position_match.groupdict()
                current_byte = int(groups["current"])
                bytes_in_total = int(groups["sum"])
                progress = int((current_byte / bytes_in_total) * 100)
                self.byte_position_signal.send(self,
                                               current=current_byte,
                                               total=bytes_in_total)

                # if we're SD printing and there is no reporting,
                # let's get it from here
                if self.model.job.from_sd \
                        and not self.model.job.inbuilt_reporting:
                    log.debug(
                        "SD print has no inbuilt percentage tracking, "
                        "falling back to getting progress from byte "
                        "position in the file. "
                        "Progress: %s%% Byte %s/%s", progress, current_byte,
                        bytes_in_total)
                    self.current_telemetry.progress = progress
                    self.telemetry_updated()

            print_timer_match: re.Match = instruction.match(2)
            if print_timer_match:
                groups = print_timer_match.groupdict()
                hours = int(groups["hours"])
                mins = int(groups["minutes"])
                hours_in_sec = hours * 60 * 60
                mins_in_sec = mins * 60
                printing_time_sec = mins_in_sec + hours_in_sec
                self.current_telemetry.time_printing = printing_time_sec
                self.telemetry_updated()

            mixed_path = file_or_status_match.group("sdn_lfn")
            try:
                long_path = self.model.sd_card.mixed_to_lfn_paths[mixed_path]
                self.file_path_signal.send(self,
                                           path=long_path,
                                           filename_only=False)
            except KeyError:
                filename = Path(mixed_path).name
                self.file_path_signal.send(self,
                                           path=filename,
                                           filename_only=True)
            self.printing_signal.send(self)

        elif file_or_status_match.group("no_print"):
            self.not_printing_signal.send(self)

        elif file_or_status_match.group("serial_paused"):
            self.paused_serial_signal.send(self)

        elif file_or_status_match.group("sd_paused"):
            self.paused_sd_signal.send(self)

    def flow_rate_result(self, instruction: MandatoryMatchableInstruction):
        """arses the flow rate poll result"""
        match = instruction.match()
        if match:
            flow = int(match.group("percent"))
            if 0 <= flow <= 999:
                self.current_telemetry.flow = flow
                self.telemetry_updated()

    def speed_multiplier_result(self,
                                instruction: MandatoryMatchableInstruction):
        """Parses the speed multiplier poll result"""
        match = instruction.match()
        if match:
            speed = int(match.group("percent"))
            log.debug("Speed is %s%%", speed)
            if 0 <= speed <= 999:
                self.current_telemetry.speed = speed
                self.telemetry_updated()

    def print_info_result(self, instruction: MandatoryMatchableInstruction):
        """Print info polling handler"""
        match = instruction.match()
        self.print_info_handler(self, match)

    def print_info_handler(self, sender, match: re.Match):
        """
        Parses print info autoreports which includes minutes remaining and
        progress percentage
        Called by the polling handler as the output is identical

        The minutes remaining are naively multiplied by the inverse of the
        speed multiplier
        """
        assert sender is not None
        if match:
            groups = match.groupdict()
            progress = int(groups["progress"])
            speed_agnostic_mins_remaining = int(groups["time"])

            if self.model.last_telemetry.speed is not None:
                speed_multiplier = self.model.last_telemetry.speed / 100
            else:
                speed_multiplier = 1
            inverse_speed_multiplier = speed_multiplier**-1

            mins_remaining = int(speed_agnostic_mins_remaining *
                                 inverse_speed_multiplier)
            log.debug("Mins without speed considering %s, mins otherwise %s",
                      speed_agnostic_mins_remaining, mins_remaining)
            secs_remaining = mins_remaining * 60
            progress_broken = not 0 <= progress <= 100
            if not progress_broken:
                self.current_telemetry.progress = progress
                self.telemetry_updated()

            self.progress_broken_signal.send(self,
                                             progress_broken=progress_broken)

            if mins_remaining >= 0:
                self.current_telemetry.time_estimated = secs_remaining
                self.telemetry_updated()

    def heating_handler(self, sender, match: re.Match):
        """
        When the printer is heating, it cannot be polled, but it
        starts reporting temperatures by itself.
        Parses the output of the printer when its heating the heatbed
        and the extruder
        """
        assert sender is not None
        groups = match.groupdict()

        self.current_telemetry.temp_nozzle = float(groups["ntemp"])
        self.current_telemetry.temp_bed = float(groups["btemp"])
        self.telemetry_updated()

    def heating_hotend_handler(self, sender, match: re.Match):
        """
        When the printer is heating, it cannot be polled, but it
        starts reporting temperatures by itself.
        Parses the output of the printer when its heating the hotend
        and the extruder
        """
        assert sender is not None
        groups = match.groupdict()

        self.current_telemetry.temp_nozzle = float(groups["ntemp"])
        self.telemetry_updated()

    def new_print(self):
        """
        When a new print is detected, it implies some values like progress and
        time_printing being at zero
        """
        self.current_telemetry.progress = 0
        self.current_telemetry.time_printing = 0
        self.telemetry_updated()
