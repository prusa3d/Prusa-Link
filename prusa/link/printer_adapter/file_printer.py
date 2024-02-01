"""Contains implementation of the FilePrinter class"""
import json
import logging
import os
from collections import deque
from threading import RLock
from time import sleep
from typing import Optional

from blinker import Signal  # type: ignore

from ..config import Config
from ..const import (
    HISTORY_LENGTH,
    PRINT_QUEUE_SIZE,
    QUIT_INTERVAL,
    STATS_EVERY,
    TAIL_COMMANDS,
)
from ..serial.helpers import enqueue_instruction, wait_for_instruction
from ..serial.instruction import Instruction
from ..serial.serial_parser import ThreadedSerialParser
from ..serial.serial_queue import SerialQueue
from ..util import get_clean_path, get_gcode, get_print_stats_gcode, prctl_name
from .model import Model
from .print_stats import PrintStats
from .structures.mc_singleton import MCSingleton
from .structures.model_classes import PPData
from .structures.module_data_classes import FilePrinterData
from .structures.regular_expressions import (
    CANCEL_REGEX,
    RESUMED_REGEX,
)
from .updatable import Thread

log = logging.getLogger(__name__)


class FilePrinter(metaclass=MCSingleton):
    """
    Facilitates serial printing, its pausing, resuming and stopping as well,
    controls print_stats, which provide info about progress and time left
    for gcodes without said info
    """

    # pylint: disable=too-many-arguments
    def __init__(self, serial_queue: SerialQueue,
                 serial_parser: ThreadedSerialParser, model: Model,
                 cfg: Config) -> None:
        self.print_stats = PrintStats(model)
        self.serial_queue = serial_queue
        self.serial_parser = serial_parser
        self.model = model

        self.new_print_started_signal = Signal()
        self.print_stopped_signal = Signal()
        self.print_finished_signal = Signal()
        self.time_printing_signal = Signal()
        self.byte_position_signal = Signal()  # kwargs: current: int
        #                                               total: int
        self.layer_trigger_signal = Signal()
        self.recovery_done_signal = Signal()

        self.lock = RLock()

        self.model.file_printer = FilePrinterData(
            printing=False,
            paused=False,
            recovering=False,
            was_stopped=False,
            power_panic=False,
            recovery_ready=False,
            file_path="",
            pp_file_path=get_clean_path(cfg.daemon.power_panic_file),
            enqueued=deque(),
            gcode_number=0)
        self.data = self.model.file_printer

        self.serial_parser.add_decoupled_handler(
            CANCEL_REGEX, lambda sender, match: self.stop_print())
        self.serial_parser.add_decoupled_handler(
            RESUMED_REGEX, lambda sender, match: self.resume())

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

    def print(self, os_path: str, from_gcode_number=None) -> None:
        """Starts a file print for the supplied path"""
        if self.data.printing:
            raise RuntimeError("Cannot print two things at once")

        if from_gcode_number is None and self.pp_exists:
            os.remove(self.data.pp_file_path)

        self.data.file_path = os_path
        self.thread = Thread(target=self._print,
                             name="file_print",
                             args=(from_gcode_number,),
                             daemon=True)
        self.data.printing = True
        self.data.recovering = from_gcode_number is not None
        self.data.was_stopped = False
        self.data.power_panic = False
        self.data.paused = False
        self.data.enqueued.clear()
        self.print_stats.start_time_segment()
        self.new_print_started_signal.send(self)
        self.print_stats.track_new_print(self.data.file_path,
                                         from_gcode_number)
        self.thread.start()

    def power_panic(self) -> None:
        """Handle the printer sending us a power panic  signal
        This means halt the serial print, do not send any more instructions
        Do not delete the power panic file"""
        self.data.power_panic = True
        self.data.printing = False
        log.warning("Power panic!")

    def _print(self, from_gcode_number=None):
        """
        Parses and sends the gcode commands from the file to serial.
        Supports pausing, resuming and stopping.

        param from_gcode_number:
            the gcode number to start from. Implies power panic recovery -
            goes into pause when the correct gcode number is reached
        """
        history_accumulator = []

        prctl_name()
        total_size = os.path.getsize(self.data.file_path)
        with open(self.data.file_path, "r", encoding='utf-8') as file:
            self.data.gcode_number = 0
            self.data.enqueued.clear()

            if not self.data.recovering:
                # Reset the line counter, printing a new file
                self.serial_queue.reset_message_number()
                self.do_instruction("M75")  # start printer's print timer

            while True:
                line = file.readline()

                # Recognise the end of the file
                if line == "" or not self.data.printing:
                    break

                gcode = get_gcode(line)
                # Skip to the part we need to recover from
                if (self.data.recovering
                        and from_gcode_number > self.data.gcode_number):
                    if gcode:
                        history_from = from_gcode_number - HISTORY_LENGTH
                        if self.data.gcode_number >= history_from:
                            history_accumulator.append(gcode)
                        self.data.gcode_number += 1
                    continue

                # Skip finished, pause here, remove the recovering flag
                if self.data.recovering:
                    history_accumulator.append(gcode)
                    self.serial_queue.replenish_history(history_accumulator)
                    self.pause()

                # This will make it PRINT_QUEUE_SIZE lines in front of what
                # is being sent to the printer, which is another as much as
                # 16 gcode commands in front of what's actually being printed.
                current_byte = file.tell()
                self.byte_position_signal.send(self,
                                               current=current_byte,
                                               total=total_size)

                if self.data.paused:
                    self._print_pause()
                    if not self.data.printing:
                        break

                # Trigger cameras on layer change
                if ";LAYER_CHANGE" in line:
                    self.layer_trigger_signal.send()

                if gcode:
                    self.print_gcode(gcode)
                    self.wait_for_queue()
                    self.react_to_gcode(gcode)

            # Print ended
            self._print_end()

    def _print_pause(self):
        """Handles the specific of a paused flie print"""
        log.debug("Pausing USB print")
        if self.data.recovering:
            self.data.recovery_ready = True
        else:
            # pause printer's print timer
            self.do_instruction("M76")
        while self.data.paused:
            sleep(QUIT_INTERVAL)

        if self.data.recovering:
            self.data.recovering = False
            self.data.recovery_ready = False
            self.recovery_done_signal.send()

        # If we ended the pause by a print stop, do not unpause the timer
        if self.data.printing:
            log.debug("Resuming USB print")
            self.do_instruction("M75")  # resume printer's print timer

    def _print_end(self):
        """Handles the end of a file print"""
        self.data.enqueued.clear()
        self.print_stats.reset_stats()
        log.debug("Print ended")

        if self.data.power_panic:
            return

        os.remove(self.data.pp_file_path)
        self.do_instruction("M77")  # stop printer's print timer

        self.data.printing = False

        if self.data.was_stopped:
            self.serial_queue.flush_print_queue()
            # Prevents the print head from stopping in the print
            enqueue_instruction(self.serial_queue, "M603", to_front=True)
            self.print_stopped_signal.send(self)
        else:
            self.print_finished_signal.send(self)

    def do_instruction(self, message):
        """Shorthand for enqueueing and waiting for an instruction
        Enqueues everything to front as commands have a higher priority"""
        instruction = enqueue_instruction(self.serial_queue,
                                          message,
                                          to_front=True)
        wait_for_instruction(instruction, lambda: self.data.printing)
        return instruction

    def print_gcode(self, gcode):
        """Sends a gcode to print, keeps a small buffer of gcodes
         and inlines print stats for files without them
        (estimated time left and progress)"""
        with self.lock:
            self.data.gcode_number += 1

            divisible = self.data.gcode_number % STATS_EVERY == 0
            if divisible:
                time_printing = int(self.print_stats.get_time_printing())
                self.time_printing_signal.send(
                    self, time_printing=time_printing)

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

    def send_print_stats(self):
        """Sends a gcode to the printer, which tells it the progress
        percentage and estimated time left, the printer is expected to send
        back its standard print stats output for parsing in telemetry"""
        percent_done, time_remaining = self.print_stats.get_stats(
            self.data.gcode_number)

        # Idk what to do here, idk what would have happened if we used
        # the other mode, so let's report both modes the same
        stat_command = get_print_stats_gcode(
            normal_percent=percent_done,
            normal_left=time_remaining,
            quiet_percent=percent_done,
            quiet_left=time_remaining)
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
        # TODO: wrong, needs to be in line with the rest of commands
        if not self.data.printing:
            return
        if not self.data.paused:
            return
        self.data.paused = False
        self.print_stats.start_time_segment()

    def stop_print(self):
        """If printing, stops the print and indicates by a flag, that the
        print has been stopped and did not finish on its own"""
        # TODO: wrong, needs to be in line with the rest of commands
        if self.data.printing:
            self.data.was_stopped = True
            self.data.printing = False
            self.data.paused = False

    def write_file_stats(self, file_path, message_number, gcode_number):
        """Writes the data needed for power panic recovery"""
        data = PPData(
            file_path=file_path,
            connect_path=self.model.job.selected_file_path,
            message_number=message_number,
            gcode_number=gcode_number,
            using_rip_port=self.model.serial_adapter.using_port.is_rpi_port,
        )
        with open(self.data.pp_file_path, "w", encoding="UTF-8") as pp_file:
            pp_file.write(json.dumps(data.dict()))
            os.fsync(pp_file)  # make sure this gets written to storage

    def serial_message_number_changed(self, message_number):
        """Updates the pairing of the FW message number to gcode line number

        If all the instructions in the buffer are sent
        The message number belongs to the next instruction
        that will be sent

        Here's an illustration of the situation
        _________________________________________
        |enqueued   |gcode_number|message_number|
        |           | current=25 | current=100  |
        |___________|____________|______________|
        |next instr.|     26     |     102      |
        | I0        |    *25*    |     101      |
        | I1        |     24     |    *100*     |
        | I2 (sent) |     23     |     99       |
        | I3 (sent) |     22     |     98       |
        |___________|____________|______________|
        """

        with self.lock:
            instruction_gcode_number = self.data.gcode_number + 1
            for instruction in self.data.enqueued:
                if instruction.is_sent():
                    break
                instruction_gcode_number -= 1
            self.write_file_stats(self.data.file_path, message_number,
                                  instruction_gcode_number)
