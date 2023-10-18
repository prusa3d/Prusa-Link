"""Contains implementation of the class for keeping track of the sd status
and its files"""
import calendar
import logging
import re
from itertools import islice
from pathlib import Path
from threading import Lock
from time import time
from typing import Optional

from blinker import Signal  # type: ignore

from prusa.connect.printer.const import State

from ...const import (
    MAX_FILENAME_LENGTH,
    SD_INTERVAL,
    SD_STORAGE_NAME,
    SFN_TO_LFN_EXTENSIONS,
)
from ...sdk_augmentation.file import SDFile
from ...serial.helpers import (
    enqueue_list_from_str,
    enqueue_matchable,
    wait_for_instruction,
)
from ...serial.serial_parser import ThreadedSerialParser
from ...serial.serial_queue import SerialQueue
from ...util import fat_datetime_to_tuple
from ..model import Model
from ..structures.model_classes import SDState
from ..structures.module_data_classes import SDCardData
from ..structures.regular_expressions import (
    CONFIRMATION_REGEX,
    LFN_CAPTURE,
    SD_EJECTED_REGEX,
    SD_PRESENT_REGEX,
)
from ..updatable import ThreadedUpdatable

log = logging.getLogger(__name__)


def alternative_filename(long_filename: str,
                         short_filename: str,
                         long_extension: Optional[str] = None):
    """
    Ensures uniqueness of a file name by prepending it with its
    guaranteed to be unique short name
    """
    new_filename = f"{short_filename} - ({long_filename})"
    if long_extension is not None:
        new_filename += f".{long_extension}"
    log.warning("Filename %s too long, using an alternative: %s",
                long_filename, new_filename)
    return new_filename


def get_root():
    """Gets the root node for sd card files"""
    return SDFile(name=SD_STORAGE_NAME, is_dir=True, read_only=True)


class FileTreeParser:
    """
    Parses the file tree from a printer supplied format
    """

    def __init__(self, matches):
        self.matches = matches
        self.tree = get_root()
        self.current_dir = Path("/")
        self.lfn_to_sfn_paths = {}
        self.sfn_to_lfn_paths = {}
        self.mixed_to_lfn_paths = {}

        if not matches:
            return

        first_line_group = matches[0].group("begin")
        last_line_group = matches[-1].group("end")
        if first_line_group is None or last_line_group is None:
            log.warning("Captured unexpected output.")
            return

        # Captured can be three distinct lines.
        # Dir entry, dir exit, or a file listing.
        for match in islice(matches, 1, len(matches) - 1):
            groups = match.groupdict()
            if groups["dir_enter"] is not None:  # Dir entry
                self.parse_dir(groups)
            elif groups["file"] is not None:  # The list item
                self.parse_file(groups)
            elif groups["dir_exit"] is not None:  # Dir exit
                self.current_dir = self.current_dir.parent

    def check_uniqueness(self, path: Path):
        """Checks, whether the supplied path is not present in the tree"""
        # Ignores the first "/"
        if self.tree.get(path.parts[1:]) is not None:
            log.error("Despite our efforts, there is a name conflict for %s",
                      path)

    def parse_file(self, groups):
        """Parses the file listing using the _captured groups"""
        # pylint: disable=too-many-locals
        short_path_string = groups["sfn"].lower()
        if short_path_string[0] != "/":
            short_path_string = "/" + short_path_string
        short_filename = Path(short_path_string).name
        short_dir_path = Path(short_path_string).parent
        short_extension = groups["extension"]
        long_extension = SFN_TO_LFN_EXTENSIONS[short_extension]
        raw_long_filename = groups["lfn"]

        if raw_long_filename is None:
            return

        # --- Parse the long file name ---

        too_long = len(raw_long_filename) >= MAX_FILENAME_LENGTH

        if too_long:
            long_file_name = alternative_filename(raw_long_filename,
                                                  short_filename,
                                                  long_extension)
        else:
            long_file_name = raw_long_filename

        long_path = self.current_dir.joinpath(long_file_name)
        self.check_uniqueness(long_path)
        long_path_string = str(long_path)

        mixed_path = short_dir_path.joinpath(raw_long_filename)
        mixed_path_string = str(mixed_path).lower()

        # Add translation between the two
        log.debug("Adding translation between %s and %s", long_path_string,
                  short_path_string)
        log.debug("Adding translation from %s to %s", mixed_path,
                  long_path_string)
        self.lfn_to_sfn_paths[long_path_string] = short_path_string
        self.sfn_to_lfn_paths[short_path_string] = long_path_string
        self.mixed_to_lfn_paths[mixed_path_string] = long_path_string

        # --- parse additional properties ---

        additional_properties = {}

        str_size = groups["size"]
        if str_size is not None:
            additional_properties["size"] = int(str_size)

        str_m_time = groups["m_time"]
        if str_m_time is not None:
            m_time = fat_datetime_to_tuple(int(str_m_time, 16))
            m_timestamp = calendar.timegm(m_time)
            additional_properties["m_timestamp"] = m_timestamp

        # Add the file to the tree
        try:
            self.tree.add_file(self.current_dir,
                               long_file_name,
                               short_filename,
                               filename_too_long=too_long,
                               **additional_properties)
        except FileNotFoundError as exception:
            log.exception(exception)

    def parse_dir(self, groups):
        """Parses the dir info using the _captured groups"""
        long_dir_name = groups["ldn"]
        short_dir_name = Path(groups["sdn"]).name

        # Sanitize the dir name
        too_long = len(long_dir_name) >= MAX_FILENAME_LENGTH
        if too_long:
            new_name = alternative_filename(long_dir_name, short_dir_name)
            self.current_dir = self.current_dir.joinpath(new_name)
        else:
            self.current_dir = self.current_dir.joinpath(long_dir_name)

        self.check_uniqueness(self.current_dir)
        # Add the dir to the tree
        try:
            self.tree.add_directory(self.current_dir.parent,
                                    self.current_dir.name,
                                    short_dir_name,
                                    filename_too_long=too_long)
        except FileNotFoundError as exception:
            log.exception(exception)


