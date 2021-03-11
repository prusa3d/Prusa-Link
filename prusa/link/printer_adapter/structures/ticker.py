"""Contains implementation of the Ticker class"""
from time import time


class Ticker:
    """Sets the output to True every X seconds"""
    def __init__(self, interval):
        self.interval = interval
        self.last_tick = 0
        self.output_signal = False

    def update(self):
        """
        Check, if it was X seconds since last time the output was True
        If yes, set it to true, if the output is True, reset it
        """
        if self.output_signal:
            self.output_signal = False

        if time() - self.last_tick > self.interval:
            self.last_tick = time()
            self.output_signal = True

    def output(self):
        """The output getter"""
        return self.output_signal
