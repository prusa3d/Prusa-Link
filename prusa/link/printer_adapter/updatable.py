"""
Contains implementation of the ThreadedUpdatable class
There was an updatable without a thread, but it stopped being used

Also contains a thread utility function
"""
from threading import Thread as _Thread, current_thread
from cProfile import Profile

import prctl  # type: ignore

from .const import QUIT_INTERVAL
from .util import run_slowly_die_fast


def prctl_name():
    """Set system thread name with python thread name."""
    prctl.set_name(f"prusal#{current_thread().name}")


class Thread(_Thread):
    """https://stackoverflow.com/a/1922945"""
    def profile_run(self):
        """run method for profiling"""
        profiler = Profile()
        profiler.enable()
        try:
            return profiler.runcall(_Thread.run, self)
        finally:
            profiler.disable()
            profiler.dump_stats(f'prusalink-{self.name}.profile')

    @staticmethod
    def enable_profiling():
        """Swap run method."""
        Thread.run = Thread.profile_run

    @staticmethod
    def disable_profiling():
        """Swap run method."""
        Thread.run = _Thread.run


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
