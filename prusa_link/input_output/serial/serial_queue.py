import logging
import re
from threading import Lock, Thread
from time import time
from typing import List

from prusa_link.input_output.serial.serial import Serial
from prusa_link.default_settings import get_settings
from prusa_link.structures.regular_expressions import CONFIRMATION_REGEX, \
    FILE_OPEN_REGEX, PAUSED_REGEX, RESEND_REGEX, \
    TEMPERATURE_REGEX, BUSY_REGEX, ATTENTION_REGEX, HEATING_HOTEND_REGEX, \
    HEATING_REGEX
from prusa_link.util import run_slowly_die_fast
from .instruction import Instruction
from .serial_reader import SerialReader

LOG = get_settings().LOG
SQ = get_settings().SQ
TIME = get_settings().TIME


log = logging.getLogger(__name__)
log.setLevel(LOG.SERIAL_QUEUE)


class BadChecksumUseError(Exception):
    ...


class SerialQueue:

    # This thing could buffer messages, shame the printer is so stoooopid
    # No need to have that functionality around tho

    def __init__(self, serial: Serial, serial_reader: SerialReader,
                 rx_size=SQ.RX_SIZE):
        self.serial = serial
        self.serial_reader = serial_reader

        # A gueue of instructions for the printer
        self.queue: List[Instruction] = []

        # Maximum bytes we'll write
        self.rx_max = rx_size

        # Make it possible to enqueue multiple consecutive instructions
        self.write_lock = Lock()

        # For numbered messages with checksums
        self.message_number = 0

        # When enqueuing instructions to front keep track of where to
        # enqueue next, so they aren't getting mixed
        self.insert_priority_at = 0

        self.closed = False

        self.serial_reader.add_handler(CONFIRMATION_REGEX,
                                       self._confirmation_handler,
                                       priority=float("inf"))
        self.serial_reader.add_handler(FILE_OPEN_REGEX,
                                       self._yeeted_handler)
        self.serial_reader.add_handler(PAUSED_REGEX,
                                       self._paused_handler)
        self.serial_reader.add_handler(RESEND_REGEX,
                                       self._resend_handler)

    # --- Getters ---

    def get_front_instruction(self):
        if self.queue:
            return self.queue[0]
        else:  # Not omitting this for readability
            return None

    def get_current_delay(self):
        if self.is_empty():
            return 0
        else:
            return time() - self.last_event_on

    # --- If statements in methods ---
    def can_write(self):
        return not self.is_empty() and not self.front_instruction.is_sent() \
               and not self.closed

    def is_empty(self):
        return not bool(self.queue)

    # --- Actual methods ---

    def _try_writing(self):
        with self.write_lock:
            if self.can_write():
                self._write()

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

    def _write(self):
        # message_number has been raced for, so let's not do that
        instruction = self.front_instruction

        if not instruction.sent():
            # Is this the first time we are sending this?
            if instruction.to_checksum:
                self.message_number += 1

            for regexp in instruction.capturing_regexps:
                self.serial_reader.add_handler(
                    regexp,
                    instruction.output_captured,
                    priority=time()
                )

            self.front_instruction.sent()

        data = self.get_data(instruction)

        size = len(data)
        if size > self.rx_max:
            raise RuntimeError(f"The data {data.decode('ASCII')} we're trying "
                               f"to write is {size}B. But we can only send "
                               f"{self.rx_max}B max.")

        log.debug(f"{data.decode('ASCII')} sent")

        self.serial.write(data)

    def _enqueue(self, instruction: Instruction, front=False):
        if front:
            self.queue.insert(self.insert_priority_at,
                              instruction)
            self.insert_priority_at += 1
        else:
            if self.is_empty():
                self.insert_priority_at = 1
            self.queue.append(instruction)

    def enqueue_one(self, instruction: Instruction, front=False):
        """
        Enqueue one instruction
        Don't interrupt, if anyone else is enqueueing instructions
        :param instruction: the thing to be enqueued
        :param front: whether to enqueue to front of the queue
        """

        with self.write_lock:
            was_empty = self.is_empty()
            log.debug(f"{instruction} enqueued. "
                      f"{'to the front' if front else ''}")

            self._enqueue(instruction, front)

        self._try_writing()

    def enqueue_list(self, instruction_list: List[Instruction], front=False):
        """
        Enqueue list of instructions
        Don't interrupt, if anyone else is enqueueing instructions
        :param instruction_list: the list to enqueue
        :param front: whether to enqueue to front of the queue
        """

        with self.write_lock:
            was_empty = self.is_empty()
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
                TEMPERATURE_REGEX in self.front_instruction.capturing_regexps:
            temperature_match = TEMPERATURE_REGEX.match(additional_output)
            if temperature_match:
                self.front_instruction.output_captured(None,
                                                       match=temperature_match)

        self._confirmed()

    def _paused_handler(self, sender, match: re.Match):
        # Another special case is when pausing. The "ok" is omitted
        # Let's add it to the captured stuff and confirm it ourselves
        # Yes, i force the match no matter what, it's a stupid special case
        self.front_instruction.output_captured(None, match=match)
        self._confirmed()

    def _yeeted_handler(self, sender, match: re.Match):
        self._rx_buffer_got_yeeted()

    def _resend_handler(self, sender, match: re.Match):
        number = match.groups()[0]
        log.warning(f"Resend of {number} requested. Current is "
                    f"{self.message_number - 1}")
        if (self.front_instruction.to_checksum and
                self.message_number - 1 == number):
            self._recover_front()
        else:
            log.error("Most likely the serial communication "
                      "will fall apart after this!")

    # ---

    def _confirmed(self):
        """
        Printer _confirmed an instruction.
        Assume it confirms exactly one instruction once
        """
        self.last_event_on = time()
        if self.is_empty() or not self.front_instruction.is_sent():
            log.error("Unexpected message confirmation. Ignoring")
        elif self.front_instruction.confirm():
            with self.write_lock:
                instruction = self.front_instruction

                # If the instruction did not refuse to be confirmed
                # Yes, that needs to happen because of M602
                log.debug(f"{instruction} confirmed")

                for regexp in instruction.capturing_regexps:
                    self.serial_reader.remove_handler(
                        regexp, instruction.output_captured
                    )

                del self.queue[0]

                if self.insert_priority_at > 1:
                    self.insert_priority_at -= 1
                if self.is_empty():
                    self.insert_priority_at = 0

        elif not self.front_instruction.is_sent():
            # Something thinks the instruction failed sending, re-send
            self._try_writing()
        else:
            log.debug(f"{self.front_instruction} refused confirmation. "
                      f"Hopefully it has a reason for that")

        #  rx_current decreased, let's try if we'll fit into the rx buffer
        self._try_writing()

    def _rx_buffer_got_yeeted(self):
        """
        Something caused the RX buffer to get thrown out, let's re-send
        everything supposed to be in it.
        """
        log.debug(f"Think that RX Buffer got yeeted, re-sending instruction")
        with self.write_lock:
            # Let's bypass the check and write if we can.
            if not self.is_empty():
                self._write()

    def _recover_front(self):
        # The message that failed gets confirmed
        # Let's stop that from happening as we need to re-send it
        self.front_instruction.needs_two_okays = True

        # The message errored out on send, so let's try it again
        self.front_instruction.sent_event.clear()

        # We'll be sending a message with the same number again
        self.message_number -= 1

    front_instruction = property(get_front_instruction)

    def reset_message_number(self):
        instruction = Instruction("M110 N1")
        self.enqueue_one(instruction, front=True)
        while not self.closed:
            if instruction.wait_for_confirmation(timeout=TIME.QUIT_INTERVAL):
                break
        self.message_number = 1
        

