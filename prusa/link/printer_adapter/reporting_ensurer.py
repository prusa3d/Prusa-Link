"""Contains implementation of the ReportingEnsurer class"""
from time import time

from .input_output.serial.serial_queue import SerialQueue
from .input_output.serial.serial_parser import SerialParser
from .input_output.serial.helpers import \
        enqueue_instruction, wait_for_instruction
from .const import REPORTING_TIMEOUT
from .structures.regular_expressions import \
        TEMPERATURE_REGEX, POSITION_REGEX, FAN_REGEX
from .updatable import ThreadedUpdatable


class ReportingEnsurer(ThreadedUpdatable):
    """
    Monitors autoreporting output, if any is missing, tries to turn
    autoreporting back on
    """
    thread_name = "temp_ensurer"
    update_interval = 10

    def __init__(self, serial_parser: SerialParser, serial_queue: SerialQueue):
        super().__init__()
        self.serial_parser = serial_parser
        self.serial_queue = serial_queue
        self.serial_parser.add_handler(
            TEMPERATURE_REGEX, lambda sender, match: self.temps_recorded())
        self.serial_parser.add_handler(
            POSITION_REGEX, lambda sender, match: self.positions_recorded())
        self.serial_parser.add_handler(
            FAN_REGEX, lambda sender, match: self.fans_recorded())

        self.last_seen_temps = time()
        self.last_seen_positions = time()
        self.last_seen_fans = time()

    def temps_recorded(self):
        """Resets the timeout for temperatures"""
        self.last_seen_temps = time()

    def positions_recorded(self):
        """Resets the timeout for positions"""
        self.last_seen_positions = time()

    def fans_recorded(self):
        """Resets the timeout for fans"""
        self.last_seen_fans = time()

    def update(self):
        """
        Calculates the time since the last time an autoreport came in
        for each monitored value.
        If any one of the is larger than REPORTING_TIMEOUT, calls
        turn_reporting_on()
        """
        since_last_temps = time() - self.last_seen_temps
        since_last_positions = time() - self.last_seen_positions
        since_last_fans = time() - self.last_seen_fans

        if since_last_positions > REPORTING_TIMEOUT:
            self.turn_reporting_on()
        if since_last_fans > REPORTING_TIMEOUT:
            self.turn_reporting_on()

        if since_last_temps > REPORTING_TIMEOUT:
            self.turn_reporting_on()

    def turn_reporting_on(self):
        """
        Tries to turn reporting on using the M155
        The C argument  is the bitmask for type of autoreporting
        The S argument is the frequency of autoreports
        """
        instruction = enqueue_instruction(self.serial_queue, "M155 S2 C7")
        wait_for_instruction(instruction, should_wait_evt=self.quit_evt)
        self.temps_recorded()
        self.positions_recorded()
        self.fans_recorded()

    def proper_stop(self):
        """
        Stops the autoreporting ensurer
        and tries to turn the auto-reporting off
        """
        timeout_at = time() + 5
        instruction = enqueue_instruction(self.serial_queue, "M155 S0 C0")
        wait_for_instruction(instruction, lambda: time() < timeout_at)
        super().stop()
