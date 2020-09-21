"""
I'll try to explain myself here
The SD state can start only in the UNSURE state, we know nothing

From there, we will ask the printer about the files present.
If there are files, the SD card is present.
If not, we still know nothing and need to ask the printer to r-init the card
that provides the information about SD card presence

The same situation arises when the user inserts a card.
We get into the INITIALISING state.
The card could have been removed immediately after insertion, it could have
been empty, or full of files. Normally inserted card with files is easy.
We'll see files. If there are no files, the re-init tells us the truth
- If we determined, the card is present, let's tell Connect.

Now the card removal is tricky. We cannot tell whether an empty card was removed
so we need to re-init empty cards periodically, to ensure their presence.
If the card was full of files and suddenly there are none. Use re-init to check
if it was removed.
- If we determined, the card got removed, let's tell Connect

Finally, we could have not noticed the card removal and the printer is telling
us about a SD insertion. Let's tell connect the card got removed and go to the
INITIALISING state

"""

import logging
import re
from time import time

from blinker import Signal

from prusa_link.informers.filesystem.models import SDState, InternalFileTree
from prusa_link.informers.state_manager import StateManager
from prusa_link.input_output.serial.serial_reader import SerialReader
from prusa_link.structures.constants import PRINTING_STATES
from prusa_link.structures.model_classes import FileType
from prusa_link.input_output.serial.serial_queue import SerialQueue
from prusa_link.input_output.serial.helpers import wait_for_instruction, \
    enqueue_matchable, enqueue_collecting
from prusa_link.default_settings import get_settings
from prusa_link.structures.regular_expressions import SD_PRESENT_REGEX, \
    BEGIN_FILES_REGEX, END_FILES_REGEX, FILE_PATH_REGEX
from prusa_link.updatable import ThreadedUpdatable

LOG = get_settings().LOG
TIME = get_settings().TIME

log = logging.getLogger(__name__)
log.setLevel(LOG.SD_CARD)


class SDCard(ThreadedUpdatable):
    thread_name = "sd_updater"
    update_interval = TIME.SD_INTERVAL

    def __init__(self, serial_queue: SerialQueue, serial_reader: SerialReader,
                 state_manager: StateManager):

        self.tree_updated_signal = Signal()  # kwargs: tree: FileTree
        self.state_changed_signal = Signal()  # kwargs: sd_state: SDState
        self.inserted_signal = Signal()  # kwargs: root: str, files: FileTree
        self.ejected_signal = Signal()  # kwargs: root: str

        self.serial_reader = serial_reader
        self.serial_reader.add_handler(
            SD_PRESENT_REGEX, self.sd_inserted)
        self.serial_queue: SerialQueue = serial_queue
        self.state_manager = state_manager

        self.expecting_insertion = False

        self.sd_state: SDState = SDState.UNSURE

        super().__init__()

    def _update(self):
        # Do not update while printing
        if self.state_manager.get_state() in PRINTING_STATES:
            return

        self.file_tree = self.construct_file_tree()

        unsure_states = {SDState.INITIALISING, SDState.UNSURE}
        # If we do not know the sd state and no files were found,
        # check the SD presence
        # If there were files and now there is nothing,
        # the SD was most likely ejected. So check for that
        if self.sd_state in unsure_states:
            if self.file_tree:
                self.sd_state_changed(SDState.PRESENT)
            else:
                self.decide_presence()
        if not self.file_tree and self.sd_state == SDState.PRESENT:
            self.decide_presence()
        if self.file_tree and self.sd_state == SDState.ABSENT:
            log.error("ERROR: Sanity check failed. SD is not present, "
                      "but we see files!")

        self.tree_updated_signal.send(self, tree=self.file_tree)

    def construct_file_tree(self):
        tree = InternalFileTree(name="SD Card", file_type=FileType.MOUNT,
                                ro=True, mounted_at="/")

        if self.sd_state == SDState.ABSENT:
            return tree

        instruction = enqueue_collecting(self.serial_queue, "M20",
                                         begin_regex=BEGIN_FILES_REGEX,
                                         capture_regex=FILE_PATH_REGEX,
                                         end_regex=END_FILES_REGEX)
        wait_for_instruction(instruction, lambda: self.running)

        pre = time()
        for match in instruction.captured:
            tree.add_file_from_line(match.string.lower())
        log.debug(f"Tree construction took {time() - pre}s")

        return tree

    def sd_inserted(self, sender, match: re.Match):
        """
        If received while expecting it, stop expecting another one
        If received unexpectedly, this signalises someone physically
        inserting a card
        """
        # Using a multi-purpose regex,
        # only interested if the first group matches
        if match.groups()[0]:
            if self.expecting_insertion:
                self.expecting_insertion = False
            else:
                self.sd_state_changed(SDState.INITIALISING)

    def sd_state_changed(self, new_state):
        log.debug(f"SD state changed from {self.sd_state} to "
                  f"{new_state}")

        if self.sd_state == SDState.INITIALISING and \
                new_state == SDState.PRESENT:
            log.debug("SD Card inserted")

            self.inserted_signal.send(self, root=self.file_tree.full_path,
                                      files=self.file_tree.to_api_file_tree())

        elif self.sd_state == SDState.PRESENT and \
                new_state in {SDState.ABSENT, SDState.INITIALISING}:
            log.debug("SD Card removed")
            self.ejected_signal.send(self, root=self.file_tree.full_path)

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
