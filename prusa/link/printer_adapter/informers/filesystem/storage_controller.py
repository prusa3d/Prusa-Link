import logging
from typing import Optional

from blinker import Signal

from prusa.connect.printer.files import File
from prusa.link.printer_adapter.default_settings import get_settings
from prusa.link.printer_adapter.informers.filesystem.mounts import FSMounts, \
    DirMounts
from prusa.link.printer_adapter.informers.filesystem.sd_card import SDCard
from prusa.link.printer_adapter.informers.state_manager import StateManager
from prusa.link.printer_adapter.input_output.serial.serial_queue import \
    SerialQueue
from prusa.link.printer_adapter.input_output.serial.serial_reader import \
    SerialReader
from prusa.link.sdk_augmentation.file import SDFile

MOUNT = get_settings().MOUNT

log = logging.getLogger(__name__)


class StorageController:

    def __init__(self, cfg, serial_queue: SerialQueue,
                 serial_reader: SerialReader,
                 state_manager: StateManager):
        self.updated_signal = Signal()  # kwargs: tree: FileTree
        self.dir_mounted_signal = Signal()
        self.dir_unmounted_signal = Signal()
        self.sd_mounted_signal = Signal()
        self.sd_unmounted_signal = Signal()

        self.serial_reader = serial_reader
        self.serial_queue: SerialQueue = serial_queue
        self.state_manager = state_manager

        self.sd_card = SDCard(self.serial_queue, self.serial_reader,
                              self.state_manager)
        self.sd_card.tree_updated_signal.connect(self.sd_tree_updated)
        self.sd_card.sd_mounted_signal.connect(self.sd_mounted)
        self.sd_card.sd_unmounted_signal.connect(self.sd_unmounted)

        self.fs_mounts = FSMounts()
        self.dir_mounts = DirMounts(cfg)
        self.fs_mounts.mounted_signal.connect(self.dir_mounted)
        self.fs_mounts.unmounted_signal.connect(self.dir_unmounted)
        self.dir_mounts.mounted_signal.connect(self.dir_mounted)
        self.dir_mounts.unmounted_signal.connect(self.dir_unmounted)

        self.sd_tree: Optional[SDFile] = None

    def dir_mounted(self, sender, path: str):
        self.dir_mounted_signal.send(self, path=path)

    def dir_unmounted(self, sender, path: str):
        self.dir_unmounted_signal.send(self, path=path)

    def sd_mounted(self, sender, files: File):
        self.sd_mounted_signal.send(self, files=files)

    def sd_unmounted(self, sender):
        self.sd_unmounted_signal.send(self)

    def update(self):
        self.sd_card.update()
        self.fs_mounts.update()
        self.dir_mounts.update()

    def start(self):
        self.sd_card.start()
        self.fs_mounts.start()
        self.dir_mounts.start()

    def sd_tree_updated(self, sender, tree: SDFile):
        self.sd_tree = tree
        # TODO: what about this?

    def stop(self):
        self.sd_card.stop()
        self.fs_mounts.stop()
        self.dir_mounts.stop()

