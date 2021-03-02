"""Definition for ThreadUpdatable class."""
from threading import Thread

from .const import QUIT_INTERVAL
from .util import run_slowly_die_fast


class ThreadedUpdatable:
    """Thread for parallel update operation."""
    thread_name = "updater_thread"
    update_interval = 1

    def __init__(self):
        self.running = True
        self.thread = Thread(target=self.__keep_updating,
                             name=self.thread_name)
        # XXX introduce a `signal` instance attr here?

    def start(self):
        """Start thread."""
        self.thread.start()

    def __keep_updating(self):
        run_slowly_die_fast(lambda: self.running, QUIT_INTERVAL,
                            lambda: self.update_interval, self.update)

    def stop(self):
        """Stop thread"""
        self.running = False
        self.thread.join()

    def update(self):
        """Put code for updating here."""
        raise NotImplementedError
