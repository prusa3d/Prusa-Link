"""Implements classes for monitoring and updating arbitrary values"""

import logging
from math import inf
from multiprocessing import Event
from queue import Empty, PriorityQueue, Queue
from threading import RLock, Thread
from time import time
from typing import Any, Callable, Iterable, Optional, Set

from blinker import Signal  # type: ignore

from ...util import prctl_name

log = logging.getLogger(__name__)


class SideEffectOnly(Exception):
    """An exception to raise in a gatherer that has nothing to return,
    but its side effects succeeded in setting a value or
    have otherwise ensured that the value would be received eventually"""


class Watchable:
    """Encapsulates the common stuff between watched values and groups"""

    def __init__(self):

        self.valid = False

        self.became_valid_signal = Signal()
        self.became_invalid_signal = Signal()


class WatchedItem(Watchable):
    """
    A value, that can be polled or set.
    Can be tracked in the info updater
    """
    # Set to None to disable automatic refreshes on read/validation fails
    default_on_fail_interval = 5

    # pylint: disable=too-many-arguments
    def __init__(self,
                 name,
                 gather_function: Optional[Callable[[], Any]] = None,
                 write_function: Optional[Callable[[Any], None]] = None,
                 validation_function: Optional[Callable[[Any], bool]] = None,
                 interval=None,
                 timeout=None,
                 on_fail_interval=default_on_fail_interval):
        super().__init__()
        self.name = name
        self.value: Any = None
        self.lock = RLock()

        self.in_groups: Set["WatchedGroup"] = set()

        self.scheduled = False  # Are we scheduled for a value refresh
        # Imprecise timing intended
        self.interval = interval  # If set, gets invalidated each interval
        self.disabled = False  # If True, the interval is overridden with None

        self.on_fail_interval = on_fail_interval  # Refresh reschedule timeout
        self.timeout = timeout  # How long can we be invalid, before timing out

        # internal timestamps
        self.invalidate_at = inf
        self.times_out_at = inf

        # pylint: disable=unused-argument
        def _default_validation(value):
            return True

        # pylint: disable=unused-argument
        def _default_write(value):
            ...

        if validation_function is None:
            validation_function = _default_validation

        if write_function is None:
            write_function = _default_write

        # A function that returns a value, or throws an error
        # If it returns None, The value is not written and the item gets
        # re-scheduled
        self.gather_function: Optional[Callable[[], Any]] = gather_function
        # If valid, returns Ture, if not, throws an error or returns False
        self.validation_function: Callable[[Any], bool] = validation_function
        # Takes care of putting the value in the right places
        # Shall not throw anything EVER!
        self.write_function: Callable[["WatchedItem"], None] = write_function

        # -- Signals --

        self.timed_out_signal = Signal()
        self.error_refreshing_signal = Signal()
        self.validation_error_signal = Signal()  # kwargs: validation exception
        self.value_changed_signal = Signal()  # sender is the value
        # Combined gather error signal
        self.val_err_timeout_signal = Signal()

    def __repr__(self):
        return super().__repr__() + ": " + self.name

    def __lt__(self, other):
        if not isinstance(other, WatchedItem):
            return NotImplemented
        return self.name < other.name

    def __eq__(self, other):
        if not isinstance(other, WatchedItem):
            return NotImplemented
        return self.name == other.name

    def __hash__(self):
        return hash(self.name)


