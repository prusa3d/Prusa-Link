import logging
import re
from collections import deque
from threading import Lock, Thread
from time import time, sleep
from typing import List, Optional

from blinker import Signal

from prusa.link.printer_adapter.input_output.serial.serial import Serial
from prusa.link.printer_adapter.structures.regular_expressions import \
    CONFIRMATION_REGEX, RESEND_REGEX, TEMPERATURE_REGEX, \
    BUSY_REGEX, ATTENTION_REGEX, HEATING_HOTEND_REGEX, HEATING_REGEX, \
    M110_REGEXP
from prusa.link.printer_adapter.util import run_slowly_die_fast
from .instruction import Instruction
from .is_planner_fed import IsPlannerFed
from .serial_reader import SerialReader
from prusa.link.printer_adapter.structures.mc_singleton import MCSingleton
from prusa.link.printer_adapter.const import PRINTER_BOOT_WAIT, QUIT_INTERVAL, \
    SERIAL_QUEUE_MONITOR_INTERVAL, SERIAL_QUEUE_TIMEOUT, RX_SIZE, HISTORY_LENGTH

log = logging.getLogger(__name__)


class BadChecksumUseError(Exception):
    ...


class SerialQueue(metaclass=MCSingleton):

    def __init__(self, serial: Serial, serial_reader: SerialReader,
                 rx_size=RX_SIZE):
        self.serial = serial
        self.serial_reader = serial_reader

        # When the serial_queue cannot re-establish communication with the
        # printer, let's signal this to other modules
        self.serial_queue_failed = Signal()

        # A queue of instructions for the printer
        self.queue: deque[Instruction] = deque()

        # This one shall contain time critical instructions
        self.priority_queue: deque[Instruction] = deque()

        # Instruction that is currently being handled
        self.current_instruction: Optional[Instruction] = None

        # Maximum bytes we'll write
        self.rx_max = rx_size

        # Make it possible to enqueue multiple consecutive instructions
        self.write_lock = Lock()

        # For numbered messages with checksums
        self.message_number = 0

        # When filament runs out or other buffer flushing calamity occurs
        # We need to re-send some commands that we already had dismissed as
        # confirmed
        self.send_history = deque(maxlen=HISTORY_LENGTH)

        # A list which will contain all messages needed to recover
        self.recovery_list = []
        self.rx_yeet_slot = None

        # For stopping fast (power panic)
        self.closed = False

        # Flag to be set when serial communication fails
        self.has_failed = False

        # Workaround around M110 involves syncing the FW buffers using a G4
        # Whenever an M110 comes, a G4 needs to be prepended.
        # To avoid getting stuck in an endless loop, let's flip a flag
        self.m110_workaround_slot = None
        self.worked_around_m110 = False

        self.serial_reader.add_handler(
            CONFIRMATION_REGEX, self._confirmation_handler,
            priority=float("inf"))
        self.serial_reader.add_handler(
            RESEND_REGEX, self._resend_handler)

        self.is_planner_fed = IsPlannerFed()

    def peek_next(self):
        """Look, what the next instruction is going to be"""
        if self.m110_workaround_slot is not None:
            return self.m110_workaround_slot
        elif self.rx_yeet_slot is not None:
            return self.rx_yeet_slot
        elif self.recovery_list:
            return self.recovery_list[-1]
        elif self.priority_queue:
            if self.is_planner_fed() and self.queue:
                return self.queue[-1]
            else:
                return self.priority_queue[-1]
        elif self.queue:
            return self.queue[-1]

    def next_instruction(self):
        """
        Get a fresh instruction into the self.current_instruction handling slot
        """

        if self.current_instruction is not None:
            raise RuntimeError("Cannot send a new instruction. "
                               "When the last one didn't finish processing.")
        if self.m110_workaround_slot is not None:
            self.current_instruction = self.m110_workaround_slot
            self.m110_workaround_slot = None
        elif self.rx_yeet_slot is not None:
            self.current_instruction = self.rx_yeet_slot
            self.rx_yeet_slot = None
        elif self.recovery_list:
            self.current_instruction = self.recovery_list.pop()
        elif self.priority_queue:
            if self.is_planner_fed() and self.queue:
                # Invalidate, so the unimportant queue doesn't go all at once
                self.is_planner_fed.is_fed = False
                log.debug("Allowing a non-important instruction through")
                self.current_instruction = self.queue.pop()
            else:
                self.current_instruction = self.priority_queue.pop()
        elif self.queue:
            self.current_instruction = self.queue.pop()

    # --- If statements in methods ---
    def can_write(self):
        return self.current_instruction is None and not self.is_empty() and \
               not self.closed

    def is_empty(self):
        return not self.queue and not self.priority_queue and \
               not self.recovery_list and self.rx_yeet_slot is None

    # --- Actual methods ---

    def _try_writing(self):
        with self.write_lock:
            if self.can_write():
                self._send()

    def get_data(self, instruction):
        data = instruction.message.encode("ASCII")
        if instruction.to_checksum:
            number_part = f"N{self.message_number} ".encode("ASCII")
            to_checksum = number_part + data + b" "
            checksum = self.get_checksum(to_checksum)
            checksum_data = f"*{checksum}".encode("ASCII")
            data = to_checksum + checksum_data
        data += b"\n"
        return data

    def get_checksum(self, data: bytes):
        checksum = 0
        for byte in data:
            checksum ^= byte
        return checksum

    def _hookup_output_capture(self):
        for regexp in self.current_instruction.capturing_regexps:
            self.serial_reader.add_handler(
                regexp,
                self.current_instruction.output_captured,
                priority=time()
            )

    def _teardown_output_capture(self):
        for regexp in self.current_instruction.capturing_regexps:
            self.serial_reader.remove_handler(
                regexp, self.current_instruction.output_captured
            )

    def _send(self):
        """
        Gets a new instruction and depending on what appears
        in the handling slot. Tries its best to send it
        :return:
        """
        next_instruction = self.peek_next()
        if M110_REGEXP.match(next_instruction.message) and \
                not self.worked_around_m110:
            self.m110_workaround_slot = Instruction("G4 S0.001")
            self.worked_around_m110 = True

        self.next_instruction()
        instruction = self.current_instruction

        if instruction.data is None:
            if instruction.to_checksum:
                self.send_history.append(instruction)
                self.message_number += 1
                if self.message_number == 1000000000:
                    self._reset_message_number()

            instruction.data = self.get_data(instruction)

        # If the instruction is M110 read the value it'll set and save it
        m110_match = M110_REGEXP.match(instruction.message)
        if m110_match:
            self.worked_around_m110 = False
            self.send_history.clear()
            log.debug("The message number is getting reset")
            number = m110_match.groups()[1]
            if number is not None:
                try:
                    self.message_number = int(number)
                except ValueError:
                    self.message_number = 0

        size = len(instruction.data)
        if size > self.rx_max:
            log.warning(f"The data {instruction.data.decode('ASCII')} "
                        f"we're trying to write is {size}B. But we can "
                        f"only send {self.rx_max}B max.")

        self._hookup_output_capture()
        self.current_instruction.sent()

        log.debug(f"{instruction.data.decode('ASCII')} sent")
        self.serial.write(self.current_instruction.data)

    def _enqueue(self, instruction: Instruction, to_front=False):
        if to_front:
            self.priority_queue.appendleft(instruction)
        else:
            self.queue.appendleft(instruction)

    def enqueue_one(self, instruction: Instruction, to_front=False):
        """
        Enqueue one instruction
        Don't interrupt, if anyone else is enqueueing instructions
        :param instruction: the thing to be enqueued
        :param to_front: whether to enqueue to front of the queue
        """

        with self.write_lock:
            log.debug(f"{instruction} enqueued. "
                      f"{'to the front' if to_front else ''}")

            self._enqueue(instruction, to_front)

        self._try_writing()

    def enqueue_list(self, instruction_list: List[Instruction], front=False):
        """
        Enqueue list of instructions
        Don't interrupt, if anyone else is enqueueing instructions
        :param instruction_list: the list to enqueue
        :param front: whether to enqueue to front of the queue
        """

        with self.write_lock:
            log.debug(f"Instructions {instruction_list} enqueued"
                      f"{'to the front' if front else ''}")

            for instruction in instruction_list:
                self._enqueue(instruction, front)

        self._try_writing()

    # --- Static capture handlers ---

    def _confirmation_handler(self, sender, match: re.Match):
        # There is a special case, M105 prints "ok" on the same line as
        # output So if there is anything after ok, try if it isn't the temps
        # and capture them if we are expecting them
        additional_output = match.groups()[0]
        if additional_output and \
                TEMPERATURE_REGEX in self.current_instruction.capturing_regexps:
            temperature_match = TEMPERATURE_REGEX.match(additional_output)
            if temperature_match:
                self.current_instruction.output_captured(
                    None, match=temperature_match)

        self._confirmed()

    def _paused_handler(self, sender, match: re.Match):
        # Another special case is when pausing. The "ok" is omitted
        # Let's confirm it ourselves
        self._confirmed()

    def _resend_handler(self, sender, match: re.Match):
        number = int(match.groups()[0])
        log.info(f"Resend of {number} requested. Current is "
                 f"{self.message_number}")
        if self.message_number >= number:
            if (self.current_instruction is None or
                    not self.current_instruction.to_checksum):
                log.warning("Re-send requested for on a non-numbered message")
                # If that happened, the non-numbered message got yeeted from the
                # buffer, so let's solve that first
                self._rx_got_yeeted()
            self._resend((self.message_number - number) + 1)
        else:
            log.warning("We haven't sent anything with that number yet. "
                        "The communication shouldn't fail after this.")

    # ---

    def _resend(self, count):
        if not 0 < count < len(self.send_history):
            log.error("Impossible re-send request! Aborting...")
            self._worst_case_scenario()
        else:
            with self.write_lock:
                # get the instructions newest first, they are going to reverse
                # in the list
                history = list(reversed(self.send_history))

                self.recovery_list.clear()
                for instruction_from_history in history[:count]:
                    instruction = Instruction(instruction_from_history.message,
                                              to_checksum=True,
                                              data=instruction_from_history.data)
                    self.recovery_list.append(instruction)

    def _confirmed(self, force=False):
        """
        Printer _confirmed an instruction.
        Assume it confirms exactly one instruction once
        """
        self.last_event_on = time()
        if self.current_instruction is None or \
                not self.current_instruction.is_sent():
            log.error("Unexpected message confirmation. Ignoring")
        elif self.current_instruction.confirm(force=force):
            with self.write_lock:
                instruction = self.current_instruction

                # If the instruction did not refuse to be confirmed
                # Yes, that needs to happen
                log.debug(f"{instruction} confirmed")

                self._teardown_output_capture()

                if instruction.to_checksum:
                    # Only check those times for check-summed instructions
                    self.is_planner_fed.process_value(
                        instruction.time_to_confirm)

                self.current_instruction = None
        else:
            log.debug(f"{self.current_instruction} refused confirmation. "
                      f"Hopefully it has a reason for that")

        self._try_writing()

    def _rx_got_yeeted(self):
        """
        Something caused the RX buffer to get thrown out, let's re-send
        everything supposed to be in it.
        """
        log.debug(f"Think that RX Buffer got yeeted, sending instruction again")
        # Let's bypass the check and write if we can.
        if self.current_instruction is not None:
            instruction = self.current_instruction
            # These two types have to be recovered in their own ways
            with self.write_lock:
                self.rx_yeet_slot = instruction
                self._teardown_output_capture()
                instruction.reset()
                self.current_instruction = None
                self._send()

    def reset_message_number(self):
        """
        Does not wait for the result, everything that gets enqueued after this
        will be executed after this. If this is no longer true, stuff will break
        """
        with self.write_lock:
            self._reset_message_number()

    def _reset_message_number(self):
        instruction = Instruction("M110 N0")
        self._enqueue(instruction, to_front=True)

    def _flush_queues(self):
        if self.current_instruction is not None:
            # To flush the one instruction, that has not yet been confirmed
            # but has been sent, use the usual way
            self.current_instruction.confirm(force=True)
            self._teardown_output_capture()
            self.current_instruction = None
            self.next_instruction()
        while self.current_instruction is not None:
            # obviously don't send the other ones,
            # so they can be handled faster
            self.current_instruction.sent()
            self.current_instruction.confirm(force=True)
            self.current_instruction = None
            self.next_instruction()

    def _worst_case_scenario(self):
        """
        Everything has failed, let's abandon whatever we were doing and save
        the printer/user
        """
        self.has_failed = True
        log.error("Communication failed. Aborting...")
        self.serial_queue_failed.send()

    def printer_reset(self, was_printing):
        Thread(target=self._printer_reset, args=(was_printing, ),
               name="serial_queue_reset_thread").start()

    def _printer_reset(self, was_printing):
        """Printer resets for two reasons, it has been stopped by the user,
        or the serial communication failed"""
        with self.write_lock:
            self._flush_queues()
            sleep(PRINTER_BOOT_WAIT)

            final_instruction = None

            if self.has_failed:
                beep_instruction = Instruction("M300 S880 P200")
                self._enqueue(beep_instruction, to_front=True)
                stop_instruction = Instruction("M603")
                self._enqueue(stop_instruction, to_front=True)
                message_instruction = Instruction("M1 FW COMM ERR. Aborted")
                self._enqueue(message_instruction, to_front=True)
                final_instruction = message_instruction
                self.has_failed = False
            elif was_printing:
                stop_instruction = Instruction("M603")
                self._enqueue(stop_instruction, to_front=True)
                final_instruction = stop_instruction

        if final_instruction is not None:
            self._try_writing()
            while not self.closed:
                if final_instruction.wait_for_confirmation(
                        timeout=QUIT_INTERVAL):
                    break



