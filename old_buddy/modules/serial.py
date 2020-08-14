import logging
import re
from functools import partial
from threading import Thread, Event, Lock
from time import sleep
from typing import List

import serial
from blinker import Signal

from old_buddy.settings import SERIAL_LOG_LEVEL

log = logging.getLogger(__name__)
log.setLevel(SERIAL_LOG_LEVEL)

ANY_REGEX = re.compile(r".*")
CONFIRMATION_REGEX = re.compile(r"^ok\s?(.*)$")
RX_YEETED_REGEX = re.compile(r"^echo:Now fresh file: .*$")
PAUSED_REGEX = re.compile(r"^// action:paused$")
OK_REGEX = re.compile(r"^ok$")
RENEW_TIMEOUT_REGEX = re.compile(r"(^echo:busy: processing$)|"
                                 r"(^echo:busy: paused for user$)|"
                                 r"(^T:(\d+\.\d+) E:\d+ B:(\d+\.\d+)$)|"
                                 r"(^T:(\d+\.\d+) E:([?]|\d+) W:([?]|\d+)$)")

REJECTION_REGEX = re.compile("echo:Unknown command: (\"[^\"]*\")$")

# using M113 as sort of a ping,
# because there is a very low chance anyone else will use it
PING_REGEX = re.compile(r"^echo:M113 S\d+$")


class Serial:
    received = Signal()  # kwargs: line: str
    serial_timed_out = Signal()

    # Just checks if there is not more than one instance in existence,
    # but this is not a singleton!
    instance = None

    def __init__(self, port="/dev/ttyAMA0", baudrate=115200, timeout=1,
                 write_timeout=0, connection_write_delay=1,
                 default_timeout=None):
        assert self.instance is None, "If running more than one instance" \
                                      "is required, consider moving the " \
                                      "signals from class to instance " \
                                      "variables."
        self.instance = self

        self.default_timeout = default_timeout
        # Sometimes, we need silence except for one specific source
        # (writing files) With 0 as default, the writes without arguments
        # succeed, any other number, and only writes with the same
        # number don't get ignored
        self.channel = 0

        self.serial = serial.Serial(baudrate=baudrate, port=port,
                                    timeout=timeout,
                                    write_timeout=write_timeout)

        sleep(connection_write_delay)

        self.running = True
        self.read_thread = Thread(target=self._read_continually,
                                  name="serial_read_thread")
        self.read_thread.start()

        self.write_read_lock = Lock()

        # The big black dog keeps eating my handlers...
        self.__garbage_collector_safehouse = set()

    def _read_continually(self):
        """Ran in a thread, reads stuff over an over"""
        while self.running:
            try:
                line = self.serial.readline().decode("ASCII").strip()
            except serial.SerialException:
                log.error("Failed when reading from the printer. Ignoring")
            else:
                if line != "":
                    # with self.write_read_lock:
                    # Why would I not want to write and handle reads
                    # at the same time? IDK, but if something weird starts
                    # happening, i'll re-enablle this
                    log.debug(f"Printer says: '{line}'")
                    self.received.send(line=line)

    def write(self, message: bytes):
        """
        Writes a message

        :param message: the message to be sent
        """
        # with self.write_read_lock:
        # Why would i not want to write and handle reads at the same time?
        log.debug(f"Sending to printer: {message}")
        try:
            self.serial.write(message)
        except serial.SerialException:
            log.error(
                f"Serial error when sending '{message}' to the printer")

    def register_output_handler(self, regex: re.Pattern, handler, *args,
                                debug=False, **kwargs):
        """
        register an output handler for an arbitrary regex
        The regex will be searched each response from the printer

        :param regex: what to look for in the printer output
        :param handler: what to call, when a match is discovered
        :param debug: should we print regex matching (possibly lot of output)
        :param args: additional handler args
        :param kwargs: additional handler kwargs
        :return: the filter function that will determine if regex matched
        """
        handler_partial = partial(handler, *args, **kwargs)

        def read_filter(sender, line):
            match = regex.fullmatch(line)
            if match:
                handler_partial(match)
            elif debug:
                log.debug(f"No match on: '{line}' pattern: '{regex.pattern}'")

        self.__garbage_collector_safehouse.add(read_filter)
        self.received.connect(read_filter)
        return read_filter

    def stop(self):
        self.running = False
        self.serial.close()
        self.read_thread.join()


class OutputCollector:

    def __init__(self, begin_regex: re.Pattern,
                 end_regex: re.Pattern = OK_REGEX,
                 capture_regex: re.Pattern = ANY_REGEX, timeout: float = None,
                 debug: bool = False):
        self.begin_regex = begin_regex
        self.end_regex = end_regex
        self.capture_regex = capture_regex
        self.timeout = timeout
        self.debug = debug

        self.event: Event = Event()
        self.output: List[re.Match] = []

        Serial.received.connect(self.begin_capture)

    def begin_capture(self, sender, line):
        match = self.begin_regex.fullmatch(line)
        if match:
            Serial.received.disconnect(self.begin_capture)
            Serial.received.connect(self.capture)
            Serial.received.connect(self.end_capture)
        elif self.debug:
            log.debug(
                f"Message {line} did not match the begin capture regex "
                f"{self.begin_regex.pattern}")

    def capture(self, sender, line):
        match = self.capture_regex.fullmatch(line)
        if match:
            self.output.append(match)
        elif self.debug:
            log.debug(
                f"Message {line} did not match the capture regex "
                f"{self.capture_regex.pattern}")

    def end_capture(self, sender, line):
        match = self.end_regex.fullmatch(line)
        if match:
            Serial.received.disconnect(self.capture)
            Serial.received.disconnect(self.end_capture)

            # As blinker executes handlers at random and not in order in which
            # they subscribed, we don't know, whether the last line meaning
            # to end the capture was included in the output or not.
            # This solves that.
            # Or this: https://github.com/jek/blinker/pull/34 but that's
            # against "Blinker philosophy"
            if line == self.output[-1]:
                self.output = self.output[:-1]
                if self.debug:
                    log.debug(
                        f"The end message '{self.output[-1]}' "
                        f"was added to the captured output. Removing")

            if self.debug:
                log.debug(
                    f"Message {line} matched the end capture regex "
                    f"{self.end_regex.pattern}. Ending capture")

            self.event.set()

    def wait_for_output(self) -> List[re.Match]:
        success = self.event.wait(timeout=self.timeout)
        if not success:
            Serial.serial_timed_out.send()
            raise TimeoutError(
                f"Timed out waiting for output block, starting wih "
                f"'{self.begin_regex.pattern}' and ending with "
                f"'{self.end_regex.pattern}' pattern.")

        return self.output
