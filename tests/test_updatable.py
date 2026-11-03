"""Tests for the threaded updatable and singleton helper"""
from threading import Event
from unittest.mock import patch

import pytest

from prusa.link.printer_adapter import updatable  # type:ignore
from prusa.link.printer_adapter.structures.mc_singleton import (  # type:ignore  # noqa: E501
    MCSingleton,
)
from prusa.link.printer_adapter.updatable import (  # type:ignore
    Thread,
    ThreadedUpdatable,
)


def test_update_not_implemented():
    """The base class has nothing to update"""
    with pytest.raises(NotImplementedError):
        ThreadedUpdatable().update()


def test_updates_until_stopped():
    """update is called repeatedly, until stop is called"""
    called = Event()

    class Counter(ThreadedUpdatable):
        """Counts updates"""
        update_interval = 0.01
        count = 0

        def update(self):
            self.count += 1
            if self.count == 3:
                called.set()

    counter = Counter()
    with patch("prusa.link.util.prctl_name"):
        counter.start()
        assert called.wait(timeout=2)
        counter.stop()
        counter.wait_stopped()
    assert counter.count >= 3
    assert not counter.thread.is_alive()


def test_profiling_swap():
    """Profiling replaces run() and can be switched back"""
    original = Thread.run
    try:
        Thread.enable_profiling()
        assert Thread.run is Thread.profile_run
        Thread.disable_profiling()
        assert Thread.run is updatable._Thread.run  # pylint: disable=W0212
    finally:
        Thread.run = original


def test_mc_singleton():
    """Only one instance may exist, get_instance returns it"""
    class Single(metaclass=MCSingleton):
        """Singleton"""
        def __init__(self, value):
            self.value = value

    assert Single.get_instance() is None
    single = Single(5)
    assert single.value == 5
    assert Single.get_instance() is single
    with pytest.raises(RuntimeError):
        Single(6)
