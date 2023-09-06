"""
Uses info updater to keep up with the printer info.
Hope I can get most of printer polling to use this mechanism.
"""
import itertools
import logging

from prusa.connect.printer import Printer
from prusa.connect.printer.conditions import CondState

from ..conditions import FW, ID, JOB_ID, SN
from ..const import (
    FAST_POLL_INTERVAL,
    PRINTER_TYPES,
    QUIT_INTERVAL,
    SLOW_POLL_INTERVAL,
    VERY_SLOW_POLL_INTERVAL,
)
from ..serial.serial_parser import ThreadedSerialParser
from ..serial.serial_queue import SerialQueue
from ..util import make_fingerprint
from .filesystem.sd_card import SDCard
from .job import Job
from .model import Model
from .polling_items import (
    ActiveSheetItem,
    FirmwareVersionItem,
    FlashAirItem,
    FlowPercentItem,
    JobIdItem,
    M27Item,
    M73Item,
    MBLItem,
    NetworkInfoItem,
    NozzleDiameterItem,
    PrinterTypeItem,
    PrintModeItem,
    SerialNumberItem,
    SheetSettingsItem,
    SpeedPercentItem,
    TotalFilamentItem,
    TotalPrintTimeItem,
)
from .structures.enums import PrintMode
from .structures.item_updater import (
    ItemUpdater,
    WatchedGroup,
    WatchedItem,
)
from .structures.model_classes import (
    ProcessedPrinterData,
    Telemetry,
)
from .telemetry_passer import TelemetryPasser