class MonitoredSerialQueue(SerialQueue):

    def __init__(self, serial: Serial, serial_reader: SerialReader,
                 rx_size=128):
        super().__init__(serial, serial_reader, rx_size)

        self.serial_reader.add_handler(BUSY_REGEX, self._renew_timeout)
        self.serial_reader.add_handler(ATTENTION_REGEX, self._renew_timeout)
        self.serial_reader.add_handler(HEATING_REGEX, self._renew_timeout)
        self.serial_reader.add_handler(HEATING_HOTEND_REGEX, self._renew_timeout)

        # Remember when the last write or confirmation happened
        # If we want to time out, the communication has to be dead for some time
        # Useful only with unbuffered messages
        self.running = True
        self.last_event_on = time()
        self.monitoring_thread = Thread(target=self.keep_monitoring)
        self.monitoring_thread.start()

    def keep_monitoring(self):
        run_slowly_die_fast(lambda: self.running, TIME.QUIT_INTERVAL,
                            lambda: SQ.SERIAL_QUEUE_MONITOR_INTERVAL,
                            self.check_status)

    def check_status(self):
        if self.get_current_delay() > SQ.SERIAL_QUEUE_TIMEOUT:
            # The printer did not respond in time, lets assume it forgot
            # what it was supposed to do
            log.info(f"Timed out waiting for confirmation of "
                     f"{self.front_instruction} after "
                     f"{SQ.SERIAL_QUEUE_TIMEOUT}sec.")
            log.debug("Assuming the printer yote our RX buffer")
            self._rx_buffer_got_yeeted()

    def stop(self):
        self.running = False
        self.monitoring_thread.join()

    def _write(self):
        self.last_event_on = time()
        super()._write()

    def _confirmed(self):
        self.last_event_on = time()
        super()._confirmed()

    def _renew_timeout(self, sender, match: re.Match):
        self.last_event_on = time()
