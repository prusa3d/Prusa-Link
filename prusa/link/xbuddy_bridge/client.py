"""xBuddy WebSocket client.

Runs an asyncio loop on a dedicated thread, opens
``GET <hostname>:<port>/p/ws`` with the ``Token`` / ``Fingerprint``
headers and ``prusa-connect`` subprotocol, and pumps text/binary
frames between the WebSocket and the bridge's command dispatcher /
transfer reassembler.

All public methods on this class are safe to call from any thread:
they post coroutines onto the dedicated event loop via
``run_coroutine_threadsafe``. That is the only place in Prusa-Link
where asyncio crosses the threading boundary.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import threading
from typing import Any, Awaitable, Callable, Optional

try:
    import websockets
    from websockets.exceptions import WebSocketException
except ImportError:  # pragma: no cover - hard-required at runtime
    websockets = None  # type: ignore[assignment]
    WebSocketException = Exception  # type: ignore[assignment,misc]

from . import protocol

log = logging.getLogger(__name__)

SUBPROTOCOL = "prusa-connect"
WS_PATH = "/p/ws"


class XBuddyClient:
    """Outbound WebSocket client speaking the xBuddy protocol.

    The client is initially passive: ``start()`` starts the asyncio
    thread and begins connection attempts; ``stop()`` shuts it down.
    Incoming frames are forwarded to ``on_frame``. Outgoing telemetry
    and events go through ``publish``/``send_event``."""

    def __init__(
        self,
        *,
        hostname: str,
        port: int,
        tls: bool,
        token: str,
        fingerprint: str,
        on_frame: Callable[[bytes], None],
        reconnect_min: float = 1.0,
        reconnect_max: float = 60.0,
        ping_interval: float = 30.0,
    ) -> None:
        if websockets is None:
            raise RuntimeError(
                "the 'websockets' package is required for xbuddy_bridge; "
                "install it via requirements.txt")

        self._url = self._build_url(hostname, port, tls)
        self._headers = {"Token": token, "Fingerprint": fingerprint}
        self._on_frame = on_frame
        self._reconnect_min = reconnect_min
        self._reconnect_max = reconnect_max
        self._ping_interval = ping_interval

        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._ws: Any = None  # set on the loop thread only
        self._stop_requested = threading.Event()
        self._connected = threading.Event()

    @staticmethod
    def _build_url(hostname: str, port: int, tls: bool) -> str:
        scheme = "wss" if tls else "ws"
        # Port 0 in [service::connect] means "scheme default".
        if port == 0:
            port_part = ""
        else:
            port_part = f":{port}"
        return f"{scheme}://{hostname}{port_part}{WS_PATH}"

    # --- lifecycle (called from the daemon thread) -------------------------

    def start(self) -> None:
        if self._thread is not None:
            raise RuntimeError("XBuddyClient already started")
        self._stop_requested.clear()
        self._thread = threading.Thread(
            target=self._thread_main, name="xbuddy_bridge", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        if self._thread is None:
            return
        self._stop_requested.set()
        loop = self._loop
        if loop is not None and loop.is_running():
            asyncio.run_coroutine_threadsafe(self._shutdown(), loop)
        self._thread.join(timeout=timeout)
        self._thread = None
        self._loop = None

    def is_connected(self) -> bool:
        return self._connected.is_set()

    # --- outbound API (called from any thread) -----------------------------

    def publish(self, payload: dict) -> None:
        """Send a printer -> server JSON message (telemetry, event,
        transfer request). Dropped silently if not connected."""
        self._submit(self._send_text(json.dumps(payload, separators=(",", ":"))))

    def send_event(self, event: dict) -> None:
        """Alias for ``publish`` that documents intent."""
        self.publish(event)

    def send_binary(self, data: bytes) -> None:
        self._submit(self._send_binary(data))

    # --- internals ---------------------------------------------------------

    def _submit(self, coro: Awaitable[None]) -> None:
        loop = self._loop
        if loop is None or not loop.is_running():
            log.debug("xbuddy_bridge not running; dropping outbound message")
            return
        asyncio.run_coroutine_threadsafe(coro, loop)

    def _thread_main(self) -> None:
        loop = asyncio.new_event_loop()
        self._loop = loop
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._run_forever())
        finally:
            with contextlib.suppress(Exception):
                loop.run_until_complete(loop.shutdown_asyncgens())
            loop.close()

    async def _run_forever(self) -> None:
        delay = self._reconnect_min
        while not self._stop_requested.is_set():
            try:
                await self._connect_and_pump()
                delay = self._reconnect_min  # successful session -> reset backoff
            except asyncio.CancelledError:
                break
            except Exception as exc:  # noqa: BLE001 — any WS / network error
                log.warning("xbuddy_bridge session ended: %s", exc)

            if self._stop_requested.is_set():
                break
            log.info("xbuddy_bridge reconnecting in %.1fs", delay)
            try:
                await asyncio.sleep(delay)
            except asyncio.CancelledError:
                break
            delay = min(delay * 2, self._reconnect_max)

    async def _connect_and_pump(self) -> None:
        log.info("xbuddy_bridge connecting to %s", self._url)
        async with websockets.connect(
            self._url,
            additional_headers=self._headers,
            subprotocols=[SUBPROTOCOL],
            ping_interval=self._ping_interval,
            ping_timeout=self._ping_interval,
            max_size=2 ** 16,
        ) as ws:
            self._ws = ws
            self._connected.set()
            try:
                async for message in ws:
                    self._deliver(message)
            finally:
                self._connected.clear()
                self._ws = None

    def _deliver(self, message: Any) -> None:
        if isinstance(message, str):
            data = message.encode("utf-8")
        else:
            data = bytes(message)
        try:
            self._on_frame(data)
        except Exception:  # noqa: BLE001
            log.exception("on_frame raised; continuing")

    async def _send_text(self, text: str) -> None:
        ws = self._ws
        if ws is None:
            return
        try:
            await ws.send(text)
        except WebSocketException as exc:
            log.debug("send_text dropped: %s", exc)

    async def _send_binary(self, data: bytes) -> None:
        ws = self._ws
        if ws is None:
            return
        try:
            await ws.send(data)
        except WebSocketException as exc:
            log.debug("send_binary dropped: %s", exc)

    async def _shutdown(self) -> None:
        ws = self._ws
        if ws is not None:
            with contextlib.suppress(Exception):
                await ws.close()
