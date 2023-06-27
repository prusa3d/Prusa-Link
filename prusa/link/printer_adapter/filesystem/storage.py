"""
Contains the implementation of Storage, FSStorage and FolderStorage for keeping
track of Linux and folder storage.
"""
import abc
import logging
import os
import select
from typing import ClassVar, List, Set

from blinker import Signal  # type: ignore

from ...config import Config
from ...const import (
    BLACKLISTED_NAMES,
    BLACKLISTED_PATHS,
    BLACKLISTED_TYPES,
    DIR_RESCAN_INTERVAL,
    QUIT_INTERVAL,
)
from ...util import ensure_directory, get_clean_path
from ..model import Model
from ..structures.module_data_classes import StorageData
from ..updatable import ThreadedUpdatable

log = logging.getLogger(__name__)


class Storage(ThreadedUpdatable):
    """
    This module is the base for modules tracking attaching and detaching
    of storage
    """

    paths_to_storage: ClassVar[List[str]] = []

    def __init__(self, model: Model):
        super().__init__()
        self.model: Model = model

        self.attached_signal = Signal()  # kwargs = path: str
        self.detached_signal = Signal()  # kwargs = path: str

        self.data: StorageData = self.get_data_object()

        self.data.blacklisted_paths = self._get_clean_paths(BLACKLISTED_PATHS)
        self.data.blacklisted_names = BLACKLISTED_NAMES

        candidate_storage = self._get_clean_paths(self.paths_to_storage)

        # Cannot start with blacklisted paths
        finalist_storage = set(
            self.filter_blacklisted_paths(candidate_storage,
                                          self.data.blacklisted_paths))

        # Cannot have a blacklisted name
        self.data.configured_storage = set(
            self.filter_blacklisted_names(finalist_storage,
                                          self.data.blacklisted_names))

        log.debug("Configured mounpoints: %s", self.data.configured_storage)

        self.data.attached_set = set()

    def update(self):
        """
        Synchronizes our data model with the OS, produces signals for
        storage we're interested in
        """

        new_storage_set = self.get_storage()

        added, removed = self.get_differences(new_storage_set)

        for path in added:
            log.info("Newly attached %s", path)
            self.attached_signal.send(self, path=path)

        for path in removed:
            log.info("Detached %s", path)
            self.detached_signal.send(self, path=path)

        self.data.attached_set = new_storage_set

    @staticmethod
    def filter_blacklisted_paths(candidate_list, black_list):
        """Filter out anything that is inside of the blacklisted dirs"""
        filtered = []

        for candidate in candidate_list:
            if not Storage.is_path_blacklisted(candidate, black_list):
                filtered.append(candidate)
        return filtered

    @staticmethod
    def filter_blacklisted_names(candidate_list, black_list):
        """Filter out anything that is inside of the blacklisted dirs"""
        filtered = []

        for candidate in candidate_list:
            if not Storage.is_path_blacklisted(candidate, black_list):
                filtered.append(candidate)
        return filtered

    @staticmethod
    def is_path_blacklisted(candidate, black_list):
        """Returns the blacklist item that caused tha candidate to be flagged
        """
        for blacklisted in black_list:
            if candidate.startswith(blacklisted):
                log.warning("Ignoring %s because it's blacklisted by %s",
                            candidate, blacklisted)
                return True
        return False

    @staticmethod
    def is_name_blacklisted(candidate, black_list):
        """Returns the blacklist item that caused tha candidate to be flagged
        """
        clean_candidate = candidate.strip("/").split("/")[-1]
        for blacklisted in black_list:
            if clean_candidate == blacklisted:
                log.warning("Ignoring %s because it's blacklisted by %s",
                            clean_candidate, blacklisted)
                return True
        return False

    @staticmethod
    def _get_clean_paths(dirty_paths):
        """
        Cleans a list of paths by converting them to Path objects and back
        """
        return [get_clean_path(path) for path in dirty_paths]

    def get_differences(self, new_storage_set: Set[str]):
        """Retur the added and removed items from a given set"""
        removed = self.data.attached_set.difference(new_storage_set)
        added = new_storage_set.difference(self.data.attached_set)
        return added, removed

    @abc.abstractmethod
    def get_storage(self) -> Set[str]:
        """
        The implementation is expected to return a set of valid
        storage based on its configuration
        """

    @abc.abstractmethod
    def get_data_object(self) -> StorageData:
        """
        There need to be two different object for the two different storage
        types. This method takes care of that
        """


