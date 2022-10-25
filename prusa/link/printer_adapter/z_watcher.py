"""Implements the Z step detection component"""
import logging
from collections import deque
from typing import Deque

from blinker import Signal  # type: ignore

from prusa.connect.printer.const import State, TriggerScheme
from .model import Model

HISTORY_LEN = 6  # How many values to get for layer mode
THROW_OUT = 3  # How many highest to throw out (Z hop)
FLAT_THRESHOLD = HISTORY_LEN - THROW_OUT  # equal values to use layer mode

VASE_MODE_STEP = 0.2  # When doing vase mode, trigger in steps of (n)mm
TRIGGER_EACH = 1  # Trigger each n layers - 1 means every layer
VASE_THRESHOLD = 4  # consecutively increasing readings for vase mode
BIG_VASE_JUMP_THRESHOLD = 0.02  # Any bigger jump disqualifies big vase mode

# You should not print a millimeter of a vase in a few seconds
# PrusaLink does not support Voron printers anyway
ASCENSION_THRESHOLD = 1

Z_HOP_THRESHOLD = 1  # If no triggering is working, trigger during a z-hop

log = logging.getLogger(__name__)


class ZWatcher:
    """A component that watches the Z values, determining when to trigger
    a camera"""

    def __init__(self, model: Model):
        self.trigger_signal = Signal()  # kwargs: trigger_scheme: TriggerScheme
        self._model = model
        self._z_history: Deque[float] = deque(maxlen=HISTORY_LEN)

        # Layer part
        self._last_layer_z = -1  # Start so even zero is a new layer

        # Z hop saver
        self._last_z_hop_layer = -1  # The same logic as above

        # Vase mode part
        # Init last vase layer triggered so even zero is a new layer
        self._last_vase_step = -1 * VASE_MODE_STEP
        self._rising = True  # Start ready for rising sequence
        self._n_rising = 0
        self._rising_slowly = True
        self._n_rising_slowly = 0

        # Trigger counter
        self._trigger_count = 0

    def z_changed(self, new_z: float):
        """A Z change handler. Triggers on a new "layer" - Z height step"""
        if self._model.state_manager.base_state == State.BUSY:
            # Printer is either heating up or levelling Z, not interested.
            return
        if self._model.state_manager.printing_state != State.PRINTING:
            # Printer is either paused or not printing at all
            not_printing = self._model.state_manager.printing_state is None
            if not_printing and self._z_history:
                log.debug("Final trigger Z = %s", new_z)
                self._trigger()
                self._reset()
            return

        self._z_history.appendleft(new_z)

        vase_z = self._vase_mode_z()
        layer_z = self._layer_mode_z()
        z_hop_z = self._z_hops()

        if layer_z is not None:
            difference = abs(layer_z - self._last_layer_z)
            # Have we moved at all? If yes, by how much?
            # Do not register slow vases as layers
            if difference > BIG_VASE_JUMP_THRESHOLD:
                self._last_layer_z = layer_z
                log.debug("Trigger in layer mode at Z = %s", new_z)
                self._trigger()
                return

        if vase_z is not None:
            # Block layer mode if we believe we're in vase mode,
            # by not allowing it to cross the vase mode threshold
            self._last_layer_z = vase_z

            # Avoid float weirdness, use int
            difference = abs(self._last_vase_step - vase_z)
            int_difference = int(difference*100)
            # Have we moved by enough in the vase to warrant a new trigger
            if int_difference >= int(VASE_MODE_STEP*100):
                # Save only full steps
                # do it in ints to avoid float weirdness
                int_z = int(vase_z*100)
                int_step = max(1, int(VASE_MODE_STEP*100))
                int_last_step = int_z - (int_z % int_step)

                self._last_vase_step = int_last_step / 100
                self._trigger()
                log.debug("Trigger in vase mode at Z = %s", new_z)
                return

        if z_hop_z is not None:
            any_last_detection = max(self._last_layer_z,
                                     self._last_vase_step,
                                     self._last_z_hop_layer)
            difference = abs(z_hop_z - any_last_detection)
            # Have we moved so much, that the Z-hop detection should kick in?
            if difference > Z_HOP_THRESHOLD:
                self._last_z_hop_layer = z_hop_z
                log.debug("Trigger in Z hop mode at Z = %s", new_z)
                self._trigger()
                return

        log.debug("No layer nor vase mode matches Z behavior")

    def _reset(self):
        """Reset the values for a new job, comments to this are in __init__"""
        self._z_history.clear()

        self._last_vase_step = self._last_layer_z = -1
        self._last_z_hop_layer = -1.
        self._rising = self._rising_slowly = True
        self._n_rising = self._n_rising_slowly = 0

    def _vase_mode_z(self):
        """Output a Z value if the recorded Z values indicate a vase mode"""
        # Need at least two values, the first value is still a sequence of one
        if len(self._z_history) == 1:
            self._n_rising = 1
            self._n_rising_slowly = 1
            return None

        # If we're going up consistently during a print, it's probably a vase
        new_rising = self._z_history[0] > self._z_history[1]
        if new_rising != self._rising:
            self._n_rising = 1
        elif self._rising:
            self._n_rising += 1
        self._rising = new_rising

        # If the vase is large, we probably spend more than a second on
        # a resolution step, but we cannot pass constant Z as a vase layer
        # either. So filter out any large jumps like Z hops and layers
        max_jump_amount = 0
        # TODO: try islice
        z_history = list(self._z_history)
        for z_value, z_next in zip(z_history[:-1], z_history[1:]):
            jump_amount = abs(z_value - z_next)
            max_jump_amount = max(max_jump_amount, jump_amount)
        new_rising_slowly = (self._z_history[0] >= self._z_history[1]
                             and max_jump_amount <= BIG_VASE_JUMP_THRESHOLD)
        if new_rising_slowly != self._rising_slowly:
            self._n_rising_slowly = 1
        elif self._rising_slowly:
            self._n_rising_slowly += 1
        self._rising_slowly = new_rising_slowly

        # Filter out fast Z travel up
        ascension_rate = self._z_history[0] - self._z_history[-1]
        rising_valid = (self._n_rising >= VASE_THRESHOLD
                        and ascension_rate < ASCENSION_THRESHOLD)

        # Submit only occurrences with a change in Z height (no constant Z)
        rising_slowly_valid = (self._n_rising_slowly >= VASE_THRESHOLD
                               and ascension_rate > 0)
        if rising_valid or rising_slowly_valid:
            average = sum(self._z_history) / len(self._z_history)
            return average
        return None

    def _layer_mode_z(self):
        """Output a Z value if the recorded Z values indicate a layer mode"""
        # Skip evaluating with incomplete data
        if len(self._z_history) != self._z_history.maxlen:
            return None

        sorted_history = sorted(self._z_history)
        filtered_history = sorted_history[:FLAT_THRESHOLD]

        # It's sorted, if the first and last items are equal, all  are equal
        if filtered_history[0] == filtered_history[-1]:
            return filtered_history[0]
        return None

    def _z_hops(self):
        """Detect if we're z-hopping an awful lot to take at least some
        pictures if that happens"""
        # Skip evaluating with incomplete data
        if len(self._z_history) != self._z_history.maxlen:
            return None

        sorted_history = sorted(self._z_history)
        # Throws out the two lower values
        filtered_history = sorted_history[FLAT_THRESHOLD:]

        # Still sorted, the same flatness logic as layer mode
        if filtered_history[0] == filtered_history[-1]:
            return filtered_history[0]
        return None

    def _trigger(self):
        """Calls the appropriate trigger signals each layer
        and each five layers"""
        self.trigger_signal.send(TriggerScheme.EACH_LAYER)
        self._trigger_count += 1
        if self._trigger_count >= 5:
            self.trigger_signal.send(TriggerScheme.FIFTH_LAYER)
            self._trigger_count = 0
