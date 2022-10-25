"""Implements test for the Z_watcher component"""
from unittest import mock

import pytest

from prusa.connect.printer.const import State, TriggerScheme
from prusa.link.printer_adapter.z_watcher import ZWatcher, VASE_THRESHOLD, \
    HISTORY_LEN, THROW_OUT, FLAT_THRESHOLD

# pylint: disable=protected-access

Mock = mock.Mock


class MockStateManagerData:
    """Mock of relevant StateManager data in the model"""

    def __init__(self):
        self.base_state = State.IDLE
        self.printing_state = State.PRINTING


class MockModel:
    """Mock of the model, with state manager data as the only attribute"""
    def __init__(self):
        self.state_manager = MockStateManagerData()


@pytest.fixture(name="z_watcher")
def fixture_z_watcher():
    """Gets new instance of Z watcher"""
    return ZWatcher(MockModel())


def evaluate_z(z_watcher, values):
    """Passes a list of values to Z watcher as z changed events"""
    for value in values:
        # Sender attribute is always None,
        z_watcher.z_changed(new_z=value)


def test_layer(z_watcher):
    """Test that a constant measurement of one layer will trigger"""
    trigger_mock = Mock(spec={})
    z_watcher.trigger_signal.connect(trigger_mock)

    values = [0.2] * HISTORY_LEN
    evaluate_z(z_watcher, values)

    trigger_mock.assert_called_once()
    assert z_watcher._last_layer_z == 0.2


def test_layer_mode_z_hop(z_watcher):
    """Test that the layer with a few z-hop values is going to trigger"""
    trigger_mock = Mock(spec={})
    z_watcher.trigger_signal.connect(trigger_mock)

    values = [0.2] * 4 + [0.4] * 2
    evaluate_z(z_watcher, values)

    trigger_mock.assert_called_once()
    assert z_watcher._last_layer_z == 0.2


def test_layer_mode_long_running_layer(z_watcher):
    """Test that a long running layer triggers only once"""
    trigger_mock = Mock(spec={})
    z_watcher.trigger_signal.connect(trigger_mock)

    values = [0.2] * 50
    evaluate_z(z_watcher, values)

    trigger_mock.assert_called_once()


def test_layer_mode_too_much_z_hop(z_watcher):
    """There's more Z hop values than the real layer, yet we still don't
    trigger during Z hop thinking it's a new layer"""
    trigger_mock = Mock(spec={})
    z_watcher.trigger_signal.connect(trigger_mock)

    values = [0.2] * (FLAT_THRESHOLD-1) + [0.4] * (THROW_OUT+1)
    evaluate_z(z_watcher, values)

    trigger_mock.assert_not_called()


def test_layer_noisy(z_watcher):
    """Test values being all over the place not triggering"""
    trigger_mock = Mock(spec={})
    z_watcher.trigger_signal.connect(trigger_mock)

    values = [1, 0.2, 0.4, 0.2, 0.3, 0.25, 0.1]
    evaluate_z(z_watcher, values)

    trigger_mock.assert_not_called()


def test_second_layer(z_watcher):
    """Test two perfect layers"""
    trigger_mock = Mock(spec={})
    z_watcher.trigger_signal.connect(trigger_mock)

    values = [0.2] * HISTORY_LEN + [0.4] * HISTORY_LEN
    evaluate_z(z_watcher, values)

    trigger_mock.assert_has_calls(calls=[mock.call(TriggerScheme.EACH_LAYER),
                                         mock.call(TriggerScheme.EACH_LAYER)])
    assert z_watcher._last_layer_z == 0.4


def test_second_layer_with_z_hop(z_watcher):
    """Test two layers with some bumpiness"""
    trigger_mock = Mock(spec={})
    z_watcher.trigger_signal.connect(trigger_mock)

    values = ([0.2] * FLAT_THRESHOLD
              + [0.4] * THROW_OUT
              + [0.4] * FLAT_THRESHOLD
              + [0.6] * THROW_OUT)
    evaluate_z(z_watcher, values)

    trigger_mock.assert_has_calls(calls=[mock.call(TriggerScheme.EACH_LAYER),
                                         mock.call(TriggerScheme.EACH_LAYER)])
    assert z_watcher._last_layer_z == 0.4


