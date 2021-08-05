"""
Uses info updater to keep up with the printer info.
Hope I can get most of printer polling to use this mechanism.
"""
import logging

from prusa.connect.printer import Printer

from .. import errors
from .const import QUIT_INTERVAL
from .informers.getters import get_printer_type, \
    get_firmware_version, get_serial_number, get_nozzle_diameter, \
    get_network_info
from .input_output.serial.serial_queue import \
    SerialQueue
from .model import Model
from .structures.info_updater import InfoUpdater, \
    WatchedValue, WatchedGroup
from .util import make_fingerprint

log = logging.getLogger(__name__)


class MK3Info(InfoUpdater):
    """
    Sets up the tracked values for info_updater
    """

    quit_interval = QUIT_INTERVAL

    def __init__(self, serial_queue: SerialQueue, printer: Printer,
                 model: Model):
        super().__init__()

        self.serial_queue = serial_queue
        self.printer = printer
        self.model = model

        self.initialized = False

        network_info = WatchedValue(
            "network_info",
            gather_function=lambda: get_network_info(self.model),
            write_function=self._set_network_info)
        self.tracked_items["network_info"] = network_info

        printer_type = WatchedValue(
            "printer_type",
            gather_function=lambda: get_printer_type(self.serial_queue, lambda:
                                                     self.running),
            write_function=self._set_printer_type,
            validation_function=self._validate_printer_type)
        self.tracked_items["printer_type"] = printer_type

        firmware_version = WatchedValue(
            "firmware_version",
            gather_function=lambda: get_firmware_version(
                self.serial_queue, lambda: self.running),
            write_function=self._set_firmware_version)
        self.tracked_items["firmware_version"] = firmware_version

        nozzle_diameter = WatchedValue(
            "nozzle_diameter",
            gather_function=lambda: get_nozzle_diameter(
                self.serial_queue, lambda: self.running),
            write_function=self._set_nozzle_diameter)
        nozzle_diameter.interval = 10
        self.tracked_items["nozzle_diameter"] = nozzle_diameter

        serial_number = WatchedValue(
            "serial_number",
            gather_function=self._get_serial_number,
            write_function=self._set_serial_number,
            validation_function=self._validate_serial_number)
        serial_number.timeout = 25
        serial_number.became_valid_signal.connect(
            lambda item: self._set_sn_error(True))
        serial_number.val_err_timeout_signal.connect(
            lambda item: self._set_sn_error(False))
        self.tracked_items["serial_number"] = serial_number

        self.printer_info = WatchedGroup([
            network_info, printer_type, firmware_version, nozzle_diameter,
            serial_number
        ])

        for item in self.printer_info:
            item.value_changed_signal.connect(lambda sender: self._send_info())

        for item in self.tracked_items.values():
            self.invalidate(item)

    def invalidate_printer_info(self):
        """Invalidates all of printer info related watched values"""
        self.invalidate(self.printer_info)

    def polling_not_ok(self):
        """Stops polling of some values"""
        self.tracked_items["nozzle_diameter"].interval = None

        self.cancel_scheduled_invalidation("nozzle_diameter")

    def polling_ok(self):
        """Re-starts polling of some values"""
        self.tracked_items["nozzle_diameter"].interval = 10

        self.schedule_invalidation("nozzle_diameter")

    # -- Gather --

    def _get_serial_number(self):
        """Returns the match"""
        serial_number = get_serial_number(self.serial_queue,
                                          lambda: self.running)

        return serial_number

    # -- Validate --

    def _validate_serial_number(self, value):
        """
        Validates the serial number, throws error because a more
        descriptive error message can be shown this way
        """
        if self.printer.sn is not None and value != self.printer.sn:
            log.error("The new serial number is different from the old one!")
            raise RuntimeError(f"Serial numbers differ. Original: "
                               f"{self.printer.sn} new one: {value}.")
        return True

    def _validate_printer_type(self, value):
        """
        Validates the printer type, throws error because a more
        descriptive error message can be shown this way
        """
        if self.printer.type is not None and value != self.printer.type:
            log.error("The printer type changed. ")
            raise RuntimeError(f"Printer type cannot change! Original: "
                               f"{self.printer.sn} current: {value}.")
        return True

    # -- Write --
    def _set_network_info(self, item):
        """Sets network info"""
        self.printer.network_info = item.value

    def _set_printer_type(self, item):
        """
        Do not try and overwrite the printer type, that would
        raise an error
        """
        if self.printer.type is None:
            self.printer.type = item.value

    def _set_firmware_version(self, item):
        """
        It's a setter, what am I expected to write here?
        Sets the firmware version duh
        """
        self.printer.firmware_version = item.value

    def _set_nozzle_diameter(self, item):
        """Sets the nozzle diameter"""
        self.printer.nozzle_diameter = item.value

    def _set_serial_number(self, item):
        """Set serial number and fingerprint"""
        if self.printer.sn is None:
            self.printer.sn = item.value
            self.printer.fingerprint = make_fingerprint(item.value)

    # -- Signal handlers --

    @staticmethod
    def _set_sn_error(value):
        """Needs to exist because we cannot assign in lambdas"""
        errors.SN.ok = value

    def _send_info(self):
        """
        Sends info on every value change

        If the printer is not initialized yet, does not send anything
        """
        # This relies on update being called after became_valid_signal
        if self.printer_info.valid:
            self.printer.event_cb(**self.printer.get_info())
