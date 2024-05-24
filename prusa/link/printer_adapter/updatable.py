"""
Contains implementation of the ThreadedUpdatable class
There was an updatable without a thread, but it stopped being used

Also contains a thread utility function
"""
from cProfile import Profile
from functools import partial
from threading import Event
from threading import Thread as _Thread
from time import monotonic
from typing import Callable

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


class DeadManSwitch(ThreadedUpdatable):
    """A class allowing the user to call a function after touch()
    stops being called"""

    thread_name = "deadman_sw"
    update_interval = 1

    def __init__(self, timeout: float, callable: Callable[[], None]):
        super().__init__()
        self.timeout = timeout
        self.callable = callable
        self.active = False
        self.last_touched = monotonic()

    def touch(self):
        """Refresh the timeout and activate the deadman switch"""
        self.active = True
        self.last_touched = monotonic()

    def update(self):
        """Fires if the timeout has been reached"""
        while not self.quit_evt.is_set():
            if self.active and monotonic() - self.last_touched > self.timeout:
                self.fire()

    def fire(self):
        self.active = False
        self.callable()
