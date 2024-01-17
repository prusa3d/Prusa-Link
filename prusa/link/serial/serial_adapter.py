"""Contains implementation of the Serial class"""
import glob
import logging
import os
import re
from importlib import util
from pathlib import Path
from threading import Event, RLock
from time import sleep, time
from typing import List, Optional

from blinker import Signal  # type: ignore

from prusa.connect.printer.conditions import CondState

from ..conditions import SERIAL
from ..const import (
    PRINTER_BOOT_WAIT,
    PRINTER_TYPES,
    QUIT_INTERVAL,
    RESET_PIN,
    SERIAL_REOPEN_TIMEOUT,
)
from ..printer_adapter.model import Model
from ..printer_adapter.structures.mc_singleton import MCSingleton
from ..printer_adapter.structures.module_data_classes import (
    Port,
    SerialAdapterData,
)
from ..printer_adapter.structures.regular_expressions import (
    ATTENTION_REGEX,
    BUSY_REGEX,
    FW_REGEX,
    PRINTER_TYPE_REGEX,
)
from ..printer_adapter.updatable import Thread
from ..util import decode_line, get_usb_printers, prctl_name
from .serial import Serial, SerialException
from .serial_parser import ThreadedSerialParser

log = logging.getLogger(__name__)


class PortAdapter:
    """Use the Port class, but allow to pass a Serial instance with it"""
    def __init__(self, port: Port) -> None:
        self.port: Port = port
        self.serial: Optional[Serial] = None


