"""
Uses info updater to keep up with the printer info.
Hope I can get most of printer polling to use this mechanism.
"""
import itertools
import logging
import re
from datetime import timedelta
from typing import List

from prusa.connect.printer import Printer
from prusa.connect.printer.conditions import CondState

from ..conditions import FW, ID, JOB_ID, SN
from ..const import (
    FAST_POLL_INTERVAL,
    PRINT_STATE_PAIRING,
    PRINTER_TYPES,
    QUIT_INTERVAL,
    SLOW_POLL_INTERVAL,
    VERY_SLOW_POLL_INTERVAL,
)
from ..serial.helpers import enqueue_matchable, wait_for_instruction
from ..serial.serial_parser import ThreadedSerialParser
from ..serial.serial_queue import SerialQueue
from ..util import get_d3_code, make_fingerprint
from .filesystem.sd_card import SDCard
from .job import Job
from .model import Model
from .polling_items import (
    ActiveSheetItem,
    FirmwareVersionItem,
    FlashAirItem,
    FlowMultiplierItem,
    MBLItem,
    NetworkInfoItem,
    NozzleDiameterItem,
    PrinterTypeItem,
    PrintModeItem,
    SerialNumberItem,
    SheetSettingsItem,
    SpeedMultiplierItem,
    TotalFilamentItem,
    TotalPrintTimeItem,
)
from .structures.enums import EEPROMParams, PrintMode
from .structures.item_updater import (
    ItemUpdater,
    SideEffectOnly,
    WatchedGroup,
    WatchedItem,
)
from .structures.model_classes import (
    Telemetry,
)
from .structures.regular_expressions import (
    D3_OUTPUT_REGEX,
    M27_OUTPUT_REGEX,
    PRINT_INFO_REGEX,
)
from .telemetry_passer import TelemetryPasser

log = logging.getLogger(__name__)


class InfoGroup(WatchedGroup):
    """A WatchedGroup with a flag for sending"""

    def __init__(self, *args, **kwargs):
        self.to_send = False
        super().__init__(*args, **kwargs)

    def mark_for_send(self):
        """Marks printer info for sending"""
        self.to_send = True


