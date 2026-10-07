"""Session, recording and preview tests.

None of these need a camera. The pipeline is replaced with
:class:`FakeBackend`, which implements the same four method protocol as
:class:`app.camera.RealSenseBackend` and hands out synthetic frames. That keeps
the suite runnable on a build machine while still exercising the real state
machine, the real disk layout and the real routes.

The fake also earns its place as a fault injector. ``fail_on_open`` and
``stall`` reproduce the two failures that matter on a rig, a stream set the
device refuses and a cable that stops delivering, without unplugging anything.
"""

from __future__ import annotations

import io
import json
import sys
import time
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient
from PIL import Image

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.camera import CameraWorker, Frame, FrameSet  # noqa: E402
from app.capture import CaptureService  # noqa: E402
from app.config import load_config  # noqa: E402
from app.errors import ApiError  # noqa: E402
from app.main import create_app  # noqa: E402

# A tiny solid payload per stream, enough for the encoder to produce a JPEG.
COLOR_W, COLOR_H = 8, 6


def _color_frame() -> Frame:
    return Frame(
        stream="color",
        format="bgr8",
        width=COLOR_W,
        height=COLOR_H,
        data=bytes([40, 80, 160]) * (COLOR_W * COLOR_H),
    )


def _grey_frame(stream: str) -> Frame:
    return Frame(
        stream=stream, format="y8", width=COLOR_W, height=COLOR_H, data=b"\x90" * (COLOR_W * COLOR_H)
    )


def _motion_frame(stream: str) -> Frame:
    return Frame(stream=stream, format="motion_xyz32f", width=0, height=0, data=b"\x00" * 12)


class FakeBackend:
    """Stand-in pipeline. Records what it was asked for and emits frames."""

    # Shared so a test can assert on what the worker requested.
    opened: list = []
    fail_on_open: str | None = None
    stall: bool = False

    def __init__(self, serial: str = "") -> None:
        self.serial = serial
        self.plan = None
        self.closed = False

    # ------------------------------------------------------------ protocol

    def open(self, plan) -> None:
        if FakeBackend.fail_on_open:
            raise ApiError("CAMERA_ERROR", FakeBackend.fail_on_open, detail={"sdk": "fake"})
        self.plan = plan
        self.closed = False
        FakeBackend.opened.append(plan)

    def close(self) -> None:
        self.closed = True
        self.plan = None

    def wait(self, timeout_ms: int):
        if self.plan is None or self.closed or FakeBackend.stall:
            # Mirrors a real wait: no frames, no exception. The worker turns a
            # long enough silence into a CAMERA_ERROR itself.
            time.sleep(min(timeout_ms, 20) / 1000.0)
            return None
        time.sleep(0.004)
        frames = []
        for name in self.plan.streams:
            if name == "color":
                frames.append(_color_frame())
            elif name in ("infrared_1", "infrared_2"):
                frames.append(_grey_frame(name))
            elif name == "depth":
                frames.append(
                    Frame(
                        stream="depth",
                        format="z16",
                        width=4,
                        height=4,
                        data=(b"\x00\x10" * 16),
                    )
                )
            else:
                frames.append(_motion_frame(name))
        # A recording plan writes a real file so the size check has something to
        # measure, otherwise the service would discard every take as empty.
        if self.plan.record_path:
            target = Path(self.plan.record_path)
            target.parent.mkdir(parents=True, exist_ok=True)
            with open(target, "ab") as handle:
                handle.write(b"\x00" * 4096)
        return FrameSet(frames=frames)

    def device_info(self):
        return {"name": "Fake D435I", "serial": "FAKE123", "firmware": "0.0.0"}


# --------------------------------------------------------------- fixtures


def write_config(
    base: Path,
    *,
    stages: int = 3,
    allow_roots=None,
    guides_required: bool = False,
    seed_defaults: bool = False,
) -> Path:
    library = base / "library"
    library.mkdir(parents=True, exist_ok=True)
    config_dir = base / "conf"
    config_dir.mkdir(parents=True, exist_ok=True)

    payload = {
        "app": {"title": "RealSense Data-Collector", "host": "127.0.0.1", "port": 8123},
        "paths": {
            "service_dir": "../var",
            "guide_root": "../var/guides",
            "log_dir": "../var/logs",
            "settings_file": "../var/settings.json",
        },
        "project": {
            "default_root": "",
            "min_free_space_gb": 0,
            "require_writable": True,
        },
        "fs": {"allow_roots": allow_roots if allow_roots is not None else [str(library)]},
        "guides": {
            "required": guides_required,
            "max_size_mb": 1,
            "allowed_types": ["image/png", "image/jpeg"],
            "instructions_max_length": 500,
            "display_max_width": 1600,
            "persist": True,
            "rebuild_from_dir": True,
            "keep_backup": True,
            # See test_api.write_config: off so these tests own an empty
            # directory, the seeding path is covered on its own.
            "seed_defaults": seed_defaults,
        },
        "camera": {
            "probe": False,
            "serial": "",
            "preview": {"width": 640, "height": 480, "fps": 15, "jpeg_quality": 80},
            "recording": {
                "min_duration_s": 0.2,
                "max_duration_s": 60,
                "streams": {
                    "depth": {"width": 848, "height": 480, "format": "z16", "fps": 30},
                    "color": {"width": 640, "height": 480, "format": "bgr8", "fps": 30},
                    "accel": {"format": "motion_xyz32f", "fps": 100},
                    "gyro": {"format": "motion_xyz32f", "fps": 200},
                },
            },
        },
        "storage": {"compress_bag": False, "keep_finished_sessions": True},
        "stages": [
            {
                "index": index,
                "name": f"Stage {index}",
                "instructions": f"Do the thing for stage {index}.",
                "max_duration_s": 30,
            }
            for index in range(1, stages + 1)
        ],
    }
    path = config_dir / "config.yaml"
    path.write_text(yaml.safe_dump(payload), encoding="utf-8")
    return path


