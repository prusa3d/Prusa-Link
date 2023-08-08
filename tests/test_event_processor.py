"""Tests the event processor module"""
from enum import Enum
from unittest.mock import Mock

from _pytest.python_api import raises

from prusa.link.const import QUIT_INTERVAL
from prusa.link.printer_adapter.event_processor import (
    EventInfo,
    EventProcessor,
    ProcessorEvent,
    StateWatcher,
)
from tests.util import EventSetMock


class TestInputEventName(Enum):
    """Test input event names"""
    INPUT_OUTPUT = "INPUT_OUTPUT"
    INPUT_NO_OUTPUT = "INPUT_NO_OUTPUT"
    STOP_WATCHING = "STOP_WATCHING"


class MockCaller:
    """Allows tests to register and call a callback"""

    def __init__(self):
        self.callback = None

    def set_callback(self, callback):
        """Set the callback, that we can then trigger by call()"""
        self.callback = callback

    def unset_callback(self, _):
        """Unset the callback"""
        self.callback = None

    def call(self, *args, **kwargs):
        """Trigger the callback if presen"""
        if self.callback is not None:
            self.callback(*args, **kwargs)


class MockWatcher(StateWatcher):
    """A mock state watcher tracking a few simple events"""

    class OutputEvents(Enum):
        """Test output event names"""
        OUTPUT_TEST = "OUTPUT_TEST"

    def __init__(self):
        super().__init__()
        self.mock_handler = EventSetMock()
        self.event_handlers = {
            TestInputEventName.INPUT_OUTPUT: self.input_test_handler,
            TestInputEventName.INPUT_NO_OUTPUT: self.input_no_output_handler,
            TestInputEventName.STOP_WATCHING: self.stop_watching_handler,
        }

    def input_test_handler(self, *args, **kwargs):
        """Handler for the INPUT_OUTPUT event"""
        self.mock_handler(*args, **kwargs)
        return ProcessorEvent(self.OutputEvents.OUTPUT_TEST,
                              "Headcrab", "Zombie")

    def input_no_output_handler(self):
        """Handler for the INPUT_NO_OUTPUT event"""
        self.mock_handler()

    def stop_watching_handler(self):
        """Handler for the STOP_WATCHING event"""
        self.mock_handler()
        self._stop_watching(TestInputEventName.INPUT_OUTPUT)


def test_event_info_registration():
    """Tests that adding a watcher calls the registration and removing it
    calls the deregistration"""
    event_info = EventInfo(
        name="test",
        registration=Mock(),
        deregistration=Mock(),
    )
    watcher = Mock()
    event_info.add_watcher(watcher)
    event_info.registration.assert_called_once()
    event_info.deregistration.assert_not_called()
    event_info.remove_watcher(watcher)
    event_info.deregistration.assert_called_once()


def test_event_info_callback():
    """Tests that a callback with the event is called"""
    mock_callback_generator = MockCaller()
    event_info = EventInfo(
        name="test",
        registration=mock_callback_generator.set_callback,
        deregistration=mock_callback_generator.unset_callback,
    )
    callback_mock = Mock()
    event_info.set_callback(callback_mock)

    watcher = Mock()
    event_info.add_watcher(watcher)
    mock_callback_generator.call("test", 1, 2, 3,
                                 psyche="rock", thelegend=27)
    processor_event = callback_mock.call_args[0][0]
    assert processor_event.args == ("test", 1, 2, 3)
    assert processor_event.kwargs == {"psyche": "rock", "thelegend": 27}


def test_event_path():
    """Tests that an event is processed and an output one is generated"""
    event_processor = EventProcessor()
    io_mock_caller = MockCaller()
    no_io_mock_caller = MockCaller()
    stop_mock_caller = MockCaller()
    io_event_info = EventInfo(
        name=TestInputEventName.INPUT_OUTPUT,
        registration=io_mock_caller.set_callback,
        deregistration=io_mock_caller.unset_callback,
    )
    no_io_event_info = EventInfo(
        name=TestInputEventName.INPUT_NO_OUTPUT,
        registration=no_io_mock_caller.set_callback,
        deregistration=no_io_mock_caller.unset_callback,
    )
    stop_watching_event_info = EventInfo(
        name=TestInputEventName.STOP_WATCHING,
        registration=stop_mock_caller.set_callback,
        deregistration=stop_mock_caller.unset_callback,
    )

    event_processor.track_event(io_event_info)
    event_processor.track_event(no_io_event_info)
    event_processor.track_event(stop_watching_event_info)
    output_mock = EventSetMock()
    event_processor.add_output_event_handler(
        MockWatcher.OutputEvents.OUTPUT_TEST,
        output_mock)
    mock_watcher = MockWatcher()
    event_processor.add_watcher(mock_watcher)

    assert mock_watcher.tracked_events[io_event_info.name] == io_event_info
    mock_watcher.mock_handler.assert_not_called()

    # Test an event that generates the output
    io_mock_caller.call("Half-Life", 3, status="confirmed")
    mock_watcher.mock_handler.event.wait(QUIT_INTERVAL)
    mock_watcher.mock_handler.assert_called_with(
        "Half-Life", 3, status="confirmed",
    )
    output_mock.event.wait(QUIT_INTERVAL)
    output_mock.assert_called_once()

    mock_watcher.mock_handler.reset_mock()
    output_mock.reset_mock()

    # Test an event watcher that does not generate an output
    no_io_mock_caller.call()
    mock_watcher.mock_handler.event.wait(QUIT_INTERVAL)
    mock_watcher.mock_handler.assert_called_once()
    output_mock.event.wait(QUIT_INTERVAL)
    output_mock.assert_not_called()

    mock_watcher.mock_handler.reset_mock()
    output_mock.reset_mock()

    # Test an event that de-registers the io event info from the call generator
    stop_mock_caller.call()
    mock_watcher.mock_handler.event.wait(QUIT_INTERVAL)
    mock_watcher.mock_handler.assert_called_once()

    mock_watcher.mock_handler.reset_mock()
    output_mock.reset_mock()

    # Stop the component so if an event was created,
    # it would stay in the queue
    event_processor.stop()
    event_processor.wait_stopped()

    # Check that the input event has not been enqueued
    io_mock_caller.call("Half-Life", 3, status="confirmed")
    assert event_processor.input_queue.empty()


def test_untracked_event():
    """Tests that registering a watcher that watches an untracked event
    raises an exception"""
    event_processor = EventProcessor()
    mock_watcher = MockWatcher()
    with raises(ValueError):
        event_processor.add_watcher(mock_watcher)

    event_processor.stop()
    event_processor.wait_stopped()
