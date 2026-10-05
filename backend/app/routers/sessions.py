"""Session and recording endpoints, docs/API.md section 6.

Every handler is a plain synchronous function. FastAPI runs those on a thread
pool, which is what docs/API.md section 9.2 asks for: the recording calls block on
the SDK for around a second and must not stall the event loop that serves the
MJPEG stream.

No handler holds the service lock across a pipeline restart. The lock is inside
:class:`~app.capture.CaptureService`, which is the only place that knows which
parts of the work are fast and which are not.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse

from ..capture import CaptureService
from ..errors import ApiError
from ..models import (
    AdvanceResult,
    DiscardRecordResult,
    PreviewResult,
    RecordNoteBody,
    Session,
    SessionCreateBody,
    SessionDeleteResult,
    SessionListResponse,
    StartRecordResult,
    StopRecordResult,
)
from ..services import Services
from .system import get_services

log = logging.getLogger(__name__)

router = APIRouter(tags=["sessions"])


def _capture(services: Services) -> CaptureService:
    return services.capture


@router.get("/sessions", response_model=SessionListResponse)
def list_sessions(services: Services = Depends(get_services)) -> SessionListResponse:
    """Rounds in the current project folder.

    The frontend uses this for the name collision check, so the guarantee that
    matters is that every occupied name appears. Counters for rounds the service
    has not loaded are left at zero on purpose, per docs/API.md section 6.1.
    """
    return SessionListResponse(**_capture(services).list_sessions())


@router.post("/sessions", response_model=Session, status_code=201)
def create_session(
    body: SessionCreateBody,
    services: Services = Depends(get_services),
) -> Session:
    """Start a round. Six preconditions, checked in the documented order."""
    return Session(**_capture(services).create_session(body.name, body.operator, body.note))


@router.get("/sessions/{sid}", response_model=Session)
def get_session(sid: str, services: Services = Depends(get_services)) -> Session:
    """Read a round. Also valid for a finished one, which the finish screen needs."""
    return Session(**_capture(services).get_session(sid))


@router.delete("/sessions/{sid}", response_model=SessionDeleteResult)
def discard_session(sid: str, services: Services = Depends(get_services)) -> SessionDeleteResult:
    """Delete a round and its data. Idempotent, refuses while recording."""
    return SessionDeleteResult(**_capture(services).discard_session(sid))


@router.post("/sessions/{sid}/preview/start", response_model=PreviewResult)
def start_preview(sid: str, services: Services = Depends(get_services)) -> PreviewResult:
    """Open the preview pipeline. Idempotent."""
    return PreviewResult(**_capture(services).start_preview(sid))


@router.post("/sessions/{sid}/preview/stop", response_model=PreviewResult)
def stop_preview(sid: str, services: Services = Depends(get_services)) -> PreviewResult:
    """Close the preview, saving a take in progress first.

    Deliberately takes no request body. The frontend sends this with
    ``navigator.sendBeacon`` on page unload, which cannot set headers or send a
    payload, and declaring a Pydantic body here would make FastAPI answer 422, a
    shape the frontend does not recognise. docs/API.md section 6.6.
    """
    return PreviewResult(**_capture(services).stop_preview(sid))


@router.post("/sessions/{sid}/stages/{index}/record/start", response_model=StartRecordResult)
def start_record(
    sid: str,
    index: int,
    body: RecordNoteBody | None = None,
    services: Services = Depends(get_services),
) -> StartRecordResult:
    """Restart the pipeline with the recorder attached and begin a take.

    The body is optional so a client can post nothing at all.
    """
    note = body.note if body is not None else None
    return StartRecordResult(**_capture(services).start_record(sid, index, note))


@router.post("/sessions/{sid}/stages/{index}/record/stop", response_model=StopRecordResult)
def stop_record(
    sid: str, index: int, services: Services = Depends(get_services)
) -> StopRecordResult:
    """Stop, flush and save. Idempotent so a retry after a lost response succeeds."""
    return StopRecordResult(**_capture(services).stop_record(sid, index))


@router.post(
    "/sessions/{sid}/stages/{index}/record/discard", response_model=DiscardRecordResult
)
def discard_record(
    sid: str, index: int, services: Services = Depends(get_services)
) -> DiscardRecordResult:
    """Delete a take and return the stage to idle. Idempotent."""
    return DiscardRecordResult(**_capture(services).discard_record(sid, index))


@router.post("/sessions/{sid}/stages/{index}/advance", response_model=AdvanceResult)
def advance_stage(
    sid: str, index: int, services: Services = Depends(get_services)
) -> AdvanceResult:
    """Move to the next stage, or finish the round after the last one."""
    return AdvanceResult(**_capture(services).advance(sid, index))


@router.get("/sessions/{sid}/stages/{index}/artifact", response_model=None)
def stage_artifact(sid: str, index: int, services: Services = Depends(get_services)) -> dict:
    """Recorded metadata for one stage."""
    return _capture(services).artifact(sid, index)


@router.get("/sessions/{sid}/stages/{index}/thumbnail")
def stage_thumbnail(sid: str, index: int, services: Services = Depends(get_services)) -> FileResponse:
    """Poster frame of a take, so the collector can confirm it before advancing."""
    path, content_type = _capture(services).thumbnail(sid, index)
    return FileResponse(
        path,
        media_type=content_type,
        headers={"Cache-Control": "no-cache"},
    )


# Kept for symmetry with the other routers, which raise ApiError directly.
__all__ = ["router", "ApiError"]
