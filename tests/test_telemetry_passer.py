"""Tests of the telemetry passer, without its thread"""
from unittest.mock import MagicMock, patch

import pytest
from prusa.connect.printer.const import State

from prusa.link.config import Settings  # type:ignore
from prusa.link.const import (  # type:ignore
    JITTER_THRESHOLD,
    TELEMETRY_IDLE_INTERVAL,
    TELEMETRY_PRINTING_INTERVAL,
    TELEMETRY_REFRESH_INTERVAL,
    TELEMETRY_SLEEP_AFTER,
    TELEMETRY_SLEEPING_INTERVAL,
)
from prusa.link.printer_adapter import telemetry_passer  # type:ignore
from prusa.link.printer_adapter.structures.model_classes import (  # type:ignore  # noqa: E501
    IndividualSlot,
    Slot,
    Telemetry,
)
from prusa.link.printer_adapter.telemetry_passer import (  # type:ignore
    Modifier,
    TelemetryPasser,
)

# pylint: disable=redefined-outer-name,protected-access,unused-argument


@pytest.fixture
def settings(monkeypatch):
    """Settings which allow sending the telemetry"""
    settings_ = MagicMock()
    settings_.use_connect.return_value = True
    settings_.is_wizard_needed.return_value = False
    monkeypatch.setattr(Settings, "instance", settings_, raising=False)
    return settings_


@pytest.fixture
def printer():
    """The SDK printer, initialised and with an empty queue"""
    printer_ = MagicMock()
    printer_.is_initialised.return_value = True
    printer_.queue.qsize.return_value = 0
    printer_.mmu_enabled = True
    return printer_


@pytest.fixture
def model():
    """The model with the state manager"""
    model_ = MagicMock()
    model_.state_manager.current_state = State.IDLE
    return model_


@pytest.fixture
def passer(model, printer, settings):
    """The passer in the idle state, the singleton is released after"""
    passer_ = TelemetryPasser(model, printer)
    passer_.state_changed()
    printer.telemetry.reset_mock()
    yield passer_
    TelemetryPasser._MCSingleton__instance = None


def sent(printer):
    """What was passed to the SDK by the last call"""
    return printer.telemetry.call_args.kwargs


# --- lifecycle ---

def test_is_singleton(passer, model, printer):
    """There can be only one passer"""
    assert TelemetryPasser.get_instance() is passer
    with pytest.raises(RuntimeError):
        TelemetryPasser(model, printer)


def test_start_and_stop(passer):
    """The thread runs until stopped"""
    with patch.object(passer, "_update"):
        passer.start()
        passer.stop()
        passer.wait_stopped()
    assert passer.running is False
    assert passer.notify_evt.is_set()


# --- intervals ---

@pytest.mark.parametrize(("state", "interval"), [
    (State.IDLE, TELEMETRY_IDLE_INTERVAL),
    (State.READY, TELEMETRY_IDLE_INTERVAL),
    (State.PRINTING, TELEMETRY_PRINTING_INTERVAL),
    (State.PAUSED, TELEMETRY_PRINTING_INTERVAL),
])
def test_interval_by_state(passer, model, state, interval):
    """Awake passer sends faster while printing"""
    model.state_manager.current_state = state
    passer.last_activity_at = telemetry_passer.time()
    passer._update()
    assert passer.sleeping is False
    assert passer.telemetry_interval == interval


def test_interval_when_sleeping(passer):
    """After a time without any activity the passer slows down"""
    passer.last_activity_at = telemetry_passer.time() \
        - TELEMETRY_SLEEP_AFTER - 1
    passer._update()
    assert passer.sleeping is True
    assert passer.telemetry_interval == TELEMETRY_SLEEPING_INTERVAL


def test_activity_wakes_up(passer):
    """The activity wakes up the sleeping passer at once"""
    passer.sleeping = True
    passer.activity_observed()
    assert passer.notify_evt.is_set()


def test_activity_awake(passer):
    """The awake passer only remembers the time"""
    passer.sleeping = False
    passer.last_activity_at = 0
    passer.activity_observed()
    assert passer.last_activity_at > 0
    assert not passer.notify_evt.is_set()


# --- passing ---

@pytest.mark.parametrize("what", [
    "connect", "initialised", "wizard", "queue"])
def test_nothing_sent_when(passer, printer, settings, what):
    """There are reasons not to send anything"""
    if what == "connect":
        settings.use_connect.return_value = False
    elif what == "initialised":
        printer.is_initialised.return_value = False
    elif what == "wizard":
        settings.is_wizard_needed.return_value = True
    else:
        printer.queue.qsize.return_value = 4
    passer.set_telemetry(Telemetry(axis_z=1.0))
    passer.pass_telemetry()
    printer.telemetry.assert_not_called()
    assert passer._to_send  # kept for later