@pytest.fixture(autouse=True)
def _reset_fake():
    FakeBackend.opened = []
    FakeBackend.fail_on_open = None
    FakeBackend.stall = False
    yield
    FakeBackend.opened = []
    FakeBackend.fail_on_open = None
    FakeBackend.stall = False


@pytest.fixture()
def env(tmp_path: Path):
    library = tmp_path / "library"
    library.mkdir(parents=True, exist_ok=True)
    project_dir = library / "Project_A"
    config_path = write_config(tmp_path, stages=3, allow_roots=[str(library)])
    return {"base": tmp_path, "library": library, "config": config_path, "project": project_dir}


class Harness:
    """A client wired to the fake camera, plus the pieces tests want to poke."""

    def __init__(self, client: TestClient, services, project_dir: Path) -> None:
        self.client = client
        self.services = services
        self.project_dir = project_dir

    def set_project(self, root: Path | None = None) -> None:
        target = root or self.project_dir
        response = self.client.put("/api/project", json={"root": str(target)})
        assert response.status_code == 200, response.text

    def create(self, name: str = "session_a", **kwargs):
        body = {"name": name, **kwargs}
        return self.client.post("/api/sessions", json=body)


@pytest.fixture()
def harness(env):
    app = create_app(env["config"])
    services = app.state.services

    # Stand in for an attached camera. The probe itself stays off, which is what
    # keeps the suite free of subprocesses and hardware.
    services.device.info = lambda force=False: {
        "connected": True,
        "name": "Fake D435I",
        "serial": "FAKE123",
        "firmware": "0.0.0",
        "usb_type": "3.2",
        "reason": None,
    }
    services.device.last_kind = "connected"

    # Swap the real SDK pipeline for the fake, and rebuild the service that
    # holds it. The routers read app.state.services.capture, so the replacement
    # is what they will call.
    config = load_config(env["config"])
    services.camera = CameraWorker(config, backend_factory=FakeBackend)
    services.capture = CaptureService(
        config=config,
        sessions=services.sessions,
        project=services.project,
        guides=services.guides,
        device=services.device,
        camera=services.camera,
    )

    with TestClient(app) as started:
        yield Harness(started, services, env["project"])


# ------------------------------------------------------------------- basics


def test_create_session_lays_out_the_folder(harness):
    harness.set_project()
    response = harness.create("session_a", operator="zhangsan", note="batch A")
    assert response.status_code == 201, response.text
    body = response.json()

    assert body["name"] == "session_a"
    assert body["status"] == "in_progress"
    assert body["current_stage"] == 1
    assert len(body["stages"]) == 3
    assert body["operator"] == "zhangsan"

    data_dir = Path(body["data_dir"])
    assert data_dir.is_dir()
    assert (data_dir / "session.json").is_file()
    for index in (1, 2, 3):
        assert (data_dir / f"stage_{index:02d}").is_dir()


def test_session_id_has_the_documented_shape(harness):
    harness.set_project()
    sid = harness.create().json()["session_id"]
    date, clock, suffix = sid.split("-")
    assert len(date) == 8 and date.isdigit()
    assert len(clock) == 6 and clock.isdigit()
    assert len(suffix) == 4


def test_stage_carries_its_own_description_and_limits(harness):
    harness.set_project()
    stages = harness.create().json()["stages"]
    assert stages[0]["instructions"] == "Do the thing for stage 1."
    assert stages[0]["min_duration_s"] == 0.2
    assert stages[0]["max_duration_s"] == 30
    assert stages[0]["state"] == "idle"
    assert stages[0]["allowed_actions"] == ["start"]
    assert stages[0]["artifact"] is None