class SerialAdapter(metaclass=MCSingleton):
    """
    Class handling the basic serial management, opening, re-opening,
    writing and reading.

    It also can reset the connected device using DTR - works only with USB
    """

    @staticmethod
    def is_rpi_port(port_path):
        """Figure out, whether we're running through the Einsy RPi port"""
        try:
            port_name = Path(port_path).name
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
        return True

    def __init__(self,
                 serial_parser: ThreadedSerialParser,
                 model: Model,
                 configured_port="auto",
                 baudrate: int = 115200,
                 timeout: int = 2,
                 reset_disabling: bool = True) -> None:

        # pylint: disable=too-many-arguments
        self.model: Model = model
        self.model.serial_adapter = SerialAdapterData(
            using_port=None, reset_disabling=reset_disabling)
        self.data: SerialAdapterData = model.serial_adapter
        self.configured_port = configured_port
        self.baudrate = baudrate
        self.timeout = timeout

        self.write_lock = RLock()

        self.serial: Optional[Serial] = None
        self.serial_parser = serial_parser

        self.failed_signal = Signal()
        self.renewed_signal = Signal()

        self.running = True
        self._work_around_power_panic = Event()
        self._work_around_power_panic.set()

        self.read_thread = Thread(target=self._read_continually,
                                  name="serial_read_thread",
                                  daemon=True)
        self.read_thread.start()

    @staticmethod
    def is_open(serial) -> bool:
        """Returns bool indicating whether there's a serial connection"""
        return serial is not None and serial.is_open

    @staticmethod
    def _get_info(port_adapter: PortAdapter):
        """Gets info about the supplied port
        returns whether it figured something out or not"""
        serial = port_adapter.serial
        port = port_adapter.port

        if serial is None:
            raise SerialException("Tried getting info without a serial port "
                                  "(mostly for mypy to stop crying)")

        name = version = error_text = None
        serial.write(b"PRUSA Fir\nM862.2 Q\n")
        timeout_at = time() + 5
        while (raw_line := serial.readline()) and time() < timeout_at:
            line = decode_line(raw_line)
            log.debug("Printer detection for '%s' returned: %s",
                      port.path, line)
            if match := PRINTER_TYPE_REGEX.match(line):
                if (code := int(match.group("code"))) in PRINTER_TYPES:
                    name = "Prusa " + PRINTER_TYPES[code].name
                else:
                    error_text = "The printer is not supported"
            elif match := FW_REGEX.match(line):
                version = match.group("version")
            elif BUSY_REGEX.match(line):
                error_text = "The printer is busy"
            elif ATTENTION_REGEX.match(line):
                error_text = "The printer wants user attention"

            if name and version:
                port.usable = True
                port.description = f"{name} - FW: {version}"
                return
            if error_text:
                port.description = error_text
                return
        port.description = "A printer did not answer in time"

    @staticmethod
    def _detect(port_adapter: PortAdapter):
        """
        Detects the usability of given port
        Split into two for pylint, this one is responsible for opening serial
        """
        prctl_name()
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

        except (SerialException, FileNotFoundError, OSError) as error:
            port.description = "Failed to open. Is a printer connected " \
                               f"to this port? Error: {error}"
            if SerialAdapter.is_open(serial):
                serial.close()  # type: ignore
        port.checked = True
        log.debug("Port: '%s' description: '%s'",
                  port.path, port.description)

    def _reopen(self) -> bool:
        """Re-open the configured serial port. Do a full re-scan if
        auto is configured"""
        self.data.using_port = None
        self.data.ports = []
        port_adapters: List[PortAdapter] = []
        threads = []
        with self.write_lock:
            self.close()

            if self.configured_port == "auto":
                paths = glob.glob("/dev/ttyAMA*")
                paths.extend(glob.glob("/dev/ttyACM*"))
                paths.extend(glob.glob("/dev/ttyUSB*"))
            else:
                # Follow symlinks to the real device file
                device_path = os.path.realpath(self.configured_port)
                paths = [device_path]

            # Pair the usb printer paths with their serial numbers
            usb_printers = {
                printer.path: printer.serial_number
                for printer in get_usb_printers()
            }

            for path in paths:
                port = Port(path=path,
                            baudrate=115200,
                            timeout=2,
                            is_rpi_port=self.is_rpi_port(path))
                if path in usb_printers:
                    port.sn = usb_printers[path]
                port_adapter = PortAdapter(port)
                self.data.ports.append(port)
                port_adapters.append(port_adapter)
                thread = Thread(target=self._detect,
                                args=(port_adapter,),
                                name="port_detector",
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
                    # The above if guarantees there's not a None
                    # in port.serial. Mypy is being dramatic again
                    port_adapter.serial.close()  # type: ignore
                    log.debug("Other port - %s", port)
            return found

    def close(self):
        """Close the serial. If the read thread is running,
        it should renew the connection.
        """
        with self.write_lock:
            if self.is_open(self.serial):
                self.serial.close()

    def _renew_serial_connection(self, starting: bool = False):
        """
        Informs the rest of the app about failed serial connection,
        After which it keeps trying to re-open the serial port

        If it succeeds, generates a signal to inform the rest of the app
        """
        # Wait for power panic timeout
        if not self._work_around_power_panic.is_set():
            self.failed_signal.send(self)
            SERIAL.state = CondState.NOK

        while self.running:
            if self._work_around_power_panic.wait(QUIT_INTERVAL):
                break

        if self.is_open(self.serial):
            raise RuntimeError("Don't reconnect what is not disconnected")

        while self.running:
            if starting:
                starting = False
            else:
                self.failed_signal.send(self)
                SERIAL.state = CondState.NOK

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
            self.data.resets_enabled = None
            self.renewed_signal.send(self)

    def _read_continually(self):
        """Ran in a thread, reads stuff over an over"""
        prctl_name()
        self._renew_serial_connection(starting=True)

        while self.running:
            raw_line = "[No data] - This is a fallback value, " \
                       "so stuff doesn't break"
            try:
                if not self._work_around_power_panic.is_set():
                    raise SerialException(
                        "Need to re-connect serial after power panic")
                raw_line = self.serial.readline()
                line = decode_line(raw_line)
            except (SerialException, OSError):
                log.exception("Failed when reading from the printer. "
                              "Trying to re-open")
                self.close()
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
                    log.debug("Recv: %s", line)
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
                    self.close()
                    raise SerialException(
                        "Serial error when sending") from error
                sent = True
                log.debug("Send: %s", message)

    def disable_dtr_resets(self):
        """Disables DTR resets - should be used by a command handler"""
        if not self.data.reset_disabling:
            return
        if self.data.resets_enabled is False:
            return
        self.write(b"\n;C32u2_RMD\n")

    def enable_dtr_resets(self):
        """Enables DTR resets - should be used by a command handler"""
        if not self.data.reset_disabling:
            return
        if self.data.resets_enabled is True:
            return
        self.write(b"\n;C32u2_RME\n")

    def _reset_pi(self):
        """Resets the connected raspberry pi"""
        spam_loader = util.find_spec('wiringpi')
        if spam_loader is None:
            log.warning("WiringPi missing, cannot reset using pins")
            return

        # pylint: disable=import-outside-toplevel
        # pylint: disable=import-error
        import wiringpi  # type: ignore
        wiringpi.wiringPiSetupGpio()
        wiringpi.pinMode(RESET_PIN, wiringpi.OUTPUT)
        wiringpi.digitalWrite(RESET_PIN, wiringpi.HIGH)
        wiringpi.digitalWrite(RESET_PIN, wiringpi.LOW)
        sleep(0.1)
        wiringpi.digitalWrite(RESET_PIN, wiringpi.LOW)

    def _blip_dtr(self):
        """Pulses the DTR to reset the connected device.
        Works only over USB"""
        with self.write_lock:
            self.serial.dtr = False
            self.serial.dtr = True
            sleep(PRINTER_BOOT_WAIT)

    def reset_client(self):
        """Resets the connected device, over USB or using the reset pin"""
        if not self.is_open(self.serial):
            log.warning("No serial connected, will not reset anything.")
            return

        if self.data.using_port.is_rpi_port:
            self._reset_pi()
        else:
            self._blip_dtr()

    def stop(self):
        """Stops the component"""
        self.running = False
        self.close()

    def wait_stopped(self):
        """Waits for the serial to be stopped"""
        self.read_thread.join()

    def power_panic_observed(self):
        """Called when a power panic is observed"""
        self._work_around_power_panic.clear()

    def power_panic_unblock(self):
        """Re-sets the power panic flag that holds the serial disconnected"""
        self._work_around_power_panic.set()
