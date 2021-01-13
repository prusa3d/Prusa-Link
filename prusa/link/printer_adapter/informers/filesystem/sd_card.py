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
from enum import Enum
from time import time
from typing import Optional

from blinker import Signal

from prusa.link.printer_adapter.informers.state_manager import StateManager
from prusa.link.printer_adapter.input_output.serial.serial_queue import \
    SerialQueue
from prusa.link.printer_adapter.input_output.serial.serial_reader import \
    SerialReader
from prusa.link.printer_adapter.input_output.serial.helpers import \
    wait_for_instruction, enqueue_matchable, enqueue_collecting
from prusa.link.printer_adapter.structures.regular_expressions import \
    SD_PRESENT_REGEX, BEGIN_FILES_REGEX, END_FILES_REGEX, FILE_PATH_REGEX, \
    SD_EJECTED_REGEX
from prusa.link.printer_adapter.structures.constants import PRINTING_STATES, \
    SD_INTERVAL, SD_FILESCAN_INTERVAL
from prusa.link.printer_adapter.updatable import ThreadedUpdatable
from prusa.link.sdk_augmentation.file import SDFile

log = logging.getLogger(__name__)


class SDState(Enum):
    PRESENT = "PRESENT"
    INITIALISING = "INITIALISING"
    UNSURE = "UNSURE"
    ABSENT = "ABSENT"


class SDCard(ThreadedUpdatable):
    thread_name = "sd_updater"

    # Cycle fast, but re-scan only on events or in big intervals
    update_interval = SD_INTERVAL

    def __init__(self, serial_queue: SerialQueue, serial_reader: SerialReader,
                 state_manager: StateManager):

        self.tree_updated_signal = Signal()  # kwargs: tree: FileTree
        self.state_changed_signal = Signal()  # kwargs: sd_state: SDState
        self.sd_mounted_signal = Signal()  # kwargs: files: SDFile
        self.sd_unmounted_signal = Signal()

        self.serial_reader = serial_reader
        self.serial_reader.add_handler(
            SD_PRESENT_REGEX, self.sd_inserted)
        self.serial_reader.add_handler(
            SD_EJECTED_REGEX, self.sd_ejected)
        self.serial_queue: SerialQueue = serial_queue
        self.state_manager = state_manager

        self.expecting_insertion = False
        self.invalidated = True
        self.last_updated = time()

        self.sd_state: SDState = SDState.UNSURE

        self.files: Optional[SDFile] = None

        super().__init__()

    def update(self):
        # Do not update while printing
        if self.state_manager.get_state() in PRINTING_STATES:
            return

        # Do not update, when the interval didn't pass and the tree wasn't
        # invalidated
        if not self.invalidated and \
                time() - self.last_updated < SD_FILESCAN_INTERVAL:
            return

        self.last_updated = time()
        self.invalidated = False

        self.files = self.construct_file_tree()

        # If we do not know the sd state and no files were found,
        # check the SD presence
        if self.sd_state == SDState.UNSURE:
            if self.files:
                self.sd_state_changed(SDState.PRESENT)
            else:
                self.decide_presence()

        if self.sd_state == SDState.INITIALISING:
            self.sd_state_changed(SDState.PRESENT)

        self.tree_updated_signal.send(self, tree=self.files)

    def construct_file_tree(self):
        if self.sd_state == SDState.ABSENT:
            return None

        tree = SDFile(name="SD Card", is_dir=True, ro=True)
        instruction = enqueue_collecting(self.serial_queue, "M20",
                                         begin_regex=BEGIN_FILES_REGEX,
                                         capture_regex=FILE_PATH_REGEX,
                                         end_regex=END_FILES_REGEX)
        wait_for_instruction(instruction, lambda: self.running)
        for match in instruction.captured:
            tree.add_file_from_line(match.string.lower())
        return tree

    def sd_inserted(self, sender, match: re.Match):
        """
        If received while expecting it, stop expecting another one
        If received unexpectedly, this signalises someone physically
        inserting a card
        """
        # Using a multi-purpose regex, only interested in the first group
        if match.groups()[0]:
            if self.expecting_insertion:
                self.expecting_insertion = False
            else:
                self.invalidated = True
                self.sd_state_changed(SDState.INITIALISING)

    def sd_ejected(self, sender, match: re.Match):
        self.invalidated = True
        self.sd_state_changed(SDState.ABSENT)

    def sd_state_changed(self, new_state):
        log.debug(f"SD state changed from {self.sd_state} to "
                  f"{new_state}")

        if self.sd_state in {SDState.INITIALISING, SDState.UNSURE} and \
                new_state == SDState.PRESENT:
            log.debug("SD Card inserted")
            self.sd_mounted_signal.send(self, files=self.files)

        elif self.sd_state == SDState.PRESENT and \
                new_state in {SDState.ABSENT, SDState.INITIALISING}:
            log.debug("SD Card removed")
            self.sd_unmounted_signal.send(self)

        self.sd_state = new_state
        self.state_changed_signal.send(self, sd_state=self.sd_state)

    def decide_presence(self):
        """
        Calling this can be disruptive to the user experience,
        the card will reload. If there is nothing on the SD card or
        if we suspect there is no SD card, calling this should be fine
        """
        self.expecting_insertion = True
        instruction = enqueue_matchable(self.serial_queue, "M21",
                                        SD_PRESENT_REGEX)
        wait_for_instruction(instruction, lambda: self.running)
        self.expecting_insertion = False

        if not instruction.is_confirmed():
            log.debug("Failed determining the SD presence.")
        else:
            match = instruction.match()
            if match is not None and match.groups()[0] is not None:
                if self.sd_state != SDState.PRESENT:
                    self.sd_state_changed(SDState.PRESENT)
            else:
                self.sd_state_changed(SDState.ABSENT)
