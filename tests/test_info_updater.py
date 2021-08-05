"""Test of the InfoUpdater component"""

# pylint:disable=redefined-outer-name too-many-locals too-many-statements

import logging
from threading import Event
from time import time, sleep
from unittest import mock
from unittest.mock import Mock

import pytest

from prusa.link.printer_adapter.structures.info_updater import (  # type:ignore
    ItemUpdater, WatchedItem, WatchedGroup)

logging.basicConfig(level="DEBUG")

THRESHOLD = 0.05


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

        super().__init__(*args,
                         side_effect=lambda: waiter(self.event),
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
                         side_effect=lambda item=None: self.event.set(),
                         **kwargs)
        self.event = Event()


@pytest.fixture
def updater_instance():
    """
    A fixture providing an instance of the ItemUpdater for the tests
    """
    info_updater = ItemUpdater()
    info_updater.start()
    yield info_updater
    info_updater.stop()


@pytest.fixture
def validator():
    """A fixture providing a validator that only accepts 42 to the tests"""
    def inner(value):
        """A validator that accepts only 42 as a valid value"""
        return value == 42

    return inner


def get_waiting_mock(event_to_wait_for, return_value):
    """
    Returns a mock, that waits for a given event indefinitely and returns
    a given value
    """
    return Mock(side_effect=lambda: waiter(event_to_wait_for),
                return_value=return_value)


def test_basics(updater_instance: ItemUpdater):
    """
    Tests the basics, adding a watched item, gathering and writing of a value
    and became valid/invalid signalling
    :param updater_instance:
    :return:
    """

    gather = WaitingMock(return_value=42)
    write = Mock()
    # This empty spec makes it possible to pass this mock straight to a
    # blinker signal
    invalidated = EventSetMock(spec={})
    valid = EventSetMock(spec={})
    basic_item = WatchedItem("basic_item",
                             gather_function=gather,
                             write_function=write)
    basic_item.became_valid_signal.connect(valid)
    basic_item.became_invalid_signal.connect(invalidated)
    updater_instance.add_watched_item(basic_item)

    # Reminder that invalidation is only signalled when going from a
    # valid state, so not at the beginning
    invalidated.assert_not_called()
    valid.assert_not_called()
    write.assert_not_called()

    gather.event.set()  # unstuck the gather
    assert valid.event.wait(timeout=1), "Didn't signal becoming valid"

    gather.assert_called_once()
    write.assert_called_once_with(42)
    valid.assert_called_once_with(basic_item)

    updater_instance.invalidate(basic_item)
    assert invalidated.event.wait(timeout=1), "Didn't signal becoming invalid"

    invalidated.assert_called_once_with(basic_item)


def test_group(updater_instance: ItemUpdater):
    """
    Tests that the WatchedGroup becomes valid only after all its
    children are valid.
    Tests that the WatchedGroup becomes invalid, if it was valid and one of
    its members becomes invalid
    Tests that the WatchedGroup does not signal invalidation unless it was
    valid before
    """
    gather_1 = WaitingMock(return_value=1)
    gather_2 = WaitingMock(return_value=2)
    group_valid = EventSetMock(spec={})
    group_invalidated = EventSetMock(spec={})
    item_1_valid = EventSetMock(spec={})
    item_2_valid = EventSetMock(spec={})
    item_2_invalidated = EventSetMock(spec={})
    watched_item_1 = WatchedItem("watched_item_1",
                                 gather_function=gather_1,
                                 write_function=Mock())
    watched_item_1.became_valid_signal.connect(item_1_valid)
    watched_item_2 = WatchedItem("watched_item_2",
                                 gather_function=gather_2,
                                 write_function=Mock())
    watched_item_2.became_valid_signal.connect(item_2_valid)
    watched_item_2.became_invalid_signal.connect(item_2_invalidated)
    watched_group = WatchedGroup([watched_item_1, watched_item_2])
    watched_group.became_valid_signal.connect(group_valid)
    watched_group.became_invalid_signal.connect(group_invalidated)

    updater_instance.add_watched_item(watched_item_1)
    updater_instance.add_watched_item(watched_item_2)

    group_valid.assert_not_called()

    gather_1.event.set()

    assert item_1_valid.event.wait(THRESHOLD)

    group_valid.assert_not_called()

    gather_2.event.set()

    assert item_2_valid.event.wait(THRESHOLD)

    group_valid.assert_called_once()
    group_invalidated.assert_not_called()

    updater_instance.invalidate(watched_item_1)

    assert group_invalidated.event.wait(THRESHOLD)

    group_invalidated.assert_called_once()

    updater_instance.invalidate(watched_item_2)

    assert item_2_invalidated.event.wait(THRESHOLD)

    # Still only called once, not every time a member invalidates
    group_invalidated.assert_called_once()