def test_print_start(z_watcher):
    """Don't trigger during a busy state"""
    z_watcher._model.state_manager.base_state = State.BUSY
    trigger_mock = Mock(spec={})
    z_watcher.trigger_signal.connect(trigger_mock)

    values = [0.2]*20
    evaluate_z(z_watcher, values)

    trigger_mock.assert_not_called()


def test_pause(z_watcher):
    """Don't trigger during pauses"""
    z_watcher._model.state_manager.printing_state = State.PAUSED
    trigger_mock = Mock(spec={})
    z_watcher.trigger_signal.connect(trigger_mock)

    values = [20.2]*20
    evaluate_z(z_watcher, values)

    trigger_mock.assert_not_called()


def test_vase(z_watcher):
    """Simulate a layer of vase, ensure it triggers"""
    trigger_mock = Mock(spec={})
    z_watcher.trigger_signal.connect(trigger_mock)

    values = map(lambda i: 1+i/10, range(VASE_THRESHOLD))
    evaluate_z(z_watcher, values)

    trigger_mock.assert_called_once()


def test_vase_layers(z_watcher):
    """Go up two steps like a vase would"""
    trigger_mock = Mock(spec={})
    z_watcher.trigger_signal.connect(trigger_mock)

    values = map(lambda i: i * 0.1, range(6))
    evaluate_z(z_watcher, values)

    trigger_mock.assert_has_calls(calls=[mock.call(TriggerScheme.EACH_LAYER),
                                         mock.call(TriggerScheme.EACH_LAYER)])


def test_vase_layers_descending(z_watcher):
    """A backwards vase?"""
    trigger_mock = Mock(spec={})
    z_watcher.trigger_signal.connect(trigger_mock)

    values = map(lambda i: 1+i/100, range(20, 0, -1))
    evaluate_z(z_watcher, values)

    trigger_mock.assert_not_called()


def test_vase_slow(z_watcher):
    """A vase that takes too long to go up by a measurable amount"""
    trigger_mock = Mock(spec={})
    z_watcher._last_layer_z = 0
    z_watcher.trigger_signal.connect(trigger_mock)

    values = [0.2]*3 + [0.21]*3
    evaluate_z(z_watcher, values)

    trigger_mock.assert_called_once()


def test_vase_slow_to_fast(z_watcher):
    """Switch from a really slow vase to a really fast vase,
    but under the threshold"""
    trigger_mock = Mock(spec={})
    z_watcher.trigger_signal.connect(trigger_mock)
    values = [0.2]*3 + [0.21]*3
    values.extend(map(lambda i: 0.3 + i / 5, range(VASE_THRESHOLD)))
    evaluate_z(z_watcher, values)

    trigger_mock.assert_has_calls(calls=[mock.call(TriggerScheme.EACH_LAYER),
                                         mock.call(TriggerScheme.EACH_LAYER)])


def test_vase_too_fast(z_watcher):
    """Go up faster than the ascension threshold"""
    trigger_mock = Mock(spec={})
    z_watcher.trigger_signal.connect(trigger_mock)

    # Going up by one is fast enough for ascension threshold of one
    values = range(20)
    evaluate_z(z_watcher, values)

    trigger_mock.assert_not_called()


def test_slow_vase_layer(z_watcher):
    """Slow vases should not trigger as both a vase and a layer"""
    trigger_mock = Mock(spec={})
    z_watcher.trigger_signal.connect(trigger_mock)

    # First layer triggers layer and vase - layer wins
    # Second should be correctly recognized as a slow vase
    values = []
    for i in range(25):
        values.extend([i/100] * HISTORY_LEN)
    evaluate_z(z_watcher, values)

    trigger_mock.assert_has_calls(calls=[mock.call(TriggerScheme.EACH_LAYER),
                                         mock.call(TriggerScheme.EACH_LAYER)])
    assert z_watcher._last_vase_step == 0.2
