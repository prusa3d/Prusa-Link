import logging
import os
from enum import Enum
from pathlib import Path
from typing import Set, Dict

from pydantic import BaseModel

from old_buddy.default_settings import get_settings
from old_buddy.structures.model_classes import FileType, FileTree

LOG = get_settings().LOG
TIME = get_settings().TIME

log = logging.getLogger(__name__)
log.setLevel(LOG.SD_CARD_LOG_LEVEL)


class InternalFileTree:
    """
    The expected structure is root of type DIR containing only nodes
    of type MOUNT, diffs are only possible on

    """

    @staticmethod
    def new_root_node():
        return InternalFileTree(file_type=FileType.DIR, path="/")

    def __init__(self, file_type: FileType = None, path: str = None,
                 ro: bool = None, size: int = None,
                 m_date: int = None, m_time: int = None,
                 parent: 'InternalFileTree' = None,

                 full_fs_path: str = None,
                 mounted_at=None,
                 ancestor_mount=None):

        self.type = file_type
        self.path = path
        self.ro = ro
        self.size = size
        self.m_date = m_date
        self.descendants_set: Set[InternalFileTree] = set()
        self.children_dict: Dict[str, InternalFileTree] = {}
        self._parent: InternalFileTree = parent

        # Where in the tree are we mounted?
        # True even if we aren't attached there yet
        self.mounted_at = mounted_at
        # Which mount does this belong to?
        self.ancestor_mount: InternalFileTree = ancestor_mount

        # Path to the nearest mount or to the root
        self.path_from_mount = self.get_path_from_mount()

        # For linux FS, where the file really is.
        self.full_fs_path = full_fs_path

    def __hash__(self):
        return hash((self.type, self.ro, self.size, self.m_date,
                     self.path_from_mount, self.ancestor_mount.mounted_at))

    def __str__(self):
        output = self.full_path + "\n"
        for child in self.children_dict.values():
            output += child.__str__()
        return output

    def __bool__(self):
        return bool(self.children_dict)

    @property
    def parent(self):
        return self._parent

    @parent.setter
    def parent(self, parent: 'InternalFileTree'):
        self._parent = parent

    def add_child(self, child: 'InternalFileTree'):
        self.children_dict[child.path] = child
        if child.type == FileType.MOUNT and self.ancestor_mount is not None:
            raise RuntimeError("Nested mounts are not supported!")

        if child.parent is None:
            child.parent = self

        if self.type == FileType.MOUNT:
            child.ancestor_mount = self
        else:
            child.ancestor_mount = self.ancestor_mount

        if self.ancestor_mount is not None:
            self.ancestor_mount.descendants_set.add(child)
        return child

    def add_file_from_line(self, line: str):

        path, str_size = line.rsplit(" ", 1)
        clean_path = path.strip("/")
        size = int(str_size)

        self.add_file(path=clean_path, size=size, ro=True)

    def add_file(self, path, size, ro=True, m_time=None, full_fs_path=None):
        parts = Path(path).parts

        node = self
        for part in parts[1:-1]:
            if part not in node.children_dict:
                child = InternalFileTree(file_type=FileType.DIR, path=part,
                                         parent=node, ro=ro, m_time=m_time,
                                         full_fs_path=full_fs_path)
                node.add_child(child)

            node = node.children_dict[part]

        # last one is the file itself
        leaf = InternalFileTree(file_type=FileType.FILE, path=parts[-1],
                                size=size, parent=node, ro=ro, m_time=m_time,
                                full_fs_path=full_fs_path)
        node.add_child(leaf)

        # Finally, lets add the leaf to descendant sets
        while node is not None:
            if node.type == FileType.MOUNT:
                node.descendants_set.add(leaf)
            node = node.parent

    def get_file(self, path_string: str):
        path = Path(path_string)
        clean_path_string = str(path)
        log.debug(f"Searching for file {path_string} in {self.full_path}")
        if not clean_path_string.startswith(self.full_path):
            raise FileNotFoundError("The file you requested is not in this "
                                    "subtree")

        node_path = Path(self.full_path)
        parts_to_descendant = path.parts[len(node_path.parts):]

        node = self
        for part in parts_to_descendant:
            try:
                log.debug(f"Getting {part} from node {node.full_path}")
                node = node.children_dict[part]
            except KeyError:
                raise FileNotFoundError("The file you requested is not "
                                        "available anymore")
        return node

    def get_path_from_mount(self):
        """
        Gets the path from the closest mountpoint with the mountpoint name
        as the first path member
        If that's not possible, returns the path to root
        """
        path = []
        current_node = self

        # Get the path from the root or mount
        while (current_node is not None and
               current_node.type != FileType.MOUNT):
            path.append(current_node.path)
            current_node = current_node.parent

        if current_node is not None and current_node.type == FileType.MOUNT:
            path.append(current_node.path)
        if path:
            return os.path.join(*reversed(path))
        else:
            return ""

    @property
    def full_path(self):
        if self.ancestor_mount is not None:
            return os.path.join(self.ancestor_mount.mounted_at,
                                self.path_from_mount)
        elif self.type == FileType.MOUNT:
            return os.path.join(self.mounted_at,
                                self.path_from_mount)
        else:
            return self.path_from_mount

    def diff(self, other_tree: 'InternalFileTree'):
        if not (self.type == other_tree.type == FileType.MOUNT):
            raise RuntimeError("Cannot compare anything else than mounts")

        removed_files = self.descendants_set.difference(
            other_tree.descendants_set)
        new_files = self.descendants_set.difference(other_tree.descendants_set)

        removed_paths = {file.full_path for file in removed_files}
        new_paths = {file.full_path for file in new_files}

        changed_file_paths = removed_paths.intersection(new_paths)

        for file in removed_files:
            if file.full_path in changed_file_paths:
                log.debug(f"File at {file.full_path} has been changed.")
            else:
                log.debug(f"File at {file.full_path} has been removed.")

        for file in new_files:
            if file.full_path not in changed_file_paths:
                log.debug(f"File at {file.full_path} has been created.")

    def to_api_file_tree(self):
        file_tree = FileTree()
        if self.type == FileType.MOUNT:
            file_tree.type = FileType.DIR.name
        else:
            file_tree.type = self.type.name
        file_tree.path = self.path
        file_tree.ro = self.ro
        file_tree.size = self.size
        file_tree.m_date = self.m_date
        unconverted_children = list(self.children_dict.values())
        file_tree.children = [child.to_api_file_tree()
                              for child in unconverted_children]
        if not file_tree.children:
            file_tree.children = None
        return file_tree


class SDState(Enum):
    PRESENT = "PRESENT"
    INITIALISING = "INITIALISING"
    UNSURE = "UNSURE"
    ABSENT = "ABSENT"


class MountPoint(BaseModel):

    ro: bool = True
    path: str = None
    type: str = None
