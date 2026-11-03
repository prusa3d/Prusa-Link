"""Tests for the serial instructions"""
import re

import pytest

from prusa.link.serial.instruction import (  # type:ignore
    Instruction,
    MandatoryMatchableInstruction,
    MatchableInstruction,
)


def test_newline_refused():
    """Instruction is a single line"""
    with pytest.raises(RuntimeError):
        Instruction("G28\nG29")


def test_str_and_repr():
    """Human readable representation"""
    instruction = Instruction("G28 ")
    assert str(instruction) == "Instruction 'G28'"
    assert repr(instruction) == str(instruction)


def test_sent_and_confirm():
    """Events and times are set on send and on confirmation"""
    instruction = Instruction("G28")
    assert not instruction.is_sent()
    assert not instruction.is_confirmed()
    assert not instruction.wait_for_send(timeout=0)
    assert not instruction.wait_for_confirmation(timeout=0)

    instruction.sent()
    assert instruction.is_sent()
    assert instruction.sent_at is not None
    assert instruction.wait_for_send(timeout=0)

    assert instruction.confirm()
    assert instruction.is_confirmed()
    assert instruction.time_to_confirm >= 0
    assert instruction.wait_for_confirmation(timeout=0)


def test_reset():
    """Reset makes the instruction unsent again"""
    instruction = Instruction("G28")
    instruction.sent()
    instruction.reset()
    assert not instruction.is_sent()
    assert instruction.sent_at is None


def test_fill_data_plain():
    """Without a checksum only the newline is appended"""
    instruction = Instruction("G28")
    instruction.fill_data(5)
    assert instruction.data == b"G28\n"
    assert instruction.number is None


def test_fill_data_checksum():
    """Numbered line with a checksum, as the RepRap protocol wants"""
    instruction = Instruction("G28", to_checksum=True)
    instruction.fill_data(5)
    payload = b"N5 G28 "
    expected = payload + f"*{Instruction.get_checksum(payload)}".encode() \
        + b"\n"
    assert instruction.data == expected
    assert instruction.number == 5


def test_get_checksum():
    """XOR of all bytes"""
    assert Instruction.get_checksum(b"") == 0
    assert Instruction.get_checksum(b"A") == ord("A")
    assert Instruction.get_checksum(b"AA") == 0
    assert Instruction.get_checksum(b"N0 M110 N0 ") == \
        Instruction.get_checksum(b"N0 M110 N0 ")
    assert Instruction.get_checksum(bytes([1, 2, 4])) == 7


def test_base_does_not_capture():
    """The base instruction has no regexps and ignores the output"""
    instruction = Instruction("G28")
    assert not instruction.capturing_regexps
    match = re.match(r"ok", "ok")
    instruction.output_captured(sender=object(), match=match)


def test_matchable_capture():
    """Matchable keeps the captured output"""
    regex = re.compile(r"(?P<a>ok)")
    instruction = MatchableInstruction("M105", capture_matching=regex)
    assert instruction.capturing_regexps == [regex]
    assert instruction.match() is None
    assert not instruction.get_matches()

    first, second = regex.match("ok"), regex.match("ok")
    instruction.output_captured(sender=object(), match=first)
    instruction.output_captured(sender=object(), match=second)
    assert instruction.match() is first
    assert instruction.match(1) is second
    assert instruction.match(2) is None
    assert instruction.get_matches() == [first, second]


def test_matchable_confirms_without_match():
    """Plain matchable instruction can be confirmed without any output"""
    instruction = MatchableInstruction("M105")
    instruction.sent()
    assert instruction.confirm()


def test_mandatory_matchable_refuses():
    """Mandatory one refuses confirmation without the output"""
    regex = re.compile(r"ok")
    instruction = MandatoryMatchableInstruction("M105",
                                                capture_matching=regex)
    instruction.sent()
    assert not instruction.confirm()
    assert not instruction.is_confirmed()


def test_mandatory_matchable_force():
    """Forcing overrides the refusal"""
    instruction = MandatoryMatchableInstruction("M105")
    instruction.sent()
    assert instruction.confirm(force=True)
    assert instruction.is_confirmed()


def test_mandatory_matchable_confirms():
    """With the output captured, it is confirmed"""
    regex = re.compile(r"ok")
    instruction = MandatoryMatchableInstruction("M105",
                                                capture_matching=regex)
    instruction.sent()
    instruction.output_captured(sender=object(), match=regex.match("ok"))
    assert instruction.confirm()
    assert instruction.is_confirmed()
