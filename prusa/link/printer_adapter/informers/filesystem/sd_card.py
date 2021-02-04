"""
The SD state can start only in the UNSURE state, we know nothing

From there, we will ask the printer about the files present.
If there are files, the SD card is present.
If not, we still know nothing and need to ask the printer to re-init the card
that provides the information about SD card presence

Now there is an SD ejection message, so no more fortune-telling wizardry
is happening

Unlikely now, was very likely before:
The card removal could've gone unnoticed and the printer is telling
us about an SD insertion. Let's tell connect the card got removed and go to the
INITIALISING state
"""

import logging
import re
from pathlib import Path
from time import time

from blinker import Signal

from prusa.link.printer_adapter.informers.state_manager import StateManager
from prusa.link.printer_adapter.input_output.serial.serial_queue import \
    SerialQueue
from prusa.link.printer_adapter.input_output.serial.serial_reader import \
    SerialReader
from prusa.link.printer_adapter.input_output.serial.helpers import \
    wait_for_instruction, enqueue_matchable, enqueue_collecting
from prusa.link.printer_adapter.model import Model
from prusa.link.printer_adapter.structures.model_classes import SDState
from prusa.link.printer_adapter.structures.regular_expressions import \
    SD_PRESENT_REGEX, BEGIN_FILES_REGEX, END_FILES_REGEX, \
    SD_EJECTED_REGEX, LFN_CAPTURE
from prusa.link.printer_adapter.const import PRINTING_STATES, \
    SD_INTERVAL, SD_FILESCAN_INTERVAL, SD_MOUNT_NAME, \
    SFN_TO_LFN_EXTENSIONS
from prusa.link.printer_adapter.updatable import ThreadedUpdatable
from prusa.link.sdk_augmentation.file import SDFile

log = logging.getLogger(__name__)


