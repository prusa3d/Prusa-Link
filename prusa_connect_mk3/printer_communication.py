import logging
import re
from functools import partial
from threading import Thread, Event, Lock
from time import sleep, time
from typing import Union

import serial
from blinker import Signal

log = logging.getLogger(__name__)

OK_PATTERN = re.compile("^ok\n$")


class Signals:

    def __init__(self):
        self.received = Signal()  # kwargs: line: str


class OutputCollector:
    def __init__(self, regex: re.Pattern, received_signal, timeout=None):
        """
        When expecting a response on some command, ensure the response won't come earlier
        than you starting to wait for it.

        Starts listening on instantiation. Call wait_for_output to get your collected data or to wait for it

        :param regex: what to look for in the printer output
        :param received_signal: only the receive signal is supported now
        :param timeout: how long to wait
        :return: The regex match object
        """

        self.regex = regex
        self.received_signal = received_signal
        self.timeout = timeout

        self.event = Event()
        self.match = None

        self.received_signal.connect(self.handler)

    def handler(self, sender, line):
        match = self.regex.fullmatch(line)
        if match:
            self.match = match
            self.event.set()
        else:
            log.debug(f"Message {line} did not match {self.regex.pattern}")

    def wait_for_output(self):
        success = self.event.wait(timeout=self.timeout)
        if not success:
            raise TimeoutError(f"Timed out waiting for match with regex '{self.regex.pattern}'")
        self.received_signal.disconnect(self.handler)

        return self.match


class PrinterCommunication:

    def __init__(self, port="/dev/ttyAMA0", baudrate=115200, timeout=1, write_timeout=0, connection_write_delay=1):
        self.serial = serial.Serial(baudrate=baudrate, port=port, timeout=timeout, write_timeout=write_timeout)

        self.signals = Signals()

        sleep(connection_write_delay)

        self.running = True
        self.read_thread = Thread(target=self._read_continually, name="serial_read_thread")
        self.read_thread.start()

        self.write_read_lock = Lock()

        self.__garbage_collector_safehouse = set()  # The big black dog keeps eating my handlers...

    def _read_continually(self):
        """Ran in a thread, reads stuff over an over"""
        while self.running:
            line = self.serial.readline().decode("ASCII").strip()
            if line != "":
                self.write_read_lock.acquire()
                log.info(f"Printer says: '{line}'")
                self.signals.received.send( line=line)
                self.write_read_lock.release()

    def write(self, message: str, wait_for_regex: re.Pattern = None, timeout=None) -> Union[None, re.Match]:
        """
        Writes a message, has an ability to wait for an arbitrary regex after that

        :param message: the message to be sent
        :param wait_for_regex: regex pattern to wait for
        :param timeout: time in seconds to wait before giving up
        :return: the match object if any pattern was given. Otherwise None
        """
        if message[-1] != "\n":
            message += "\n"
        message_bytes = message.encode("ASCII")

        response_waiter = None
        if wait_for_regex is not None:
            response_waiter = OutputCollector(wait_for_regex, self.signals.received, timeout=timeout)

        with self.write_read_lock:
            log.info(f"Sending to printer: {message_bytes}")
            self.serial.write(message_bytes)

        if wait_for_regex is not None:
            return response_waiter.wait_for_output()

    def register_output_handler(self, regex: re.Pattern, handler, *args, **kwargs):
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
            else:
                log.debug(f"No match on: '{line}' pattern: '{regex.pattern}'")

        self.__garbage_collector_safehouse.add(read_filter)
        self.signals.received.connect(read_filter)
        return read_filter

    def stop(self):
        self.running = False
        self.read_thread.join()





