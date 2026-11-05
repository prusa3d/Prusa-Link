"""Tests of the printer control endpoints (/api/printer/printhead, ...)"""
from unittest.mock import patch

import pytest
from prusa.connect.printer.const import State

from prusa.link.const import LimitsMK3  # type:ignore
from prusa.link.web import controls  # type:ignore

# pylint: disable=redefined-outer-name,unused-argument


@pytest.fixture
def gcodes(daemon):
    """Collects the gcodes enqueued by the endpoints"""
    sent = []

    def fake_enqueue(queue, message, **kwargs):
        assert queue is daemon.prusa_link.serial_queue
        sent.append(message)

    with patch.object(controls, "enqueue_instruction",
                      side_effect=fake_enqueue):
        yield sent


def _state(daemon, state):
    daemon.prusa_link.model.state_manager.current_state = state
    daemon.prusa_link.printer.state = state


@pytest.mark.parametrize(("value", "error"), [
    (-1, "Temperature too low"),
    (301, "Temperature too high"),
])
def test_check_temperature_limits(value, error):
    """Temperatures out of limits are refused with a specific error"""
    with pytest.raises(Exception, match="") as exc:
        controls.check_temperature_limits(value, 0, 300)
    assert exc.value.title == error


def test_check_temperature_limits_ok():
    """Limits are inclusive"""
    controls.check_temperature_limits(0, 0, 300)
    controls.check_temperature_limits(300, 0, 300)


def test_check_value_limits():
    """Same for generic values"""
    controls.check_value_limits(5, 5, 10)
    controls.check_value_limits(10, 5, 10)
    with pytest.raises(controls.ValueTooLow):
        controls.check_value_limits(4, 5, 10)
    with pytest.raises(controls.ValueTooHigh):
        controls.check_value_limits(11, 5, 10)


def test_printhead_unknown_command(client, daemon, gcodes):
    """Nothing happens for an unknown command"""
    _state(daemon, State.IDLE)
    assert client.post("/api/printer/printhead",
                       data={"command": "dance"}).status == 204
    assert not gcodes


def test_disable_steppers(client, daemon, gcodes):
    """Steppers are disabled in any state"""
    _state(daemon, State.PRINTING)
    response = client.post("/api/printer/printhead",
                           data={"command": "disable_steppers"})
    assert response.status == 204
    assert gcodes == ["M84"]


@pytest.mark.parametrize("command", ["speed", "feedrate"])
def test_speed(client, daemon, gcodes, command):
    """Octoprint's feedrate is the speed in percent"""
    _state(daemon, State.PRINTING)
    client.post("/api/printer/printhead",
                data={"command": command, "factor": 120})
    assert gcodes == ["M220 S120"]


def test_speed_default(client, daemon, gcodes):
    """100 % is the default"""
    _state(daemon, State.IDLE)
    client.post("/api/printer/printhead", data={"command": "speed"})
    assert gcodes == ["M220 S100"]


@pytest.mark.parametrize("factor", [LimitsMK3.print_speed_min - 1,
                                    LimitsMK3.print_speed_max + 1])
def test_speed_out_of_limits(client, daemon, gcodes, factor):
    """Speed has limits"""
    _state(daemon, State.IDLE)
    response = client.post("/api/printer/printhead",
                           data={"command": "speed", "factor": factor})
    assert response.status == 400
    assert not gcodes


def test_jog_relative(client, daemon, gcodes):
    """Relative movement is G91 and a move"""
    _state(daemon, State.IDLE)
    response = client.post("/api/printer/printhead",
                           data={"command": "jog", "x": 10, "feedrate": 1000})
    assert response.status == 204
    assert gcodes[0] == "G91"
    assert gcodes[1].startswith("G1 F1000 ")


def test_jog_absolute(client, daemon, gcodes):
    """Absolute movement is G90"""
    _state(daemon, State.IDLE)
    client.post("/api/printer/printhead",
                data={"command": "jog", "x": 10, "absolute": True})
    assert gcodes[0] == "G90"


def test_jog_gcode_format(client, daemon, gcodes):
    """The G1 gcode lists the axes the way Marlin understands"""
    _state(daemon, State.IDLE)
    client.post("/api/printer/printhead",
                data={"command": "jog", "x": 10, "y": 20})
    assert gcodes[1] == f"G1 F{LimitsMK3.feedrate_x_max} X10 Y20"


def test_jog_speed_alias(client, daemon, gcodes):
    """Octoprint's speed is accepted instead of feedrate"""
    _state(daemon, State.IDLE)
    client.post("/api/printer/printhead",
                data={"command": "jog", "x": 1, "speed": 500})
    assert gcodes[1].startswith("G1 F500 ")


@pytest.mark.parametrize(("axis", "value"), [
    ("x", LimitsMK3.position_x_max + 1),
    ("y", LimitsMK3.position_y_min - 1),
    ("z", LimitsMK3.position_z_max + 1),
])
def test_jog_absolute_out_of_limits(client, daemon, gcodes, axis, value):
    """Absolute positions are limited, relative ones are not"""
    _state(daemon, State.IDLE)
    response = client.post(
        "/api/printer/printhead",
        data={"command": "jog", axis: value, "absolute": True})
    assert response.status == 400
    assert not gcodes
    response = client.post("/api/printer/printhead",
                           data={"command": "jog", axis: value})
    assert response.status == 204


