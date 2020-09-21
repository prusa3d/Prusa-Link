import logging
import os
import shutil
from threading import Thread
from time import sleep

from blinker import Signal

from prusa_link.default_settings import get_settings
from prusa_link.informers.telemetry_gatherer import TelemetryGatherer
from prusa_link.input_output.serial.helpers import enqueue_instruction, \
    wait_for_instruction
from prusa_link.input_output.serial.serial_queue import SerialQueue
from prusa_link.input_output.serial.serial_reader import SerialReader
from prusa_link.structures.model_classes import Telemetry
from prusa_link.structures.regular_expressions import POWER_PANIC_REGEX, \
    PRINTER_BOOT_REGEX
from prusa_link.util import get_clean_path, ensure_directory

LOG = get_settings().LOG
TIME = get_settings().TIME
PATH = get_settings().PATH

log = logging.getLogger(__name__)
log.setLevel(LOG.FILE_PRINTER)


class FilePrinter:

    def __init__(self, serial_queue: SerialQueue, serial_reader: SerialReader,
                 telemetry_gatherer: TelemetryGatherer):
        self.new_print_started_signal = Signal()
        self.print_ended_signal = Signal()

        self.tmp_file_path = get_clean_path(PATH.TMP_FILE)
        self.pp_file_path = get_clean_path(PATH.PP_FILE)
        ensure_directory(os.path.dirname(self.tmp_file_path))

        self.serial_queue = serial_queue
        self.serial_reader = serial_reader
        self.telemetry_gatherer = telemetry_gatherer

        self.serial_reader.add_handler(
            PRINTER_BOOT_REGEX, lambda sender, match: self.printer_reset())
        self.serial_reader.add_handler(
            POWER_PANIC_REGEX, lambda sender, match: self.power_panic())
        
        self.telemetry_gatherer.updated_signal.connect(self.telemetry_updated)

        self.printing = False
        self.paused = False
        self.line_number = 0
        
        self.target_nozzle_temp = 0
        self.target_bed_temp = 0

        self.thread = None

    def start(self):
        self.check_failed_print()

    @property
    def pp_exists(self):
        return os.path.exists(self.pp_file_path)

    @property
    def tmp_exists(self):
        return os.path.exists(self.pp_file_path)

    def check_failed_print(self):
        if self.tmp_exists and self.pp_exists:
            log.warning("There was a loss of power, let's try to recover")

            with open(self.pp_file_path, "r") as pp_file:
                parts = pp_file.read().split(" ")
                int_parts = map(lambda item: int(item), parts)
                line_number, bed_temp, nozzle_temp = int_parts
                line_index = line_number - 1
            """
            self.printing = True

            prep_gcodes = ["G28 XY"]

            if bed_temp != 0:
                prep_gcodes.append(f"M140 S{bed_temp}")
            if nozzle_temp != 0:
                prep_gcodes.append(f"M104 S{nozzle_temp}")
            if bed_temp != 0:
                prep_gcodes.append(f"M190 R{bed_temp}")
            if nozzle_temp != 0:
                prep_gcodes.append(f"M109 R{nozzle_temp}")

            for gcode in prep_gcodes:
                instruction = enqueue_instruction(self.serial, gcode,
                                                 front=True)
                wait_for_instruction(instruction, lambda: self.printing)

            self.thread = Thread(target=self._print, name="file_print",
                                 args=(line_index,))
            self.thread.start()
"""
            if self.pp_exists:
                os.remove(self.pp_file_path)

    def print(self, os_path):
        if self.printing:
            raise RuntimeError("Cannot print two things at once")

        shutil.copy(os_path, self.tmp_file_path)

        self.thread = Thread(target=self._print, name="file_print")
        self.printing = True
        self.new_print_started_signal.send(self)
        self.thread.start()

    def _print(self, from_line=0):
        tmp_file = open(self.tmp_file_path)

        # Reset the line counter, printing a new file
        self.serial_queue.reset_message_number()

        line_list = tmp_file.readlines()

        for line_index, line in enumerate(line_list[from_line:]):
            if self.paused:
                log.debug("Pausing USB print")
                self.wait_for_unpause()
                log.debug("Resuming USB print")

            self.line_number = line_index + 1
            gcode = line.split(";", 1)[0].strip()
            if gcode:
                log.debug(f"USB printing gcode: {gcode}")
                instruction = enqueue_instruction(self.serial_queue, gcode,
                                                  front=True, to_checksum=True)
                wait_for_instruction(instruction, lambda: self.printing)

                log.debug(f"{gcode} confirmed")

            if not self.printing:
                break

        log.debug(f"Print ended")

        os.remove(self.tmp_file_path)
        if self.pp_exists:
            os.remove(self.pp_file_path)
        self.printing = False
        self.print_ended_signal.send(self)

    def power_panic(self):
        if self.printing:
            self.paused = True
            log.warning("POWER PANIC!")
            self.serial_queue.queue.clear()
            with open(self.pp_file_path, "w") as pp_file:
                pp_file.write(f"{self.line_number} {self.target_bed_temp} "
                              f"{self.target_nozzle_temp}")
                pp_file.flush()
                os.fsync(pp_file.fileno())
            os.sync()

    def printer_reset(self):
        self.stop_print()

    def telemetry_updated(self, sender, telemetry: Telemetry):
        if telemetry.target_bed is not None:
            self.target_bed_temp = int(telemetry.target_bed)
        if telemetry.target_nozzle is not None:
            self.target_nozzle_temp = int(telemetry.target_nozzle)

    def wait_for_unpause(self):
        while self.printing and self.paused:
            sleep(TIME.QUIT_INTERVAL)

    def pause(self):
        self.paused = True

    def resume(self):
        self.paused = False

    def stop_print(self):
        if self.printing:
            self.printing = False
            self.thread.join()
            self.paused = False