class FilesystemStorage(Storage):
    """
    Responsible for reporting which valid linux storage was attached
    """

    thread_name = "filesystem_storage_thread"
    update_interval = 0  # The waiting is done in epoll timeout instead of here

    def __init__(self, model: Model, cfg: Config):
        FilesystemStorage.paths_to_storage = \
            list(cfg.printer.storage)

        model.filesystem_storage = StorageData(blacklisted_paths=[],
                                               blacklisted_names=[],
                                               configured_storage=set(),
                                               attached_set=set())
        # Call this after initializing the data
        super().__init__(model)

        # Force the update, even if no events are caught, we need to see
        # which things are attached, before beginning to only observe changes
        self.force_update = True

        # pylint: disable=consider-using-with
        self.mtab = open("/etc/mtab", "r", encoding='utf-8')
        self.epoll_obj = select.epoll(1)
        self.epoll_obj.register(self.mtab.fileno(), select.EPOLLOUT)

    def get_data_object(self) -> StorageData:
        return self.model.filesystem_storage

    def get_storage(self) -> Set[str]:
        """
        Checks epoll for storage changes. if there are changes, gets
        a new storage list from mtab.
        If not, returns the current storage
        """
        # Non-empty epoll result means something regarding storage has changed
        epoll_result = self.epoll_obj.poll(QUIT_INTERVAL)
        if epoll_result or self.force_update:
            self.force_update = False

            self.mtab.seek(0)
            new_storage_set: Set[str] = set()

            line_list = self.mtab.readlines()
            for line in line_list:
                _name, string_path, fs_type, *_ = line.split(" ")
                clean_path = get_clean_path(string_path)

                if self.storage_belongs(clean_path, fs_type):
                    new_storage_set.add(clean_path)
            # If something changed, return the newly constructed dict
            return new_storage_set
        # Otherwise, return the same dict
        return self.data.attached_set

    def storage_belongs(self, path, fs_type):
        """Checks if we are interested in tracking a given storage"""
        is_wanted = str(path) in self.data.configured_storage
        type_valid = is_wanted and fs_type not in BLACKLISTED_TYPES
        return is_wanted and type_valid

    def stop(self):
        """Stops this component"""
        super().stop()
        self.mtab.close()


class FolderStorage(Storage):
    """
    Configured directories are reported as storage too,
    having the fs_type of "directory".
    """

    def __init__(self, model: Model, cfg: Config):
        FolderStorage.paths_to_storage = [cfg.printer.directory]

        model.folder_storage = StorageData(blacklisted_paths=[],
                                           blacklisted_names=[],
                                           configured_storage=set(),
                                           attached_set=set())

        # Call this after initializing the data
        super().__init__(model)

        for directory in self.data.configured_storage:
            ensure_directory(directory)

    thread_name = "folder_storage_thread"
    update_interval = DIR_RESCAN_INTERVAL

    def get_data_object(self) -> StorageData:
        """
        There need to be two different object for the two different storage
        types. This method takes care of that
        """
        return self.model.folder_storage

    def get_storage(self) -> Set[str]:
        new_directory_set: Set[str] = set()
        for directory in self.data.configured_storage:

            # try to create non-existing ones
            try:
                ensure_directory(directory)
            except OSError:
                log.exception("Cannot create a directory at %s", directory)

            if self.dir_belongs(directory):
                new_directory_set.add(directory)
            else:
                log.warning("Directory %s does not exist or isn't readable.",
                            directory)
        return new_directory_set

    @staticmethod
    def dir_belongs(directory: str):
        """
        Checks if we are interested in tracking a given directory storage
        """
        exists = os.path.exists(directory)
        readable = exists and os.access(directory, os.R_OK)
        return exists and readable
