"""
Contains implementation of the ThreadedUpdatable class
There was an updatable without a thread, but it stopped being used

Also contains a thread utility function
"""
from threading import Thread, current_thread

import prctl  # type: ignore

from .const import QUIT_INTERVAL
from .util import run_slowly_die_fast


def prctl_name():
    """Set system thread name with python thread name."""
    prctl.set_name("prusal#%s" % current_thread().name)


class ThreadedUpdatable:
    """Thread for parallel update operation."""
    thread_name = "updater_thread"
    update_interval = 1.0

    def __init__(self):
        self.running = True
        self.thread = Thread(target=self.__keep_updating,
                             name=self.thread_name)

    def start(self):
        """Start thread."""
        self.thread.start()

    def __keep_updating(self):
        prctl_name()
        run_slowly_die_fast(lambda: self.running, QUIT_INTERVAL,
                            lambda: self.update_interval, self.update)

    def stop(self):
        """Stop thread"""
        self.running = False
        self.thread.join()

    def update(self):
        """Put code for updating here."""
        raise NotImplementedError
