"""Try read SN from file."""

import logging
import math
from time import time

from blinker import Signal

from .. import errors
from .updatable import ThreadedUpdatable
from .input_output.serial.instruction import MatchableInstruction
from .structures.regular_expressions import SN_REGEX
from .input_output.serial.helpers import wait_for_instruction

log = logging.getLogger(__name__)


class SNReader(ThreadedUpdatable):
    """Obtain the SN using the FW"""
    thread_name = "sn_updater"

    def __init__(self, serial_queue, handler):
        super().__init__()
        self.updated_signal = Signal()
        self.updated_signal.connect(handler)
        self.serial_queue = serial_queue

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
        """Read the serial number and stop running  based its value"""
        sn = self.read_sn()
        if sn is not None:
            self.running = False
            self.updated_signal.send(sn)
