"""Contains implementation of the Model class"""

from .structures.mc_singleton import MCSingleton
from .structures.model_classes import Telemetry
from .structures.module_data_classes import \
    FilePrinterData, StateManagerData, JobData, IPUpdaterData, SDCardData, \
    MountsData, PrintStatsData


class Model(metaclass=MCSingleton):
    """
    This class should collect every bit of info from all the informer classes
    Some values are reset upon reading, other, more state oriented should stay
    """
    def __init__(self):
        self.latest_telemetry: Telemetry = Telemetry()

        # Let's try and share inner module states for cooperation
        # The idea is, every module will get the model.
        # Every component HAS TO write its OWN INFO ONLY but can read
        # everything
        self.file_printer: FilePrinterData
        self.print_stats: PrintStatsData
        self.state_manager: StateManagerData
        self.job: JobData
        self.ip_updater: IPUpdaterData
        self.sd_card: SDCardData
        self.dir_mounts: MountsData
        self.fs_mounts: MountsData