class WatchedGroup(Watchable):
    """
    A group of watched items.
    Aggregates the validity signals from its members
    """

    def __init__(self, items: Iterable[WatchedItem]):
        super().__init__()

        if not items:
            raise ValueError(
                "Supply at least one item, or group to be watched")

        self.all_items = list(items)
        self.valid_items = set()
        self.invalid_items = set()

        for item in items:
            # Tracking using these signals,
            item.in_groups.add(self)

            if item.valid:
                self.valid_items.add(item)
            else:
                self.invalid_items.add(item)

        if not self.invalid_items:
            self.valid = True

    def __iter__(self):
        return self.all_items.__iter__()

    def invalid_handler(self, item):
        """
        A member became invalid. Moves the member to the invalid pile
        If the group was valid, it's not anymore and that gets signalled
        """
        self.valid_items.remove(item)
        self.invalid_items.add(item)

        if self.valid:
            self.valid = False
            self.became_invalid_signal.send(self)

    def valid_handler(self, item):
        """
        A member became valid. Moves the member to the valid pile
        If all members are valid, sends a signal
        """
        self.invalid_items.remove(item)
        self.valid_items.add(item)

        if not self.valid and not self.invalid_items:
            self.valid = True
            self.became_valid_signal.send(self)


class ItemUpdater:
    """
    This governs some defined variables

    Variables can be made to be refreshed manually, or on a timer
    Variable getters can time out, which sends out a signal
    Variables can be validated
    On validation or read error, variable refresh can be re-scheduled
    automatically on a timer
    """

    def __init__(self, quit_interval=0.2):
        self.quit_interval = quit_interval

        self.running = True

        self.invalidate_timers = PriorityQueue()
        self.invalidate_queue_event = Event()
        self.timeout_timers = PriorityQueue()
        self.timeout_queue_event = Event()
        self.refresh_queue = Queue()

        self.refresher_thread = Thread(target=self._refresher,
                                       name="polling",
                                       daemon=True)
        self.invalidator_thread = Thread(target=self._process_invalidations,
                                         name="item_invalidator",
                                         daemon=True)
        self.timeout_thread = Thread(target=self._process_timeouts,
                                     name="polling_timeout",
                                     daemon=True)

        self.items = set()

    def start(self):
        """Starts up the governing threads"""
        self.refresher_thread.start()
        self.invalidator_thread.start()
        self.timeout_thread.start()

    def stop(self):
        """Stops the value tracker"""
        self.running = False
        self.invalidate_queue_event.set()
        self.timeout_queue_event.set()

    def wait_stopped(self):
        """waits for the value tracker to quit"""
        self.invalidator_thread.join()
        self.timeout_thread.join()
        self.refresher_thread.join()

    def add_item(self, item: WatchedItem, start_tracking=True):
        """
        Only invalid items can be added for now
        :param item: The item to add to watched ones
        :param start_tracking: Whether to invalidate the item.
            Without this, the item does not gather its value and has to be
            invalidated manually
        """
        if not issubclass(type(item), WatchedItem):
            raise TypeError("Can't track something, that isn't a WatchedItem.")
        self.items.add(item)
        if start_tracking:
            self.invalidate(item)

    def invalidate_group(self, group: WatchedGroup):
        """
        Invalidates every item of the supplied WatchedGroup
        """
        for group_item in group:
            self.invalidate(group_item)

    def invalidate(self, item: WatchedItem):
        """
        Invalidates the item, putting it into the queue for validation
        If the object has a timeout, sets up the timer for it

        Calling repeatedly should not affect anything,
        the first invalidation matters

        If the item already is invalidated but is not scheduled for a refresh,
        it gets scheduled
        """
        self._validate_is_tracked(item)

        with item.lock:
            if item.disabled:
                log.debug("Will not invalidate item %s because it's disabled.",
                          item.name)
                return
            log.debug("Item %s has been invalidated", item.name)
            item.invalidate_at = inf
            if item.valid:
                item.valid = False
                for group in item.in_groups:
                    group.invalid_handler(item)
                item.became_invalid_signal.send(item)

            if not item.scheduled:
                self._enqueue_refresh(item)

    def disable(self, item: WatchedItem):
        """Disables the item polling without changing its interval"""
        self._validate_is_tracked(item)

        with item.lock:
            if item.disabled:
                return
            item.disabled = True
            self.cancel_scheduled_invalidation(item)

    def enable(self, item: WatchedItem):
        """Enables the item polling without changing its interval"""
        self._validate_is_tracked(item)

        with item.lock:
            if not item.disabled:
                return
            item.disabled = False
            self.invalidate(item)

    def set_value(self, item: WatchedItem, value):
        """
        Validates the value and writes it

        Forcefully re-schedules invalidation. This can be used to enable
        polling, when auto reporting stops for example
        """

        self._validate_is_tracked(item)

        with item.lock:
            try:
                if not item.validation_function(value):
                    raise ValueError(f"Invalid value for {item.name}: {value}")
            # pylint: disable=broad-except
            except Exception:
                log.debug("Validation of item %s has failed", item.name)
                item.validation_error_signal.send(item)
                item.val_err_timeout_signal.send(item)

                # If the item is valid, do not schedule a gather, as this
                # probably was a setter from the outside with a bad value
                if not item.valid:
                    self._gather_error_reschedule(item)
            else:
                log.debug("Value of item %s has been determined to be %s",
                          item.name, value)
                self._set_value(item, value)

    def schedule_invalidation(self, item: WatchedItem, interval=None,
                              reschedule=False):
        """
        Schedules an item invalidation at a certain time
        Will not shift already scheduled invalidation unless forced to

        If an already invalid item is scheduled for example after a
        gather/validation error, it is just added to the refresh queue without
        emitting any additional signals
        :param item: The item to schedule invalidation for.
        :param interval: How long in the future should we invalidate?
                         If left empty, the default is used, if that's None
                         an error will be raised
        :param reschedule: If an invalidation is already scheduled,
                           it won't get re-scheduled unless this is True

        """
        self._validate_is_tracked(item)

        with item.lock:
            if item.disabled:
                log.debug("Will not schedule item %s because it is disabled.",
                          item.name)
                return
            if item.invalidate_at != inf and not reschedule:
                log.debug(
                    "Will not schedule an invalidation for item %s because "
                    "another is already scheduled", item.name)
                return

            if interval is None:
                interval = item.interval

            if interval is None:
                raise AttributeError(f"No interval specified for item "
                                     f"{item.name} has no default and none"
                                     f" has been provided!")

            log.debug(
                "Scheduling invalidation of item %s for %ss in "
                "the future", item.name, interval)
            item.invalidate_at = time() + interval
            self.invalidate_timers.put((item.invalidate_at, item))
            self.invalidate_queue_event.set()

    def cancel_scheduled_invalidation(self, item: WatchedItem):
        """
        Cancels the scheduled invalidation. The timer itself cannot
        be cancelled, but the invalidate_at value has to match before
        anything is executed. Changing it to infinity will accomplish
        that nicely
        """
        self._validate_is_tracked(item)

        with item.lock:
            if item.invalidate_at == inf:
                return
            log.debug("Cancelling scheduled invalidation of item %s ",
                      item.name)
            item.invalidate_at = inf

    # -- Private --

    @staticmethod
    def _time_out(item: WatchedItem):
        """
        Times out the item, notifying everyone of the fail
        :return:
        """

        with item.lock:
            log.warning("Timed out when getting item %s", item.name)
            item.times_out_at = inf
            item.timed_out_signal.send(item)
            item.val_err_timeout_signal.send(item)

    def _validate_is_tracked(self, item: WatchedItem):
        if item not in self.items:
            raise ValueError(
                f"Item {item.name} is not tracked by this instance.")

    def _gather(self, item: WatchedItem):
        """
        Refreshes the item value, if the item has a refresh interval,
        sets up the timed invalidation

        If the value gathering throws an error, it re-schedules its refresh
        and notifies of a fail
        """
        if item.valid:
            return

        # Items without gather functions have no point in spinning,
        # something else needs to take care of them
        if item.gather_function is None:
            return

        log.debug("Gathering new value for item %s", item.name)
        try:
            value = item.gather_function()
        # pylint: disable=broad-except
        except SideEffectOnly:
            # Special case for gatherers with just side effects
            # Useful for when the value is autoreported and gather needs
            # to only turn the reporting on

            # If the gatherer sets its own items value, then let's not
            # re-schedule anything
            if not item.valid:
                # Counting on set_item cancelling the re-schedule
                self._gather_error_reschedule(item)

        except Exception:
            with item.lock:
                log.exception("Gather of %s has failed", item.name)
                item.error_refreshing_signal.send(item)
                item.val_err_timeout_signal.send(item)
                self._gather_error_reschedule(item)
        else:
            with item.lock:
                self.set_value(item, value)

    def _gather_error_reschedule(self, item):
        """
        Reschedules the value refresh on gather or validation errors
        Reschedules only if the reschedule interval is set (default = 5s)
        """
        with item.lock:
            if item.on_fail_interval is not None:
                log.debug(
                    "Rescheduling gather of item %s for "
                    "%ss in the future", item.name, item.on_fail_interval)
                self.schedule_invalidation(item, item.on_fail_interval)

    def _set_value(self, item, value):
        """
        Internal, only sets the value without validation
        Should be pre-validate before this gets called
        """
        with item.lock:
            changed = value != item.value
            if changed:
                log.debug("Item %s got a new value! old: %s new: %s",
                          item.name, item.value, value)
            item.value = value
            item.write_function(value)
            was_invalid = not item.valid
            item.valid = True
            item.times_out_at = inf
            if item.interval is not None:
                self.schedule_invalidation(item, reschedule=True)
            if was_invalid:
                for group in item.in_groups:
                    group.valid_handler(item)
                item.became_valid_signal.send(item)
            if changed:
                item.value_changed_signal.send(value)

    def _enqueue_refresh(self, item):
        """
        Forcefully enqueues the item for refresh
        Does not re-schedule the time out. If the item failed to gather for
        example, it gets re-scheduled. But has to time out in the set time
        since it is invalid for more than X seconds
        :param item:
        :return:
        """
        with item.lock:
            if item.timeout is not None and item.times_out_at == inf:
                item.times_out_at = time() + item.timeout
                self.timeout_timers.put((item.times_out_at, item))

            item.scheduled = True
            self.refresh_queue.put(item)

    def _refresher(self):
        """
        Processes all values queued up for refreshing
        """
        prctl_name()
        while self.running:
            try:
                item = self.refresh_queue.get(timeout=self.quit_interval)
            except Empty:
                pass
            else:
                with item.lock:
                    item.scheduled = False
                self._gather(item)

    def _process_invalidations(self):
        """
        Processes the invalidation queue.
        If a timer is checked and does not match with the set timer on an
        item, it is discarded, so only valid timers call their callbacks
        :return:
        """
        prctl_name()
        while self.running:
            try:
                invalidate_at, item = self.invalidate_timers.get(
                    timeout=self.quit_interval)
            except Empty:
                pass
            else:
                # Check if the timer is valid
                if invalidate_at != item.invalidate_at:
                    continue

                current_time = time()
                if invalidate_at > current_time:
                    self.invalidate_timers.put((invalidate_at, item))
                    self.invalidate_queue_event.wait(invalidate_at -
                                                     current_time)
                    self.invalidate_queue_event.clear()
                else:
                    self.invalidate(item)

    def _process_timeouts(self):
        """
        Same as invalidators, except its timeouts
        """
        prctl_name()
        while self.running:
            try:
                times_out_at, item = self.timeout_timers.get(
                    timeout=self.quit_interval)
            except Empty:
                pass
            else:
                # Check if the timer is valid
                if times_out_at != item.times_out_at:
                    continue

                current_time = time()
                if times_out_at > current_time:
                    self.timeout_timers.put((times_out_at, item))
                    self.timeout_queue_event.wait(times_out_at - current_time)
                    self.timeout_queue_event.clear()
                else:
                    self._time_out(item)
