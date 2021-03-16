"""Contains implementation of the Serial class"""
import logging
import termios
from threading import Thread, Lock
from time import sleep

import serial  # type: ignore

from blinker import Signal  # type: ignore

from .serial_reader import SerialReader
from ...const import PRINTER_BOOT_WAIT, \
    SERIAL_REOPEN_TIMEOUT
from ...structures.mc_singleton import MCSingleton
from ...updatable import prctl_name
from .... import errors

log = logging.getLogger(__name__)


class Serial(metaclass=MCSingleton):
    """
    Class handling the basic serial management, opening, re-opening,
    writing and reading.

    It also can reset the connected device using DTR - works only with USB
    """
    def __init__(self,
                 serial_reader: SerialReader,
                 port="/dev/ttyAMA0",
                 baudrate=115200,
                 timeout=1,
                 write_timeout=0,
                 connection_write_delay=10):

        # pylint: disable=too-many-arguments
        self.connection_write_delay = connection_write_delay
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout
        self.write_timeout = write_timeout

        self.write_lock = Lock()

        self.serial = None
        self.serial_reader = serial_reader

        self.failed_signal = Signal()
        self.renewed_signal = Signal()

        self.running = True
        self._renew_serial_connection()

        self.read_thread = Thread(target=self._read_continually,
                                  name="serial_read_thread")
        self.read_thread.start()

    def _reopen(self):
        """
        If open, closes the serial port, for usb prevents unnecessary
        device resets and finally tries to open the serial again
        """
        if self.serial is not None and self.serial.is_open:
            self.serial.close()

        # Prevent a hangup on serial close, this will make it,
        # so the printer resets only on reboot or replug,
        # not when prusa_link restarts
        serial_descriptor = open(self.port)
        attrs = termios.tcgetattr(serial_descriptor)
        log.debug("Serial attributes: %s", attrs)
        # disable hangup
        attrs[2] = attrs[2] & ~termios.HUPCL
        # TCSAFLUSH set after everything is done
        termios.tcsetattr(serial_descriptor, termios.TCSAFLUSH, attrs)
        serial_descriptor.close()

        self.serial = serial.Serial(port=self.port,
                                    baudrate=self.baudrate,
                                    timeout=self.timeout,
                                    write_timeout=self.write_timeout)

        # Tried to predict, whether the printer was going to restart on serial
        # connect, but it proved unreliable
        log.debug("Waiting for the printer to boot")
        sleep(PRINTER_BOOT_WAIT)

    def _renew_serial_connection(self):
        """
        Informs the rest of the app about failed serial connection,
        After which it keeps trying to re-open the serial port

        If it succeeds, generates a signal to remove the rest of the app
        """
        # Never call this without locking the write lock first!

        # When just starting, this is fine as the signal handlers
        # can't be connected yet
        self.failed_signal.send(self)
        while self.running:
            try:
                self._reopen()
            except (serial.SerialException, FileNotFoundError, OSError):
                errors.SERIAL.ok = False
                log.warning("Opening of the serial port %s failed. Retrying",
                            self.port)
                sleep(SERIAL_REOPEN_TIMEOUT)
            except Exception:  # pylint: disable=broad-except
                # The same as above, just a different warning
                errors.SERIAL.ok = False
                log.warning("Opening of the serial port failed for a "
                            "different reason than what's expected. "
                            "Please report this!")
                sleep(SERIAL_REOPEN_TIMEOUT)
            else:
                errors.SERIAL.ok = True
                break
        self.renewed_signal.send(self)

    def _read_continually(self):
        """Ran in a thread, reads stuff over an over"""
        prctl_name()
        while self.running:
            raw_line = "[No data] - This is a fallback value, " \
                       "so stuff doesn't break"
            try:
                raw_line = self.serial.readline()
                line = raw_line.decode("ASCII").strip()
            except serial.SerialException:
                log.error("Failed when reading from the printer. "
                          "Trying to re-open")

                with self.write_lock:  # Let's lock the writing
                    # if the serial is broken
                    self._renew_serial_connection()
            except UnicodeDecodeError:
                log.error("Failed decoding a message %s", raw_line)
            else:
                if line != "":
                    # with self.write_read_lock:
                    # Why would I not want to write and handle reads
                    # at the same time? IDK, but if something weird starts
                    # happening, i'll re-enable this
                    log.debug("Printer says: '%s'", line)
                    self.serial_reader.decide(line)

    def write(self, message: bytes):
        """
        Writes a message to serial, if it for some reason fails,
        calls _renew_serial_connection

        :param message: the message to be sent
        """
        log.debug("Sending to printer: %s", message)

        sent = False
        if not self.serial:
            return

        with self.write_lock:
            while not sent and self.running:
                try:
                    self.serial.write(message)
                except serial.SerialException:
                    log.error("Serial error when sending '%s' to the printer",
                              message)
                    self._renew_serial_connection()
                else:
                    sent = True

    def blip_dtr(self):
        """Pulses the DTR to reset the connected device. Work only over USB"""
        with self.write_lock:
            self.serial.dtr = False
            self.serial.dtr = True
            sleep(PRINTER_BOOT_WAIT)

    def stop(self):
        """Stops the component"""
        self.running = False
        self.serial.close()
        self.read_thread.join()
