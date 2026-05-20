"""Tests for the inbound command dispatcher in xbuddy_bridge.commands.

The dispatcher must invoke the same handlers Prusa-Link binds to the
SDK at ``PrusaLink.__init__`` and emit ACCEPTED / FINISHED / REJECTED /
FAILED events keyed by the original ``command_id``.
"""

from __future__ import annotations

from typing import List
from unittest.mock import MagicMock

import pytest

from prusa.link.xbuddy_bridge import commands, protocol


@pytest.fixture
def captured_events() -> List[dict]:
    return []


@pytest.fixture
def send_event(captured_events):
    return captured_events.append


def _build_dispatcher(handlers, send_event):
    return commands.CommandDispatcher(handlers=handlers, send_event=send_event)


def test_start_print_invokes_handler_and_emits_finished(captured_events,
                                                        send_event):
    handler = MagicMock(return_value={"status": "ok"})
    dispatcher = _build_dispatcher({"START_PRINT": handler}, send_event)

    frame = protocol.encode_json_frame(
        0x2A,
        {"command": "START_PRINT", "kwargs": {"path": "/usb/model.gcode"}},
    ).encode("utf-8")
    dispatcher.dispatch(frame)

    handler.assert_called_once()
    shim = handler.call_args.args[0]
    assert shim.command_id == 0x2A
    assert shim.kwargs == {"path": "/usb/model.gcode"}

    assert len(captured_events) == 1
    evt = captured_events[0]
    assert evt["event"] == "FINISHED"
    assert evt["command_id"] == 0x2A
    assert evt["data"] == {"status": "ok"}


def test_handler_exception_emits_failed(captured_events, send_event):
    handler = MagicMock(side_effect=RuntimeError("boom"))
    dispatcher = _build_dispatcher({"PAUSE_PRINT": handler}, send_event)

    frame = protocol.encode_json_frame(
        7, {"command": "PAUSE_PRINT", "kwargs": {}}).encode("utf-8")
    dispatcher.dispatch(frame)

    assert handler.called
    assert captured_events == [{
        "event": "FAILED",
        "command_id": 7,
        "reason": "boom",
        "machine_reason": "RuntimeError",
    }]


def test_unsupported_command_is_rejected_without_invoking_handlers(
        captured_events, send_event):
    handler = MagicMock()
    dispatcher = _build_dispatcher({"START_PRINT": handler}, send_event)

    frame = protocol.encode_json_frame(
        99,
        # SET_VALUE is in the xBuddy command set but Prusa-Link has no
        # matching handler -> must be rejected with UNSUPPORTED.
        {"command": "SET_VALUE", "kwargs": {"foo": 1}},
    ).encode("utf-8")
    dispatcher.dispatch(frame)

    handler.assert_not_called()
    assert len(captured_events) == 1
    evt = captured_events[0]
    assert evt["event"] == "REJECTED"
    assert evt["command_id"] == 99
    assert evt["machine_reason"] == "UNSUPPORTED"


def test_missing_command_field_is_rejected(captured_events, send_event):
    handler = MagicMock()
    dispatcher = _build_dispatcher({"START_PRINT": handler}, send_event)

    frame = protocol.encode_json_frame(11, {"kwargs": {}}).encode("utf-8")
    dispatcher.dispatch(frame)

    handler.assert_not_called()
    assert captured_events[0]["event"] == "REJECTED"
    assert captured_events[0]["machine_reason"] == "MISSING_COMMAND"


def test_invalid_json_body_is_rejected(captured_events, send_event):
    handler = MagicMock()
    dispatcher = _build_dispatcher({"START_PRINT": handler}, send_event)

    frame = b"J0000000d{not valid json"
    dispatcher.dispatch(frame)

    handler.assert_not_called()
    assert captured_events[0]["event"] == "REJECTED"
    assert captured_events[0]["machine_reason"] == "INVALID_JSON"


def test_g_frame_dispatches_to_gcode_handler(captured_events, send_event):
    handler = MagicMock(return_value=None)
    dispatcher = _build_dispatcher({"GCODE": handler}, send_event)

    frame = protocol.encode_gcode_frame(1, "G28").encode("utf-8")
    dispatcher.dispatch(frame)

    handler.assert_called_once()
    shim = handler.call_args.args[0]
    assert shim.command_id == 1
    assert shim.kwargs == {"gcode": "G28"}
    assert shim.force is False

    assert captured_events[0] == {"event": "FINISHED", "command_id": 1}


def test_f_frame_sets_force_true(captured_events, send_event):
    handler = MagicMock(return_value=None)
    dispatcher = _build_dispatcher({"GCODE": handler}, send_event)

    frame = protocol.encode_gcode_frame(2, "M112", forced=True).encode("utf-8")
    dispatcher.dispatch(frame)

    handler.assert_called_once()
    shim = handler.call_args.args[0]
    assert shim.force is True
    assert shim.kwargs == {"gcode": "M112"}


def test_t_frame_is_not_dispatched_as_command(captured_events, send_event):
    handler = MagicMock()
    dispatcher = _build_dispatcher({"GCODE": handler}, send_event)

    # T-frames should be routed by the client to transfer.py before
    # reaching the dispatcher. If one slips through, the dispatcher
    # must silently ignore it: no handler call, no ack event.
    frame = protocol.encode_transfer_frame(1, b"chunk")
    dispatcher.dispatch(frame)

    handler.assert_not_called()
    assert captured_events == []


def test_unparseable_frame_is_silently_dropped(captured_events, send_event):
    handler = MagicMock()
    dispatcher = _build_dispatcher({"START_PRINT": handler}, send_event)

    dispatcher.dispatch(b"")  # too short for a header
    dispatcher.dispatch(b"X00000001payload")  # unknown type

    handler.assert_not_called()
    assert captured_events == []
