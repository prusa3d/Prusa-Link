import logging
import os
from collections import deque
from enum import Enum
from typing import Optional

from prusa.link.printer_adapter.default_settings import get_settings
from prusa.link.printer_adapter.structures.constants import QUEUE_SIZE, \
    DEFAULT_THRESHOLD, USE_DYNAMIC_THRESHOLD, IGNORE_ABOVE, HEAP_RATIO
from prusa.link.printer_adapter.structures.heap import MinHeap, MaxHeap, \
    HeapItem
from prusa.link.printer_adapter.util import ensure_directory, get_clean_path

PATH = get_settings().PATH

log = logging.getLogger(__name__)


class HeapName(Enum):
    SHORT_TIMES = "SHORT_TIMES"
    LONG_TIMES = "LONG_TIMES"


class TimeValue(HeapItem):
    def __init__(self, value):
        super().__init__(value)
        self.heap_name: Optional[HeapName] = None


class IsPlannerFed:
    """
    If the planner queue is full, I expect the printer to take longer when
    confirming print instructions, if the time surpasses a threshold,
    I assume full buffer. To stay future-proof, let's compute this threshold on
    the go.

    Let's measure the times for all instructions, disqualifying the ones that
    took too long. Now the threshold computation mimics the way one would
    compute a moving median. I use the two heaps approach.

    left heap is a max_heap, the right one is a min_heap, when a number comes,
    I compare it with the threshold and depending on the result I put it
    into one of the heaps. If that throws the ratio of element counts off,
    the heap that is larger than supposed to gives its root to the smaller one.

    The threshold is an average between the two roots.

    After the queue is full, the heaps shed the oldest values, so it can adapt,
    if for some reason the print commands start taking different amounts of
    time during the print. Problems can arise in hi-res cylindrical vases
    and other shapes with homogeneously long segments.

    To get rid of the inaccuracies caused by an initially low number of
    measured values, let's use a threshold from a previous run, or a default one
    until the values accumulate.
    """

    def __init__(self):
        self.times_queue = deque(maxlen=QUEUE_SIZE)

        self.threshold_path = get_clean_path(PATH.THRESHOLD_FILE)
        ensure_directory(os.path.dirname(self.threshold_path))

        if not USE_DYNAMIC_THRESHOLD:
            self.default_threshold = DEFAULT_THRESHOLD
        else:
            try:
                with open(self.threshold_path) as threshold_file:
                    self.default_threshold = float(threshold_file.read())
            except (FileNotFoundError, ValueError):
                self.default_threshold = DEFAULT_THRESHOLD

        self.is_fed = False

        self.short_times = MaxHeap()
        self.long_times = MinHeap()

    @property
    def item_count(self):
        return len(self.times_queue)

    @property
    def threshold(self):
        if self.item_count < self.times_queue.maxlen or \
                not USE_DYNAMIC_THRESHOLD:
            return self.default_threshold
        else:
            return self.get_dynamic_threshold()

    def get_dynamic_threshold(self):
        if not self.short_times and not self.long_times:
            return float("inf")
        elif self.short_times:
            return self.short_times[0].value
        else:
            return (self.long_times[0].value + self.short_times[0].value) / 2

    def __call__(self):
        """
        :return: boolean - Did it take too long?
        """
        return self.is_fed

    def process_value(self, value):
        """
        :param value: how long it took from send to confirmation
        """
        if value > IGNORE_ABOVE:
            return

        if self.item_count >= self.times_queue.maxlen:
            self._remove_last()
        self._add(value)

        self.is_fed = value > self.threshold

        if self.is_fed:
            log.debug(f"Buffer is fed, threshold: {self.threshold}, "
                      f"value: {value}")

    def _remove_last(self):
        item: TimeValue = self.times_queue.popleft()
        if item.heap_name == HeapName.LONG_TIMES:
            self.long_times.pop(item.heap_index)
        else:
            self.short_times.pop(item.heap_index)
        self.balance()

    def _add(self, value):
        item = TimeValue(value)

        if not self.short_times:
            self._short_push(item)
        elif not self.long_times:
            if self.short_times[0].value > value:
                larger_item = self.short_times.pop()
                self._short_push(item)
                self._long_push(larger_item)
            else:
                self._long_push(item)
        else:
            if value < self.get_dynamic_threshold():
                self._short_push(item)
            else:
                self._long_push(item)
            self.balance()

        self.times_queue.append(item)

    def balance(self):
        num_long = len(self.long_times)
        num_short = len(self.short_times)
        total = num_long + num_short
        ideal_short_count = round(total * HEAP_RATIO)
        if num_short < ideal_short_count - 1:
            self._short_push(self.long_times.pop())
        elif num_short > ideal_short_count + 1:
            self._long_push(self.short_times.pop())

        if self.short_times[0].value > self.long_times[0].value:
            raise RuntimeError("Smaller value heap has a higher value than "
                               "the higher value heap, that's not right...")

    def _short_push(self, item: TimeValue):
        item.heap_name = HeapName.SHORT_TIMES
        self.short_times.push(item)

    def _long_push(self, item: TimeValue):
        item.heap_name = HeapName.LONG_TIMES
        self.long_times.push(item)

    def save(self):
        if self.item_count >= self.times_queue.maxlen:
            with open(self.threshold_path, "w") as threshold_file:
                threshold_file.write(str(self.get_dynamic_threshold()))

