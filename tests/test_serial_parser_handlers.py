"""Tests of the handler management and of the decoupling in serial parser"""
import re
from threading import Event, get_ident
from unittest.mock import MagicMock

import pytest

from prusa.link.serial.serial_parser import (  # type:ignore
    RegexPairing,
    SerialParser,
    ThreadedSerialParser,
)

# pylint: disable=redefined-outer-name,protected-access

HELLO = re.compile(r"^hello (?P<who>\w+)$")
ANY = re.compile(r"^.*$")


@pytest.fixture
def parser():
    """Plain parser, the singleton is released after the test"""
    parser_ = SerialParser()
    yield parser_
    SerialParser._MCSingleton__instance = None


@pytest.fixture
def threaded():
    """The decoupled parser with its thread, stopped afterwards"""
    parser_ = ThreadedSerialParser()
    yield parser_
    parser_.stop()
    parser_.wait_stopped()
    SerialParser._MCSingleton__instance = None
    ThreadedSerialParser._MCSingleton__instance = None


def test_pairing_description():
    """The description counts handlers"""
    pairing = RegexPairing(HELLO, priority=3)
    assert "1 handler" not in str(pairing)
    pairing.signal.connect(lambda *_, **__: None, weak=False)
    assert str(pairing).endswith("having 1 handler")
    pairing.signal.connect(lambda *_, **__: None, weak=False)
    assert repr(pairing).endswith("having 2 handlers")
    assert "priority 3" in str(pairing)


def test_pairing_survives_failing_handler():
    """A broken handler must not kill the reader"""
    pairing = RegexPairing(HELLO)
    seen = MagicMock()

    def broken(*_, **__):
        raise RuntimeError("boom")

    pairing.signal.connect(broken, weak=False)
    pairing.signal.connect(seen, weak=False)
    pairing.fire(HELLO.match("hello you"))


def test_decide_calls_handler_with_match(parser):
    """The handler gets the match"""
    handler = MagicMock()
    parser.add_handler(HELLO, handler)
    parser.decide("hello world")
    assert handler.call_args.kwargs["match"].group("who") == "world"


def test_decide_without_match(parser):
    """Unknown lines are only logged"""
    handler = MagicMock()
    parser.add_handler(HELLO, handler)
    parser.decide("bye")
    handler.assert_not_called()


def test_higher_priority_goes_first(parser):
    """Only the first matching pairing is fired"""
    low, high = MagicMock(), MagicMock()
    parser.add_handler(ANY, low, priority=1)
    parser.add_handler(HELLO, high, priority=5)
    parser.decide("hello you")
    high.assert_called_once()
    low.assert_not_called()


def test_same_regexp_collects_handlers(parser):
    """All handlers of the one regexp are called"""
    first, second = MagicMock(), MagicMock()
    parser.add_handler(HELLO, first)
    parser.add_handler(HELLO, second)
    parser.decide("hello you")
    first.assert_called_once()
    second.assert_called_once()
    assert len(parser.pattern_list) == 1


def test_adding_with_higher_priority_reorders(parser):
    """A later handler can raise the priority of the pairing"""
    first, second = MagicMock(), MagicMock()
    parser.add_handler(ANY, first, priority=2)
    parser.add_handler(HELLO, second, priority=1)
    parser.add_handler(HELLO, MagicMock(), priority=9)
    parser.decide("hello you")
    second.assert_called_once()
    first.assert_not_called()


def test_remove_handler(parser):
    """The pairing disappears with its last handler"""
    first, second = MagicMock(), MagicMock()
    parser.add_handler(HELLO, first)
    parser.add_handler(HELLO, second)
    parser.remove_handler(HELLO, first)
    assert HELLO in parser.pairing_dict
    parser.remove_handler(HELLO, second)
    assert HELLO not in parser.pairing_dict
    assert not parser.pattern_list


def test_remove_unknown_handler(parser):
    """Removing what was not added is an error"""
    with pytest.raises(RuntimeError):
        parser.remove_handler(HELLO, MagicMock())


def test_decoupled_handler_runs_in_the_thread(threaded):
    """The handler is called from the decoupler thread"""
    finished = Event()
    received = {}

    def handler(_, match):
        received["thread"] = get_ident()
        received["who"] = match.group("who")
        finished.set()

    threaded.add_decoupled_handler(HELLO, handler)
    threaded.decide("hello world")
    assert finished.wait(2)
    assert received["thread"] != get_ident()
    assert received["who"] == "world"


def test_stop_ends_the_thread(threaded):
    """Stopping wakes the thread up"""
    threaded.stop()
    threaded.wait_stopped()
    assert not threaded.thread.is_alive()