class SDCard(ThreadedUpdatable):
    thread_name = "sd_updater"

    # Cycle fast, but re-scan only on events or in big intervals
    update_interval = SD_INTERVAL

    def __init__(self, serial_queue: SerialQueue, serial_reader: SerialReader,
                 state_manager: StateManager, model: Model):

        self.tree_updated_signal = Signal()  # kwargs: tree: FileTree
        self.state_changed_signal = Signal()  # kwargs: sd_state: SDState
        self.sd_mounted_signal = Signal()  # kwargs: files: SDFile
        self.sd_unmounted_signal = Signal()

        self.serial_reader = serial_reader
        self.serial_reader.add_handler(SD_PRESENT_REGEX, self.sd_inserted)
        self.serial_reader.add_handler(SD_EJECTED_REGEX, self.sd_ejected)
        self.serial_queue: SerialQueue = serial_queue
        self.state_manager = state_manager
        self.model = model
        
        self.data = self.model.sd_card

        self.data.expecting_insertion = False
        self.data.invalidated = True
        self.data.last_updated = time()
        self.data.sd_state = SDState.UNSURE
        self.data.files = None
        self.data.lfn_to_sfn_paths = {}
        self.data.sfn_to_lfn_paths = {}

        super().__init__()

    def update(self):
        # Do not update while printing
        if self.state_manager.get_state() in PRINTING_STATES:
            return

        # Do not update, when the interval didn't pass and the tree wasn't
        # invalidated
        if not self.data.invalidated and \
                time() - self.data.last_updated < SD_FILESCAN_INTERVAL:
            return

        self.data.last_updated = time()
        self.data.invalidated = False

        self.data.files = self.construct_file_tree()

        # If we do not know the sd state and no files were found,
        # check the SD presence
        if self.data.sd_state == SDState.UNSURE:
            if self.data.files:
                self.sd_state_changed(SDState.PRESENT)
            else:
                self.decide_presence()

        if self.data.sd_state == SDState.INITIALISING:
            self.sd_state_changed(SDState.PRESENT)

        self.tree_updated_signal.send(self, tree=self.data.files)

    def construct_file_tree(self):
        if self.data.sd_state == SDState.ABSENT:
            return None

        tree = SDFile(name=SD_MOUNT_NAME, is_dir=True, ro=True)

        instruction = enqueue_collecting(self.serial_queue, "M20 -L",
                                         begin_regex=BEGIN_FILES_REGEX,
                                         capture_regex=LFN_CAPTURE,
                                         end_regex=END_FILES_REGEX)
        wait_for_instruction(instruction, lambda: self.running)

        # Captured can be three distinct lines. Dir entry, exit or a file
        # listing. We need to maintain the info about which dir we are currently
        # in, as that doesn't repeat in the file listing lines
        current_dir = Path("/")
        lfn_to_sfn_paths = {}
        sfn_to_lfn_paths = {}
        for match in instruction.captured:
            groups = match.groups()
            if groups[0] is not None:  # Dir entry
                current_dir = current_dir.joinpath(groups[2])
                short_dir_name = Path(groups[1]).name
                current_dir = self.ensure_uniqueness(current_dir,
                                                     short_dir_name, tree)
            elif groups[3] is not None:  # The list item
                # Parse
                short_path_string = groups[4]
                short_name = Path(short_path_string).name
                short_extension = groups[5]
                long_extension = SFN_TO_LFN_EXTENSIONS[short_extension]
                raw_long_file_name = groups[6]
                long_file_name = self.ensure_extension(raw_long_file_name,
                                                       short_extension,
                                                       long_extension)

                long_path = current_dir.joinpath(long_file_name)
                long_path = self.ensure_uniqueness(long_path, short_name, tree)
                long_path_string = str(long_path)

                size = int(groups[7])

                # Add translation between the two
                log.debug(f"Adding translation between {long_path_string} "
                          f"and {short_path_string}")
                lfn_to_sfn_paths[long_path_string] = short_path_string
                sfn_to_lfn_paths[short_path_string] = long_path_string
                tree.add_by_path(long_path, size)
            elif groups[8] is not None:  # Dir exit
                current_dir = current_dir.parent

        # Try to be as atomic as possible
        self.data.lfn_to_sfn_paths = lfn_to_sfn_paths
        self.data.sfn_to_lfn_paths = sfn_to_lfn_paths
        return tree

    def ensure_uniqueness(self, path: Path, short_name, tree):
        log.debug(f"Ensuring uniqueness of {path}. Got {tree.get(path.parts)} "
                  f"from the already constructed part of the file tree.")
        if tree.get(path.parts[1:]) is not None:
            unique_name = f"{short_name} - {Path(path).name}"
            path = path.parent.joinpath(unique_name)
            log.warning(f"Name conflict! Using a fallback path {path}.")

            # Verify, it's really unique
            if tree.get(path.parts[1:]) is not None:
                log.error(
                    "Can't resolve the name conflict!")
        return path

    def ensure_extension(self, filename: str, short_extension: str,
                         long_extension: str):
        if not filename.endswith(short_extension) and \
                not filename.endswith(long_extension):
            original_extension = filename.split(".")[-1]
            # The filenames can end in parts of short or long versions of
            # their extensions. If that extension is incomplete,
            # let's use the long one, cause it's shorter to write
            if original_extension in long_extension or \
                    original_extension in short_extension:
                filename = filename[:-len(original_extension)]
                filename += long_extension
            else:
                if not filename.endswith("."):
                    filename += "."
                filename += long_extension
        return filename

    def sd_inserted(self, sender, match: re.Match):
        """
        If received while expecting it, stop expecting another one
        If received unexpectedly, this signalises someone physically
        inserting a card
        """
        # Using a multi-purpose regex, only interested in the first group
        if match.groups()[0]:
            if self.data.expecting_insertion:
                self.data.expecting_insertion = False
            else:
                self.data.invalidated = True
                self.sd_state_changed(SDState.INITIALISING)

    def sd_ejected(self, sender, match: re.Match):
        self.data.invalidated = True
        self.sd_state_changed(SDState.ABSENT)

    def sd_state_changed(self, new_state):
        log.debug(f"SD state changed from {self.data.sd_state} to "
                  f"{new_state}")

        if self.data.sd_state in {SDState.INITIALISING, SDState.UNSURE} and \
                new_state == SDState.PRESENT:
            log.debug("SD Card inserted")
            self.sd_mounted_signal.send(self, files=self.data.files)

        elif self.data.sd_state == SDState.PRESENT and \
                new_state in {SDState.ABSENT, SDState.INITIALISING}:
            log.debug("SD Card removed")
            self.sd_unmounted_signal.send(self)

        self.data.sd_state = new_state
        self.state_changed_signal.send(self, sd_state=self.data.sd_state)

    def decide_presence(self):
        """
        Calling this can be disruptive to the user experience,
        the card will reload. If there is nothing on the SD card or
        if we suspect there is no SD card, calling this should be fine
        """
        self.data.expecting_insertion = True
        instruction = enqueue_matchable(self.serial_queue, "M21",
                                        SD_PRESENT_REGEX)
        wait_for_instruction(instruction, lambda: self.running)
        self.data.expecting_insertion = False

        if not instruction.is_confirmed():
            log.debug("Failed determining the SD presence.")
        else:
            match = instruction.match()
            if match is not None and match.groups()[0] is not None:
                if self.data.sd_state != SDState.PRESENT:
                    self.sd_state_changed(SDState.PRESENT)
            else:
                self.sd_state_changed(SDState.ABSENT)