def test_a_guides_description_reaches_a_new_session(harness):
    """docs/API.md section 5.6: the session freezes what was on screen."""
    harness.client.put("/api/guides/2", json={"instructions": "Written on the guides screen."})
    harness.set_project()
    stages = harness.create().json()["stages"]
    assert stages[1]["instructions"] == "Written on the guides screen."
    assert stages[0]["instructions"] == "Do the thing for stage 1."


def test_a_guides_title_reaches_a_new_session(harness):
    """Same rule for the title: the frozen stage table carries what was on screen."""
    harness.client.put("/api/guides/2", json={"name": "Front left, wide"})
    harness.set_project()
    stages = harness.create().json()["stages"]
    assert stages[1]["name"] == "Front left, wide"
    assert stages[0]["name"] == "Stage 1"


def test_second_session_is_refused_while_one_is_running(harness):
    harness.set_project()
    harness.create("session_a")
    response = harness.create("session_b")
    assert response.status_code == 409
    body = response.json()
    assert body["error"]["code"] == "SESSION_ACTIVE_EXISTS"
    assert body["error"]["detail"]["session"]["name"] == "session_a"


# ------------------------------------------------------------ name checking


@pytest.mark.parametrize("name", ["", "   "])
def test_a_missing_name_is_reported_as_required(harness, name):
    harness.set_project()
    response = harness.create(name)
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "SESSION_NAME_REQUIRED"


@pytest.mark.parametrize("name", [".hidden", "has space", "a/b", "a\\b", "..", "x" * 65])
def test_an_invalid_name_is_refused(harness, name):
    harness.set_project()
    response = harness.create(name)
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "SESSION_NAME_INVALID"


def test_a_duplicate_folder_name_is_refused(harness):
    harness.set_project()
    harness.project_dir.joinpath("session_a").mkdir()
    response = harness.create("session_a")
    assert response.status_code == 409
    body = response.json()
    assert body["error"]["code"] == "SESSION_DIR_EXISTS"
    assert body["error"]["detail"]["name"] == "session_a"


def test_missing_diagrams_block_creation_when_required(harness, tmp_path):
    """``guides.required`` is the switch that decides whether this bites."""
    payload = yaml.safe_load(harness.services.config.source.read_text(encoding="utf-8"))
    payload["guides"]["required"] = True
    harness.services.config.source.write_text(yaml.safe_dump(payload), encoding="utf-8")

    # The running service keeps the config it booted with, so rebuild it.
    from app.config import load_config

    harness.services.config.guides_required = True
    assert load_config(harness.services.config.source).guides_required is True

    harness.set_project()
    response = harness.create()
    assert response.status_code == 409
    body = response.json()
    assert body["error"]["code"] == "GUIDES_INCOMPLETE"
    assert sorted(body["error"]["detail"]["missing_indices"]) == [1, 2, 3]


def test_the_seeded_defaults_unblock_a_session_on_a_fresh_rig(env):
    """The point of seeding: a real backend can run a round with no preparation.

    Diagrams are required here, which is the shipped setting, and nothing has been
    uploaded by hand.
    """
    config_path = write_config(
        env["base"],
        stages=3,
        allow_roots=[str(env["library"])],
        guides_required=True,
        seed_defaults=True,
    )
    app = create_app(config_path)
    _stand_in_camera(app)

    with TestClient(app) as client:
        harness = Harness(client, app.state.services, env["project"])

        guides = client.get("/api/guides").json()
        assert guides["required"] is True
        assert guides["uploaded"] == 3
        assert guides["ready"] is True

        harness.set_project()
        response = harness.create()
        assert response.status_code == 201, response.text
        assert response.json()["current_stage"] == 1


def test_the_shipped_config_seeds_and_requires_the_diagrams():
    """Checked against the file, so the fixtures cannot hide a change here."""
    shipped = load_config(BACKEND_DIR / "config" / "config.yaml")
    assert shipped.guides_required is True
    assert shipped.guides_seed_defaults is True, "a fresh rig would need 8 hand made images"


# -------------------------------------------------------------- recording


def test_a_full_take_saves_and_reports_an_artifact(harness):
    harness.set_project()
    harness.create()

    started = harness.client.post("/api/sessions/session_a/stages/1/record/start", json={})
    assert started.status_code == 200, started.text
    start_body = started.json()
    assert start_body["state"] == "recording"
    assert start_body["auto_stop_at_s"] == 30
    assert start_body["bag_abs_path"].endswith("capture.db3")
    assert "stage_01" in start_body["bag_abs_path"]

    time.sleep(0.35)
    stopped = harness.client.post("/api/sessions/session_a/stages/1/record/stop")
    assert stopped.status_code == 200, stopped.text
    body = stopped.json()
    assert body["state"] == "saved"

    artifact = body["artifact"]
    assert artifact["bag_path"].replace("\\", "/") == "stage_01/capture.db3"
    assert artifact["size_bytes"] > 0
    assert artifact["duration_s"] >= 0.2
    assert artifact["streams"] == ["accel", "color", "depth", "gyro"]
    assert artifact["frame_counts"]["color"] > 0
    assert artifact["thumbnail_url"].endswith("/stages/1/thumbnail")

    stage = harness.client.get("/api/sessions/session_a").json()["stages"][0]
    assert stage["state"] == "saved"
    assert stage["allowed_actions"] == ["discard", "advance"]

    stage_dir = harness.project_dir / "session_a" / "stage_01"
    assert (stage_dir / "capture.db3").is_file()
    assert (stage_dir / "meta.json").is_file()
    assert (stage_dir / "thumb.jpg").is_file()