def test_scheduled_invalidation(updater_instance: ItemUpdater):
    """
    Tests that scheduling an invalidation works properly

    1. Scheduling without an interval has to throw an error
    2. Scheduling without an interval when the item has a default uses the it
    3. Scheduling with an interval overwrites the default
    4. Re-scheduling the interval does nothing
    5. Force re-scheduling resets the interval
    6. Setting the value resets the scheduled invalidation
    7. Auto invalidation works
    8. Cancelling a scheduled invalidation works
    9. Setting a value to an item scheduled for invalidation without default
       interval does not cancel the scheduling (should it work this way?)
    """
    base_interval = 0.2  # base invalidation interval
    # for tests that refresh it, when to do so. should be < base_interval
    refresh_offset = 0.1
    offset_interval = base_interval + refresh_offset

    time_of_start = 0.0  # set up later to time()
    results = {}

    watched_item_1 = WatchedItem("watched_item_1",
                                 gather_function=Mock(),
                                 write_function=lambda value: None)
    watched_item_2 = WatchedItem("watched_item_2",
                                 gather_function=Mock(),
                                 write_function=Mock())
    watched_item_2.became_invalid_signal.connect(
        lambda item: results.update({2: time() - time_of_start}), weak=False)
    watched_item_3 = WatchedItem("watched_item_3",
                                 gather_function=Mock(),
                                 write_function=Mock())
    watched_item_3.became_invalid_signal.connect(
        lambda item: results.update({3: time() - time_of_start}), weak=False)
    watched_item_4 = WatchedItem("watched_item_4",
                                 gather_function=Mock(),
                                 write_function=Mock())
    watched_item_4.became_invalid_signal.connect(
        lambda item: results.update({4: time() - time_of_start}), weak=False)
    watched_item_5 = WatchedItem("watched_item_5",
                                 gather_function=Mock(),
                                 write_function=Mock())
    watched_item_5.became_invalid_signal.connect(
        lambda item: results.update({5: time() - time_of_start}), weak=False)
    watched_item_6 = WatchedItem("watched_item_6",
                                 gather_function=Mock(),
                                 write_function=Mock())
    watched_item_6.became_invalid_signal.connect(
        lambda item: results.update({6: time() - time_of_start}), weak=False)
    watched_item_7 = WatchedItem("watched_item_7",
                                 gather_function=Mock(),
                                 write_function=Mock(),
                                 interval=base_interval)
    watched_item_7.became_invalid_signal.connect(
        lambda item: results.update({7: time() - time_of_start}), weak=False)
    watched_item_8 = WatchedItem("watched_item_8",
                                 gather_function=Mock(),
                                 write_function=Mock(),
                                 interval=base_interval)
    watched_item_8.became_invalid_signal.connect(
        lambda item: results.update({8: time() - time_of_start}), weak=False)
    watched_item_9 = WatchedItem("watched_item_9",
                                 gather_function=Mock(),
                                 write_function=Mock(),
                                 interval=base_interval)
    watched_item_9.became_invalid_signal.connect(
        lambda item: results.update({9: time() - time_of_start}), weak=False)

    group_valid = EventSetMock(spec={})
    watched_group = WatchedGroup([
        watched_item_1, watched_item_2, watched_item_3, watched_item_4,
        watched_item_5, watched_item_6, watched_item_7, watched_item_8,
        watched_item_9
    ])
    watched_group.became_valid_signal.connect(group_valid)

    updater_instance.add_watched_item(watched_item_1)
    updater_instance.add_watched_item(watched_item_2)
    updater_instance.add_watched_item(watched_item_3)
    updater_instance.add_watched_item(watched_item_4)
    updater_instance.add_watched_item(watched_item_5)
    updater_instance.add_watched_item(watched_item_6)
    updater_instance.add_watched_item(watched_item_7)
    updater_instance.add_watched_item(watched_item_8)
    updater_instance.add_watched_item(watched_item_9)

    assert group_valid.event.wait(THRESHOLD)

    # set intervals after the items become valid for them to not auto schedule
    watched_item_2.interval = base_interval
    watched_item_3.interval = base_interval
    watched_item_4.interval = base_interval
    watched_item_5.interval = base_interval
    watched_item_6.interval = base_interval

    time_of_start = time()

    failed = False
    try:
        updater_instance.schedule_invalidation(watched_item_1)
    # pylint: disable=broad-except
    except Exception:
        failed = True
    assert failed, "Scheduling invalidation without an interval has to error"

    updater_instance.schedule_invalidation(watched_item_2)
    updater_instance.schedule_invalidation(watched_item_3,
                                           base_interval + refresh_offset)

    updater_instance.schedule_invalidation(watched_item_4)
    updater_instance.schedule_invalidation(watched_item_5)
    updater_instance.schedule_invalidation(watched_item_6)
    updater_instance.schedule_invalidation(watched_item_8,
                                           interval=base_interval)
    updater_instance.schedule_invalidation(watched_item_8,
                                           interval=base_interval)

    sleep(refresh_offset)
    updater_instance.cancel_scheduled_invalidation(watched_item_8)
    updater_instance.schedule_invalidation(watched_item_4)
    updater_instance.schedule_invalidation(watched_item_5, force=True)
    updater_instance.set_value(watched_item_6, 6)
    updater_instance.set_value(watched_item_9, 9)

    # Reset the intervals to none, so the items won't auto re-schedule
    watched_item_2.interval = None
    watched_item_3.interval = None
    watched_item_4.interval = None
    watched_item_5.interval = None
    watched_item_6.interval = None
    watched_item_7.interval = None

    # a "busy" wait in a test is fine
    times_out_at = time() + refresh_offset + THRESHOLD
    while not {2, 3, 4, 5, 6, 7, 9}.issubset(set(results.keys())):
        if time() > times_out_at:
            assert False, """Timed out waiting for invalidation results"""
        sleep(0.1)

    assert base_interval <= results[2] <= base_interval + THRESHOLD
    assert offset_interval <= results[3] <= offset_interval + THRESHOLD
    assert base_interval <= results[4] <= base_interval + THRESHOLD
    assert offset_interval <= results[5] <= offset_interval + THRESHOLD
    assert offset_interval <= results[6] <= offset_interval + THRESHOLD
    assert offset_interval <= results[9] <= offset_interval + THRESHOLD

    # No precise syncing is done for this one, it's assumed it would have
    # already invalidated because we were waiting more than THRESHOLD after
    # it would if it was broken
    assert 8 not in results


