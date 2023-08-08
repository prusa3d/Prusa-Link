"""Utilities for the event processor"""
from threading import Event, Thread
from time import monotonic

from .event_processor import EventInfo


class SerialEventInfoFactory:
    """A factory to create EventInfo objects that generate Events from
    the serial signals"""
    def __init__(self, serial_parser):
        self.serial_parser = serial_parser

    def create(self, name, regexp, priority=0):
        """Creates the EventInfo object"""

        def registration(handler):
            """Registers the handler to the serial parser"""
            self.serial_parser.add_handler(
                regexp=regexp,
                handler=handler,
                priority=priority)

        def deregistration(handler):
            """De-registers the handler from the serial parser"""
            self.serial_parser.remove_handler(
                regexp=regexp,
                handler=handler)

        return EventInfo(
            name=name,
            registration=registration,
            deregistration=deregistration,
        )


class Ticker:
    """A class that calls a callback every interval seconds"""

    def __init__(self, interval=0.2):
        self.last_tick = monotonic()
        self.interval = interval  # seconds
        self.quit_evt = Event()
        self.callback = None
        self.thread = Thread(target=self.ticker, name="Ticker", daemon=True)
        self.thread.start()

    def ticker(self):
        """Ticks every interval seconds, calls the callback"""
        while not self.quit_evt.is_set():
            self.last_tick = monotonic()
            if self.callback is not None:
                self.callback()

            wait_amount = self.interval - (monotonic() - self.last_tick)
            if wait_amount > 0:
                self.quit_evt.wait(wait_amount)

    def set_handler(self, handler):
        """Sets the callback"""
        self.callback = handler

    def stop(self):
        """Stops the ticker"""
        self.quit_evt.set()

    def wait_stopped(self):
        """Waits for the ticker thread to stop"""
        self.thread.join()
