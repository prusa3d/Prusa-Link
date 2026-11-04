"""Tests for the base Command class"""
from unittest.mock import Mock, patch

import pytest
from prusa.connect.printer.const import Source

from prusa.link.printer_adapter import command as module  # type:ignore
from prusa.link.printer_adapter.command import (  # type:ignore
    Command,
    CommandFailed,
    FileNotFound,
    NotStateToPrint,
)


class Returning(Command):
    """Command returning custom data"""
    def __init__(self, data, **kwargs):
        super().__init__(**kwargs)
        self.data = data

    def _run_command(self):
        return self.data


def test_exceptions_hierarchy():
    """Specific failures are command failures"""
    assert issubclass(NotStateToPrint, CommandFailed)
    assert issubclass(FileNotFound, CommandFailed)


def test_defaults():
    """The source is Connect by default, the components are not set"""
    command = Returning(None)
    assert command.source == Source.CONNECT
    assert command.command_id is None
    assert not command.quit_evt.is_set()


def test_run_command_default_data():
    """The source is always in the result"""
    command = Returning(None, source=Source.WUI, command_id=7)
    assert command.run_command() == {"source": Source.WUI}


def test_run_command_merges_data():
    """The command can add to or override the default data"""
    command = Returning({"a": 1, "source": Source.MARLIN})
    assert command.run_command() == {"a": 1, "source": Source.MARLIN}


def test_stop():
    """Stopping sets the quit event"""
    command = Returning(None)
    command.stop()
    assert command.quit_evt.is_set()


def test_do_instruction_confirmed():
    """Instruction is enqueued to the front and returned when confirmed"""
    command = Returning(None)
    instruction = Mock()
    instruction.is_confirmed.return_value = True
    with patch.object(module, "enqueue_instruction",
                      return_value=instruction) as enqueue, \
            patch.object(module, "wait_for_instruction"):
        assert command.do_instruction("G28") is instruction
    enqueue.assert_called_once_with(command.serial_queue, "G28",
                                    to_front=True)


def test_do_instruction_interrupted():
    """An unconfirmed instruction means the command was interrupted"""
    command = Returning(None)
    instruction = Mock()
    instruction.is_confirmed.return_value = False
    with patch.object(module, "enqueue_instruction",
                      return_value=instruction), \
            patch.object(module, "wait_for_instruction"):
        with pytest.raises(CommandFailed):
            command.do_instruction("G28")


def test_do_matchable():
    """Matchable instruction is enqueued to the front with the regexp"""
    command = Returning(None)
    instruction = Mock()
    instruction.is_confirmed.return_value = True
    regexp = Mock()
    with patch.object(module, "enqueue_matchable",
                      return_value=instruction) as enqueue, \
            patch.object(module, "wait_for_instruction"):
        assert command.do_matchable("M105", regexp) is instruction
    enqueue.assert_called_once_with(command.serial_queue, "M105", regexp,
                                    to_front=True)


def test_wait_while_running_uses_quit_event():
    """Waiting ends when the command is stopped"""
    command = Returning(None)
    instruction = Mock()
    with patch.object(module, "wait_for_instruction") as wait:
        command.wait_while_running(instruction)
    wait.assert_called_once_with(instruction,
                                 should_wait_evt=command.quit_evt)
