"""Inbound command dispatch.

Parses J / G / F frames, picks the matching Prusa-Link handler (the
same ones the SDK uses at ``prusa_link.PrusaLink.__init__`` lines
196-210), and invokes it with an SDK-Command-shaped shim. Emits the
ACCEPTED / FINISHED / REJECTED / FAILED events the server expects via
the supplied ``send_event`` callable.

The dispatch runs on a worker thread (caller's choice — typically the
asyncio loop's default thread pool via ``run_in_executor``) because
the underlying ``CommandQueue.do_command`` is blocking.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional

from . import events, protocol

log = logging.getLogger(__name__)

EventSender = Callable[[dict], None]
HandlerFn = Callable[["SDKCommandShim"], Optional[Dict[str, Any]]]

# xBuddy command name -> SDK CommandType.name. Anything not listed is
# rejected with UNSUPPORTED so the server stops sending it.
COMMAND_NAME_TO_HANDLER_KEY: Dict[str, str] = {
    "START_PRINT": "START_PRINT",
    "PAUSE_PRINT": "PAUSE_PRINT",
    "RESUME_PRINT": "RESUME_PRINT",
    "STOP_PRINT": "STOP_PRINT",
    "SEND_JOB_INFO": "SEND_JOB_INFO",
    "LOAD_FILAMENT": "LOAD_FILAMENT",
    "UNLOAD_FILAMENT": "UNLOAD_FILAMENT",
    "SET_PRINTER_READY": "SET_PRINTER_READY",
    "CANCEL_PRINTER_READY": "CANCEL_PRINTER_READY",
    "RESET_PRINTER": "RESET_PRINTER",
    "RESET": "RESET_PRINTER",
}


@dataclass
class SDKCommandShim:
    """Quacks like ``prusa.connect.printer.command.Command`` for the
    purposes of Prusa-Link's existing handlers in
    ``prusa_link.PrusaLink``: handlers only read ``command_id``,
    ``kwargs``, and (for GCODE) ``force``."""

    command_id: int
    kwargs: Dict[str, Any] = field(default_factory=dict)
    force: bool = False


class CommandDispatcher:
    """Turn a server -> printer frame into a handler call.

    ``handlers`` is the same dict that
    ``prusa_link.PrusaLink._register_handlers`` would produce: keys
    are the SDK ``CommandType.name`` strings (``"START_PRINT"`` etc.),
    values are the bound methods that take an SDKCommand-like object
    and return a ``CommandResult`` dict (or raise on failure).
    """

    def __init__(self, handlers: Dict[str, HandlerFn], send_event: EventSender):
        self._handlers = handlers
        self._send_event = send_event

    def dispatch(self, raw_frame: bytes) -> None:
        """Parse ``raw_frame``, dispatch, and emit follow-up events."""
        try:
            type_letter, command_id, body = protocol.decode_frame(raw_frame)
        except protocol.FrameError as exc:
            log.warning("Discarding unparseable frame: %s", exc)
            return

        if type_letter == protocol.TYPE_JSON_COMMAND:
            self._dispatch_json(command_id, body)
        elif type_letter in (protocol.TYPE_GCODE_COMMAND, protocol.TYPE_FORCED_GCODE):
            forced = type_letter == protocol.TYPE_FORCED_GCODE
            self._dispatch_gcode(command_id, body.decode("utf-8"), forced=forced)
        elif type_letter == protocol.TYPE_TRANSFER_CHUNK:
            # Transfer chunks are not commands and don't ack via
            # FINISHED/REJECTED. The client routes T-frames straight
            # to transfer.py before reaching the dispatcher.
            log.debug("CommandDispatcher saw a T-frame; ignoring "
                      "(should be handled by transfer.py)")
        elif type_letter == protocol.TYPE_DEBUG:
            log.debug("xbuddy debug frame: %s", body.decode("utf-8", errors="replace"))
        else:
            log.warning("Unhandled frame type %r", type_letter)

    def _dispatch_json(self, command_id: int, body: bytes) -> None:
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            log.warning("Invalid JSON body in J-frame %#x: %s", command_id, exc)
            self._send_event(events.rejected(
                command_id, reason="invalid JSON", machine_reason="INVALID_JSON"))
            return

        command_name = payload.get("command")
        if not isinstance(command_name, str):
            self._send_event(events.rejected(
                command_id, reason="missing command", machine_reason="MISSING_COMMAND"))
            return

        kwargs = payload.get("kwargs") or {}
        if not isinstance(kwargs, dict):
            self._send_event(events.rejected(
                command_id, reason="invalid kwargs", machine_reason="INVALID_KWARGS"))
            return

        handler_key = COMMAND_NAME_TO_HANDLER_KEY.get(command_name)
        if handler_key is None:
            self._send_event(events.rejected(
                command_id, reason=f"unsupported command: {command_name}",
                machine_reason="UNSUPPORTED"))
            return

        handler = self._handlers.get(handler_key)
        if handler is None:
            self._send_event(events.rejected(
                command_id, reason=f"no handler bound for {command_name}",
                machine_reason="NO_HANDLER"))
            return

        self._invoke(handler, SDKCommandShim(command_id=command_id, kwargs=kwargs))

    def _dispatch_gcode(self, command_id: int, gcode: str, *, forced: bool) -> None:
        handler = self._handlers.get("GCODE")
        if handler is None:
            self._send_event(events.rejected(
                command_id, reason="GCODE handler not bound",
                machine_reason="NO_HANDLER"))
            return
        shim = SDKCommandShim(
            command_id=command_id,
            kwargs={"gcode": gcode},
            force=forced,
        )
        self._invoke(handler, shim)

    def _invoke(self, handler: HandlerFn, shim: SDKCommandShim) -> None:
        try:
            result = handler(shim)
        except Exception as exc:  # noqa: BLE001 — handlers raise CommandFailed and other things
            log.exception("Handler for command_id=%#x failed", shim.command_id)
            self._send_event(events.failed(
                shim.command_id, reason=str(exc),
                machine_reason=type(exc).__name__))
            return
        data = result if isinstance(result, dict) else None
        self._send_event(events.finished(shim.command_id, data=data))
