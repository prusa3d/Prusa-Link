import logging
import termios
from threading import Thread, Lock
from time import sleep

import serial

from prusa_link.default_settings import get_settings
from prusa_link.input_output.serial.serial_reader import SerialReader

LOG = get_settings().LOG
TIME = get_settings().TIME


log = logging.getLogger(__name__)
log.setLevel(LOG.SERIAL)


class Serial:
    def __init__(self, serial_reader: SerialReader,
                 port="/dev/ttyAMA0", baudrate=115200, timeout=1,
                 write_timeout=0, connection_write_delay=10):

        self.connection_write_delay = connection_write_delay
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout
        self.write_timeout = write_timeout

        self.write_lock = Lock()

        self.serial = None
        self.serial_reader = serial_reader

        self.running = True
        self._renew_serial_connection()

        self.read_thread = Thread(target=self._read_continually,
                                  name="serial_read_thread")
        self.read_thread.start()

    def _reopen(self):
        # Prevent writing while reopening serial
        with self.write_lock:
            if self.serial is not None and self.serial.is_open:
                self.serial.close()

            # Prevent a hangup on serial close, this will make it,
            # so the printer resets only on reboot or replug,
            # not on prusa_link restarts
            f = open(self.port)
            attrs = termios.tcgetattr(f)
            log.debug(f"Serial attributes: {attrs}")
            # disable hangup
            attrs[2] = attrs[2] & ~termios.HUPCL
            # TCSAFLUSH set after everything is done
            termios.tcsetattr(f, termios.TCSAFLUSH, attrs)
            f.close()

            self.serial = serial.Serial(port=self.port, baudrate=self.baudrate,
                                        timeout=self.timeout,
                                        write_timeout=self.write_timeout)

            # No idea what these mean, but they seem to be 0, when the printer
            # isn't going to restart
            # FIXME: thought i could determine whether the printer is going to
            #  restart or not. But that proved unreliable
            # if attrs[0] != 0 or attrs[1] != 0:
            log.debug("Waiting for the printer to boot")
            sleep(TIME.PRINTER_BOOT_WAIT)

    def _renew_serial_connection(self):
        while self.running:
            try:
                self._reopen()
            except (serial.SerialException, FileNotFoundError,
                    PermissionError):
                log.debug("Openning of the serial port failed. Retrying...")
                sleep(TIME.SERIAL_REOPEN_TIMEOUT)
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
                    self.serial_reader.decide(line)

    def write(self, message: bytes):
        """
        Writes a message

        :param message: the message to be sent
        """
        # with self.write_read_lock:
        # Why would i not want to write and handle reads at the same time?
        log.debug(f"Sending to printer: {message}")

        errored_out = False
        with self.write_lock:
            try:
                self.serial.write(message)
            except serial.SerialException:
                log.error(
                    f"Serial error when sending '{message}' to the printer")
                errored_out = True
        if errored_out:
            self._renew_serial_connection()

    def blip_dtr(self):
        with self.write_lock:
            self.serial.dtr = False
            self.serial.dtr = True
            sleep(TIME.PRINTER_BOOT_WAIT)

    def stop(self):
        self.running = False
        self.serial.close()
        self.read_thread.join()
