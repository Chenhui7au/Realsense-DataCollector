"""YAML loading and startup validation.

Relative paths in the YAML resolve against the directory holding the config
file, not against the process working directory. That way starting the service
from a different shell does not silently point it at a different data folder,
which is the failure mode docs/DESIGN.md warns about.

Validation failures raise :class:`ConfigError` and the service refuses to boot.
A bad stage table is not something to discover halfway through a capture round.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

log = logging.getLogger(__name__)


class ConfigError(Exception):
    """Raised when the configuration is unusable. Startup must stop."""


DEFAULT_STAGE_MAX_DURATION_S = 300.0
DEFAULT_MIN_DURATION_S = 1.0

# Bytes one pixel occupies, per stream format. Anything not listed is treated as
# two byte, which is the safe middle: too high overstates disk use, too low
# understates it, and understating is the one that gets someone into trouble.
FORMAT_BYTES_PER_PIXEL = {
    "z16": 2,
    "y8": 1,
    "y16": 2,
    "bgr8": 3,
    "rgb8": 3,
    "bgra8": 4,
    "rgba8": 4,
}
# motion_xyz32f is three 32 bit floats.
MOTION_SAMPLE_BYTES = 12


class Config:
    """Parsed configuration with the defaults filled in."""

    def __init__(self, raw: Dict[str, Any], source: Path) -> None:
        self.source = source
        self.raw = raw
        base = source.parent

        app = raw.get("app") or {}
        self.app_title: str = str(app.get("title") or "RealSense Data-Collector")
        self.host: str = str(app.get("host") or "127.0.0.1")
        self.port: int = int(app.get("port") or 8000)
        self.cors_origins: List[str] = list(app.get("cors_origins") or [])

        paths = raw.get("paths") or {}
        self.service_dir = self._resolve(paths.get("service_dir") or "./var", base)
        self.guide_root = self._resolve(paths.get("guide_root") or "./var/guides", base)
        self.log_dir = self._resolve(paths.get("log_dir") or "./var/logs", base)
        self.settings_file = self._resolve(
            paths.get("settings_file") or "./var/settings.json", base
        )

        project = raw.get("project") or {}
        default_root = str(project.get("default_root") or "").strip()
        self.default_root: Optional[str] = default_root or None
        self.min_free_space_gb: float = float(project.get("min_free_space_gb", 5))
        self.require_writable: bool = bool(project.get("require_writable", True))

        fs = raw.get("fs") or {}
        configured_roots = fs.get("allow_roots") or ["~"]
        self.allow_roots: List[str] = [
            os.path.normpath(os.path.expanduser(str(root))) for root in configured_roots
        ]

        guides = raw.get("guides") or {}
        self.guides_required: bool = bool(guides.get("required", True))
        self.guide_max_size_bytes: int = int(float(guides.get("max_size_mb", 10)) * 1024 * 1024)
        # Default list is the shipped one in config.yaml. PNG leads it, the rest
        # are the common formats a browser can also render back to the operator.
        self.guide_allowed_types: List[str] = list(
            guides.get("allowed_types")
            or ["image/png", "image/jpeg", "image/webp", "image/bmp", "image/gif"]
        )
        self.guide_instructions_max_length: int = int(guides.get("instructions_max_length", 500))
        # Upper bound on the per-stage title the operator writes on the diagrams
        # screen. Shorter than the description because a title is rendered inline
        # on the rail, the guide heading and the result cards.
        self.guide_name_max_length: int = int(guides.get("name_max_length", 60))
        self.guide_display_max_width: int = int(guides.get("display_max_width", 1600))
        self.guide_recommended_aspect: str = str(guides.get("recommended_aspect") or "4:3")
        # Draw the placeholder diagram set when the guide directory is still
        # empty, so a fresh service can be driven end to end without eight
        # prepared images. Defaults to on because the alternative is a service
        # that refuses to start a session for a reason only the config explains.
        self.guides_seed_defaults: bool = bool(guides.get("seed_defaults", True))
        self.guides_persist: bool = bool(guides.get("persist", True))
        self.guides_rebuild_from_dir: bool = bool(guides.get("rebuild_from_dir", True))
        self.guides_keep_backup: bool = bool(guides.get("keep_backup", True))

        camera = raw.get("camera") or {}
        self.camera_serial: str = str(camera.get("serial") or "").strip()
        # When false the service never opens the camera, it just reports that no
        # device is available. Used by the test suite and by frontend-only work.
        self.camera_probe: bool = bool(camera.get("probe", True))
        self.camera_reset_on_session_end: bool = bool(camera.get("reset_on_session_end", False))
        preview = camera.get("preview") or {}
        self.preview_width: int = int(preview.get("width", 640))
        self.preview_height: int = int(preview.get("height", 480))
        self.preview_fps: int = int(preview.get("fps", 15))
        self.preview_jpeg_quality: int = int(preview.get("jpeg_quality", 80))
        recording = camera.get("recording") or {}
        self.min_duration_s: float = float(recording.get("min_duration_s", DEFAULT_MIN_DURATION_S))
        self.max_duration_default_s: float = float(
            recording.get("max_duration_s", DEFAULT_STAGE_MAX_DURATION_S)
        )
        self.colorize_depth: bool = bool(recording.get("colorize_depth", False))
        self.recording_streams: Dict[str, Any] = dict(recording.get("streams") or {})
        self.recording_bytes_per_s: int = self._estimate_record_rate()

        storage = raw.get("storage") or {}
        self.compress_bag: bool = bool(storage.get("compress_bag", False))
        self.keep_finished_sessions: bool = bool(
            storage.get("keep_finished_sessions", True)
        )

        self.stages: List[Dict[str, Any]] = [dict(item) for item in (raw.get("stages") or [])]

        self._validate_stages()

    # ------------------------------------------------------------- helpers

    def _estimate_record_rate(self) -> int:
        """Bytes per second the configured recording streams produce.

        Uncompressed frames, so it is exact for the image streams and a very close
        lower bound for the IMU, which the SDK writes one sample at a time. Used by
        the capture screen to show how much disk a running take is using; the value
        previously lived in the frontend as a hand measured constant and was four
        times too low, so a gauge meant to warn about disk use understated it.

        Measured against a real D435I with the shipped six stream config: this
        predicts 76.4 MB/s, the device writes 59 to 71 MB/s depending on take
        length. Close enough for a gauge, and it tracks config changes for free.
        """
        total = 0.0
        for spec in self.recording_streams.values():
            if not isinstance(spec, dict):
                continue
            fps = float(spec.get("fps") or 30)
            # A motion sample is three floats, x, y and z.
            if "width" not in spec and "height" not in spec:
                total += MOTION_SAMPLE_BYTES * fps
                continue
            width = float(spec.get("width") or 0)
            height = float(spec.get("height") or 0)
            pixel = FORMAT_BYTES_PER_PIXEL.get(str(spec.get("format") or "").lower(), 2)
            total += width * height * pixel * fps
        return int(round(total))

    @staticmethod
    def _resolve(value: str, base: Path) -> Path:
        expanded = Path(os.path.expanduser(str(value)))
        if not expanded.is_absolute():
            expanded = base / expanded
        return Path(os.path.normpath(str(expanded)))

    def _validate_stages(self) -> None:
        if not self.stages:
            raise ConfigError("stages must define at least one stage")

        indices: List[int] = []
        for position, stage in enumerate(self.stages, start=1):
            raw_index = stage.get("index")
            if not isinstance(raw_index, int) or isinstance(raw_index, bool):
                raise ConfigError(
                    f"stages[{position}].index must be an integer, got {raw_index!r}"
                )
            indices.append(raw_index)

            name = str(stage.get("name") or "").strip()
            if not name:
                raise ConfigError(f"stages[{position}] has no name")

            duration = stage.get("max_duration_s", self.max_duration_default_s)
            try:
                seconds = float(duration)
            except (TypeError, ValueError):
                raise ConfigError(
                    f"stages[{position}].max_duration_s must be a number, got {duration!r}"
                )
            if seconds <= 0:
                raise ConfigError(f"stages[{position}].max_duration_s must be greater than zero")

        duplicates = sorted({i for i in indices if indices.count(i) > 1})
        if duplicates:
            raise ConfigError(f"stages contain duplicate indices: {duplicates}")

        expected = list(range(1, len(self.stages) + 1))
        if sorted(indices) != expected:
            raise ConfigError(
                "stage indices must be contiguous and start at 1, "
                f"expected {expected}, got {sorted(indices)}"
            )

        if self.min_duration_s <= 0:
            raise ConfigError("camera.recording.min_duration_s must be greater than zero")
        if self.max_duration_default_s < self.min_duration_s:
            raise ConfigError(
                "camera.recording.max_duration_s must not be below min_duration_s"
            )

    # -------------------------------------------------------------- stages

    @property
    def total_stages(self) -> int:
        return len(self.stages)

    def stage_indices(self) -> List[int]:
        return [int(stage["index"]) for stage in self.stages]

    def has_stage(self, index: int) -> bool:
        return 1 <= index <= self.total_stages

    def stage_config(self, index: int) -> Optional[Dict[str, Any]]:
        """Config view of one stage, used before a session exists."""
        if not self.has_stage(index):
            return None
        stage = self.stages[index - 1]
        return {
            "index": int(stage["index"]),
            "name": str(stage["name"]),
            "instructions": str(stage.get("instructions") or "").strip(),
            "max_duration_s": float(
                stage.get("max_duration_s", self.max_duration_default_s)
            ),
        }

    def stage_snapshot(self) -> List[Dict[str, Any]]:
        """Frozen copy for ``session.json``, per docs/API.md section 8.4."""
        snapshot: List[Dict[str, Any]] = []
        for index in self.stage_indices():
            item = self.stage_config(index)
            if item is not None:
                item["min_duration_s"] = self.min_duration_s
                snapshot.append(item)
        return snapshot

    # ------------------------------------------------------------- startup

    def ensure_service_dirs(self) -> None:
        """Create the service directory tree. Called once at startup."""
        for directory in (self.service_dir, self.guide_root, self.log_dir):
            try:
                directory.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                raise ConfigError(f"cannot create directory {directory}: {exc}") from exc


def load_config(path: str | os.PathLike) -> Config:
    """Read and validate the YAML file at ``path``."""
    source = Path(path).expanduser().resolve()
    if not source.is_file():
        raise ConfigError(f"config file not found: {source}")

    try:
        text = source.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"cannot read config file {source}: {exc}") from exc

    try:
        raw = yaml.safe_load(text) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"config file {source} is not valid YAML: {exc}") from exc

    if not isinstance(raw, dict):
        raise ConfigError(f"config file {source} must contain a mapping at the top level")

    config = Config(raw, source)
    log.info("config loaded from %s, %d stages", source, config.total_stages)
    return config
