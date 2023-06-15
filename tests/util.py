"""Utility functions and classes for tests"""
from threading import Event
from unittest import mock
from unittest.mock import Mock


def waiter(event: Event):
    """
    Waits for the supplied event, returns a constant that allows a Mock
    to behave nicely
    """
    event.wait()
    return mock.DEFAULT


class WaitingMock(Mock):
    """
    Waits for its built in event when called, otherwise it's a regular mock
    """
    def __init__(self, *args, side_effect=None, **kwargs):
        if side_effect is not None:
            raise AttributeError("Do not provide a side effect to this mock, "
                                 "it has its own waiting one")

        super().__init__(
            *args,
            side_effect=lambda *args, **kwargs: waiter(self.event),
            **kwargs)
        self.event = Event()


class EventSetMock(Mock):
    """
    Sets its built in event when called, otherwise it's a regular mock
    """
    def __init__(self, *args, side_effect=None, **kwargs):
        if side_effect is not None:
            raise AttributeError("Do not provide a side effect to this mock, "
                                 "it has its own waiting one")

        super().__init__(*args,
                         side_effect=lambda *args, **kwargs: self.event.set(),
                         **kwargs)
        self.event = Event()

    def reset_mock(self, *args, **kwargs) -> None:
        super().reset_mock(*args, **kwargs)
        self.event.clear()
