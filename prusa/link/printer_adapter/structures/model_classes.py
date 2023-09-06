"""
Contains models that were originally intended for sending to the connect.
Pydantic makes a great tool for cleanly serializing simple python objects,
while enforcing their type
"""
from typing import Any, Deque, Dict, List, Optional, Set, Tuple, Union

from blinker import Signal
from packaging.version import Version
from pydantic import (
    BaseModel,
    PrivateAttr,
    ValidationError,
    field_validator,
)

from prusa.connect.printer.const import PrinterType, State

from ...const import MINIMAL_FIRMWARE, PRINTER_TYPES
from ...multi_instance.const import VALID_SN_REGEX
from .enums import JobState, PrintMode, PrintState, SDState

# pylint: disable=too-few-public-methods


class Telemetry(BaseModel):
    """The Telemetry model"""
    temp_nozzle: Optional[float] = None
    temp_bed: Optional[float] = None
    target_nozzle: Optional[float] = None
    target_bed: Optional[float] = None
    axis_x: Optional[float] = None
    axis_y: Optional[float] = None
    axis_z: Optional[float] = None
    fan_extruder: Optional[int] = None
    fan_hotend: Optional[int] = None
    fan_print: Optional[int] = None
    target_fan_extruder: Optional[int] = None
    target_fan_hotend: Optional[int] = None
    target_fan_print: Optional[int] = None
    progress: Optional[int] = None
    filament: Optional[str] = None
    flow: Optional[int] = None
    speed: Optional[int] = None
    time_printing: Optional[int] = None
    time_transferring: Optional[int] = None
    time_remaining: Optional[int] = None
    odometer_x: Optional[int] = None
    odometer_y: Optional[int] = None
    odometer_z: Optional[int] = None
    odometer_e: Optional[int] = None
    material: Optional[str] = None
    total_filament: Optional[int] = None
    total_print_time: Optional[int] = None
    filament_change_in: Optional[int] = None
    inaccurate_estimates: Optional[bool] = None


class NetworkInfo(BaseModel):
    """The Network Info model"""

    lan_ipv4: Optional[str] = None  # not implemented yet
    lan_ipv6: Optional[str] = None  # not implemented yet
    lan_mac: Optional[str] = None  # not implemented yet
    wifi_ipv4: Optional[str] = None
    wifi_ipv6: Optional[str] = None  # not implemented yet
    wifi_mac: Optional[str] = None
    wifi_ssid: Optional[str] = None  # not implemented yet
    hostname: Optional[str] = None
    username: Optional[str] = None
    digest: Optional[str] = None


class ObservableModel(BaseModel, extra='forbid', validate_assignment=True):
    """A pydantic model in which value changes,
    refreshes and validation errors can be observed"""
    _refreshed_signals = PrivateAttr(default_factory=dict)
    _changed_signals = PrivateAttr(default_factory=dict)
    _validation_failed_signals = PrivateAttr(default_factory=dict)

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

        for key in self.model_fields:
            self._refreshed_signals[key] = Signal()
            self._changed_signals[key] = Signal()
            self._validation_failed_signals[key] = Signal()

    def __setattr__(self, key, new_value):
        old_value = self.__getattribute__(key)
        try:
            super().__setattr__(key, new_value)
        except ValidationError as error:
            self._validation_failed_signals[key].send(error)
            super().__setattr__(key, None)
            raise error
        if old_value != new_value:
            self._changed_signals[key].send(new_value)
        self._refreshed_signals[key].send(new_value)

    def value_changed_connect(self, keys: Union[str, List[str]], callback,
                              weak=True):
        """Connects a callback to the value_changed signal of the given keys
        """
        self._connect_to(self._changed_signals, keys, callback, weak)

    def value_refreshed_connect(self, keys: Union[str, List[str]], callback,
                                weak=True):
        """Connects a callback to the value_refreshed signal of the given keys
        """""
        self._connect_to(self._refreshed_signals, keys, callback, weak)

    def validation_failed_connect(self, keys: Union[str, List[str]], callback,
                                  weak=True):
        """Connects a callback to the validation_failed signal of the given
        keys"""
        self._connect_to(self._validation_failed_signals,
                         keys, callback, weak)

    def _connect_to(self, signal_dict, keys: Union[str, List[str]], callback,
                    weak=True):
        """A generic signal connecting method"""
        if isinstance(keys, str):
            keys = [keys]

        for key in keys:
            if key not in self.model_fields:
                raise KeyError(f"Key {key} not in fields")
            signal_dict[key].connect(callback, weak=weak)

    def value_changed_disconnect(self, keys: Union[str, List[str]], callback):
        """Disconnects a callback from the value_changed signal of the given
        keys"""
        self._disconnect_from(self._changed_signals, keys, callback)

    def value_refreshed_disconnect(self, keys: Union[str, List[str]],
                                   callback):
        """Disconnects a callback from the value_refreshed signal of the given
        keys"""
        self._disconnect_from(self._refreshed_signals, keys, callback)

    def validation_failed_disconnect(self, keys: Union[str, List[str]],
                                     callback):
        """Disconnects a callback from the validation_failed signal of the
        given keys"""
        self._disconnect_from(self._validation_failed_signals, keys, callback)

    def _disconnect_from(self, signal_dict, keys: Union[str, List[str]],
                         callback):
        """A generic signal disconnecting method"""
        if isinstance(keys, str):
            keys = [keys]

        for key in keys:
            if key not in self.model_fields:
                raise KeyError(f"Key {key} not in fields")
            signal_dict[key].disconnect(callback)


