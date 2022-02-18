"""Contains implementation of the Serial class"""
import logging
from threading import Lock
from time import sleep

from blinker import Signal  # type: ignore

from .serial_parser import SerialParser
from ...lib import serial
from ...const import PRINTER_BOOT_WAIT, \
    SERIAL_REOPEN_TIMEOUT
from ...structures.mc_singleton import MCSingleton
from ...updatable import prctl_name, Thread
from .... import errors

log = logging.getLogger(__name__)


class SerialAdapter(metaclass=MCSingleton):
    """
    Class handling the basic serial management, opening, re-opening,
    writing and reading.

    It also can reset the connected device using DTR - works only with USB
    """
    def __init__(self,
                 serial_parser: SerialParser,
                 port="/dev/ttyAMA0",
                 baudrate=115200,
                 timeout=2):

        # pylint: disable=too-many-arguments
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout

        self.write_lock = Lock()

        self.serial = None
        self.serial_parser = serial_parser

        self.failed_signal = Signal()
        self.renewed_signal = Signal()

        self.running = True
        self._renew_serial_connection()

        self.read_thread = Thread(target=self._read_continually,
                                  name="serial_read_thread",
                                  daemon=True)
        self.read_thread.start()

    def _reopen(self):
        """
        If open, closes the serial port, for usb prevents unnecessary
        device resets and finally tries to open the serial again
        """
        if self.serial is not None and self.serial.is_open:
            self.serial.close()

        self.serial = serial.Serial(port=self.port,
                                    baudrate=self.baudrate,
                                    timeout=self.timeout)

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
            except (serial.SerialException, FileNotFoundError, OSError) as err:
                errors.SERIAL.ok = False
                log.debug(str(err))
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
                line = raw_line.decode("cp437").strip().replace('\x00', '')
            except serial.SerialException:
                log.exception("Failed when reading from the printer. "
                              "Trying to re-open")

                with self.write_lock:  # Let's lock the writing
                    # if the serial is broken
                    self._renew_serial_connection()
            except UnicodeDecodeError:
                log.error("Failed decoding a message %s", raw_line)
            else:
                # with self.write_read_lock:
                # Why would I not want to write and handle reads
                # at the same time? IDK, but if something weird starts
                # happening, i'll re-enable this
                if line == "":
                    log.debug("Printer has most likely sent something, "
                              "which is not human readable")
                else:
                    log.debug("Printer says: '%s'", line)
                self.serial_parser.decide(line)

    def write(self, message: bytes):
        """
        Writes a message to serial, if it for some reason fails,
        calls _renew_serial_connection

        :param message: the message to be sent
        """

        sent = False
        if not self.serial:
            log.warning("No serial to send '%s' to", message)
            return

        with self.write_lock:
            while not sent and self.running:
                try:
                    self.serial.write(message)
                except OSError:
                    log.error("Serial error when sending '%s' to the printer",
                              message)
                    self._renew_serial_connection()
                else:
                    sent = True
                    log.debug("Sent to printer: %s", message)

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

    def wait_stopped(self):
        """Waits for the serial to be stopped"""
        self.read_thread.join()
