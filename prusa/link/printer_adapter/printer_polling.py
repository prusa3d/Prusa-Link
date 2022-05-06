"""
Uses info updater to keep up with the printer info.
Hope I can get most of printer polling to use this mechanism.
"""
import logging
import re
from datetime import timedelta
from typing import List

from packaging.version import Version

from prusa.connect.printer import Printer
from .filesystem.sd_card import SDCard

from .job import Job
from .telemetry_passer import TelemetryPasser
from ..serial.helpers import wait_for_instruction, \
    enqueue_matchable
from ..serial.serial_parser import SerialParser
from .structures.model_classes import NetworkInfo, Telemetry, PrintMode
from .structures.regular_expressions import SN_REGEX, PRINTER_TYPE_REGEX, \
    FW_REGEX, NOZZLE_REGEX, D3_OUTPUT_REGEX, VALID_SN_REGEX, \
    PERCENT_REGEX, PRINT_INFO_REGEX, M27_OUTPUT_REGEX, MBL_REGEX
from .. import errors
from ..const import QUIT_INTERVAL, PRINTER_TYPES, MINIMAL_FIRMWARE, \
    SLOW_POLL_INTERVAL, FAST_POLL_INTERVAL, PRINT_STATE_PAIRING, \
    PRINT_MODE_ID_PAIRING, FLASH_AIR_INTERVAL
from ..serial.serial_queue import \
    SerialQueue
from .model import Model
from .structures.item_updater import ItemUpdater, \
    WatchedItem, WatchedGroup, SideEffectOnly
from ..util import make_fingerprint

log = logging.getLogger(__name__)


