from time import time


class Ticker:

    def __init__(self, interval):
        self.interval = interval
        self.last_tick = 0

    def should_tick(self):
        if time() - self.last_tick > self.interval:
            self.last_tick = time()
            return True
        else:
            return False
