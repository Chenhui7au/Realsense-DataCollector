"""Response models.

These mirror docs/API.md section 3 field for field. They are only used at the API
boundary, so the OpenAPI document FastAPI generates at ``/docs`` is an accurate
description of the contract. Internally the stores deal in plain dicts.

Every field documented as nullable is declared ``Optional``. That is what makes
``/docs`` honest about, for example, an unconfigured guide entry where only
``index`` and ``name`` carry a value.
"""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

from .sessions import BAG_NAME


# ------------------------------------------------------------------ sections

class DeviceInfo(BaseModel):
    connected: bool
    name: Optional[str] = None
    serial: Optional[str] = None
    firmware: Optional[str] = None
    usb_type: Optional[str] = None
    reason: Optional[str] = None


class ProjectInfo(BaseModel):
    configured: bool
    root: Optional[str] = None
    name: Optional[str] = None
    exists: bool = False
    writable: bool = False
    free_gb: float = 0.0
    enough: bool = False
    error: Optional[str] = None


class GuidesReadiness(BaseModel):
    ready: bool
    uploaded: int
    total: int
    missing_indices: List[int] = Field(default_factory=list)


class StageArtifact(BaseModel):
    bag_path: str
    size_bytes: int
    duration_s: float
    started_at: str
    stopped_at: str
    streams: List[str] = Field(default_factory=list)
    frame_counts: Dict[str, int] = Field(default_factory=dict)
    thumbnail_url: str


class Stage(BaseModel):
    index: int
    name: str
    instructions: str
    min_duration_s: float
    max_duration_s: float
    state: Literal["idle", "recording", "saved"]
    allowed_actions: List[Literal["start", "stop", "discard", "advance"]] = Field(default_factory=list)
    recording_started_at: Optional[str] = None
    artifact: Optional[StageArtifact] = None


class Session(BaseModel):
    session_id: str
    name: str
    created_at: str
    finished_at: Optional[str] = None
    status: Literal["in_progress", "finished"]
    current_stage: int
    data_dir: str
    operator: str = ""
    note: str = ""
    stages: List[Stage] = Field(default_factory=list)


class SessionSummary(BaseModel):
    session_id: str
    name: str
    created_at: str
    finished_at: Optional[str] = None
    status: Literal["in_progress", "finished"]
    current_stage: int
    saved_count: int
    size_bytes: int
    data_dir: str


class SessionListResponse(BaseModel):
    project_root: Optional[str] = None
    sessions: List[SessionSummary] = Field(default_factory=list)


# ------------------------------------------------------- sessions and recording

class SessionCreateBody(BaseModel):
    """Body of ``POST /api/sessions``. Only the name is required."""

    name: str
    operator: Optional[str] = None
    note: Optional[str] = None


class SessionDeleteResult(BaseModel):
    session_id: str
    deleted: bool
    removed_dir: Optional[str] = None


class PreviewResult(BaseModel):
    streaming: bool
    stream_url: Optional[str] = None
    auto_saved: bool = False


class RecordNoteBody(BaseModel):
    """Body of ``record/start``. Everything is optional."""

    note: Optional[str] = None


class StartRecordResult(BaseModel):
    stage_index: int
    state: Literal["recording"]
    started_at: str
    # Absolute, unlike StageArtifact.bag_path which is session relative.
    bag_abs_path: str
    auto_stop_at_s: float


class StopRecordResult(BaseModel):
    stage_index: int
    state: Literal["saved"]
    artifact: StageArtifact


class DiscardRecordResult(BaseModel):
    stage_index: int
    state: Literal["idle"]
    deleted: List[str] = Field(default_factory=list)


class AdvanceResult(BaseModel):
    session: Session
    # Not a typed model on purpose. docs/API.md section 6.10 specifies two exact
    # shapes, ``{"type": "guide", "stage_index": 2}`` and ``{"type": "finish"}``,
    # and a typed model with an optional field would serialise the finish case as
    # ``{"type": "finish", "stage_index": null}``.
    next: Dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------- health

