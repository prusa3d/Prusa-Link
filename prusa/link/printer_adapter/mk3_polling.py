"""
Uses info updater to keep up with the printer info.
Hope I can get most of printer polling to use this mechanism.
"""
import logging
from distutils.version import StrictVersion

from prusa.connect.printer import Printer

from .informers.job import Job
from .input_output.serial.helpers import wait_for_instruction, \
    enqueue_matchable
from .structures.model_classes import NetworkInfo
from .structures.regular_expressions import SN_REGEX, PRINTER_TYPE_REGEX, \
    FW_REGEX, NOZZLE_REGEX, D3_OUTPUT_REGEX, VALID_SN_REGEX
from .. import errors
from .const import QUIT_INTERVAL, PRINTER_TYPES, MINIMAL_FIRMWARE
from .input_output.serial.serial_queue import \
    SerialQueue
from .model import Model
from .structures.item_updater import ItemUpdater, \
    WatchedItem, WatchedGroup
from .util import make_fingerprint

log = logging.getLogger(__name__)


class MK3Polling:
    """
    Sets up the tracked values for info_updater
    """

    quit_interval = QUIT_INTERVAL

    # pylint: disable=too-many-statements
    def __init__(self, serial_queue: SerialQueue, printer: Printer,
                 model: Model, job: Job):
        super().__init__()
        self.item_updater = ItemUpdater()

        self.serial_queue = serial_queue
        self.printer = printer
        self.model = model
        self.job = job

        # Printer info (for init and SEND_INFO)

        self.network_info = WatchedItem("network_info",
                                        gather_function=self._get_network_info,
                                        write_function=self._set_network_info)
        self.item_updater.add_watched_item(self.network_info)

        self.printer_type = WatchedItem(
            "printer_type",
            gather_function=self._get_printer_type,
            write_function=self._set_printer_type,
            validation_function=self._validate_printer_type)
        self.item_updater.add_watched_item(self.printer_type)
        self.printer_type.became_valid_signal.connect(
            lambda item: self._set_id_error(True), weak=False)
        self.printer_type.val_err_timeout_signal.connect(
            lambda item: self._set_id_error(False), weak=False)

        self.firmware_version = WatchedItem(
            "firmware_version",
            gather_function=self._get_firmware_version,
            write_function=self._set_firmware_version,
            validation_function=self._validate_fw_version)
        self.item_updater.add_watched_item(self.firmware_version)
        self.firmware_version.became_valid_signal.connect(
            lambda item: self._set_fw_error(True), weak=False)
        self.firmware_version.val_err_timeout_signal.connect(
            lambda item: self._set_fw_error(False), weak=False)

        self.nozzle_diameter = WatchedItem(
            "nozzle_diameter",
            gather_function=self._get_nozzle_diameter,
            write_function=self._set_nozzle_diameter)
        self.nozzle_diameter.interval = 10
        self.item_updater.add_watched_item(self.nozzle_diameter)

        self.serial_number = WatchedItem(
            "serial_number",
            gather_function=self._get_serial_number,
            write_function=self._set_serial_number,
            validation_function=self._validate_serial_number)
        self.serial_number.timeout = 25
        self.serial_number.became_valid_signal.connect(
            lambda item: self._set_sn_error(True), weak=False)
        self.serial_number.val_err_timeout_signal.connect(
            lambda item: self._set_sn_error(False), weak=False)
        self.item_updater.add_watched_item(self.serial_number)

        self.printer_info = WatchedGroup([
            self.network_info, self.printer_type, self.firmware_version,
            self.nozzle_diameter, self.serial_number
        ])

        self.job_id = WatchedItem(
            "job_id",
            gather_function=self._get_job_id,
            write_function=self._set_job_id,
        )
        self.job_id.became_valid_signal.connect(
            lambda item: self._set_job_id_error(True), weak=False)
        self.job_id.val_err_timeout_signal.connect(
            lambda item: self._set_job_id_error(False), weak=False)
        self.item_updater.add_watched_item(self.job_id)

        # TODO: Put this outside
        for item in self.printer_info:
            item.value_changed_signal.connect(lambda value: self._send_info(),
                                              weak=False)

    def start(self):
        """Starts the item updater"""
        self.item_updater.start()

    def stop(self):
        """Stops the item updater"""
        self.item_updater.stop()

    def wait_stopped(self):
        """Waits for the item updater to stop"""
        self.item_updater.wait_stopped()

    def invalidate_printer_info(self):
        """Invalidates all of printer info related watched values"""
        self.item_updater.invalidate_group(self.printer_info)

    def invalidate_network_info(self):
        """Invalidates just the network info"""
        self.item_updater.invalidate(self.network_info)

    def invalidate_serial_number(self):
        """Invalidates just the serial number"""
        self.item_updater.invalidate(self.serial_number)

    def polling_not_ok(self):
        """Stops polling of some values"""
        self.item_updater.watched_items["nozzle_diameter"].interval = None

        self.item_updater.cancel_scheduled_invalidation("nozzle_diameter")

    def polling_ok(self):
        """Re-starts polling of some values"""
        self.item_updater.watched_items["nozzle_diameter"].interval = 10

        self.item_updater.schedule_invalidation("nozzle_diameter")

    def ensure_job_id(self):
        """
        This is an oddball, I don't have anything able to ensure the job_id
        stays in sync, I cannot wait for it, that would block the read thread
        I cannot just write it either, I wouldn't know if it failed.
        """
        def job_became_valid(item):
            self.job_id.became_valid_signal.disconnect(job_became_valid)
            if self.model.job.job_id != item.value:
                log.warning(
                    "Job id on the printer: %s differs from the local"
                    " one: %s!", item.value, self.model.job.job_id)
                self.job.write()
                self.ensure_job_id()

        self.item_updater.schedule_invalidation(ambiguous_item=self.job_id,
                                                interval=1)
        self.job_id.became_valid_signal.connect(job_became_valid)

    # -- Gather --
    def should_wait(self):
        """Gather helper returning if the component is still running"""
        return self.item_updater.running

    def do_matcheble(self, gcode, regex, to_front=False):
        """
        Analogic to the command one, as the getters do this
        over and over again
        """
        instruction = enqueue_matchable(self.serial_queue,
                                        gcode,
                                        regex,
                                        to_front=to_front)
        wait_for_instruction(instruction, self.should_wait)
        match = instruction.match()
        if match is None:
            raise RuntimeError("Printer responded with something unexpected")
        return match

    def _get_network_info(self):
        """Gets the mac and ip addresses and packages them into an object."""
        network_info = NetworkInfo()
        ip_data = self.model.ip_updater
        if ip_data.local_ip is not None:
            if ip_data.is_wireless:
                log.debug("WIFI - mac: %s", ip_data.mac)
                network_info.wifi_ipv4 = ip_data.local_ip
                network_info.wifi_ipv6 = ip_data.local_ip6
                network_info.wifi_mac = ip_data.mac
                network_info.wifi_ssid = ip_data.ssid
                network_info.lan_ipv4 = None
                network_info.lan_ipv6 = None
                network_info.lan_mac = None
            else:
                log.debug("LAN - mac: %s", ip_data.mac)
                network_info.lan_ipv4 = ip_data.local_ip
                network_info.lan_ipv6 = ip_data.local_ip6
                network_info.lan_mac = ip_data.mac
                network_info.wifi_ipv4 = None
                network_info.wifi_ipv6 = None
                network_info.wifi_mac = None
                network_info.wifi_ssid = None

            network_info.hostname = ip_data.hostname
            network_info.username = ip_data.username
            network_info.digest = ip_data.digest

        return network_info.dict()

    def _get_printer_type(self):
        """Gets the printer code using the M862.2 Q gcode."""
        match = self.do_matcheble("M862.2 Q",
                                  PRINTER_TYPE_REGEX,
                                  to_front=True)
        return int(match.group("code"))

    def _get_firmware_version(self):
        """Try to get firmware version from the printer."""
        match = self.do_matcheble("PRUSA Fir", FW_REGEX, to_front=True)
        return match.group("version")

    def _get_nozzle_diameter(self):
        """Gets the printers nozzle diameter using M862.1 Q"""
        match = self.do_matcheble("M862.1 Q", NOZZLE_REGEX, to_front=True)
        return float(match.group("size"))

    def _get_serial_number(self):
        """Returns the SN regex match"""
        match = self.do_matcheble("PRUSA SN", SN_REGEX, to_front=True)
        return match.group("sn")

    def _get_job_id(self):
        """Gets the current job_id from the printer"""
        match = self.do_matcheble("D3 Ax0D05 C4",
                                  D3_OUTPUT_REGEX,
                                  to_front=True)
        return int(match.group("data").replace(" ", ""), base=16)

    # -- Validate --

    def _validate_serial_number(self, value):
        """
        Validates the serial number, throws error because a more
        descriptive error message can be shown this way
        """
        if VALID_SN_REGEX.match(value) is None:
            return False

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
        if value not in PRINTER_TYPES:
            raise ValueError(f"The printer with type {value} is not supported")

        printer_type = PRINTER_TYPES[value]
        if self.printer.type is not None and printer_type != self.printer.type:
            log.error("The printer type changed. ")
            raise RuntimeError(f"Printer type cannot change! Original: "
                               f"{self.printer.sn} current: {value}.")
        return True

    @staticmethod
    def _validate_fw_version(value):
        """Validates that the printer fw version is up to date enough"""
        without_buildnumber = value.split("-")[0]
        if StrictVersion(without_buildnumber) < MINIMAL_FIRMWARE:
            raise ValueError("The printer firmware is outdated")
        return True

    @staticmethod
    def _validate_percent(value):
        """Validates the speed multiplier as well as the flow rate"""
        if not 0 <= value <= 999:
            raise ValueError("The speed multiplier or flow rate is not "
                             "between 0 and 999")
        return True

    # -- Write --
    def _set_network_info(self, value):
        """Sets network info"""
        self.printer.network_info = value

    def _set_printer_type(self, value):
        """
        Do not try and overwrite the printer type, that would
        raise an error
        """
        if self.printer.type is None:
            self.printer.type = PRINTER_TYPES[value]

    def _set_firmware_version(self, value):
        """
        It's a setter, what am I expected to write here?
        Sets the firmware version duh
        """
        self.printer.firmware = value

    def _set_nozzle_diameter(self, value):
        """Sets the nozzle diameter"""
        self.printer.nozzle_diameter = value

    def _set_serial_number(self, value):
        """Set serial number and fingerprint"""
        if self.printer.sn is None:
            self.printer.sn = value
            self.printer.fingerprint = make_fingerprint(value)

    def _set_job_id(self, value):
        """Set the job id"""
        self.job.job_id_from_eeprom(value)

    # -- Signal handlers --

    @staticmethod
    def _set_sn_error(value):
        """Needs to exist because we cannot assign in lambdas"""
        errors.SN.ok = value

    @staticmethod
    def _set_id_error(value):
        """Needs to exist because we cannot assign in lambdas"""
        errors.ID.ok = value

    @staticmethod
    def _set_fw_error(value):
        """Needs to exist because we cannot assign in lambdas"""
        errors.FW.ok = value

    @staticmethod
    def _set_job_id_error(value):
        """Needs to exist because we cannot assign in lambdas"""
        errors.JOB_ID.ok = value

    def _send_info(self):
        """
        Sends info on every value change

        If the printer is not initialized yet, does not send anything
        """
        # This relies on update being called after became_valid_signal
        if self.printer_info.valid:
            self.printer.event_cb(**self.printer.get_info())
