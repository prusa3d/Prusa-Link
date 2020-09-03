import logging
from threading import Lock, Thread
from time import time
from typing import List

from old_buddy.input_output.serial import Serial
from old_buddy.default_settings import get_settings
from old_buddy.structures.regular_expressions import CONFIRMATION_REGEX, \
    RX_YEETED_REGEX, PAUSED_REGEX, RENEW_TIMEOUT_REGEX
from old_buddy.util import run_slowly_die_fast
from .instruction import Instruction

LOG = get_settings().LOG
SQ = get_settings().SQ
TIME = get_settings().TIME


log = logging.getLogger(__name__)
log.setLevel(LOG.SERIAL_QUEUE_LOG_LEVEL)


class BadChecksumUseError(Exception):
    ...


class SerialQueue:

    # This thing could buffer messages, shame the printer is so stoooopid
    # No need to have that functionality around tho

    def __init__(self, serial: Serial, rx_size=SQ.RX_SIZE):
        self.serial = serial

        # A gueue of instructions for the printer
        self.queue: List[Instruction] = []

        # Maximum bytes we'll write
        self.rx_max = rx_size

        # Make it possible to enqueue multiple consecutive instructions
        self.write_lock = Lock()

        # For numbered messages with checksums
        self.message_number = 1

        Serial.received.connect(self._serial_read)

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
        return not self.is_empty()

    def is_empty(self):
        return not bool(self.queue)

    # --- Actual methods ---

    def _try_writing(self):
        if self.can_write():
            self._write()

    def get_front_bytes(self):
        data = self.front_instruction.message.encode("ASCII")
        if self.front_instruction.to_checksum:
            number_part = f"N{self.message_number} ".encode("ASCII")
            self.message_number += 1
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
        data = self.get_front_bytes()
        if len(data) > self.rx_max:
            raise RuntimeError("")
        log.debug(f"{data.decode('ASCII')} sent")
        self.front_instruction.sent()
        self.serial.write(data)

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

            if front and not self.is_empty():
                self.queue.insert(1, instruction)
            else:
                self.queue.append(instruction)

        if was_empty:
            self._try_writing()

    def enqueue_list(self, instructions: List[Instruction], front=False):
        """
        Enqueue list of instructions
        Don't interrupt, if anyone else is enqueueing instructions
        :param instructions: the list to enqueue
        :param front: whether to enqueue to front of the queue
        """

        with self.write_lock:
            was_empty = self.is_empty()
            log.debug(f"Instructions {instructions} enqueued"
                      f"{'to the front' if front else ''}")

            if front and not self.is_empty():
                self.queue = self.queue[0:1] + instructions + self.queue[1:]
            else:
                self.queue.extend(instructions)

        if was_empty:
            self._try_writing()

    def _serial_read(self, sender, line):
        """
        Something has been read, decide if it's a message confirmation, output,
        or both
        """
        # No instruction is waiting
        # Printer is not responding to anything we said...
        if self.front_instruction is None:
            return

        confirmation_match = CONFIRMATION_REGEX.fullmatch(line)
        yeeted_match = RX_YEETED_REGEX.fullmatch(line)
        paused_match = PAUSED_REGEX.fullmatch(line)

        if confirmation_match:
            # There is a special case, M105 prints "ok" on the same line as
            # output So if there is anything after ok, add it to the captured
            # output before confirming
            additional_output = confirmation_match.groups()[0]
            if additional_output:
                self._output_captured(additional_output)

            self._confirmed()
        elif paused_match:
            # Another special case is when pausing. The "ok" is omitted
            # Let's add it to the captured stuff and confirm it ourselves
            self._output_captured(line)
            self._confirmed()
        elif yeeted_match:
            self._rx_buffer_got_yeeted()
        else:  # no match, it's not a confirmation
            self._output_captured(line)

    def _confirmed(self):
        """
        Printer _confirmed an instruction.
        Assume it confirms exactly one instruction once
        """
        if self.is_empty() or not self.front_instruction.is_sent():
            log.error("Unexpected message confirmation. Ignoring")
        self.last_event_on = time()

        if self.front_instruction.confirm():
            # If the instruction did not refuse to be confirmed
            # Yes, that needs to happen because of M602
            log.debug(f"{self.front_instruction} confirmed")
            del self.queue[0]
        else:
            log.debug(f"{self.front_instruction} refused confirmation. "
                      f"Hopefully it has a reason for that")

        #  rx_current decreased, let's try if we'll fit into the rx buffer
        self._try_writing()

    def _output_captured(self, line):
        """
        Printer said something. It did that between confirming the previous
        and our instruction. Assume it's for us
        (It's not, but we can filter stuff we don't need with regexps later)
        """
        log.debug(f"Output {line} captured for {self.front_instruction}")
        self.front_instruction.output_captured(line)

    def _rx_buffer_got_yeeted(self):
        """
        Something caused the RX buffer to get thrown out, let's re-send
        everything supposed to be in it.
        """
        log.debug(f"Think that RX Buffer got yeeted, re-sending what should "
                  f"have been inside")

        self._try_writing()

    front_instruction = property(get_front_instruction)


class MonitoredSerialQueue(SerialQueue):

    def __init__(self, serial: Serial, rx_size=128):
        super().__init__(serial, rx_size)
        # Remember when the last write or confirmation happened
        # If we want to time out, the communication has to be dead for some time
        # Useful only with unbuffered messages
        self.running = True
        self.last_event_on = time()
        self.monitoring_thread = Thread(target=self.keep_monitoring)
        self.monitoring_thread.start()

    def keep_monitoring(self):
        run_slowly_die_fast(lambda: self.running, TIME.QUIT_INTERVAL,
                            SQ.SERIAL_QUEUE_MONITOR_INTERVAL,
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

    def _serial_read(self, sender, line):
        super()._serial_read(sender, line)
        renew_timeout_match = RENEW_TIMEOUT_REGEX.fullmatch(line)
        if renew_timeout_match:
            self.last_event_on = time()
