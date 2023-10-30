"""Contains the keepalive implementation"""
from enum import Enum
from threading import Event, Thread
from time import monotonic

from ..const import KEEPALIVE_INTERVAL
from ..serial.helpers import enqueue_instruction, wait_for_instruction
from ..serial.serial_queue import SerialQueue
from .structures.mc_singleton import MCSingleton


class KeepaliveMode(Enum):
    """The modes the keepalive can be in"""
    PL = "PL"  # PrusaLink
    PC = "PC"  # PrusaConnect


class Keepalive(metaclass=MCSingleton):
    """Its job is to keep the PrusaLink printer mode on"""

    def __init__(self, serial_queue: SerialQueue):
        self.serial_queue: SerialQueue = serial_queue

        self.mode = KeepaliveMode.PL

        self.quit_evt = Event()
        self.wait_evt = Event()
        self.keepalive_thread: Thread = Thread(target=self._keepalive,
                                               name="Keepalive")

        self.last_keepalive = monotonic() - KEEPALIVE_INTERVAL

    def start(self):
        """Starts the module"""
        self.keepalive_thread.start()

    def set_use_connect(self, use_connect: bool):
        """Changes the mode of the keepalive"""
        self.mode = KeepaliveMode.PC if use_connect else KeepaliveMode.PL
        self.last_keepalive = monotonic() - KEEPALIVE_INTERVAL
        self.wait_evt.set()

    def _keepalive(self):
        """Keep sending out a signal, that PrusaLink is connected"""
        while not self.quit_evt.is_set():
            instruction = enqueue_instruction(
                self.serial_queue, f"M79 S\"{self.mode.value}\"",
                to_front=True)
            self.last_keepalive = monotonic()
            wait_for_instruction(instruction, should_wait_evt=self.quit_evt)
            to_wait = self.last_keepalive + KEEPALIVE_INTERVAL - monotonic()
            if to_wait >= 0:
                self.wait_evt.wait(to_wait)
            if self.wait_evt.is_set():
                self.wait_evt.clear()

    def stop(self):
        """Stops the keepalive sender"""
        self.quit_evt.set()
        self.wait_evt.set()

    def wait_stopped(self):
        """Waits for Keepalive thread to stop"""
        self.keepalive_thread.join()
