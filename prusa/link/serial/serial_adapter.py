"""Contains implementation of the Serial class"""
import logging
import os
import re
from pathlib import Path
from threading import Lock
from time import sleep

from blinker import Signal  # type: ignore
from prusa.connect.printer.conditions import CondState

from ..conditions import SERIAL
from ..const import PRINTER_BOOT_WAIT, SERIAL_REOPEN_TIMEOUT
from ..printer_adapter.structures.mc_singleton import MCSingleton
from ..printer_adapter.updatable import Thread, prctl_name
from . import serial
from .serial import SerialException
from .serial_parser import SerialParser

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

        self.through_rpi_port = False
        self.through_rpi_port = self.is_rpi_port()

        self.read_thread = Thread(target=self._read_continually,
                                  name="serial_read_thread",
                                  daemon=True)
        self.read_thread.start()

    def is_rpi_port(self):
        """Figure out, whether we're running through the Einsy RPi port"""
        try:
            port_name = Path(self.port).name
            if not port_name.startswith("ttyAMA"):
                return False
            sys_path = Path(f"/sys/class/tty/{port_name}")
            link_path = os.readlink(str(sys_path))
            device_path = sys_path.parent.joinpath(link_path).resolve()
            path_regexp = re.compile(r"^/sys/devices/platform/soc/"
                                     r"[^.]*\.serial/tty/ttyAMA\d+$")
            match = path_regexp.match(str(device_path))
            if match is None:
                return False
        except Exception:  # pylint: disable=broad-except
            log.exception("Exception when checking if we're connected through "
                          "the Einsy pins. Assuming we're not.")
            return False
        else:
            return True

    def is_open(self):
        """Returns bool indicating whether there's a serial connection"""
        return self.serial is not None and self.serial.is_open

    def _reopen(self):
        """
        If open, closes the serial port, for usb prevents unnecessary
        device resets and finally tries to open the serial again
        """
        with self.write_lock:
            if self.is_open():
                self.serial.close()

            self.serial = serial.Serial(port=self.port,
                                        baudrate=self.baudrate,
                                        timeout=self.timeout)

            if self.through_rpi_port:
                log.debug("Think we're connected through the Einsy pins, "
                          "skipping waiting for boot")
            else:
                log.debug("Waiting for the printer to boot")
                sleep(PRINTER_BOOT_WAIT)

    def renew_serial_connection(self, starting: bool = False):
        """
        Informs the rest of the app about failed serial connection,
        After which it keeps trying to re-open the serial port

        If it succeeds, generates a signal to remove the rest of the app
        """

        if self.is_open():
            raise RuntimeError("Don't reconnect what is not disconnected")

        while self.running:
            if starting:
                starting = False
            else:
                self.failed_signal.send(self)

            try:
                self._reopen()
            except (serial.SerialException, FileNotFoundError, OSError) as err:
                SERIAL.state = CondState.NOK
                log.debug(str(err))
                log.warning("Opening of the serial port %s failed. Retrying",
                            self.port)
                sleep(SERIAL_REOPEN_TIMEOUT)
            except Exception:  # pylint: disable=broad-except
                # The same as above, just a different warning
                SERIAL.state = CondState.NOK
                log.exception("Opening of the serial port failed for a "
                              "different reason than what's expected. "
                              "Please report this!")
                sleep(SERIAL_REOPEN_TIMEOUT)
            else:
                break

        if self.running and not SERIAL:
            SERIAL.state = CondState.OK
            self.renewed_signal.send(self)

    def _read_continually(self):
        """Ran in a thread, reads stuff over an over"""
        prctl_name()
        self.renew_serial_connection(starting=True)

        while self.running:
            raw_line = "[No data] - This is a fallback value, " \
                       "so stuff doesn't break"
            try:
                raw_line = self.serial.readline()
                line = raw_line.decode("cp437").strip().replace('\x00', '')
            except (serial.SerialException, OSError):
                log.exception("Failed when reading from the printer. "
                              "Trying to re-open")
                self.serial.close()
                self.renew_serial_connection()
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

        Raises SerialException when the communication fails
        """

        sent = False

        with self.write_lock:
            if not self.is_open():
                log.warning("No serial to send '%s' to", message)
                return
            while not sent and self.running:
                try:
                    # Mypy does not work with functions that check for None
                    self.serial.write(message)  # type: ignore
                except OSError as error:
                    log.error("Serial error when sending '%s' to the printer",
                              message)
                    if self.is_open():
                        # Same as the write above
                        self.serial.close()  # type: ignore
                    raise SerialException(
                        "Serial error when sending") from error
                else:
                    sent = True
                    log.debug("Sent to printer: %s", message)

    def blip_dtr(self):
        """Pulses the DTR to reset the connected device. Work only over USB"""
        if not self.is_open():
            log.warning("No serial connected, no blips will take place")
        with self.write_lock:
            self.serial.dtr = False
            self.serial.dtr = True
            sleep(PRINTER_BOOT_WAIT)

    def stop(self):
        """Stops the component"""
        self.running = False
        if self.is_open():
            self.serial.close()

    def wait_stopped(self):
        """Waits for the serial to be stopped"""
        self.read_thread.join()