def test_validation(updater_instance: ItemUpdater, validator):
    """
    Tests that an item which gathers a valid value validates and
    that an item which gathers an invalid one errors out
    """
    valid_valid = EventSetMock(spec={})
    valid_errored = EventSetMock(spec={})
    valid_item = WatchedItem("valid_item",
                             gather_function=Mock(return_value=42),
                             write_function=Mock(),
                             validation_function=validator)
    valid_item.became_valid_signal.connect(valid_valid)
    valid_item.validation_error_signal.connect(valid_errored)
    updater_instance.add_watched_item(valid_item)

    invalid_valid = EventSetMock(spec={})
    invalid_errored = EventSetMock(spec={})
    invalid_item = WatchedItem("invalid_item",
                               gather_function=Mock(return_value=69),
                               write_function=Mock(),
                               validation_function=validator)
    invalid_item.became_valid_signal.connect(invalid_valid)
    invalid_item.validation_error_signal.connect(invalid_errored)
    updater_instance.add_watched_item(invalid_item)

    assert valid_valid.event.wait(THRESHOLD)
    assert invalid_errored.event.wait(THRESHOLD)

    valid_valid.assert_called_once_with(valid_item)
    valid_errored.assert_not_called()
    invalid_valid.assert_not_called()
    invalid_errored.assert_called_once_with(invalid_item)


def test_gather_error(updater_instance: ItemUpdater):
    """
    Test gather exception handling
    Test addressing items by their names
    :param updater_instance:
    :return:
    """
    fail_interval = 0.1
    threshold = 0.05

    item_errored = EventSetMock(spec={})
    write_mock = EventSetMock()
    item = WatchedItem("item",
                       gather_function=Mock(side_effect=RuntimeError("Test")),
                       write_function=write_mock,
                       on_fail_interval=fail_interval)
    item.error_refreshing_signal.connect(item_errored)

    time_of_start = time()
    updater_instance.add_watched_item(item)
    assert item_errored.event.wait(threshold)
    item_errored.event.clear()
    assert item_errored.event.wait(fail_interval + threshold)
    assert fail_interval < time() - time_of_start < fail_interval + threshold

    write_mock.assert_not_called()
    updater_instance.cancel_scheduled_invalidation("item")
    updater_instance.set_value("item", 42)
    assert write_mock.event.wait(threshold)


