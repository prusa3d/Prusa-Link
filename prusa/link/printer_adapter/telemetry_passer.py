"""
The frequency at which we send telemetry is being determined by quite a
lot of factore. This module takes care of monitoring, how often to
send telemetry and what actual telemetry to send
"""
import logging
from copy import deepcopy
from enum import Enum
from threading import Event, RLock, Thread
from time import time
from typing import Any

from pydantic import BaseModel
from pydantic.utils import deep_update

from prusa.connect.printer import Printer
from prusa.connect.printer.const import State

from ..config import Settings
from ..const import (
    JITTER_THRESHOLD,
    MMU_SLOTS,
    PRINTING_STATES,
    TELEMETRY_IDLE_INTERVAL,
    TELEMETRY_PRINTING_INTERVAL,
    TELEMETRY_REFRESH_INTERVAL,
    TELEMETRY_SLEEP_AFTER,
    TELEMETRY_SLEEPING_INTERVAL,
)
from ..util import loop_until, walk_dict
from .model import Model
from .structures.mc_singleton import MCSingleton
from .structures.model_classes import Telemetry

log = logging.getLogger(__name__)

# beyond this many things waiting to get sent by the SDK,
# we'll stop sending telemetry
QUEUE_LENGTH_LIMIT = 4


class Modifier(Enum):
    """The modifiers for telemetry"""
    FILTER_IDLE = "FILTER_IDLE"  # Filtered when idle
    FILTER_PRINTING = "FILTER_PRINTING"  # Filtered when printing
    FILTER_MMU_OFF = "FILTER_MMU_OFF"  # Filtered when MMU is disconnected
    JITTER_TEMP = "JITTER_TEMP"  # Temperature jitter filtr preset
    ACTIVATE_IDLE = "ACTIVATE_IDLE"  # Wakes up fast telemetry when idle
    ACTIVATE_PRINTING = "ACTIVATE_PRINTING"  # Same but when printing


# Important - all filter paths are in the dict format
# model is different in structure, the paths are mapped using a mapping below
MODIFIERS: dict[tuple[str, ...], set[Modifier]] = {
    ("target_nozzle",): {Modifier.ACTIVATE_IDLE},
    ("target_bed",): {Modifier.ACTIVATE_IDLE},
    ("axis_x",): {Modifier.ACTIVATE_IDLE, Modifier.FILTER_PRINTING},
    ("axis_y",): {Modifier.ACTIVATE_IDLE, Modifier.FILTER_PRINTING},
    ("axis_z",): {Modifier.ACTIVATE_IDLE},
    ("target_fan_print",): {Modifier.ACTIVATE_IDLE},
    ("speed",): {Modifier.ACTIVATE_IDLE, Modifier.ACTIVATE_PRINTING},
    ("temp_nozzle",): {Modifier.JITTER_TEMP},
    ("temp_bed",): {Modifier.JITTER_TEMP},
    ("time_printing",): {Modifier.FILTER_IDLE},
    ("time_remaining",): {Modifier.FILTER_IDLE},
    ("progress",): {Modifier.FILTER_IDLE},
    ("inaccurate_estimates",): {Modifier.FILTER_IDLE},
    ("slot",): {Modifier.FILTER_MMU_OFF},
    # ("a", "b") - applies to a key b in a subtree a
    # ("a") - applies to "a", so if it's filtered, its children are too
}

MAPPING = {  # type: ignore
    "slot": {},
}

for i_ in range(1, MMU_SLOTS+1):
    # Map slots from orm to the dict representation
    MAPPING["slot"][str(i_)] = ("slot", "slots", str(i_))
    # Add jitter temps to every slot temp value
    MODIFIERS[("slot", str(i_), "temp")] = {Modifier.JITTER_TEMP}


