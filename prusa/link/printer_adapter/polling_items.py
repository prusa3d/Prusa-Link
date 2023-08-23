"""
Uses info updater to keep up with the printer info.
Hope I can get most of printer polling to use this mechanism.
"""
import abc
import logging
import struct
from typing import List, Optional

from packaging.version import Version

from ..const import (
    FAST_POLL_INTERVAL,
    MINIMAL_FIRMWARE,
    MK25_PRINTERS,
    PRINT_MODE_ID_PAIRING,
    PRINTER_TYPES,
    SLOW_POLL_INTERVAL,
)
from ..serial.helpers import enqueue_matchable, wait_for_instruction
from ..util import get_d3_code
from .structures.item_updater import (
    WatchedItem,
)
from .structures.model_classes import (
    EEPROMParams,
    NetworkInfo,
)
from .structures.module_data_classes import Sheet
from .structures.regular_expressions import (
    D3_OUTPUT_REGEX,
    FW_REGEX,
    MBL_REGEX,
    NOZZLE_REGEX,
    PERCENT_REGEX,
    PRINTER_TYPE_REGEX,
    SN_REGEX,
    VALID_SN_REGEX,
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
    def validation_function(self, value):
        """Defaults to true, if overriden, validates the gathered data"""
        assert value is not None
        return True


class ModelAccessingItem(SelfSufficientItem, abc.ABC):
    """An item that needs access to the model"""

    def __init__(self, serial_queue, model, **kwargs):
        super().__init__(serial_queue, **kwargs)
        self.model = model


class PrinterTypeItem(SelfSufficientItem):
    """Tracks printer type"""

    name = "printer_type"

    def gather_function(self):
        """Gets the printer code using the M862.2 Q gcode."""
        match = self.do_matchable("M862.2 Q",
                                  PRINTER_TYPE_REGEX,
                                  to_front=True)
        return int(match.group("code"))

    def validation_function(self, value):
        """Validates the printer type, throws error because a more
        descriptive error message can be shown this way"""
        if value not in PRINTER_TYPES:
            raise ValueError(f"The printer with type {value} is not supported")

        if self.value not in {None, value}:
            log.error("The printer type changed while running.")
            raise RuntimeError(
                f"Printer type cannot change! Original: "
                f"{self.value} current: {value}.")
        return True


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

        return network_info.dict()


class FirmwareVersionItem(SelfSufficientItem):
    """Tracks firmware version"""

    name = "firmware_version"

    def gather_function(self):
        """Try to get firmware version from the printer."""
        match = self.do_matchable("PRUSA Fir", FW_REGEX, to_front=True)
        return match.group("version")

    def validation_function(self, value):
        """Validates that the printer fw version is up to date enough"""
        without_buildnumber = value.split("-")[0]
        if Version(without_buildnumber) < MINIMAL_FIRMWARE:
            raise ValueError("The printer firmware is outdated")
        return True


class NozzleDiameterItem(SelfSufficientItem):
    """Tracks nozzle diameter"""

    name = "nozzle_diameter"

    def gather_function(self):
        """Gets the printers nozzle diameter using M862.1 Q"""
        match = self.do_matchable("M862.1 Q", NOZZLE_REGEX, to_front=True)
        return float(match.group("size"))


class SerialNumberItem(ModelAccessingItem):
    """Tracks serial number"""

    name = "serial_number"

    def gather_function(self):
        """Returns the SN regex match"""
        # If we're connected through USB and we know the SN, use that one
        serial_port = self.model.serial_adapter.using_port
        if serial_port is not None and serial_port.sn is not None:
            return serial_port.sn
        # Do not ask MK2.5 for its SN, it would break serial communications
        if self.model.printer.printer_model in MK25_PRINTERS | {None}:
            return ""
        match = self.do_matchable("PRUSA SN", SN_REGEX, to_front=True)
        return match.group("sn")

    def validation_function(self, value):
        """Validates the serial number, throws error because a more
        descriptive error message can be shown this way"""
        if VALID_SN_REGEX.match(value) is None:
            return False

        if self.value not in {None, value}:
            log.error(
                "The new serial number is different from the old one!")
            raise RuntimeError(f"Serial numbers differ. Original: "
                               f"{self.value} new one: {value}.")
        return True


class SheetSettingsItem(SelfSufficientItem):
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

        return sheets


class ActiveSheetItem(SelfSufficientItem):
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
        return active_sheet


class PrintModeItem(SelfSufficientItem):
    """Tracks the print mode"""

    name = "print_mode"
    interval = SLOW_POLL_INTERVAL

    def gather_function(self):
        """Gets the print mode from the printer"""
        match = self.do_matchable(
            get_d3_code(*EEPROMParams.PRINT_MODE.value),
            D3_OUTPUT_REGEX, to_front=True)
        index = int(match.group("data").replace(" ", ""), base=16)
        return PRINT_MODE_ID_PAIRING[index]


class MBLItem(SelfSufficientItem):
    """Tracks the mesh bed leveling data"""

    name = "mbl"
    on_fail_interval = None

    def gather_function(self):
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
                data["data"].extend(values)
        return data

    def validation_function(self, value):
        """Validates the mesh bed leveling data"""
        num_x, num_y = value["shape"]
        number_of_points = num_x * num_y
        data = value["data"]
        if len(data) != number_of_points:
            raise ValueError(f"The mbl data matrix was reported to have "
                             f"{num_x} x {num_y} values, but "
                             f"{len(data)} were observed.")
        return True


class FlashAirItem(SelfSufficientItem):
    """Tracks the flash air option on/off"""

    name = "flash_air"

    def gather_function(self):
        """Determines if the Flash Air functionality is on"""
        match = self.do_matchable(
            get_d3_code(*EEPROMParams.FLASH_AIR.value), D3_OUTPUT_REGEX)
        return match.group("data") == "01"

    def validation_function(self, value):
        """Validates that the flash air data is a boolean"""
        return isinstance(value, bool)


class SpeedMultiplierItem(SelfSufficientItem):
    """Tracks the speed multiplier"""

    name = "speed_multiplier"
    interval = FAST_POLL_INTERVAL

    def gather_function(self):
        """Gets the speed multiplier"""
        match = self.do_matchable("M220", PERCENT_REGEX)
        return int(match.group("percent"))

    def validation_function(self, value):
        """Validates the speed multiplier as well as the flow rate"""
        if not 0 <= value <= 999:
            raise ValueError("The speed multiplier or flow rate is not "
                             "between 0 and 999")
        return True


class FlowMultiplierItem(SelfSufficientItem):
    """Tracks the flow multiplier"""

    name = "flow_multiplier"
    interval = FAST_POLL_INTERVAL

    def gather_function(self):
        """Gets the flow multiplier percent"""
        match = self.do_matchable("M221", PERCENT_REGEX)
        return int(match.group("percent"))

    def validation_function(self, value):
        """Validates the speed multiplier as well as the flow rate"""
        if not 0 <= value <= 999:
            raise ValueError("The speed multiplier or flow rate is not "
                             "between 0 and 999")
        return True


class TotalFilamentItem(SelfSufficientItem):
    """Tracks the total filament used"""

    name = "total_filament"
    on_fail_interval = SLOW_POLL_INTERVAL

    def gather_function(self):
        """Gets the total filament used from the eeprom"""
        total_filament = self._eeprom_little_endian_uint32(
            get_d3_code(*EEPROMParams.TOTAL_FILAMENT.value))
        return total_filament * 1000


class TotalPrintTimeItem(SelfSufficientItem):
    """Tracks the total print time"""

    name = "total_print_time"
    on_fail_interval = SLOW_POLL_INTERVAL

    def gather_function(self):
        """Gets the total print time from the eeprom"""
        total_minutes = self._eeprom_little_endian_uint32(
            get_d3_code(*EEPROMParams.TOTAL_PRINT_TIME.value))
        return total_minutes * 60
