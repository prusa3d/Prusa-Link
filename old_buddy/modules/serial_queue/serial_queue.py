import logging
from threading import Lock, Thread
from time import time
from typing import List

from .instruction import Instruction
from ..regular_expressions import CONFIRMATION_REGEX, RX_YEETED_REGEX, \
    PAUSED_REGEX, RENEW_TIMEOUT_REGEX
from ..serial import Serial
from ...settings import SERIAL_QUEUE_LOG_LEVEL, SERIAL_QUEUE_TIMEOUT, \
    QUIT_INTERVAL, SERIAL_QUEUE_MONITOR_INTERVAL, RX_SIZE
from ...util import run_slowly_die_fast

RX_SAFETY_MARGIN = 16
MAX_ONE_INSTRUCTION = True

log = logging.getLogger(__name__)
log.setLevel(SERIAL_QUEUE_LOG_LEVEL)


class SerialQueue:

    def __init__(self, serial: Serial, rx_size=RX_SIZE):
        self.serial = serial

        # A gueue of instructions for the printer
        self.queue: List[Instruction] = []

        # Maximum bytes we'll write
        self.rx_max = rx_size - RX_SAFETY_MARGIN

        # Bytes currently believed to be in the buffer
        self.rx_current = 0

        # Queue index of an item, that has yet to be written
        self.next_instruction_index = 0

        # Make it possible to enqueue multiple consecutive instructions
        self.write_lock = Lock()

        Serial.received.connect(self._serial_read)

    # --- Getters ---

    def get_front_instruction(self):
        if self.queue:
            return self.queue[0]
        else:  # Not omitting this for readability
            return None

    def get_next_instruction(self):
        if self.next_instruction_exists():
            return self.queue[self.next_instruction_index]
        else:  # Not omitting this for readability
            return None

    def get_current_delay(self):
        if self.is_empty():
            return 0
        else:
            return time() - self.last_event_on

    # --- If statements in methods ---

    def is_rx_full(self):
        return self.next_instruction_exists()

    def next_instruction_exists(self):
        return self.next_instruction_index <= len(self.queue) - 1

    def next_instruction_is_last(self):
        return self.next_instruction_index == len(self.queue) - 1

    def can_write(self):
        instruction_exists = self.next_instruction_exists()
        fits = False
        if instruction_exists and self.fits_on_rx(self.next_instruction):
            fits = True

        # denies a write if buffering is disabled and the write
        # would cause more than one instruction to be present in the RX buffer
        denied = False
        if MAX_ONE_INSTRUCTION and self.next_instruction_index != 0:
            denied = True
        return instruction_exists and fits and not denied

    def fits_on_rx(self, instruction):
        return self.rx_current + instruction.size < self.rx_max

    def is_empty(self):
        return not bool(self.queue)

    # --- Actual methods ---

    def _try_writing(self):
        while self.can_write():
            self._write()

    def _write(self):
            log.debug(f"{self.next_instruction} sent")
            self.next_instruction.sent()
            self.serial.write(self.next_instruction.data)
            self.rx_current += self.next_instruction.size
            self.next_instruction_index += 1

    def _enqueue(self, instruction: Instruction):
        log.debug(f"{instruction} enqueued")
        self.queue.append(instruction)

        # if the item just added has the index to get written
        if self.next_instruction_is_last():
            self._try_writing()

    def enqueue_one(self, instruction: Instruction):
        """
        Enqueue one instruction
        Don't interrupt, if anyone else is enqueueing instructions
        """
        with self.write_lock:
            self._enqueue(instruction)

    def enqueue_list(self, instructions: List[Instruction]):
        """
        Enqueue list of instructions
        Don't interrupt, if anyone else is enqueueing instructions
        """
        with self.write_lock:
            for instruction in instructions:
                self._enqueue(instruction)

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
            # If the instruction did ot refuse to be confirmed
            # Yes, that needs to happen because of M602
            log.debug(f"{self.front_instruction} confirmed")
            self.rx_current -= self.front_instruction.size
            del self.queue[0]
            self.next_instruction_index -= 1
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
        self.rx_current = 0
        self.next_instruction_index = 0

        self._try_writing()

    front_instruction = property(get_front_instruction)
    next_instruction = property(get_next_instruction)


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
        run_slowly_die_fast(lambda: self.running, QUIT_INTERVAL,
                            SERIAL_QUEUE_MONITOR_INTERVAL, self.check_status)

    def check_status(self):
        if self.get_current_delay() > SERIAL_QUEUE_TIMEOUT:
            # The printer did not respond in time, lets assume it forgot
            # what it was supposed to do
            log.info(f"Timed out waiting for confirmation of "
                     f"{self.front_instruction} after "
                     f"{SERIAL_QUEUE_TIMEOUT}sec.")
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
