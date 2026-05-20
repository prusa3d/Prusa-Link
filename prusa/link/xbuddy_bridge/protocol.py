"""xBuddy application framing on top of WebSocket.

Server -> printer frames have a fixed 9-byte header:

    [Type letter][8 hex chars: 32-bit ID][payload]

Payload is text JSON for J, raw G-code text for G and F, plain text
for D, and raw binary for T. Printer -> server messages are plain JSON
text frames with no application header; the server classifies them by
JSON shape (presence of ``event`` or ``transfer == "inline"``).
"""

import json
from typing import Tuple

HEADER_LEN = 9
HEX_ID_LEN = 8

# Server -> printer frame type letters.
TYPE_JSON_COMMAND = "J"
TYPE_GCODE_COMMAND = "G"
TYPE_FORCED_GCODE = "F"
TYPE_DEBUG = "D"
TYPE_TRANSFER_CHUNK = "T"

_VALID_TYPES = {
    TYPE_JSON_COMMAND,
    TYPE_GCODE_COMMAND,
    TYPE_FORCED_GCODE,
    TYPE_DEBUG,
    TYPE_TRANSFER_CHUNK,
}


class FrameError(ValueError):
    """Raised when a server -> printer frame can't be parsed."""


def encode_id(command_id: int) -> str:
    """8-char lowercase hex, zero-padded. Wraps at 2**32."""
    if command_id < 0:
        raise ValueError("command_id must be non-negative")
    return f"{command_id & 0xFFFFFFFF:08x}"


def _encode_header(type_letter: str, command_id: int) -> str:
    if type_letter not in _VALID_TYPES:
        raise ValueError(f"unknown frame type: {type_letter!r}")
    return type_letter + encode_id(command_id)


def encode_json_frame(command_id: int, payload: dict) -> str:
    """Build a J-frame text payload. Returns a string suitable for a
    WebSocket text frame."""
    body = json.dumps(payload, separators=(",", ":"))
    return _encode_header(TYPE_JSON_COMMAND, command_id) + body


def encode_gcode_frame(command_id: int, gcode: str, *, forced: bool = False) -> str:
    """Build a G or F frame text payload."""
    letter = TYPE_FORCED_GCODE if forced else TYPE_GCODE_COMMAND
    return _encode_header(letter, command_id) + gcode


def encode_transfer_frame(file_id: int, chunk: bytes) -> bytes:
    """Build a T-frame binary payload. The 9-byte ASCII header is
    followed by raw binary chunk bytes."""
    header = _encode_header(TYPE_TRANSFER_CHUNK, file_id).encode("ascii")
    return header + chunk


def decode_frame(data: bytes) -> Tuple[str, int, bytes]:
    """Parse a server -> printer frame.

    Returns ``(type_letter, command_id, payload_bytes)``. The payload
    is returned as bytes; callers decode UTF-8 themselves for text
    frame types (J, G, F, D).
    """
    if len(data) < HEADER_LEN:
        raise FrameError(
            f"frame too short: need at least {HEADER_LEN} bytes, got {len(data)}",
        )

    type_letter = chr(data[0])
    if type_letter not in _VALID_TYPES:
        raise FrameError(f"unknown frame type: {type_letter!r}")

    id_hex = data[1:HEADER_LEN].decode("ascii", errors="replace")
    try:
        command_id = int(id_hex, 16)
    except ValueError as exc:
        raise FrameError(f"invalid command id hex: {id_hex!r}") from exc

    return type_letter, command_id, data[HEADER_LEN:]


def classify_printer_message(payload: dict) -> str:
    """Classify a printer -> server JSON message.

    Returns one of ``"event"``, ``"transfer_request"``, ``"telemetry"``.
    Matches the firmware-side rules: objects with ``event`` are events,
    objects with ``transfer == "inline"`` are inline transfer requests,
    everything else is telemetry.
    """
    if "event" in payload:
        return "event"
    if payload.get("transfer") == "inline":
        return "transfer_request"
    return "telemetry"
