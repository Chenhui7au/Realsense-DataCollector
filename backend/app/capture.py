"""The session and stage state machine, and the orchestration around it.

This is docs/API.md section 9.1's ``CaptureService`` and it is the only place that
decides what a stage is allowed to do. Three rules from docs/DESIGN.md section 3.1
and section 9.2 shape it.

* **One lock guards the state machine, not the disk.** Every transition happens
  under ``self._lock``. Anything slow, a project directory probe or writing a bag
  sized file, happens outside it. A probe under the lock would block a recording
  command behind a filesystem call.
* **The camera is a separate owner.** Pipeline work goes to
  :class:`~app.camera.CameraWorker`, which has its own thread and its own
  condition variable. Nothing here holds the session lock across a pipeline
  restart either, because that is a one second operation.
* **Exactly one session at a time.** ``SESSION_ACTIVE_EXISTS`` is what enforces
  it, and it is the reason the service needs no quota logic.

``allowed_actions`` is derived here and nowhere else, which is what
docs/API.md section 3.1 means by a single source of truth. The frontend only
renders what it is given.
"""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import paths
from .audio import AudioWorker, DisabledAudio, dbfs
from .camera import CameraWorker
from .config import Config
from .device import DeviceProbe
from .errors import ApiError
from .guides import GuideStore
from .project import ProjectStore
from .sessions import (
    AUDIO_NAME,
    BAG_NAME,
    REVIEW_VERDICTS,
    THUMB_NAME,
    SessionStore,
    empty_review,
    normalize_review,
    now_iso,
    review_verdict,
    stage_dir_name,
)

log = logging.getLogger(__name__)

# Actions per stage state, docs/API.md section 3.2. Not a suggestion: the frontend
# disables buttons from this list, so a wrong value here is a wrong UI.
#
# ``advance`` is deliberately absent for ``saved``: a take must carry a passed or
# failed verdict first, and :meth:`CaptureService._allowed_actions` appends it once
# that verdict exists. Keeping the gate there rather than in the route means the
# button state and the 409 agree by construction.
ALLOWED_ACTIONS: Dict[str, List[str]] = {
    "idle": ["start"],
    "recording": ["stop"],
    "saved": ["discard"],
}

# How long a stopped pipeline is given to release the bag before the file is
# opened for a size check. Windows keeps a handle briefly after pipe.stop().
FILE_SETTLE_S = 0.2

# How long a single frame request waits for a cold pipeline. Opening the colour
# stream takes a few hundred milliseconds, so this covers a start plus a slow
# first frame without hiding a real failure.
SNAPSHOT_TIMEOUT_S = 8.0