def test_changes_are_sent_once(passer, printer):
    """Only the new values are sent"""
    passer.set_telemetry(Telemetry(axis_z=1.0, fan_hotend=5))
    passer.pass_telemetry()
    assert sent(printer) == {"axis_z": 1.0, "fan_hotend": 5}
    printer.telemetry.reset_mock()

    passer.set_telemetry(Telemetry(axis_z=1.0, fan_hotend=6))
    passer.pass_telemetry()
    assert sent(printer) == {"fan_hotend": 6}
    assert passer.model.latest_telemetry.fan_hotend == 6


def test_nothing_new_sends_empty(passer, printer):
    """The SDK is still called, so it knows we are alive"""
    passer.pass_telemetry()
    printer.telemetry.assert_called_once_with()


def test_jitter_of_temperatures_is_ignored(passer, printer):
    """Small temperature changes are not worth sending"""
    passer.set_telemetry(Telemetry(temp_nozzle=200.0))
    passer.pass_telemetry()
    printer.telemetry.reset_mock()

    passer.set_telemetry(Telemetry(temp_nozzle=200.0 + JITTER_THRESHOLD))
    passer.pass_telemetry()
    assert sent(printer) == {}

    passer.set_telemetry(Telemetry(temp_nozzle=200.0 + 2 * JITTER_THRESHOLD))
    passer.pass_telemetry()
    assert sent(printer) == {"temp_nozzle": 200.0 + 2 * JITTER_THRESHOLD}


def test_slot_values_are_sent_by_path(passer, printer):
    """The MMU slots have a different structure in the model"""
    slot = Slot(active=1, slots={"1": IndividualSlot(temp=200.0)})
    passer.set_telemetry(Telemetry(slot=slot))
    passer.pass_telemetry()
    assert sent(printer)["slot"]["1"]["temp"] == 200.0
    printer.telemetry.reset_mock()

    slot = Slot(active=1, slots={"1": IndividualSlot(temp=200.1)})
    passer.set_telemetry(Telemetry(slot=slot))
    passer.pass_telemetry()
    assert "slot" not in sent(printer)


# --- filters ---

def test_idle_filters(passer, printer):
    """The print progress means nothing when idle"""
    passer.set_telemetry(Telemetry(progress=50, axis_z=2.0))
    passer.pass_telemetry()
    assert sent(printer) == {"axis_z": 2.0}
    assert passer.model.latest_telemetry.progress is None
    assert passer._latest_full.progress == 50


def test_state_change_releases_filtered_values(passer, model, printer):
    """When the printing starts, the remembered progress is sent"""
    passer.set_telemetry(Telemetry(progress=50))
    model.state_manager.current_state = State.PRINTING
    passer.state_changed()
    passer.pass_telemetry()
    assert sent(printer)["progress"] == 50


def test_printing_filters_axes(passer, model, printer):
    """Axes x and y are hidden while printing"""
    model.state_manager.current_state = State.PRINTING
    passer.state_changed()
    printer.telemetry.reset_mock()
    passer.set_telemetry(Telemetry(axis_x=1.0, axis_z=3.0))
    passer.pass_telemetry()
    assert sent(printer) == {"axis_z": 3.0}

    model.state_manager.current_state = State.PAUSED
    passer.state_changed()
    assert Modifier.FILTER_PRINTING not in passer._active_filters
    assert Modifier.FILTER_IDLE not in passer._active_filters


def test_mmu_filter(passer, printer):
    """Slots are filtered while the MMU is off"""
    printer.mmu_enabled = False
    passer.state_changed()
    assert Modifier.FILTER_MMU_OFF in passer._active_filters
    passer.set_telemetry(Telemetry(slot=Slot(active=1)))
    assert passer.model.latest_telemetry.slot is None

    printer.mmu_enabled = True
    passer.state_changed()
    assert Modifier.FILTER_MMU_OFF not in passer._active_filters


def test_filtered_paths(passer, model):
    """Filtered paths depend on the state"""
    assert ("progress",) in passer._get_filtered_paths()
    model.state_manager.current_state = State.PRINTING
    assert ("axis_x",) in passer._get_filtered_paths()
    model.state_manager.current_state = State.PAUSED
    assert passer._get_filtered_paths() == set()


def test_modifiers_of_a_path(passer):
    """Slot temperatures are jittering, the slot is MMU dependent"""
    modifiers = passer._get_modifiers(("slot", "1", "temp"))
    assert modifiers == {Modifier.FILTER_MMU_OFF, Modifier.JITTER_TEMP}


# --- waking up ---

@pytest.mark.parametrize(("state", "modifiers", "wake"), [
    (State.IDLE, {Modifier.ACTIVATE_IDLE}, True),
    (State.IDLE, set(), False),
    (State.PRINTING, {Modifier.ACTIVATE_IDLE}, False),
    (State.PRINTING, {Modifier.ACTIVATE_PRINTING}, False),
    (State.PRINTING, {Modifier.ACTIVATE_PRINTING,
                      Modifier.FILTER_PRINTING}, True),
])
def test_should_wake_up(passer, model, state, modifiers, wake):
    """Only the interesting changes wake the passer up"""
    model.state_manager.current_state = state
    assert passer._should_wake_up(modifiers) is wake