def test_timeouts(updater_instance: ItemUpdater, validator):
    """
    1. Test that a stuck getter times out
    2. Test that a failed getter which doesn't get re-scheduled times out
    3. Test that a getter that keeps failing times out
    4. Test that a getter which got stuck does not time out
       if the value is provided from the outside
    5. Test that an item which refreshes and then gets stuck times out after
       the full time out amount
    6. Test that an item which refreshes successfully doesn't time out
    """

    base_interval = 0.2  # base timeout interval
    wait_interval = 0.1
    offset_interval = base_interval + wait_interval

    stuck_gatherer = WaitingMock()

    item_1_timeout_mock = EventSetMock(spec={})
    watched_item_1 = WatchedItem("watched_item_1",
                                 gather_function=stuck_gatherer,
                                 write_function=Mock(),
                                 timeout=base_interval)
    watched_item_1.timed_out_signal.connect(item_1_timeout_mock)

    item_2_timeout_mock = EventSetMock(spec={})
    watched_item_2 = WatchedItem("watched_item_2",
                                 gather_function=Mock(return_value=69),
                                 write_function=Mock(),
                                 timeout=base_interval,
                                 on_fail_interval=1000,
                                 validation_function=validator)
    watched_item_2.timed_out_signal.connect(item_2_timeout_mock)

    item_3_timeout_mock = EventSetMock(spec={})
    watched_item_3 = WatchedItem("watched_item_3",
                                 gather_function=Mock(return_value=69),
                                 write_function=Mock(),
                                 timeout=base_interval,
                                 on_fail_interval=1000,
                                 validation_function=validator)
    watched_item_3.timed_out_signal.connect(item_3_timeout_mock)

    item_4_timeout_mock = EventSetMock(spec={})
    watched_item_4 = WatchedItem("watched_item_4",
                                 gather_function=stuck_gatherer,
                                 write_function=Mock(),
                                 timeout=base_interval)
    watched_item_4.timed_out_signal.connect(item_4_timeout_mock)

    item_5_timeout_mock = EventSetMock(spec={})
    item_5_valid_mock = EventSetMock(spec={})
    watched_item_5 = WatchedItem("watched_item_5",
                                 gather_function=stuck_gatherer,
                                 write_function=Mock(),
                                 timeout=base_interval)
    watched_item_5.timed_out_signal.connect(item_5_timeout_mock)
    watched_item_5.became_valid_signal.connect(item_5_valid_mock)

    item_6_timeout_mock = EventSetMock(spec={})
    watched_item_6 = WatchedItem("watched_item_6",
                                 gather_function=Mock(return_value=42),
                                 write_function=Mock(),
                                 timeout=base_interval)
    watched_item_6.timed_out_signal.connect(item_6_timeout_mock)

    time_of_start = time()
    updater_instance.add_watched_item(watched_item_1)
    assert item_1_timeout_mock.event.wait(base_interval + THRESHOLD)
    assert base_interval < time() - time_of_start < base_interval + THRESHOLD
    stuck_gatherer.event.set()
    stuck_gatherer.event.clear()

    time_of_start = time()
    updater_instance.add_watched_item(watched_item_2)
    assert item_2_timeout_mock.event.wait(base_interval + THRESHOLD)
    assert base_interval < time() - time_of_start < base_interval + THRESHOLD
    updater_instance.set_value(watched_item_2, 42)
    # make sure this does not get invalidated
    updater_instance.cancel_scheduled_invalidation(watched_item_2)

    time_of_start = time()
    updater_instance.add_watched_item(watched_item_3)
    assert item_3_timeout_mock.event.wait(base_interval + THRESHOLD)
    assert base_interval < time() - time_of_start < base_interval + THRESHOLD
    updater_instance.set_value(watched_item_3, 42)
    # make sure this does not get invalidated
    updater_instance.cancel_scheduled_invalidation(watched_item_3)

    updater_instance.add_watched_item(watched_item_4)
    sleep(wait_interval)
    updater_instance.set_value(watched_item_4, 1)
    assert not item_4_timeout_mock.event.wait(base_interval + THRESHOLD)
    stuck_gatherer.event.set()
    stuck_gatherer.event.clear()

    time_of_start = time()
    updater_instance.add_watched_item(watched_item_5)
    sleep(wait_interval)
    stuck_gatherer.event.set()
    stuck_gatherer.event.clear()
    assert item_5_valid_mock.event.wait(THRESHOLD)
    updater_instance.invalidate(watched_item_5)
    assert item_5_timeout_mock.event.wait(base_interval + THRESHOLD)
    # It is important that it's not less than the offset interval
    offset_with_threshold = offset_interval + THRESHOLD
    assert offset_interval < time() - time_of_start < offset_with_threshold
    stuck_gatherer.event.set()
    stuck_gatherer.event.clear()

    updater_instance.add_watched_item(watched_item_6)
    assert not item_6_timeout_mock.event.wait(base_interval + THRESHOLD)