log = logging.getLogger(__name__)


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
        self.model.processed_printer = ProcessedPrinterData()
        self.data = self.model.processed_printer
        self.telemetry_passer = telemetry_passer
        self.job = job
        self.sd_card = sd_card

        # TODO: Validation errors might be broken
        # Printer info (for init and SEND_INFO)
        item_args = (self.serial_queue, self.model)
        self.network_info = NetworkInfoItem(*item_args)
        self.printer_type = PrinterTypeItem(*item_args)
        self.firmware_version = FirmwareVersionItem(*item_args)
        self.nozzle_diameter = NozzleDiameterItem(*item_args)
        self.serial_number = SerialNumberItem(*item_args)
        self.sheet_settings = SheetSettingsItem(*item_args)
        self.active_sheet = ActiveSheetItem(*item_args)
        self.print_mode = PrintModeItem(*item_args)
        self.mbl = MBLItem(*item_args)
        self.flash_air = FlashAirItem(*item_args)
        self.job_id = JobIdItem(*item_args)
        self.flow_percent = FlowPercentItem(*item_args)
        self.speed_percent = SpeedPercentItem(*item_args)
        # Print info can be auto-reported or polled
        self.m73 = M73Item(*item_args)
        # These are sometimes auto reported, but due to technical
        # limitations, I'm not able to read them when auto reported
        self.m27 = M27Item(*item_args)
        self.total_filament = TotalFilamentItem(*item_args)
        self.total_print_time = TotalPrintTimeItem(*item_args)

        self.printer_info = WatchedGroup([
            self.network_info,
            self.printer_type,
            self.firmware_version,
            self.nozzle_diameter,
            self.serial_number,
            self.sheet_settings,
            self.active_sheet,
        ])

        self.telemetry = WatchedGroup([
            self.speed_percent,
            self.flow_percent,
            self.m73,
            self.m27,
            self.total_filament,
            self.total_print_time,
        ])

        self.other_stuff = WatchedGroup([
            self.job_id,
            self.print_mode,
            self.mbl,
            self.flash_air,
        ])

        # --- Printer info sending ---
        self.model.raw_printer.value_changed_connect([
                "network_info",
                "firmware_version",
                "nozzle_diameter",
                "sheet_settings",
                "active_sheet",
            ], self._printer_info_changed)
        self.data.value_changed_connect(
            ["printer_type", "serial_number"],
            self._printer_info_changed)
        self.data.value_changed_connect(
            "printer_info_complete", self._set_printer_info_complete)

        # --- Printer info ---
        self.data.value_changed_connect(
            "printer_type", self._set_printer_type)
        self.data.value_changed_connect(
            "serial_number", self._set_serial_number)
        self.model.raw_printer.value_changed_connect(
            "firmware_version", self._set_firmware_version)
        self.model.raw_printer.value_changed_connect(
            "nozzle_diameter", self._set_nozzle_diameter)
        self.model.raw_printer.value_changed_connect(
            "network_info", self._set_network_info)

        # --- Other stuff ---
        self.model.raw_printer.value_refreshed_connect(
            "flash_air", self._set_flash_air)

        # --- Telemetry ---
        self.model.raw_printer.value_refreshed_connect(
            "total_filament",
            self._telemetry_passer("total_filament"), weak=False)
        self.model.raw_printer.value_refreshed_connect(
            "total_print_time",
            self._telemetry_passer("total_print_time"), weak=False)
        self.model.raw_printer.value_refreshed_connect(
            "speed_percent",
            self._telemetry_passer("speed"), weak=False)
        self.model.raw_printer.value_refreshed_connect(
            "flow_percent",
            self._telemetry_passer("flow"), weak=False)
        self.data.value_refreshed_connect(
            "progress",
            self._telemetry_passer("progress"), weak=False)
        self.data.value_refreshed_connect(
            "time_remaining",
            self._telemetry_passer("time_remaining"), weak=False)
        self.data.value_refreshed_connect(
            "printer_filament_change_in",
            self._telemetry_passer("filament_change_in"), weak=False)
        self.data.value_refreshed_connect(
            "inaccurate_estimates",
            self._telemetry_passer("inaccurate_estimates"), weak=False)

        # --- Processing ---
        self.model.raw_printer.value_changed_connect(
            "printer_type", self._process_printer_type)
        self.model.raw_printer.value_changed_connect(
            "serial_number", self._process_serial_number)
        self.model.raw_printer.value_changed_connect(
            "byte_position", self._get_progress_from_byte_position)
        self.data.value_changed_connect(
            "printer_time_remaining", self._infer_estimate_accuracy)
        self.model.raw_printer.value_changed_connect(
            "speed_percent", self._infer_estimate_accuracy)
        self.model.raw_printer.value_changed_connect(
            "print_stats", self._process_print_stats)
        self.model.raw_printer.value_changed_connect(
            "sd_seconds_printing", self._guess_time_remaining)
        self.data.value_changed_connect(
            ["progress", "printer_time_remaining"],
            self._guess_time_remaining)
        self.model.file_printer.value_changed_connect(
            "time_printing", self._process_time_printing)
        self.model.raw_printer.value_changed_connect(
            "sd_seconds_printing", self._process_time_printing)
        self.data.value_changed_connect(
            "printer_progress", self._process_progress)
        self.data.value_changed_connect(
            "progress_from_bytes", self._process_progress)
        self.data.value_changed_connect(
            ["time_remaining_estimate", "printer_time_remaining"],
            self._process_time_remaining)
        self.model.raw_printer.value_refreshed_connect(
            "job_id", self._process_job_id)

        for item in itertools.chain(self.printer_info, self.telemetry,
                                    self.other_stuff):
            self.item_updater.add_item(item, start_tracking=False)

        self.reset_polling()

    def start(self):
        """Starts the item updater"""
        self.item_updater.start()

    def stop(self):
        """Stops the item updater"""
        self.item_updater.stop()

    def wait_stopped(self):
        """Waits for the item updater to stop"""
        self.item_updater.wait_stopped()

    def reset_polling(self):
        """Re gathers everything as if we were just starting up"""
        for item in itertools.chain(self.telemetry, self.other_stuff,
                                    self.printer_info):
            self.item_updater.disable(item)

        for field in self.model.raw_printer.model_fields:
            setattr(self.model.raw_printer, field, None)
        for field in self.data.model_fields:
            setattr(self.data, field, None)

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

    def invalidate_job_id(self):
        """Invalidates the job id, so it gets updated."""
        self.item_updater.invalidate(self.job_id)

    def schedule_printer_type_invalidation(self):
        """Marks printer_type gor gathering in X seconds"""
        self.item_updater.schedule_invalidation(self.printer_type,
                                                SLOW_POLL_INTERVAL)

    def _change_interval(self, item: WatchedItem, interval):
        """Changes the item interval and schedules depending on the new one"""
        # TODO: This will go into the async gatherers
        item.interval = interval
        if interval is None:
            self.item_updater.cancel_scheduled_invalidation(item)
        else:
            self.item_updater.schedule_invalidation(item)

    def polling_not_ok(self):
        """Stops polling of some values"""
        self._change_interval(self.nozzle_diameter, None)
        self._change_interval(self.flow_percent, SLOW_POLL_INTERVAL)
        self._change_interval(self.speed_percent, SLOW_POLL_INTERVAL)
        self._change_interval(self.m73, SLOW_POLL_INTERVAL)
        self._change_interval(self.sheet_settings, None)
        self._change_interval(self.active_sheet, None)
        self._change_interval(self.flash_air, None)

    def polling_ok(self):
        """Re-starts polling of some values"""
        self._change_interval(self.nozzle_diameter, SLOW_POLL_INTERVAL)
        self._change_interval(self.flow_percent, FAST_POLL_INTERVAL)
        self._change_interval(self.speed_percent, FAST_POLL_INTERVAL)
        self._change_interval(self.m73, None)
        self._change_interval(self.sheet_settings, VERY_SLOW_POLL_INTERVAL)
        self._change_interval(self.active_sheet, SLOW_POLL_INTERVAL)
        self._change_interval(self.flash_air, VERY_SLOW_POLL_INTERVAL)

    def _filter_sheet_stuff(self, *_):
        """If we receive active_sheet or sheet_settings, we
        need to call the update only if it might complete
        the first printer_info"""
        if not self.data.printer_info_complete:
            self._printer_info_changed()

    def _printer_info_changed(self, *_):
        """Send printer info only if everything is valid"""
        info_values = [
            self.model.raw_printer.network_info,
            self.data.printer_type,
            self.model.raw_printer.firmware_version,
            self.model.raw_printer.nozzle_diameter,
            self.data.serial_number,
            self.model.raw_printer.sheet_settings,
            self.model.raw_printer.active_sheet,
        ]
        complete = True
        for value in info_values:
            if value is None:
                complete = False
                break
        if not complete:
            self.data.printer_info_complete = False
        else:
            if not self.data.printer_info_complete:
                self.data.printer_info_complete = True

    # TODO: not ideal, we need a better persistent storage for this value
    def _process_job_id(self, value):
        """Processes the job id got from the printer"""
        if value is None:
            # TODO: This might never happen
            JOB_ID.state = CondState.NOK
            self.item_updater.schedule_invalidation(
                self.job_id, interval=1)
            return

        if self.model.job.job_id is None:
            # Job component doesn't know the job_id yet, printer has priority
            self.model.job.job_id = value
            JOB_ID.state = CondState.OK
        else:
            # Job knows its job_id, so it has priority
            if self.model.job.job_id != value:
                log.warning(
                    "Job id on the printer: %s differs from the local"
                    " one: %s!", value,
                    self.model.job.job_id)
                # TODO: await, save me
                self.job.write()
                self.invalidate_job_id()

    # --- Data processing for additional inferred stats ---

    def _infer_estimate_accuracy(self, *_):
        """Looks at the current state of things and infers whether the
        time estimates are accurate or not"""
        if self.data.printer_time_remaining is None:
            self.data.inaccurate_estimates = True
        elif self.model.raw_printer.speed_percent != 100:
            self.data.inaccurate_estimates = True
        else:
            self.data.inaccurate_estimates = False

    def _process_print_stats(self, *_):
        """Extracts the actual print stats from printer data"""

        def _use_normal_mode_data(normal_valid_, silent_valid_):
            """Decides which data to use, based on the print mode"""
            if self.model.raw_printer.print_mode == PrintMode.SILENT:
                if silent_valid_:
                    return False
                return True
            # else:
            if normal_valid_:
                return True
            return False

        def _speed_adjust(value):
            """Adjusts the value based on the speed multiplier"""
            if value is None:
                return None
            if self.model.latest_telemetry.speed is not None:
                speed_multiplier = self.model.latest_telemetry.speed / 100
            else:
                speed_multiplier = 1
            inverse_speed_multiplier = 1 / speed_multiplier

            adjusted_value = int(value * inverse_speed_multiplier)
            return adjusted_value

        def _set_print_stats(progress=None,
                             time_remaining=None,
                             filament_change_in=None):
            """Sets the print stats"""
            self.data.printer_progress = progress
            self.data.printer_time_remaining = time_remaining
            self.data.printer_filament_change_in = filament_change_in

        print_stats = self.model.raw_printer.print_stats
        if print_stats is None:
            return _set_print_stats()

        normal_valid = print_stats.progress_normal is not None
        silent_valid = print_stats.progress_silent is not None

        if not normal_valid and not silent_valid:
            # reset the stats to None
            return _set_print_stats()

        if _use_normal_mode_data(normal_valid, silent_valid):
            _set_print_stats(
                progress=print_stats.progress_normal,
                time_remaining=_speed_adjust(
                    print_stats.time_remaining_normal),
                filament_change_in=_speed_adjust(
                    print_stats.filament_change_in_normal),
            )
        else:
            _set_print_stats(
                progress=print_stats.progress_silent,
                time_remaining=_speed_adjust(
                    print_stats.time_remaining_silent),
                filament_change_in=_speed_adjust(
                    print_stats.filament_change_in_silent),
            )
        return None

    def _get_progress_from_byte_position(self):
        """Gets a progress value out of byte position"""
        if self.model.raw_printer.byte_position is None:
            self.data.progress_from_bytes = None
        current, total = self.model.raw_printer.byte_position
        progress = int((current / total) * 100)
        self.data.progress_from_bytes = progress

    def _guess_time_remaining(self, *_):
        """Tracking is nonexistent, guess a time_remaining value
        and write it to the model"""
        raw_printer_data = self.model.raw_printer
        progress = self.data.progress

        if self.data.printer_time_remaining is not None:
            self.data.time_remaining_estimate = None
            return  # Estimate not needed
        if raw_printer_data.sd_seconds_printing is None:
            self.data.time_remaining_estimate = None
            return  # Cannot guess anything without that
        if not progress:
            self.data.time_remaining_estimate = None
            return  # No progress or zero, no guessing yet

        time_multiplier = (100 - progress) / progress
        self.data.time_remaining_estimate = \
            raw_printer_data.sd_seconds_printing * time_multiplier

    def _process_time_printing(self, *_):
        """infers which time_printing to use"""
        if self.model.raw_printer.sd_seconds_printing is not None:
            # Only is not none when actually SD printing
            self.data.time_printing = \
                self.model.raw_printer.sd_seconds_printing
        else:
            self.data.time_printing = \
                self.model.file_printer.time_printing

    def _process_progress(self, *_):
        """infers which progress to use"""
        progress = self.data.printer_progress
        if progress is None:
            progress = self.data.progress_from_bytes
        # If still None, whatever
        self.data.progress = progress

    def _process_time_remaining(self, *_):
        """infers which time_remaining to use"""
        time_remaining = self.data.printer_time_remaining
        if time_remaining is None:
            time_remaining = \
                self.data.time_remaining_estimate
        self.data.time_remaining = time_remaining

    def _process_printer_type(self, value):
        """processes the printer type and does not allow it to change"""
        if self.data.printer_type is None and value is not None:
            self.data.printer_type = PRINTER_TYPES[value]

    def _process_serial_number(self, value):
        """processes the serial_number and does not allow it to change"""
        if self.data.serial_number is None and value is not None:
            self.data.serial_number = value

    # -- Setters --

    @staticmethod
    def _get_cond_state(value):
        """Converts a value or None to a CondState"""
        return CondState.OK if value else CondState.NOK

    # Startup mechanism
    # TODO: move this to be in one place
    def _set_printer_info_complete(self, incomplete):
        """Printer info became valid, we can start looking at telemetry
        and other stuff"""
        if not incomplete:
            self.printer.event_cb(**self.printer.get_info())
            # Part of the startup mechanism
            for item in itertools.chain(self.telemetry, self.other_stuff):
                self.item_updater.enable(item)

    def _set_printer_type(self, value):
        """Do not try and overwrite the printer type, that would
        raise an error"""
        # Part of the startup mechanism
        ID.state = self._get_cond_state(value)
        # TODO: Identify that the it changed while running and tell the user
        if value is not None:
            if self.printer.type != value:
                # Should never happen
                self.printer.type = value
            self.item_updater.enable(self.firmware_version)

    def _set_firmware_version(self, value):
        """It's a setter, what am I expected to write here?
        Sets the firmware version duh"""
        self.printer.firmware = value
        FW.state = self._get_cond_state(value)

        # Part of the startup mechanism
        if value is not None:
            for item in self.printer_info:
                self.item_updater.enable(item)

    # End of startup mechanism

    def _set_serial_number(self, value):
        """Set serial number and fingerprint"""
        SN.state = self._get_cond_state(value)
        # TODO: Identify that the it changed while running and tell the user
        if value is not None:
            if self.printer.sn != value:
                # Should never happen
                self.printer.sn = value
                self.printer.fingerprint = make_fingerprint(value)

    def _set_network_info(self, value):
        """Sets network info"""
        if value is not None:
            self.printer.network_info = value

    def _set_nozzle_diameter(self, value):
        """Sets the nozzle diameter"""
        if value is not None:
            self.printer.nozzle_diameter = value

    def _set_flash_air(self, value):
        """Passes the flash air value to sd updater"""
        if value is not None:
            self.sd_card.set_flash_air(value)

    def _telemetry_passer(self, key):
        """A template function to pass telemetry"""
        def _pass_to_telemetry(value):
            """Passes the value to telemetry"""
            if value is not None:
                self.telemetry_passer.set_telemetry(
                    Telemetry(**{key: value}))
        return _pass_to_telemetry