class SDCard(ThreadedUpdatable):
    """
    Keeps track of the SD Card presence and content

    The SD state can start only in the UNSURE state, we know nothing

    From there, we will ask the printer about the files present.
    If there are files, the SD card is present.
    If not, we still know nothing and need to ask the printer to re-init the
    card that provides the information about SD card presence

    Now that there's the SD ejection message, no more fortune-telling wizardry
    needs to be happening

    Unlikely now, was very likely before:
    The card removal could've gone unnoticed and the printer is telling
    us about an SD insertion. Let's tell connect the card got removed and go
    to the INITIALISING state
    """
    thread_name = "sd_updater"

    # Cycle fast, but re-scan only on events or in big intervals
    update_interval = SD_INTERVAL

    def __init__(self, serial_queue: SerialQueue,
                 serial_parser: ThreadedSerialParser, model: Model):

        self.tree_updated_signal = Signal()  # kwargs: tree: FileTree
        self.state_changed_signal = Signal()  # kwargs: sd_state: SDState
        self.sd_attached_signal = Signal()  # kwargs: files: SDFile
        self.sd_detached_signal = Signal()
        self.menu_found_signal = Signal()  # kwargs: menu_sfn: str

        self.serial_parser = serial_parser
        self.serial_parser.add_decoupled_handler(SD_PRESENT_REGEX,
                                                 self.sd_inserted)
        self.serial_parser.add_decoupled_handler(SD_EJECTED_REGEX,
                                                 self.sd_ejected)
        self.serial_queue: SerialQueue = serial_queue
        self.model = model

        self.model.sd_card = SDCardData(expecting_insertion=False,
                                        invalidated=True,
                                        last_updated=time(),
                                        last_checked_flash_air=time(),
                                        sd_state=SDState.UNSURE,
                                        files=None,
                                        lfn_to_sfn_paths={},
                                        sfn_to_lfn_paths={},
                                        mixed_to_lfn_paths={},
                                        is_flash_air=False)
        self.data = self.model.sd_card
        self.lock = Lock()

        super().__init__()

    def handle_special_menu(self, file_tree_parser):
        """If the SD contains a special menu folder, add the menu items
        and inform others that the menu exists."""
        if "PrusaLink menu" not in file_tree_parser.tree.children:
            return
        node = file_tree_parser.tree.children["PrusaLink menu"]
        if not node.is_dir:
            return
        menu_sfn = node.attrs["sfn"].lower()
        if "SETREADY.G" not in node.children:
            enqueue_list_from_str(
                self.serial_queue,
                [f"M28 {menu_sfn}/setready.g", "M84", "M29"],
                CONFIRMATION_REGEX,
                to_front=True)
        del file_tree_parser.tree.children["PrusaLink menu"]
        self.menu_found_signal.send(menu_sfn=menu_sfn)

    def update(self):
        """
        Updates the file list on the SD Card.
        Except:
        - when the printer state is not IDLE
        - when we already have a file listing and no FlashAir is connected
        - When FlashAir is connected and configured, but it hasn't been long
          enough from the previous update
        """
        # Update only if IDLE
        if self.model.state_manager.current_state != State.IDLE:
            return

        # since_last_update = time() - self.data.last_updated
        # due_for_update = since_last_update > SD_FILESCAN_INTERVAL

        # Do not update, if the tree wasn't invalidated.
        # Also, if there is no flash air, or if there is, but it wasn't long
        # enough from the last update
        if not self.data.invalidated:
            # or due_for_update and self.data.is_flash_air:
            return

        self.data.last_updated = time()
        self.data.invalidated = False

        if self.data.sd_state == SDState.ABSENT:
            return

        file_tree_parser = self._construct_file_tree()

        self.handle_special_menu(file_tree_parser)

        to_decide_presence = False

        with self.lock:
            self._set_files(file_tree_parser)

            if self.data.sd_state == SDState.UNSURE:
                # The files are of type SDFile - the root is always present
                # Check if it has any children -> files were found on the SD
                if self.data.files.children:
                    self._sd_state_changed(SDState.PRESENT)
                else:
                    # If we do not know the sd state and no files were found,
                    # check the SD presence
                    to_decide_presence = True

            if self.data.sd_state == SDState.INITIALISING:
                self._sd_state_changed(SDState.PRESENT)

        if to_decide_presence:
            self.decide_presence()

        self.tree_updated_signal.send(self, tree=self.data.files)

    def _set_files(self, file_tree_parser: FileTreeParser):
        """Sets the file variables according to the supplied parsing context"""
        assert self.lock.locked()
        self.data.files = file_tree_parser.tree
        # Try to be as atomic as possible
        self.data.lfn_to_sfn_paths = file_tree_parser.lfn_to_sfn_paths
        self.data.sfn_to_lfn_paths = file_tree_parser.sfn_to_lfn_paths
        # 8.3/8.3/LFN format to LFN/LFN/LFN
        self.data.mixed_to_lfn_paths = file_tree_parser.mixed_to_lfn_paths

    def set_flash_air(self, is_flash_air):
        """
        Sets the value determining if flash air functionality should be on
        (temporary)
        """
        self.data.is_flash_air = is_flash_air

    def _construct_file_tree(self) -> FileTreeParser:
        """
        Uses M20 LT to get the list of paths.

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

        instruction = enqueue_matchable(self.serial_queue,
                                        message="M20 LT",
                                        regexp=LFN_CAPTURE)
        wait_for_instruction(instruction, should_wait_evt=self.quit_evt)
        matches = instruction.get_matches()
        file_tree_parser = FileTreeParser(matches)
        return file_tree_parser

    def sd_inserted(self, sender, match: re.Match):
        """
        If received while expecting it, stop expecting another one
        If received unexpectedly, this signalises someone physically
        inserting a card
        """
        assert sender is not None
        # Using a multi-purpose regex, only interested in the first group
        if match.group("ok"):
            with self.lock:
                if self.data.expecting_insertion:
                    self.data.expecting_insertion = False
                else:
                    self.data.invalidated = True
                    self._sd_state_changed(SDState.INITIALISING)

    def sd_ejected(self, sender, match: re.Match):
        """
        Handler for sd ejected serial messages.
        Sets the card state to absent and notifies others
        """
        assert sender is not None
        assert match is not None
        with self.lock:
            self.data.invalidated = True
            self._sd_state_changed(SDState.ABSENT)

    def _sd_state_changed(self, new_state):
        """
        Transforms the internal state changes to Signals about sd card
        attaching/detaching. Also sets the internal state to the supplied one
        :param new_state: the state to switch to
        """
        assert self.lock.locked()
        log.debug("SD state changed from %s to %s", self.data.sd_state,
                  new_state)

        if self.data.sd_state in {SDState.INITIALISING, SDState.UNSURE} and \
                new_state == SDState.PRESENT:
            log.debug("SD Card inserted")
            self.sd_attached_signal.send(self, files=self.data.files)

        elif self.data.sd_state == SDState.PRESENT and \
                new_state in {SDState.ABSENT, SDState.INITIALISING}:
            log.debug("SD Card removed")
            self.sd_detached_signal.send(self)
            self._set_files(FileTreeParser(matches=[]))

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
        wait_for_instruction(instruction, should_wait_evt=self.quit_evt)
        self.data.expecting_insertion = False

        match = instruction.match()
        if match is not None:
            with self.lock:
                if match.group("ok") is not None:
                    if self.data.sd_state == SDState.UNSURE:
                        self._sd_state_changed(SDState.PRESENT)
                else:
                    self._sd_state_changed(SDState.ABSENT)
        else:
            log.debug("Failed determining the SD presence.")
