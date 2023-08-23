"""
Contains implementation of the  controller for interfacing with the storage
"subsystem", which included the linux filesystem and sd card file management,
now only sd card and storage tracking remain
"""
import logging
from typing import Optional

from blinker import Signal  # type: ignore

from prusa.connect.printer.files import File

from ...printer_adapter.model import Model
from ...sdk_augmentation.file import SDFile
from ...serial.serial_parser import ThreadedSerialParser
from ...serial.serial_queue import SerialQueue
from .sd_card import SDCard
from .storage import FolderStorage  # FilesystemStorage

log = logging.getLogger(__name__)


class StorageController:
    """
    Sort of an interface layer between the (once larger) storage system
    and the rest of the app
    """

    # pylint: disable=too-many-arguments
    def __init__(self, cfg, serial_queue: SerialQueue,
                 serial_parser: ThreadedSerialParser,
                 model: Model):
        self.folder_attached_signal = Signal()
        self.folder_detached_signal = Signal()
        self.sd_attached_signal = Signal()
        self.sd_detached_signal = Signal()
        self.menu_found_signal = Signal()

        self.serial_parser = serial_parser
        self.serial_queue: SerialQueue = serial_queue
        self.model = model

        self.sd_card = SDCard(self.serial_queue, self.serial_parser,
                              self.model)
        self.sd_card.sd_attached_signal.connect(self.sd_attached)
        self.sd_card.sd_detached_signal.connect(self.sd_detached)
        self.sd_card.menu_found_signal.connect(self.menu_found)

        # self.filesystem_storage = FilesystemStorage(self.model, cfg)
        self.folder_storage = FolderStorage(self.model, cfg)
        # self.filesystem_storage.attached_signal.connect(self.folder_attached)
        # self.filesystem_storage.detached_signal.connect(self.folder_detached)
        self.folder_storage.attached_signal.connect(self.folder_attached)
        self.folder_storage.detached_signal.connect(self.folder_detached)

        self.sd_tree: Optional[SDFile] = None

    def folder_attached(self, sender, path: str):
        """Signal pass-through"""
        assert sender is not None
        self.folder_attached_signal.send(self, path=path)

    def folder_detached(self, sender, path: str):
        """Signal pass-through"""
        assert sender is not None
        self.folder_detached_signal.send(self, path=path)

    def sd_attached(self, sender, files: File):
        """Signal pass-through"""
        assert sender is not None
        self.sd_attached_signal.send(self, files=files)

    def sd_detached(self, sender):
        """Signal pass-through"""
        assert sender is not None
        self.sd_detached_signal.send(self)

    def menu_found(self, _, menu_sfn):
        """Secret menu has been found signal passthrough"""
        self.menu_found_signal.send(menu_sfn=menu_sfn)

    def update(self):
        """Passes the call to update() to all its submodules"""
        self.sd_card.update()
        # self.filesystem_storage.update()
        self.folder_storage.update()

    def start(self):
        """Starts submodules"""
        self.sd_card.start()
        # self.filesystem_storage.start()
        self.folder_storage.start()

    def stop(self):
        """Stops submodules"""
        self.sd_card.stop()
        # self.filesystem_storage.stop()
        self.folder_storage.stop()

    def wait_stopped(self):
        """SWait for storage submodules to quit"""
        self.sd_card.wait_stopped()
        # self.filesystem_storage.wait_stopped()
        self.folder_storage.wait_stopped()