def test_the_thumbnail_is_a_jpeg(harness):
    harness.set_project()
    harness.create()
    harness.client.post("/api/sessions/session_a/stages/1/record/start", json={})
    time.sleep(0.3)
    harness.client.post("/api/sessions/session_a/stages/1/record/stop")

    response = harness.client.get("/api/sessions/session_a/stages/1/thumbnail")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/jpeg"
    with Image.open(io.BytesIO(response.content)) as image:
        assert image.size == (COLOR_W, COLOR_H)


def test_recording_must_be_attached_at_pipeline_start(harness):
    """The recorder cannot be added to a running pipeline, so a take restarts it."""
    harness.set_project()
    harness.create()
    FakeBackend.opened = []

    harness.client.post("/api/sessions/session_a/stages/1/record/start", json={})
    assert FakeBackend.opened[-1].record_path is not None

    time.sleep(0.25)
    harness.client.post("/api/sessions/session_a/stages/1/record/stop")
    # The stop restores the preview, which is a second open with no recorder.
    assert FakeBackend.opened[-1].record_path is None
    assert FakeBackend.opened[-1].mode == "preview"


def test_a_second_start_on_a_recording_stage_is_refused(harness):
    harness.set_project()
    harness.create()
    harness.client.post("/api/sessions/session_a/stages/1/record/start", json={})
    response = harness.client.post("/api/sessions/session_a/stages/1/record/start", json={})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "STAGE_ALREADY_RECORDING"


def test_stopping_a_saved_stage_returns_the_artifact(harness):
    """Idempotent, so a retry after a lost response reads as success."""
    harness.set_project()
    harness.create()
    harness.client.post("/api/sessions/session_a/stages/1/record/start", json={})
    time.sleep(0.3)
    first = harness.client.post("/api/sessions/session_a/stages/1/record/stop").json()
    second = harness.client.post("/api/sessions/session_a/stages/1/record/stop")
    assert second.status_code == 200
    assert second.json() == first


def test_stopping_an_idle_stage_is_refused(harness):
    harness.set_project()
    harness.create()
    response = harness.client.post("/api/sessions/session_a/stages/1/record/stop")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "STAGE_NOT_RECORDING"


def test_a_too_short_take_is_discarded(harness):
    harness.set_project()
    harness.create()
    harness.client.post("/api/sessions/session_a/stages/1/record/start", json={})
    # min_duration_s is 0.2 in this config, so stopping at once is below it.
    response = harness.client.post("/api/sessions/session_a/stages/1/record/stop")
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "RECORDING_TOO_SHORT"

    stage = harness.client.get("/api/sessions/session_a").json()["stages"][0]
    assert stage["state"] == "idle"
    assert stage["allowed_actions"] == ["start"]
    stage_dir = harness.project_dir / "session_a" / "stage_01"
    assert not (stage_dir / "capture.db3").exists()
    assert not (stage_dir / "thumb.jpg").exists()


def test_discard_returns_the_stage_to_idle(harness):
    harness.set_project()
    harness.create()
    harness.client.post("/api/sessions/session_a/stages/1/record/start", json={})
    time.sleep(0.3)
    harness.client.post("/api/sessions/session_a/stages/1/record/stop")

    response = harness.client.post("/api/sessions/session_a/stages/1/record/discard")
    assert response.status_code == 200
    body = response.json()
    assert body["state"] == "idle"
    assert "capture.db3" in body["deleted"]

    stage_dir = harness.project_dir / "session_a" / "stage_01"
    assert not (stage_dir / "capture.db3").exists()
    assert harness.client.get("/api/sessions/session_a/stages/1/artifact").status_code == 409


def test_discard_is_idempotent(harness):
    harness.set_project()
    harness.create()
    first = harness.client.post("/api/sessions/session_a/stages/1/record/discard")
    second = harness.client.post("/api/sessions/session_a/stages/1/record/discard")
    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["deleted"] == []


def test_discard_refuses_while_recording(harness):
    harness.set_project()
    harness.create()
    harness.client.post("/api/sessions/session_a/stages/1/record/start", json={})
    response = harness.client.post("/api/sessions/session_a/stages/1/record/discard")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "STAGE_ALREADY_RECORDING"


