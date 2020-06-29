import logging
import re
from enum import Enum
from threading import Thread
from typing import Dict, Set, Callable

from old_buddy.modules.connect_api import FileType, FileTree, States
from old_buddy.modules.state_manager import StateManager
from old_buddy.modules.serial import Serial, OutputCollector, WriteIgnored
from old_buddy.settings import SD_LIST_TIMEOUT, SD_CARD_LOG_LEVEL, QUIT_INTERVAL, SD_INTERVAL
from old_buddy.util import run_slowly_die_fast

log = logging.getLogger(__name__)
log.setLevel(SD_CARD_LOG_LEVEL)


BEGIN_FILES_REGEX = re.compile(r"^Begin file list$")
FILE_PATH_REGEX = re.compile(r"^(/?[^/]*)+ (\d+)$")
END_FILES_REGEX = re.compile(r"^End file list$")

SD_PRESENT_REGEX = re.compile(r"^(echo:SD card ok)|(echo:SD init fail)$")
INSERTED_REGEX = re.compile(r"^(echo:SD card ok)$")


class CouldNotConstructTree(RuntimeError):
    ...


class InternalFileTree:

    @staticmethod
    def new_root_node():
        return InternalFileTree(file_type=FileType.DIR, path="sd_card")

    def __init__(self, file_type: FileType = None, path: str = None, ro: bool = None, size: int = None,
                 m_date: int = None, m_time: int = None, parent: 'InternalFileTree' = None):

        self.type = file_type
        self.path = path
        self.ro = ro
        self.size = size
        self.m_date = m_date
        self.m_time = m_time
        self.descendants_set: Set[InternalFileTree] = set()
        self.chilren_dict: Dict[str, InternalFileTree] = {}
        self.full_path = self.get_full_path()
        self._parent: InternalFileTree = parent

    def __hash__(self):
        return hash((self.type, self.ro, self.size, self.m_date, self.m_time))

    @property
    def parent(self):
        return self._parent

    @parent.setter
    def parent(self, parent: 'InternalFileTree'):
        self._parent = parent
        self.full_path = self.get_full_path()

    def add_child(self, child: 'InternalFileTree'):
        self.chilren_dict[child.path] = child
        if child.parent is None:
            child.parent = self
        return child

    def child_from_path(self, line: str):
        """Expected to be called only on the root element, otherwise diffs break"""
        log.debug(f"Parsing line {line}")
        clean_line = line.strip("/")
        parts = clean_line.split("/", 1)

        if len(parts) == 2:  # Need to insert this deeper onto the tree, recurse
            path, rest = parts

            if path not in self.chilren_dict:
                child = InternalFileTree(file_type=FileType.DIR, path=path)
                self.add_child(child)

            log.debug(f"The file is in a directory {path}. Adding it inside")
            added_child = self.chilren_dict[path].child_from_path(rest)

        else:  # Insert to this level
            path, str_size = parts[0].split(" ")
            size = int(str_size)
            child = InternalFileTree(file_type=FileType.FILE, path=path, size=size)
            log.debug(f"Added a file {path} {size/1000}kb to self")
            added_child = self.add_child(child)

        # if success (?) not expecting invalid strings, so I'm not checking
        self.descendants_set.add(added_child)
        return added_child

    def get_full_path(self):
        path = []
        current_node = self
        while current_node.parent is not None: # We do not need the root node's name, so this is sufficient
            path.append(current_node.path)

        return "/" + "/".join(path)

    def diff(self, other_tree: 'InternalFileTree'):
        removed_files = self.descendants_set.difference(other_tree.descendants_set)
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
        file_tree.type = self.type
        file_tree.path = self.path
        file_tree.ro = self.ro
        file_tree.size = self.size
        file_tree.m_date = self.m_date
        file_tree.m_time = self.m_time
        file_tree.children = list(self.chilren_dict.values())
        return file_tree


class SDPresence(Enum):
    YES = "YES"
    UNSURE = "UNSURE"
    NO = "NO"


