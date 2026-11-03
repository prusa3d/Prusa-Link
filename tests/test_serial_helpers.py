"""Tests for the instruction enqueuing helpers"""
import re
from threading import Event
from unittest.mock import Mock

from prusa.link.serial.helpers import (  # type:ignore
    enqueue_instruction,
    enqueue_list_from_str,
    enqueue_matchable,
    wait_for_instruction,
)
from prusa.link.serial.instruction import (  # type:ignore
    Instruction,
    MandatoryMatchableInstruction,
    MatchableInstruction,
)


def test_enqueue_instruction():
    """A plain instruction is created and enqueued"""
    queue = Mock()
    instruction = enqueue_instruction(queue, "G28", to_front=True,
                                      to_checksum=True)
    assert type(instruction) is Instruction  # pylint: disable=C0123
    assert instruction.message == "G28"
    assert instruction.to_checksum
    queue.enqueue_one.assert_called_once_with(instruction, to_front=True)


def test_enqueue_matchable_mandatory():
    """By default the instruction has to match"""
    queue = Mock()
    regex = re.compile("ok")
    instruction = enqueue_matchable(queue, "M105", regex)
    assert isinstance(instruction, MandatoryMatchableInstruction)
    assert instruction.capture_matching is regex
    queue.enqueue_one.assert_called_once_with(instruction, to_front=False)


def test_enqueue_matchable_optional():
    """The match can be made optional"""
    queue = Mock()
    instruction = enqueue_matchable(queue, "M105", re.compile("ok"),
                                    has_to_match=False)
    assert type(instruction) is MatchableInstruction  # pylint: disable=C0123


def test_enqueue_list_from_str():
    """Every message becomes one matchable, they are enqueued together"""
    queue = Mock()
    regex = re.compile("ok")
    instructions = enqueue_list_from_str(queue, ["G28", "M105"], regex,
                                         to_checksum=True)
    assert [i.message for i in instructions] == ["G28", "M105"]
    assert all(i.capture_matching is regex and i.to_checksum
               for i in instructions)
    queue.enqueue_list.assert_called_once_with(instructions, to_front=False)


def test_wait_for_instruction_confirmed():
    """Returns True once the instruction is confirmed"""
    instruction = Mock()
    instruction.wait_for_confirmation.side_effect = [False, True]
    assert wait_for_instruction(instruction, check_every=0.01)
    assert instruction.wait_for_confirmation.call_count == 2


def test_wait_for_instruction_should_not_wait():
    """Returns False without waiting, when waiting is not wanted"""
    instruction = Mock()
    assert not wait_for_instruction(instruction, should_wait=lambda: False)
    instruction.wait_for_confirmation.assert_not_called()


def test_wait_for_instruction_event():
    """The quit event stops the waiting"""
    instruction = Mock()
    evt = Event()
    evt.set()
    assert not wait_for_instruction(instruction, should_wait_evt=evt)
    instruction.wait_for_confirmation.assert_not_called()