class MBL(BaseModel):
    """Mesh bed leveling data"""
    shape: Tuple[int, int]
    data: List[List[float]]


class Port(BaseModel):
    """Data known about a port"""
    path: str
    is_rpi_port: bool = False
    checked: bool = False  # False if it has not been finished checking
    usable: bool = False  # We can probably use this port for communication
    selected: bool = False  # PrusaLink selected to use this port
    description: str = "Unknown"  # A nice human-readable status
    baudrate: int = 115200
    timeout: int = 2
    sn: Optional[str] = None  # Save the USB descriptor SN if valid

    def __str__(self):
        return (f"Port: {self.path}, "
                f"Checked: {self.checked}, "
                f"Usable: {self.usable}, "
                f"Selected: {self.selected}, "
                f"RPi port: {self.is_rpi_port}, "
                f"Description: {self.description}")


class SerialAdapterData(BaseModel):
    """Data of the SerialAdapter class"""
    ports: List[Port] = []
    using_port: Optional[Port] = None


class FilePrinterData(ObservableModel):
    """Data of the FilePrinter class"""
    file_path: str
    pp_file_path: str
    printing: bool
    paused: bool
    stopped_forcefully: bool
    line_number: int
    time_printing: Optional[int] = None

    # In reality Deque[Instruction] but that cannot be validated by pydantic
    enqueued: Deque[Any]
    gcode_number: int


class StateManagerData(BaseModel):
    """Data of the StateManager class"""
    # The ACTUAL states considered when reporting
    base_state: State
    printing_state: Optional[State] = None
    override_state: Optional[State] = None

    # Reported state history
    last_state: State
    current_state: State
    state_history: Deque[State]
    awaiting_error_reason: bool


class JobData(BaseModel):
    """Data of the Job class"""
    job_id: Optional[int] = None
    job_id_offset: int
    already_sent: Optional[bool] = None
    job_start_cmd_id: Optional[int] = None
    selected_file_path: Optional[str] = None
    selected_file_m_timestamp: Optional[int] = None
    selected_file_size: Optional[str] = None
    printing_file_byte: Optional[int] = None
    path_incomplete: Optional[bool] = None
    from_sd: Optional[bool] = None

    job_state: JobState

    def get_job_id_for_api(self):
        """
        The API does not send None values. This function returns None when
        no job is running, otherwise it gives the job_id
        """
        if self.job_state == JobState.IDLE:
            return None
        return self.job_id


class IPUpdaterData(BaseModel):
    """Data of the IpUpdater class"""
    local_ip: Optional[str] = None
    local_ip6: Optional[str] = None
    mac: Optional[str] = None
    is_wireless: bool
    update_ip_on: float
    ssid: Optional[str] = None
    hostname: Optional[str] = None
    username: Optional[str] = None
    digest: Optional[str] = None


class SDCardData(BaseModel):
    """Data of the SDCard class"""
    expecting_insertion: bool
    invalidated: bool
    is_flash_air: bool
    last_updated: float
    last_checked_flash_air: float
    sd_state: SDState
    files: Any  # We cannot type-check SDFile, only basic ones
    sfn_to_lfn_paths: Dict[str, str]
    lfn_to_sfn_paths: Dict[str, str]
    mixed_to_lfn_paths: Dict[str, str]


class StorageData(BaseModel):
    """Data of the Storage class"""
    blacklisted_paths: List[str]
    blacklisted_names: List[str]
    configured_storage: Set[str]
    attached_set: Set[str]


class PrintStatsData(BaseModel):
    """Data of the PrintStats class"""
    print_time: float
    segment_start: float
    has_inbuilt_stats: bool
    total_gcode_count: int  # is not computed for files containg reporting
    #                         to speed stuff up


class Sheet(BaseModel):
    """Data available for sheets in the printer EEPROM"""
    name: str = ""
    z_offset: float = 0.0
    # temps at the time of calibration
    bed_temp: int = 0
    pinda_temp: int = 0


class PrinterData(BaseModel):
    """Data of the SDK Printer"""
    printer_type: Optional[int] = None


