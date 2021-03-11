"""Contains implementation of the FilePrinter class"""
import logging
import os
import shutil
from collections import deque
from threading import Thread
from time import sleep

from blinker import Signal  # type: ignore

from ..config import Config
from .input_output.serial.instruction import \
    Instruction
from .input_output.serial.serial_queue import \
    SerialQueue
from .input_output.serial.serial_reader import \
    SerialReader
from .input_output.serial.helpers import \
    enqueue_instruction, wait_for_instruction
from .model import Model
from .print_stats import PrintStats
from .const import STATS_EVERY, \
    PRINT_QUEUE_SIZE, TAIL_COMMANDS, QUIT_INTERVAL
from .structures.mc_singleton import MCSingleton
from .structures.regular_expressions import \
    POWER_PANIC_REGEX, ERROR_REGEX, CANCEL_REGEX, \
    PAUSED_REGEX, RESUMED_REGEX
from .util import get_clean_path, ensure_directory, \
    get_gcode
from .updatable import prctl_name

log = logging.getLogger(__name__)


class FilePrinter(metaclass=MCSingleton):
    """
    Facilitates serial printing, its pausing, resuming and stopping as well,
    controls print_stats, which provide info about progress and time left
    for gcodes without said info
    """

    # pylint: disable=too-many-instance-attributes
    def __init__(self, serial_queue: SerialQueue, serial_reader: SerialReader,
                 model: Model, cfg: Config, print_stats: PrintStats):
        # pylint: disable=too-many-arguments
        self.serial_queue = serial_queue
        self.serial_reader = serial_reader
        self.print_stats = print_stats
        self.model = model

        self.new_print_started_signal = Signal()
        self.print_stopped_signal = Signal()
        self.print_finished_signal = Signal()
        self.time_printing_signal = Signal()
        self.byte_position_signal = Signal()  # kwargs: current: int
        #                                               total: int

        self.data = self.model.file_printer

        self.data.tmp_file_path = get_clean_path(cfg.daemon.current_file)
        self.data.pp_file_path = get_clean_path(cfg.daemon.power_panic_file)
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
        self.serial_reader.add_handler(CANCEL_REGEX,
                                       lambda sender, match: self.stop_print())
        self.serial_reader.add_handler(PAUSED_REGEX,
                                       lambda sender, match: self.pause())
        self.serial_reader.add_handler(RESUMED_REGEX,
                                       lambda sender, match: self.resume())

        self.thread = None

    def start(self):
        """Power panic is not yet implemented, sso this does nothing"""
        self.check_failed_print()

    @property
    def pp_exists(self):
        """Checks whether a file created on power panic exists"""
        return os.path.exists(self.data.pp_file_path)

    @property
    def tmp_exists(self):
        """Checks whether the print was stopped so abruptly we failed to clean
        our temporary gcode file copy"""
        return os.path.exists(self.data.tmp_file_path)

    def check_failed_print(self):
        """Not implemented, would try to resume after power panic or error"""
        if self.tmp_exists and self.pp_exists:
            log.warning("There was a loss of power, let's try to recover")
            """
            with open(self.data.pp_file_path, "r") as pp_file:
                content = pp_file.read()
                line_number = int(content)
                line_index = line_number - 1
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
        """Starts a file print for the supplied path"""
        if self.data.printing:
            raise RuntimeError("Cannot print two things at once")

        shutil.copy(os_path, self.data.tmp_file_path)

        self.thread = Thread(target=self._print, name="file_print")
        self.data.printing = True
        self.data.stopped_forcefully = False
        self.print_stats.start_time_segment()
        self.print_stats.track_new_print(self.data.tmp_file_path)
        self.new_print_started_signal.send(self)
        self.thread.start()

    def _print(self, from_line=0):
        """
        Parses and sends the gcode commands from the file to serial.
        Supports pausing, resuming and stopping.
        """

        prctl_name()
        total_size = os.path.getsize(self.data.tmp_file_path)
        with open(self.data.tmp_file_path, "r") as tmp_file:
            # Reset the line counter, printing a new file
            self.serial_queue.reset_message_number()

            self.data.gcode_number = 0
            self.data.enqueued.clear()
            line_index = 0
            while True:
                line = tmp_file.readline()
                if line == "":
                    break

                # This will make it PRINT_QUEUE_SIZE lines in front of what
                # is being sent to the printer, which is another as much as
                # 16 gcode commands in front of what's actually being printed.
                current_byte = tmp_file.tell()
                self.byte_position_signal.send(self,
                                               current=current_byte,
                                               total=total_size)

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
                    self.react_to_gcode(gcode)

                line_index += 1

                if not self.data.printing:
                    break

            log.debug("Print ended")

            os.remove(self.data.tmp_file_path)
            if self.pp_exists:
                os.remove(self.data.pp_file_path)
            self.data.printing = False
            self.data.enqueued.clear()

            if self.data.stopped_forcefully:
                self.print_stopped_signal.send(self)
            else:
                self.print_finished_signal.send(self)

    def print_gcode(self, gcode):
        """
        Sends a gcode to print, keeps a small buffer of gcodes
         and inlines print stats for files without them
        (estimated time left and progress)"""
        self.data.gcode_number += 1

        divisible = self.data.gcode_number % STATS_EVERY == 0
        if divisible:
            time_printing = self.print_stats.get_time_printing()
            self.time_printing_signal.send(time_printing=time_printing)

        if self.to_print_stats(self.data.gcode_number):
            self.send_print_stats()

        log.debug("USB enqueuing gcode: %s", gcode)
        instruction = enqueue_instruction(self.serial_queue,
                                          gcode,
                                          to_front=True,
                                          to_checksum=True)
        self.data.enqueued.append(instruction)
        if len(self.data.enqueued) >= PRINT_QUEUE_SIZE:
            wait_for: Instruction = self.data.enqueued.popleft()
            wait_for_instruction(wait_for, lambda: self.data.printing)

            log.debug("%s confirmed", wait_for.message)

    def react_to_gcode(self, gcode):
        """
        Some gcodes need to be reacted to right after they get enqueued
         in order to compensate for the file_printer gcode buffer

        For example M601 - Pause needs to pause the file read process
        as soon as it's sent
        :param gcode: gcode to react to
        """
        if gcode.startswith("M601") or gcode.startswith("M25"):
            self.pause()

    def power_panic(self):
        """Not used/working"""
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
        """Sends a gcode to the printer, which tells it the progress
        percentage and estimated time left, the printer is expected to send
        back its standard print stats output for parsing in telemetry"""
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
        """
        Decides whether to calculate and send print stats based on the
        file being printed having stats or not,v the gcode number
        divisibility, or just before the end of a file print
        """
        divisible = gcode_number % STATS_EVERY == 0
        do_stats = not self.model.print_stats.has_inbuilt_stats
        print_ending = (
            gcode_number == self.model.print_stats.total_gcode_count -
            TAIL_COMMANDS)
        return do_stats and (divisible or print_ending)

    def printer_error(self):
        """Reacts to a hard printer error by stopping the serial print"""
        self.stop_print()

    def wait_for_unpause(self):
        """
        Loops until some other thread flips a flag back, to resume the
        print
        """
        while self.data.printing and self.data.paused:
            sleep(QUIT_INTERVAL)

    def pause(self):
        """Pauses the print by flipping a flag, pauses print timer"""
        self.data.paused = True
        self.print_stats.end_time_segment()

    def resume(self):
        """
        If paused, resumes the print by flipping a flag,
        resumes print timer
        """
        if self.data.printing:
            self.data.paused = False
            self.print_stats.start_time_segment()

    def stop_print(self):
        """If printing, stops the print and indicates by a flag, that the
        print has been stopped and did not finish on its own"""
        if self.data.printing:
            self.data.stopped_forcefully = True
            self.data.printing = False
            self.serial_queue.flush_print_queue()
            self.data.enqueued.clear()  # Ensure this gets cleared
            self.thread.join()
            self.data.paused = False
