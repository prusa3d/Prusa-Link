import re
from enum import Enum
from threading import Event

from blinker import Signal

from old_buddy.structures.regular_expressions import OK_REGEX


class Instruction:
    """Basic instruction which can be enqueued into SerialQueue"""

    def __init__(self, message: str, to_checksum: bool=False):
        if message.count("\n") != 0:
            raise RuntimeError("Instructions cannot contain newlines.")

        # Some messages need to be sent with numbered lines and with checksums
        # This shall be exclusive for printing from files
        self.to_checksum = to_checksum

        # Can be changed before the instruction is sent.
        self.message = message

        # M602 is generous, it gives us a second "OK"
        # completely free of charge.
        # This enables us to compensate for it
        self.needs_two_okays = self.message.startswith("M602")

        # Event set when the write has been _confirmed by the printer
        self.confirmed_event = Event()

        # The plan is to move from waiting for events to reacting to them
        # using blinker signals
        self.confirmed_signal = Signal()

        # Event set when the write has been sent to the printer
        self.sent_event = Event()

        # Signal sent on the same ocasion
        self.sent_signal = Signal()

    def __str__(self):
        return f"Instruction '{self.message.strip()}'"

    def confirm(self) -> bool:
        """
        Didn't think a confirmation would need to fail, but it needs to
        in some cases
        """
        if self.needs_two_okays:
            self.needs_two_okays = False
            return False

        self.confirmed_event.set()
        self.confirmed_signal.send(self)
        return True

    def sent(self):
        self.sent_event.set()
        self.sent_signal.send(self)

    def output_captured(self, line):
        pass

    def wait_for_send(self, timeout=None):
        return self.sent_event.wait(timeout)

    def wait_for_confirmation(self, timeout=None):
        return self.confirmed_event.wait(timeout)

    def is_sent(self):
        return self.sent_event.is_set()

    def is_confirmed(self):
        return self.confirmed_event.is_set()


class MatchableInstruction(Instruction):
    """
    Same as EasyInstruction but captures its output, which can be matched
    to a regular expression
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # Output captured between command submission and confirmation
        self.captured = []

    def confirm(self) -> bool:
        result = super().confirm()

        if result and self.captured:
            # If the capture contains "ok" as the first or last thing, delete it
            if OK_REGEX.fullmatch(self.captured[-1]):
                del self.captured[-1]
            elif OK_REGEX.fullmatch(self.captured[0]):
                del self.captured[0]
        return result

    def output_captured(self, line):
        self.captured.append(line)

    def match(self, pattern: re.Pattern):
        for line in self.captured:
            match = pattern.match(line)
            if match:
                return match


class CollectingInstruction(Instruction):
    """
    Same as Instruction, but captures output only after begin_regex matches
    only captures match object of capture_regex
    and ends the capture after end_regex matches
    the start and end matches shall be omitted
    """

    class States(Enum):
        NOT_CAPTURING_YET = 0
        CAPTURING = 1
        ENDED = 2

    def __init__(self, begin_regex: re.Pattern,
                 capture_regex: re.Pattern,
                 end_regex: re.Pattern,
                 *args, **kwargs):
        super().__init__(*args, **kwargs)

        # Output captured between begin and end regex,
        # which matched capture regex
        self.end_regex = end_regex
        self.capture_regex = capture_regex
        self.begin_regex = begin_regex
        self.captured_matches = []
        self.state = self.States.NOT_CAPTURING_YET

    def output_captured(self, line):
        # The order of these blocks is important, it prevents the
        # begin and end matches from also matching with the capture regex
        if self.state == self.States.CAPTURING:
            end_match = self.end_regex.match(line)
            if end_match:
                self.state = self.States.ENDED

        if self.state == self.States.CAPTURING:
            capture_match = self.capture_regex.match(line)
            if capture_match:
                self.captured_matches.append(capture_match)

        if self.state == self.States.NOT_CAPTURING_YET:
            begin_match = self.begin_regex.match(line)
            if begin_match:
                self.state = self.States.CAPTURING
