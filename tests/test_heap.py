"""Tests for the min and max heap"""
import random

import pytest

from prusa.link.printer_adapter.structures.heap import (  # type:ignore
    HeapItem,
    MaxHeap,
    MinHeap,
)


def _drain(heap):
    values = []
    while heap:
        values.append(heap.pop().value)
    return values


def test_min_heap_order():
    """Items come out smallest first"""
    heap = MinHeap()
    for value in (5, 1, 4, 2, 3, 0):
        heap.push(HeapItem(value))
    assert len(heap) == 6
    assert _drain(heap) == [0, 1, 2, 3, 4, 5]
    assert not heap


def test_max_heap_order():
    """Items come out biggest first"""
    heap = MaxHeap()
    for value in (5, 1, 4, 2, 3, 0):
        heap.push(HeapItem(value))
    assert _drain(heap) == [5, 4, 3, 2, 1, 0]


@pytest.mark.parametrize(("heap_class", "reverse"), [
    (MinHeap, False),
    (MaxHeap, True),
])
def test_random_values(heap_class, reverse):
    """Random values are sorted"""
    rng = random.Random(42)
    values = [rng.randint(-100, 100) for _ in range(200)]
    heap = heap_class()
    for value in values:
        heap.push(HeapItem(value))
    assert _drain(heap) == sorted(values, reverse=reverse)


def test_pop_by_index():
    """An item from the middle can be removed using its heap index"""
    heap = MinHeap()
    items = [HeapItem(value) for value in range(10)]
    for item in items:
        heap.push(item)
    removed = heap.pop(items[6].heap_index)
    assert removed is items[6]
    assert _drain(heap) == [0, 1, 2, 3, 4, 5, 7, 8, 9]


def test_pop_last():
    """Popping the only item leaves an empty heap"""
    heap = MinHeap()
    item = HeapItem(1)
    heap.push(item)
    assert heap.pop() is item
    assert len(heap) == 0
    assert not heap


def test_pop_wrong_index():
    """Inconsistent index is detected"""
    heap = MinHeap()
    item = HeapItem(1)
    heap.push(item)
    item.heap_index = 5
    with pytest.raises(RuntimeError):
        heap.pop()


def test_getitem_setitem():
    """Direct access to the underlying list"""
    heap = MinHeap()
    item = HeapItem(1)
    heap.push(item)
    assert heap[0] is item
    other = HeapItem(2)
    heap[0] = other
    assert heap[0] is other


def test_heap_item_comparison():
    """Items are compared by their heap value"""
    small, big = HeapItem(1), HeapItem(2)
    small.heap_value, big.heap_value = 1, 2
    assert small < big
    assert small <= big
    assert big > small
    assert big >= small
    assert small != big
    same = HeapItem(1)
    same.heap_value = 1
    assert small == same
    assert hash(small) == hash(same)


@pytest.mark.parametrize("operation", [
    lambda a, b: a < b,
    lambda a, b: a <= b,
    lambda a, b: a > b,
    lambda a, b: a >= b,
    lambda a, b: a == b,
])
def test_heap_item_comparison_type(operation):
    """Items can be compared only with other items"""
    with pytest.raises(TypeError):
        operation(HeapItem(1), 1)
