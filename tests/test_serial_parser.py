"""Tests for the serial parser component"""
import re
from unittest.mock import Mock

from prusa.link.serial.serial_parser import SerialParser  # type:ignore

# pylint: disable=protected-access


def test_basic():
    """Basic, one handler, call it on match"""
    regex = re.compile(r"(?P<a>Hello)")
    handler = Mock()
    parser = SerialParser()
    parser.add_handler(regex, handler)
    parser.decide("Hello")
    handler.assert_called_once()
    assert handler.call_args.kwargs["match"].group("a") == "Hello"
    SerialParser._MCSingleton__instance = None


def test_inverted_basic():
    """Basic, don't call when it does not match"""
    regex = re.compile(r"(?P<a>Hello)")
    handler = Mock()
    parser = SerialParser()
    parser.add_handler(regex, handler)
    parser.decide("Bye")
    handler.assert_not_called()
    SerialParser._MCSingleton__instance = None


def test_basic_removal():
    """Basic, one handler, call it on match"""
    regex = re.compile(r"(?P<a>Hello)")
    handler = Mock()
    parser = SerialParser()
    parser.add_handler(regex, handler)
    parser.remove_handler(regex, handler)
    parser.decide("Hello")
    handler.assert_not_called()
    SerialParser._MCSingleton__instance = None


def test_priority():
    """
    Do call just the higher priority handler don't call the rest
    Do not use the class like this, ideally there should not be two regexps
    matching the same thing, but if yes, usually it's for an instruction to
    take priority over some other thing.
    """
    regex1 = re.compile(r"(?P<a>Hell[o])")
    regex2 = re.compile(r"(?P<a>[H]ello)")
    regex3 = re.compile(r"(?P<a>H[e]llo)")
    handler1 = Mock()
    handler2 = Mock()
    handler3 = Mock()
    parser = SerialParser()

    parser.add_handler(regex1, handler1, 1)
    parser.add_handler(regex3, handler3, 3)
    parser.add_handler(regex2, handler2, 2)
    parser.decide("Hello")
    handler3.assert_called_once()
    handler2.assert_not_called()
    handler1.assert_not_called()
    assert handler3.call_args.kwargs["match"].group("a") == "Hello"
    SerialParser._MCSingleton__instance = None


def test_bump_priority():
    """
    If the same regex is already registered, adding a handler with a higher
    priority should elevate all other handlers to the same priority.
    This is mostly an implementation detail
    """
    regex1 = re.compile(r"(?P<a>Hell[o])")
    regex2 = re.compile(r"(?P<a>[H]ello)")
    handler1 = Mock()
    handler2 = Mock()
    handler3 = Mock()
    parser = SerialParser()

    parser.add_handler(regex1, handler1, 1)
    parser.add_handler(regex2, handler2, 2)
    parser.add_handler(regex1, handler3, 3)
    parser.decide("Hello")
    handler3.assert_called_once()
    handler1.assert_called_once()
    handler2.assert_not_called()
    assert handler3.call_args.kwargs["match"].group("a") == "Hello"
    assert handler1.call_args.kwargs["match"].group("a") == "Hello"
    SerialParser._MCSingleton__instance = None