class PrinterPolling:
    """
    Sets up the tracked values for info_updater
    """

    quit_interval = QUIT_INTERVAL

    # pylint: disable=too-many-statements, too-many-arguments
    def __init__(self, serial_queue: SerialQueue, serial_parser: SerialParser,
                 printer: Printer, model: Model,
                 telemetry_passer: TelemetryPasser,
                 job: Job, sd_card: SDCard):
        super().__init__()
        self.item_updater = ItemUpdater()

        self.serial_queue = serial_queue
        self.serial_parser = serial_parser
        self.printer = printer
        self.model = model
        self.telemetry_passer = telemetry_passer
        self.job = job
        self.sd_card = sd_card

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

        self.print_mode = WatchedItem(
            "print_mode",
            gather_function=self._get_print_mode,
            interval=SLOW_POLL_INTERVAL
        )
        # Make silent the default for when we fail to get the value in time
        self.item_updater.add_watched_item(self.print_mode)
        self.item_updater.set_value(self.print_mode, PrintMode.SILENT)

        # TODO: Put this outside
        for item in self.printer_info:
            item.became_valid_signal.connect(lambda value: self._send_info(),
                                              weak=False)

        self.mbl = WatchedItem(
            "mbl",
            gather_function=self._get_mbl,
            validation_function=self._validate_mbl,
            on_fail_interval=None
        )
        self.item_updater.add_watched_item(self.mbl)

        self.flash_air = WatchedItem(
            "flash_air",
            gather_function=self._get_flash_air,
            write_function=self._set_flash_air,
            validation_function=lambda value: isinstance(value, bool)
        )
        self.item_updater.add_watched_item(self.flash_air)
        self.item_updater.set_value(self.flash_air, False)

        # Telemetry
        self.speed_multiplier = WatchedItem(
            "speed_multiplier",
            gather_function=self._get_speed_multiplier,
            write_function=self._set_speed_multiplier,
            validation_function=self._validate_percent,
            interval=FAST_POLL_INTERVAL)
        self.item_updater.add_watched_item(self.speed_multiplier)

        self.flow_multiplier = WatchedItem(
            "flow_multiplier",
            gather_function=self._get_flow_multiplier,
            write_function=self._set_flow_multiplier,
            validation_function=self._validate_percent,
            interval=FAST_POLL_INTERVAL)
        self.item_updater.add_watched_item(self.flow_multiplier)

        # Print info can be autoreported or polled

        # Only the progress gets an interval
        # Its gatherer sets all the other values manually while other
        # get set in cascade, converted from sooner acquired values
        self.print_progress = WatchedItem(
            "print_progress",
            gather_function=self._get_print_info,
            validation_function=self._validate_progress,
            write_function=self._set_print_progress
        )
        self.item_updater.add_watched_item(self.print_progress)

        self.progress_broken = WatchedItem("progress_broken")
        self.print_progress.validation_error_signal.connect(
            lambda: self.item_updater.set_value(self.progress_broken, True))
        self.print_progress.became_valid_signal.connect(
            lambda: self.item_updater.set_value(self.progress_broken, False
                                                ))
        self.item_updater.add_watched_item(self.progress_broken)

        # These two times remaining update together through this
        # convertor or whatever it is
        self.speed_adjusted_secs_remaining = WatchedItem(
            "speed_adjusted_secs_remaining",
            validation_function=self._validate_time_remaining,
            write_function=self._set_speed_adjusted_secs_remaining)
        self.item_updater.add_watched_item(self.speed_adjusted_secs_remaining)

        # Once this is set, the write function passes the value to the other
        # watched item
        self.speed_agnostic_mins_remaining = WatchedItem(
            "speed_agnostic_mins_remaining",
            validation_function=self._validate_time_remaining,
            write_function=self._get_speed_adjusted_mins_remaining
        )
        self.item_updater.add_watched_item(self.speed_agnostic_mins_remaining)

        # M27 results
        # These are sometimes auto reported, but due to some technical
        # limitations, I'm not able to read them when auto reported
        self.print_state = WatchedItem("print_state",
                                       gather_function=self._get_m27,
                                       interval=FAST_POLL_INTERVAL,
                                       on_fail_interval=SLOW_POLL_INTERVAL)
        self.item_updater.add_watched_item(self.print_state)

        # short (8.3) folder names, long file name (52 chars)
        self.mixed_path = WatchedItem("mixed_path")
        self.item_updater.add_watched_item(self.mixed_path)

        self.byte_position = WatchedItem("byte_position")
        self.item_updater.add_watched_item(self.byte_position)

        self.progress_from_bytes = WatchedItem(
            "progress_from_bytes",
            write_function=self._set_progress_from_bytes)
        self.byte_position.value_changed_signal.connect(
            self._get_progress_from_byte_position)
        self.item_updater.add_watched_item(self.progress_from_bytes)

        self.sd_seconds_printing = WatchedItem(
            "sd_seconds_printing",
            write_function=self._set_sd_seconds_printing)
        self.item_updater.add_watched_item(self.sd_seconds_printing)

        self.telemetry = WatchedGroup([
            self.speed_multiplier,
            self.flow_multiplier,
            self.print_progress,
            self.speed_adjusted_secs_remaining,
            self.speed_agnostic_mins_remaining,
            self.print_state,
            self.mixed_path,
            self.byte_position,
            self.progress_from_bytes,
            self.sd_seconds_printing
        ])

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

    def invalidate_telemetry(self):
        """Invalidates every value of Telemetry gathered by the poller"""
        self.item_updater.invalidate_group(self.telemetry)

    def invalidate_network_info(self):
        """Invalidates just the network info"""
        self.item_updater.invalidate(self.network_info)

    def invalidate_serial_number(self):
        """Invalidates just the serial number"""
        self.item_updater.invalidate(self.serial_number)

    def invalidate_mbl(self):
        """Invalidates the mbl_data, so it will get updated."""
        self.item_updater.invalidate(self.mbl)

    def polling_not_ok(self):
        """Stops polling of some values"""
        self.nozzle_diameter.interval = None
        self.flow_multiplier.interval = SLOW_POLL_INTERVAL
        self.speed_multiplier.interval = SLOW_POLL_INTERVAL
        self.print_progress.interval = SLOW_POLL_INTERVAL
        self.flash_air.interval = None

        self.item_updater.cancel_scheduled_invalidation(self.nozzle_diameter)
        self.item_updater.schedule_invalidation(self.flow_multiplier)
        self.item_updater.schedule_invalidation(self.speed_multiplier)
        self.item_updater.schedule_invalidation(self.print_progress)
        self.item_updater.cancel_scheduled_invalidation(self.flash_air)

    def polling_ok(self):
        """Re-starts polling of some values"""
        self.nozzle_diameter.interval = SLOW_POLL_INTERVAL
        self.flow_multiplier.interval = FAST_POLL_INTERVAL
        self.speed_multiplier.interval = FAST_POLL_INTERVAL
        self.print_progress.interval = None
        self.flash_air.interval = FLASH_AIR_INTERVAL

        self.item_updater.schedule_invalidation(self.nozzle_diameter)
        self.item_updater.schedule_invalidation(self.flow_multiplier)
        self.item_updater.schedule_invalidation(self.speed_multiplier)
        self.item_updater.cancel_scheduled_invalidation(self.print_progress)
        self.item_updater.schedule_invalidation(self.flash_air)

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

    def do_matchable(self, gcode, regex, to_front=False):
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

    def do_multimatch(self, gcode, regex, to_front=False):
        """Send an instruction with multiple lines as output"""
        instruction = enqueue_matchable(
            self.serial_queue, gcode, regex, to_front=to_front)
        wait_for_instruction(instruction, self.should_wait)
        matches = instruction.get_matches()
        if not matches:
            raise RuntimeError(f"There are no matches for {gcode}. "
                               f"That is weird.")
        return matches

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
        match = self.do_matchable("M862.2 Q",
                                  PRINTER_TYPE_REGEX,
                                  to_front=True)
        return int(match.group("code"))

    def _get_firmware_version(self):
        """Try to get firmware version from the printer."""
        match = self.do_matchable("PRUSA Fir", FW_REGEX, to_front=True)
        return match.group("version")

    def _get_nozzle_diameter(self):
        """Gets the printers nozzle diameter using M862.1 Q"""
        match = self.do_matchable("M862.1 Q", NOZZLE_REGEX, to_front=True)
        return float(match.group("size"))

    def _get_serial_number(self):
        """Returns the SN regex match"""
        match = self.do_matchable("PRUSA SN", SN_REGEX, to_front=True)
        return match.group("sn")

    def _get_job_id(self):
        """Gets the current job_id from the printer"""
        match = self.do_matchable("D3 Ax0D05 C4",
                                  D3_OUTPUT_REGEX,
                                  to_front=True)
        return int(match.group("data").replace(" ", ""), base=16)

    def _get_mbl(self):
        """Gets the current MBL data"""
        matches = self.do_multimatch("G81", MBL_REGEX, to_front=True)
        groups = matches[0].groupdict()

        data = {}
        if groups["no_mbl"] is None:
            num_x = int(groups["num_x"])
            num_y = int(groups["num_y"])
            data["shape"] = (num_x, num_y)
            data["data"] = []
            for i, match in enumerate(matches):
                if i == 0:
                    continue
                line = match.group("mbl_row")
                str_values = line.split()
                values = [float(val) for val in str_values]
                data["data"].append(values)
        return data

    def _get_flash_air(self):
        """Determines if the Flash Air functionality is on"""
        match = self.do_matchable("D3 Ax0fbb C1", D3_OUTPUT_REGEX)
        return match.group("data") == "01"

    def _get_print_mode(self):
        """Gets the print mode from the printer"""
        match = self.do_matchable("D3 Ax0fff C1",
                                  D3_OUTPUT_REGEX,
                                  to_front=True)
        index = int(match.group("data").replace(" ", ""), base=16)
        return PRINT_MODE_ID_PAIRING[index]

    def _get_speed_multiplier(self):
        match = self.do_matchable("M220", PERCENT_REGEX)
        return int(match.group("percent"))

    def _get_flow_multiplier(self):
        match = self.do_matchable("M221", PERCENT_REGEX)
        return int(match.group("percent"))

    def _get_print_info(self):
        """Polls the print info, but instead of returning it, it uses
        another method, that will eventually set it"""
        matches = self.do_multimatch("M73", PRINT_INFO_REGEX)
        self.print_info_handler(self, matches)

        raise SideEffectOnly()

    def _get_m27(self):
        """Polls M27, sets all values got from it manually,
        and returns its own"""
        matches = self.do_multimatch("M27 P", M27_OUTPUT_REGEX,
                                     to_front=True)

        if len(matches) >= 3:
            third_match = matches[2]
            self._parse_sd_seconds_printing(third_match.groupdict())

        if len(matches) >= 2:
            second_match = matches[1]
            self._parse_byte_position(second_match.groupdict())

        if len(matches) >= 1:
            first_match = matches[0]
            self._parse_mixed_path(first_match.groupdict())
            return self._parse_print_state(first_match.groupdict())

        raise RuntimeError("Failed to gather print info")

    @staticmethod
    def _parse_print_state(groups):
        """Parse a printer tracked state depending on which match group
        is present"""
        for group, state in PRINT_STATE_PAIRING.items():
            if groups[group] is not None:
                return state
        return None

    def _parse_mixed_path(self, groups):
        """Here we get a printer print state and if printing
        a mixed length path of the file being printed from the SD card"""
        if groups["sdn_lfn"] is not None:
            self.item_updater.set_value(self.mixed_path, groups["sdn_lfn"])

    def _parse_byte_position(self, groups):
        """Gets the byte position of the file being sd printed"""
        byte_position = (int(groups["current"]), int(groups["sum"]))
        self.item_updater.set_value(self.byte_position, byte_position)

    def _parse_sd_seconds_printing(self, groups):
        """Gets the time for which we've been printing already"""
        printing_time = timedelta(hours=int(groups["hours"]),
                                  minutes=int(groups["minutes"]))
        self.item_updater.set_value(self.sd_seconds_printing,
                                    printing_time.seconds)

    def _get_progress_from_byte_position(self, value):
        """Gets a progress value out of byte position"""
        current, total = value
        progress = int((current / total) * 100)
        self.item_updater.set_value(self.progress_from_bytes, progress)

    def print_info_handler(self, sender, matches: List[re.Match]):
        """One special handler supporting polling and spontaneous
        unsolicited reporting of progress and minutes remaining"""
        assert sender is not None

        class PrintInfo:
            """A shell for print stat data"""
            def __init__(self):
                self.valid = False
                self.progress = -1
                self.remaining = -1

        silent, normal = PrintInfo(), PrintInfo()
        for match in matches:
            groups = match.groupdict()
            info = PrintInfo()
            info.progress = int(groups["progress"])
            info.remaining = int(groups["remaining"])
            try:
                info.valid = self._validate_progress(info.progress)
            except ValueError:
                pass

            if match.group("mode") == PrintMode.SILENT.value:
                silent = info
            elif match.group("mode") == PrintMode.NORMAL.value:
                normal = info

        use_normal = False

        if self.print_mode.value == PrintMode.NORMAL:
            if not normal.valid and silent.valid:
                log.warning("We are in normal mode but only silent print "
                            "tracking info is valid. That's weird")
            else:
                use_normal = True
        elif not silent.valid:
            # The file must have been sliced in a semi-compatible slicer
            use_normal = True
        # Yes, this solution ignores MK25 auto mode. Sorry

        # Gladly reports even the wrong values
        # just to set off handlers that depend on the validation failing
        if use_normal:
            self.item_updater.set_value(self.print_progress, normal.progress)
            self.item_updater.set_value(self.speed_agnostic_mins_remaining,
                                        normal.remaining)
        else:
            self.item_updater.set_value(self.print_progress, silent.progress)
            self.item_updater.set_value(self.speed_agnostic_mins_remaining,
                                        silent.remaining)

    # -- From other watched items --

    def _get_speed_adjusted_mins_remaining(self, value):
        """
        The minutes remaining are naively multiplied by the inverse of the
        speed multiplier
        """
        speed_agnostic_mins_remaining = value
        if self.model.latest_telemetry.speed is not None:
            speed_multiplier = self.model.latest_telemetry.speed / 100
        else:
            speed_multiplier = 1
        inverse_speed_multiplier = 1 / speed_multiplier

        mins_remaining = int(speed_agnostic_mins_remaining *
                             inverse_speed_multiplier)
        log.debug("Mins without speed considering %s, mins otherwise %s",
                  speed_agnostic_mins_remaining, mins_remaining)
        secs_remaining = mins_remaining * 60
        self.item_updater.set_value(self.speed_adjusted_secs_remaining,
                                    secs_remaining)

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
        if Version(without_buildnumber) < MINIMAL_FIRMWARE:
            raise ValueError("The printer firmware is outdated")
        return True

    @staticmethod
    def _validate_mbl(value):
        """Validates the mesh bed leveling data"""
        num_x, num_y = value["shape"]
        data = value["data"]
        if len(data) != num_y:
            raise ValueError(f"The mbl data matrix was reported to have "
                             f"{num_y} rows, but only {len(data)} "
                             f"were observed")
        for i, row in enumerate(data):
            if len(row) != num_x:
                raise ValueError(f"The mbl data matrix was reported to have "
                                 f"{num_x} values per row, but only "
                                 f"{len(row)} were observed on row with"
                                 f" index {i}.")
        return True

    @staticmethod
    def _validate_percent(value):
        """Validates the speed multiplier as well as the flow rate"""
        if not 0 <= value <= 999:
            raise ValueError("The speed multiplier or flow rate is not "
                             "between 0 and 999")
        return True

    @staticmethod
    def _validate_progress(value):
        """Validates progress"""
        if not 0 <= value <= 100:
            raise ValueError("The progress value is outside 0 and 100, this is"
                             " usually a perfectly normal behaviour")
        return True

    @staticmethod
    def _validate_time_remaining(value):
        """
        Validates both time values because negative time
        remaining is impossible
        """
        if value < 0:
            raise ValueError("There cannot be negative time remaining")
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

    def _set_flash_air(self, value):
        """Passes the flash air value to sd updater"""
        self.sd_card.set_flash_air(value)

    def _set_temps(self, value):
        """Write the temps to the model"""
        telemetry = Telemetry(temp_nozzle=float(value["ntemp"]))
        if "btemp" in value:
            telemetry.temp_bed = float(value["btemp"])
        if "set_ntemp" in value and "set_btemp" in value:
            telemetry.target_nozzle = float(value["set_ntemp"])
            telemetry.target_bed = float(value["set_btemp"])
        self.telemetry_passer.set_telemetry(telemetry)

    def _set_positions(self, value):
        """Write the position values to the model"""
        self.telemetry_passer.set_telemetry(
            Telemetry(axis_x=float(value["x"]),
                      axis_y=float(value["y"]),
                      axis_z=float(value["z"])))

    def _set_fans(self, value):
        """Write the fan values to the model"""
        self.telemetry_passer.set_telemetry(
            Telemetry(fan_extruder=int(value["extruder_rpm"]),
                      fan_print=int(value["print_rpm"]),
                      target_fan_extruder=int(value["extruder_power"]),
                      target_fan_print=int(value["print_power"])))

    def _set_speed_multiplier(self, value):
        """Write the speed multiplier to model"""
        self.telemetry_passer.set_telemetry(Telemetry(speed=value))

    def _set_flow_multiplier(self, value):
        """Write the flow multiplier to model"""
        self.telemetry_passer.set_telemetry(Telemetry(flow=value))

    def _set_print_progress(self, value):
        """Write the progress"""
        self.telemetry_passer.set_telemetry(Telemetry(progress=value))

    def _set_speed_adjusted_secs_remaining(self, value):
        """sets the time remaining adjusted for speed"""
        self.telemetry_passer.set_telemetry(Telemetry(time_estimated=value))

    def _set_sd_seconds_printing(self, value):
        """sets the time we've been printing"""
        self.telemetry_passer.set_telemetry(Telemetry(time_printing=value))

    def _set_progress_from_bytes(self, value):
        """
        Sets the progress gathered from the byte position,
        But only if it's broken in the printer
        """
        if self.progress_broken.value:
            log.debug(
                "SD print has no inbuilt percentage tracking, "
                "falling back to getting progress from byte "
                "position in the file. "
                "Progress: %s%% Byte %s/%s", value,
                self.byte_position.value[0], self.byte_position.value[1])
            self.telemetry_passer.set_telemetry(Telemetry(progress=value))


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
