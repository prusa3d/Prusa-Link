"""Tests for the command queue"""
from threading import Event
from unittest.mock import Mock, patch

import pytest

from prusa.link.printer_adapter import command_queue as module  # type:ignore
from prusa.link.printer_adapter.command import (  # type:ignore
    Command,
    CommandFailed,
)
from prusa.link.printer_adapter.command_queue import (  # type:ignore
    CommandAdapter,
    CommandQueue,
)

# pylint: disable=redefined-outer-name


@pytest.fixture
def queue():
    """A started queue, with the telemetry passer and prctl faked"""
    with patch.object(module, "TelemetryPasser"), \
            patch.object(module, "prctl_name"):
        command_queue = CommandQueue()
        command_queue.start()
        yield command_queue
        command_queue.stop()
        command_queue.runner_thread.join(timeout=2)


def _command(result=None, error=None):
    command = Mock(spec=Command)
    if error is None:
        command.run_command.return_value = result
    else:
        command.run_command.side_effect = error
    return command


def test_adapter():
    """The adapter starts as unprocessed"""
    command = Mock()
    adapter = CommandAdapter(command)
    assert adapter.command is command
    assert not adapter.processed.is_set()
    assert adapter.exception is None
    assert not adapter.data


def test_do_command_returns_result(queue):
    """The result of the command is passed to the caller"""
    assert queue.do_command(_command({"a": 1})) == {"a": 1}


def test_do_command_raises(queue):
    """The exception of the command is passed to the caller"""
    with pytest.raises(CommandFailed, match="nope"):
        queue.do_command(_command(error=CommandFailed("nope")))


def test_commands_run_in_order(queue):
    """The queue is processed sequentially"""
    order = []
    commands = []
    for number in range(5):
        command = _command({"n": number})
        command.run_command.side_effect = \
            lambda number=number: order.append(number) or {"n": number}
        commands.append(command)
    adapters = [queue.enqueue_command(command) for command in commands]
    for adapter in adapters:
        assert adapter.processed.wait(timeout=2)
    assert order == [0, 1, 2, 3, 4]


def test_do_command_not_running():
    """In a stopped queue the command fails instead of blocking forever"""
    with patch.object(module, "TelemetryPasser"):
        command_queue = CommandQueue()
        with pytest.raises(CommandFailed):
            command_queue.do_command(_command())


def test_clear_queue_stops_commands():
    """Waiting commands are stopped and dropped"""
    command_queue = CommandQueue()
    waiting = [_command(), _command()]
    for command in waiting:
        command_queue.enqueue_command(command)
    current = _command()
    command_queue.current_command_adapter = CommandAdapter(current)
    command_queue.clear_queue()
    assert command_queue.command_queue.empty()
    current.stop.assert_called_once_with()
    for command in waiting:
        command.stop.assert_called_once_with()


def test_stop_stops_current():
    """Stopping the queue stops the running command"""
    command_queue = CommandQueue()
    command_queue.running = True
    current = _command()
    command_queue.current_command_adapter = CommandAdapter(current)
    command_queue.stop()
    assert not command_queue.running
    current.stop.assert_called_once_with()


def test_force_command(queue):
    """Force drops the waiting commands and runs the supplied one"""
    blocker = Event()
    slow = _command()
    slow.run_command.side_effect = lambda: blocker.wait(2) and {}
    slow.stop.side_effect = blocker.set
    dropped = _command({"dropped": True})

    running = queue.enqueue_command(slow)
    queue.enqueue_command(dropped)
    # wait for the slow one to be picked up
    for _ in range(100):
        if queue.current_command_adapter is running:
            break
        blocker.wait(0.01)
    assert queue.force_command(_command({"forced": True})) == {
        "forced": True}
    dropped.stop.assert_called_once_with()
    dropped.run_command.assert_not_called()
