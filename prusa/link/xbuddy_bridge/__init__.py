"""xBuddy WebSocket bridge.

Speaks the xBuddy printer-facing WebSocket protocol on behalf of the
serial-attached 8-bit printer that Prusa-Link drives. Lets Prusa-Link
talk to cloud Prusa Connect (or any compatible server, e.g. Prusa
Connect Local) over WebSocket instead of HTTP polling.

The bridge is a translation layer only: it consumes the same
TelemetryPasser / StateManager / CommandQueue / handlers the SDK uses,
and reframes that traffic onto a WebSocket. See
``docs/src/content/docs/prusa-xbuddy-websocket-protocol.md`` in the
prusa-connect-local repo for the wire spec.
"""

__all__ = ["XBuddyClient"]


def __getattr__(name):
    if name == "XBuddyClient":
        from .client import XBuddyClient

        return XBuddyClient
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
