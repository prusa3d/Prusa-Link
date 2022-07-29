"""Contains implementation of the Serial class"""
import glob
import logging
import os
import re
from pathlib import Path
from threading import Lock
from time import sleep, time
from typing import List

from blinker import Signal  # type: ignore
from prusa.connect.printer.conditions import CondState

from ..conditions import SERIAL
from ..const import PRINTER_BOOT_WAIT, SERIAL_REOPEN_TIMEOUT, PRINTER_TYPES
from ..printer_adapter.model import Model
from ..printer_adapter.structures.mc_singleton import MCSingleton
from ..printer_adapter.structures.module_data_classes import Port, \
    SerialAdapterData
from ..printer_adapter.structures.regular_expressions import \
    PRINTER_TYPE_REGEX, FW_REGEX, BUSY_REGEX, ATTENTION_REGEX
from ..printer_adapter.updatable import Thread, prctl_name
from .serial import SerialException, Serial
from .serial_parser import SerialParser
from ..util import decode_line

log = logging.getLogger(__name__)


class PortAdapter:
    """Use the Port class, but allow to pass a Serial instance with it"""
    def __init__(self, port):
        self.port: Port = port
        self.serial = None


class SerialAdapter(metaclass=MCSingleton):
    """
    Class handling the basic serial management, opening, re-opening,
    writing and reading.

    It also can reset the connected device using DTR - works only with USB
    """

    @staticmethod
    def is_rpi_port(port):
        """Figure out, whether we're running through the Einsy RPi port"""
        try:
            port_name = Path(port).name
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

    def __init__(self,
                 serial_parser: SerialParser,
                 model: Model,
                 configured_port="auto",
                 baudrate=115200,
                 timeout=2):

        # pylint: disable=too-many-arguments
        self.model: Model = model
        self.model.serial_adapter = SerialAdapterData()
        self.data: SerialAdapterData = model.serial_adapter
        self.configured_port = configured_port
        self.baudrate = baudrate
        self.timeout = timeout

        self.write_lock = Lock()

        self.serial = None
        self.serial_parser = serial_parser

        self.failed_signal = Signal()
        self.renewed_signal = Signal()

        self.running = True

        self.read_thread = Thread(target=self._read_continually,
                                  name="serial_read_thread",
                                  daemon=True)
        self.read_thread.start()

    @staticmethod
    def is_open(serial):
        """Returns bool indicating whether there's a serial connection"""
        return serial is not None and serial.is_open

    @staticmethod
    def _get_info(port_adapter: PortAdapter):
        """Gets info about the supplied port
        returns whether it figured something out or not"""
        serial = port_adapter.serial
        port = port_adapter.port

        name = version = inoperable_because = None
        serial.write(b"PRUSA Fir\nM862.2 Q\n")
        timeout_at = time() + 5
        while (raw_line := serial.readline()) and time() < timeout_at:
            line = decode_line(raw_line)
            if match := PRINTER_TYPE_REGEX.match(line):
                if (code := int(match.group("code"))) in PRINTER_TYPES:
                    name = "Prusa " + PRINTER_TYPES[code].name
                else:
                    inoperable_because = "the printer is not supported"
            elif match := FW_REGEX.match(line):
                version = match.group("version")
            elif BUSY_REGEX.match(line):
                inoperable_because = "the printer is busy"
            elif ATTENTION_REGEX.match(line):
                inoperable_because = "the printer wants user attention"

            if name and version:
                port.usable = True
                port.description = f"{name} - FW: {version}"
                return
            if inoperable_because:
                port.description = f"Won't connect because " \
                                   f"{inoperable_because}"
                return

    @staticmethod
    def _detect(port_adapter: PortAdapter):
        """
        Detects the usability of given port
        Split into two for pylint, this one is responsible for opening serial
        """
        port = port_adapter.port
        serial = None
        try:
            if not SerialAdapter.is_open(serial):
                serial = Serial(port=port.path,
                                baudrate=port.baudrate,
                                timeout=port.timeout)
                port_adapter.serial = serial
                if not port.is_rpi_port:
                    port.description = "Waiting for printer to boot"
                    sleep(8)

            SerialAdapter._get_info(port_adapter)

        except (SerialException, FileNotFoundError, OSError):
            if SerialAdapter.is_open(serial):
                serial.close()  # type: ignore
        port.checked = True

    def _reopen(self):
        """Re-open the configured serial port. Do a full re-scan if
        auto is configured"""
        self.data.using_port = None
        self.data.ports = []
        port_adapters: List[PortAdapter] = []
        threads = []
        with self.write_lock:
            if self.is_open(self.serial):
                self.serial.close()

            if self.configured_port == "auto":
                paths = glob.glob("/dev/ttyAMA*")
                paths.extend(glob.glob("/dev/ttyACM*"))
                paths.extend(glob.glob("/dev/ttyUSB*"))
            else:
                paths = [self.configured_port]

            for path in paths:
                port = Port(path=path,
                            baudrate=115200,
                            timeout=2,
                            is_rpi_port=self.is_rpi_port(path))
                port_adapter = PortAdapter(port)
                self.data.ports.append(port)
                port_adapters.append(port_adapter)
                thread = Thread(target=self._detect,
                                args=(port_adapter,),
                                daemon=True)
                threads.append(thread)
                thread.start()

            for thread in threads:
                thread.join()

            found = False
            for port_adapter in port_adapters:
                if port_adapter.port.usable and not found:
                    found = True
                    port_adapter.port.selected = True
                    self.data.using_port = port_adapter.port
                    self.serial = port_adapter.serial
                    log.info("Using the serial port %s",
                             self.data.using_port.path)
                elif self.is_open(port_adapter.serial):
                    port_adapter.serial.close()
                    log.debug("Other port - %s", port)
            return found

    def renew_serial_connection(self, starting: bool = False):
        """
        Informs the rest of the app about failed serial connection,
        After which it keeps trying to re-open the serial port

        If it succeeds, generates a signal to remove the rest of the app
        """

        if self.is_open(self.serial):
            raise RuntimeError("Don't reconnect what is not disconnected")

        while self.running:
            if starting:
                starting = False
            else:
                self.failed_signal.send(self)

            if not self._reopen():
                SERIAL.state = CondState.NOK
                log.warning("Error when connecting to serial according to "
                            "user config:  %s",
                            self.configured_port)
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
                line = decode_line(raw_line)
            except (SerialException, OSError):
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
            if not self.is_open(self.serial):
                log.warning("No serial to send '%s' to", message)
                return
            while not sent and self.running:
                try:
                    # Mypy does not work with functions that check for None
                    self.serial.write(message)  # type: ignore
                except OSError as error:
                    log.error("Serial error when sending '%s' to the printer",
                              message)
                    if self.is_open(self.serial):
                        # Same as the write above
                        self.serial.close()  # type: ignore
                    raise SerialException(
                        "Serial error when sending") from error
                else:
                    sent = True
                    log.debug("Sent to printer: %s", message)

    def blip_dtr(self):
        """Pulses the DTR to reset the connected device. Work only over USB"""
        if not self.is_open(self.serial):
            log.warning("No serial connected, no blips will take place")
        with self.write_lock:
            self.serial.dtr = False
            self.serial.dtr = True
            sleep(PRINTER_BOOT_WAIT)

    def stop(self):
        """Stops the component"""
        self.running = False
        if self.is_open(self.serial):
            self.serial.close()

    def wait_stopped(self):
        """Waits for the serial to be stopped"""
        self.read_thread.join()
