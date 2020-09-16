import logging
import re
import termios
from functools import partial
from threading import Thread, Lock
from time import sleep

import serial
from blinker import Signal

from old_buddy.default_settings import get_settings

LOG = get_settings().LOG
TIME = get_settings().TIME


log = logging.getLogger(__name__)
log.setLevel(LOG.SERIAL_LOG_LEVEL)


class Serial:
    received = Signal()  # kwargs: line: str

    def __init__(self, port="/dev/ttyAMA0", baudrate=115200, timeout=1,
                 write_timeout=0, connection_write_delay=10):

        self.connection_write_delay = connection_write_delay
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout
        self.write_timeout = write_timeout

        self.serial = None

        self.running = True
        self._renew_serial_connection()

        self.read_thread = Thread(target=self._read_continually,
                                  name="serial_read_thread")
        self.read_thread.start()

        self.write_read_lock = Lock()

        # The big black dog keeps eating my handlers...
        self.__garbage_collector_safehouse = set()

    def _reopen(self):
        if self.serial is not None and self.serial.is_open:
            self.serial.close()

        # Prevent a hungup on serial close, this will make it,
        # so the printer resets only on reboot or replug,
        # not on old_buddy restarts
        f = open(self.port)
        attrs = termios.tcgetattr(f)
        log.debug(f"Serial attributes: {attrs}")
        attrs[2] = attrs[2] & ~termios.HUPCL
        termios.tcsetattr(f, termios.TCSAFLUSH, attrs)
        f.close()

        self.serial = serial.Serial(port=self.port, baudrate=self.baudrate,
                                    timeout=self.timeout,
                                    write_timeout=self.write_timeout)

        # No idea what these mean, but they seem to be 0, when the printer
        # isn't going to restart
        if attrs[0] != 0 or attrs[1] != 0:
            sleep(TIME.PRINTER_BOOT_WAIT)

    def _renew_serial_connection(self):
        while self.running:
            try:
                self._reopen()
            except serial.SerialException:
                log.debug("Openning of the serial port failed. Retrying...")
            else:
                break

    def _read_continually(self):
        """Ran in a thread, reads stuff over an over"""
        while self.running:
            try:
                line = self.serial.readline().decode("ASCII").strip()
            except serial.SerialException:
                log.error("Failed when reading from the printer. "
                          "Trying to re-open")
                self._renew_serial_connection()
            except UnicodeDecodeError:
                log.error("Failed decoding a message")
            else:
                if line != "":
                    # with self.write_read_lock:
                    # Why would I not want to write and handle reads
                    # at the same time? IDK, but if something weird starts
                    # happening, i'll re-enable this
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
        with self.write_read_lock:
            try:
                self.serial.write(message)
            except serial.SerialException:
                log.error(
                    f"Serial error when sending '{message}' to the printer")

    def add_output_handler(self, regex: re.Pattern, handler, *args,
                           debug=False, **kwargs):
        """
        register an output handler for an arbitrary regex
        The regex will be searched each response from the printer
        To unregister, just get rid off any references to the returned object
        garbage collection should take care of the rest

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

    def blip_dtr(self):
        with self.write_read_lock:
            self.serial.dtr = False
            self.serial.dtr = True
            sleep(TIME.PRINTER_BOOT_WAIT)

    def stop(self):
        self.running = False
        self.serial.close()
        self.read_thread.join()
