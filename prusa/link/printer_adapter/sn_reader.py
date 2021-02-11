"""Try read SN from file."""

import logging

from blinker import Signal

from prusa.link import errors
from prusa.link.printer_adapter.informers.getters import get_serial_number, \
    NoSNError
from prusa.link.printer_adapter.updatable import ThreadedUpdatable

log = logging.getLogger(__name__)


class SNReader(ThreadedUpdatable):
    """Obtain the SN using the FW"""
    thread_name = "sn_updater"

    def __init__(self, serial_queue, handler):
        self.updated_signal = Signal()
        self.updated_signal.connect(handler)
        self.serial_queue = serial_queue
        super().__init__()

    def update(self):
        try:
            serial_number = get_serial_number(self.serial_queue)
            log.debug("Got serial %s", serial_number)
            self.updated_signal.send(serial_number)
            self.running = False
            errors.SN.ok = True
        except NoSNError:
            log.debug("Got no serial")
            errors.SN.ok = False