def test_jog_feedrate_out_of_limits(client, daemon, gcodes):
    """Feedrate is limited"""
    _state(daemon, State.IDLE)
    response = client.post(
        "/api/printer/printhead",
        data={"command": "jog", "x": 1,
              "feedrate": LimitsMK3.feedrate_x_max + 1})
    assert response.status == 400
    assert not gcodes


@pytest.mark.parametrize(("current", "allowed"), [
    (State.IDLE, True), (State.READY, True), (State.FINISHED, True),
    (State.STOPPED, True), (State.PAUSED, False), (State.PRINTING, False),
    (State.BUSY, False),
])
def test_jog_z_states(client, daemon, gcodes, current, allowed):
    """The Z axis is moved only when the printer is not printing"""
    _state(daemon, current)
    response = client.post("/api/printer/printhead",
                           data={"command": "jog", "z": 1})
    assert response.status == (204 if allowed else 400)
    assert bool(gcodes) is allowed


@pytest.mark.parametrize(("current", "allowed"), [
    (State.IDLE, True), (State.PAUSED, True), (State.PRINTING, False),
    (State.ATTENTION, False),
])
def test_jog_xy_states(client, daemon, gcodes, current, allowed):
    """X and Y can be moved even when paused"""
    _state(daemon, current)
    response = client.post("/api/printer/printhead",
                           data={"command": "jog", "x": 1})
    assert response.status == (204 if allowed else 400)
    assert bool(gcodes) is allowed


@pytest.mark.parametrize(("current", "allowed"), [
    (State.IDLE, True), (State.READY, True), (State.PAUSED, False),
    (State.PRINTING, False),
])
def test_home_states(client, daemon, gcodes, current, allowed):
    """Homing is allowed only in the idle states"""
    _state(daemon, current)
    response = client.post("/api/printer/printhead",
                           data={"command": "home"})
    assert response.status == (204 if allowed else 400)
    assert bool(gcodes) is allowed


@pytest.mark.parametrize(("axes", "gcode"), [
    (None, "G28 X Y Z"),
    (["x"], "G28 X"),
])
def test_home_gcode_format(client, daemon, gcodes, axes, gcode):
    """Homing all axes or only the chosen ones"""
    _state(daemon, State.IDLE)
    data = {"command": "home"}
    if axes:
        data["axes"] = axes
    client.post("/api/printer/printhead", data=data)
    assert gcodes == [gcode]


def test_tool_target(client, daemon, gcodes):
    """Nozzle temperature"""
    response = client.post("/api/printer/tool",
                           data={"command": "target",
                                 "targets": {"tool0": 215}})
    assert response.status == 204
    assert gcodes == ["M104 S215"]


@pytest.mark.parametrize(("temperature", "status"), [
    (LimitsMK3.temp_nozzle_min - 1, 400),
    (LimitsMK3.temp_nozzle_max + 1, 400),
    (LimitsMK3.temp_nozzle_max, 204),
])
def test_tool_target_limits(client, daemon, gcodes, temperature, status):
    """Nozzle temperature has limits"""
    response = client.post("/api/printer/tool",
                           data={"command": "target",
                                 "targets": {"tool0": temperature}})
    assert response.status == status


def test_tool_extrude(client, daemon, gcodes):
    """Extrusion needs a hot nozzle and no print"""
    daemon.prusa_link.model.latest_telemetry.temp_nozzle = 200
    daemon.prusa_link.printer.state = State.IDLE
    response = client.post("/api/printer/tool",
                           data={"command": "extrude", "amount": 5})
    assert response.status == 204
    assert gcodes[0] == "M83"
    assert gcodes[1].startswith("G1 F")
    assert gcodes[1].endswith("E5")


def test_tool_extrude_cold(client, daemon, gcodes):
    """Cold extrusion is refused"""
    daemon.prusa_link.model.latest_telemetry.temp_nozzle = 20
    daemon.prusa_link.printer.state = State.IDLE
    response = client.post("/api/printer/tool",
                           data={"command": "extrude", "amount": 5})
    assert response.status == 400
    assert not gcodes


def test_tool_extrude_printing(client, daemon, gcodes):
    """Extrusion during a print is refused"""
    daemon.prusa_link.model.latest_telemetry.temp_nozzle = 200
    daemon.prusa_link.printer.state = State.PRINTING
    response = client.post("/api/printer/tool",
                           data={"command": "extrude", "amount": 5})
    assert response.status == 409
    assert not gcodes


def test_tool_flowrate(client, daemon, gcodes):
    """Flow rate in percent, with limits"""
    client.post("/api/printer/tool",
                data={"command": "flowrate", "factor": 95})
    assert gcodes == ["M221 S95"]
    assert client.post(
        "/api/printer/tool",
        data={"command": "flowrate",
              "factor": LimitsMK3.print_flow_max + 1}).status == 400


def test_bed_target(client, daemon, gcodes):
    """Bed temperature with limits"""
    assert client.post("/api/printer/bed",
                       data={"command": "target", "target": 60},
                       ).status == 204
    assert gcodes == ["M140 S60"]
    assert client.post("/api/printer/bed",
                       data={"command": "target", "target": 200},
                       ).status == 400
    assert gcodes == ["M140 S60"]
