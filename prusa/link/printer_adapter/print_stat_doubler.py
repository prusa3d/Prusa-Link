"""Implements the print stat line doubling"""
import re
from typing import List

from blinker import Signal

from ..serial.serial_parser import ThreadedSerialParser
from .structures.regular_expressions import (
    CONFIRMATION_REGEX,
    PRINT_INFO_REGEX,
)


class PrintStatDoubler:
    """
    The print stats are coming automatically, as we read a line at a time, we
    lose the info of which one is valid and so cannot decide
    on which one to use.
    With this, we can handle both lines at the same time without heavily
    modifying the underlying serial communication layers
    """

    def __init__(self, serial_parser: ThreadedSerialParser):
        self.serial_parser = serial_parser

        self.matches: List[re.Match] = []

        self.serial_parser.add_decoupled_handler(
                PRINT_INFO_REGEX, self.matched)
        # TODO: Actually, reset on a timeout, but send whatever we got
        self.serial_parser.add_decoupled_handler(
                CONFIRMATION_REGEX, self.reset)

        # TODO: maybe don't unify these in the gatherer, seems weird
        self.print_stat_signal = Signal()  # sender: matches = List[re.match]

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
            self.print_stat_signal.send(self.matches)
            self.matches.clear()
