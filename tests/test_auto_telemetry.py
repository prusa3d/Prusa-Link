"""Tests of the autoreporting monitor"""
from unittest.mock import MagicMock, patch

import pytest

from prusa.link.const import REPORTING_TIMEOUT  # type:ignore
from prusa.link.printer_adapter import auto_telemetry  # type:ignore
from prusa.link.printer_adapter.auto_telemetry import (  # type:ignore
    AutoTelemetry,
)
from prusa.link.printer_adapter.structures.regular_expressions import (  # type:ignore  # noqa: E501
    FAN_REGEX,
    HEATING_HOTEND_REGEX,
    HEATING_REGEX,
    POSITION_REGEX,
    TEMPERATURE_REGEX,
)

# pylint: disable=redefined-outer-name,protected-access
# pylint: disable=comparison-with-callable


@pytest.fixture
def telemetry():
    """The auto telemetry with mocks, the last written telemetry"""
    auto = AutoTelemetry(MagicMock(), MagicMock(), MagicMock(), MagicMock())
    return auto


def written(auto):
    """The telemetry given to the passer"""
    return auto.telemetry_passer.set_telemetry.call_args.args[0]


def test_handlers_registered():
    """Every kind of the report is listened to"""
    parser = MagicMock()
    auto = AutoTelemetry(parser, MagicMock(), MagicMock(), MagicMock())
    registered = {call.args[0]: call.args[1]
                  for call in parser.add_decoupled_handler.call_args_list}
    assert registered[TEMPERATURE_REGEX] == auto.temps_recorded
    assert registered[HEATING_REGEX] == auto.temps_recorded
    assert registered[HEATING_HOTEND_REGEX] == auto.temps_recorded
    assert registered[POSITION_REGEX] == auto.positions_recorded
    assert registered[FAN_REGEX] == auto.fans_recorded


def test_full_temperatures(telemetry):
    """Temperatures with their targets"""
    match = TEMPERATURE_REGEX.match(
        "T:215.0 /216.0 B:60.0 /61.0 T0:215.0 /216.0 @:30 B@:40")
    telemetry.temps_recorded(object(), match)
    assert (written(telemetry).temp_nozzle, written(telemetry).temp_bed) \
        == (215.0, 60.0)
    assert (written(telemetry).target_nozzle,
            written(telemetry).target_bed) == (216.0, 61.0)
    assert telemetry.last_seen_temps > 0


def test_heating_temperatures(telemetry):
    """The heating report has no targets"""
    telemetry.temps_recorded(
        object(), HEATING_REGEX.match("T:200.5 E:0 B:55.2"))
    assert written(telemetry).temp_bed == 55.2
    assert written(telemetry).target_nozzle is None


def test_hotend_only_temperatures(telemetry):
    """Only the nozzle temperature is known"""
    telemetry.temps_recorded(
        object(), HEATING_HOTEND_REGEX.match("T:200.5 E:? W:?"))
    assert written(telemetry).temp_nozzle == 200.5
    assert written(telemetry).temp_bed is None


def test_positions(telemetry):
    """Positions of the axes"""
    match = POSITION_REGEX.match(
        "X:1.00 Y:2.00 Z:3.00 E:0.00 Count X: 1.00 Y:2.00 Z:3.00 E:0.00")
    telemetry.positions_recorded(object(), match)
    assert (written(telemetry).axis_x, written(telemetry).axis_y,
            written(telemetry).axis_z) == (1.0, 2.0, 3.0)
    assert telemetry.last_seen_positions > 0


def test_fans(telemetry):
    """Speeds and the power of the fans"""
    match = FAN_REGEX.search("E0:4000 RPM PRN1:3000 RPM E0@:255 PRN1@:128")
    telemetry.fans_recorded(object(), match)
    sent = written(telemetry)
    assert (sent.fan_extruder, sent.fan_hotend, sent.fan_print) == \
        (4000, 4000, 3000)
    assert (sent.target_fan_hotend, sent.target_fan_print) == (255, 128)
    assert telemetry.last_seen_fans > 0


def test_update_when_reporting_works(telemetry):
    """Recent reports mean nothing to do"""
    telemetry._reset_last_seen()
    with patch.object(telemetry, "turn_reporting_on") as turn_on:
        telemetry.update()
    turn_on.assert_not_called()


def test_update_when_one_report_is_missing(telemetry):
    """One missing kind of report turns the reporting on"""
    telemetry._reset_last_seen()
    telemetry.last_seen_fans = auto_telemetry.time() - REPORTING_TIMEOUT - 1
    with patch.object(telemetry, "turn_reporting_on") as turn_on:
        telemetry.update()
    turn_on.assert_called_once_with()


def test_turn_reporting_on(telemetry):
    """The M155 instruction is sent and the timers restart"""
    with patch.object(auto_telemetry, "enqueue_instruction") as enqueue, \
            patch.object(auto_telemetry, "wait_for_instruction") as wait:
        telemetry.turn_reporting_on()
    enqueue.assert_called_once_with(telemetry.serial_queue, "M155 S2 C7")
    wait.assert_called_once()
    assert telemetry.last_seen_temps > 0


def test_proper_stop(telemetry):
    """The reporting is switched off on the way out"""
    with patch.object(auto_telemetry, "enqueue_instruction") as enqueue, \
            patch.object(auto_telemetry, "wait_for_instruction") as wait:
        telemetry.proper_stop()
    enqueue.assert_called_once_with(telemetry.serial_queue, "M155 S0 C0")
    wait.assert_called_once()
    assert telemetry.quit_evt.is_set()
