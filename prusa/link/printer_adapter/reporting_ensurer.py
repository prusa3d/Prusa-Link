from time import time

from prusa.link.printer_adapter.input_output.serial.serial_queue import \
    SerialQueue
from prusa.link.printer_adapter.input_output.serial.serial_reader import \
    SerialReader
from prusa.link.printer_adapter.input_output.serial.helpers import \
    enqueue_instruction, wait_for_instruction
from prusa.link.printer_adapter.const import REPORTING_TIMEOUT
from prusa.link.printer_adapter.structures.regular_expressions import \
    TEMPERATURE_REGEX, POSITION_REGEX, FAN_REGEX
from prusa.link.printer_adapter.updatable import ThreadedUpdatable


class ReportingEnsurer(ThreadedUpdatable):
    thread_name = "temp_ensurer"
    update_interval = 10

    def __init__(self, serial_reader: SerialReader, serial_queue: SerialQueue):
        super().__init__()
        self.serial_reader = serial_reader
        self.serial_queue = serial_queue
        self.serial_reader.add_handler(TEMPERATURE_REGEX, self.temps_recorded)
        self.serial_reader.add_handler(POSITION_REGEX, self.positions_recorded)
        self.serial_reader.add_handler(FAN_REGEX, self.fans_recorded)

        self.last_seen_temps = time()
        self.last_seen_positions = time()
        self.last_seen_fans = time()

        self.turn_reporting_on()

    def temps_recorded(self, sender=None, match=None):
        self.last_seen_temps = time()

    def positions_recorded(self, sender=None, match=None):
        self.last_seen_positions = time()

    def fans_recorded(self, sender=None, match=None):
        self.last_seen_fans = time()

    def update(self):
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
        instruction = enqueue_instruction(self.serial_queue, "M155 S2 C7")
        wait_for_instruction(instruction, lambda: self.running)
        self.temps_recorded()
        self.positions_recorded()
        self.fans_recorded()

    def stop(self):
        enqueue_instruction(self.serial_queue, "M155 S0 C0")
        super().stop()