class SDState:

    def __init__(self, serial: Serial, state_manager: StateManager, state_changed_callback: Callable[[], None]):
        self.state_changed_callback = state_changed_callback
        self.state_manager = state_manager
        self.serial = serial

        self.sd_present: SDPresence = SDPresence.UNSURE
        self.previous_sd_present: SDPresence = SDPresence.UNSURE

        self.serial.register_output_handler(INSERTED_REGEX, lambda: self.inserted())

    def inserted(self):
        self.sd_present = SDPresence.YES
        self.state_changed_callback()

    def unsure(self):
        """
        Calling this can be disruptive to the user experience, the card will reload.
        If there is nothing on the SD card or if we suspect there is no SD card, calling this should be fine
        """
        try:
            match = self.serial.write_wait_response("M21", wait_for_regex=SD_PRESENT_REGEX)
        except TimeoutError:
            log.debug("Failed determining the SD presence.")
        else:
            if match.groups()[0] is not None:
                self.sd_present = SDPresence.YES
            else:
                self.sd_present = SDPresence.NO
            self.state_changed_callback()


class SDCard:

    def __init__(self, serial: Serial, state_manager: StateManager):
        self.state_manager = state_manager
        self.serial = serial

        self.running = True

        self.sd_state = SDState(self.serial, self.state_manager, self.sd_state_changed)
        self.file_tree: InternalFileTree = InternalFileTree.new_root_node()
        self.previous_file_tree: InternalFileTree = self.file_tree

        self.sd_update_thread = Thread(target=self.keep_updating_sd_stuff)
        self.sd_update_thread.start()

    def keep_updating_sd_stuff(self):
        run_slowly_die_fast(lambda: self.running, QUIT_INTERVAL, SD_INTERVAL, self.update_sd_stuff)

    def update_sd_stuff(self):
        if self.state_manager.base_state == States.BUSY:
            log.debug("Not bothering with updating file structure, printer looks busy")
            return

        self.previous_file_tree = self.file_tree
        try:
            self.file_tree = self.construct_file_tree()
        except CouldNotConstructTree:
            log.error("No file tree could be constructed, printer seems busy")
        else:
            # If we do not know the sd state and no files were found, check the SD presence
            # If there were files and now there is nothing, the SD was most likely ejected. So check for that
            if not self.file_tree.descendants_set:
                if self.previous_file_tree.descendants_set or self.sd_state == SDPresence.UNSURE:
                    self.sd_state.unsure()

    def construct_file_tree(self):
        tree = InternalFileTree(path="SD Card", file_type=FileType.MOUNT)

        # TODO: sd state
        # if not self.sd_present:
        #     return tree

        collector = OutputCollector(begin_regex=BEGIN_FILES_REGEX, end_regex=END_FILES_REGEX,
                                    capture_regex=FILE_PATH_REGEX, timeout=SD_LIST_TIMEOUT, debug=True)
        try:
            self.serial.write("M20")
        except WriteIgnored:
            raise CouldNotConstructTree("No tree was constructed")

        try:
            output = collector.wait_for_output()
        except TimeoutError:
            raise CouldNotConstructTree()

        for match in output:
            tree.child_from_path(match.string)

        return tree

    def get_api_file_tree(self):
        self.file_tree.to_api_file_tree()

    def sd_state_changed(self):
        if self.sd_state.previous_sd_present == self.sd_state.sd_present == SDPresence.YES:
            # This would typically happen if someone removed and inserted an SD card faster, than we update our state.
            # Set previous file tree as if we registered the unplug
            self.previous_file_tree = InternalFileTree.new_root_node()

            # TODO: media ejected
            # TODO: Media inserted

        elif self.sd_state.previous_sd_present != self.sd_state.sd_present: # what about going from unsure? no events?
            if self.sd_state.sd_present == SDPresence.YES:
                # TODO: Media inserted
                ...

            elif self.sd_state.sd_present == SDPresence.NO:
                # TODO: Media ejected
                ...

    def stop(self):
        self.running = False
        self.sd_update_thread.join()



