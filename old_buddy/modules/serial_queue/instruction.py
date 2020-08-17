import re
from enum import Enum
from threading import Event

from old_buddy.modules.regular_expressions import OK_REGEX


class Instruction:
    """Basic instruction which can be enqueued into SerialQueue"""

    @staticmethod
    def get_data_from_string(message: str):
        if message[-1] != "\n":
            message += "\n"
        return message.encode("ASCII")

    @staticmethod
    def needs_two_okays(message: str):
        return message.startswith("M602")

    def __init__(self, data: bytes, needs_two_okays=False):
        assert isinstance(data, bytes), "Instructions have to contain bytes" \
                                        "Try Instruction.from_string()"
        assert data.endswith(b"\n"), "Instructions have to end with a newline"
        assert data.count(b"\n") == 1, "Instructions can have only one newline"

        # M602 is generous, it gives us a second "OK"
        # completely free of charge.
        # This enables us to compensate for it
        self.needs_two_okays = needs_two_okays

        # Can be changed before the instruction is sent.
        self.data = data

        # Event set when the write has been _confirmed by the printer
        self.confirmed_event = Event()

        # Event set when the write has been sent to the printer
        self.sent_event = Event()

    def __str__(self):
        return f"Instruction '{self.data.decode('ASCII').strip()}'"

    def get_data_size(self):
        return len(self.data)

    def confirm(self) -> bool:
        """
        Didn't think a confirmation would need to fail, but it needs to
        in some cases
        """
        if self.needs_two_okays:
            self.needs_two_okays = False
            return False

        self.confirmed_event.set()
        return True

    def sent(self):
        self.sent_event.set()

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

    size = property(get_data_size)


class EasyInstruction(Instruction):
    """Same as Instruction but supports its creation from string messages"""

    @staticmethod
    def from_string(message: str) -> "EasyInstruction":
        return EasyInstruction(**EasyInstruction._get_args(message))

    @staticmethod
    def _get_args(message: str):
        data = Instruction.get_data_from_string(message)
        needs_two_okays = Instruction.needs_two_okays(message)
        return dict(data=data, needs_two_okays=needs_two_okays)


class MatchableInstruction(EasyInstruction):
    """
    Same as EasyInstruction but captures its output, which can be matched
    to a regular expression
    """

    @staticmethod
    def from_string(message: str) -> "MatchableInstruction":
        return MatchableInstruction(**MatchableInstruction._get_args(message))

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
        # To be able to match, the instruction has to be confirmed
        self.wait_for_confirmation()

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
