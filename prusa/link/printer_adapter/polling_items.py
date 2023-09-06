"""
Uses info updater to keep up with the printer info.
Hope I can get most of printer polling to use this mechanism.
"""
import abc
import logging
import re
import struct
from contextlib import suppress
from datetime import timedelta
from typing import List, Optional

from pydantic import ValidationError

from ..const import (
    FAST_POLL_INTERVAL,
    MK25_PRINTERS,
    PRINT_MODE_ID_PAIRING,
    PRINT_STATE_PAIRING,
    SLOW_POLL_INTERVAL,
)
from ..serial.helpers import enqueue_matchable, wait_for_instruction
from ..util import get_d3_code
from .model import Model
from .structures.enums import EEPROMParams
from .structures.item_updater import (
    WatchedItem,
)
from .structures.model_classes import (
    MBL,
    NetworkInfo,
    PrintMode,
    PrintState,
    PrintStats,
    RawPrinterData,
    Sheet,
)
from .structures.regular_expressions import (
    D3_OUTPUT_REGEX,
    FW_REGEX,
    M27_OUTPUT_REGEX,
    MBL_REGEX,
    NOZZLE_REGEX,
    PERCENT_REGEX,
    PRINT_INFO_REGEX,
    PRINTER_TYPE_REGEX,
    SN_REGEX,
)

log = logging.getLogger(__name__)


class SelfSufficientItem(WatchedItem, abc.ABC):
    """An item that can gather its own data, does not accept
    external functions"""

    name: str = "ChangeThis"
    interval: Optional[float] = None
    timeout: Optional[float] = None
    on_fail_interval: Optional[float] = WatchedItem.default_on_fail_interval

    def __init__(self, serial_queue):
        if self.name == "ChangeThis":
            raise ValueError("You have to provide SelfSufficientItem "
                             "descendants with a name")
        super().__init__(name=self.name,
                         gather_function=self.gather_function,
                         validation_function=self.validation_function,
                         interval=self.interval,
                         timeout=self.timeout,
                         on_fail_interval=self.on_fail_interval)
        self.serial_queue = serial_queue

    def should_wait(self):
        """Returns true if we should keep waiting for the instruction to
        finish"""
        # TODO: transition to async
        return not self.serial_queue.quit_evt.is_set()

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
            raise RuntimeError(
                "Printer responded with something unexpected")
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

    def _eeprom_little_endian_uint32(self, dcode):
        """Reads and decodes the D-Code specified little-endian uint32_t
        eeprom variable"""
        match = self.do_matchable(dcode,
                                  D3_OUTPUT_REGEX,
                                  to_front=True)
        str_data = match.group("data").replace(" ", "")
        data = bytes.fromhex(str_data)
        return struct.unpack("<I", data)[0]

    # pylint: disable=method-hidden
    @abc.abstractmethod
    def gather_function(self):
        """Descendants override this and gather data from the printer here"""

    # pylint: disable=method-hidden
    def validation_function(self, _):
        """Defaults to true, if overriden, validates the gathered data"""
        return True


# TODO: needs to handle timeouts by also setting the item to none or something
class ModelAccessingItem(SelfSufficientItem, abc.ABC):
    """An item that needs access to the model"""

    def __init__(self, serial_queue, model: Model, **kwargs):
        super().__init__(serial_queue, **kwargs)
        self.model: Model = model
        if not hasattr(model, "raw_printer"):
            model.raw_printer = RawPrinterData()
        self.data: RawPrinterData = self.model.raw_printer


class PrinterTypeItem(ModelAccessingItem):
    """Tracks printer type"""

    name = "printer_type"

    def gather_function(self):
        """Gets the printer code using the M862.2 Q gcode."""
        match = self.do_matchable("M862.2 Q",
                                  PRINTER_TYPE_REGEX,
                                  to_front=True)
        # Let gather fail on value error
        self.data.printer_type = int(match.group("code"))


# TODO: Nope, this has to be elsewhere
class NetworkInfoItem(ModelAccessingItem):
    """Tracks network info"""

    name = "network_info"

    def gather_function(self):
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

        self.data.network_info = network_info.model_dump()


class FirmwareVersionItem(ModelAccessingItem):
    """Tracks firmware version"""

    name = "firmware_version"

    def gather_function(self):
        """Try to get firmware version from the printer."""
        match = self.do_matchable("PRUSA Fir", FW_REGEX, to_front=True)
        self.data.firmware_version = match.group("version")


class NozzleDiameterItem(ModelAccessingItem):
    """Tracks nozzle diameter"""

    name = "nozzle_diameter"
    interval = SLOW_POLL_INTERVAL

    def gather_function(self):
        """Gets the printers nozzle diameter using M862.1 Q"""
        match = self.do_matchable("M862.1 Q", NOZZLE_REGEX, to_front=True)
        self.data.nozzle_diameter = float(match.group("size"))


