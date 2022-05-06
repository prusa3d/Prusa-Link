"""
The frequency at which we send telemetry is being determined by quite a
lot of factore. This module takes care of monitoring, how often to
send telemetry and what actual telemetry to send
"""
import logging
from threading import Event, Lock, Thread
from typing import Any

from time import time

from prusa.connect.printer import Printer
from prusa.connect.printer.const import State
from ..const import TELEMETRY_IDLE_INTERVAL, \
    TELEMETRY_PRINTING_INTERVAL, TELEMETRY_SLEEPING_INTERVAL, \
    JITTER_THRESHOLD, PRINTING_STATES, TELEMETRY_SLEEP_AFTER
from .model import Model
from .structures.mc_singleton import MCSingleton
from .structures.model_classes import Telemetry
from .updatable import prctl_name
from ..util import loop_until

log = logging.getLogger(__name__)

# beyond this many things waiting to get sent by the SDK,
# we'll stop sending telemetry
QUEUE_LENGTH_LIMIT = 4

JITTERY_TEMPERATURES = {"temp_nozzle", "temp_bed"}
ACTIVATING_CHANGES = {"target_nozzle", "target_bed", "axis_x", "axis_y",
                      "axis_z", "target_fan_print", "speed"}
NOT_PRINTING_IGNORED = {"time_printing", "time_estimated",
                        "time_remaining", "progress"}
PRINTING_IGNORED = {"axis_x", "axis_y"}


class TelemetryPasser(metaclass=MCSingleton):
    """Tasked with passing the correct telemetry with the correct timing"""

    def __init__(self, model: Model, printer: Printer):
        self.model: Model = model
        self.printer: Printer = printer

        self.lock = Lock()
        self.notify_evt: Event = Event()
        self.running = True
        self.sleeping = False
        self.telemetry_interval = TELEMETRY_SLEEPING_INTERVAL
        self.thread = Thread(target=self._keep_updating,
                             name="telemetry_passer")
        self.latest_changed = False

        self._last_sent: dict[str, Any] = {}
        self._to_send: dict[str, Any] = {}
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
        """keeps spinning until supposed to stop"""
        prctl_name()
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

    def _get_and_reset_telemetry(self):
        """
        Telemetry is special, to report only the most recent values,
        each read it gets reset

        The last telemetry is not being reset, so the recent values can be
        read for web etc.
        """
        with self.lock:

            # Update what we sent last time
            self._last_sent.update(self._to_send)

            to_return = self._to_send
            self._to_send = {}
            return to_return

    def pass_telemetry(self):
        """
        Passes the telemetry to the SDK
        and pushes the newer telemetry into the sent telemetry
        """
        if self.printer.queue.qsize() >= QUEUE_LENGTH_LIMIT:
            log.debug("SDK queue looks stuck, not passing telemetry to it")
            return

        with self.lock:
            # Update what we sent last time
            self._last_sent.update(self._to_send)

            telemetry = self._to_send
            self._to_send = {}

        self.printer.telemetry(**telemetry)

    def _is_appropriate_for_state(self, key):
        """
        Return True, if the telemetry key is appropriate to send
        in the state we're currently in
        """
        state = self.model.state_manager.current_state
        if state not in PRINTING_STATES and key in NOT_PRINTING_IGNORED:
            return False
        if state == State.PRINTING and key in PRINTING_IGNORED:
            return False
        return True

    def set_telemetry(self, new_telemetry: Telemetry):
        """
        Filters jitter, state inappropriate or unchanged data
        Updates the telemetries with new data
        """
        with self.lock:
            new_telemetry_dict = new_telemetry.dict(exclude_none=True)
            for key, value in new_telemetry_dict.items():
                if value is None:
                    continue

                if not self._is_appropriate_for_state(key):
                    # Internally we need to check against none
                    setattr(self.model.latest_telemetry, key, None)
                    continue

                setattr(self.model.latest_telemetry, key, value)

                to_update = False
                if key not in self._last_sent:
                    to_update = True
                elif key in JITTERY_TEMPERATURES:
                    old = self._last_sent[key]
                    new = value
                    assert new is not None
                    if old is None:
                        to_update = True
                    else:
                        assert isinstance(new, float)
                        assert isinstance(old, float)
                        if abs(old - new) > JITTER_THRESHOLD:
                            to_update = True
                elif value != self._last_sent[key]:
                    to_update = True

                # Wake up from sleep, when specific values change
                if to_update and key in ACTIVATING_CHANGES:
                    state = self.model.state_manager.current_state
                    if state not in PRINTING_STATES or key == "speed":
                        self.activity_observed()

                if to_update:
                    self._to_send[key] = value

    def activity_observed(self):
        """Call if any activity that constitutes waking up from sleep occurs"""
        self.last_activity_at = time()
        if self.sleeping:
            log.debug("Telemetry passer woke up.")
            self.notify_evt.set()

    def wipe_telemetry(self):
        """
        Resets the telemetry, so the values don't lie
        Paired with polling value invalidation, this will get and send
        fresh telemetry values
        """
        with self.lock:
            self.model.latest_telemetry = Telemetry()
            self._last_sent = {}
            self._to_send = {}

    def resend_latest_telemetry(self):
        """
        Move the latest telemetry, so it gets sent next time.
        Great for reconnections and other telemetry forgetting situations
        """
        with self.lock:
            self._to_send = self.model.latest_telemetry.dict(exclude_none=True)
        self.pass_telemetry()