class TelemetryPasser(metaclass=MCSingleton):
    """Tasked with passing the correct telemetry with the correct timing"""

    def __init__(self, model: Model, printer: Printer):
        self.model: Model = model
        self.printer: Printer = printer

        self.lock = RLock()
        self.notify_evt: Event = Event()
        self.running = True
        self.sleeping = False
        self.telemetry_interval = TELEMETRY_SLEEPING_INTERVAL
        self.thread = Thread(target=self._keep_updating,
                             name="telemetry_passer")
        self.full_refresh_at = 0

        self._active_filters: set[Any] = set()

        self._last_sent: dict[str, Any] = {}
        self._to_send: dict[str, Any] = {}
        self._latest_full = Telemetry()
        self.model.latest_telemetry = Telemetry()

        self.last_activity_at = time()

    def start(self):
        """Starts the passer"""
        self.thread.start()

    def stop(self):
        """Stops the passer"""
        self.running = False
        self.notify_evt.set()

    def wait_stopped(self):
        """Wait for the passer to stop"""
        self.thread.join()

    def _keep_updating(self):
        """keeps spinning until supposed to stop

        The loop here facilitates the instant wakeup of the telemetry passer
        after activity is observed"""
        while self.running:
            self.notify_evt.clear()
            loop_until(loop_evt=self.notify_evt,
                       run_every_sec=lambda: self.telemetry_interval,
                       to_run=self._update)

    def _update(self):
        """Updates how fast to send and sends the telemetry"""
        self.sleeping = time() - self.last_activity_at > TELEMETRY_SLEEP_AFTER
        if self.sleeping:
            log.debug("Telemetry passer is sleeping... zzz")
            self.telemetry_interval = TELEMETRY_SLEEPING_INTERVAL
        else:
            state = self.model.state_manager.current_state
            if state in PRINTING_STATES:
                self.telemetry_interval = TELEMETRY_PRINTING_INTERVAL
            else:
                self.telemetry_interval = TELEMETRY_IDLE_INTERVAL

        self.pass_telemetry()

    def pass_telemetry(self):
        """Passes the telemetry to the SDK
        and pushes the newer telemetry into the sent telemetry"""
        if not Settings.instance.use_connect():
            log.debug("Connect isn't configured -> no telemetry")
            return

        if not self.printer.is_initialised():
            log.debug("Printer isn't initialised -> no telemetry")
            return

        if Settings.instance.is_wizard_needed():
            log.debug("Wizard has not been completed yet -> no telemetry")
            return

        if self.printer.queue.qsize() >= QUEUE_LENGTH_LIMIT:
            log.debug("SDK queue looks stuck -> no telemetry")
            return

        with self.lock:
            # Update what we sent last time

            self._last_sent = deep_update(self._last_sent, self._to_send)

            telemetry = self._to_send
            self._to_send = {}

        self.printer.telemetry(**telemetry)

    def _get_filtered_paths(self):
        state = self.model.state_manager.current_state
        if state not in PRINTING_STATES:
            looking_for = Modifier.FILTER_IDLE
        elif state == State.PRINTING:
            looking_for = Modifier.FILTER_PRINTING
        else:
            return set()

        filtered = set()
        for key_path, filters in MODIFIERS.items():
            if looking_for in filters:
                filtered.add(key_path)

        return filtered

    def _get_modifiers(self, key_path):
        modifiers = set()
        for i in range(len(key_path)):
            modifiers.update(MODIFIERS.get(key_path[:i+1], set()))
        return modifiers

    def set_telemetry(self, new_telemetry: Telemetry):
        """Filters jitter, state inappropriate or unchanged data
        Updates the telemetries with new data"""
        with self.lock:
            new_telemetry_dict = new_telemetry.dict(exclude_none=True)
            for key_path, value in walk_dict(new_telemetry_dict):
                key_path = tuple(key_path)

                if value is None or value == {}:
                    continue  # ignore nones and empty dicts

                modifiers = self._get_modifiers(key_path)

                self._update_by_path(
                    self._latest_full, new_telemetry, key_path)

                if modifiers & self._active_filters:
                    # Internally we need to check against none
                    self._reset_by_path(
                        self.model.latest_telemetry, key_path)
                    continue

                self._update_by_path(
                    self.model.latest_telemetry, new_telemetry, key_path)

                to_update = False
                if self._get_by_path(self._last_sent, key_path) is None:
                    to_update = True
                elif Modifier.JITTER_TEMP in modifiers:
                    old = self._get_by_path(self._last_sent, key_path)
                    new = value
                    assert new is not None
                    if old is None:
                        to_update = True
                    else:
                        assert isinstance(new, float)
                        assert isinstance(old, float)
                        if abs(old - new) > JITTER_THRESHOLD:
                            to_update = True
                elif value != self._get_by_path(self._last_sent, key_path):
                    to_update = True

                # Wake up from sleep, when specific values change
                if to_update:
                    if self._should_wake_up(modifiers):
                        self.activity_observed()
                    self._update_by_path(
                        self._to_send, new_telemetry_dict, key_path)

        self._resend_telemetry_on_timer()

    def _should_wake_up(self, modifiers):
        """Returns true if the telemetry passer should wake up from sleep
        based on the current state and the modifiers present"""
        state = self.model.state_manager.current_state
        if state in PRINTING_STATES:
            if Modifier.ACTIVATE_PRINTING not in modifiers:
                return False
            if Modifier.FILTER_PRINTING in modifiers:
                return True
        return Modifier.ACTIVATE_IDLE in modifiers

    def reset_value(self, key_path):
        """Resets the value for filament_change_in and nothing else"""
        with self.lock:
            self._reset_by_path(self._latest_full, key_path)
            self._reset_by_path(self.model.latest_telemetry, key_path)

    def _set_multi(self, structure, key, value):
        """Sets a value from a dictionary or a model"""
        if isinstance(structure, dict):
            structure[key] = value
        elif issubclass(type(structure), BaseModel):
            setattr(structure, key, value)
        else:
            raise TypeError("Unsupported type for traversing")

    def _get_multi(self, structure, key):
        """Gets a value from a dictionary or a model"""
        if isinstance(structure, dict):
            return structure.get(key)
        if issubclass(type(structure), BaseModel):
            return getattr(structure, key)
        raise TypeError("Unsupported type for traversing")

    def _get_correct_path(self, structure, key_path):
        """Gets the correct path depending on the structure type"""
        if isinstance(structure, dict):
            return key_path
        if issubclass(type(structure), BaseModel):
            return self._path_to_model(key_path)
        raise TypeError("Unsupported type for traversing")

    def _update_by_path(self, target, source, key_path,
                        set_none=False):
        """Sets a value in the model,
        allow setting none, or pushing more data
        key path is auto mapped, provide the dict equivalent one

        Some assumptions not to be broken as this is fragile AF
        Always supply the full path, do not let it end on a sub dict
        or sub model, full paths only
        Supply only models or dicts
        """
        if not isinstance(target, type(source)):
            raise TypeError("Source and target must be of the same type")
        model_path = self._get_correct_path(target, key_path)

        for key in model_path[:-1]:
            if not isinstance(target, type(source)):
                raise TypeError("Source and target differ in structure")
            next_source = self._get_multi(source, key)
            next_target = self._get_multi(target, key)
            if next_source is None:
                # Source has less depth than target
                if set_none:
                    self._set_multi(target, key, None)
                return
            if next_target is None:
                self._set_multi(target, key, deepcopy(next_source))
                return  # We have set a subtree, we're done
            source = next_source
            target = next_target
        value = self._get_multi(source, model_path[-1])
        if value is None and not set_none:
            return  # We don't want to set None, only add more data
        self._set_multi(target, model_path[-1], value)

    def _reset_by_path(self, target, key_path):
        """Resets a value in the model, does so only for the node at the end
        of the supplied path"""
        model_path = self._get_correct_path(target, key_path)

        for key in model_path[:-1]:
            target = self._get_multi(target, key)
            if target is None:
                return
        self._set_multi(target, model_path[-1], None)

    def _get_by_path(self, source, key_path):
        """Gets a value from model or dict"""
        model_path = self._get_correct_path(source, key_path)

        for key in model_path:
            source = self._get_multi(source, key)
            if source is None:
                return None
        return source

    def _path_to_model(self, key_path) -> tuple[Any, ...]:
        """As the ORM is now different from the dict structure,
        this maps the key path to the model"""
        sub_mapping = MAPPING
        iterable_path = iter(key_path)
        for key in iterable_path:
            result = sub_mapping.get(key)
            if result is None:
                return key_path
            if isinstance(result, tuple):
                break
            sub_mapping = result
        else:  # no break or return encountered
            raise ValueError("Mapping seems to be invalid")

        return result + tuple(iterable_path)

    def _resend_telemetry_on_timer(self):
        """If sufficient time elapsed, mark all telemetry values to be sent"""
        if time() - self.full_refresh_at > TELEMETRY_REFRESH_INTERVAL:
            self.full_refresh_at = time()
            self.resend_latest_telemetry()

    def state_changed(self):
        """When the state changes, update what keys do we filter.
        Call the setters on any keys for which the filtered status
        changes, to update them"""
        with self.lock:
            # Update the active filters
            state = self.model.state_manager.current_state
            if state not in PRINTING_STATES:
                self._active_filters.add(Modifier.FILTER_IDLE)
            elif Modifier.FILTER_IDLE in self._active_filters:
                self._active_filters.remove(Modifier.FILTER_IDLE)

            if state == State.PRINTING:
                self._active_filters.add(Modifier.FILTER_PRINTING)
            elif Modifier.FILTER_PRINTING in self._active_filters:
                self._active_filters.remove(Modifier.FILTER_PRINTING)

            if not self.printer.mmu_enabled:
                self._active_filters.add(Modifier.FILTER_MMU_OFF)
            elif Modifier.FILTER_MMU_OFF in self._active_filters:
                self._active_filters.remove(Modifier.FILTER_MMU_OFF)

            # Update the telemetry to reflect new filters
            self.set_telemetry(self._latest_full)

    def activity_observed(self):
        """Call if any activity that constitutes waking up from sleep occurs"""
        self.last_activity_at = time()
        if self.sleeping:
            log.debug("Telemetry passer woke up.")
            self.notify_evt.set()

    def wipe_telemetry(self):
        """Resets the telemetry, so the values don't lie
        Paired with polling value invalidation, this will get and send
        fresh telemetry values"""
        with self.lock:
            self.model.latest_telemetry = Telemetry()
            self._last_sent = {}
            self._to_send = {}

    def resend_latest_telemetry(self):
        """Move the latest telemetry, so it gets sent next time.
        Great for reconnections and other telemetry forgetting situations"""
        with self.lock:
            self._to_send = self.model.latest_telemetry.dict(exclude_none=True)
        self.pass_telemetry()