class SerialNumberItem(ModelAccessingItem):
    """Tracks serial number"""

    name = "serial_number"

    def gather_function(self):
        """Returns the SN regex match"""
        # If we're connected through USB and we know the SN, use that one
        serial_port = self.model.serial_adapter.using_port
        if serial_port is not None and serial_port.sn is not None:
            self.data.serial_number = serial_port.sn
        # Do not ask MK2.5 for its SN, it would break serial communications
        elif self.model.printer.printer_model in MK25_PRINTERS | {None}:
            self.data.serial_number = ""
        else:
            match = self.do_matchable("PRUSA SN", SN_REGEX, to_front=True)
            self.data.serial_number = match.group("sn")


class SheetSettingsItem(ModelAccessingItem):
    """Tracks sheet settings"""

    name = "sheet_settings"

    def gather_function(self):
        """Gets all the sheet settings from the EEPROM"""
        # TODO: How do we deal with default settings?
        matches = self.do_multimatch(
            get_d3_code(*EEPROMParams.SHEET_SETTINGS.value),
            D3_OUTPUT_REGEX, to_front=True)

        sheets: List[Sheet] = []
        str_data = ""
        for match in matches:
            str_data += match.group("data").replace(" ", "")

        data = bytes.fromhex(str_data)
        for i in range(0, 8 * 11, 11):
            sheet_data = data[i:i + 11]

            z_offset_u16 = struct.unpack("H", sheet_data[7:9])[0]
            max_uint16 = 2 ** 16 - 1
            if z_offset_u16 in {0, max_uint16}:
                z_offset_workaround = max_uint16
            else:
                z_offset_workaround = z_offset_u16 - 1
            z_offset = (z_offset_workaround - max_uint16) / 400

            sheets.append(Sheet(
                name=sheet_data[:7].decode("ascii"),
                z_offset=z_offset,
                bed_temp=struct.unpack("B", sheet_data[9:10])[0],
                pinda_temp=struct.unpack("B", sheet_data[10:11])[0],
            ))

        self.data.sheet_settings = sheets


class ActiveSheetItem(ModelAccessingItem):
    """Tracks the active sheet"""

    name = "active_sheet"

    def gather_function(self):
        """Gets the active sheet from the EEPROM"""
        matches = self.do_matchable(
            get_d3_code(*EEPROMParams.ACTIVE_SHEET.value),
            D3_OUTPUT_REGEX, to_front=True)

        str_data = matches.group("data").replace(" ", "")
        data = bytes.fromhex(str_data)
        active_sheet = struct.unpack("B", data)[0]
        self.data.active_sheet = active_sheet


class PrintModeItem(ModelAccessingItem):
    """Tracks the print mode"""

    name = "print_mode"
    interval = SLOW_POLL_INTERVAL

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.data.print_mode = PrintMode.SILENT

    def gather_function(self):
        """Gets the print mode from the printer"""
        match = self.do_matchable(
            get_d3_code(*EEPROMParams.PRINT_MODE.value),
            D3_OUTPUT_REGEX, to_front=True)
        index = int(match.group("data").replace(" ", ""), base=16)
        self.data.print_mode = PRINT_MODE_ID_PAIRING[index]


class MBLItem(ModelAccessingItem):
    """Tracks the mesh bed leveling data"""

    name = "mbl"
    on_fail_interval = None

    def gather_function(self):
        """Gets the current MBL data"""
        matches = self.do_multimatch("G81", MBL_REGEX, to_front=True)
        groups = matches[0].groupdict()

        if groups["no_mbl"] is None:
            num_x = int(groups["num_x"])
            num_y = int(groups["num_y"])
            shape = (num_x, num_y)
            data = []
            for i, match in enumerate(matches):
                if i == 0:
                    continue
                line = match.group("mbl_row")
                str_values = line.split()
                values = [float(val) for val in str_values]
                data.extend(values)
            self.data.mbl = MBL(shape=shape, data=data)
        else:
            self.data.mbl = None


class FlashAirItem(ModelAccessingItem):
    """Tracks the flash air option on/off"""

    name = "flash_air"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.data.flash_air = False

    def gather_function(self):
        """Determines if the Flash Air functionality is on"""
        match = self.do_matchable(
            get_d3_code(*EEPROMParams.FLASH_AIR.value), D3_OUTPUT_REGEX)
        self.data.flash_air = match.group("data") == "01"


class SpeedPercentItem(ModelAccessingItem):
    """Tracks the speed percentage value"""

    name = "speed_percent"
    interval = FAST_POLL_INTERVAL

    def gather_function(self):
        """Gets the speed multiplier"""
        match = self.do_matchable("M220", PERCENT_REGEX)
        self.data.speed_percent = int(match.group("percent"))


class FlowPercentItem(ModelAccessingItem):
    """Tracks the flow percentage"""

    name = "flow_percent"
    interval = FAST_POLL_INTERVAL

    def gather_function(self):
        """Gets the flow multiplier percent"""
        match = self.do_matchable("M221", PERCENT_REGEX)
        self.data.flow_percent = int(match.group("percent"))


