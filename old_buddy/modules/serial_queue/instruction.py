import re
from threading import Event

from old_buddy.modules.serial import OK_REGEX


class Instruction:

    @staticmethod
    def from_string(message: str):
        if message[-1] != "\n":
            message += "\n"

        needs_two_okays = False
        if message.startswith("M602"):
            needs_two_okays = True
        data = message.encode("ASCII")
        return Instruction(data, needs_two_okays=needs_two_okays)

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

        # Output captured between command submission ad confirmation
        self.captured = []

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

        # If the capture contains "ok" as the first or last thing, delete it
        if self.captured:
            if OK_REGEX.fullmatch(self.captured[-1]):
                del self.captured[-1]
            elif OK_REGEX.fullmatch(self.captured[0]):
                del self.captured[0]
        self.confirmed_event.set()

        return True

    def sent(self):
        self.sent_event.set()

    def output_captured(self, line):
        self.captured.append(line)

    def wait_for_send(self, timeout=None):
        return self.sent_event.wait(timeout)

    def wait_for_confirmation(self, timeout=None):
        return self.confirmed_event.wait(timeout)

    def is_sent(self):
        return self.sent_event.is_set()

    def is_confirmed(self):
        return self.confirmed_event.is_set()

    def match(self, pattern: re.Pattern):
        # To be able to match, the instruction has to be _confirmed
        self.wait_for_confirmation()

        for line in self.captured:
            match = pattern.match(line)
            if match:
                return match

    size = property(get_data_size)
