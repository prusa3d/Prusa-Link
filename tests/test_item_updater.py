"""Test of the InfoUpdater component"""

# pylint:disable=redefined-outer-name too-many-locals too-many-statements

import logging
import math
from queue import PriorityQueue
from time import sleep, time
from unittest.mock import Mock

import pytest

from prusa.link.printer_adapter.structures.item_updater import (  # type:ignore
    ItemUpdater,
    WatchedGroup,
    WatchedItem,
)

from .util import EventSetMock, WaitingMock

logging.basicConfig(level="DEBUG")

THRESHOLD = 0.05


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
    updater_instance.add_item(basic_item)

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
    item_1 = WatchedItem("item_1",
                         gather_function=gather_1,
                         write_function=Mock())
    item_1.became_valid_signal.connect(item_1_valid)
    item_2 = WatchedItem("item_2",
                         gather_function=gather_2,
                         write_function=Mock())
    item_2.became_valid_signal.connect(item_2_valid)
    item_2.became_invalid_signal.connect(item_2_invalidated)
    watched_group = WatchedGroup([item_1, item_2])
    watched_group.became_valid_signal.connect(group_valid)
    watched_group.became_invalid_signal.connect(group_invalidated)

    updater_instance.add_item(item_1)
    updater_instance.add_item(item_2)

    group_valid.assert_not_called()

    gather_1.event.set()

    assert item_1_valid.event.wait(THRESHOLD)

    group_valid.assert_not_called()

    gather_2.event.set()

    assert item_2_valid.event.wait(THRESHOLD)

    group_valid.assert_called_once()
    group_invalidated.assert_not_called()

    updater_instance.invalidate(item_1)

    assert group_invalidated.event.wait(THRESHOLD)

    group_invalidated.assert_called_once()

    updater_instance.invalidate(item_2)

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

    item_1 = WatchedItem("item_1",
                         gather_function=Mock(),
                         write_function=lambda value: None)
    item_2 = WatchedItem("item_2", gather_function=Mock())
    item_2.became_invalid_signal.connect(
        lambda item: results.update({2: time() - time_of_start}), weak=False)
    item_3 = WatchedItem("item_3", gather_function=Mock())
    item_3.became_invalid_signal.connect(
        lambda item: results.update({3: time() - time_of_start}), weak=False)
    item_4 = WatchedItem("item_4", gather_function=Mock())
    item_4.became_invalid_signal.connect(
        lambda item: results.update({4: time() - time_of_start}), weak=False)
    item_5 = WatchedItem("item_5", gather_function=Mock())
    item_5.became_invalid_signal.connect(
        lambda item: results.update({5: time() - time_of_start}), weak=False)
    item_6 = WatchedItem("item_6", gather_function=Mock())
    item_6.became_invalid_signal.connect(
        lambda item: results.update({6: time() - time_of_start}), weak=False)
    item_7 = WatchedItem("item_7",
                         gather_function=Mock(),
                         interval=base_interval)
    item_7.became_invalid_signal.connect(
        lambda item: results.update({7: time() - time_of_start}), weak=False)
    item_8 = WatchedItem("item_8",
                         gather_function=Mock(),
                         interval=base_interval)
    item_8.became_invalid_signal.connect(
        lambda item: results.update({8: time() - time_of_start}), weak=False)
    item_9 = WatchedItem("item_9",
                         gather_function=Mock(),
                         interval=base_interval)
    item_9.became_invalid_signal.connect(
        lambda item: results.update({9: time() - time_of_start}), weak=False)

    group_valid = EventSetMock(spec={})
    watched_group = WatchedGroup([
        item_1, item_2, item_3, item_4, item_5, item_6, item_7, item_8, item_9,
    ])
    watched_group.became_valid_signal.connect(group_valid)

    updater_instance.add_item(item_1)
    updater_instance.add_item(item_2)
    updater_instance.add_item(item_3)
    updater_instance.add_item(item_4)
    updater_instance.add_item(item_5)
    updater_instance.add_item(item_6)
    updater_instance.add_item(item_7)
    updater_instance.add_item(item_8)
    updater_instance.add_item(item_9)

    assert group_valid.event.wait(THRESHOLD)

    # set intervals after the items become valid for them to not auto schedule
    item_2.interval = base_interval
    item_3.interval = base_interval
    item_4.interval = base_interval
    item_5.interval = base_interval
    item_6.interval = base_interval

    time_of_start = time()

    failed = False
    try:
        updater_instance.schedule_invalidation(item_1)
    # pylint: disable=broad-except
    except Exception:
        failed = True
    assert failed, "Scheduling invalidation without an interval has to error"

    updater_instance.schedule_invalidation(item_2)
    updater_instance.schedule_invalidation(item_3,
                                           base_interval + refresh_offset)

    updater_instance.schedule_invalidation(item_4)
    updater_instance.schedule_invalidation(item_5)
    updater_instance.schedule_invalidation(item_6)
    updater_instance.schedule_invalidation(item_8, interval=base_interval)
    updater_instance.schedule_invalidation(item_8, interval=base_interval)

    sleep(refresh_offset)
    updater_instance.cancel_scheduled_invalidation(item_8)
    updater_instance.schedule_invalidation(item_4)
    updater_instance.schedule_invalidation(item_5, reschedule=True)
    updater_instance.set_value(item_6, 6)
    updater_instance.set_value(item_9, 9)

    # Reset the intervals to none, so the items won't auto re-schedule
    item_2.interval = None
    item_3.interval = None
    item_4.interval = None
    item_5.interval = None
    item_6.interval = None
    item_7.interval = None

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
                             validation_function=validator)
    valid_item.became_valid_signal.connect(valid_valid)
    valid_item.validation_error_signal.connect(valid_errored)
    updater_instance.add_item(valid_item)

    invalid_valid = EventSetMock(spec={})
    invalid_errored = EventSetMock(spec={})
    invalid_item = WatchedItem("invalid_item",
                               gather_function=Mock(return_value=69),
                               validation_function=validator)
    invalid_item.became_valid_signal.connect(invalid_valid)
    invalid_item.validation_error_signal.connect(invalid_errored)
    updater_instance.add_item(invalid_item)

    assert valid_valid.event.wait(THRESHOLD)
    assert invalid_errored.event.wait(THRESHOLD)

    valid_valid.assert_called_once_with(valid_item)
    valid_errored.assert_not_called()
    invalid_valid.assert_not_called()
    invalid_errored.assert_called_once()


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
    updater_instance.add_item(item)
    assert item_errored.event.wait(threshold)
    item_errored.event.clear()
    assert item_errored.event.wait(fail_interval + threshold)
    assert fail_interval < time() - time_of_start < fail_interval + threshold

    write_mock.assert_not_called()
    updater_instance.cancel_scheduled_invalidation(item)
    updater_instance.set_value(item, 42)
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
    item_1 = WatchedItem("item_1",
                         gather_function=stuck_gatherer,
                         timeout=base_interval)
    item_1.timed_out_signal.connect(item_1_timeout_mock)

    item_2_timeout_mock = EventSetMock(spec={})
    item_2 = WatchedItem("item_2",
                         gather_function=Mock(return_value=69),
                         timeout=base_interval,
                         on_fail_interval=1000,
                         validation_function=validator)
    item_2.timed_out_signal.connect(item_2_timeout_mock)

    item_3_timeout_mock = EventSetMock(spec={})
    item_3 = WatchedItem("item_3",
                         gather_function=Mock(return_value=69),
                         timeout=base_interval,
                         on_fail_interval=1000,
                         validation_function=validator)
    item_3.timed_out_signal.connect(item_3_timeout_mock)

    item_4_timeout_mock = EventSetMock(spec={})
    item_4 = WatchedItem("item_4",
                         gather_function=stuck_gatherer,
                         timeout=base_interval)
    item_4.timed_out_signal.connect(item_4_timeout_mock)

    item_5_timeout_mock = EventSetMock(spec={})
    item_5_valid_mock = EventSetMock(spec={})
    item_5 = WatchedItem("item_5",
                         gather_function=stuck_gatherer,
                         timeout=base_interval)
    item_5.timed_out_signal.connect(item_5_timeout_mock)
    item_5.became_valid_signal.connect(item_5_valid_mock)

    item_6_timeout_mock = EventSetMock(spec={})
    item_6 = WatchedItem("item_6",
                         gather_function=Mock(return_value=42),
                         timeout=base_interval)
    item_6.timed_out_signal.connect(item_6_timeout_mock)

    time_of_start = time()
    updater_instance.add_item(item_1)
    assert item_1_timeout_mock.event.wait(base_interval + THRESHOLD)
    assert base_interval < time() - time_of_start < base_interval + THRESHOLD
    stuck_gatherer.event.set()
    stuck_gatherer.event.clear()

    time_of_start = time()
    updater_instance.add_item(item_2)
    assert item_2_timeout_mock.event.wait(base_interval + THRESHOLD)
    assert base_interval < time() - time_of_start < base_interval + THRESHOLD
    updater_instance.set_value(item_2, 42)
    # make sure this does not get invalidated
    updater_instance.cancel_scheduled_invalidation(item_2)

    time_of_start = time()
    updater_instance.add_item(item_3)
    assert item_3_timeout_mock.event.wait(base_interval + THRESHOLD)
    assert base_interval < time() - time_of_start < base_interval + THRESHOLD
    updater_instance.set_value(item_3, 42)
    # make sure this does not get invalidated
    updater_instance.cancel_scheduled_invalidation(item_3)

    updater_instance.add_item(item_4)
    sleep(wait_interval)
    updater_instance.set_value(item_4, 1)
    assert not item_4_timeout_mock.event.wait(base_interval + THRESHOLD)
    stuck_gatherer.event.set()
    stuck_gatherer.event.clear()

    time_of_start = time()
    updater_instance.add_item(item_5)
    sleep(wait_interval)
    stuck_gatherer.event.set()
    stuck_gatherer.event.clear()
    assert item_5_valid_mock.event.wait(THRESHOLD)
    updater_instance.invalidate(item_5)
    assert item_5_timeout_mock.event.wait(base_interval + THRESHOLD)
    # It is important that it's not less than the offset interval
    offset_with_threshold = offset_interval + THRESHOLD
    assert offset_interval < time() - time_of_start < offset_with_threshold
    stuck_gatherer.event.set()
    stuck_gatherer.event.clear()

    updater_instance.add_item(item_6)
    assert not item_6_timeout_mock.event.wait(base_interval + THRESHOLD)


