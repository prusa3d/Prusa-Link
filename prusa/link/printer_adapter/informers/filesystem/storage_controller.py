import logging
from typing import List, Optional

from blinker import Signal

from prusa.link.printer_adapter.default_settings import get_settings
from prusa.link.printer_adapter.informers.filesystem.models import \
    InternalFileTree, SDState
from prusa.link.printer_adapter.informers.filesystem.linux_filesystem import \
    LinuxFilesystem
from prusa.link.printer_adapter.informers.filesystem.sd_card import SDCard
from prusa.link.printer_adapter.informers.state_manager import StateManager
from prusa.link.printer_adapter.input_output.serial.serial_queue import \
    SerialQueue
from prusa.link.printer_adapter.input_output.serial.serial_reader import \
    SerialReader

LOG = get_settings().LOG
TIME = get_settings().TIME
MOUNT = get_settings().MOUNT

log = logging.getLogger(__name__)
log.setLevel(LOG.STORAGE)


class StorageController:

    def __init__(self, serial_queue: SerialQueue, serial_reader: SerialReader,
                 state_manager: StateManager):
        self.updated_signal = Signal()  # kwargs: tree: FileTree
        self.inserted_signal = Signal()  # kwargs: root: str, files: FileTree
        self.ejected_signal = Signal()  # kwargs: root: str

        # Pass this through
        self.sd_state_changed_signal = Signal()  # kwargs: sd_state: SDState

        self.serial_reader = serial_reader
        self.serial_queue: SerialQueue = serial_queue
        self.state_manager = state_manager

        self.sd_card = SDCard(self.serial_queue, self.serial_reader,
                              self.state_manager)
        self.sd_card.tree_updated_signal.connect(self.sd_tree_updated)
        self.sd_card.state_changed_signal.connect(self.sd_state_changed)
        self.sd_card.inserted_signal.connect(self.media_inserted)
        self.sd_card.ejected_signal.connect(self.media_ejected)

        self.linux_fs = LinuxFilesystem(self.state_manager)
        self.linux_fs.updated_signal.connect(self.fs_updated)
        self.linux_fs.inserted_signal.connect(self.media_inserted)
        self.linux_fs.ejected_signal.connect(self.media_ejected)

        self.sd_tree: Optional[InternalFileTree] = None
        self.fs_tree_list: List[InternalFileTree] = []

    def update(self):
        self.sd_card.update()
        self.linux_fs.update()

    def start(self):
        self.sd_card.start()
        self.linux_fs.start()

    def sd_tree_updated(self, sender, tree: InternalFileTree):
        self.sd_tree = tree
        self.updated()

    def sd_state_changed(self, sender, sd_state: SDState):
        self.sd_state_changed_signal.send(sender, sd_state=sd_state)

    def fs_updated(self, sender, tree_list: List[InternalFileTree]):
        self.fs_tree_list = tree_list
        self.updated()

    def updated(self):
        root = InternalFileTree.new_root_node()
        if self.sd_tree is not None:
            root.add_child(self.sd_tree)

        for tree in self.fs_tree_list:
            root.add_child(tree)

        log.debug(f"Constructed tree: \n{root}")

        self.updated_signal.send(self, tree=root)

    def media_inserted(self, sender, root, files):
        self.inserted_signal.send(sender, root=root, files=files)

    def media_ejected(self, sender, root):
        self.ejected_signal.send(sender, root=root)

    def stop(self):
        self.sd_card.stop()
        self.linux_fs.stop()

