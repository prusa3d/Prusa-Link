"""Tests for the SET_VALUE high-level command"""
from unittest.mock import Mock

import pytest
from prusa.connect.printer.const import State

from prusa.link.const import LimitsMK3  # type:ignore
from prusa.link.printer_adapter import command as command_module
from prusa.link.printer_adapter.command import CommandFailed  # type:ignore
from prusa.link.printer_adapter.command_handlers import (  # type:ignore
    SetValue,
)

# pylint: disable=redefined-outer-name,duplicate-code

ALL_KEYS = {
    "speed": ("M220 S{}", LimitsMK3.print_speed_min,
              LimitsMK3.print_speed_max),
    "flow": ("M221 S{}", LimitsMK3.print_flow_min, LimitsMK3.print_flow_max),
    "nozzle_temperature": ("M104 S{}", LimitsMK3.temp_nozzle_min,
                           LimitsMK3.temp_nozzle_max),
    "bed_temperature": ("M140 S{}", LimitsMK3.temp_bed_min,
                        LimitsMK3.temp_bed_max),
}
ALLOWED_STATES = {State.IDLE, State.PAUSED, State.FINISHED, State.STOPPED,
                  State.PRINTING, State.READY}
FORBIDDEN_STATES = set(State) - ALLOWED_STATES


@pytest.fixture
def state_of_printer(monkeypatch):
    """Replaces all the singletons a Command grabs, returns the state setter
    and the list of gcodes, which were sent to the printer"""
    for name in ("MonitoredSerialQueue", "SerialAdapter",
                 "ThreadedSerialParser", "MyPrinter", "StateManager",
                 "FilePrinter", "Job"):
        monkeypatch.setattr(command_module, name, Mock())
    model = Mock()
    monkeypatch.setattr(command_module, "Model",
                        Mock(get_instance=Mock(return_value=model)))

    sent = []
    monkeypatch.setattr(command_module.Command, "do_instruction",
                        lambda self, message: sent.append(message))

    def set_state(state):
        model.state_manager.current_state = state

    set_state(State.IDLE)
    set_state.sent = sent
    return set_state


def run(**kwargs):
    """Runs the SET_VALUE command with the given kwargs"""
    return SetValue(parameters=kwargs, command_id=1).run_command()


@pytest.mark.parametrize("key", ALL_KEYS)
def test_sends_gcode(state_of_printer, key):
    """Every key sends its gcode"""
    run(**{key: 42})
    assert state_of_printer.sent == [ALL_KEYS[key][0].format(42)]


@pytest.mark.parametrize("key", ALL_KEYS)
def test_limits_inclusive(state_of_printer, key):
    """Limit values themselves are accepted"""
    gcode, low, high = ALL_KEYS[key]
    run(**{key: low})
    run(**{key: high})
    assert state_of_printer.sent == [gcode.format(low), gcode.format(high)]


@pytest.mark.parametrize("key", ALL_KEYS)
def test_out_of_limits(state_of_printer, key):
    """Values outside of the limits are rejected without sending anything"""
    _, low, high = ALL_KEYS[key]
    for value in (low - 1, high + 1):
        with pytest.raises(CommandFailed):
            run(**{key: value})
    assert not state_of_printer.sent


@pytest.mark.parametrize("key", ALL_KEYS)
@pytest.mark.parametrize("value", [True, 42.5, "42", None])
def test_not_an_integer(state_of_printer, key, value):
    """Only real integers are valid, bool is not a number here"""
    with pytest.raises(CommandFailed):
        run(**{key: value})
    assert not state_of_printer.sent


@pytest.mark.parametrize("kwargs", [
    {},
    None,
    {"unknown": 1},
    {"speed": 100, "flow": 100},
    {"speed": 100, "unknown": 1},
])
def test_exactly_one_known_key(state_of_printer, kwargs):
    """Unknown keys and anything but exactly one value are rejected"""
    with pytest.raises(CommandFailed):
        SetValue(parameters=kwargs, command_id=1).run_command()
    assert not state_of_printer.sent


@pytest.mark.parametrize("key", ["speed", "flow", "nozzle_temperature",
                                 "bed_temperature"])
def test_states(state_of_printer, key):
    """Allowed states pass, all the others are rejected"""
    gcode, low, high = ALL_KEYS[key]
    # min_temp_nozzle_e only matters for printing, pick a safe value
    value = min(high, max(low, LimitsMK3.min_temp_nozzle_e))
    for state in ALLOWED_STATES:
        state_of_printer(state)
        run(**{key: value})
    assert len(state_of_printer.sent) == len(ALLOWED_STATES)
    assert set(state_of_printer.sent) == {gcode.format(value)}

    state_of_printer.sent.clear()
    for state in FORBIDDEN_STATES:
        state_of_printer(state)
        with pytest.raises(CommandFailed):
            run(**{key: value})
    assert not state_of_printer.sent


def test_nozzle_too_cold_while_printing(state_of_printer):
    """Cooling below the extrusion minimum during a print is rejected"""
    state_of_printer(State.PRINTING)
    with pytest.raises(CommandFailed):
        run(nozzle_temperature=LimitsMK3.min_temp_nozzle_e - 1)
    assert not state_of_printer.sent

    run(nozzle_temperature=LimitsMK3.min_temp_nozzle_e)
    assert state_of_printer.sent == [f"M104 S{LimitsMK3.min_temp_nozzle_e}"]


def test_nozzle_cooling_outside_print(state_of_printer):
    """Turning the nozzle off is fine when not printing"""
    state_of_printer(State.PAUSED)
    run(nozzle_temperature=0)
    assert state_of_printer.sent == ["M104 S0"]