def test_empty_group():
    """Test that an empty group raises an error on creation"""
    with pytest.raises(ValueError):
        WatchedGroup([])


def test_group_invalidation(updater_instance: ItemUpdater):
    """Test that group invalidation invalidates every member"""
    item_1_invalidated = EventSetMock(spec={})
    watched_item_1 = WatchedItem("watched_item_1",
                                 gather_function=Mock(),
                                 write_function=Mock())
    watched_item_1.became_invalid_signal.connect(item_1_invalidated)
    item_2_invalidated = EventSetMock(spec={})
    watched_item_2 = WatchedItem("watched_item_2",
                                 gather_function=Mock(),
                                 write_function=Mock())
    watched_item_2.became_invalid_signal.connect(item_2_invalidated)
    group = WatchedGroup([watched_item_1, watched_item_2])

    group_validated = EventSetMock(spec={})
    group.became_valid_signal.connect(group_validated)

    updater_instance.add_watched_item(watched_item_1)
    updater_instance.add_watched_item(watched_item_2)

    assert group_validated.event.wait(THRESHOLD)

    updater_instance.invalidate_group(group)
    assert item_1_invalidated.event.wait(THRESHOLD)
    assert item_2_invalidated.event.wait(THRESHOLD)


def test_garb(updater_instance: ItemUpdater):
    """
    Tests that item addressing by an invalid type fails and
    that that addressing a non existing item throws a key error
    Test that giving a non tracked item to the Item updater throws an error
    """
    item = WatchedItem("item", gather_function=Mock(), write_function=Mock())
    with pytest.raises(TypeError):
        updater_instance.schedule_invalidation(42)
    with pytest.raises(KeyError):
        updater_instance.schedule_invalidation("foo")
    with pytest.raises(ValueError):
        updater_instance.schedule_invalidation(item)


def test_valid_group_init(updater_instance: ItemUpdater):
    """
    Test that adding valid items to a WatchedGroup works too
    :param updater_instance:
    :return:
    """
    item_1_valid = EventSetMock(spec={})
    watched_item_1 = WatchedItem("watched_item_1",
                                 gather_function=Mock(),
                                 write_function=Mock())
    watched_item_1.became_valid_signal.connect(item_1_valid)

    item_2_valid = EventSetMock(spec={})
    watched_item_2 = WatchedItem("watched_item_2",
                                 gather_function=Mock(),
                                 write_function=Mock())
    watched_item_2.became_valid_signal.connect(item_2_valid)

    updater_instance.add_watched_item(watched_item_1)
    updater_instance.add_watched_item(watched_item_2)

    assert item_1_valid.event.wait(THRESHOLD)
    assert item_2_valid.event.wait(THRESHOLD)

    group_valid = EventSetMock(spec={})
    group_invalid = EventSetMock(spec={})

    group = WatchedGroup([watched_item_1, watched_item_2])
    group.became_valid_signal.connect(group_valid)
    group.became_valid_signal.connect(group_invalid)

    updater_instance.invalidate(watched_item_1)

    assert group_invalid.event.wait(THRESHOLD)
    assert group_valid.event.wait(THRESHOLD)


def test_valid_item_doesnt_gather(updater_instance: ItemUpdater):
    """
    Test that an item which became valid while being scheduled for gather
    does not actually gather
    """
    item_1_gather = WaitingMock()
    watched_item_1 = WatchedItem("watched_item_1",
                                 gather_function=item_1_gather,
                                 write_function=Mock())

    item_2_gather = EventSetMock(return_value=42)
    watched_item_2 = WatchedItem("watched_item_2",
                                 gather_function=item_2_gather,
                                 write_function=Mock())

    updater_instance.add_watched_item(watched_item_1)
    updater_instance.add_watched_item(watched_item_2)

    updater_instance.set_value(watched_item_2, 42)
    item_1_gather.event.set()

    # Check that even when the get got unstuck an already valid item
    # does not gather (Or should it?)
    assert not item_2_gather.event.wait(THRESHOLD)
