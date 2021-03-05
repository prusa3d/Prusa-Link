"""Try read SN from file."""

import logging
import math
from time import time

from blinker import Signal

from .const import SN_OBTAIN_INTERVAL
from .. import errors
from .updatable import ThreadedUpdatable
from .input_output.serial.instruction import MatchableInstruction
from .structures.regular_expressions import SN_REGEX
from .input_output.serial.helpers import wait_for_instruction

log = logging.getLogger(__name__)


class SNReader(ThreadedUpdatable):
    """Obtain the SN using the FW"""
    thread_name = "sn_updater"
    update_interval = SN_OBTAIN_INTERVAL

    def __init__(self, serial_queue):
        super().__init__()
        self.updated_signal = Signal()  # kwargs: serial_number: string
        self.serial_queue = serial_queue
        self.interested_in_sn = False
        self.start()

    def try_getting_sn(self):
        self.interested_in_sn = True

    def read_sn(self, timeout=math.inf):
        """Read SN from serial line and set `prusa.link.errors.SN`"""
        def should_wait():
            return self.running and time() < timeout

        instruction = MatchableInstruction("PRUSA SN",
                                           capture_matching=SN_REGEX)
        self.serial_queue.enqueue_one(instruction, to_front=True)
        wait_for_instruction(instruction, should_wait)
        match = instruction.match()
        errors.SN.ok = match is not None
        if match:
            result = match.group("sn")
            log.debug("Got serial %s", result)
            return result

    def update(self):
        """
        If asked to, tries to read the serial number until inevitable
        success ;)
        """
        log.debug("interested: %s, sn error: %s", self.interested_in_sn,
                  not errors.SN.ok)
        if self.interested_in_sn or not errors.SN.ok:
            sn = self.read_sn()
            if sn is not None:
                self.updated_signal.send(self, serial_number=sn)
                self.interested_in_sn = False
