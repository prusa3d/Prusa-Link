"""Implements the print stat line doubling"""
import re
from typing import List

from .input_output.serial.serial_parser import SerialParser
from .printer_polling import MK3Polling
from .structures.regular_expressions import PRINT_INFO_REGEX, \
    CONFIRMATION_REGEX


class PrintStatDoubler:
    """
    The print stats are coming automatically, as we read a line at a time, we
    lose the info of which one is valid and so cannot decide
    on which one to use.
    With this, we can handle both lines at the same time without heavily
    modifying the underlying serial communication layers
    """

    def __init__(self, serial_parser: SerialParser,
                 printer_polling: MK3Polling):
        self.printer_polling = printer_polling
        self.serial_parser = serial_parser

        self.matches: List[re.Match] = []

        self.serial_parser.add_handler(PRINT_INFO_REGEX, self.matched)
        self.serial_parser.add_handler(CONFIRMATION_REGEX, self.reset)

    def reset(self, sender, match):
        """Resets the accumulated stat lines from the list"""
        assert sender is not None
        assert match is not None
        self.matches.clear()

    def matched(self, sender, match):
        """A print stat line was matched, add it to the list. If we have both,
        send them along to the handler"""
        assert sender is not None
        self.matches.append(match)

        if len(self.matches) >= 2:
            self.printer_polling.print_info_handler(self, self.matches)
            self.matches.clear()
