import logging
import os
import shutil
from collections import deque
from threading import Thread
from time import sleep

from blinker import Signal

from prusa.link.printer_adapter.default_settings import get_settings
from prusa.link.printer_adapter.input_output.serial.instruction import \
    Instruction
from prusa.link.printer_adapter.input_output.serial.serial_queue import \
    SerialQueue
from prusa.link.printer_adapter.input_output.serial.serial_reader import \
    SerialReader
from prusa.link.printer_adapter.input_output.serial.helpers import \
    enqueue_instruction, wait_for_instruction
from prusa.link.printer_adapter.model import Model
from prusa.link.printer_adapter.print_stats import PrintStats
from prusa.link.printer_adapter.const import STATS_EVERY, \
    PRINT_QUEUE_SIZE, TAIL_COMMANDS, QUIT_INTERVAL
from prusa.link.printer_adapter.structures.mc_singleton import MCSingleton
from prusa.link.printer_adapter.structures.regular_expressions import \
    POWER_PANIC_REGEX, ERROR_REGEX, RESUMED_REGEX
from prusa.link.printer_adapter.util import get_clean_path, ensure_directory, \
    get_gcode

PATH = get_settings().PATH

log = logging.getLogger(__name__)


class FilePrinter(metaclass=MCSingleton):

    def __init__(self, serial_queue: SerialQueue, serial_reader: SerialReader,
                 model: Model):
        self.serial_queue = serial_queue
        self.serial_reader = serial_reader
        self.model = model

        self.print_stats = PrintStats()

        self.new_print_started_signal = Signal()
        self.print_ended_signal = Signal()
        self.time_printing_signal = Signal()

        self.data = self.model.file_printer

        self.data.tmp_file_path = get_clean_path(PATH.TMP_FILE)
        self.data.pp_file_path = get_clean_path(PATH.PP_FILE)
        ensure_directory(os.path.dirname(self.data.tmp_file_path))

        self.data.printing = False
        self.data.paused = False

        self.data.line_number = 0
        self.data.gcode_number = 0

        self.data.enqueued = deque()

        self.serial_queue.serial_queue_failed.connect(
            lambda sender: self.stop_print())

        self.serial_reader.add_handler(
            POWER_PANIC_REGEX, lambda sender, match: self.power_panic())
        self.serial_reader.add_handler(
            ERROR_REGEX, lambda sender, match: self.printer_error())
        self.serial_reader.add_handler(
            RESUMED_REGEX, lambda sender, match: self.resume())

        self.thread = None

    def start(self):
        self.check_failed_print()

    @property
    def pp_exists(self):
        return os.path.exists(self.data.pp_file_path)

    @property
    def tmp_exists(self):
        return os.path.exists(self.data.tmp_file_path)

    def check_failed_print(self):
        if self.tmp_exists and self.pp_exists:
            log.warning("There was a loss of power, let's try to recover")

            with open(self.data.pp_file_path, "r") as pp_file:
                content = pp_file.read()
                line_number = int(content)
                line_index = line_number - 1
            """
            self.data.printing = True

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
                wait_for_instruction(instruction, lambda: self.data.printing)

            self.thread = Thread(target=self._print, name="file_print",
                                 args=(line_index,))
            self.thread.start()
"""
            if self.pp_exists:
                os.remove(self.data.pp_file_path)

    def print(self, os_path):
        if self.data.printing:
            raise RuntimeError("Cannot print two things at once")

        shutil.copy(os_path, self.data.tmp_file_path)

        self.thread = Thread(target=self._print, name="file_print")
        self.data.printing = True
        self.print_stats.start_time_segment()
        self.new_print_started_signal.send(self)
        self.thread.start()

    def _print(self, from_line=0):
        self.print_stats.track_new_print(self.data.tmp_file_path)

        with open(self.data.tmp_file_path, "r") as tmp_file:

            # Reset the line counter, printing a new file
            self.serial_queue.reset_message_number()

            self.data.gcode_number = 0
            for line_index, line in enumerate(tmp_file):
                if line_index < from_line:
                    continue

                if self.data.paused:
                    log.debug("Pausing USB print")
                    self.wait_for_unpause()
                    log.debug("Resuming USB print")

                self.data.line_number = line_index + 1
                gcode = get_gcode(line)
                if gcode:
                    self.print_gcode(gcode)

                if not self.data.printing:
                    break

            log.debug(f"Print ended")

            os.remove(self.data.tmp_file_path)
            if self.pp_exists:
                os.remove(self.data.pp_file_path)
            self.data.printing = False
            self.print_ended_signal.send(self)

    def print_gcode(self, gcode):
        self.data.gcode_number += 1

        divisible = self.data.gcode_number % STATS_EVERY == 0
        if divisible:
            time_printing = self.print_stats.get_time_printing()
            self.time_printing_signal.send(time_printing=time_printing)

        if self.to_print_stats(self.data.gcode_number):
            self.send_print_stats()

        log.debug(f"USB enqueuing gcode: {gcode}")
        instruction = enqueue_instruction(self.serial_queue, gcode,
                                          to_front=True,
                                          to_checksum=True)
        self.data.enqueued.append(instruction)
        if len(self.data.enqueued) >= PRINT_QUEUE_SIZE:
            wait_for: Instruction = self.data.enqueued.popleft()
            wait_for_instruction(wait_for, lambda: self.data.printing)

            log.debug(f"{wait_for.message} confirmed")

    def power_panic(self):
        # TODO: write print time
        if self.data.printing:
            self.pause()
            self.serial_queue.closed = True
            log.warning("POWER PANIC!")
            with open(self.data.pp_file_path, "w") as pp_file:
                pp_file.write(f"{self.data.line_number}")
                pp_file.flush()
                os.fsync(pp_file.fileno())

    def send_print_stats(self):
        percent_done, time_remaining = self.print_stats.get_stats(
            self.data.gcode_number)

        # TODO: Idk what to do here, idk what would have happened if we used
        #  the other mode, so let's report both modes the same
        stat_command = f"M73 P{percent_done} R{time_remaining} " \
                       f"Q{percent_done} S{time_remaining} "
        instruction = enqueue_instruction(self.serial_queue,
                                          stat_command,
                                          to_front=True)
        wait_for_instruction(instruction, lambda: self.data.printing)

    def to_print_stats(self, gcode_number):
        divisible = gcode_number % STATS_EVERY == 0
        do_stats = not self.print_stats.has_inbuilt_stats
        print_ending = (gcode_number ==
                        self.print_stats.total_gcode_count - TAIL_COMMANDS)
        return (do_stats and divisible) or print_ending

    def printer_error(self):
        # TODO: Maybe pause in some cases instead
        self.stop_print()

    def wait_for_unpause(self):
        while self.data.printing and self.data.paused:
            sleep(QUIT_INTERVAL)

    def pause(self):
        self.data.paused = True
        self.print_stats.end_time_segment()

    def resume(self):
        if self.data.printing:
            self.data.paused = False
            self.print_stats.start_time_segment()

    def stop_print(self):
        if self.data.printing:
            self.data.printing = False
            self.thread.join()
            self.data.paused = False