def test_the_note_from_the_request_reaches_the_record(harness):
    harness.set_project()
    harness.create()
    harness.client.post(
        "/api/sessions/session_a/stages/1/record/start", json={"note": "target still"}
    )
    time.sleep(0.3)
    harness.client.post("/api/sessions/session_a/stages/1/record/stop")

    meta = json.loads(
        (harness.project_dir / "session_a" / "stage_01" / "meta.json").read_text(encoding="utf-8")
    )
    assert meta["note"] == "target still"


def test_frame_counts_cover_every_configured_stream(harness):
    harness.set_project()
    harness.create()
    harness.client.post("/api/sessions/session_a/stages/1/record/start", json={})
    time.sleep(0.35)
    artifact = harness.client.post("/api/sessions/session_a/stages/1/record/stop").json()["artifact"]
    assert set(artifact["frame_counts"]) == {"accel", "color", "depth", "gyro"}
    assert all(count > 0 for count in artifact["frame_counts"].values())


# ---------------------------------------------------------------- advancing


def test_advance_moves_to_the_next_stage(harness):
    harness.set_project()
    harness.create()
    harness.client.post("/api/sessions/session_a/stages/1/record/start", json={})
    time.sleep(0.3)
    harness.client.post("/api/sessions/session_a/stages/1/record/stop")

    response = harness.client.post("/api/sessions/session_a/stages/1/advance")
    assert response.status_code == 200
    body = response.json()
    assert body["next"] == {"type": "guide", "stage_index": 2}
    assert body["session"]["current_stage"] == 2


def test_advancing_an_unsaved_stage_is_refused(harness):
    harness.set_project()
    harness.create()
    response = harness.client.post("/api/sessions/session_a/stages/1/advance")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "STAGE_NOT_SAVED"


def test_advancing_a_non_current_stage_is_refused(harness):
    """Otherwise a look back at stage one would rewind the whole round."""
    harness.set_project()
    harness.create()
    for index in (1, 2):
        harness.client.post(f"/api/sessions/session_a/stages/{index}/record/start", json={})
        time.sleep(0.25)
        harness.client.post(f"/api/sessions/session_a/stages/{index}/record/stop")
    harness.client.post("/api/sessions/session_a/stages/1/advance")

    response = harness.client.post("/api/sessions/session_a/stages/1/advance")
    assert response.status_code == 409
    body = response.json()
    assert body["error"]["code"] == "STAGE_NOT_CURRENT"
    assert body["error"]["detail"]["current_stage"] == 2


def test_the_last_advance_finishes_the_round(harness):
    harness.set_project()
    harness.create()
    for index in (1, 2, 3):
        harness.client.post(f"/api/sessions/session_a/stages/{index}/record/start", json={})
        time.sleep(0.25)
        harness.client.post(f"/api/sessions/session_a/stages/{index}/record/stop")

    harness.client.post("/api/sessions/session_a/stages/1/advance")
    harness.client.post("/api/sessions/session_a/stages/2/advance")
    response = harness.client.post("/api/sessions/session_a/stages/3/advance")
    assert response.status_code == 200
    body = response.json()
    assert body["next"] == {"type": "finish"}
    assert body["session"]["status"] == "finished"
    assert body["session"]["finished_at"]

    # A finished round stays readable, which the finish screen depends on, but
    # it can no longer record or preview.
    assert harness.client.get("/api/sessions/session_a").status_code == 200
    blocked = harness.client.post("/api/sessions/session_a/stages/3/record/start", json={})
    assert blocked.status_code == 409
    assert blocked.json()["error"]["code"] == "SESSION_FINISHED"


# ------------------------------------------------------------- persistence


def test_a_restart_recovers_the_running_session(env):
    """docs/API.md section 8.2 step eight, and the fix for a stray refresh."""
    first_app = create_app(env["config"])
    _stand_in_camera(first_app)
    with TestClient(first_app) as client:
        client.put("/api/project", json={"root": str(env["project"])})
        client.post("/api/sessions", json={"name": "session_a"})
        client.post("/api/sessions/session_a/stages/1/record/start", json={})
        time.sleep(0.3)
        client.post("/api/sessions/session_a/stages/1/record/stop")

    second_app = create_app(env["config"])
    _stand_in_camera(second_app)
    with TestClient(second_app) as client:
        body = client.get("/api/sessions/session_a").json()
        assert body["name"] == "session_a"
        assert body["status"] == "in_progress"
        assert body["stages"][0]["state"] == "saved"
        assert body["stages"][0]["artifact"]["size_bytes"] > 0


