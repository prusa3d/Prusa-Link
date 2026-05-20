"""Round-trip tests for the xBuddy application framing layer.

Byte vectors are aligned with the encoder in
``prusa-connect-local/internal/protocol/buddy_encoder.go``.
"""

import json

import pytest

from prusa.link.xbuddy_bridge import protocol


def test_encode_id_pads_to_8_hex_lower():
    assert protocol.encode_id(0) == "00000000"
    assert protocol.encode_id(0x2A) == "0000002a"
    assert protocol.encode_id(0xDEADBEEF) == "deadbeef"


def test_encode_id_wraps_at_32_bits():
    assert protocol.encode_id(0x1_0000_0001) == "00000001"


def test_encode_id_rejects_negative():
    with pytest.raises(ValueError):
        protocol.encode_id(-1)


def test_encode_json_frame_matches_spec_example():
    # Spec example:
    # J0000002A{"command":"START_PRINT","kwargs":{"path":"/usb/model.gcode"}}
    frame = protocol.encode_json_frame(
        0x2A, {"command": "START_PRINT", "kwargs": {"path": "/usb/model.gcode"}})
    assert frame.startswith("J0000002a")
    body = frame[protocol.HEADER_LEN:]
    assert json.loads(body) == {
        "command": "START_PRINT",
        "kwargs": {"path": "/usb/model.gcode"},
    }


def test_encode_gcode_frame_default_is_G():
    frame = protocol.encode_gcode_frame(1, "G90")
    assert frame == "G00000001G90"


def test_encode_gcode_frame_forced_uses_F():
    frame = protocol.encode_gcode_frame(1, "M112", forced=True)
    assert frame == "F00000001M112"


def test_encode_transfer_frame_is_binary_with_ascii_header():
    chunk = bytes(range(256))
    frame = protocol.encode_transfer_frame(0x315, chunk)
    assert frame[:protocol.HEADER_LEN] == b"T00000315"
    assert frame[protocol.HEADER_LEN:] == chunk


def test_decode_frame_round_trip_json():
    payload = {"command": "STOP_PRINT"}
    frame = protocol.encode_json_frame(7, payload).encode("utf-8")
    type_letter, command_id, body = protocol.decode_frame(frame)
    assert type_letter == "J"
    assert command_id == 7
    assert json.loads(body.decode("utf-8")) == payload


def test_decode_frame_round_trip_gcode():
    frame = protocol.encode_gcode_frame(0xABCD, "M104 S200").encode("utf-8")
    type_letter, command_id, body = protocol.decode_frame(frame)
    assert type_letter == "G"
    assert command_id == 0xABCD
    assert body == b"M104 S200"


def test_decode_frame_round_trip_transfer():
    chunk = b"\x00\x01\xfe\xff"
    frame = protocol.encode_transfer_frame(789, chunk)
    type_letter, command_id, body = protocol.decode_frame(frame)
    assert type_letter == "T"
    assert command_id == 789
    assert body == chunk


def test_decode_frame_rejects_short_input():
    with pytest.raises(protocol.FrameError):
        protocol.decode_frame(b"J00000")


def test_decode_frame_rejects_unknown_type():
    with pytest.raises(protocol.FrameError):
        protocol.decode_frame(b"X00000001payload")


def test_decode_frame_rejects_non_hex_id():
    with pytest.raises(protocol.FrameError):
        protocol.decode_frame(b"J0000zzzzpayload")


def test_classify_event():
    assert protocol.classify_printer_message({"event": "FINISHED"}) == "event"


def test_classify_transfer_request():
    msg = {"transfer": "inline", "file_id": 1, "chunk": 4096, "start": 0, "end": 4095}
    assert protocol.classify_printer_message(msg) == "transfer_request"


def test_classify_telemetry_default():
    assert protocol.classify_printer_message({"state": "IDLE"}) == "telemetry"


def test_classify_telemetry_ignores_non_inline_transfer():
    # A bare "transfer" key without value "inline" still counts as telemetry.
    assert protocol.classify_printer_message({"transfer": "other"}) == "telemetry"
