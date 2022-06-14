"""An implementation of a hidden menu logic"""
import logging
import re
from time import time

from blinker import Signal  # type:ignore

from .command import CommandFailed
from .command_handlers import SetReady
from .command_queue import CommandQueue
from .lcd_printer import LCDPrinter
from .structures.carousel import LCDLine
from .structures.regular_expressions import \
    START_PRINT_REGEX, OPEN_RESULT_REGEX
from ..interesting_logger import InterestingLogRotator
from ..serial.serial_parser import SerialParser

log = logging.getLogger(__name__)

CMD_TIMEOUT = 1


class SpecialCommands:
    """Filter print start related serial output and catch special menu item
    related ones"""

    def __init__(self, serial_parser: SerialParser,
                 command_queue: CommandQueue,
                 lcd_printer: LCDPrinter):
        self.command_queue = command_queue
        self.lcd_printer = lcd_printer

        self.commands = {"setready.g": self.set_ready}
        self.detected_at = 0
        self.menu_folder_sfn = None
        self.current = None

        self.file_opened_signal = Signal()  # kwargs - match: re.Match
        self.print_started_signal = Signal()

        serial_parser.add_handler(OPEN_RESULT_REGEX, self.handle_file)
        serial_parser.add_handler(START_PRINT_REGEX, self.handle_start)

    def menu_folder_found(self, _, menu_sfn):
        """An SD with the special menu has been inserted"""
        self.menu_folder_sfn = menu_sfn

    def menu_folder_gone(self, _):
        """The special menu was ejected with its SD card"""
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
        if parts[-1] not in self.commands:
            return False
        return True

    def handle_file(self, _, match):
        """A file has been opened, should we pass along that info,
        or should we prepare our special command"""
        if self._open_is_special(match):
            path = match.group("sdn_lfn").lower()
            parts = path.rsplit("/", 2)
            self.current = self.commands[parts[-1]]
            self.detected_at = time()
        else:
            self.file_opened_signal.send(match=match)

    def handle_start(self, _, match: re.Match):
        """If a command is prepared, execute it, otherwise pass through"""
        assert match is not None
        since_detected = time() - self.detected_at
        if self.current is not None and since_detected < CMD_TIMEOUT:
            self.current()
        else:
            self.print_started_signal.send()
        self.current = None

    def set_ready(self):
        """A command handler to set the printer into READY"""
        try:
            self.command_queue.do_command(SetReady())
        except CommandFailed:
            InterestingLogRotator.trigger("Attempt to set the printer ready")
            log.exception("Setting the printer to READY has failed")
            self.lcd_printer.print_message(LCDLine("Set ready failed",
                                                   resets_idle=False),
                                           force_over_fw=True)
        else:
            self.lcd_printer.print_message(LCDLine("Ready for next Job",
                                                   resets_idle=False),
                                           force_over_fw=True)
