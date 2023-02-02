"""
Contains implementation of the ThreadedUpdatable class
There was an updatable without a thread, but it stopped being used

Also contains a thread utility function
"""
from cProfile import Profile
from functools import partial
from threading import Event
from threading import Thread as _Thread

from ..util import loop_until


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
        self.quit_evt = Event()
        target = partial(
            loop_until,
            loop_evt=self.quit_evt,
            run_every_sec=lambda: self.update_interval,
            to_run=self.update)

        self.thread = Thread(target=target,
                             name=self.thread_name)

    def start(self):
        """Start thread."""
        self.thread.start()

    def stop(self):
        """Stop the updatable"""
        self.quit_evt.set()

    def wait_stopped(self):
        """Wait for the updatable to be stopped"""
        self.thread.join()

    def update(self):
        """Put code for updating here."""
        raise NotImplementedError
