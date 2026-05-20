"""Wire Prusa-Link's existing signals/hooks into the xBuddy bridge.

This is the only module in ``xbuddy_bridge`` that knows about
``printer_adapter`` internals. Everything else stays protocol-only.

The job/state signals are Blinker signals on the existing singletons:

* ``StateManager.state_changed_signal``: ``(sender, from_state,
  to_state, command_id, source, reason, ready)``
* ``Job.job_info_updated_signal``: ``(sender)``
* ``Job.job_id_updated_signal``: ``(sender, job_id=...)``

Telemetry isn't a signal; instead the bridge is invoked directly from
``TelemetryPasser.pass_telemetry`` when ``transport = websocket`` (see
the fork in that file).
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from . import events
from .client import XBuddyClient

log = logging.getLogger(__name__)


class BridgeSubscriptions:
    """Plumb signals and telemetry into a running ``XBuddyClient``.

    ``attach`` connects everything; ``detach`` disconnects it. The
    caller owns the lifetime of all dependencies."""

    def __init__(
        self,
        *,
        client: XBuddyClient,
        state_manager,
        job,
    ) -> None:
        self._client = client
        self._state_manager = state_manager
        self._job = job
        self._attached = False

    def attach(self) -> None:
        if self._attached:
            return
        self._state_manager.state_changed_signal.connect(self._on_state_changed)
        self._job.job_id_updated_signal.connect(self._on_job_id_updated)
        self._job.job_info_updated_signal.connect(self._on_job_info_updated)
        self._attached = True

    def detach(self) -> None:
        if not self._attached:
            return
        try:
            self._state_manager.state_changed_signal.disconnect(
                self._on_state_changed)
            self._job.job_id_updated_signal.disconnect(self._on_job_id_updated)
            self._job.job_info_updated_signal.disconnect(
                self._on_job_info_updated)
        except Exception:  # noqa: BLE001 — blinker disconnect is best-effort
            log.debug("detach: signal disconnect raised; ignoring",
                      exc_info=True)
        self._attached = False

    # --- telemetry entry point used by TelemetryPasser ---------------------

    def publish_telemetry(self, fields: dict) -> None:
        if not fields:
            return
        self._client.publish(events.telemetry(fields))

    # --- signal handlers ---------------------------------------------------

    def _on_state_changed(self, _sender, *, to_state, **_unused: Any) -> None:
        name = _state_name(to_state)
        if name is None:
            return
        self._client.send_event(events.state_changed_event(name))

    def _on_job_id_updated(self, _sender, *, job_id: Optional[int] = None,
                           **_unused: Any) -> None:
        if job_id is None:
            return
        self._client.send_event(
            events.job_info_event(job_id=job_id, data={}))

    def _on_job_info_updated(self, _sender, **_unused: Any) -> None:
        job_id = getattr(self._job.data, "job_id", None)
        if job_id is None:
            return
        self._client.send_event(
            events.job_info_event(job_id=job_id, data={}))


def _state_name(state) -> Optional[str]:
    """Render a State enum value as the xBuddy-style upper-case name."""
    if state is None:
        return None
    name = getattr(state, "name", None)
    if isinstance(name, str):
        return name.upper()
    return str(state).upper()
