"""Tests of the hidden menu logic"""
from unittest.mock import MagicMock, patch

import pytest

from prusa.link.printer_adapter import special_commands  # type:ignore
from prusa.link.printer_adapter.command import CommandFailed  # type:ignore
from prusa.link.printer_adapter.special_commands import (  # type:ignore
    CMD_TIMEOUT,
    SpecialCommands,
)
from prusa.link.printer_adapter.structures.regular_expressions import (  # type:ignore  # noqa: E501
    OPEN_RESULT_REGEX,
    PRINT_DONE_REGEX,
    START_PRINT_REGEX,
)

# pylint: disable=redefined-outer-name,protected-access
# pylint: disable=comparison-with-callable


@pytest.fixture
def parser():
    """The serial parser which only remembers the handlers"""
    return MagicMock()


@pytest.fixture
def queue():
    """The command queue"""
    return MagicMock()


@pytest.fixture
def commands(parser, queue):
    """The special commands with a menu folder 'menu'"""
    commands_ = SpecialCommands(parser, queue)
    commands_.menu_folder_found(None, "menu")
    return commands_


def opened(path):
    """The match of a line about opening a file"""
    return OPEN_RESULT_REGEX.match(f"File opened: {path} Size: 5")


START = START_PRINT_REGEX.match('echo:enqueing "M24"')
DONE = PRINT_DONE_REGEX.match("Done printing file")


def listen(signal):
    """Collects the matches sent through the signal"""
    received = []
    signal.connect(lambda _, match: received.append(match), weak=False)
    return received


def test_handlers_registered(parser, queue):
    """The three kinds of lines are handled"""
    commands_ = SpecialCommands(parser, queue)
    registered = {call.args[0]: call.args[1]
                  for call in parser.add_decoupled_handler.call_args_list}
    assert registered[OPEN_RESULT_REGEX] == commands_.handle_file
    assert registered[START_PRINT_REGEX] == commands_.handle_start
    assert registered[PRINT_DONE_REGEX] == commands_.handle_done


def test_menu_folder_lifetime(commands):
    """The folder is known while the card is inserted"""
    assert commands.menu_folder_sfn == "menu"
    commands.menu_folder_gone(None)
    assert commands.menu_folder_sfn is None


@pytest.mark.parametrize(("path", "special"), [
    ("/MENU/SETREADY.G", True),
    ("/menu/setready.g", True),
    ("/menu/other.g", False),
    ("/other/setready.g", False),
    ("setready.g", False),
])
def test_open_is_special(commands, path, special):
    """Only the known file in the menu folder is special"""
    assert commands._open_is_special(opened(path)) is special


def test_open_failed_is_not_special(commands):
    """A failed open has no file name"""
    match = OPEN_RESULT_REGEX.match("open failed, File: x.")
    assert commands._open_is_special(match) is False


def test_open_without_menu_is_not_special(commands):
    """Without the menu folder nothing is special"""
    commands.menu_folder_gone(None)
    assert commands._open_is_special(opened("/menu/setready.g")) is False


def test_ordinary_file_is_passed_along(commands):
    """Files we do not care about are passed to the others"""
    received = listen(commands.open_result_signal)
    match = opened("/gcodes/a.gcode")
    commands.handle_file(None, match)
    assert received == [match]
    assert commands.current is None


def test_special_file_is_held_back(commands):
    """The menu file is not passed along, the command is prepared"""
    received = listen(commands.open_result_signal)
    commands.handle_file(None, opened("/menu/setready.g"))
    assert not received
    assert commands.current == commands.set_ready
    assert commands.detected_at > 0


def test_start_of_ordinary_print(commands):
    """The start of print is passed along"""
    received = listen(commands.start_print_signal)
    commands.handle_start(None, START)
    assert received == [START]


def test_start_of_special_print(commands):
    """The start of the menu file print is held back"""
    received = listen(commands.start_print_signal)
    commands.handle_file(None, opened("/menu/setready.g"))
    commands.detected_at -= 0.5 * CMD_TIMEOUT
    before = commands.detected_at
    commands.handle_start(None, START)
    assert not received
    assert commands.current is not None
    assert commands.detected_at > before


def test_start_after_timeout(commands):
    """The prepared command expired"""
    received = listen(commands.start_print_signal)
    commands.handle_file(None, opened("/menu/setready.g"))
    commands.detected_at -= CMD_TIMEOUT + 1
    commands.handle_start(None, START)
    assert received == [START]
    assert commands.current is None


def test_done_runs_the_command(commands, queue):
    """When the placeholder is done, the command runs"""
    received = listen(commands.print_done_signal)
    commands.handle_file(None, opened("/menu/setready.g"))
    with patch.object(special_commands, "SetReady") as set_ready:
        commands.handle_done(None, DONE)
    queue.do_command.assert_called_once_with(set_ready.return_value)
    assert not received
    assert commands.current is None


def test_done_of_ordinary_print(commands, queue):
    """The end of an ordinary print is passed along"""
    received = listen(commands.print_done_signal)
    commands.handle_done(None, DONE)
    assert received == [DONE]
    queue.do_command.assert_not_called()


def test_set_ready_failure_is_logged(commands, queue):
    """The failing command does not break the parser thread"""
    queue.do_command.side_effect = CommandFailed
    with patch.object(special_commands, "SetReady"), \
            patch.object(special_commands, "InterestingLogRotator") as logs:
        commands.set_ready()
    logs.trigger.assert_called_once()