def test_an_interrupted_take_is_moved_aside_not_deleted(env):
    """A truncated bag might still be salvageable, so it is kept, per section 8.2."""
    app = create_app(env["config"])
    _stand_in_camera(app)
    with TestClient(app) as client:
        client.put("/api/project", json={"root": str(env["project"])})
        client.post("/api/sessions", json={"name": "session_a"})

    # Hand craft the state a hard kill leaves behind: the record still says
    # recording, and a half written bag is sitting on disk.
    session_dir = env["project"] / "session_a"
    record = json.loads((session_dir / "session.json").read_text(encoding="utf-8"))
    record["config_snapshot"][0]["state"] = "recording"
    (session_dir / "session.json").write_text(json.dumps(record), encoding="utf-8")
    (session_dir / "stage_01" / "capture.db3").write_bytes(b"\x00" * 128)

    app2 = create_app(env["config"])
    _stand_in_camera(app2)
    with TestClient(app2) as client:
        body = client.get("/api/sessions/session_a").json()
        assert body["stages"][0]["state"] == "idle"
        assert body["stages"][0]["artifact"] is None
        recovered = session_dir / "stage_01" / "recovered"
        assert (recovered / "capture.interrupted.db3").is_file()


def test_a_finished_round_survives_a_restart(env):
    app = create_app(env["config"])
    _stand_in_camera(app)
    with TestClient(app) as client:
        client.put("/api/project", json={"root": str(env["project"])})
        client.post("/api/sessions", json={"name": "session_a"})
        client.post("/api/sessions/session_a/stages/1/record/start", json={})
        time.sleep(0.3)
        client.post("/api/sessions/session_a/stages/1/record/stop")
        client.post("/api/sessions/session_a/stages/1/advance")

    app2 = create_app(env["config"])
    _stand_in_camera(app2)
    with TestClient(app2) as client:
        listed = client.get("/api/sessions").json()["sessions"]
        names = [item["name"] for item in listed]
        assert "session_a" in names
        assert client.get("/api/sessions/session_a").status_code == 200


def test_a_hand_renamed_folder_still_loads(env):
    """Tidying folders by hand is reasonable and must not make data unreadable."""
    app = create_app(env["config"])
    _stand_in_camera(app)
    with TestClient(app) as client:
        client.put("/api/project", json={"root": str(env["project"])})
        client.post("/api/sessions", json={"name": "session_a"})

    (env["project"] / "session_a").rename(env["project"] / "renamed_by_hand")

    app2 = create_app(env["config"])
    _stand_in_camera(app2)
    with TestClient(app2) as client:
        body = client.get("/api/sessions/renamed_by_hand").json()
        assert body["name"] == "renamed_by_hand"
        assert body["data_dir"].endswith("renamed_by_hand")


# ------------------------------------------------------------------ listing


def test_the_list_reports_both_live_and_disk_sessions(env):
    app = create_app(env["config"])
    _stand_in_camera(app)
    with TestClient(app) as client:
        client.put("/api/project", json={"root": str(env["project"])})
        client.post("/api/sessions", json={"name": "session_a"})

    # A folder left by an earlier run, with no live session.
    (env["project"] / "session_old").mkdir()

    app2 = create_app(env["config"])
    _stand_in_camera(app2)
    with TestClient(app2) as client:
        client.put("/api/project", json={"root": str(env["project"])})
        body = client.get("/api/sessions").json()
        names = sorted(item["name"] for item in body["sessions"])
        assert names == ["session_a", "session_old"]
        assert body["project_root"].endswith("Project_A")


def test_the_list_is_empty_when_nothing_is_configured(harness):
    body = harness.client.get("/api/sessions").json()
    assert body == {"project_root": None, "sessions": []}


# ------------------------------------------------------------------ discard


def test_discarding_a_round_removes_its_folder(harness):
    harness.set_project()
    harness.create()
    response = harness.client.delete("/api/sessions/session_a")
    assert response.status_code == 200
    body = response.json()
    assert body["deleted"] is True
    assert not (harness.project_dir / "session_a").exists()


def test_discarding_twice_is_success(harness):
    harness.set_project()
    harness.create()
    harness.client.delete("/api/sessions/session_a")
    response = harness.client.delete("/api/sessions/session_a")
    assert response.status_code == 200
    assert response.json()["deleted"] is True


def test_discarding_refuses_while_recording(harness):
    harness.set_project()
    harness.create()
    harness.client.post("/api/sessions/session_a/stages/1/record/start", json={})
    response = harness.client.delete("/api/sessions/session_a")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "STAGE_ALREADY_RECORDING"


# ------------------------------------------------------------------ preview


def test_preview_start_and_stop(harness):
    harness.set_project()
    harness.create()
    started = harness.client.post("/api/sessions/session_a/preview/start")
    assert started.status_code == 200
    assert started.json() == {"streaming": True, "stream_url": "/api/preview/stream", "auto_saved": False}

    stopped = harness.client.post("/api/sessions/session_a/preview/stop")
    assert stopped.status_code == 200
    assert stopped.json()["streaming"] is False