class PrintStats(ObservableModel):
    """A group for print stats, so they get updated at once"""
    progress_normal: Optional[int] = None
    progress_silent: Optional[int] = None
    time_remaining_normal: Optional[int] = None
    time_remaining_silent: Optional[int] = None
    filament_change_in_normal: Optional[int] = None
    filament_change_in_silent: Optional[int] = None

    @field_validator("progress_normal", "progress_silent")
    @classmethod
    def print_progress_validator(cls, value):
        """Validates the print progress"""
        if value is None:
            return value
        if not 0 <= value <= 100:
            raise ValueError("The print progress is not between 0 and 100")
        return value

    @field_validator("time_remaining_normal",
                     "time_remaining_silent",
                     "filament_change_in_normal",
                     "filament_change_in_silent")
    @classmethod
    def time_remaining_validator(cls, value):
        """Validates the time remaining"""
        if value is None:
            return value
        if value < 0:
            raise ValueError("The time remaining is negative")
        return value


class RawPrinterData(ObservableModel):
    """Pretty much raw data read from the printer"""
    # a number reported by printer, validated
    printer_type: Optional[int] = None
    network_info: Optional[NetworkInfo] = None
    firmware_version: Optional[str] = None
    nozzle_diameter: Optional[float] = None
    serial_number: Optional[str] = None
    sheet_settings: Optional[List[Sheet]] = None
    active_sheet: Optional[int] = None
    print_mode: Optional[PrintMode] = None
    mbl: Optional[MBL] = None
    flash_air: Optional[bool] = None
    speed_percent: Optional[int] = None
    flow_percent: Optional[int] = None
    total_filament: Optional[int] = None
    total_print_time: Optional[int] = None
    print_state: Optional[PrintState] = None
    sd_seconds_printing: Optional[int] = None
    byte_position: Optional[int] = None
    # short (8.3) folder names, long file name (52 chars)
    mixed_path: Optional[str] = None
    print_stats: Optional[PrintStats] = None
    job_id: Optional[int] = None

    @field_validator("printer_type")
    @classmethod
    def printer_type_validator(cls, value):
        """Validates that the printer type is listed in the supported ones"""
        if value is None:
            return value
        if value not in PRINTER_TYPES:
            raise ValueError(f"The printer with type {value} is not supported")
        return value

    @field_validator("firmware_version")
    @classmethod
    def firmware_version_validator(cls, value):
        """Validates that the printer fw version is up to date enough"""
        if value is None:
            return value
        without_buildnumber = value.split("-")[0]
        if Version(without_buildnumber) < MINIMAL_FIRMWARE:
            raise ValueError("The printer firmware is outdated")
        return value

    @field_validator("serial_number")
    @classmethod
    def serial_number_validator(cls, value):
        """Validates the serial number, throws error because a more
        descriptive error message can be shown this way"""
        if value is None:
            return value
        if VALID_SN_REGEX.match(value) is None:
            raise ValueError("The printer serial number is invalid")
        return value

    @field_validator("mbl")
    @classmethod
    def mbl_validator(cls, value):
        """Validates the mesh bed leveling data"""
        if value is None:
            return value
        num_x, num_y = value.shape
        number_of_points = num_x * num_y
        if len(value.data) != number_of_points:
            raise ValueError(f"The mbl data matrix was reported to have "
                             f"{num_x} x {num_y} values, but "
                             f"{len(value.data)} were observed.")
        return value

    @field_validator("speed_percent", "flow_percent")
    @classmethod
    def speed_percent_validator(cls, value):
        """Validates the speed multiplier as well as the flow rate"""
        if value is None:
            return value
        if not 0 <= value <= 999:
            raise ValueError("The speed multiplier or flow rate is not "
                             "between 0 and 999")
        return value


class ProcessedPrinterData(ObservableModel):
    """Pre-processed data, got by selecting, or combining
    other printer data"""
    progress_from_bytes: Optional[int] = None
    time_remaining_estimate: Optional[int] = None
    progress_broken: Optional[bool] = None
    time_broken: Optional[bool] = None
    valid_printer_type: Optional[int] = None
    valid_serial_number: Optional[str] = None
    inaccurate_estimates: Optional[bool] = None

    # TODO: what about mmu connecting later, that's broken
    # these two get set and shouldn't change
    printer_type: Optional[PrinterType] = None
    serial_number: Optional[str] = None

    printer_progress: Optional[int] = None
    printer_time_remaining: Optional[int] = None  # Adjusted for speed
    printer_filament_change_in: Optional[int] = None  # Adjusted for speed

    time_remaining: Optional[int] = None  # Adjusted for speed
    time_printing: Optional[int] = None

    progress: Optional[int] = None

    printer_info_complete: Optional[bool] = True  # None is False here
