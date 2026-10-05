"""Placeholder routes for the parts of the contract that need the camera.

docs/API.md sections 6 and 7 cover sessions, recording and preview. None of them
can be finished or tested without a D435i, so instead of leaving the frontend to
hit a bare 404 that reads like a typo, they answer 501 with a plain explanation.

Delete a line here as its real router lands. Nothing else imports this module.
"""

from __future__ import annotations

from fastapi import APIRouter

from ..errors import ApiError

router = APIRouter(tags=["pending"])

PENDING_MESSAGE = (
    "This endpoint needs the camera, or the session state machine that goes with "
    "it, and is not implemented yet."
)


def _pending(feature: str) -> ApiError:
    return ApiError("NOT_IMPLEMENTED", f"{feature} is not implemented yet.", detail={"feature": feature})


@router.get("/sessions")
def list_sessions() -> None:
    raise _pending("Session listing")


@router.post("/sessions")
def create_session() -> None:
    raise _pending("Session creation")


@router.get("/sessions/{sid}")
def get_session(sid: str) -> None:
    raise _pending("Reading a session")


@router.delete("/sessions/{sid}")
def discard_session(sid: str) -> None:
    raise _pending("Discarding a session")


@router.post("/sessions/{sid}/preview/start")
def start_preview(sid: str) -> None:
    raise _pending("Preview")


@router.post("/sessions/{sid}/preview/stop")
def stop_preview(sid: str) -> None:
    raise _pending("Preview")


@router.post("/sessions/{sid}/stages/{index}/record/start")
def start_record(sid: str, index: int) -> None:
    raise _pending("Recording")


@router.post("/sessions/{sid}/stages/{index}/record/stop")
def stop_record(sid: str, index: int) -> None:
    raise _pending("Recording")


@router.post("/sessions/{sid}/stages/{index}/record/discard")
def discard_record(sid: str, index: int) -> None:
    raise _pending("Recording")


@router.post("/sessions/{sid}/stages/{index}/advance")
def advance_stage(sid: str, index: int) -> None:
    raise _pending("Stage advance")


@router.get("/sessions/{sid}/stages/{index}/artifact")
def stage_artifact(sid: str, index: int) -> None:
    raise _pending("Artifact lookup")


@router.get("/sessions/{sid}/stages/{index}/thumbnail")
def stage_thumbnail(sid: str, index: int) -> None:
    raise _pending("Thumbnails")


@router.get("/preview/stream")
def preview_stream() -> None:
    raise _pending("The MJPEG stream")


@router.get("/preview/snapshot")
def preview_snapshot() -> None:
    raise _pending("Snapshots")