def test_a_snapshot_returns_a_jpeg_once_frames_flow(harness):
    harness.set_project()
    harness.create()
    harness.client.post("/api/sessions/session_a/preview/start")
    # Give the worker thread a moment to publish a frame.
    deadline = time.monotonic() + 3.0
    response = None
    while time.monotonic() < deadline:
        response = harness.client.get("/api/preview/snapshot")
        if response.status_code == 200:
            break
        time.sleep(0.05)
    assert response is not None and response.status_code == 200
    assert response.headers["content-type"] == "image/jpeg"
    with Image.open(io.BytesIO(response.content)) as image:
        assert image.size == (COLOR_W, COLOR_H)


def test_leaving_the_page_saves_the_take(harness):
    """docs/API.md section 6.6: a take in progress is saved, not abandoned."""
    harness.set_project()
    harness.create()
    harness.client.post("/api/sessions/session_a/preview/start")
    harness.client.post("/api/sessions/session_a/stages/1/record/start", json={})
    time.sleep(0.3)

    response = harness.client.post("/api/sessions/session_a/preview/stop")
    assert response.status_code == 200
    body = response.json()
    assert body["streaming"] is False
    assert body["auto_saved"] is True

    stage = harness.client.get("/api/sessions/session_a").json()["stages"][0]
    assert stage["state"] == "saved"


# -------------------------------------------------------------- camera faults


def test_an_unresolvable_stream_set_says_so(harness):
    """The failure mode the shipped config actually hit on real hardware."""
    harness.set_project()
    harness.create()
    FakeBackend.fail_on_open = "Couldn't resolve requests"
    response = harness.client.post("/api/sessions/session_a/stages/1/record/start", json={})
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "CAMERA_ERROR"

    # The stage must be left alone rather than wedged in recording, so the
    # collector can fix the configuration and retry.
    stage = harness.client.get("/api/sessions/session_a").json()["stages"][0]
    assert stage["state"] == "idle"
    assert stage["allowed_actions"] == ["start"]


def test_the_sdk_stream_error_is_translated_for_the_operator():
    """The SDK text is opaque, so it is replaced with what to actually check."""
    from app.camera import RealSenseBackend

    error = RealSenseBackend._translate_open_error("Couldn't resolve requests")
    assert error.code == "CAMERA_ERROR"
    assert "combination of streams" in error.message
    assert error.detail["reason"] == "unresolvable_streams"

    missing = RealSenseBackend._translate_open_error("No device connected")
    assert missing.code == "DEVICE_NOT_FOUND"


def test_a_stalled_stream_is_reported_as_a_camera_error(harness):
    harness.set_project()
    harness.create()
    harness.client.post("/api/sessions/session_a/preview/start")
    FakeBackend.stall = True
    time.sleep(0.05)
    # The stall guard is measured in seconds, so drive it directly rather than
    # waiting out the real interval.
    services = harness.services
    services.camera._last_frame_at = time.monotonic() - 10.0
    services.camera._check_stall()
    assert services.camera.streaming is False
    assert services.camera.last_error is not None
    assert services.camera.last_error.code == "CAMERA_ERROR"


# -------------------------------------------------------------- plan checking


def test_the_motion_rate_the_device_refuses_is_caught_before_open(harness, env):
    """accel at 63 Hz made the whole request unresolvable on real firmware."""
    from app.camera import PipelinePlan

    worker = harness.services.camera
    plan = PipelinePlan(
        mode="record",
        streams={"accel": {"format": "motion_xyz32f", "fps": 63}},
        record_path=str(env["base"] / "x.db3"),
    )
    with pytest.raises(ApiError) as caught:
        worker.validate_plan(plan)
    assert caught.value.code == "CAMERA_ERROR"
    assert "100, 200, 400" in caught.value.message


def test_an_unknown_stream_is_refused_before_open(harness, env):
    from app.camera import PipelinePlan

    worker = harness.services.camera
    plan = PipelinePlan(
        mode="record",
        streams={"lidar": {"format": "y8", "fps": 30}},
        record_path=str(env["base"] / "x.db3"),
    )
    with pytest.raises(ApiError) as caught:
        worker.validate_plan(plan)
    assert "Unknown stream" in caught.value.message


def test_a_recording_path_must_end_in_db3(env):
    """Verified against SDK 2.58, which refuses any other extension."""
    from app.camera import require_db3

    require_db3(str(env["base"] / "capture.db3"))

    with pytest.raises(ApiError) as caught:
        require_db3(str(env["base"] / "capture.bag"))
    assert "must end in .db3" in caught.value.message


def test_the_shipped_recording_path_uses_the_required_extension():
    """deviation guard: capture.bag would fail on the device, so the on disk name
    is capture.db3 and the docs say so."""
    from app.sessions import BAG_NAME

    assert BAG_NAME == "capture.db3"


