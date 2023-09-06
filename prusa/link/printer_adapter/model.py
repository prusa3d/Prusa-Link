"""Contains implementation of the Model class"""

from .structures.mc_singleton import MCSingleton
from .structures.model_classes import (
    FilePrinterData,
    IPUpdaterData,
    JobData,
    PrinterData,
    PrintStatsData,
    ProcessedPrinterData,
    RawPrinterData,
    SDCardData,
    SerialAdapterData,
    StateManagerData,
    StorageData,
    Telemetry,
)


class Model(metaclass=MCSingleton):
    """
    This class should collect every bit of info from all the informer classes
    Some values are reset upon reading, other, more state oriented should stay
    """
    latest_telemetry: Telemetry = Telemetry()

    # Let's try and share inner module states for cooperation
    # The idea is, every module will get the model.
    # Every component HAS TO write its OWN INFO ONLY but can read
    # everything
    serial_adapter: SerialAdapterData
    file_printer: FilePrinterData
    print_stats: PrintStatsData
    state_manager: StateManagerData
    job: JobData
    ip_updater: IPUpdaterData
    sd_card: SDCardData
    folder_storage: StorageData
    filesystem_storage: StorageData
    printer: PrinterData
    raw_printer: RawPrinterData
    processed_printer: ProcessedPrinterData

    def __init__(self) -> None:
        self.latest_telemetry: Telemetry = Telemetry()
