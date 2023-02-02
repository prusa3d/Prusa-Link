"""Contains implementation of the FilePrinter class"""
import logging
import os
from collections import deque
from time import sleep
from typing import Optional

from blinker import Signal  # type: ignore

from ..config import Config
from ..const import PRINT_QUEUE_SIZE, QUIT_INTERVAL, STATS_EVERY, TAIL_COMMANDS
from ..serial.helpers import enqueue_instruction, wait_for_instruction
from ..serial.instruction import Instruction
from ..serial.serial_parser import SerialParser
from ..serial.serial_queue import SerialQueue
from ..util import get_clean_path, get_gcode, get_print_stats_gcode, \
    prctl_name
from .model import Model
from .print_stats import PrintStats
from .structures.mc_singleton import MCSingleton
from .structures.module_data_classes import FilePrinterData
from .structures.regular_expressions import (CANCEL_REGEX, POWER_PANIC_REGEX,
                                             RESUMED_REGEX)
from .updatable import Thread

log = logging.getLogger(__name__)


class FilePrinter(metaclass=MCSingleton):
    """
    Facilitates serial printing, its pausing, resuming and stopping as well,
    controls print_stats, which provide info about progress and time left
    for gcodes without said info
    """

    # pylint: disable=too-many-arguments
    def __init__(self, serial_queue: SerialQueue, serial_parser: SerialParser,
                 model: Model, cfg: Config, print_stats: PrintStats) -> None:
        self.serial_queue = serial_queue
        self.serial_parser = serial_parser
        self.print_stats = print_stats
        self.model = model

        self.new_print_started_signal = Signal()
        self.print_stopped_signal = Signal()
        self.print_finished_signal = Signal()
        self.time_printing_signal = Signal()
        self.byte_position_signal = Signal()  # kwargs: current: int
        #                                               total: int
        self.layer_trigger_signal = Signal()

        self.model.file_printer = FilePrinterData(
            printing=False,
            paused=False,
            stopped_forcefully=False,
            file_path="",
            pp_file_path=get_clean_path(cfg.daemon.power_panic_file),
            enqueued=deque(),
            line_number=0,
            gcode_number=0)
        self.data = self.model.file_printer

        self.serial_parser.add_handler(
            POWER_PANIC_REGEX, lambda sender, match: self.power_panic())
        self.serial_parser.add_handler(CANCEL_REGEX,
                                       lambda sender, match: self.stop_print())
        self.serial_parser.add_handler(RESUMED_REGEX,
                                       lambda sender, match: self.resume())

        self.thread: Optional[Thread] = None

    def start(self) -> None:
        """Power panic is not yet implemented, sso this does nothing"""
        # self.check_failed_print()

    def stop(self) -> None:
        """Indicate to the printing thread to stop"""
        if self.data.printing:
            self.stop_print()

    def wait_stopped(self) -> None:
        """Wait for the printing thread to stop"""
        if self.thread is not None and self.thread.is_alive():
            self.thread.join()

    @property
    def pp_exists(self) -> bool:
        """Checks whether a file created on power panic exists"""
        return os.path.exists(self.data.pp_file_path)

    def check_failed_print(self) -> None:
        """Not implemented, would try to resume after power panic or error"""
        # log.warning("There was a loss of power, let's try to recover")
        if self.pp_exists:
            os.remove(self.data.pp_file_path)

    def print(self, os_path: str) -> None:
        """Starts a file print for the supplied path"""
        if self.data.printing:
            raise RuntimeError("Cannot print two things at once")

        self.data.file_path = os_path
        self.thread = Thread(target=self._print,
                             name="file_print",
                             daemon=True)
        self.data.printing = True
        self.data.stopped_forcefully = False
        self.print_stats.start_time_segment()
        self.new_print_started_signal.send(self)
        self.print_stats.track_new_print(self.data.file_path)
        self.thread.start()

    def _print(self, from_line=0):
        """
        Parses and sends the gcode commands from the file to serial.
        Supports pausing, resuming and stopping.
        """

        prctl_name()
        total_size = os.path.getsize(self.data.file_path)
        with open(self.data.file_path, "r", encoding='utf-8') as file:
            # Reset the line counter, printing a new file
            self.serial_queue.reset_message_number()

            self.data.gcode_number = 0
            self.data.enqueued.clear()
            line_index = 0
            while True:
                line = file.readline()

                # Recognise the end of the file
                if line == "":
                    break

                # This will make it PRINT_QUEUE_SIZE lines in front of what
                # is being sent to the printer, which is another as much as
                # 16 gcode commands in front of what's actually being printed.
                current_byte = file.tell()
                self.byte_position_signal.send(self,
                                               current=current_byte,
                                               total=total_size)

                if line_index < from_line:
                    continue

                if self.data.paused:
                    log.debug("Pausing USB print")
                    self.wait_for_unpause()

                    if not self.data.printing:
                        break

                    log.debug("Resuming USB print")

                # Trigger cameras on layer change
                if ";LAYER_CHANGE" in line:
                    self.layer_trigger_signal.send()

                self.data.line_number = line_index + 1
                gcode = get_gcode(line)
                if gcode:
                    self.print_gcode(gcode)
                    self.wait_for_queue()
                    self.react_to_gcode(gcode)

                line_index += 1

                if not self.data.printing:
                    break

            log.debug("Print ended")

            if self.pp_exists:
                os.remove(self.data.pp_file_path)
            self.data.printing = False
            self.data.enqueued.clear()

            if self.data.stopped_forcefully:
                self.serial_queue.flush_print_queue()
                self.data.enqueued.clear()  # Ensure this gets cleared
                # This results in double stop on 3.10 hopefully will get
                # changed
                # Prevents the print head from stopping in the print
                enqueue_instruction(self.serial_queue, "M603", to_front=True)
                self.print_stopped_signal.send(self)
            else:
                self.print_finished_signal.send(self)

    def print_gcode(self, gcode):
        """Sends a gcode to print, keeps a small buffer of gcodes
         and inlines print stats for files without them
        (estimated time left and progress)"""
        self.data.gcode_number += 1

        divisible = self.data.gcode_number % STATS_EVERY == 0
        if divisible:
            time_printing = self.print_stats.get_time_printing()
            self.time_printing_signal.send(self, time_printing=time_printing)

        if self.to_print_stats(self.data.gcode_number):
            self.send_print_stats()

        log.debug("USB enqueuing gcode: %s", gcode)
        instruction = enqueue_instruction(self.serial_queue,
                                          gcode,
                                          to_front=True,
                                          to_checksum=True)
        self.data.enqueued.append(instruction)

    def wait_for_queue(self) -> None:
        """Gets rid of already confirmed messages and waits for any
        unconfirmed surplus"""
        # Pop all already confirmed instructions from the queue
        while self.data.enqueued:  # ensure there is at least one item
            instruction = self.data.enqueued.popleft()
            if not instruction.is_confirmed():
                self.data.enqueued.appendleft(instruction)
                break
            log.debug("Throwing out trash %s", instruction.message)
        # If there are more than allowed and yet unconfirmed messages
        # Wait for the surplus ones
        while len(self.data.enqueued) >= PRINT_QUEUE_SIZE:
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
        # when doing this again don't forget to write print time
        if self.data.printing:
            self.pause()
            self.serial_queue.closed = True
            log.warning("POWER PANIC!")
            with open(self.data.pp_file_path, "w",
                      encoding='utf-8') as pp_file:
                pp_file.write(f"{self.data.line_number}")
                pp_file.flush()
                os.fsync(pp_file.fileno())

    def send_print_stats(self):
        """Sends a gcode to the printer, which tells it the progress
        percentage and estimated time left, the printer is expected to send
        back its standard print stats output for parsing in telemetry"""
        percent_done, time_remaining = self.print_stats.get_stats(
            self.data.gcode_number)

        # Idk what to do here, idk what would have happened if we used
        # the other mode, so let's report both modes the same
        stat_command = get_print_stats_gcode(percent_done, time_remaining,
                                             percent_done, time_remaining)
        instruction = enqueue_instruction(self.serial_queue,
                                          stat_command,
                                          to_front=True)
        self.data.enqueued.append(instruction)

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

    def wait_for_unpause(self):
        """
        Loops until some other thread flips a flag back, to resume the
        print
        """
        while self.data.paused:
            sleep(QUIT_INTERVAL)

    def pause(self):
        """Pauses the print by flipping a flag, pauses print timer"""
        if self.data.paused:
            return
        self.data.paused = True
        self.print_stats.end_time_segment()

    def resume(self):
        """
        If paused, resumes the print by flipping a flag,
        resumes print timer
        """
        if not self.data.printing:
            return
        if not self.data.paused:
            return
        self.data.paused = False
        self.print_stats.start_time_segment()

    def stop_print(self):
        """If printing, stops the print and indicates by a flag, that the
        print has been stopped and did not finish on its own"""
        if self.data.printing:
            self.data.stopped_forcefully = True
            self.data.printing = False
            self.data.paused = False