def test_empty_group():
    """Test that an empty group raises an error on creation"""
    with pytest.raises(ValueError):
        WatchedGroup([])


def test_group_invalidation(updater_instance: ItemUpdater):
    """Test that group invalidation invalidates every member"""
    item_1_invalidated = EventSetMock(spec={})
    item_1 = WatchedItem("item_1", gather_function=Mock())
    item_1.became_invalid_signal.connect(item_1_invalidated)
    item_2_invalidated = EventSetMock(spec={})
    item_2 = WatchedItem("item_2", gather_function=Mock())
    item_2.became_invalid_signal.connect(item_2_invalidated)
    group = WatchedGroup([item_1, item_2])

    group_validated = EventSetMock(spec={})
    group.became_valid_signal.connect(group_validated)

    updater_instance.add_item(item_1)
    updater_instance.add_item(item_2)

    assert group_validated.event.wait(THRESHOLD)

    updater_instance.invalidate_group(group)
    assert item_1_invalidated.event.wait(THRESHOLD)
    assert item_2_invalidated.event.wait(THRESHOLD)


def test_garb(updater_instance: ItemUpdater):
    """
    Tests that addressing a non existing item throws a ValueError error
    Tests that adding a garbage for tracking fails
    """
    item = WatchedItem("item", gather_function=Mock())
    with pytest.raises(ValueError):
        updater_instance.schedule_invalidation(item)


