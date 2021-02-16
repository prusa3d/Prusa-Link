import logging
import os
import select
from typing import Set

from blinker import Signal

from prusa.link.config import Config
from prusa.link.printer_adapter.const import BLACKLISTED_PATHS, \
    BLACKLISTED_NAMES, BLACKLISTED_TYPES, QUIT_INTERVAL, DIR_RESCAN_INTERVAL
from prusa.link.printer_adapter.updatable import ThreadedUpdatable
from prusa.link.printer_adapter.util import get_clean_path, ensure_directory

log = logging.getLogger(__name__)


class Mounts(ThreadedUpdatable):
    """
    This module is the base for modules tracking mounting and unmounting of
    supported mountpints
    """

    paths_to_mount = []

    def __init__(self, data):
        super().__init__()

        self.data = data

        self.mounted_signal = Signal()  # kwargs = path: str
        self.unmounted_signal = Signal()  # kwargs = path: str

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

        log.debug(f"Configured mounpoints: {self.data.configured_mounts}")

        self.data.mounted_set = set()

    def update(self):
        # Add non mount directories to the mounts
        new_mount_set = self.get_mountpoints()

        added, removed = self.get_differences(new_mount_set)

        for path in added:
            log.info(f"Newly mounting {path}")
            self.mounted_signal.send(self, path=path)

        for path in removed:
            log.info(f"Unmounted {path}")
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
                log.warning(f"Ignoring {candidate} because it's "
                            f"blacklisted by {blacklisted}")
                return True
        return False

    @staticmethod
    def is_name_blacklisted(candidate, black_list):
        """Returns the blacklist item that caused tha candidate to be flagged
        """
        clean_candidate = candidate.strip("/").split("/")[-1]
        for blacklisted in black_list:
            if clean_candidate == blacklisted:
                log.warning(f"Ignoring {clean_candidate} because it's "
                            f"blacklisted by {blacklisted}")
                return True
        return False

    @staticmethod
    def _get_clean_paths(dirty_paths):
        return [get_clean_path(path) for path in dirty_paths]

    def get_differences(self, new_mount_set: Set[str]):
        removed = self.data.mounted_set.difference(new_mount_set)
        added = new_mount_set.difference(self.data.mounted_set)
        return added, removed

    def get_mountpoints(self):
        raise NotImplementedError(
            "This is just a base class, don't instantiate")


class FSMounts(Mounts):
    """
    Responsible for reporting which valid linux mountpoints were attached
    """

    thread_name = "fs_mounts_thread"
    update_interval = 0  # The waiting is done in epoll timeout instead of here

    def __init__(self, data, mountpoints=None):
        if mountpoints:
            FSMounts.paths_to_mount = mountpoints
        super().__init__(data)

        # Force the update, even if no events are caught, we need to see
        # which things are mounted, before beginning to only observe changes
        self.force_update = True

        self.mtab = open("/etc/mtab", "r")
        self.epoll_obj = select.epoll(1)
        self.epoll_obj.register(self.mtab.fileno(), select.EPOLLOUT)

    def get_mountpoints(self):
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
        else:
            # Otherwise, return the same dict
            return self.data.mounted_set

    def mount_belongs(self, path, fs_type):
        is_wanted = str(path) in self.data.configured_mounts
        type_valid = is_wanted and fs_type not in BLACKLISTED_TYPES
        return is_wanted and type_valid

    def stop(self):
        super().stop()
        self.mtab.close()


class DirMounts(Mounts):
    """
    Configured directories are reported as mountpoints too,
    having the fs_type of "directory".
    """
    def __init__(self, cfg: Config, data):
        DirMounts.paths_to_mount = cfg.printer.directories
        super().__init__(data)

        for directory in self.data.configured_mounts:
            ensure_directory(directory)

    thread_name = "dir_mounts_thread"
    update_interval = DIR_RESCAN_INTERVAL

    def get_mountpoints(self):
        new_directory_set: Set[str] = set()
        for directory in self.data.configured_mounts:

            # try to create non-existing ones
            try:
                ensure_directory(directory)
            except OSError:
                log.exception(f"Cannot create a dirextory at {directory}")

            if self.dir_belongs(directory):
                new_directory_set.add(directory)
            else:
                log.warning(f"Directory {directory} does not exist or isn't "
                            f"readable.")
        return new_directory_set

    @staticmethod
    def dir_belongs(directory: str):
        exists = os.path.exists(directory)
        readable = exists and os.access(directory, os.R_OK)
        return exists and readable
