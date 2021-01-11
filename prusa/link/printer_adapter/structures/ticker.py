from time import time


class Ticker:

    def __init__(self, interval):
        self.interval = interval
        self.last_tick = 0
        self.output_signal = False

    def update(self):
        if self.output_signal:
            self.output_signal = False

        if time() - self.last_tick > self.interval:
            self.last_tick = time()
            self.output_signal = True

    def output(self):
        return self.output_signal
