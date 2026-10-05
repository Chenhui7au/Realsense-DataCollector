"""Session directories and the persisted session record.

Layout is fixed by docs/API.md section 8.4.

    <project root>/<name>/
      session.json
      stage_01/capture.bag, meta.json, thumb.jpg
      stage_08/...

Four things worth naming.

* The project directory holds session directories and nothing else, so copying it
  away carries a clean dataset. Nothing here writes to the service directory.
* The in-memory state machine is authoritative while the service runs,
  ``session.json`` is its mirror. A restart rebuilds state by scanning the
  project directory, which is what makes a browser refresh and a service restart
  both survivable.
* When a stage is interrupted mid recording the bag is truncated and the SDK
  never flushed it. Rather than delete it, the half file is moved to
  ``recovered/`` and the stage returns to ``idle``. docs/API.md section 8.2.
* A session directory renamed by hand on disk wins over the name recorded inside
  ``session.json``. The operator tidying folders is a reasonable act and must not
  make the data unreadable.

This module owns what is inside the project directory. :class:`~app.project.ProjectStore`
owns whether the project directory itself is usable.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import paths
from .config import Config
from .errors import ApiError

log = logging.getLogger(__name__)

SESSION_FILE = "session.json"
STAGE_DIR_RE = "stage_{:02d}"
RECORD_DIR_NAME = "recovered"
# Written by the SDK and read back for the artifact. The extension is not
# cosmetic: enable_record_to_file rejects anything but .db3, verified against
# SDK 2.58 on a D435I. The docs call it a bag file, which it is in the rosbag2
# sense, but the name on disk has to carry this suffix.
BAG_NAME = "capture.db3"
META_NAME = "meta.json"
THUMB_NAME = "thumb.jpg"

SESSION_SCHEMA_VERSION = 1


def now_iso() -> str:
    """Local time as ISO 8601 with an offset, the format used on the wire."""
    return datetime.now().astimezone().isoformat(timespec="seconds")


def new_session_id(when: Optional[datetime] = None) -> str:
    """``YYYYMMDD-HHMMSS-xxxx``, per docs/API.md section 1.1."""
    stamp = when or datetime.now()
    return f"{stamp:%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:4]}"


def stage_dir_name(index: int) -> str:
    return STAGE_DIR_RE.format(index)


class SessionStore:
    """Creates, reads, lists and deletes session directories."""

    def __init__(self, config: Config) -> None:
        self.config = config
        # docs/API.md section 9.2: the session lock guards the state machine. The
        # store only ever runs inside the service's lock, so it does not keep one
        # of its own. Disk work that is not part of a state transition happens
        # outside it, see :meth:`CaptureService.list_sessions`.
        self._load_lock = threading.RLock()

    # ----------------------------------------------------------------- rules

    def validate_name(self, name: Optional[str]) -> str:
        """Return the accepted name or raise the code from docs/API.md 6.2."""
        candidate = (name or "").strip()
        if not candidate:
            raise ApiError("SESSION_NAME_REQUIRED", "Give this session a name.")
        if not paths.valid_name(candidate):
            raise ApiError(
                "SESSION_NAME_INVALID",
                paths.name_error(candidate),
                detail={"name": candidate},
            )
        return candidate

    def data_dir(self, root: str, name: str) -> str:
        return paths.normalize(os.path.join(root, name))

    # ---------------------------------------------------------------- create

    def create(
        self,
        root: str,
        name: str,
        operator: str,
        note: str,
        stage_snapshot: List[Dict[str, Any]],
        device: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Build the session directory tree and write ``session.json``."""
        data_dir = self.data_dir(root, name)
        if os.path.exists(data_dir):
            raise ApiError(
                "SESSION_DIR_EXISTS",
                f"A folder named {name} already exists in the project.",
                detail={"name": name, "data_dir": data_dir},
            )

        session = {
            "session_id": new_session_id(),
            "name": name,
            "created_at": now_iso(),
            "finished_at": None,
            "status": "in_progress",
            "current_stage": 1,
            "data_dir": data_dir,
            "operator": operator,
            "note": note,
            "stages": [
                {
                    "index": int(item["index"]),
                    "name": str(item["name"]),
                    "instructions": str(item.get("instructions") or ""),
                    "min_duration_s": float(item.get("min_duration_s", self.config.min_duration_s)),
                    "max_duration_s": float(item["max_duration_s"]),
                    "state": "idle",
                    "recording_started_at": None,
                    "artifact": None,
                    "note": "",
                }
                for item in stage_snapshot
            ],
        }

        staged: List[Path] = []
        try:
            # Build the tree somewhere private first, then rename it into place.
            # A failure part way through therefore leaves no half session that the
            # name collision check would trip over.
            staging = Path(root) / f".{name}.{uuid.uuid4().hex[:8]}.creating"
            staging.mkdir(parents=True, exist_ok=False)
            staged.append(staging)
            for stage in session["stages"]:
                (staging / stage_dir_name(stage["index"])).mkdir()
            self._write_session_file(staging / SESSION_FILE, session, device)
            os.replace(staging, data_dir)
            staged.clear()
        except OSError as exc:
            for leftover in staged:
                shutil.rmtree(leftover, ignore_errors=True)
            raise ApiError(
                "PROJECT_PATH_NOT_WRITABLE",
                f"Could not create the session folder: {exc}",
                detail={"data_dir": data_dir},
            ) from exc

        log.info("session %s created at %s", session["session_id"], data_dir)
        return session

    def _write_session_file(
        self, target: Path, session: Dict[str, Any], device: Dict[str, Any]
    ) -> None:
        """Persist the session record.

        ``config_snapshot`` carries the frozen stage table *and* each stage's
        state. The extra state and note fields are deliberate: docs/API.md
        section 8.4 says the on-disk files are a superset of the wire format, and
        writing the state is what lets a restart tell a stage that was mid
        recording apart from one that finished cleanly. Without it the only
        signal left is a truncated file, which is indistinguishable from a
        complete take whose metadata went missing.
        """
        from . import __version__

        payload = {
            "version": SESSION_SCHEMA_VERSION,
            "session_id": session["session_id"],
            "name": session["name"],
            "created_at": session["created_at"],
            "finished_at": session.get("finished_at"),
            "status": session["status"],
            "current_stage": session["current_stage"],
            "operator": session.get("operator", ""),
            "note": session.get("note", ""),
            "device": {
                "name": device.get("name"),
                "serial": device.get("serial"),
                "firmware": device.get("firmware"),
            },
            "app_version": __version__,
            "config_snapshot": [
                {
                    "index": stage["index"],
                    "name": stage["name"],
                    "instructions": stage["instructions"],
                    "min_duration_s": stage["min_duration_s"],
                    "max_duration_s": stage["max_duration_s"],
                    "state": stage["state"],
                    "note": stage.get("note", ""),
                }
                for stage in session["stages"]
            ],
        }
        temp = target.with_name(f".{SESSION_FILE}.{uuid.uuid4().hex[:8]}.tmp")
        try:
            with open(temp, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2, ensure_ascii=False)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp, target)
        except OSError as exc:
            try:
                temp.unlink(missing_ok=True)
            except OSError:
                pass
            raise ApiError(
                "PROJECT_PATH_NOT_WRITABLE",
                f"Could not write the session record: {exc}",
                detail={"file": str(target)},
            ) from exc

    def save(self, session: Dict[str, Any], device: Optional[Dict[str, Any]] = None) -> None:
        """Mirror the live session to disk. Called after every state change."""
        target = Path(session["data_dir"]) / SESSION_FILE
        self._write_session_file(target, session, device or {})

    def save_meta(self, session: Dict[str, Any], index: int) -> None:
        """Write ``stage_XX/meta.json``. A superset of the artifact response."""
        stage = self.stage_of(session, index)
        artifact = stage.get("artifact") or {}
        payload = {
            "version": SESSION_SCHEMA_VERSION,
            "stage_index": index,
            "name": stage["name"],
            "state": stage["state"],
            "started_at": artifact.get("started_at"),
            "stopped_at": artifact.get("stopped_at"),
            "duration_s": artifact.get("duration_s"),
            "bag_file": BAG_NAME,
            "size_bytes": artifact.get("size_bytes"),
            "sha256": artifact.get("sha256"),
            "streams": artifact.get("streams", []),
            "frame_counts": artifact.get("frame_counts", {}),
            "note": stage.get("note", ""),
        }
        directory = Path(session["data_dir"]) / stage_dir_name(index)
        directory.mkdir(parents=True, exist_ok=True)
        temp = directory / f".{META_NAME}.{uuid.uuid4().hex[:8]}.tmp"
        try:
            with open(temp, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2, ensure_ascii=False)
                handle.write("\n")
            os.replace(temp, directory / META_NAME)
        except OSError as exc:
            log.warning("could not write meta.json for stage %d: %s", index, exc)

    # ------------------------------------------------------------------ read

    @staticmethod
    def stage_of(session: Dict[str, Any], index: int) -> Dict[str, Any]:
        for stage in session.get("stages", []):
            if int(stage["index"]) == int(index):
                return stage
        raise ApiError(
            "STAGE_NOT_FOUND",
            detail={"stage_index": index, "total_stages": len(session.get("stages", []))},
        )

    def stage_dirs(self, session: Dict[str, Any]) -> List[int]:
        directory = Path(session["data_dir"])
        found: List[int] = []
        for stage in session.get("stages", []):
            if (directory / stage_dir_name(stage["index"])).is_dir():
                found.append(int(stage["index"]))
        return found

    def read_record(self, session_dir: Path) -> Optional[Dict[str, Any]]:
        """Read ``session.json``. Returns ``None`` when it is absent or unusable."""
        path = session_dir / SESSION_FILE
        if not path.is_file():
            return None
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            log.warning("session record %s is unreadable: %s", path, exc)
            return None
        return raw if isinstance(raw, dict) else None

    def looks_like_session(self, directory: Path) -> bool:
        """A directory is a session when it carries a session record."""
        return (directory / SESSION_FILE).is_file()

    # ------------------------------------------------------------------ scan

    def scan(self, root: str, stage_snapshot: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Rebuild sessions from the project directory.

        Used at startup and after a project directory change. Anything already
        known by the caller is left alone, so a live session is never replaced by
        a stale copy read from disk. Recording stages are recovered as described
        in the module docstring.
        """
        try:
            entries = sorted(os.scandir(root), key=lambda e: e.name.lower())
        except OSError as exc:
            log.warning("cannot scan the project directory %s: %s", root, exc)
            return []

        found: List[Dict[str, Any]] = []
        with self._load_lock:
            for entry in entries:
                if not entry.is_dir(follow_symlinks=False) or entry.name.startswith("."):
                    continue
                directory = Path(entry.path)
                if not self.looks_like_session(directory):
                    continue
                session = self.load(directory, root, stage_snapshot)
                if session is not None:
                    found.append(session)
        return found

    def load(
        self, directory: Path, root: str, stage_snapshot: List[Dict[str, Any]]
    ) -> Optional[Dict[str, Any]]:
        """Rebuild one session from disk, recovering an interrupted recording."""
        record = self.read_record(directory)
        if record is None:
            return None

        name = directory.name
        if str(record.get("name") or "") != name:
            # Hand renamed folder. The directory is authoritative, per the module
            # docstring, so correct the record instead of refusing to load it.
            log.warning(
                "session folder %s records the name %r, using the folder name",
                directory,
                record.get("name"),
            )
            record["name"] = name
            record["data_dir"] = str(directory)
            record["_needs_save"] = True

        record.setdefault("status", "in_progress")
        record.setdefault("current_stage", 1)
        record["data_dir"] = str(directory)
        if not record.get("session_id"):
            record["session_id"] = name
        if not record.get("created_at"):
            record["created_at"] = now_iso()
        record.setdefault("operator", "")
        record.setdefault("note", "")

        record["stages"] = self._rebuild_stages(record, directory, stage_snapshot)
        record["current_stage"] = self._clamp_current_stage(record)
        return record

    def _rebuild_stages(
        self,
        record: Dict[str, Any],
        directory: Path,
        stage_snapshot: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """Merge the frozen snapshot with what is actually on disk.

        The snapshot is the source of truth for text and limits, because
        docs/API.md section 3.2 promises a session is self describing. The disk is
        the source of truth for whether a take exists.

        Deciding "saved" needs both signals, and the order matters:

        * The record says ``recording`` for a stage. The process was killed mid
          take, so the bag is truncated and is moved aside, per the recovery table
          in docs/API.md section 8.2.
        * The bag exists, is not empty, and ``meta.json`` is present. That is a
          completed take, because ``meta.json`` is only written after the SDK has
          flushed.
        * The bag exists with no ``meta.json`` and the record does not claim the
          stage was saved. Something went wrong that was not recorded, so the file
          is treated the same way as an interrupted take rather than being
          presented to the collector as a usable result.
        """
        frozen = [
            item
            for item in (record.get("config_snapshot") or [])
            if isinstance(item, dict) and isinstance(item.get("index"), int)
        ]
        # An older or hand made file may carry no snapshot at all. Falling back to
        # the live configuration keeps such a folder readable.
        source = frozen or stage_snapshot

        stages: List[Dict[str, Any]] = []
        for item in source:
            index = int(item["index"])
            stage = {
                "index": index,
                "name": str(item["name"]),
                "instructions": str(item.get("instructions") or ""),
                "min_duration_s": float(item.get("min_duration_s", self.config.min_duration_s)),
                "max_duration_s": float(item["max_duration_s"]),
                "state": "idle",
                "recording_started_at": None,
                "artifact": None,
                "note": str(item.get("note") or ""),
            }

            if str(item.get("state") or "") == "recording":
                self._recover_interrupted_stage(directory, index)
                stages.append(stage)
                continue

            artifact = self._artifact_from_disk(directory, index)
            if artifact is not None:
                stage["state"] = "saved"
                stage["artifact"] = artifact
            elif self._has_unfinished_bag(directory, index):
                log.warning(
                    "stage %d has a recording with no metadata, treating it as interrupted",
                    index,
                )
                self._recover_interrupted_stage(directory, index)
            stages.append(stage)
        return stages

    @staticmethod
    def _has_unfinished_bag(directory: Path, index: int) -> bool:
        bag = directory / stage_dir_name(index) / BAG_NAME
        try:
            return bag.is_file() and bag.stat().st_size > 0
        except OSError:
            return False

    def _artifact_from_disk(self, directory: Path, index: int) -> Optional[Dict[str, Any]]:
        """Rebuild the artifact for a completed take, or ``None``.

        ``meta.json`` is the completion marker. It is written only after the SDK
        has flushed the bag, so its presence is what distinguishes a real take
        from a truncated one.
        """
        stage_dir = directory / stage_dir_name(index)
        bag = stage_dir / BAG_NAME
        meta_path = stage_dir / META_NAME
        if not bag.is_file() or not meta_path.is_file():
            return None

        try:
            size = bag.stat().st_size
        except OSError:
            return None
        if size <= 0:
            return None

        meta: Dict[str, Any] = {}
        try:
            loaded = json.loads(meta_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                meta = loaded
        except (OSError, ValueError) as exc:
            log.warning("meta.json for stage %d is unreadable: %s", index, exc)
            return None

        thumb = stage_dir / THUMB_NAME
        return {
            "bag_path": os.path.join(stage_dir_name(index), BAG_NAME),
            "size_bytes": int(meta.get("size_bytes") or size),
            "duration_s": float(meta.get("duration_s") or 0.0),
            "started_at": meta.get("started_at"),
            "stopped_at": meta.get("stopped_at"),
            "streams": list(meta.get("streams") or []),
            "frame_counts": dict(meta.get("frame_counts") or {}),
            "sha256": meta.get("sha256"),
            "thumbnail_url": (
                f"/api/sessions/{directory.name}/stages/{index}/thumbnail"
                if thumb.is_file()
                else None
            ),
        }

    def _recover_interrupted_stage(self, directory: Path, index: int) -> None:
        """Move a truncated bag out of the way so the stage can be re-recorded."""
        stage_dir = directory / stage_dir_name(index)
        moved: List[str] = []
        for name in (BAG_NAME, META_NAME, THUMB_NAME):
            source = stage_dir / name
            if not source.is_file():
                continue
            target_dir = stage_dir / RECORD_DIR_NAME
            try:
                target_dir.mkdir(parents=True, exist_ok=True)
                target = target_dir / f"{source.stem}.interrupted{source.suffix}"
                if target.exists():
                    target = target_dir / f"{source.stem}.interrupted-{uuid.uuid4().hex[:4]}{source.suffix}"
                os.replace(source, target)
                moved.append(name)
            except OSError as exc:
                log.warning("could not move %s aside: %s", source, exc)
        if moved:
            log.warning(
                "stage %d was interrupted mid recording, moved %s into %s/",
                index,
                ", ".join(moved),
                RECORD_DIR_NAME,
            )

    def _clamp_current_stage(self, record: Dict[str, Any]) -> int:
        """Keep ``current_stage`` in range and past every saved stage.

        A folder restored from disk may carry a stale value, for example after
        someone deletes a stage folder by hand.
        """
        stages = record.get("stages") or []
        if not stages:
            return 1
        saved = [int(s["index"]) for s in stages if s.get("state") == "saved"]
        candidate = int(record.get("current_stage") or 1)
        lowest = (max(saved) + 1) if saved else 1
        candidate = max(candidate, lowest, 1)
        return min(candidate, len(stages))

    # ---------------------------------------------------------------- delete

    def delete(self, session: Dict[str, Any]) -> str:
        """Remove the session directory. Returns the path that was removed."""
        data_dir = str(session["data_dir"])
        try:
            shutil.rmtree(data_dir)
        except FileNotFoundError:
            # Already gone. The endpoint is idempotent, so this is success.
            log.info("session directory %s was already gone", data_dir)
        except OSError as exc:
            raise ApiError(
                "PROJECT_PATH_NOT_WRITABLE",
                f"Could not remove the session folder: {exc}",
                detail={"data_dir": data_dir},
            ) from exc
        log.info("session %s removed from %s", session.get("session_id"), data_dir)
        return data_dir

    def remove_stage_files(self, session: Dict[str, Any], index: int) -> List[str]:
        """Delete a stage's bag, metadata and thumbnail. Missing files are fine."""
        stage_dir = Path(session["data_dir"]) / stage_dir_name(index)
        deleted: List[str] = []
        for name in (BAG_NAME, META_NAME, THUMB_NAME):
            target = stage_dir / name
            try:
                if target.is_file():
                    target.unlink()
                    deleted.append(name)
            except OSError as exc:
                log.warning("could not delete %s: %s", target, exc)
        return deleted

    # ---------------------------------------------------------------- summary

    @staticmethod
    def summary(session: Dict[str, Any]) -> Dict[str, Any]:
        """The compact form used by the home screen and the session list."""
        saved = [s for s in session.get("stages", []) if s.get("artifact")]
        return {
            "session_id": session["session_id"],
            "name": session["name"],
            "created_at": session["created_at"],
            "finished_at": session.get("finished_at"),
            "status": session["status"],
            "current_stage": int(session["current_stage"]),
            "saved_count": len(saved),
            "size_bytes": int(sum(int(s["artifact"].get("size_bytes") or 0) for s in saved)),
            "data_dir": session["data_dir"],
        }

    @staticmethod
    def occupied_names(sessions: List[Dict[str, Any]]) -> List[str]:
        return sorted({str(s["name"]) for s in sessions})

    def disk_only_summaries(
        self, root: str, known: List[str]
    ) -> List[Dict[str, Any]]:
        """Directories under the root that the service has not loaded.

        docs/API.md section 6.1 wants these listed so the name is known to be
        taken. Counters are left at zero because nothing opens them to count.
        """
        try:
            entries = sorted(os.scandir(root), key=lambda e: e.name.lower())
        except OSError:
            return []
        known_lower = {name.lower() for name in known}
        found: List[Dict[str, Any]] = []
        for entry in entries:
            if not entry.is_dir(follow_symlinks=False) or entry.name.startswith("."):
                continue
            if entry.name.lower() in known_lower:
                continue
            directory = Path(entry.path)
            record = self.read_record(directory)
            finished = bool(record and record.get("finished_at"))
            found.append(
                {
                    "session_id": str((record or {}).get("session_id") or entry.name),
                    "name": entry.name,
                    "created_at": str((record or {}).get("created_at") or now_iso()),
                    "finished_at": (record or {}).get("finished_at"),
                    "status": "finished" if finished or record is None else "in_progress",
                    "current_stage": int((record or {}).get("current_stage") or 1),
                    "saved_count": 0,
                    "size_bytes": 0,
                    "data_dir": str(directory),
                }
            )
        return found
