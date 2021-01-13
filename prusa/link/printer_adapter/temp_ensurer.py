from time import time

from prusa.link.printer_adapter.input_output.serial.serial_queue import \
    SerialQueue
from prusa.link.printer_adapter.input_output.serial.serial_reader import \
    SerialReader
from prusa.link.printer_adapter.input_output.serial.helpers import \
    enqueue_instruction
from prusa.link.printer_adapter.structures.constants import TEMP_TIMEOUT
from prusa.link.printer_adapter.structures.regular_expressions import \
    TEMPERATURE_REGEX
from prusa.link.printer_adapter.updatable import ThreadedUpdatable


class TempEnsurer(ThreadedUpdatable):
    thread_name = "temp_ensurer"
    update_interval = 10

    def __init__(self, serial_reader: SerialReader, serial_queue: SerialQueue):
        self.serial_reader = serial_reader
        self.serial_queue = serial_queue
        self.serial_reader.add_handler(TEMPERATURE_REGEX,
                                       self.temperatures_recorded)

        self.last_seen = time()

        self.turn_reporting_on()

        super().__init__()

    def temperatures_recorded(self, sender=None, match=None):
        self.last_seen = time()

    def update(self):
        if time() - self.last_seen > TEMP_TIMEOUT:
            self.turn_reporting_on()

    def turn_reporting_on(self):
        enqueue_instruction(self.serial_queue, "M155 S1")
        self.temperatures_recorded()

    def stop(self):
        enqueue_instruction(self.serial_queue, "M155 S0")
        super().stop()