class CaptureService:
    """Owns the live session and coordinates the camera, stores and disk."""

    def __init__(
        self,
        config: Config,
        sessions: SessionStore,
        project: ProjectStore,
        guides: GuideStore,
        device: DeviceProbe,
        camera: CameraWorker,
        audio: Optional[AudioWorker] = None,
    ) -> None:
        self.config = config
        self.sessions = sessions
        self.project = project
        self.guides = guides
        self.device = device
        self.camera = camera
        # Optional so a caller that only exercises the camera state machine, such
        # as the session tests, does not have to build an audio device.
        self.audio = audio if audio is not None else DisabledAudio(config)

        # The state machine lock. Held only for in memory transitions.
        self.lock = threading.RLock()
        self._session: Optional[Dict[str, Any]] = None
        # Guards the pipeline restart window so two browsers cannot both start a
        # take, and so a discard cannot race a stop.
        self._camera_lock = threading.RLock()
        self._auto_stopped = False
        camera.set_auto_stop_callback(self._on_auto_stop)

    # ------------------------------------------------------------ snapshot

    def _stage_snapshot(self) -> List[Dict[str, Any]]:
        """The frozen per stage config for a new session.

        Starts from the YAML and then applies the operator's titles and
        descriptions from the guides screen, so a session created now carries the
        wording that is actually on screen. docs/API.md section 5.6 keeps this one
        directional: editing either later does not reach into a round already
        running.
        """
        snapshot = self.config.stage_snapshot()
        for item in snapshot:
            index = int(item["index"])
            item["name"] = self.guides.effective_name(index)
            item["instructions"] = self.guides.effective_instructions(index)
        return snapshot

    # ------------------------------------------------------------ startup

    def bootstrap(self) -> None:
        """Recover an interrupted session and start the camera thread.

        docs/API.md section 8.2 step eight. A session left ``in_progress`` is
        adopted so a browser refresh or a service restart does not strand it.
        """
        self.camera.start()
        root = self.project.root
        if not root:
            return
        with self.lock:
            found = self.sessions.scan(root, self.config.stage_snapshot())
            live = [s for s in found if s.get("status") == "in_progress"]
            if live:
                # Newest first so the most recent round is the one adopted.
                live.sort(key=lambda s: str(s.get("created_at") or ""), reverse=True)
                self._session = live[0]
                log.info(
                    "recovered session %s with %d of %d stages saved",
                    self._session["name"],
                    sum(1 for s in self._session["stages"] if s.get("artifact")),
                    len(self._session["stages"]),
                )
            self._forget_stale(found, keep=self._session)

    def _forget_stale(
        self, found: List[Dict[str, Any]], keep: Optional[Dict[str, Any]]
    ) -> None:
        """Normalise finished rounds on disk.

        A round that already finished should carry an end stamp. One that does not
        was interrupted after its last stage, so the stamp is filled in from the
        last stage's stop time rather than left null forever.
        """
        for session in found:
            if keep is not None and session["session_id"] == keep["session_id"]:
                continue
            if session.get("status") == "finished" and session.get("finished_at"):
                continue
            stamped = [s for s in session["stages"] if s.get("artifact")]
            if len(stamped) != len(session["stages"]):
                continue
            session["status"] = "finished"
            session["finished_at"] = (
                session.get("finished_at")
                or (stamped[-1]["artifact"].get("stopped_at") if stamped else None)
                or now_iso()
            )
            session["current_stage"] = len(session["stages"])
            try:
                self.sessions.save(session)
            except ApiError as exc:
                log.warning("could not normalise %s: %s", session.get("name"), exc.message)

    def shutdown(self) -> None:
        self.audio.shutdown()
        self.camera.shutdown()

    # ------------------------------------------------------------- sessions

    @property
    def session(self) -> Optional[Dict[str, Any]]:
        return self._session

    def active_summary(self) -> Optional[Dict[str, Any]]:
        """The in-progress session, for the ``active_session`` field of /api/health.

        docs/API.md section 4.1 uses this so a second browser, a new tab, or a
        reloaded page can reattach to a running round. Without it the frontend has
        no idea a session exists, so a fresh page lands on home and offers to start
        a new round that the backend then refuses with SESSION_ACTIVE_EXISTS.
        """
        with self.lock:
            session = self._session
            if session is None:
                return None
            if str(session.get("status")) == "finished":
                return None
            return self.sessions.summary(session)

    def list_sessions(self) -> Dict[str, Any]:
        """Live session plus whatever else sits in the project directory.

        docs/API.md section 6.1. The disk walk happens outside the state machine
        lock, and the counters for unloaded directories are deliberately left at
        zero because nothing opens them to count.
        """
        root = self.project.root
        if not root:
            return {"project_root": None, "sessions": []}

        with self.lock:
            live = self._session

        summaries: List[Dict[str, Any]] = []
        known: List[str] = []
        if live is not None:
            summaries.append(self.sessions.summary(live))
            known.append(str(live["name"]))

        loaded = self.sessions.scan(root, self.config.stage_snapshot())
        for session in loaded:
            if live is not None and session["session_id"] == live["session_id"]:
                continue
            if session["name"] in known:
                continue
            summaries.append(self.sessions.summary(session))
            known.append(str(session["name"]))

        summaries.extend(self.sessions.disk_only_summaries(root, known))
        summaries.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
        return {"project_root": root, "sessions": summaries}

    def create_session(
        self, name: Optional[str], operator: Optional[str], note: Optional[str]
    ) -> Dict[str, Any]:
        """Six checks in the order docs/API.md section 6.2 fixes.

        The camera comes first because it is the only precondition the collector
        cannot fix from the page they are looking at.
        """
        # 1. Camera.
        info = self.device.info(force=True)
        if not info.get("connected"):
            # A device that is present but unopenable is a different problem from
            # nothing being plugged in, and the operator's next action differs.
            # Kinds come from app.device.REASONS.
            absent_kinds = {"no_device", "sdk_missing", "disabled", "timeout", "None"}
            kind = self.device.last_kind
            if kind in absent_kinds or kind is None:
                raise ApiError("DEVICE_NOT_FOUND", info.get("reason"), detail={"reason": kind})
            raise ApiError("DEVICE_BUSY", info.get("reason"), detail={"reason": kind})

        # 2. Project directory.
        root = self.project.usable_root()

        # 3. No round in progress.
        with self.lock:
            if self._session is not None:
                raise ApiError(
                    "SESSION_ACTIVE_EXISTS",
                    detail={"session": self.sessions.summary(self._session)},
                )

        # 4. Diagrams.
        readiness = self.guides.readiness()
        if readiness["required"] and not readiness["ready"]:
            raise ApiError(
                "GUIDES_INCOMPLETE",
                detail={"missing_indices": readiness["missing_indices"]},
            )

        # 5. Name.
        accepted = self.sessions.validate_name(name)

        # 6. No folder with that name, then create. The existence check inside
        # create() is the authoritative one, this one only sharpens the message.
        session = self.sessions.create(
            root=root,
            name=accepted,
            operator=(operator or "").strip(),
            note=(note or "").strip(),
            stage_snapshot=self._stage_snapshot(),
            device=info,
        )
        with self.lock:
            self._session = session
            public = self._public(session)
        return public

    def get_session(self, sid: str) -> Dict[str, Any]:
        """Read a session. A finished round stays readable for the finish screen."""
        with self.lock:
            session = self._session
            if session is not None and self._matches(session, sid):
                return self._public(session)

        loaded = self._load_from_disk(sid)
        if loaded is None:
            raise ApiError("SESSION_NOT_FOUND", detail={"session_id": sid})
        return self._public(loaded)

    def discard_session(self, sid: str) -> Dict[str, Any]:
        """Delete the round. Idempotent, and refuses mid recording.

        docs/API.md section 6.4 wants a recording take protected, and wants a
        second call after a successful one to still succeed.
        """
        with self.lock:
            session = self._session
            if session is not None and self._matches(session, sid):
                if any(s["state"] == "recording" for s in session["stages"]):
                    raise ApiError(
                        "STAGE_ALREADY_RECORDING",
                        "Stop the current take before discarding the round.",
                        detail={
                            "stage_index": next(
                                s["index"] for s in session["stages"] if s["state"] == "recording"
                            )
                        },
                    )
                removed = self.sessions.delete(session)
                self._session = None
                return {"session_id": sid, "deleted": True, "removed_dir": removed}

        loaded = self._load_from_disk(sid)
        if loaded is None:
            # Already gone. DELETE is idempotent, so this is success.
            return {"session_id": sid, "deleted": True, "removed_dir": None}
        removed = self.sessions.delete(loaded)
        return {"session_id": sid, "deleted": True, "removed_dir": removed}

    # -------------------------------------------------------------- preview

    def start_preview(self, sid: str) -> Dict[str, Any]:
        """Open the preview pipeline. Idempotent, per docs/API.md section 6.5."""
        session = self._require_live(sid)
        if session["status"] == "finished":
            raise ApiError("SESSION_FINISHED", detail={"session_id": sid})
        with self._camera_lock:
            self.camera.start_preview()
        return {"streaming": True, "stream_url": "/api/preview/stream"}

    def stop_preview(self, sid: str) -> Dict[str, Any]:
        """Close the preview, auto saving a take in progress.

        Idempotent and tolerant of an unknown session: docs/API.md section 6.6
        has the browser send this on unload with ``sendBeacon``, so there is
        nobody left to read an error.
        """
        auto_saved = False
        with self.lock:
            session = self._session
            recording = None
            if session is not None and self._matches(session, sid):
                recording = next(
                    (s for s in session["stages"] if s["state"] == "recording"), None
                )

        if recording is not None:
            try:
                self.stop_record(sid, int(recording["index"]))
                auto_saved = True
            except ApiError as exc:
                log.warning("auto save while stopping the preview failed: %s", exc.message)

        with self._camera_lock:
            self.camera.stop()
            self.camera.clear_frames()
        return {"streaming": False, "auto_saved": auto_saved}

    # ------------------------------------------------------------ recording

    def start_record(self, sid: str, index: int, note: Optional[str]) -> Dict[str, Any]:
        """Restart the pipeline with a recorder attached. docs/API.md section 6.7."""
        with self.lock:
            session = self._require_live(sid)
            if session["status"] == "finished":
                raise ApiError("SESSION_FINISHED", detail={"session_id": sid})
            stage = self.sessions.stage_of(session, index)
            self._require_action(stage, "start")

            free = paths.format_gb(paths.free_bytes(session["data_dir"]))
            if free < self.config.min_free_space_gb:
                raise ApiError(
                    "DISK_SPACE_LOW",
                    detail={"free_gb": free, "required_gb": self.config.min_free_space_gb},
                )

            stage_dir = Path(session["data_dir"]) / stage_dir_name(index)
            bag_path = stage_dir / BAG_NAME
            audio_path = stage_dir / AUDIO_NAME
            limit = float(stage["max_duration_s"])
            session_id = session["session_id"]

        stage_dir.mkdir(parents=True, exist_ok=True)
        # Leftovers from a previous take would confuse the SDK recorder, and the
        # size check below would then read the wrong file. A stale wav would also
        # make the next take look as if it had audio it does not have.
        for leftover in (bag_path, audio_path):
            try:
                leftover.unlink(missing_ok=True)
            except OSError as exc:
                log.warning("could not clear the previous recording %s: %s", leftover, exc)

        audio_opened = False
        with self._camera_lock:
            # Audio first, camera second, so the wav covers the whole take. The
            # excess at the head is measured by the offset, whereas audio that
            # started late would lose its tail with no way to recover it.
            if self.audio.enabled:
                try:
                    self.audio.start(audio_path, limit)
                    audio_opened = True
                except ApiError as exc:
                    if self.audio.required:
                        raise
                    # Audio is the sidecar, not the dataset. Record the video and
                    # let the screens say the take has no audio.
                    log.warning("recording without audio: %s", exc.message)
            try:
                self.camera.start_recording(bag_path, limit)
            except ApiError:
                if audio_opened:
                    self.audio.stop()
                raise

        started_at = now_iso()
        with self.lock:
            session = self._session
            if session is None or session["session_id"] != session_id:
                # The round vanished while the pipeline was restarting. Release
                # the device rather than leaving it recording into a deleted folder.
                with self._camera_lock:
                    self.camera.stop()
                    if audio_opened:
                        self.audio.stop()
                raise ApiError("SESSION_NOT_FOUND", detail={"session_id": sid})
            stage = self.sessions.stage_of(session, index)
            stage["state"] = "recording"
            stage["recording_started_at"] = started_at
            stage["note"] = (note or "").strip()
            stage["artifact"] = None
            # A new take replaces whatever the previous one was marked as.
            stage["review"] = empty_review()
            self._auto_stopped = False
            self._persist(session)

        log.info("stage %d recording to %s", index, bag_path)
        return {
            "stage_index": index,
            "state": "recording",
            "started_at": started_at,
            "bag_abs_path": str(bag_path),
            "auto_stop_at_s": limit,
        }

    def stop_record(self, sid: str, index: int) -> Dict[str, Any]:
        """Stop, flush, measure and persist. Idempotent, per section 6.8.

        Returning the existing artifact for an already saved stage is deliberate.
        A lost response followed by a retry must read as success, otherwise a
        collector who did nothing wrong is told their take failed.
        """
        with self.lock:
            session = self._session
            if session is None or not self._matches(session, sid):
                loaded = self._load_from_disk(sid)
                if loaded is None:
                    raise ApiError("SESSION_NOT_FOUND", detail={"session_id": sid})
                stage = self.sessions.stage_of(loaded, index)
                if stage["state"] == "saved" and stage.get("artifact"):
                    return {"stage_index": index, "state": "saved", "artifact": stage["artifact"]}
                raise ApiError("STAGE_NOT_RECORDING", detail={"stage_index": index})

            stage = self.sessions.stage_of(session, index)
            if stage["state"] == "saved" and stage.get("artifact"):
                return {"stage_index": index, "state": "saved", "artifact": stage["artifact"]}
            if stage["state"] != "recording":
                raise ApiError("STAGE_NOT_RECORDING", detail={"stage_index": index, "state": stage["state"]})

            started_at = stage.get("recording_started_at")
            limit = float(stage["max_duration_s"])
            session_id = session["session_id"]
            data_dir = session["data_dir"]

        with self._camera_lock:
            # Order matters, and this is the only ordering that lines the audio up
            # with the picture.
            #
            # The take ends at the instant `stop_recording` stamps it, which is
            # before the pipeline is closed: closing a multi gigabyte bag takes
            # one to four seconds. So the audio is told to stop from inside that
            # stamp, via `on_take_end`, and only joined afterwards. Stopping it
            # from here instead is wrong in both directions: before the call the
            # WAV ends seconds early, after it the WAV runs seconds long. That
            # second case is what produced an 11 s take with a 16 s file.
            audio_on = self.audio.enabled
            audio_take = None
            counts = self.camera.stop_recording(
                on_take_end=self.audio.request_stop if audio_on else None
            )
            if audio_on:
                audio_take = self.audio.stop()
            video_span = self.camera.recording_span()
            stopped_at = now_iso()
            thumbnail = self.camera.take_thumbnail()
            self.camera.start_preview()

        duration = self.camera.last_recording_seconds()
        stage_dir = Path(data_dir) / stage_dir_name(index)
        bag_path = stage_dir / BAG_NAME

        if duration < float(self.config.min_duration_s):
            # docs/API.md section 6.8: too short means discard, not save. A
            # mis-click must not leave a two frame bag behind.
            self._cleanup_stage_files(stage_dir)
            with self.lock:
                session = self._session
                if session is not None and session["session_id"] == session_id:
                    stage = self.sessions.stage_of(session, index)
                    stage["state"] = "idle"
                    stage["recording_started_at"] = None
                    stage["artifact"] = None
                    stage["review"] = empty_review()
                    self._persist(session)
            raise ApiError(
                "RECORDING_TOO_SHORT",
                f"That take lasted {duration:.1f}s. The minimum is "
                f"{self.config.min_duration_s:.0f}s, so it was discarded.",
                detail={"stage_index": index, "duration_s": duration},
            )

        # Let Windows release its handle on the bag before it is measured.
        time.sleep(FILE_SETTLE_S)
        size = bag_path.stat().st_size if bag_path.is_file() else 0
        if size <= 0:
            self._cleanup_stage_files(stage_dir)
            with self.lock:
                session = self._session
                if session is not None and self._matches(session, sid):
                    stage = self.sessions.stage_of(session, index)
                    stage["state"] = "idle"
                    stage["recording_started_at"] = None
                    stage["artifact"] = None
                    stage["review"] = empty_review()
                    self._persist(session)
            raise ApiError(
                "CAMERA_ERROR",
                "The recording produced no data, so it was discarded.",
                detail={"stage_index": index},
            )

        if thumbnail:
            try:
                (stage_dir / THUMB_NAME).write_bytes(thumbnail)
            except OSError as exc:
                log.warning("could not write the thumbnail for stage %d: %s", index, exc)

        audio_block = self._audio_artifact(
            index, stage_dir, audio_take, video_span, duration
        )
        if audio_take is not None and audio_take.get("error") is not None:
            log.warning(
                "stage %d audio was partial: %s",
                index,
                audio_take["error"].message,
            )

        artifact = {
            "bag_path": f"{stage_dir_name(index)}\\{BAG_NAME}" if "\\" in data_dir else f"{stage_dir_name(index)}/{BAG_NAME}",
            "size_bytes": int(size),
            "duration_s": round(duration, 2),
            "started_at": started_at,
            "stopped_at": stopped_at,
            "streams": sorted(counts.keys()),
            "frame_counts": counts,
            "sha256": None,
            "thumbnail_url": (
                f"/api/sessions/{sid}/stages/{index}/thumbnail" if thumbnail else None
            ),
            "audio": audio_block,
        }

        with self.lock:
            session = self._session
            if session is None or session["session_id"] != session_id:
                raise ApiError("SESSION_NOT_FOUND", detail={"session_id": sid})
            stage = self.sessions.stage_of(session, index)
            stage["state"] = "saved"
            stage["recording_started_at"] = None
            stage["artifact"] = artifact
            # Every save starts un-reviewed, which is what makes the verdict
            # mandatory before advancing.
            stage["review"] = empty_review()
            self._persist(session)
            self.sessions.save_meta(session, index)
            public = self._public_artifact(artifact)

        log.info(
            "stage %d saved, %.1fs, %d bytes, %d streams%s",
            index,
            duration,
            size,
            len(counts),
            f", audio {audio_block['duration_s']:.1f}s" if audio_block else ", no audio",
        )
        return {"stage_index": index, "state": "saved", "artifact": public}

    def discard_record(self, sid: str, index: int) -> Dict[str, Any]:
        """Delete a stage's take and return it to idle. Idempotent, section 6.9."""
        with self.lock:
            session = self._session
            if session is None or not self._matches(session, sid):
                loaded = self._load_from_disk(sid)
                if loaded is None:
                    raise ApiError("SESSION_NOT_FOUND", detail={"session_id": sid})
                stage = self.sessions.stage_of(loaded, index)
                if stage["state"] == "recording":
                    raise ApiError("STAGE_ALREADY_RECORDING", detail={"stage_index": index})
                deleted = self.sessions.remove_stage_files(loaded, index)
                # The verdict belongs to the take that just went away, so it goes
                # with it. Both files are rewritten, otherwise a later load would
                # read the old verdict back out of the frozen snapshot.
                stage["state"] = "idle"
                stage["recording_started_at"] = None
                stage["artifact"] = None
                stage["review"] = empty_review()
                self.sessions.save(loaded)
                self.sessions.save_meta(loaded, index)
                return {"stage_index": index, "state": "idle", "deleted": deleted}

            stage = self.sessions.stage_of(session, index)
            if stage["state"] == "recording":
                raise ApiError(
                    "STAGE_ALREADY_RECORDING",
                    "Stop the take before discarding it.",
                    detail={"stage_index": index},
                )
            state = stage["state"]

        # Re-recording an already saved stage is the only way back to idle, so a
        # discard on a saved stage is the normal path rather than an error.
        if state == "idle":
            return {"stage_index": index, "state": "idle", "deleted": []}

        deleted = self.sessions.remove_stage_files(session, index)
        with self.lock:
            session = self._session
            if session is not None and self._matches(session, sid):
                stage = self.sessions.stage_of(session, index)
                stage["state"] = "idle"
                stage["recording_started_at"] = None
                stage["artifact"] = None
                stage["review"] = empty_review()
                self._persist(session)
                self._persist_meta_removal(session, index)
        log.info("stage %d discarded, removed %s", index, ", ".join(deleted) or "nothing")
        return {"stage_index": index, "state": "idle", "deleted": deleted}

    # --------------------------------------------------------------- review

    def save_review(
        self, sid: str, index: int, verdict: str, comment: Optional[str]
    ) -> Dict[str, Any]:
        """Record the operator's verdict and comment for a saved take.

        docs/API.md section 6.10. Only the current stage can be reviewed, which
        is the same rule the capture screen follows: once a round has advanced,
        that stage's verdict is settled.

        Both the session mirror and ``meta.json`` are rewritten. The mirror is what
        a restart rebuilds state from, and ``meta.json`` is the per stage record a
        consumer of the dataset reads, so the two must not drift.
        """
        text = (comment or "").strip()
        limit = self.config.review_comment_max_length
        if len(text) > limit:
            raise ApiError(
                "REVIEW_NOTE_TOO_LONG",
                f"That comment is {len(text)} characters. The limit is {limit}.",
                detail={"length": len(text), "max_length": limit},
            )
        if verdict not in REVIEW_VERDICTS:
            # Unreachable through HTTP, the route body is a Literal. Kept so a
            # direct caller cannot persist a third value.
            raise ApiError(
                "REQUEST_INVALID",
                detail={"field": "verdict", "allowed": list(REVIEW_VERDICTS)},
            )

        with self.lock:
            session = self._require_live(sid)
            if session["status"] == "finished":
                raise ApiError("SESSION_FINISHED", detail={"session_id": sid})

            stage = self.sessions.stage_of(session, index)
            if stage["state"] != "saved":
                raise ApiError(
                    "STAGE_NOT_SAVED",
                    "Record and save the take before reviewing it.",
                    detail={"stage_index": index, "state": stage["state"]},
                )

            current = int(session["current_stage"])
            if int(index) != current:
                raise ApiError(
                    "STAGE_NOT_CURRENT",
                    detail={"stage_index": index, "current_stage": current},
                )

            stage["review"] = {
                "verdict": verdict,
                "comment": text,
                "reviewed_at": now_iso(),
            }
            self._persist(session)
            self.sessions.save_meta(session, index)
            public = self._public(session)

        log.info("stage %d reviewed as %s", index, verdict)
        return {"session": public, "stage_index": index}

    def advance(self, sid: str, index: int) -> Dict[str, Any]:
        """Move to the next stage, or finish the round. docs/API.md section 6.11."""
        with self.lock:
            session = self._require_live(sid)
            if session["status"] == "finished":
                raise ApiError("SESSION_FINISHED", detail={"session_id": sid})

            stage = self.sessions.stage_of(session, index)
            self._require_action(stage, "advance")

            current = int(session["current_stage"])
            if int(index) != current:
                raise ApiError(
                    "STAGE_NOT_CURRENT",
                    detail={"stage_index": index, "current_stage": current},
                )

            total = len(session["stages"])
            if index >= total:
                session["status"] = "finished"
                session["finished_at"] = now_iso()
                target: Dict[str, Any] = {"type": "finish"}
            else:
                session["current_stage"] = index + 1
                target = {"type": "guide", "stage_index": index + 1}

            self._persist(session)
            public = self._public(session)

        if target["type"] == "finish":
            with self._camera_lock:
                self.camera.stop()
            log.info("session %s finished", sid)
        return {"session": public, "next": target}

    # -------------------------------------------------------------- read outs

    def artifact(self, sid: str, index: int) -> Dict[str, Any]:
        session = self._require_any(sid)
        stage = self.sessions.stage_of(session, index)
        if stage["state"] != "saved" or not stage.get("artifact"):
            raise ApiError("STAGE_NOT_SAVED", detail={"stage_index": index})
        return self._public_artifact(stage["artifact"])

    def thumbnail(self, sid: str, index: int) -> tuple:
        """Path and content type for the poster frame, or raise the documented code."""
        session = self._require_any(sid)
        stage = self.sessions.stage_of(session, index)
        if stage["state"] != "saved":
            raise ApiError("STAGE_NOT_SAVED", detail={"stage_index": index})
        target = Path(session["data_dir"]) / stage_dir_name(index) / THUMB_NAME
        if not target.is_file():
            raise ApiError("STAGE_NOT_SAVED", "No thumbnail was captured for that stage.")
        return target, "image/jpeg"

    def snapshot(self) -> bytes:
        """One JPEG, for the degraded preview path and for scripted checks.

        The preview is opened on demand. docs/API.md section 7.2 presents this
        endpoint as the fallback when MJPEG is unavailable, and a caller in that
        situation has no reason to have already opened the stream, so requiring
        it first would make the fallback useless. Returns the current frame if
        one is already flowing, so a warm preview is not disturbed.
        """
        payload = self.camera.latest_frame()
        if payload is not None:
            return payload

        with self.lock:
            session = self._session
            sid = str(session["session_id"]) if session is not None else ""

        if sid:
            self.start_preview(sid)
        else:
            with self._camera_lock:
                self.camera.start_preview()

        deadline = time.monotonic() + SNAPSHOT_TIMEOUT_S
        while time.monotonic() < deadline:
            payload = self.camera.latest_frame()
            if payload is not None:
                return payload
            error = self.camera.last_error
            if error is not None:
                raise error
            time.sleep(0.02)

        raise ApiError(
            "CAMERA_ERROR",
            "The camera did not deliver a frame in time.",
            detail={"reason": "no_frame", "timeout_s": SNAPSHOT_TIMEOUT_S},
        )

    # --------------------------------------------------------------- internals

    def _allowed_actions(self, stage: Dict[str, Any]) -> List[str]:
        """Actions the stage permits right now, in the documented order.

        Single source of truth for both ``allowed_actions`` on the wire and the
        checks in :meth:`_require_action`, so the disabled button and the 409 can
        never disagree.
        """
        state = str(stage.get("state") or "idle")
        actions = list(ALLOWED_ACTIONS.get(state, []))
        if state == "saved" and review_verdict(stage) is not None:
            actions.append("advance")
        return actions

    def _require_action(self, stage: Dict[str, Any], action: str) -> None:
        """Enforce the transition table rather than trusting the caller."""
        state = str(stage.get("state") or "idle")
        if action in self._allowed_actions(stage):
            return
        if action == "start":
            if state == "recording":
                raise ApiError("STAGE_ALREADY_RECORDING", detail={"stage_index": stage["index"]})
            raise ApiError("STAGE_ALREADY_SAVED", detail={"stage_index": stage["index"]})
        if action == "advance":
            if state == "saved":
                raise ApiError("REVIEW_REQUIRED", detail={"stage_index": stage["index"]})
            raise ApiError("STAGE_NOT_SAVED", detail={"stage_index": stage["index"]})
        if action == "stop":
            raise ApiError("STAGE_NOT_RECORDING", detail={"stage_index": stage["index"]})
        raise ApiError("STAGE_NOT_SAVED", detail={"stage_index": stage["index"]})

    @staticmethod
    def _matches(session: Dict[str, Any], sid: str) -> bool:
        return str(session.get("session_id")) == str(sid) or str(session.get("name")) == str(sid)

    def _require_live(self, sid: str) -> Dict[str, Any]:
        with self.lock:
            session = self._session
            if session is not None and self._matches(session, sid):
                return session
        loaded = self._load_from_disk(sid)
        if loaded is None:
            raise ApiError("SESSION_NOT_FOUND", detail={"session_id": sid})
        return loaded

    def _require_any(self, sid: str) -> Dict[str, Any]:
        return self._require_live(sid)

    def _load_from_disk(self, sid: str) -> Optional[Dict[str, Any]]:
        """Find a session by identifier or folder name, outside the state lock."""
        root = self.project.root
        if not root:
            return None
        for session in self.sessions.scan(root, self._stage_snapshot()):
            if self._matches(session, sid):
                return session
        return None

    def _persist(self, session: Dict[str, Any]) -> None:
        """Mirror the state machine to disk. Caller holds the lock."""
        try:
            self.sessions.save(session)
        except ApiError as exc:
            # A failed mirror must not undo a transition the collector already saw.
            log.error("could not persist session %s: %s", session.get("session_id"), exc.message)

    def _persist_meta_removal(self, session: Dict[str, Any], index: int) -> None:
        try:
            self.sessions.save_meta(session, index)
        except ApiError as exc:  # pragma: no cover - meta is advisory
            log.warning("could not rewrite meta.json for stage %d: %s", index, exc.message)

    def _cleanup_stage_files(self, stage_dir: Path) -> None:
        for name in (BAG_NAME, AUDIO_NAME, THUMB_NAME):
            try:
                (stage_dir / name).unlink(missing_ok=True)
            except OSError as exc:
                log.warning("could not remove %s: %s", stage_dir / name, exc)

    # ---------------------------------------------------------------- audio

    def _audio_artifact(
        self,
        index: int,
        stage_dir: Path,
        take: Optional[Dict[str, Any]],
        video_span: Tuple[Optional[float], Optional[float]],
        video_duration: float = 0.0,
    ) -> Optional[Dict[str, Any]]:
        """Shape one take's audio block, or ``None`` when it has no audio.

        The offset is measured here rather than at start because the first video
        frame only exists after the pipeline has been running for a moment. Using
        the pipeline open time instead would fold the 50-300 ms start latency into
        the offset, which is the same order as the tolerance the dataset needs.
        """
        if take is None or not take.get("file"):
            return None

        path = stage_dir / AUDIO_NAME
        try:
            size = path.stat().st_size
        except OSError:
            return None
        if size <= 0:
            return None

        audio_started = take.get("started_mono")
        audio_stopped = take.get("stopped_mono")
        video_started, video_stopped = video_span

        offset_ms = 0
        if audio_started is not None and video_started is not None:
            offset_ms = int(round((audio_started - video_started) * 1000))

        # A real clock-drift figure needs two anchors measured in the same domain.
        # The second one is not available here: the worker knows when it noticed
        # the stop, not when the device captured the last sample. Reporting the
        # difference would measure thread scheduling and look like a clock fault,
        # so the check is stated as what it actually is: did the audio come out
        # materially shorter than the video it covers.
        audio_duration = float(take.get("duration_s") or 0.0)
        if audio_duration > 0 and (audio_duration + 1.0) < video_duration:
            log.warning(
                "stage %d audio is %.1fs but the video is %.1fs; samples were lost",
                index,
                audio_duration,
                video_duration,
            )
        # A second or more past the picture means the stop ordering regressed and
        # the microphone is being recorded while the bag is finalised, which is how
        # an 11 s take once produced a 16 s file. A few hundred milliseconds is the
        # device's own buffer period and is expected.
        if audio_stopped is not None and video_stopped is not None:
            tail_ms = (audio_stopped - video_stopped) * 1000
            if tail_ms > 1500:
                log.warning(
                    "stage %d audio ran %.0f ms past the video; the take was stopped "
                    "in the wrong order",
                    index,
                    tail_ms,
                )
        samplerate = int(take.get("samplerate") or self.config.audio_samplerate)
        block_ms = int(round(self.config.audio_block_frames / float(samplerate) * 1000))
        return {
            "file": AUDIO_NAME,
            "device": take.get("device") or "",
            "samplerate": samplerate,
            "channels": int(take.get("channels") or self.config.audio_channels),
            "sample_format": self.config.audio_sample_format,
            "duration_s": audio_duration,
            "size_bytes": int(size),
            "partial": bool(take.get("partial")),
            "overruns": int(take.get("overruns") or 0),
            "peak_dbfs": dbfs(take.get("peak") or 0),
            "sync": {
                "offset_ms": offset_ms,
                "method": "monotonic_anchor",
                # One read block plus the device's own input latency, which is
                # not visible to this process. Reported so a consumer knows this
                # is an estimate and not a sample-accurate claim.
                "estimated_error_ms": block_ms + 60,
            },
        }

    def audio_status(self) -> Dict[str, Any]:
        """Live audio view for the capture screen, one light call per second."""
        return self.audio.status()

    def _on_auto_stop(self) -> None:
        """The camera reached the stage limit. Save it without a browser.

        Runs on the timer thread, so it goes through the same public entry point
        the route uses. That keeps the idempotent stop path as the single
        implementation.
        """
        with self.lock:
            session = self._session
            if session is None:
                return
            stage = next(
                (s for s in session["stages"] if s["state"] == "recording"), None
            )
            if stage is None:
                return
            sid, index = session["session_id"], int(stage["index"])

        try:
            self.stop_record(sid, index)
            self._auto_stopped = True
            log.info("stage %d auto saved at its time limit", index)
        except ApiError as exc:
            log.warning("auto stop failed for stage %d: %s", index, exc.message)

    # ------------------------------------------------------------ public view

    def _public(self, session: Dict[str, Any]) -> Dict[str, Any]:
        """Shape a session for the wire, docs/API.md sections 3.1 and 3.2."""
        with self.lock:
            stages = [
                {
                    "index": int(stage["index"]),
                    "name": stage["name"],
                    "instructions": stage.get("instructions") or "",
                    "min_duration_s": float(stage.get("min_duration_s", self.config.min_duration_s)),
                    "max_duration_s": float(stage["max_duration_s"]),
                    "state": stage["state"],
                    "allowed_actions": self._allowed_actions(stage),
                    "recording_started_at": stage.get("recording_started_at"),
                    "review": normalize_review(stage.get("review")),
                    "artifact": (
                        self._public_artifact(stage["artifact"])
                        if stage.get("artifact")
                        else None
                    ),
                }
                for stage in session["stages"]
            ]
            return {
                "session_id": session["session_id"],
                "name": session["name"],
                "created_at": session["created_at"],
                "finished_at": session.get("finished_at"),
                "status": session["status"],
                "current_stage": int(session["current_stage"]),
                "data_dir": session["data_dir"],
                "operator": session.get("operator", ""),
                "note": session.get("note", ""),
                "stages": stages,
            }

    def _public_artifact(self, artifact: Dict[str, Any]) -> Dict[str, Any]:
        """Trim to the documented field set. The checksum stays on disk."""
        return {
            "bag_path": artifact["bag_path"],
            "size_bytes": int(artifact.get("size_bytes") or 0),
            "duration_s": float(artifact.get("duration_s") or 0.0),
            "started_at": artifact.get("started_at"),
            "stopped_at": artifact.get("stopped_at"),
            "streams": list(artifact.get("streams") or []),
            "frame_counts": dict(artifact.get("frame_counts") or {}),
            "thumbnail_url": artifact.get("thumbnail_url"),
            "audio": artifact.get("audio"),
        }
