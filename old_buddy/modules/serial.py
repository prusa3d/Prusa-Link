import logging
import re
from functools import partial
from threading import Thread, Event, Lock
from time import sleep
from typing import Union, Callable, List

import serial
from blinker import Signal

from old_buddy.settings import SERIAL_LOG_LEVEL

log = logging.getLogger(__name__)
log.setLevel(SERIAL_LOG_LEVEL)

OK_REGEX = re.compile(r"^ok$")
ANY_REGEX = re.compile(r".*")
REACTION_REGEX = re.compile("^( ?ok ?)|(echo:Unknown command: (\"[^\"]*\"))$")

# using M113 as sort of a ping, because there is a very low chance anyone else will use it
PING_REGEX = re.compile(r"^echo:M113 S\d+$")


class UnknownCommandException(ValueError):
    def __init__(self, *args, command):
        super().__init__(*args)
        self.command = command

class WriteIgnored(Exception):
    ...


class Serial:

    received = Signal()  # kwargs: line: str
    serial_timed_out = Signal()

    instance = None  # Just checks if there is not more than one instance in existence, not a singleton!

    def __init__(self, port="/dev/ttyAMA0", baudrate=115200, timeout=1, write_timeout=0, connection_write_delay=1,
                 default_response_timeout=None):
        if self.instance is not None:
            raise AssertionError("If this is required, we need the signals moved from class to instance variables.")

        self.instance = self

        self.default_response_timeout = default_response_timeout
        # Sometimes, we need silence except for one specific source (writing files)
        # With 0 as default, the writes without arguments succeed, any other number, and only writes with the same
        # number don't get ignored
        self.channel = 0

        self.serial = serial.Serial(baudrate=baudrate, port=port, timeout=timeout, write_timeout=write_timeout)

        sleep(connection_write_delay)

        self.running = True
        self.read_thread = Thread(target=self._read_continually, name="serial_read_thread")
        self.read_thread.start()

        self.write_read_lock = Lock()

        self.__garbage_collector_safehouse = set()  # The big black dog keeps eating my handlers...

    def _read_continually(self):
        """Ran in a thread, reads stuff over an over"""
        while self.running:
            try:
                line = self.serial.readline().decode("ASCII").strip()
            except serial.SerialException:
                log.error("Failed when reading from the printer. Ignoring")
            else:
                if line != "":
                    self.write_read_lock.acquire()
                    log.debug(f"Printer says: '{line}'")
                    self.received.send(line=line)
                    self.write_read_lock.release()

    def write(self, message: str, channel: int = 0):
        """
        Writes a message

        :param message: the message to be sent
        :param channel: the channel to write to, if this does not match the current channel, the message is ignored
        """
        if self.channel != channel:
            log.info(f"write '{message}' ignored because the message came from chhannel {channel} "
                     f"but now we only pass messages from channel number {self.channel}.")
            raise WriteIgnored()

        if message[-1] != "\n":
            message += "\n"
        message_bytes = message.encode("ASCII")

        with self.write_read_lock:
            log.debug(f"Sending to printer: {message_bytes}")
            try:
                self.serial.write(message_bytes)
            except serial.SerialException:
                log.error(f"Serial error when sending '{message}' to the printer")

    def write_wait_response(self, message: str, wait_for_regex: re.Pattern = None, timeout: float = None,
                            channel: int = 0) -> Union[None, re.Match]:
        """
        Writes a message and waits for output matching the specified regex

        :param message: the message to be sent
        :param wait_for_regex: regex pattern to wait for
        :param timeout: time in seconds to wait before giving up
        :param channel: the channel to write to, if this does not match the current channel, the message is ignored
        :return: the match object if any pattern was given. Otherwise None
        """

        if timeout is None and self.default_response_timeout is not None:
            timeout = self.default_response_timeout

        response_waiter = SingleMatchCollector(wait_for_regex, timeout=timeout)

        self.write(message, channel=channel)

        try:
            return response_waiter.wait_for_output()
        except TimeoutError:
            log.debug(f"Timed out waiting for regex {wait_for_regex}")
            raise

    def write_wait_ok(self, message: str, timeout: float = None):

        match = self.write_wait_response(message, REACTION_REGEX, timeout=timeout)
        groups = match.groups()
        if groups[1]:
            command = groups[2]
            raise UnknownCommandException(f"Unknown command {command}", command=message)

    def register_output_handler(self, regex: re.Pattern, handler, *args, debug=False, **kwargs):
        """
        register an output handler for an arbitrary regex
        The regex will be searched each response from the printer

        :param regex: what to look for in the printer output
        :param handler: what to call, when a match is discovered
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

    def is_responsive(self):
        """Check if printer is really ready to respond"""
        try:
            self.write_wait_response("M113", PING_REGEX)
        except (TimeoutError, WriteIgnored):
            return False
        else:
            return True

    def stop(self):
        self.running = False
        self.serial.close()
        self.read_thread.join()


class SingleMatchCollector:
    def __init__(self, regex: re.Pattern, timeout: float = None, debug: bool = False):
        """
        When expecting a response on some command, ensure the response won't come earlier
        than you starting to wait for it.

        Starts listening on instantiation. Call wait_for_output to get your collected data or to wait for it

        :param regex: what to look for in the printer output
        :param timeout: how long to wait
        :param debug: print debug messages?
        :return: The regex match object
        """

        self.regex = regex
        self.timeout = timeout
        self.debug = debug

        self.event = Event()
        self.match = None

        Serial.received.connect(self.handler)

    def handler(self, sender, line):
        match = self.regex.fullmatch(line)
        if match:
            self.match = match
            self.event.set()
            # signal disconnecting is moved into waits as it allows for more types of waits otherwise impossible
        elif self.debug:
            log.debug(f"Message {line} did not match {self.regex.pattern}")

    def wait_for_output(self):
        success = self.event.wait(timeout=self.timeout)
        Serial.received.disconnect(self.handler)
        if not success:
            Serial.serial_timed_out.send()
            raise TimeoutError(f"Timed out waiting for match with regex '{self.regex.pattern}'")

        return self.match

    def wait_until(self, should_keep_trying: Callable[[], bool]):
        while should_keep_trying():
            success = self.event.wait(timeout=self.timeout)
            if not success:
                self.event.clear()
            else:
                Serial.received.disconnect(self.handler)
                return self.match

        Serial.received.disconnect(self.handler)

        Serial.serial_timed_out.send()
        raise TimeoutError(f"Timed out waiting for match with regex '{self.regex.pattern}'")


class OutputCollector:

    def __init__(self, begin_regex: re.Pattern, end_regex: re.Pattern = OK_REGEX,
                 capture_regex: re.Pattern = ANY_REGEX, timeout: float = None, debug: bool = False):
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
            log.debug(f"Message {line} did not match the begin capture regex {self.begin_regex.pattern}")

    def capture(self, sender, line):
        match = self.capture_regex.fullmatch(line)
        if match:
            self.output.append(match)
        elif self.debug:
            log.debug(f"Message {line} did not match the capture regex {self.capture_regex.pattern}")

    def end_capture(self, sender, line):
        match = self.end_regex.fullmatch(line)
        if match:
            Serial.received.disconnect(self.capture)
            Serial.received.disconnect(self.end_capture)

            # As blinker executes handlers at random and not in order in which they subscribed, we don't know,
            # whether the last line meaning to end the capture was included in the output or not. This solves that.
            # Or this: https://github.com/jek/blinker/pull/34 but that's against "Blinker philosophy"
            if line == self.output[-1]:
                self.output = self.output[:-1]
                if self.debug:
                    log.debug(f"The end message '{self.output[-1]}' was added to the captured output. Removing")

            if self.debug:
                log.debug(f"Message {line} matched the end capture regex {self.end_regex.pattern}. Ending capture")

            self.event.set()

    def wait_for_output(self) -> List[re.Match]:
        success = self.event.wait(timeout=self.timeout)
        if not success:
            Serial.serial_timed_out.send()
            raise TimeoutError(f"Timed out waiting for output block, starting wih '{self.begin_regex.pattern}' "
                               f"and ending with '{self.end_regex.pattern}' pattern.")

        return self.output





