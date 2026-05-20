"""File transfer reassembly.

xBuddy inline downloads stream T-frames (binary chunks tagged with a
``file_id``) that the printer has previously requested by emitting a
``{"transfer": "inline", ...}`` message. The full flow lives in
``docs/src/content/docs/prusa-xbuddy-websocket-protocol.md``.

Phase 1 of the bridge rejects ``START_INLINE_DOWNLOAD`` (and the
other transfer-initiation commands) at the J-frame dispatcher, so we
should not see any inbound T-frames. This module exists so the client
has a routing target and so a follow-up phase can flesh out the
reassembler without restructuring the surrounding code.
"""

from __future__ import annotations

import logging

from . import protocol

log = logging.getLogger(__name__)


class TransferRouter:
    """Routes T-frames to per-file reassembly buffers.

    Currently a no-op stub: any T-frame we receive in phase 1 means
    the server believes there's an active transfer we didn't actually
    initiate. We log and drop it.
    """

    def handle_frame(self, raw_frame: bytes) -> None:
        try:
            type_letter, file_id, _ = protocol.decode_frame(raw_frame)
        except protocol.FrameError as exc:
            log.warning("Discarding malformed transfer frame: %s", exc)
            return
        if type_letter != protocol.TYPE_TRANSFER_CHUNK:
            log.warning(
                "TransferRouter received non-T frame (%r); ignoring",
                type_letter)
            return
        log.warning(
            "Unexpected T-frame for file_id=%#x; phase 1 does not "
            "support inline downloads. Drop the chunk.",
            file_id)
