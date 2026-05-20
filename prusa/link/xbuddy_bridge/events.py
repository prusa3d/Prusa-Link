"""Outbound printer -> server JSON payload builders.

These are pure functions returning dict payloads ready for
``json.dumps`` and sending as WebSocket text frames. No I/O,
no protocol-state side effects.

Event vocabulary lives in
``docs/src/content/docs/prusa-xbuddy-websocket-protocol.md`` in the
prusa-connect-local repo.
"""

from typing import Any, Optional


def telemetry(fields: dict) -> dict:
    """A telemetry payload is just the delta dict the
    ``TelemetryPasser`` already produces."""
    return dict(fields)


def info_event(*, state: Optional[str], data: dict) -> dict:
    payload: dict[str, Any] = {"event": "INFO", "data": data}
    if state is not None:
        payload["state"] = state
    return payload


def state_changed_event(state: str) -> dict:
    return {"event": "STATE_CHANGED", "state": state}


def job_info_event(*, job_id: int, data: dict) -> dict:
    return {"event": "JOB_INFO", "job_id": job_id, "data": data}


def file_info_event(data: dict) -> dict:
    return {"event": "FILE_INFO", "data": data}


def accepted(command_id: int) -> dict:
    return {"event": "ACCEPTED", "command_id": command_id}


def finished(command_id: int, *, data: Optional[dict] = None) -> dict:
    payload: dict[str, Any] = {"event": "FINISHED", "command_id": command_id}
    if data:
        payload["data"] = data
    return payload


def rejected(command_id: int, *, reason: str,
             machine_reason: Optional[str] = None) -> dict:
    payload: dict[str, Any] = {
        "event": "REJECTED",
        "command_id": command_id,
        "reason": reason,
    }
    if machine_reason is not None:
        payload["machine_reason"] = machine_reason
    return payload


def failed(command_id: int, *, reason: str,
           machine_reason: Optional[str] = None) -> dict:
    payload: dict[str, Any] = {
        "event": "FAILED",
        "command_id": command_id,
        "reason": reason,
    }
    if machine_reason is not None:
        payload["machine_reason"] = machine_reason
    return payload
