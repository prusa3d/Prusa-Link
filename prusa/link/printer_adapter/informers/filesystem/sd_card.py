"""Contains implementation of the class for keeping track of the sd status
and its files"""
import logging
import re
from pathlib import Path
from time import time

from blinker import Signal  # type: ignore

from prusa.connect.printer.const import State

from ..state_manager import StateManager
from ...input_output.serial.serial_queue import SerialQueue
from ...input_output.serial.serial_reader import SerialReader
from ...input_output.serial.helpers import \
    wait_for_instruction, enqueue_matchable, enqueue_collecting
from ...model import Model
from ...structures.model_classes import SDState
from ...structures.regular_expressions import \
    SD_PRESENT_REGEX, BEGIN_FILES_REGEX, END_FILES_REGEX, \
    SD_EJECTED_REGEX, LFN_CAPTURE, D3_C1_OUTPUT_REGEX
from ...const import SD_INTERVAL, \
    SD_FILESCAN_INTERVAL, SD_MOUNT_NAME, SFN_TO_LFN_EXTENSIONS, \
    MAX_FILENAME_LENGTH, FLASH_AIR_INTERVAL
from ...updatable import ThreadedUpdatable
from ....sdk_augmentation.file import SDFile

log = logging.getLogger(__name__)


class SDCard(ThreadedUpdatable):
    """
    Keeps track of the SD Card presence and content

    The SD state can start only in the UNSURE state, we know nothing

    From there, we will ask the printer about the files present.
    If there are files, the SD card is present.
    If not, we still know nothing and need to ask the printer to re-init the
    card that provides the information about SD card presence

    Now there is an SD ejection message, so no more fortune-telling wizardry
    is happening

    Unlikely now, was very likely before:
    The card removal could've gone unnoticed and the printer is telling
    us about an SD insertion. Let's tell connect the card got removed and go
    to the INITIALISING state
    """
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
        self.data.last_checked_flash_air = time()
        self.data.sd_state = SDState.UNSURE
        self.data.files = None
        self.data.lfn_to_sfn_paths = {}
        self.data.sfn_to_lfn_paths = {}
        self.data.mixed_to_lfn_paths = {}
        self.data.is_flash_air = False

        super().__init__()

    def update(self):
        """
        Updates the file list on the SD Card.
        Except:
        - when the printer state is not READY
        - when we already have a file listing and no FlashAir is connected
        - When FlashAir is connected and configured, but it hasn't been long
          enough from the previous update
        """
        # Update only if READY
        if self.state_manager.get_state() != State.READY:
            return

        due_for_update = time() - self.data.last_updated > SD_FILESCAN_INTERVAL
        if time() - self.data.last_checked_flash_air > FLASH_AIR_INTERVAL:
            self.determine_flash_air()
            self.data.last_checked_flash_air = time()

        # Do not update, if the tree wasn't invalidated.
        # Also, if there is no flash air, or if there is, but it wasn't long
        # enough from the last update
        if not (self.data.invalidated or
                (due_for_update and self.data.is_flash_air)):
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

    def determine_flash_air(self):
        """
        Uses a D3 command to determine whether the flash air option
        is turned on
        """
        instruction = enqueue_matchable(self.serial_queue, "D3 Ax0fbb C1",
                                        D3_C1_OUTPUT_REGEX)
        wait_for_instruction(instruction, lambda: self.running)
        match = instruction.match()
        if match:
            self.data.is_flash_air = match.group("data") == "01"

    def construct_file_tree(self):
        """
        Uses M20 L to get the list of paths.

        Some shorthand terms need explaining here:
        SFN - short file name
        LFN - long file name
        SDN - short directory name
        LDN - long directory name

        The readout is a little complicated as SDN paths are provided inline,
        but SDN -> LDN pairings are provided only when entering a directory

        The long file names over the size limit of 52 chars have a chance of
        not being unique, so this also ensures their uniqueness and
        fills in missing extensions

        :return: The constructed file tree. Also the translation data for
        converting between all used path formats get saved at the end
        """
        if self.data.sd_state == SDState.ABSENT:
            return None

        tree = SDFile(name=SD_MOUNT_NAME, is_dir=True, ro=True)

        instruction = enqueue_collecting(self.serial_queue,
                                         "M20 L",
                                         begin_regex=BEGIN_FILES_REGEX,
                                         capture_regex=LFN_CAPTURE,
                                         end_regex=END_FILES_REGEX)
        wait_for_instruction(instruction, lambda: self.running)

        # Captured can be three distinct lines. Dir entry, exit or a file
        # listing. We need to maintain the info about which dir we are
        # currently in, as that doesn't repeat in the file listing lines
        current_dir = Path("/")
        lfn_to_sfn_paths = {}
        sfn_to_lfn_paths = {}
        mixed_to_lfn_paths = {}
        for match in instruction.captured:
            groups = match.groupdict()
            if groups["dir_enter"] is not None:  # Dir entry
                # Parse the dir info
                long_dir_name = groups["ldn"]
                short_dir_name = Path(groups["sdn"]).name

                # Sanitize the dir name
                too_long = len(long_dir_name) >= MAX_FILENAME_LENGTH
                if too_long:
                    new_name = self.alternative_filename(
                        long_dir_name, short_dir_name)
                    current_dir = current_dir.joinpath(new_name)
                else:
                    current_dir = current_dir.joinpath(long_dir_name)

                self.check_uniqueness(current_dir, tree)
                # Add the dir to the tree
                try:
                    tree.add_directory(current_dir.parent,
                                       current_dir.name,
                                       filename_too_long=too_long)
                except FileNotFoundError as e:
                    log.exception(e)

            elif groups["file"] is not None:  # The list item
                # Parse the file listing
                short_path_string = groups["sfn"]
                if short_path_string[0] != "/":
                    short_path_string = "/" + short_path_string
                short_filename = Path(short_path_string).name
                short_dir_path = Path(short_path_string).parent
                short_extension = groups["extension"]
                long_extension = SFN_TO_LFN_EXTENSIONS[short_extension]
                raw_long_filename: str = groups["lfn"]
                size = int(groups["size"])

                too_long = (len(raw_long_filename) >= MAX_FILENAME_LENGTH)

                if too_long:
                    len_checked_long_filename = self.alternative_filename(
                        raw_long_filename, short_filename)
                else:
                    len_checked_long_filename = raw_long_filename

                long_file_name = self.ensure_extension(
                    len_checked_long_filename, long_extension, short_extension)

                long_path = current_dir.joinpath(long_file_name)
                self.check_uniqueness(long_path, tree)
                long_path_string = str(long_path)

                mixed_path = short_dir_path.joinpath(long_file_name)

                # Add translation between the two
                log.debug("Adding translation between %s and %s",
                          long_path_string, short_path_string)
                log.debug("Adding translation from %s to %s", mixed_path,
                          long_path_string)
                lfn_to_sfn_paths[long_path_string] = short_path_string
                sfn_to_lfn_paths[short_path_string] = long_path_string
                mixed_to_lfn_paths[mixed_path] = long_path_string

                # Add the file to the tree
                try:
                    tree.add_file(current_dir,
                                  long_file_name,
                                  size=size,
                                  filename_too_long=too_long)
                except FileNotFoundError as e:
                    log.exception(e)
            elif groups["dir_exit"] is not None:  # Dir exit
                current_dir = current_dir.parent

        # Try to be as atomic as possible
        self.data.lfn_to_sfn_paths = lfn_to_sfn_paths
        self.data.sfn_to_lfn_paths = sfn_to_lfn_paths
        # 8.3/8.3/LFN format to LFN/LFN/LFN
        self.data.mixed_to_lfn_paths = mixed_to_lfn_paths
        return tree

    def alternative_filename(self, long_filename: str, short_filename: str):
        """
        Ensures uniwueness of a file name by prepending it with its
        guaranteed to be unique short name
        """
        new_filename = f"{short_filename} - ({long_filename})"
        log.warning(
            f"Filename {long_filename} too long, using an alternative: "
            f"{new_filename}")
        return new_filename

    def check_uniqueness(self, path: Path, tree):
        """Checks, whether the supplied path is not present in the tree"""
        # Ignores the first "/"
        if tree.get(path.parts[1:]) is not None:
            log.error("Despite our efforts, there is a name conflict for %s",
                      path)

    def ensure_extension(self, long_filename: str, long_extension: str,
                         short_extension: str):
        """Fixes extensions of file names"""
        has_full_extension = (long_filename.endswith(short_extension)
                              or long_filename.endswith(long_extension))
        if not has_full_extension:
            original_extension = long_filename.split(".")[-1]
            # The filenames can end in parts of short or long versions of
            # their extensions. If that extension is incomplete,
            # let's use the long one, cause it's shorter to write
            has_incomplete_extension = (original_extension in long_extension or
                                        original_extension in short_extension)
            if has_incomplete_extension:
                long_filename = long_filename[:-len(original_extension)]
            else:
                if not long_filename.endswith("."):
                    long_filename += "."
            long_filename += long_extension
        return long_filename

    def sd_inserted(self, sender, match: re.Match):
        """
        If received while expecting it, stop expecting another one
        If received unexpectedly, this signalises someone physically
        inserting a card
        """
        # Using a multi-purpose regex, only interested in the first group
        if match.group("ok"):
            if self.data.expecting_insertion:
                self.data.expecting_insertion = False
            else:
                self.data.invalidated = True
                self.sd_state_changed(SDState.INITIALISING)

    def sd_ejected(self, sender, match: re.Match):
        """
        Handler for sd ejected serial messgaes.
        Sets the card state to absent and notifies others
        """
        self.data.invalidated = True
        self.sd_state_changed(SDState.ABSENT)

    def sd_state_changed(self, new_state):
        """
        Transforms the internal state changes to signals about sd card
        (un)mounting
        Also sets the internal state to the supplied one
        :param new_state: the state to switch to
        """
        log.debug("SD state changed from %s to %s", self.data.sd_state,
                  new_state)

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

        Asks the firmware to re-init the SD card, uses the output,
        to determine SD presence
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
