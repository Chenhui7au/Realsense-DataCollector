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


class AudioSync(BaseModel):
    """How the sidecar WAV relates to the video, in one place.

    ``offset_ms`` is positive when the audio starts later than the video. The
    audio is deliberately started first so the take is covered from its first
    frame, so the value is usually negative by the camera's start latency and the
    WAV is correspondingly longer at the head.
    """

    offset_ms: int
    method: str = "monotonic_anchor"
    estimated_error_ms: int = 120


class AudioArtifact(BaseModel):
    """One take's audio. Absent entirely when the take has no audio."""

    file: str
    device: str
    samplerate: int
    channels: int
    sample_format: str = "pcm_s16le"
    duration_s: float
    size_bytes: int
    partial: bool = False
    overruns: int = 0
    peak_dbfs: Optional[float] = None
    sync: AudioSync


class StageArtifact(BaseModel):
    bag_path: str
    size_bytes: int
    duration_s: float
    started_at: str
    stopped_at: str
    streams: List[str] = Field(default_factory=list)
    frame_counts: Dict[str, int] = Field(default_factory=dict)
    thumbnail_url: str
    # A sidecar WAV, not a camera stream. Kept out of ``streams`` on purpose:
    # that list is one entry per recorded device stream with a frame count each.
    audio: Optional[AudioArtifact] = None


class StageReview(BaseModel):
    """The operator's verdict on a take, written on the capture screen.

    ``verdict`` is null until the take is reviewed, which is also what keeps
    ``advance`` out of ``allowed_actions``. ``comment`` is free text and optional.
    """

    verdict: Optional[Literal["passed", "failed"]] = None
    comment: str = ""
    reviewed_at: Optional[str] = None


class Stage(BaseModel):
    index: int
    name: str
    instructions: str
    min_duration_s: float
    max_duration_s: float
    state: Literal["idle", "recording", "saved"]
    allowed_actions: List[Literal["start", "stop", "discard", "advance"]] = Field(default_factory=list)
    recording_started_at: Optional[str] = None
    review: StageReview = Field(default_factory=StageReview)
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


class StageReviewBody(BaseModel):
    """Body of ``stages/{index}/review``.

    The verdict is required: it is the gate in front of ``advance``. The comment
    is optional and its length is checked against the configured limit by the
    service, so the error carries the limit in ``detail``.
    """

    verdict: Literal["passed", "failed"]
    comment: Optional[str] = None


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
    # Not a typed model on purpose. docs/API.md section 6.11 specifies two exact
    # shapes, ``{"type": "guide", "stage_index": 2}`` and ``{"type": "finish"}``,
    # and a typed model with an optional field would serialise the finish case as
    # ``{"type": "finish", "stage_index": null}``.
    next: Dict[str, Any] = Field(default_factory=dict)


class ReviewResult(BaseModel):
    """Returned by the review endpoint.

    Carries the whole session rather than the single stage, so the client can
    replace its state in one step and pick up the freshly recomputed
    ``allowed_actions``. That is what unlocks ``advance`` without a second read.
    """

    session: Session
    stage_index: int


# ---------------------------------------------------------------------- health

class AudioStatus(BaseModel):
    """Audio readiness. Present even when capture is disabled, so the screens can
    explain why instead of hiding the panel."""

    enabled: bool = False
    required: bool = False
    available: bool = False
    device: Optional[str] = None
    reason: Optional[str] = None
    samplerate: Optional[int] = None
    channels: Optional[int] = None


class AudioLive(BaseModel):
    """Live view while a take is running, polled by the capture screen only."""

    enabled: bool = False
    required: bool = False
    available: bool = False
    capturing: bool = False
    device: Optional[str] = None
    samplerate: Optional[int] = None
    channels: Optional[int] = None
    duration_s: float = 0.0
    # Peak of the most recent block, 0..1, for the "is it actually hearing
    # anything" banner. Not a stream, so it cannot lag the audio.
    level: float = 0.0
    # The same peak in dBFS. Null while the block is silent, which is why the
    # screens draw the meter from this rather than from `level`.
    level_dbfs: Optional[float] = None
    # True once any block since the take started rose above the noise floor.
    heard: bool = False
    overruns: int = 0
    error: Optional[str] = None
    reason: Optional[str] = None


class AudioTestLive(BaseModel):
    """Live view of the home screen's input check, polled while it runs.

    ``level_dbfs`` is what the meter draws: a linear amplitude would leave speech
    near the bottom of the bar, because full scale is not a comfortable listening
    level in the first place.
    """

    running: bool = False
    device: Optional[str] = None
    samplerate: Optional[int] = None
    channels: Optional[int] = None
    duration_s: float = 0.0
    # Peak of the most recent block, 0..1.
    level: float = 0.0
    # The same peak in dBFS, null while the block is silent.
    level_dbfs: Optional[float] = None
    # Loudest block since the check started, so a level that only moves briefly
    # is still reported when the check stops.
    peak: int = 0
    peak_dbfs: Optional[float] = None
    heard: bool = False
    overruns: int = 0
    error: Optional[str] = None


class AudioTestResult(BaseModel):
    """What the input check heard between start and stop."""

    device: str
    samplerate: int
    channels: int
    duration_s: float
    overruns: int = 0
    peak: int
    peak_dbfs: Optional[float] = None
    # silent, quiet, ok or clipping. silent means the device opened but delivered
    # digital silence, which is the failure that looks like success everywhere else.
    verdict: Literal["silent", "quiet", "ok", "clipping"]
    # Set when the device failed during the check rather than the check finding
    # nothing, so the screen can tell the two apart.
    error: Optional[str] = None


class Health(BaseModel):
    status: Literal["ok", "degraded"]
    device: DeviceInfo
    project: ProjectInfo
    guides: GuidesReadiness
    audio: AudioStatus = Field(default_factory=AudioStatus)
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


class AudioConfig(BaseModel):
    """What the pre-session screens need to know about audio."""

    enabled: bool = False
    required: bool = False
    samplerate: int = 48000
    channels: int = 1
    # The sidecar name, so a screen can describe the output before a take exists,
    # for the same reason ``recording.output_name`` exists.
    output_name: str = "audio.wav"


class AppConfig(BaseModel):
    app_title: str
    total_stages: int
    preview: PreviewConfig
    recording: RecordingConfig
    audio: AudioConfig = Field(default_factory=AudioConfig)
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