class TotalFilamentItem(ModelAccessingItem):
    """Tracks the total filament used"""

    name = "total_filament"
    on_fail_interval = SLOW_POLL_INTERVAL

    def gather_function(self):
        """Gets the total filament used from the eeprom"""
        total_filament = self._eeprom_little_endian_uint32(
            get_d3_code(*EEPROMParams.TOTAL_FILAMENT.value))
        self.data.total_filament = total_filament * 1000


class TotalPrintTimeItem(ModelAccessingItem):
    """Tracks the total print time"""

    name = "total_print_time"
    on_fail_interval = SLOW_POLL_INTERVAL

    def gather_function(self):
        """Gets the total print time from the eeprom"""
        total_minutes = self._eeprom_little_endian_uint32(
            get_d3_code(*EEPROMParams.TOTAL_PRINT_TIME.value))
        self.data.total_print_time = total_minutes * 60


class M27Item(ModelAccessingItem):
    """Tracks the M27 stats"""

    name = "m27"
    interval = FAST_POLL_INTERVAL
    on_fail_interval = SLOW_POLL_INTERVAL

    @staticmethod
    def _parse_print_state(matches):
        """Parse a printer tracked state depending on which match group
        is present"""
        match = matches[0]
        groups = match.groupdict()
        for group, state in PRINT_STATE_PAIRING.items():
            if groups[group] is not None:
                return state
        return None

    @staticmethod
    def _parse_mixed_path(matches):
        """Here we get a printer print state and if printing
        a mixed length path of the file being printed from the SD card"""
        match = matches[0]
        groups = match.groupdict()
        if groups["sdn_lfn"] is not None:
            return groups["sdn_lfn"]
        return None

    @staticmethod
    def _parse_byte_position(matches):
        """Gets the byte position of the file being sd printed"""
        if len(matches) >= 2:
            match = matches[1]
            groups = match.groupdict()
            return int(groups["current"]), int(groups["sum"])
        return None

    @staticmethod
    def _parse_sd_seconds(matches):
        """Gets the time for which we've been printing already"""
        if len(matches) >= 3:
            match = matches[2]
            groups = match.groupdict()
            return timedelta(hours=int(groups["hours"]),
                             minutes=int(groups["minutes"]))
        return None

    def gather_function(self):
        """Gets the M27 stats"""
        matches = self.do_multimatch("M27 P", M27_OUTPUT_REGEX,
                                     to_front=True)

        # If we can get these, set them, only reset when not SD printing
        mixed_path = self._parse_mixed_path(matches)
        if mixed_path is not None:
            self.data.mixed_path = mixed_path

        sd_seconds_printing = self._parse_sd_seconds(matches)
        byte_position = self._parse_byte_position(matches)
        print_state = self._parse_print_state(matches)

        # don't write these through while SD paused
        if self.data.print_state == PrintState.SD_PAUSED:
            with suppress(ValidationError):
                self.data.sd_seconds_printing = sd_seconds_printing
            with suppress(ValidationError):
                self.data.byte_position = byte_position
            with suppress(ValidationError):
                self.data.mix_path = mixed_path
        self.data.print_state = print_state


class M73Item(ModelAccessingItem):
    """Tracks the M73 stats"""
    # TODO: unify this into one async gatherer
    name = "m73"

    def gather_function(self):
        matches = self.do_multimatch("M73", PRINT_INFO_REGEX)
        self.print_info_handler(matches)

    def print_info_handler(self, matches: List[re.Match]):
        """One special handler supporting polling and spontaneous
        unsolicited reporting of progress and minutes remaining"""

        print_stats = PrintStats()
        for match in matches:
            groups = match.groupdict()
            progress = int(groups["progress"])
            # Convert both time values to seconds and adjust by print speed
            remaining = int(groups["remaining"]) * 60
            filament_change_in = int(groups["change_in"]) * 60

            if match.group("mode") == PrintMode.SILENT.value:
                with suppress(ValidationError):
                    print_stats.progress_silent = progress
                with suppress(ValidationError):
                    print_stats.time_remaining_silent = remaining
                with suppress(ValidationError):
                    print_stats.filament_change_in_silent = \
                        filament_change_in
            elif match.group("mode") == PrintMode.NORMAL.value:
                with suppress(ValidationError):
                    print_stats.progress_normal = progress
                with suppress(ValidationError):
                    print_stats.time_remaining_normal = remaining
                with suppress(ValidationError):
                    print_stats.filament_change_in_normal = \
                        filament_change_in

        self.data.print_stats = print_stats


class JobIdItem(ModelAccessingItem):
    """Tracks the job id"""

    name = "job_id"

    def gather_function(self):
        """Gets the job id from the printer"""
        match = self.do_matchable(
            get_d3_code(*EEPROMParams.JOB_ID.value),
            D3_OUTPUT_REGEX, to_front=True)
        self.data.job_id = int(match.group("data").replace(" ", ""), base=16)
