"""Tests of the planner buffer guessing"""
from unittest.mock import patch

import pytest

from prusa.link.serial import is_planner_fed as module  # type:ignore
from prusa.link.serial.is_planner_fed import (  # type:ignore
    HeapName,
    IsPlannerFed,
    TimeValue,
)

# pylint: disable=redefined-outer-name,protected-access

QUEUE = 10


@pytest.fixture(autouse=True)
def settings():
    """Small queue, the dynamic threshold on, even heaps"""
    with patch.object(module, "QUEUE_SIZE", QUEUE), \
            patch.object(module, "USE_DYNAMIC_THRESHOLD", True), \
            patch.object(module, "DEFAULT_THRESHOLD", 0.5), \
            patch.object(module, "HEAP_RATIO", 0.5), \
            patch.object(module, "IGNORE_ABOVE", 10):
        yield


@pytest.fixture
def planner(tmp_path):
    """The planner over a not existing threshold file"""
    return IsPlannerFed(str(tmp_path / "data" / "threshold.data"))


def test_defaults(planner, tmp_path):
    """Nothing known yet, the folder for the threshold is made"""
    assert planner.default_threshold == 0.5
    assert planner.threshold == 0.5
    assert planner() is False
    assert planner.item_count == 0
    assert (tmp_path / "data").is_dir()


def test_threshold_from_the_file(tmp_path):
    """The saved threshold is used until enough values come"""
    path = tmp_path / "threshold.data"
    path.write_text("0.25")
    assert IsPlannerFed(str(path)).default_threshold == 0.25
    path.write_text("garbage")
    assert IsPlannerFed(str(path)).default_threshold == 0.5


def test_static_threshold(tmp_path):
    """Without the dynamic threshold the default is always used"""
    with patch.object(module, "USE_DYNAMIC_THRESHOLD", False):
        planner = IsPlannerFed(str(tmp_path / "t"))
        for _ in range(QUEUE + 2):
            planner.process_value(1)
        assert planner.threshold == 0.5


def test_dynamic_threshold_empty(planner):
    """No values, no threshold"""
    assert planner.get_dynamic_threshold() == float("inf")


def test_values_above_the_limit_are_ignored(planner):
    """Too long confirmations say nothing about the buffer"""
    planner.process_value(11)
    assert planner.item_count == 0


def test_fed_decision(planner):
    """The long confirmation means the buffer is full"""
    planner.process_value(0.1)
    assert planner() is False
    planner.process_value(1)
    assert planner() is True
    planner.process_value(0.1)
    assert planner() is False


def test_first_values_fill_the_heaps(planner):
    """The first two values are put to the right heaps"""
    planner.process_value(2)
    assert [item.value for item in planner.short_times] == [2]
    planner.process_value(1)
    assert planner.short_times[0].value == 1
    assert planner.long_times[0].value == 2
    planner.process_value(3)
    assert planner.item_count == 3


def test_second_larger_value(planner):
    """A larger second value goes to the long heap"""
    planner.process_value(1)
    planner.process_value(2)
    assert planner.long_times[0].value == 2
    assert planner.get_dynamic_threshold() == 1.5
    assert planner.threshold == 0.5  # not enough values yet


def test_dynamic_threshold_after_filling_the_queue(planner):
    """With the full queue the percentile is used"""
    for value in range(1, QUEUE + 1):
        planner.process_value(value / 10)
    assert planner.item_count == QUEUE
    assert planner.threshold == planner.get_dynamic_threshold()
    assert 0.1 < planner.threshold <= 1.0


def test_old_values_are_dropped(planner):
    """The queue and heaps stay limited"""
    for value in range(1, 4 * QUEUE):
        planner.process_value((value % 7 + 1) / 10)
    assert planner.item_count == QUEUE
    assert len(planner.short_times) + len(planner.long_times) == QUEUE


def test_heaps_stay_ordered(planner):
    """Every short value is not larger than every long one"""
    for value in (5, 1, 9, 3, 7, 2, 8, 4, 6, 0.5, 9.5, 1.5, 2.5):
        planner.process_value(value)
        if planner.long_times:
            assert planner.short_times[0].value <= \
                planner.long_times[0].value


def test_balance_detects_broken_heaps(planner):
    """Broken ordering is an error"""
    planner._short_push(TimeValue(5))
    planner._long_push(TimeValue(1))
    with pytest.raises(RuntimeError):
        planner.balance()


def test_heap_names():
    """Items remember their heap"""
    item = TimeValue(1)
    assert item.heap_name is None
    assert {name.value for name in HeapName} == {"SHORT_TIMES",
                                                 "LONG_TIMES"}


def test_save_only_with_a_full_queue(planner, tmp_path):
    """The threshold is saved when it is reliable"""
    path = tmp_path / "data" / "threshold.data"
    planner.process_value(1)
    planner.save()
    assert not path.exists()
    for value in range(QUEUE):
        planner.process_value((value + 1) / 10)
    planner.save()
    assert float(path.read_text()) == planner.get_dynamic_threshold()
    assert IsPlannerFed(str(path)).default_threshold == float(
        path.read_text())