def test_valid_group_init(updater_instance: ItemUpdater):
    """
    Test that adding valid items to a WatchedGroup works too
    :param updater_instance:
    :return:
    """
    item_1_valid = EventSetMock(spec={})
    item_1 = WatchedItem("item_1", gather_function=Mock())
    item_1.became_valid_signal.connect(item_1_valid)

    item_2_valid = EventSetMock(spec={})
    item_2 = WatchedItem("item_2", gather_function=Mock())
    item_2.became_valid_signal.connect(item_2_valid)

    updater_instance.add_item(item_1)
    updater_instance.add_item(item_2)

    assert item_1_valid.event.wait(THRESHOLD)
    assert item_2_valid.event.wait(THRESHOLD)

    group_valid = EventSetMock(spec={})
    group_invalid = EventSetMock(spec={})

    group = WatchedGroup([item_1, item_2])
    group.became_valid_signal.connect(group_valid)
    group.became_valid_signal.connect(group_invalid)

    updater_instance.invalidate(item_1)

    assert group_invalid.event.wait(THRESHOLD)
    assert group_valid.event.wait(THRESHOLD)


def test_valid_item_doesnt_gather(updater_instance: ItemUpdater):
    """
    Test that an item which became valid while being scheduled for gather
    does not actually gather
    """
    item_1_gather = WaitingMock()
    item_1 = WatchedItem("item_1", gather_function=item_1_gather)

    item_2_gather = EventSetMock(spec={}, return_value=69)
    item_2 = WatchedItem("item_2", gather_function=item_2_gather)

    updater_instance.add_item(item_1)
    updater_instance.add_item(item_2)

    updater_instance.set_value(item_2, 42)
    item_1_gather.event.set()

    # Check that even when the get got unstuck an already valid item
    # does not gather (Or should it?)
    assert not item_2_gather.event.wait(THRESHOLD)


