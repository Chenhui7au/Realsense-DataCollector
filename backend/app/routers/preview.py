"""Preview endpoints, docs/API.md section 7.

The stream is ``multipart/x-mixed-replace``, which an ``img`` tag consumes with no
JavaScript at all. That is the whole reason it is shaped this way rather than as a
WebSocket, see docs/DESIGN.md section 2.3.

Two details worth keeping.

* **The generator waits on the frame sequence number, it does not poll.** The
  capture thread publishes into a single slot and notifies. If frames arrive
  faster than the client reads them the older ones are simply overwritten, so the
  picture can never drift into the past.
* **The response is not JSON and carries no error envelope.** Once the first byte
  is on the wire the status code is already sent, so a failure mid stream can only
  be expressed by ending the body. The frontend treats a closed stream as a
  signal to show its own message, per docs/FRONTEND.md section 8.4.
"""

from __future__ import annotations

import asyncio
import logging
from typing import AsyncIterator

from fastapi import APIRouter, Depends
from fastapi.responses import Response, StreamingResponse

from ..capture import CaptureService
from ..services import Services
from .system import get_services

log = logging.getLogger(__name__)

router = APIRouter(tags=["preview"])

BOUNDARY = "frame"

# How long the generator waits for a new frame before looping. It exists so the
# generator can notice that the client went away, not to pace the stream.
FRAME_TIMEOUT_S = 2.0

# A stream with no frames for this long is ended rather than held open forever.
# The browser reconnects or the collector sees the placeholder, either is better
# than a silently frozen image.
IDLE_LIMIT_S = 15.0


def _capture(services: Services) -> CaptureService:
    return services.capture


@router.get("/preview/stream")
def preview_stream(services: Services = Depends(get_services)) -> StreamingResponse:
    """MJPEG. Yields the newest frame each time the sequence number advances."""
    capture = _capture(services)
    camera = capture.camera

    async def frames() -> AsyncIterator[bytes]:
        sequence = 0
        idle = 0.0
        while True:
            # The worker signals from another thread, so the blocking wait is
            # handed to a worker thread instead of holding the event loop.
            result = await asyncio.to_thread(
                camera.wait_for_frame, sequence, FRAME_TIMEOUT_S
            )
            if result is None:
                idle += FRAME_TIMEOUT_S
                if idle >= IDLE_LIMIT_S:
                    log.info("preview stream idle for %.0fs, closing it", idle)
                    return
                continue
            sequence, payload = result
            idle = 0.0
            yield (
                b"--" + BOUNDARY.encode() + b"\r\n"
                b"Content-Type: image/jpeg\r\n"
                b"Content-Length: " + str(len(payload)).encode() + b"\r\n\r\n"
                + payload
                + b"\r\n"
            )

    return StreamingResponse(
        frames(),
        media_type=f"multipart/x-mixed-replace; boundary={BOUNDARY}",
        headers={
            # Proxies and browsers must not buffer this or it stops being live.
            "Cache-Control": "no-store, no-cache, must-revalidate",
            "Pragma": "no-cache",
            "Connection": "close",
        },
    )


@router.get("/preview/snapshot")
def preview_snapshot(services: Services = Depends(get_services)) -> Response:
    """One JPEG. The degraded path when MJPEG is unavailable, and script friendly."""
    payload = _capture(services).snapshot()
    return Response(
        content=payload,
        media_type="image/jpeg",
        headers={"Cache-Control": "no-store"},
    )
