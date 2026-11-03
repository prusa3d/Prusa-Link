"""Tests for the keepalive component"""
from unittest.mock import Mock, patch

import pytest

from prusa.link.printer_adapter import keepalive as module  # type:ignore
from prusa.link.printer_adapter.keepalive import (  # type:ignore
    Keepalive,
    KeepaliveMode,
)

# pylint: disable=redefined-outer-name,protected-access


@pytest.fixture
def keepalive():
    """A keepalive that is not running, the singleton is released after"""
    instance = Keepalive(Mock())
    yield instance
    Keepalive._MCSingleton__instance = None


def test_default_mode(keepalive):
    """PrusaLink mode is the default"""
    assert keepalive.mode is KeepaliveMode.PL


def test_set_use_connect(keepalive):
    """The mode follows Connect usage and wakes the thread"""
    keepalive.set_use_connect(True)
    assert keepalive.mode is KeepaliveMode.PC
    assert keepalive.wait_evt.is_set()
    keepalive.set_use_connect(False)
    assert keepalive.mode is KeepaliveMode.PL


def test_singleton(keepalive):
    """A second instance is refused"""
    assert Keepalive.get_instance() is keepalive
    with pytest.raises(RuntimeError):
        Keepalive(Mock())


@pytest.mark.parametrize(("use_connect", "mode"),
                         [(False, "PL"), (True, "PC")])
def test_keepalive_sends_mode(keepalive, use_connect, mode):
    """The M79 gcode carries the mode, it goes in front of the queue"""
    keepalive.set_use_connect(use_connect)
    keepalive.wait_evt.clear()
    sent = []

    def fake_enqueue(queue, message, to_front=False):
        sent.append((queue, message, to_front))
        keepalive.stop()  # one round is enough
        return Mock()

    with patch.object(module, "enqueue_instruction",
                      side_effect=fake_enqueue), \
            patch.object(module, "wait_for_instruction"):
        keepalive._keepalive()
    assert sent == [(keepalive.serial_queue, f'M79 S"{mode}"', True)]


def test_stop_and_wait(keepalive):
    """Stopping sets both events, thread is joined"""
    keepalive.keepalive_thread = Mock()
    keepalive.stop()
    assert keepalive.quit_evt.is_set()
    assert keepalive.wait_evt.is_set()
    keepalive.wait_stopped()
    keepalive.keepalive_thread.join.assert_called_once_with()
