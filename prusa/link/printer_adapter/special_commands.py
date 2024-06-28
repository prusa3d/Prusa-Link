"""An implementation of a hidden menu logic"""
import logging
import re
from time import time

from blinker import Signal  # type:ignore

from ..interesting_logger import InterestingLogRotator
from ..serial.serial_parser import ThreadedSerialParser
from .command import CommandFailed
from .command_handlers import SetReady
from .command_queue import CommandQueue
from .structures.regular_expressions import (
    OPEN_RESULT_REGEX,
    PRINT_DONE_REGEX,
    START_PRINT_REGEX,
)

log = logging.getLogger(__name__)

CMD_TIMEOUT = 1


class SpecialCommands:
    """Filter print start related serial output and catch special menu item
    related ones"""

    def __init__(self, serial_parser: ThreadedSerialParser,
                 command_queue: CommandQueue):
        self.command_queue = command_queue

        self.commands = {"setready.g": self.set_ready}
        self.detected_at = 0
        self.menu_folder_sfn = None
        self.current = None

        self.open_result_signal = Signal()  # kwargs - match: re.Match
        self.start_print_signal = Signal()
        self.print_done_signal = Signal()

        serial_parser.add_decoupled_handler(
                OPEN_RESULT_REGEX, self.handle_file)
        serial_parser.add_decoupled_handler(
                START_PRINT_REGEX, self.handle_start)
        serial_parser.add_decoupled_handler(
                PRINT_DONE_REGEX, self.handle_done)

    def menu_folder_found(self, _, menu_sfn):
        """An SD with the special menu has been inserted"""
        log.debug("Registered a menu folder %s", menu_sfn)
        self.menu_folder_sfn = menu_sfn

    def menu_folder_gone(self, _):
        """The special menu was ejected with its SD card"""
        log.debug("De-registered a menu folder %s", self.menu_folder_sfn)
        self.menu_folder_sfn = None

    def _open_is_special(self, match):
        """Does this match correspond to one of our special menu item files?"""
        sdn_lfn = match.group("sdn_lfn")
        if sdn_lfn is None:
            return False
        if self.menu_folder_sfn is None:
            return False
        path = sdn_lfn.lower()
        parts = path.rsplit("/", 2)
        if len(parts) < 2:
            return False
        if parts[-2] != self.menu_folder_sfn:
            return False
        return parts[-1] in self.commands

    def handle_file(self, _, match):
        """A file has been opened, should we pass along that info,
        or should we prepare our special command"""
        if self._open_is_special(match):
            path = match.group("sdn_lfn").lower()
            parts = path.rsplit("/", 2)
            self.current = self.commands[parts[-1]]
            self.detected_at = time()
        else:
            self.open_result_signal.send(match=match)

    def handle_start(self, _, match: re.Match):
        """If a command is prepared, prolong it's lifetime,
        otherwise pass through"""
        assert match is not None
        since_detected = time() - self.detected_at
        if self.current is not None and since_detected < CMD_TIMEOUT:
            self.detected_at = time()
        else:
            self.current = None
            self.start_print_signal.send(match=match)

    def handle_done(self, _, match: re.Match):
        """If a command is prepared and the placeholder file print has been
        done, execute the command"""
        since_detected = time() - self.detected_at
        if self.current is not None and since_detected < CMD_TIMEOUT:
            self.current()
        else:
            self.print_done_signal.send(match=match)
        self.current = None

    def set_ready(self):
        """A command handler to set the printer into READY"""
        try:
            self.command_queue.do_command(SetReady())
        except CommandFailed:
            InterestingLogRotator.trigger("Attempt to set the printer ready")
            log.exception("Setting the printer to READY has failed")