def test_the_shipped_recording_plan_passes_validation():
    """The YAML that ships must satisfy the checks, or the rig fails on day one."""
    config = load_config(BACKEND_DIR / "config" / "config.yaml")
    worker = CameraWorker(config, backend_factory=FakeBackend)
    from app.camera import PipelinePlan

    plan = PipelinePlan(
        mode="record",
        streams=config.recording_streams,
        record_path="x.db3",
    )
    worker.validate_plan(plan)


class _StubProfile:
    """The slice of a pyrealsense2 profile the stream naming reads."""

    def __init__(self, name: str, index: int = 0) -> None:
        self._name = name
        self._index = index

    def stream_name(self) -> str:
        return self._name

    def stream_index(self) -> int:
        return self._index


def test_the_infrared_imagers_are_named_by_their_prefixed_stream_name():
    """deviation guard: the device reports "Infrared 1" and "Infrared 2", not a
    bare "Infrared" plus an index. Matching on the bare name silently dropped
    both imagers from every stream list and frame count."""
    from app.camera import RealSenseBackend

    assert RealSenseBackend._logical_name(_StubProfile("Infrared 1", 1)) == "infrared_1"
    assert RealSenseBackend._logical_name(_StubProfile("Infrared 2", 2)) == "infrared_2"


@pytest.mark.parametrize(
    "name,index,expected",
    [
        ("Color", 0, "color"),
        ("Depth", 0, "depth"),
        ("Accel", 0, "accel"),
        ("Gyro", 0, "gyro"),
        ("Infrared 1", 1, "infrared_1"),
        ("Infrared 2", 2, "infrared_2"),
    ],
)
def test_every_stream_the_d435i_reports_maps_to_a_logical_name(name, index, expected):
    from app.camera import RealSenseBackend

    assert RealSenseBackend._logical_name(_StubProfile(name, index)) == expected


def test_an_unknown_stream_name_is_ignored_rather_than_mislabelled():
    from app.camera import RealSenseBackend

    assert RealSenseBackend._logical_name(_StubProfile("Confidence", 0)) is None


def test_a_short_wait_that_times_out_is_not_a_pipeline_fault():
    """deviation guard: wait_for_frames raises on timeout instead of returning an
    empty frameset, so a 30 fps stream with a 60 ms wait tripped a fault several
    times a second and tore the pipeline down instantly."""
    from app.camera import RealSenseBackend

    class _Pipe:
        def wait_for_frames(self, timeout_ms: int):
            raise RuntimeError(f"Frame didn't arrive within {timeout_ms}")

    backend = RealSenseBackend()
    backend._pipe = _Pipe()
    assert backend.wait(60) is None

    class _BrokenPipe:
        def wait_for_frames(self, timeout_ms: int):
            raise RuntimeError("Failed to resolve frame")

    backend._pipe = _BrokenPipe()
    with pytest.raises(ApiError) as caught:
        backend.wait(60)
    assert caught.value.code == "CAMERA_ERROR"


class _ExplodingBackend(FakeBackend):
    """Raises something the SDK could plausibly raise and ApiError would not."""

    def open(self, plan) -> None:
        raise ValueError("the binding rejected this call")


def test_a_backend_fault_that_is_not_an_api_error_does_not_kill_the_worker(env):
    """A raw exception used to escape the worker thread, which then reported
    every later request as a twenty second timeout instead of the real cause."""
    from app.camera import CameraWorker, PipelinePlan

    worker = CameraWorker(load_config(env["config"]), backend_factory=_ExplodingBackend)
    worker.start()
    try:
        with pytest.raises(ApiError) as caught:
            worker.start_preview()
        assert caught.value.code == "CAMERA_ERROR"
        assert "ValueError" in caught.value.detail["sdk"]

        # The thread is still alive and still serving commands.
        assert worker.last_error is not None
        assert worker.streaming is False
    finally:
        worker.stop()


def test_a_frame_request_opens_the_preview_when_it_is_not_running(harness):
    """docs/API.md section 7.2 calls the snapshot the fallback for when MJPEG is
    unavailable, so it cannot require a stream to already be open."""
    harness.set_project()
    harness.create()
    FakeBackend.opened = []

    response = harness.client.get("/api/preview/snapshot")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/jpeg"
    assert response.content[:2] == b"\xff\xd8"
    assert FakeBackend.opened[-1].mode == "preview"


def _stand_in_camera(app) -> None:
    """Shared wiring for the tests that build their own app instance."""
    services = app.state.services
    services.device.info = lambda force=False: {
        "connected": True,
        "name": "Fake D435I",
        "serial": "FAKE123",
        "firmware": "0.0.0",
        "usb_type": "3.2",
        "reason": None,
    }
    services.device.last_kind = "connected"
    config = services.config
    services.camera = CameraWorker(config, backend_factory=FakeBackend)
    services.capture = CaptureService(
        config=config,
        sessions=services.sessions,
        project=services.project,
        guides=services.guides,
        device=services.device,
        camera=services.camera,
    )
