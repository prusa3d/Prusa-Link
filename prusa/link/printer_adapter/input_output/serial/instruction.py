import logging
import re
from enum import Enum
from threading import Event
from time import time

log = logging.getLogger(__name__)


class Instruction:
    """Basic instruction which can be enqueued into SerialQueue"""
    def __init__(self,
                 message: str,
                 to_checksum: bool = False,
                 data: bytes = None):
        if message.count("\n") != 0:
            raise RuntimeError("Instructions cannot contain newlines.")

        # Some messages need to be sent with numbered lines and with checksums
        # This shall be exclusive for printing from files
        self.to_checksum = to_checksum

        # Can be changed before the instruction is sent.
        self.message = message

        # If already sent, this will contain the sent bytes
        self.data = data

        # Event set when the write has been _confirmed by the printer
        self.confirmed_event = Event()

        # Event set when the write has been sent to the printer
        self.sent_event = Event()

        # Api for registering instruction regexps
        self.capturing_regexps = []

        # Measuring the time between sending and confirmation will hopefully
        # enable me to determine if the motion planner buffer is full
        self.sent_at = None
        self.time_to_confirm = None

    def __str__(self):
        return f"Instruction '{self.message.strip()}'"

    def confirm(self, force=False) -> bool:
        """
        Return False, if getting confirmed but not wanting to
        (not used in the base implementation anymore)
        """
        self.time_to_confirm = time() - self.sent_at
        self.confirmed_event.set()
        return True

    def sent(self):
        self.sent_event.set()
        self.sent_at = time()

    def output_captured(self, sender, match):
        pass

    def wait_for_send(self, timeout=None):
        return self.sent_event.wait(timeout)

    def wait_for_confirmation(self, timeout=None):
        return self.confirmed_event.wait(timeout)

    def is_sent(self):
        return self.sent_event.is_set()

    def is_confirmed(self):
        return self.confirmed_event.is_set()

    def reset(self):
        self.sent_at = None
        self.sent_event.clear()


class MatchableInstruction(Instruction):
    """
    Matches using captures_matching.
    """
    def __init__(self,
                 *args,
                 capture_matching: re.Pattern = re.compile(r".*"),
                 **kwargs):
        super().__init__(*args, **kwargs)

        # Output captured between command submission and confirmation
        self.capture_matching = capture_matching
        self.captured = []

        self.capturing_regexps = [capture_matching]

    def output_captured(self, sender, match):
        """Appends captured output to the instructions captured list"""
        self.captured.append(match)

    def match(self, index=0):
        """If match with an index exists, return it, otherwise return None"""
        if self.captured and len(self.captured) > index:
            return self.captured[index]
        else:
            return None

    def get_matches(self):
        """Returns the list of all captured matches"""
        return self.captured


class MandatoryMatchableInstruction(MatchableInstruction):
    """
    HAS TO MATCH, otherwise refuses confirmation!
    This should fix a communication error we're having.
    """
    def confirm(self, force=False) -> bool:
        # Yes, matchables HAVE TO match now!
        if not self.captured and not force:
            log.warning(
                "Instruction %s did not capture its expected output, "
                "so it REFUSES to be confirmed!", self.message)
            return False
        else:
            return super().confirm()


class CollectingInstruction(Instruction):
    """
    Same as Instruction, but captures output only after begin_regex matches
    only captures match object of capture_regex
    and ends the capture after end_regex matches
    the start and end matches shall be omitted

    Also refuses confirmation if nothing gets matched.
    """
    class States(Enum):
        """
        State of a capturing instruction that needs a start and an end match
        """
        NOT_CAPTURING_YET = 0
        CAPTURING = 1
        ENDED = 2

    def __init__(self, begin_regex: re.Pattern, capture_regex: re.Pattern,
                 end_regex: re.Pattern, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # Output captured between begin and end regex,
        # which matched capture regex
        self.end_regex = end_regex
        self.capture_regex = capture_regex
        self.begin_regex = begin_regex
        self.captured = []
        self.state = self.States.NOT_CAPTURING_YET

        self.capturing_regexps = [
            self.begin_regex, self.capture_regex, self.end_regex
        ]

    def output_captured(self, sender, match: re.Match):
        """
        Overrides the default output capturing method.
        Starts capturing only after the begin_match
        then captures everything matching capture_match
        and this gets ended by the end_match.
        """
        # The order of these blocks is important, it prevents the
        # begin and end matches from also matching with the capture regex
        if self.state == self.States.CAPTURING:
            end_match = self.end_regex.match(match.string)
            if end_match:
                self.state = self.States.ENDED

        if self.state == self.States.CAPTURING:
            capture_match = self.capture_regex.match(match.string)
            if capture_match:
                self.captured.append(capture_match)

        if self.state == self.States.NOT_CAPTURING_YET:
            begin_match = self.begin_regex.match(match.string)
            if begin_match:
                self.state = self.States.CAPTURING

    def confirm(self, force=False) -> bool:
        """
        Determines, whether the instruction agrees with being confirmed
        """
        # Yes, collecting HAVE TO match now!
        if self.state != self.States.ENDED and not force:
            log.warning(
                "Instruction %s did not capture its expected output, "
                "so it REFUSES to be confirmed!", self.message)
            return False
        else:
            return super().confirm()