class Health(BaseModel):
    status: Literal["ok", "degraded"]
    device: DeviceInfo
    project: ProjectInfo
    guides: GuidesReadiness
    active_session: Optional[SessionSummary] = None


# ---------------------------------------------------------------------- config

class PreviewConfig(BaseModel):
    fps: int
    jpeg_quality: int


class RecordingConfig(BaseModel):
    min_duration_s: float
    max_duration_s_default: float
    # The on disk file name, so the screens that describe the output before a take
    # exists do not have to keep their own copy of it. It has to end in .db3, which
    # the SDK enforces, so it is not something the frontend should guess at.
    output_name: str = BAG_NAME
    # Derived from the configured streams, for the running size gauge on the capture
    # screen. A hardcoded guess here was four times too low, which made the gauge
    # worse than useless when the point of it is watching disk usage.
    bytes_per_second: int = 0


class StageConfig(BaseModel):
    index: int
    name: str
    instructions: str
    max_duration_s: float


class AppConfig(BaseModel):
    app_title: str
    total_stages: int
    preview: PreviewConfig
    recording: RecordingConfig
    stages: List[StageConfig] = Field(default_factory=list)


# --------------------------------------------------------------------- project

class ProjectUpdateBody(BaseModel):
    root: str


# ---------------------------------------------------------------- filesystem

class FsEntry(BaseModel):
    name: str
    path: str
    writable: bool
    is_symlink: bool = False
    looks_like_session: bool = False


class FsShortcut(BaseModel):
    name: str
    path: str


class FsListing(BaseModel):
    path: str
    parent: Optional[str] = None
    name: str
    home: str
    shortcuts: List[FsShortcut] = Field(default_factory=list)
    readable: bool = False
    writable: bool = False
    free_gb: float = 0.0
    enough: bool = False
    error: Optional[str] = None
    entries: List[FsEntry] = Field(default_factory=list)


class FsMkdirBody(BaseModel):
    parent: str
    name: str


class FsCreateResult(BaseModel):
    path: str
    listing: FsListing


# ---------------------------------------------------------------------- guides

class GuideEntry(BaseModel):
    index: int
    # Effective title: the operator's own text when they wrote one, the stage name
    # from the YAML otherwise.
    name: str
    name_custom: bool = False
    configured: bool
    image_url: Optional[str] = None
    original_filename: Optional[str] = None
    content_type: Optional[str] = None
    size_bytes: Optional[int] = None
    width: Optional[int] = None
    height: Optional[int] = None
    uploaded_at: Optional[str] = None
    sha256: Optional[str] = None
    # Effective description: the operator's own text when they wrote one, the
    # stage instructions from the YAML otherwise.
    instructions: str = ""
    instructions_custom: bool = False


class GuidesResponse(BaseModel):
    required: bool
    ready: bool
    total: int
    uploaded: int
    missing_indices: List[int] = Field(default_factory=list)
    guides: List[GuideEntry] = Field(default_factory=list)


class GuideUploadResult(GuideEntry):
    generated_preview: bool = False
    ready: bool = False


class GuideUpdateBody(BaseModel):
    """Body of ``PUT /api/guides/{index}``.

    Both fields are optional and independent; only the ones present are applied.
    An empty string clears that override, which is the reset action.
    """

    name: Optional[str] = None
    instructions: Optional[str] = None


class GuideUpdateResult(GuideEntry):
    ready: bool = False
    uploaded: int = 0


class GuideUploadFailure(BaseModel):
    index: int
    code: str
    message: str


class GuideBatchResult(BaseModel):
    applied: List[int] = Field(default_factory=list)
    failed: List[GuideUploadFailure] = Field(default_factory=list)
    guides: GuidesResponse


class GuideDeleteResult(BaseModel):
    index: int
    configured: bool
    ready: bool
    uploaded: int
