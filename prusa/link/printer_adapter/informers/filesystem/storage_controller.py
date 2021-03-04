import logging
from typing import Optional

from blinker import Signal  # type: ignore

from prusa.connect.printer.files import File

from .mounts import FSMounts, DirMounts
from .sd_card import SDCard
from ..state_manager import StateManager
from ...input_output.serial.serial_queue import SerialQueue
from ...input_output.serial.serial_reader import SerialReader
from ...model import Model
from ....sdk_augmentation.file import SDFile

log = logging.getLogger(__name__)


class StorageController:
    """
    Sort of an interface layer between the (once larger) storage system
    and the rest of the app
    """
    def __init__(self, cfg, serial_queue: SerialQueue,
                 serial_reader: SerialReader, state_manager: StateManager,
                 model: Model):
        self.dir_mounted_signal = Signal()
        self.dir_unmounted_signal = Signal()
        self.sd_mounted_signal = Signal()
        self.sd_unmounted_signal = Signal()

        self.serial_reader = serial_reader
        self.serial_queue: SerialQueue = serial_queue
        self.state_manager = state_manager
        self.model = model

        self.sd_card = SDCard(self.serial_queue, self.serial_reader,
                              self.state_manager, self.model)
        self.sd_card.sd_mounted_signal.connect(self.sd_mounted)
        self.sd_card.sd_unmounted_signal.connect(self.sd_unmounted)

        self.fs_mounts = FSMounts(self.model.fs_mounts)
        self.dir_mounts = DirMounts(cfg, self.model.dir_mounts)
        self.fs_mounts.mounted_signal.connect(self.dir_mounted)
        self.fs_mounts.unmounted_signal.connect(self.dir_unmounted)
        self.dir_mounts.mounted_signal.connect(self.dir_mounted)
        self.dir_mounts.unmounted_signal.connect(self.dir_unmounted)

        self.sd_tree: Optional[SDFile] = None

    def dir_mounted(self, sender, path: str):
        """Signal pass-through"""
        self.dir_mounted_signal.send(self, path=path)

    def dir_unmounted(self, sender, path: str):
        """Signal pass-through"""
        self.dir_unmounted_signal.send(self, path=path)

    def sd_mounted(self, sender, files: File):
        """Signal pass-through"""
        self.sd_mounted_signal.send(self, files=files)

    def sd_unmounted(self, sender):
        """Signal pass-through"""
        self.sd_unmounted_signal.send(self)

    def update(self):
        """Passes the call to update() to all its submodules"""
        self.sd_card.update()
        self.fs_mounts.update()
        self.dir_mounts.update()

    def start(self):
        """Starts submodules"""
        self.sd_card.start()
        self.fs_mounts.start()
        self.dir_mounts.start()

    def stop(self):
        """Stops submodules"""
        self.sd_card.stop()
        self.fs_mounts.stop()
        self.dir_mounts.stop()
