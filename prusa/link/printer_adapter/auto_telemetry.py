"""Contains implementation of the ReportingEnsurer class"""
from re import Match
from time import time

from ..serial.serial_queue import SerialQueue
from ..serial.serial_parser import SerialParser
from ..serial.helpers import \
        enqueue_instruction, wait_for_instruction
from ..const import REPORTING_TIMEOUT
from .model import Model
from .structures.model_classes import Telemetry
from .structures.regular_expressions import \
        TEMPERATURE_REGEX, POSITION_REGEX, FAN_REGEX
from .updatable import ThreadedUpdatable


class AutoTelemetry(ThreadedUpdatable):
    """
    Monitors and parses autoreporting output, if any is missing, tries to turn
    the autoreporting back on
    """
    thread_name = "temp_ensurer"
    update_interval = 10

    def __init__(self, serial_parser: SerialParser, serial_queue: SerialQueue,
                 model: Model):
        super().__init__()
        self.serial_parser = serial_parser
        self.serial_queue = serial_queue
        self.model: Model = model
        self.serial_parser.add_handler(
            TEMPERATURE_REGEX, self.temps_recorded)
        self.serial_parser.add_handler(
            POSITION_REGEX, self.positions_recorded)
        self.serial_parser.add_handler(
            FAN_REGEX, self.fans_recorded)

        self.last_seen_positions = 0.
        self.last_seen_fans = 0.
        self.last_seen_temps = 0.

    def temps_recorded(self, sender, match: Match):
        """
        Reset the timeout for temperatures
        and write them through to the model
        """
        assert sender is not None
        self.last_seen_temps = time()

        values = match.groupdict()
        telemetry = Telemetry(temp_nozzle=float(values["ntemp"]))
        if "btemp" in values:
            telemetry.temp_bed = float(values["btemp"])
        if "set_ntemp" in values and "set_btemp" in values:
            telemetry.target_nozzle = float(values["set_ntemp"])
            telemetry.target_bed = float(values["set_btemp"])
        self.model.set_telemetry(telemetry)

    def positions_recorded(self, sender, match: Match):
        """
        Reset the timeout for positions
        and write them through to the model
        """
        assert sender is not None
        self.last_seen_positions = time()

        values = match.groupdict()
        self.model.set_telemetry(
            Telemetry(axis_x=float(values["x"]),
                      axis_y=float(values["y"]),
                      axis_z=float(values["z"])))

    def fans_recorded(self, sender, match: Match):
        """
        Reset the timeout for fans
        and write their RPMs through to the model
        """
        assert sender is not None
        self.last_seen_fans = time()

        values = match.groupdict()
        self.model.set_telemetry(
            Telemetry(fan_extruder=int(values["extruder_rpm"]),
                      fan_print=int(values["print_rpm"]),
                      target_fan_extruder=int(values["extruder_power"]),
                      target_fan_print=int(values["print_power"])))

    def update(self):
        """
        If any one of the report intervals is larger than REPORTING_TIMEOUT
        calls turn_reporting_on()
        """
        refresh_times = (self.last_seen_temps,
                         self.last_seen_positions,
                         self.last_seen_fans)
        biggest_interval = time() - min(refresh_times)

        if biggest_interval > REPORTING_TIMEOUT:
            self.turn_reporting_on()

    def turn_reporting_on(self):
        """
        Tries to turn reporting on using the M155
        The C argument is the bitmask for type of autoreporting
        The S argument is the frequency of autoreports
        """
        instruction = enqueue_instruction(self.serial_queue, "M155 S2 C7")
        wait_for_instruction(instruction, should_wait_evt=self.quit_evt)
        self._reset_last_seen()

    def proper_stop(self):
        """
        Stops the autoreporting ensurer
        and tries to turn the auto-reporting off
        """
        timeout_at = time() + 5
        instruction = enqueue_instruction(self.serial_queue, "M155 S0 C0")
        wait_for_instruction(instruction, lambda: time() < timeout_at)
        super().stop()

    def _reset_last_seen(self):
        """Resets the last seen time of all tracked values"""
        self.last_seen_positions = time()
        self.last_seen_fans = time()
        self.last_seen_temps = time()
