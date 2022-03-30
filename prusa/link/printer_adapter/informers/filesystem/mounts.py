"""Contains the implementation of Mounts, FSMounts and DirMounts for keeping
track of Linux and directory mountpoints."""
import abc
import logging
import os
import select
from typing import Set, List

from blinker import Signal  # type: ignore

from ...model import Model
from ...structures.module_data_classes import MountsData
from ....config import Config
from ...const import BLACKLISTED_PATHS, \
    BLACKLISTED_NAMES, BLACKLISTED_TYPES, QUIT_INTERVAL, DIR_RESCAN_INTERVAL
from ...updatable import ThreadedUpdatable
from ...util import get_clean_path, ensure_directory

log = logging.getLogger(__name__)


class Mounts(ThreadedUpdatable):
    """
    This module is the base for modules tracking mounting and unmounting of
    supported mountpints
    """

    paths_to_mount: List[str] = []

    def __init__(self, model: Model):
        super().__init__()
        self.model: Model = model

        self.mounted_signal = Signal()  # kwargs = path: str
        self.unmounted_signal = Signal()  # kwargs = path: str

        self.data: MountsData = self.get_data_object()

        self.data.blacklisted_paths = self._get_clean_paths(BLACKLISTED_PATHS)
        self.data.blacklisted_names = BLACKLISTED_NAMES

        candidate_mountpoints = self._get_clean_paths(self.paths_to_mount)

        # Cannot start with blacklisted paths
        finalist_mountpoints = set(
            self.filter_blacklisted_paths(candidate_mountpoints,
                                          self.data.blacklisted_paths))

        # Cannot have a blacklisted name
        self.data.configured_mounts = set(
            self.filter_blacklisted_names(finalist_mountpoints,
                                          self.data.blacklisted_names))

        log.debug("Configured mounpoints: %s", self.data.configured_mounts)

        self.data.mounted_set = set()

    def update(self):
        """
        Synchronizes our data model with the OS, produces signals for
        mounts we're interested in
        """

        # Add non mount directories to the mounts
        new_mount_set = self.get_mountpoints()

        added, removed = self.get_differences(new_mount_set)

        for path in added:
            log.info("Newly mounting %s", path)
            self.mounted_signal.send(self, path=path)

        for path in removed:
            log.info("Unmounted %s", path)
            self.unmounted_signal.send(self, path=path)

        self.data.mounted_set = new_mount_set

    @staticmethod
    def filter_blacklisted_paths(candidate_list, black_list):
        """Filter out anything that is inside of the blacklisted dirs"""
        filtered = []

        for candidate in candidate_list:
            if not Mounts.is_path_blacklisted(candidate, black_list):
                filtered.append(candidate)
        return filtered

    @staticmethod
    def filter_blacklisted_names(candidate_list, black_list):
        """Filter out anything that is inside of the blacklisted dirs"""
        filtered = []

        for candidate in candidate_list:
            if not Mounts.is_path_blacklisted(candidate, black_list):
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

    def get_differences(self, new_mount_set: Set[str]):
        """Retur the added and removed items from a given set"""
        removed = self.data.mounted_set.difference(new_mount_set)
        added = new_mount_set.difference(self.data.mounted_set)
        return added, removed

    @abc.abstractmethod
    def get_mountpoints(self) -> Set[str]:
        """
        The implementation is expected to return a set of valid
        mountpoints based on its configuration
        """

    @abc.abstractmethod
    def get_data_object(self) -> MountsData:
        """
        There need to be two different object for the two different mount
        types. This method takes care of that
        """


class FSMounts(Mounts):
    """
    Responsible for reporting which valid linux mountpoints were attached
    """

    thread_name = "fs_mounts_thread"
    update_interval = 0  # The waiting is done in epoll timeout instead of here

    def __init__(self, model: Model, mountpoints=None):
        if mountpoints:
            FSMounts.paths_to_mount = mountpoints

        model.fs_mounts = MountsData(blacklisted_paths=[],
                                     blacklisted_names=[],
                                     configured_mounts=set(),
                                     mounted_set=set())
        # Call this after initializing the data
        super().__init__(model)

        # Force the update, even if no events are caught, we need to see
        # which things are mounted, before beginning to only observe changes
        self.force_update = True

        # pylint: disable=consider-using-with
        self.mtab = open("/etc/mtab", "r", encoding='utf-8')
        self.epoll_obj = select.epoll(1)
        self.epoll_obj.register(self.mtab.fileno(), select.EPOLLOUT)

    def get_data_object(self) -> MountsData:
        return self.model.fs_mounts

    def get_mountpoints(self):
        """
        Checks epoll for mountpoint changes. Ff there are changes, gets
        a new mountpoint list from mtab.
        If not, returns the current mountpoints
        """
        # Non empty epoll result means something regarding mounts has changed
        epoll_result = self.epoll_obj.poll(QUIT_INTERVAL)
        if epoll_result or self.force_update:
            self.force_update = False

            self.mtab.seek(0)
            new_mount_set: Set[str] = set()

            line_list = self.mtab.readlines()
            for line in line_list:
                _name, string_path, fs_type, *_ = line.split(" ")
                clean_path = get_clean_path(string_path)

                if self.mount_belongs(clean_path, fs_type):
                    new_mount_set.add(clean_path)
            # If something changed, return the newly constructed dict
            return new_mount_set
        # Otherwise, return the same dict
        return self.data.mounted_set

    def mount_belongs(self, path, fs_type):
        """Checks if we are interested in tracking a given mountpoint"""
        is_wanted = str(path) in self.data.configured_mounts
        type_valid = is_wanted and fs_type not in BLACKLISTED_TYPES
        return is_wanted and type_valid

    def stop(self):
        """Stops this component"""
        super().stop()
        self.mtab.close()


class DirMounts(Mounts):
    """
    Configured directories are reported as mountpoints too,
    having the fs_type of "directory".
    """
    def __init__(self, model: Model, cfg: Config):
        DirMounts.paths_to_mount = cfg.printer.directories

        model.dir_mounts = MountsData(blacklisted_paths=[],
                                      blacklisted_names=[],
                                      configured_mounts=set(),
                                      mounted_set=set())

        # Call this after initializing the data
        super().__init__(model)

        for directory in self.data.configured_mounts:
            ensure_directory(directory)

    thread_name = "dir_mounts_thread"
    update_interval = DIR_RESCAN_INTERVAL

    def get_data_object(self) -> MountsData:
        """
        There need to be two different object for the two different mount
        types. This method takes care of that
        """
        return self.model.dir_mounts

    def get_mountpoints(self):
        new_directory_set: Set[str] = set()
        for directory in self.data.configured_mounts:

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
        Checks if we are interested in tracking a given directory mountpoint
        """
        exists = os.path.exists(directory)
        readable = exists and os.access(directory, os.R_OK)
        return exists and readable