# TODO: Don't like how parsing and result signal handling are mixed
class PrinterPolling:
    """Sets up the tracked values for info_updater"""

    quit_interval = QUIT_INTERVAL

    # pylint: disable=too-many-statements, too-many-arguments
    def __init__(self, serial_queue: SerialQueue,
                 serial_parser: ThreadedSerialParser,
                 printer: Printer, model: Model,
                 telemetry_passer: TelemetryPasser,
                 job: Job, sd_card: SDCard) -> None:
        self.item_updater = ItemUpdater()
        self.serial_queue = serial_queue
        self.serial_parser = serial_parser
        self.printer = printer
        self.model = model
        self.telemetry_passer = telemetry_passer
        self.job = job
        self.sd_card = sd_card

        # Printer info (for init and SEND_INFO)
        self.network_info = NetworkInfoItem(self.serial_queue, self.model)
        self.network_info.refreshed_signal.connect(
            self._set_network_info,
        )

        self.printer_type = PrinterTypeItem(self.serial_queue)
        self.printer_type.refreshed_signal.connect(self._set_printer_type)
        self.printer_type.became_valid_signal.connect(
            self._printer_type_became_valid)
        self.printer_type.val_err_timeout_signal.connect(
            lambda _: self._set_id_condition(CondState.NOK), weak=False)

        self.firmware_version = FirmwareVersionItem(self.serial_queue)
        self.firmware_version.refreshed_signal.connect(
            self._set_firmware_version)
        self.firmware_version.became_valid_signal.connect(
            self._firmware_version_became_valid)
        self.firmware_version.val_err_timeout_signal.connect(
            lambda _: self._set_fw_condition(CondState.NOK), weak=False)

        self.nozzle_diameter = NozzleDiameterItem(self.serial_queue)
        self.nozzle_diameter.refreshed_signal.connect(
            self._set_nozzle_diameter)
        self.nozzle_diameter.interval = 10

        self.serial_number = SerialNumberItem(self.serial_queue, self.model)
        self.serial_number.timeout = 25
        self.serial_number.refreshed_signal.connect(
            self._set_serial_number)
        self.serial_number.became_valid_signal.connect(
            lambda _: self._set_sn_condition(CondState.OK), weak=False)
        self.serial_number.val_err_timeout_signal.connect(
            lambda _: self._set_sn_condition(CondState.NOK), weak=False)

        self.sheet_settings = SheetSettingsItem(self.serial_queue)

        self.active_sheet = ActiveSheetItem(self.serial_queue)

        self.printer_info = InfoGroup([
            self.network_info, self.printer_type, self.firmware_version,
            self.nozzle_diameter, self.serial_number, self.sheet_settings,
            self.active_sheet,
        ])

        for item in self.printer_info:
            self.item_updater.add_item(item, start_tracking=False)

        for item in self.printer_info:
            if item.name in {"active_sheet", "sheet_settings"}:
                continue

            item.value_changed_signal.connect(
                lambda value: self.printer_info.mark_for_send(), weak=False)

        self.printer_info.became_valid_signal.connect(
            self._printer_info_became_valid)

        # Other stuff

        self.job_id = WatchedItem(
            "job_id",
            gather_function=self._get_job_id,
        )
        self.job_id.refreshed_signal.connect(self._set_job_id)
        self.job_id.became_valid_signal.connect(
            lambda _: self._set_job_id_condition(CondState.OK), weak=False)
        self.job_id.val_err_timeout_signal.connect(
            lambda _: self._set_job_id_condition(CondState.NOK), weak=False)

        self.print_mode = PrintModeItem(
            self.serial_queue,
        )

        self.mbl = MBLItem(self.serial_queue)

        self.flash_air = FlashAirItem(self.serial_queue)
        self.flash_air.refreshed_signal.connect(self._set_flash_air)

        self.other_stuff = WatchedGroup([
            self.job_id, self.print_mode, self.mbl, self.flash_air])

        for item in self.other_stuff:
            self.item_updater.add_item(item, start_tracking=False)

        self.item_updater.set_value(self.flash_air, False)
        # Make silent the default for when we fail to get the value in time
        self.item_updater.set_value(self.print_mode, PrintMode.SILENT)

        # Telemetry
        self.speed_multiplier = SpeedMultiplierItem(self.serial_queue)
        self.speed_multiplier.refreshed_signal.connect(
            self._set_speed_multiplier)

        self.flow_multiplier = FlowMultiplierItem(self.serial_queue)
        self.flow_multiplier.refreshed_signal.connect(
            self._set_flow_multiplier)

        # Print info can be autoreported or polled

        # Only the progress gets an interval
        # Its gatherer sets all the other values manually while other
        # get set in cascade, converted from sooner acquired values
        self.print_progress = WatchedItem(
            "print_progress",
            gather_function=self._get_print_info,
            validation_function=self._validate_progress,
        )
        self.print_progress.refreshed_signal.connect(
            self._set_print_progress)

        self.progress_broken = WatchedItem("progress_broken")
        self.print_progress.validation_error_signal.connect(
            lambda _: self.set_progress_broken(True), weak=False)
        self.print_progress.became_valid_signal.connect(
            lambda _: self.set_progress_broken(False), weak=False)

        self.time_remaining = WatchedItem(
            "time_remaining",
            validation_function=self._validate_time_till)
        self.time_remaining.refreshed_signal.connect(
            self._set_time_remaining)

        self.time_broken = WatchedItem("time_broken")
        self.time_remaining.validation_error_signal.connect(
            lambda _: self.set_time_broken(True), weak=False)
        self.time_remaining.value_changed_signal.connect(
            lambda _: self.set_time_broken(False), weak=False)

        self.filament_change_in = WatchedItem(
            "filament_change_in",
            validation_function=self._validate_time_till,
            on_fail_interval=None,
        )
        self.filament_change_in.refreshed_signal.connect(
            self._set_filament_change_in)

        self.filament_change_in.validation_error_signal.connect(
            lambda _: self.telemetry_passer.reset_value("filament_change_in"),
            weak=False)

        self.inaccurate_estimates = WatchedItem("inaccurate_estimates")
        self.time_broken.value_changed_signal.connect(
            lambda _: self._infer_estimate_accuracy(), weak=False)
        self.speed_multiplier.value_changed_signal.connect(
            lambda _: self._infer_estimate_accuracy(), weak=False)
        self.inaccurate_estimates.value_changed_signal.connect(
            self._set_inaccurate_estimates,
        )

        # --- M27 results ---
        # These are sometimes auto reported, but due to some technical
        # limitations, I'm not able to read them when auto reported
        self.print_state = WatchedItem("print_state",
                                       gather_function=self._get_m27,
                                       interval=FAST_POLL_INTERVAL,
                                       on_fail_interval=SLOW_POLL_INTERVAL)

        # short (8.3) folder names, long file name (52 chars)
        self.mixed_path = WatchedItem("mixed_path")

        self.byte_position = WatchedItem("byte_position")

        self.progress_from_bytes = WatchedItem("progress_from_bytes")
        self.progress_from_bytes.refreshed_signal.connect(
            self._set_progress_from_bytes)
        self.byte_position.value_changed_signal.connect(
            self._get_progress_from_byte_position)

        self.sd_seconds_printing = WatchedItem("sd_seconds_printing")
        self.sd_seconds_printing.refreshed_signal.connect(
            self._set_sd_seconds_printing)

        self.time_remaining_guesstimate = WatchedItem(
            "time_remaining_guesstimate")
        self.time_remaining_guesstimate.refreshed_signal.connect(
            self._set_time_remaining_guesstimate)
        self.byte_position.value_changed_signal.connect(
            self._guess_time_remaining)
        self.sd_seconds_printing.value_changed_signal.connect(
            self._guess_time_remaining)

        # --- Statistics ---

        self.total_filament = TotalFilamentItem(self.serial_queue)
        self.total_filament.refreshed_signal.connect(
            self._set_total_filament)

        self.total_print_time = TotalPrintTimeItem(self.serial_queue)
        self.total_print_time.refreshed_signal.connect(
            self._set_total_print_time)

        self.telemetry = WatchedGroup([
            self.speed_multiplier,
            self.flow_multiplier,
            self.print_progress,
            self.time_remaining,
            self.filament_change_in,
            self.print_state,
            self.mixed_path,
            self.byte_position,
            self.progress_from_bytes,
            self.time_remaining_guesstimate,
            self.sd_seconds_printing,
            self.total_filament,
            self.total_print_time,
            self.progress_broken,
            self.time_broken,
            self.inaccurate_estimates,
        ])

        for item in self.telemetry:
            self.item_updater.add_item(item, start_tracking=False)

        self.invalidate_printer_info()

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
        """Invalidates all unnecessary watched items"""
        for item in itertools.chain(self.telemetry, self.other_stuff,
                                    self.printer_info):
            self.item_updater.disable(item)

        self.item_updater.enable(self.printer_type)

    def invalidate_network_info(self):
        """Invalidates just the network info"""
        self.item_updater.invalidate(self.network_info)

    def invalidate_serial_number(self):
        """Invalidates just the serial number"""
        self.item_updater.invalidate(self.serial_number)

    def invalidate_mbl(self):
        """Invalidates the mbl_data, so it will get updated."""
        self.item_updater.invalidate(self.mbl)

    def invalidate_statistics(self):
        """Invalidates the statistics, so they get updated."""
        self.item_updater.invalidate(self.total_filament)
        self.item_updater.invalidate(self.total_print_time)

    def schedule_printer_type_invalidation(self):
        """Marks printer_type gor gathering in X seconds"""
        self.item_updater.schedule_invalidation(self.printer_type,
                                                SLOW_POLL_INTERVAL)

    def _change_interval(self, item: WatchedItem, interval):
        """Changes the item interval and schedules depending on the new one"""
        item.interval = interval
        if interval is None:
            self.item_updater.cancel_scheduled_invalidation(item)
        else:
            self.item_updater.schedule_invalidation(item)

    def polling_not_ok(self):
        """Stops polling of some values"""
        self._change_interval(self.nozzle_diameter, None)
        self._change_interval(self.flow_multiplier, SLOW_POLL_INTERVAL)
        self._change_interval(self.speed_multiplier, SLOW_POLL_INTERVAL)
        self._change_interval(self.print_progress, SLOW_POLL_INTERVAL)
        self._change_interval(self.sheet_settings, None)
        self._change_interval(self.active_sheet, None)
        self._change_interval(self.flash_air, None)

    def polling_ok(self):
        """Re-starts polling of some values"""
        self._change_interval(self.nozzle_diameter, SLOW_POLL_INTERVAL)
        self._change_interval(self.flow_multiplier, FAST_POLL_INTERVAL)
        self._change_interval(self.speed_multiplier, FAST_POLL_INTERVAL)
        self._change_interval(self.print_progress, None)
        self._change_interval(self.sheet_settings, VERY_SLOW_POLL_INTERVAL)
        self._change_interval(self.active_sheet, SLOW_POLL_INTERVAL)
        self._change_interval(self.flash_air, VERY_SLOW_POLL_INTERVAL)

    # --- Startup mechanism ---

    def _printer_type_became_valid(self, _):
        """Printer type became valid,
        set the condition and enable the fw check"""
        self.item_updater.enable(self.firmware_version)
        self._set_id_condition(CondState.OK)

    def _firmware_version_became_valid(self, _):
        """Firmware version became valid,
        enable polling of the rest of the info"""
        for item in self.printer_info:
            self.item_updater.enable(item)
        self._set_fw_condition(CondState.OK)

    def _printer_info_became_valid(self, _):
        """Printer info became valid, we can start looking at telemetry
        and other stuff"""
        self._send_info_if_changed()
        for item in itertools.chain(self.telemetry, self.other_stuff):
            self.item_updater.enable(item)

    def _send_info_if_changed(self):
        """Sends printer info if a value change marked it for sending"""
        # This relies on update being called after became_valid_signal
        if self.printer_info.valid and self.printer_info.to_send:
            self.printer.event_cb(**self.printer.get_info())
            self.printer_info.to_send = False

    # --- end of startup mechanism ---

    def _infer_estimate_accuracy(self):
        """Looks at the current state of things and infers whether the
        time estimates are accurate or not"""
        if self.time_broken.value in {None, True}:
            self.item_updater.set_value(self.inaccurate_estimates, True)
        elif self.speed_multiplier.value != 100:
            self.item_updater.set_value(self.inaccurate_estimates, True)
        else:
            self.item_updater.set_value(self.inaccurate_estimates, False)

    def ensure_job_id(self):
        """This is an oddball, I don't have anything able to ensure the job_id
        stays in sync, I cannot wait for it, that would block the read thread
        I cannot just write it either, I wouldn't know if it failed."""
        def job_became_valid(item):
            self.job_id.became_valid_signal.disconnect(job_became_valid)
            if self.model.job.job_id != item.value:
                log.warning(
                    "Job id on the printer: %s differs from the local"
                    " one: %s!", item.value, self.model.job.job_id)
                self.job.write()
                self.ensure_job_id()

        self.item_updater.schedule_invalidation(self.job_id, interval=1)
        self.job_id.became_valid_signal.connect(job_became_valid)

    # -- Helpers --
    def should_wait(self):
        """Gather helper returning if the component is still running"""
        return self.item_updater.running

    # pylint: disable=duplicate-code
    def do_matchable(self, gcode, regex, to_front=False):
        """Analog to the command one, as the getters do this
        over and over again"""
        instruction = enqueue_matchable(self.serial_queue,
                                        gcode,
                                        regex,
                                        to_front=to_front)
        wait_for_instruction(instruction, self.should_wait)
        match = instruction.match()
        if match is None:
            raise RuntimeError("Printer responded with something unexpected")
        return match

    # pylint: disable=duplicate-code
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

    # --- Gatherers ---
    def _get_job_id(self):
        """Gets the current job_id from the printer"""
        match = self.do_matchable(
            get_d3_code(*EEPROMParams.JOB_ID.value),
            D3_OUTPUT_REGEX, to_front=True)
        return int(match.group("data").replace(" ", ""), base=16)

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

    def _guess_time_remaining(self, _):
        """Tracking is nonexistant, guess a time_remaining value
        I'd just write out "On Friday" but people don't like that"""
        if not self.time_broken.value:
            return
        if not self.sd_seconds_printing.valid:
            return
        sd_seconds_printing = self.sd_seconds_printing.value
        if self.progress_broken.value:
            if not self.progress_from_bytes.valid:
                return
            progress = self.progress_from_bytes.value
        else:
            if not self.print_progress.valid:
                return
            progress = self.print_progress.value
        if progress == 0:
            return
        percent_remaining = 100 - progress
        multiplier = percent_remaining / progress
        guesstimation = sd_seconds_printing * multiplier
        self.item_updater.set_value(self.time_remaining_guesstimate,
                                    guesstimation)

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
                self.filament_change_in = -1

        silent, normal = PrintInfo(), PrintInfo()
        for match in matches:
            groups = match.groupdict()
            info = PrintInfo()
            info.progress = int(groups["progress"])
            # Convert both time values to seconds and adjust by print speed
            secs_remaining_unadjusted = int(groups["remaining"]) * 60
            info.remaining = self._speed_adjust_time_value(
                secs_remaining_unadjusted)
            secs_change_in_unadjusted = int(groups["change_in"]) * 60
            info.filament_change_in = self._speed_adjust_time_value(
                secs_change_in_unadjusted)

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
            self.item_updater.set_value(self.time_remaining, normal.remaining)
            self.item_updater.set_value(self.filament_change_in,
                                        normal.filament_change_in)
        else:
            self.item_updater.set_value(self.print_progress, silent.progress)
            self.item_updater.set_value(self.time_remaining, silent.remaining)
            self.item_updater.set_value(self.filament_change_in,
                                        silent.filament_change_in)

    # -- From other watched items --
    def _speed_adjust_time_value(self, value):
        """Multiplies tha value by the inverse of the speed multiplier"""
        if self.model.latest_telemetry.speed is not None:
            speed_multiplier = self.model.latest_telemetry.speed / 100
        else:
            speed_multiplier = 1
        inverse_speed_multiplier = 1 / speed_multiplier

        adjusted_value = int(value * inverse_speed_multiplier)
        log.debug("Secs without speed scaling %s, secs otherwise %s",
                  value, adjusted_value)
        return adjusted_value

    # -- Validators --

    @staticmethod
    def _validate_progress(value):
        """Validates progress"""
        if not 0 <= value <= 100:
            raise ValueError("The progress value is outside 0 and 100, this is"
                             " usually a perfectly normal behaviour")
        return True

    @staticmethod
    def _validate_time_till(value):
        """Validates both time values because negative time till something
        is impossible"""
        if value < 0:
            raise ValueError("There cannot be negative time till something")
        return True

    # -- Setters --
    def _set_network_info(self, value):
        """Sets network info"""
        self.printer.network_info = value

    def _set_printer_type(self, value):
        """Do not try and overwrite the printer type, that would
        raise an error"""
        if self.printer.type is None:
            self.printer.type = PRINTER_TYPES[value]

    def _set_firmware_version(self, value):
        """It's a setter, what am I expected to write here?
        Sets the firmware version duh"""
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

    def _set_speed_multiplier(self, value):
        """Write the speed multiplier to model"""
        self.telemetry_passer.set_telemetry(Telemetry(speed=value))

    def _set_flow_multiplier(self, value):
        """Write the flow multiplier to model"""
        self.telemetry_passer.set_telemetry(Telemetry(flow=value))

    def _set_print_progress(self, value):
        """Write the progress"""
        self.telemetry_passer.set_telemetry(Telemetry(progress=value))

    def _set_time_remaining(self, value):
        """Sets the time remaining adjusted for speed"""
        self.telemetry_passer.set_telemetry(Telemetry(time_remaining=value))

    def _set_filament_change_in(self, value):
        """Write the filament change in"""
        self.telemetry_passer.set_telemetry(
            Telemetry(filament_change_in=value))

    def _set_sd_seconds_printing(self, value):
        """sets the time we've been printing"""
        self.telemetry_passer.set_telemetry(Telemetry(time_printing=value))

    def _set_progress_from_bytes(self, value):
        """Sets the progress gathered from the byte position,
        But only if it's broken in the printer"""
        if self.progress_broken.value:
            log.debug(
                "SD print has no inbuilt percentage tracking, "
                "falling back to getting progress from byte "
                "position in the file. "
                "Progress: %s%% Byte %s/%s", value,
                self.byte_position.value[0], self.byte_position.value[1])
            self.telemetry_passer.set_telemetry(Telemetry(progress=value))

    def _set_time_remaining_guesstimate(self, value):
        """Set the guesstimated time remaining if the real one's broken"""
        if self.time_broken.value:
            log.debug("SD print has no time remaining tracking. "
                      "Guesstimating")
            self.telemetry_passer.set_telemetry(
                Telemetry(time_remaining=value))

    def _set_total_filament(self, value):
        """Write the total filament used into the model"""
        self.telemetry_passer.set_telemetry(Telemetry(total_filament=value))

    def _set_total_print_time(self, value):
        """Write the total print time into the model"""
        self.telemetry_passer.set_telemetry(Telemetry(total_print_time=value))

    def _set_inaccurate_estimates(self, value):
        """Write whether out time estimates are inaccurate into the model"""
        self.telemetry_passer.set_telemetry(
            Telemetry(inaccurate_estimates=value))

    # -- Signal handlers --

    def set_progress_broken(self, value: bool):
        """Sets progress as being broken or functioning normally"""
        self.item_updater.set_value(self.progress_broken, value)

    def set_time_broken(self, value: bool):
        """Sets time_remaining as being broken or functioning normally"""
        self.item_updater.set_value(self.time_broken, value)

    @staticmethod
    def _set_sn_condition(state: CondState):
        """Needs to exist because we cannot assign in lambdas"""
        SN.state = state

    @staticmethod
    def _set_id_condition(state: CondState):
        """Needs to exist because we cannot assign in lambdas"""
        ID.state = state

    @staticmethod
    def _set_fw_condition(state: CondState):
        """Needs to exist because we cannot assign in lambdas"""
        FW.state = state

    @staticmethod
    def _set_job_id_condition(state: CondState):
        """Needs to exist because we cannot assign in lambdas"""
        JOB_ID.state = state