def test_subclasses_work(updater_instance: ItemUpdater):
    """Tests a subclass validates when being added to the Updater"""
    class MyItem(WatchedItem):
        """Just a WatchedItem subclass for the test"""

    my_item = MyItem("my_item")
    updater_instance.add_item(my_item)
    assert my_item in updater_instance.items


def test_disabling(updater_instance: ItemUpdater):
    """
    Can we disable and enable the item updating without affecting
    any interval logic?
    """
    item_gather = EventSetMock(spec={})
    item = WatchedItem("item", gather_function=item_gather, interval=0.2)

    updater_instance.add_item(item, start_tracking=False)
    assert not item_gather.event.wait(THRESHOLD)
    assert item.interval == 0.2
    updater_instance.disable(item)
    updater_instance.invalidate(item)
    assert not item_gather.event.wait(THRESHOLD), \
        "Do not invalidate disabled items"
    updater_instance.schedule_invalidation(item, interval=0.1)
    assert not item_gather.event.wait(THRESHOLD), \
        "Do not invalidate " "disabled items"
    updater_instance.enable(item)
    assert item_gather.event.wait(THRESHOLD)
    assert item.invalidate_at <= time() + item.interval
    updater_instance.disable(item)
    assert item.invalidate_at == math.inf


def test_group_updating(updater_instance: ItemUpdater):
    """
    Test a bug, where if a became_valid handler invalidated the same item
    that just became balid, sometimes, the group the item was in got
    notifiied after all has been done and got confused to the point of
    raising a KeyError
    """
    item_gather = EventSetMock(spec={})
    item = WatchedItem("Item",  gather_function=item_gather)
    updater_instance.add_item(item, start_tracking=False)
    group = WatchedGroup([item])
    item.became_valid_signal.connect(
        lambda _: updater_instance.invalidate_group(group), weak=False,
    )
    for _ in range(100):
        updater_instance.invalidate(item)
        item_gather.event.wait(THRESHOLD)
        item_gather.event.clear()


def test_priority_queue():
    """Tests that you can have two watched items with the same priority
    and the app does not throw an error"""
    item_1 = WatchedItem("item_1")
    item_2 = WatchedItem("item_2")
    queue = PriorityQueue()
    queue.put((1, item_1))
    queue.put((1, item_2))
    queue.get()
    queue.get()