class MonitoredSerialQueue(SerialQueue):

    def __init__(self, serial: Serial, serial_reader: SerialReader,
                 rx_size=128):
        super().__init__(serial, serial_reader, rx_size)

        self.serial_reader.add_handler(BUSY_REGEX,
                                       self._renew_timeout)
        self.serial_reader.add_handler(ATTENTION_REGEX,
                                       self._renew_timeout)
        self.serial_reader.add_handler(HEATING_REGEX,
                                       self._renew_timeout)
        self.serial_reader.add_handler(HEATING_HOTEND_REGEX,
                                       self._renew_timeout)

        # Remember when the last write or confirmation happened
        # If we want to time out, the communication has to be dead for some time
        # Useful only with unbuffered messages
        self.running = True
        self.last_event_on = time()
        self.monitoring_thread = Thread(target=self.keep_monitoring,
                                        name="sq_stall_recovery")
        self.monitoring_thread.start()

    def get_current_delay(self):
        if self.is_empty() and self.current_instruction is None:
            return 0
        else:
            return time() - self.last_event_on

    def keep_monitoring(self):
        run_slowly_die_fast(lambda: self.running, QUIT_INTERVAL,
                            lambda: SERIAL_QUEUE_MONITOR_INTERVAL,
                            self.check_status)

    def check_status(self):
        if self.get_current_delay() > SERIAL_QUEUE_TIMEOUT:
            # The printer did not respond in time, lets assume it forgot
            # what it was supposed to do
            log.info(f"Timed out waiting for confirmation of "
                     f"{self.current_instruction} after "
                     f"{SERIAL_QUEUE_TIMEOUT}sec.")
            log.debug("Assuming the printer yeeted our RX buffer")
            self._rx_got_yeeted()

    def stop(self):
        self.running = False
        self.is_planner_fed.save()
        self.monitoring_thread.join()

    def _send(self):
        self.last_event_on = time()
        super()._send()

    def _confirmed(self, force=False):
        self.last_event_on = time()
        super()._confirmed(force=force)

    def _renew_timeout(self, sender, match: re.Match):
        self.last_event_on = time()
