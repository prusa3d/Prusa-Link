import logging
import os
from datetime import datetime

from blinker import Signal

from old_buddy.default_settings import get_settings
from old_buddy.informers.filesystem.models import InternalFileTree, MountPoint
from old_buddy.informers.filesystem.mounts import DirMounts, FSMounts
from old_buddy.informers.state_manager import StateManager
from old_buddy.structures.constants import PRINTING_STATES
from old_buddy.structures.model_classes import FileType
from old_buddy.updatable import ThreadedUpdatable

LOG = get_settings().LOG
TIME = get_settings().TIME
MOUNT = get_settings().MOUNT

log = logging.getLogger(__name__)
log.setLevel(LOG.LINUX_FILESYSTEM_LOG_LEVEL)

MOUNT_PATH = "/"

class LinuxFilesystem(ThreadedUpdatable):

    thread_name = "linux_filesystem"
    update_interval = TIME.STORAGE_INTERVAL

    def __init__(self, state_manager: StateManager):
        self.state_manager = state_manager
        self.updated_signal = Signal()  # kwargs: tree_list: List[FileTree]
        self.inserted_signal = Signal()  # kwargs: root: str, files: FileTree
        self.ejected_signal = Signal()  # kwargs: root: str

        self.mounted_fs_dict = {}
        self.mounted_dir_dict = {}

        # Keeps track of mountpoints where the mounted event has not been
        # sent yet
        self.freshly_mounted = set()

        self.fs_mounts = FSMounts()
        self.dir_mounts = DirMounts()
        self.fs_mounts.mounted_signal.connect(self.fs_mounted)
        self.fs_mounts.unmounted_signal.connect(self.fs_unmounted)
        self.dir_mounts.mounted_signal.connect(self.dir_mounted)
        self.dir_mounts.unmounted_signal.connect(self.dir_unmounted)

        super().__init__()

    def update(self):
        # Do not update while printing
        if self.state_manager.get_state() in PRINTING_STATES:
            return

        self.fs_mounts.update()
        self.dir_mounts.update()
        super().update()

    def fs_mounted(self, sender, mount_point: MountPoint):
        self.mounted_fs_dict[mount_point.path] = mount_point
        self.mounted(mount_point)

    def dir_mounted(self, sender, mount_point: MountPoint):
        self.mounted_dir_dict[mount_point.path] = mount_point
        self.mounted(mount_point)

    def mounted(self, mount_point: MountPoint):
        self.freshly_mounted.add(mount_point.path)

    def fs_unmounted(self, sender, mount_point: MountPoint):
        del self.mounted_fs_dict[mount_point.path]
        self.unmounted(mount_point)

    def dir_unmounted(self, sender, mount_point: MountPoint):
        del self.mounted_dir_dict[mount_point.path]
        self.unmounted(mount_point)

    def unmounted(self, mount_point: MountPoint):
        # If we didn't send the inserted event yet and the mountpoint has been
        # removed, remove it from freshly mounted ones (just to be sure)
        try:
            self.freshly_mounted.remove(mount_point.path)
        except KeyError:
            pass
        name = os.path.basename(mount_point.path)
        self.ejected_signal.send(self, root=f"{MOUNT_PATH}{name}")

    def _update(self):
        tree_list = []

        mount_point_list = (list(self.mounted_fs_dict.values()) +
                            list(self.mounted_dir_dict.values()))

        for mount_point in mount_point_list:
            name = os.path.basename(mount_point.path)
            tree = self.get_subtree(mount_point)

            if mount_point.path in self.freshly_mounted:
                self.freshly_mounted.remove(mount_point.path)
                log.debug(f"root='{tree.path_from_mount}'")
                self.inserted_signal.send(self, root=tree.full_path,
                                          files=tree.to_api_file_tree())

            tree_list.append(tree)

        self.updated_signal.send(self, tree_list=tree_list)

    def get_subtree(self, mount: MountPoint):
        name = os.path.basename(mount.path)
        tree = InternalFileTree(file_type=FileType.MOUNT,
                                full_fs_path=mount.path,
                                path=name, ro=mount.ro,
                                mounted_at=MOUNT_PATH)

        walker = os.walk(mount.path)
        for directory in walker:
            scan_result = os.scandir(directory[0])
            for file in scan_result:
                if os.access(file.path, os.R_OK):
                    self.add_file(tree, file)

        return tree

    def add_file(self, tree: InternalFileTree, file):
        path = file.path

        # Cut out the mountpoint path.
        # Use the name without leading or trailing slashes.
        tree_path = path[len(tree.full_fs_path):].strip("/")
        stats = file.stat()
        size = stats.st_size
        m_time = self.get_m_time(stats.st_mtime)
        ro = not os.access(file.path, os.W_OK)
        # log.debug(f"Adding file {tree_path}, os path: {path} to "
        #           f"tree name {tree.path}")
        tree.add_file(path=tree_path, size=size, ro=ro, m_time=m_time,
                      full_fs_path=path)

    def get_m_time(self, timestamp):
        m_datetime = datetime.fromtimestamp(timestamp)
        return (m_datetime.year, m_datetime.month, m_datetime.day,
                m_datetime.hour, m_datetime.minute, m_datetime.second)

    def stop(self):
        super().stop()
        self.fs_mounts.stop()