def test_interesting_change_is_activity(passer):
    """A moving axis in idle wakes the passer up"""
    passer.sleeping = True
    passer.last_activity_at = 0
    passer.set_telemetry(Telemetry(axis_z=5.0))
    assert passer.last_activity_at > 0
    assert passer.notify_evt.is_set()


# --- resending, wiping, resetting ---

def test_resend_latest(passer, printer):
    """Everything known is sent again"""
    passer.set_telemetry(Telemetry(axis_z=1.0))
    passer.pass_telemetry()
    printer.telemetry.reset_mock()
    passer.resend_latest_telemetry()
    assert sent(printer) == {"axis_z": 1.0}


def test_resend_on_timer(passer, printer):
    """Full refresh happens after the interval"""
    passer.set_telemetry(Telemetry(axis_z=1.0))
    passer.pass_telemetry()
    printer.telemetry.reset_mock()
    passer.full_refresh_at = telemetry_passer.time() \
        - TELEMETRY_REFRESH_INTERVAL - 1
    passer.set_telemetry(Telemetry(axis_z=1.0))
    assert sent(printer) == {"axis_z": 1.0}
    assert passer.full_refresh_at > 0


def test_wipe(passer, printer):
    """The wiped telemetry is sent again when it comes back"""
    passer.set_telemetry(Telemetry(axis_z=1.0))
    passer.pass_telemetry()
    passer.wipe_telemetry()
    assert passer.model.latest_telemetry == Telemetry()
    assert passer._last_sent == {}
    printer.telemetry.reset_mock()
    passer.set_telemetry(Telemetry(axis_z=1.0))
    passer.pass_telemetry()
    assert sent(printer) == {"axis_z": 1.0}


def test_reset_value(passer):
    """Single value is removed from the telemetry"""
    passer.set_telemetry(Telemetry(axis_z=1.0, axis_y=2.0))
    passer.reset_value(("axis_z",))
    assert passer.model.latest_telemetry.axis_z is None
    assert passer._latest_full.axis_z is None
    assert passer.model.latest_telemetry.axis_y == 2.0


# --- path helpers ---

def test_path_to_model(passer):
    """Dict paths are mapped to the model paths"""
    assert passer._path_to_model(("axis_z",)) == ("axis_z",)
    assert passer._path_to_model(("slot", "2", "temp")) == \
        ("slot", "slots", "2", "temp")


def test_path_to_model_invalid_mapping(passer):
    """Mapping which ends on a dict is invalid"""
    with pytest.raises(ValueError):
        passer._path_to_model(("slot",))


def test_get_by_path(passer):
    """Values are read from dicts and from models"""
    data = {"a": {"b": 1}}
    assert passer._get_by_path(data, ("a", "b")) == 1
    assert passer._get_by_path(data, ("a", "x")) is None
    assert passer._get_by_path(data, ("x", "b")) is None
    model = Telemetry(slot=Slot(slots={"1": IndividualSlot(temp=1.5)}))
    assert passer._get_by_path(model, ("slot", "1", "temp")) == 1.5
    assert passer._get_by_path(Telemetry(), ("slot", "1", "temp")) is None


def test_reset_by_path(passer):
    """Only the end of the path is reset"""
    data = {"a": {"b": 1, "c": 2}}
    passer._reset_by_path(data, ("a", "b"))
    assert data == {"a": {"b": None, "c": 2}}
    passer._reset_by_path(data, ("x", "y"))  # missing parent is fine
    assert "x" not in data


def test_update_by_path_models(passer):
    """Values are copied, the subtrees too"""
    target = Telemetry()
    source = Telemetry(axis_z=1.0, slot=Slot(
        slots={"1": IndividualSlot(temp=2.0)}))
    passer._update_by_path(target, source, ("axis_z",))
    passer._update_by_path(target, source, ("slot", "1", "temp"))
    assert target.axis_z == 1.0
    assert target.slot.slots["1"].temp == 2.0
    assert target.slot is not source.slot


def test_update_by_path_set_none(passer):
    """None is set only when asked for"""
    target = Telemetry(axis_z=1.0)
    source = Telemetry()
    passer._update_by_path(target, source, ("axis_z",))
    assert target.axis_z == 1.0
    passer._update_by_path(target, source, ("axis_z",), set_none=True)
    assert target.axis_z is None


def test_update_by_path_shallow_source(passer):
    """The source has no such subtree"""
    target = Telemetry(slot=Slot(active=1))
    passer._update_by_path(target, Telemetry(), ("slot", "active"))
    assert target.slot.active == 1
    passer._update_by_path(target, Telemetry(), ("slot", "active"),
                           set_none=True)
    assert target.slot is None


def test_update_by_path_type_mismatch(passer):
    """Types must be the same"""
    with pytest.raises(TypeError):
        passer._update_by_path({}, Telemetry(), ("axis_z",))


def test_unsupported_structures(passer):
    """Only dicts and models can be traversed"""
    with pytest.raises(TypeError):
        passer._set_multi([], "a", 1)
    with pytest.raises(TypeError):
        passer._get_multi([], "a")
    with pytest.raises(TypeError):
        passer._get_correct_path([], ("a",))
