"""Tests for the non camera endpoints.

Run with the conda base interpreter, from the repository root:

    python -m pytest backend/tests -q

Every test builds a throwaway config in a tmp directory, so nothing touches the
real service directory and the suite can run on a machine with no camera.
"""

from __future__ import annotations

import io
import json
import os
import shutil
import string
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import List

import pytest
import yaml
from fastapi.testclient import TestClient
from PIL import Image

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app import diagrams  # noqa: E402
from app import guides as guides_manifest  # noqa: E402
from app.config import ConfigError, load_config  # noqa: E402
from app.main import create_app  # noqa: E402


# --------------------------------------------------------------- fixtures

def write_config(
    base: Path,
    *,
    allow_roots=None,
    min_free_space_gb=1,
    guides_overrides=None,
    stages=8,
    default_root="",
    recording_streams=None,
) -> Path:
    config_dir = base / "conf"
    config_dir.mkdir(parents=True, exist_ok=True)
    stage_list = [
        {
            "index": index,
            "name": f"Stage {index}",
            "instructions": f"Do the thing for stage {index}.",
            "max_duration_s": 30,
        }
        for index in range(1, stages + 1)
    ]
    payload = {
        "app": {"title": "RealSense Data-Collector", "host": "127.0.0.1", "port": 8123},
        "paths": {
            "service_dir": "../var",
            "guide_root": "../var/guides",
            "log_dir": "../var/logs",
            "settings_file": "../var/settings.json",
        },
        "project": {
            "default_root": default_root,
            "min_free_space_gb": min_free_space_gb,
            "require_writable": True,
        },
        "fs": {"allow_roots": allow_roots if allow_roots is not None else [str(base / "library")]},
        "guides": {
            "required": True,
            "max_size_mb": 1,
            "allowed_types": [
                "image/png",
                "image/jpeg",
                "image/webp",
                "image/bmp",
                "image/gif",
            ],
            "instructions_max_length": 500,
            "display_max_width": 1600,
            "persist": True,
            "rebuild_from_dir": True,
            "keep_backup": True,
            # Off here so the upload tests still start from an empty directory.
            # The seeding tests set it back on through guides_overrides, and
            # test_seed_defaults_is_on_in_the_shipped_config covers the default.
            "seed_defaults": False,
        },
        "camera": {
            "probe": False,
            "serial": "",
            "preview": {"width": 640, "height": 480, "fps": 15, "jpeg_quality": 80},
            "recording": {
                "min_duration_s": 1,
                "max_duration_s": 300,
                "streams": {"depth": {"width": 848, "height": 480, "format": "z16", "fps": 30}},
            },
        },
        "storage": {"compress_bag": False, "keep_finished_sessions": True},
        "stages": stage_list,
    }
    if guides_overrides:
        payload["guides"].update(guides_overrides)
    if recording_streams is not None:
        payload["camera"]["recording"]["streams"] = recording_streams

    path = config_dir / "config.yaml"
    path.write_text(yaml.safe_dump(payload), encoding="utf-8")
    return path


@pytest.fixture()
def env(tmp_path: Path):
    """Config, library and service directories inside one tmp root.

    The config uses the same relative paths as the shipped one, so service data
    lands in ``<base>/var`` next to ``<base>/conf``.
    """
    library = tmp_path / "library"
    library.mkdir(parents=True, exist_ok=True)
    (library / "ProjectA").mkdir()
    config_path = write_config(tmp_path, allow_roots=[str(library)])
    return {"base": tmp_path, "library": library, "config": config_path, "var": tmp_path / "var"}


@pytest.fixture()
def client(env):
    app = create_app(env["config"])
    with TestClient(app) as started:
        yield started


def png_bytes(width: int = 40, height: int = 30, color=(120, 30, 200)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), color).save(buffer, format="PNG")
    return buffer.getvalue()


def jpeg_bytes(width: int = 40, height: int = 30) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), (10, 200, 40)).save(buffer, format="JPEG")
    return buffer.getvalue()


def image_bytes(pillow_format: str, width: int = 40, height: int = 30) -> bytes:
    """Encode a small solid image in one of the accepted formats."""
    mode = "RGBA" if pillow_format in {"PNG", "WEBP", "GIF"} else "RGB"
    buffer = io.BytesIO()
    Image.new(mode, (width, height), (20, 90, 160)).save(buffer, format=pillow_format)
    return buffer.getvalue()


# ------------------------------------------------------------------- basics

def test_health_is_degraded_without_a_camera(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    body = response.json()

    assert body["status"] == "degraded"
    assert body["device"]["connected"] is False
    assert body["device"]["reason"]
    assert body["project"]["configured"] is False
    assert body["guides"]["total"] == 8
    assert body["guides"]["ready"] is False
    assert body["active_session"] is None


def test_config_exposes_only_presession_fields(client):
    body = client.get("/api/config").json()
    assert body["app_title"] == "RealSense Data-Collector"
    assert body["total_stages"] == 8
    assert body["recording"]["min_duration_s"] == 1
    assert body["recording"]["max_duration_s_default"] == 300
    # The output name and the size rate are part of the contract because the
    # screens that describe a take before it exists read them from here.
    assert body["recording"]["output_name"] == "capture.db3"
    # The test config records depth 848x480 z16 at 30 fps: 848 * 480 * 2 * 30.
    assert body["recording"]["bytes_per_second"] == 848 * 480 * 2 * 30
    assert body["preview"] == {"fps": 15, "jpeg_quality": 80}
    assert len(body["stages"]) == 8
    assert body["stages"][0] == {
        "index": 1,
        "name": "Stage 1",
        "instructions": "Do the thing for stage 1.",
        "max_duration_s": 30,
    }
    # Paths and camera parameters must not leak onto the wire.
    assert "paths" not in body
    assert "camera" not in body


def test_the_size_rate_tracks_the_configured_streams(env):
    """It is derived rather than measured, so a config change moves it."""
    cases = [
        ({"color": {"width": 640, "height": 480, "format": "bgr8", "fps": 30}}, 640 * 480 * 3 * 30),
        ({"color": {"width": 1280, "height": 720, "format": "bgr8", "fps": 15}}, 1280 * 720 * 3 * 15),
        ({"depth": {"width": 848, "height": 480, "format": "z16", "fps": 30}}, 848 * 480 * 2 * 30),
        ({"infrared_1": {"width": 848, "height": 480, "format": "y8", "fps": 30}}, 848 * 480 * 1 * 30),
    ]
    for streams, expected in cases:
        config = load_config(
            write_config(env["base"], allow_roots=[str(env["library"])], recording_streams=streams)
        )
        assert config.recording_bytes_per_s == expected, streams


def test_the_size_rate_counts_a_motion_sample_as_three_floats(env):
    config = load_config(
        write_config(
            env["base"],
            allow_roots=[str(env["library"])],
            recording_streams={"accel": {"format": "motion_xyz32f", "fps": 100}},
        )
    )
    assert config.recording_bytes_per_s == 12 * 100


def test_the_size_rate_of_the_shipped_config_matches_a_measured_take():
    """73 MB/s predicted against 59 to 71 MB/s measured on a real D435I.

    The gauge exists to warn about disk use, so understating is the failure that
    matters. This asserts it is not understating.
    """
    config = load_config(BACKEND_DIR / "config" / "config.yaml")
    predicted_mb = config.recording_bytes_per_s / 1024 / 1024
    assert 70 <= predicted_mb <= 80, predicted_mb
    # 1064747008 bytes over 14.3 s, the longest take measured through the UI.
    assert predicted_mb >= (1064747008 / 14.3) / 1024 / 1024


def test_unknown_api_route_uses_the_envelope(client):
    response = client.get("/api/does-not-exist")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


def test_session_creation_needs_a_camera(client):
    """The camera is checked first because it is the one precondition the
    collector cannot resolve from the page they are on. This config has probing
    off, so the service reports no device rather than pretending to proceed."""
    response = client.post("/api/sessions", json={"name": "session_a"})
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "DEVICE_NOT_FOUND"


def test_session_list_is_empty_without_a_project_directory(client):
    """No project directory is a normal first run state, not an error."""
    response = client.get("/api/sessions")
    assert response.status_code == 200
    assert response.json() == {"project_root": None, "sessions": []}


def test_recording_on_an_unknown_session_is_a_404(client):
    response = client.post("/api/sessions/nope/stages/1/record/start", json={})
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "SESSION_NOT_FOUND"


def test_preview_stop_accepts_an_empty_body(client):
    """sendBeacon sends no payload and cannot set a content type. A declared
    body here would produce a 422 the frontend cannot parse."""
    response = client.post("/api/sessions/nope/preview/stop")
    assert response.status_code == 200
    assert response.json()["streaming"] is False


def test_preview_snapshot_without_a_camera_reports_the_device(client):
    """The suite runs with camera.probe off, which must stop the service touching
    the hardware at all. A frame request therefore fails on the device, not on a
    missing preview."""
    response = client.get("/api/preview/snapshot")
    assert response.status_code == 503
    body = response.json()
    assert body["error"]["code"] == "DEVICE_NOT_FOUND"
    assert body["error"]["detail"]["reason"] == "probe_disabled"


def test_body_validation_error_is_wrapped(client):
    response = client.put("/api/project", json={"root": 12345})
    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == "REQUEST_INVALID"
    assert body["error"]["detail"]["fields"]


# -------------------------------------------------------------- contract shape

# The exact field set of the project object, from docs/API.md section 3.5. It is
# asserted rather than just spot checked because an extra field is how a response
# and the documented contract drift apart unnoticed, which is what happened with
# the session count that used to live here.
PROJECT_FIELDS = {
    "configured",
    "root",
    "name",
    "exists",
    "writable",
    "free_gb",
    "enough",
    "error",
}


def test_project_object_matches_the_documented_field_set(client):
    assert set(client.get("/api/project").json()) == PROJECT_FIELDS


def test_health_project_matches_the_documented_field_set(client):
    body = client.get("/api/health").json()
    assert set(body["project"]) == PROJECT_FIELDS
    assert set(body) == {"status", "device", "project", "guides", "active_session"}


def test_health_device_matches_the_documented_field_set(client):
    body = client.get("/api/health").json()
    assert set(body["device"]) == {
        "connected",
        "name",
        "serial",
        "firmware",
        "usb_type",
        "reason",
    }
    assert set(body["guides"]) == {"ready", "uploaded", "total", "missing_indices"}


def test_guide_entry_matches_the_documented_field_set(client):
    expected = {
        "index",
        "name",
        "name_custom",
        "configured",
        "image_url",
        "original_filename",
        "content_type",
        "size_bytes",
        "width",
        "height",
        "uploaded_at",
        "sha256",
        "instructions",
        "instructions_custom",
    }
    assert set(client.get("/api/guides").json()["guides"][0]) == expected

    uploaded = client.post(
        "/api/guides/1", files={"file": ("a.png", png_bytes(), "image/png")}
    ).json()
    assert set(uploaded) == expected | {"generated_preview", "ready"}


def test_listing_matches_the_documented_field_set(client, env):
    body = client.get("/api/fs/list", params={"path": str(env["library"])}).json()
    assert set(body) == {
        "path",
        "parent",
        "name",
        "home",
        "shortcuts",
        "readable",
        "writable",
        "free_gb",
        "enough",
        "error",
        "entries",
    }
    assert set(body["entries"][0]) == {
        "name",
        "path",
        "writable",
        "is_symlink",
        "looks_like_session",
    }


# ------------------------------------------------------------------ project

def test_project_starts_unconfigured(client):
    body = client.get("/api/project").json()
    assert body["configured"] is False
    assert body["root"] is None
    assert body["error"]


def test_set_project_creates_and_persists_it(client, env):
    target = env["library"] / "Nested" / "Deep"
    response = client.put("/api/project", json={"root": str(target)})
    assert response.status_code == 200

    body = response.json()
    assert body["configured"] is True
    assert body["root"] == str(target)
    assert body["name"] == "Deep"
    assert body["exists"] is True
    assert body["writable"] is True
    assert body["enough"] is True
    assert body["error"] is None
    assert target.is_dir()

    settings = json.loads((env["base"] / "var" / "settings.json").read_text())
    assert settings["project_root"] == str(target)
    assert settings["version"] == 1

    # The health check must pick it up without another write.
    assert client.get("/api/health").json()["project"]["root"] == str(target)


def test_set_project_normalises_redundant_segments(client, env):
    messy = f"{env['library']}//ProjectA/./"
    response = client.put("/api/project", json={"root": messy})
    assert response.status_code == 200
    assert response.json()["root"] == str(env["library"] / "ProjectA")


def test_set_project_rejects_a_parent_reference(client, env):
    """Different from the picker, which resolves .. because Up produces it."""
    response = client.put(
        "/api/project", json={"root": f"{env['library']}/ProjectA/../ProjectA"}
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "PROJECT_PATH_INVALID"


@pytest.mark.parametrize(
    "bad",
    [
        "relative/path",
        "./here",
        "/",
        "/Users",
        "~/../escape",
        "/tmp/../etc",
        " ",
    ],
)
def test_invalid_project_paths_are_rejected(client, bad):
    response = client.put("/api/project", json={"root": bad})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "PROJECT_PATH_INVALID"


def test_project_outside_allow_range_is_refused(client, tmp_path):
    response = client.put("/api/project", json={"root": str(tmp_path / "elsewhere")})
    assert response.status_code == 403
    body = response.json()
    assert body["error"]["code"] == "PATH_NOT_ALLOWED"
    assert body["error"]["detail"]["allow_roots"]
    assert not (tmp_path / "elsewhere").exists()


def test_project_path_that_is_a_file_is_refused(client, env):
    target = env["library"] / "notafolder"
    target.write_text("x", encoding="utf-8")
    response = client.put("/api/project", json={"root": str(target)})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "PROJECT_PATH_INVALID"


def test_failed_change_keeps_the_previous_root(client, env):
    good = env["library"] / "Keep"
    assert client.put("/api/project", json={"root": str(good)}).status_code == 200

    assert client.put("/api/project", json={"root": "nope"}).status_code == 400

    body = client.get("/api/project").json()
    assert body["root"] == str(good)
    settings = json.loads((env["base"] / "var" / "settings.json").read_text())
    assert settings["project_root"] == str(good)


def test_low_disk_space_is_refused(tmp_path):
    library = tmp_path / "library"
    library.mkdir()
    config = write_config(tmp_path, allow_roots=[str(library)], min_free_space_gb=10 ** 9)
    with TestClient(create_app(config)) as client:
        response = client.put("/api/project", json={"root": str(library)})
        assert response.status_code == 507
        assert response.json()["error"]["code"] == "DISK_SPACE_LOW"

        # The listing must say the same thing so the picker can disable itself.
        listing = client.get("/api/fs/list", params={"path": str(library)}).json()
        assert listing["enough"] is False
        assert listing["error"]


def test_configured_root_survives_a_tightened_allow_range(env):
    """docs/API.md section 4.5: the folder in use is always in range."""
    target = env["library"] / "Locked"
    with TestClient(create_app(env["config"])) as client:
        assert client.put("/api/project", json={"root": str(target)}).status_code == 200

    # Same project directory, but the allow range no longer covers the library.
    narrowed = env["base"] / "conf-narrow"
    narrowed.mkdir()
    config = write_config(env["base"], allow_roots=[str(narrowed)])
    with TestClient(create_app(config)) as client:
        assert client.get("/api/health").json()["project"]["root"] == str(target)
        listing = client.get("/api/fs/list", params={"path": str(target)}).json()
        assert listing["path"] == str(target)


# --------------------------------------------------------------- filesystem

def host_volumes() -> List[str]:
    r"""Volume roots that exist on this host, in drive order.

    Empty off Windows, where a drive letter is not how volumes are named. Used by
    the tests that need a real volume root to exercise Places, since a volume has
    to be one the host actually has or the code under test filters it out.
    """
    if os.name != "nt":
        return []
    return [
        f"{letter}:\\"
        for letter in string.ascii_uppercase
        if os.path.isdir(f"{letter}:\\")
    ]


def absent_drive_letter() -> str:
    """A drive letter with nothing mounted on it."""
    for letter in reversed(string.ascii_uppercase):
        if not os.path.isdir(f"{letter}:\\"):
            return letter
    raise AssertionError("every drive letter is in use")


def test_listing_reports_parents_entries_and_flags(client, env):
    (env["library"] / "ProjectA" / "session_one").mkdir()
    (env["library"] / "ProjectA" / "session_one" / "session.json").write_text("{}")
    (env["library"] / ".hidden").mkdir()

    body = client.get("/api/fs/list", params={"path": str(env["library"])}).json()

    assert body["path"] == str(env["library"])
    assert body["name"] == "library"
    assert body["readable"] is True
    assert body["home"]
    names = [entry["name"] for entry in body["entries"]]
    assert names == ["ProjectA"], "dot folders are excluded by default"
    assert body["entries"][0]["looks_like_session"] is False
    assert isinstance(body["shortcuts"], list)

    inside = client.get(
        "/api/fs/list", params={"path": str(env["library"] / "ProjectA"), "show_hidden": "true"}
    ).json()
    session = next(e for e in inside["entries"] if e["name"] == "session_one")
    assert session["looks_like_session"] is True


def test_listing_sorts_naturally(client, env):
    for name in ("run10", "run2", "run1"):
        (env["library"] / name).mkdir()
    body = client.get("/api/fs/list", params={"path": str(env["library"])}).json()
    runs = [e["name"] for e in body["entries"] if e["name"].startswith("run")]
    assert runs == ["run1", "run2", "run10"]


def test_listing_parent_is_null_at_the_range_edge(client, env):
    body = client.get("/api/fs/list", params={"path": str(env["library"])}).json()
    # library's parent exists but is outside allow_roots, so Up must disable.
    assert body["parent"] is None


def test_listing_does_not_create_directories(client, env):
    missing = env["library"] / "ghost"
    response = client.get("/api/fs/list", params={"path": str(missing)})
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "DIR_NOT_FOUND"
    assert not missing.exists()


def test_listing_outside_the_allowed_range(client, tmp_path):
    response = client.get("/api/fs/list", params={"path": str(tmp_path)})
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "PATH_NOT_ALLOWED"


def test_listing_defaults_are_out_of_range_in_this_config(client):
    response = client.get("/api/fs/list")
    assert response.status_code == 403


def test_listing_reports_the_root_of_a_volume_as_unusable(client, env):
    body = client.get("/api/fs/list", params={"path": str(env["base"])}).json()
    assert body["error"]


def test_places_offer_only_the_allowed_volumes(client, env):
    """Drives, not person-centric folders. This picker chooses where recordings
    go, so a desktop or downloads entry is noise that buries the drives."""
    # The test range is a folder, not a volume, so Places is empty by rule.
    body = client.get("/api/fs/list", params={"path": str(env["library"])}).json()
    assert body["shortcuts"] == []


@pytest.mark.skipif(os.name != "nt", reason="drive letters are a Windows concept")
def test_places_list_the_configured_volumes_with_drive_labels(tmp_path):
    system = host_volumes()[0]
    config = write_config(tmp_path, allow_roots=[str(tmp_path / "library"), system])
    (tmp_path / "library").mkdir(exist_ok=True)

    with TestClient(create_app(config)) as client:
        body = client.get("/api/fs/list", params={"path": str(tmp_path / "library")}).json()

    assert body["shortcuts"] == [{"name": system.rstrip("\\").upper(), "path": system}]
    names = [item["name"] for item in body["shortcuts"]]
    assert not {"Home", "Desktop", "Documents", "Downloads", "Volumes", "Media"} & set(names)
    # And every place must be usable, which was the old test's point.
    for item in body["shortcuts"]:
        assert Path(item["path"]).is_dir()


@pytest.mark.skipif(os.name != "nt", reason="drive letters are a Windows concept")
def test_places_leave_out_a_configured_volume_that_is_absent(tmp_path):
    """allow_roots is a standing permission, so it can name a removable drive that
    is not plugged in. Offering a jump point that always fails is worse than not
    offering it."""
    library = tmp_path / "library"
    library.mkdir()
    absent = f"{absent_drive_letter()}:\\"
    config = write_config(tmp_path, allow_roots=[str(library), host_volumes()[0], absent])

    with TestClient(create_app(config)) as client:
        body = client.get("/api/fs/list", params={"path": str(library)}).json()

    assert absent not in [item["path"] for item in body["shortcuts"]]
    assert len(body["shortcuts"]) == 1


def test_the_label_of_a_volume_is_its_drive_letter(tmp_path):
    """Checked directly, because it needs a volume root that is a volume root."""
    library = tmp_path / "library"
    library.mkdir()
    config = write_config(tmp_path, allow_roots=[str(library)])

    with TestClient(create_app(config)) as client:
        browser = client.app.state.services.fsbrowser

    assert browser._volume_label("C:\\") == "C:"
    assert browser._volume_label("d:\\") == "D:", "case is normalised for the label"
    assert browser._volume_label("/") == "/"
    assert browser._volume_label("/mnt") == "/mnt"


def test_the_picker_opens_on_the_project_when_one_is_set(client, env):
    target = env["library"] / "ProjectA"
    assert client.put("/api/project", json={"root": str(target)}).status_code == 200
    body = client.get("/api/fs/list").json()
    assert body["path"] == str(target)


def test_the_picker_falls_back_to_home_when_no_volume_is_allowed(tmp_path):
    """Reached when allow_roots names folders only, which is the macOS shape."""
    library = tmp_path / "library"
    library.mkdir()
    config = write_config(tmp_path, allow_roots=["~", str(library)])

    with TestClient(create_app(config)) as client:
        body = client.get("/api/fs/list").json()
        assert body["shortcuts"] == []
        assert body["path"] == body["home"]


@pytest.mark.skipif(os.name != "nt", reason="drive letters are a Windows concept")
def test_the_picker_opens_on_a_data_volume_rather_than_the_system_one(tmp_path):
    """Capture data belongs on a data volume. This is what makes a fresh rig open
    on D: instead of dropping the operator into C:\\Users."""
    volumes = host_volumes()
    if len(volumes) < 2:
        pytest.skip("this host has a single volume, so there is no data volume to prefer")
    library = tmp_path / "library"
    library.mkdir()
    config = write_config(tmp_path, allow_roots=[str(library), *volumes])

    with TestClient(create_app(config)) as client:
        body = client.get("/api/fs/list").json()

    assert body["path"] != os.environ.get("SystemDrive", "C:") + "\\"
    assert body["path"] in volumes
    # The chosen volume is also the one flagged as current in the sidebar.
    assert body["path"] in [item["path"] for item in body["shortcuts"]]


@pytest.mark.skipif(os.name != "nt", reason="drive letters are a Windows concept")
def test_the_picker_opens_on_the_system_volume_when_it_is_the_only_one(tmp_path):
    library = tmp_path / "library"
    library.mkdir()
    config = write_config(tmp_path, allow_roots=[str(library), host_volumes()[0]])

    with TestClient(create_app(config)) as client:
        body = client.get("/api/fs/list").json()

    assert body["path"] == host_volumes()[0]
    assert body["shortcuts"][0]["path"] == body["path"]


def test_mkdir_creates_one_level_and_returns_the_parent_listing(client, env):
    response = client.post(
        "/api/fs/mkdir", json={"parent": str(env["library"]), "name": "Fresh"}
    )
    assert response.status_code == 201

    body = response.json()
    created = env["library"] / "Fresh"
    assert body["path"] == str(created)
    assert created.is_dir()
    assert body["listing"]["path"] == str(env["library"])
    assert "Fresh" in [entry["name"] for entry in body["listing"]["entries"]]
    # Redundant with the 201, so it must not be there.
    assert "created" not in body


@pytest.mark.parametrize("name", ["", ".hidden", "has space", "-lead", "a/b", "x" * 65, ".."])
def test_mkdir_rejects_bad_names(client, env, name):
    response = client.post("/api/fs/mkdir", json={"parent": str(env["library"]), "name": name})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "DIR_NAME_INVALID"


def test_mkdir_refuses_an_existing_directory(client, env):
    response = client.post(
        "/api/fs/mkdir", json={"parent": str(env["library"]), "name": "ProjectA"}
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "DIR_EXISTS"


def test_mkdir_needs_an_existing_parent(client, env):
    response = client.post(
        "/api/fs/mkdir", json={"parent": str(env["library"] / "absent"), "name": "New"}
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "DIR_NOT_FOUND"


def test_mkdir_outside_the_range(client, tmp_path):
    response = client.post(
        "/api/fs/mkdir", json={"parent": str(tmp_path), "name": "New"}
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "PATH_NOT_ALLOWED"


# ------------------------------------------------------------------- guides

def test_guides_start_empty(client):
    body = client.get("/api/guides").json()
    assert body["required"] is True
    assert body["ready"] is False
    assert body["total"] == 8
    assert body["uploaded"] == 0
    assert body["missing_indices"] == list(range(1, 9))
    assert body["guides"][0]["configured"] is False
    assert body["guides"][0]["image_url"] is None
    assert body["guides"][0]["sha256"] is None
    assert body["guides"][0]["name"] == "Stage 1"


def test_single_upload_and_serving(client, env):
    response = client.post(
        "/api/guides/1", files={"file": ("diagram.png", png_bytes(), "image/png")}
    )
    assert response.status_code == 200
    body = response.json()

    assert body["configured"] is True
    assert body["index"] == 1
    assert body["content_type"] == "image/png"
    assert body["width"] == 40 and body["height"] == 30
    assert body["original_filename"] == "diagram.png"
    assert body["generated_preview"] is False, "a 40px image needs no downscale"
    assert body["ready"] is False
    assert len(body["sha256"]) == 64

    stored = env["base"] / "var" / "guides" / "stage_01.png"
    assert stored.is_file()

    # Both sizes resolve, and a small original is served for either request.
    for size in ("display", "original"):
        image = client.get(f"/api/guides/1/image?size={size}")
        assert image.status_code == 200
        assert image.headers["content-type"] == "image/png"
        assert image.content == png_bytes()

    manifest = json.loads((env["base"] / "var" / "guides" / "manifest.json").read_text())
    assert manifest["version"] == 1
    assert manifest["stages"]["1"]["file"] == "stage_01.png"


def test_wide_upload_generates_a_display_copy(client, env):
    response = client.post(
        "/api/guides/2", files={"file": ("wide.png", png_bytes(3000, 1000), "image/png")}
    )
    body = response.json()
    assert body["generated_preview"] is True

    guides_dir = env["base"] / "var" / "guides"
    assert (guides_dir / "stage_02.png").is_file()
    assert (guides_dir / "stage_02.display.jpg").is_file()

    display = client.get("/api/guides/2/image")
    original = client.get("/api/guides/2/image?size=original")
    assert display.headers["content-type"] == "image/jpeg"
    assert original.headers["content-type"] == "image/png"
    with Image.open(io.BytesIO(display.content)) as image:
        assert image.width == 1600
        assert image.height == 533


def test_replacing_with_the_other_format_removes_the_old_file(client, env):
    guides_dir = env["base"] / "var" / "guides"
    client.post("/api/guides/3", files={"file": ("a.png", png_bytes(), "image/png")})
    assert (guides_dir / "stage_03.png").is_file()

    client.post("/api/guides/3", files={"file": ("b.jpg", jpeg_bytes(), "image/jpeg")})
    assert (guides_dir / "stage_03.jpg").is_file()
    assert not (guides_dir / "stage_03.png").exists(), "stale original would still be reachable"

    served = client.get("/api/guides/3/image?size=original")
    assert served.headers["content-type"] == "image/jpeg"


def test_upload_rejects_a_bad_declared_type(client):
    response = client.post(
        "/api/guides/1", files={"file": ("a.tiff", b"II*\x00", "image/tiff")}
    )
    assert response.status_code == 415
    assert response.json()["error"]["code"] == "GUIDE_UNSUPPORTED_TYPE"


def test_a_mislabelled_but_accepted_format_is_stored_under_its_real_type(client):
    """A wrong declared type is not fatal when the bytes really are acceptable."""
    response = client.post(
        "/api/guides/1", files={"file": ("photo.gif", jpeg_bytes(), "image/gif")}
    )
    assert response.status_code == 200
    assert response.json()["content_type"] == "image/jpeg"


def test_upload_rejects_an_oversized_file(client, env):
    config = load_config(env["config"])
    too_big = b"\x89PNG\r\n\x1a\n" + b"0" * (config.guide_max_size_bytes + 10)
    response = client.post("/api/guides/1", files={"file": ("big.png", too_big, "image/png")})
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "GUIDE_TOO_LARGE"


def test_upload_rejects_undecodable_bytes(client):
    response = client.post(
        "/api/guides/1", files={"file": ("fake.png", b"not an image at all", "image/png")}
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "GUIDE_INVALID_IMAGE"


def test_upload_rejects_a_real_image_in_the_wrong_container(client):
    """A JPEG labelled image/png passes the declared type check, then fails.

    Renaming alone does not get a rejected format past the decode step, and a
    GIF is no longer a convenient stand-in for "rejected" now that it is accepted.
    """
    buffer = io.BytesIO()
    Image.new("RGB", (40, 30), (5, 5, 5)).save(buffer, format="TIFF")
    response = client.post(
        "/api/guides/1", files={"file": ("lie.png", buffer.getvalue(), "image/png")}
    )
    assert response.status_code == 415


def test_upload_rejects_an_out_of_range_stage(client):
    response = client.post(
        "/api/guides/99", files={"file": ("a.png", png_bytes(), "image/png")}
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "STAGE_NOT_FOUND"


def test_a_rejected_upload_keeps_the_previous_diagram(client):
    assert client.post(
        "/api/guides/1", files={"file": ("good.png", png_bytes(), "image/png")}
    ).status_code == 200
    before = client.get("/api/guides").json()["guides"][0]["sha256"]

    assert client.post(
        "/api/guides/1", files={"file": ("bad.png", b"rubbish", "image/png")}
    ).status_code == 400

    assert client.get("/api/guides").json()["guides"][0]["sha256"] == before


def test_batch_is_independent_and_never_rolls_back(client):
    response = client.post(
        "/api/guides/batch",
        files={
            "stage_1": ("a.png", png_bytes(), "image/png"),
            "stage_3": ("c.png", b"broken", "image/png"),
            "stage_4": ("d.jpg", jpeg_bytes(), "image/jpeg"),
        },
    )
    assert response.status_code == 200
    body = response.json()

    assert body["applied"] == [1, 4]
    assert [item["index"] for item in body["failed"]] == [3]
    assert body["failed"][0]["code"] == "GUIDE_INVALID_IMAGE"
    assert body["guides"]["uploaded"] == 2
    assert 3 in body["guides"]["missing_indices"]


def test_batch_ignores_fields_that_are_not_stage_uploads(client):
    response = client.post(
        "/api/guides/batch",
        data={"note": "hello"},
        files={"stage_2": ("b.png", png_bytes(), "image/png")},
    )
    assert response.status_code == 200
    assert response.json()["applied"] == [2]


def test_full_catalogue_becomes_ready(client):
    for index in range(1, 9):
        assert client.post(
            f"/api/guides/{index}",
            files={"file": (f"s{index}.png", png_bytes(), "image/png")},
        ).status_code == 200

    body = client.get("/api/guides").json()
    assert body["ready"] is True
    assert body["uploaded"] == 8
    assert body["missing_indices"] == []
    assert all(entry["configured"] for entry in body["guides"])


def test_delete_and_its_404(client):
    response = client.delete("/api/guides/5")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "GUIDE_NOT_FOUND"

    client.post("/api/guides/5", files={"file": ("e.png", png_bytes(), "image/png")})
    response = client.delete("/api/guides/5")
    assert response.status_code == 200
    body = response.json()
    assert body == {"index": 5, "configured": False, "ready": False, "uploaded": 0}


def test_missing_image_is_a_404(client):
    response = client.get("/api/guides/4/image")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "GUIDE_NOT_FOUND"


# ------------------------------------------------- guide titles and descriptions

def test_description_defaults_to_the_yaml_instructions(client):
    entry = client.get("/api/guides").json()["guides"][0]
    assert entry["instructions"] == "Do the thing for stage 1."
    assert entry["instructions_custom"] is False


def test_description_can_be_written_without_a_diagram(client):
    """The text and the image are independent, either may come first."""
    response = client.put("/api/guides/3", json={"instructions": "Kneel and hold still."})
    assert response.status_code == 200
    body = response.json()
    assert body["configured"] is False
    assert body["instructions"] == "Kneel and hold still."
    assert body["instructions_custom"] is True
    # Readiness still tracks images only, a written description is not a diagram.
    assert body["uploaded"] == 0


def test_description_overrides_the_config_endpoint(client):
    client.put("/api/guides/2", json={"instructions": "Stand further back."})
    stages = client.get("/api/config").json()["stages"]
    assert stages[1]["instructions"] == "Stand further back."
    assert stages[0]["instructions"] == "Do the thing for stage 1."


def test_description_survives_a_restart(env):
    with TestClient(create_app(env["config"])) as first:
        first.put("/api/guides/1", json={"instructions": "Written before the restart."})

    with TestClient(create_app(env["config"])) as second:
        assert second.get("/api/guides").json()["guides"][0]["instructions"] == (
            "Written before the restart."
        )
        assert second.get("/api/config").json()["stages"][0]["instructions"] == (
            "Written before the restart."
        )


def test_an_empty_description_clears_the_override(client):
    client.put("/api/guides/1", json={"instructions": "Temporary wording."})
    response = client.put("/api/guides/1", json={"instructions": "   "})
    assert response.status_code == 200
    assert response.json()["instructions"] == "Do the thing for stage 1."
    assert response.json()["instructions_custom"] is False


def test_description_is_trimmed(client):
    response = client.put("/api/guides/1", json={"instructions": "  Padded. \n"})
    assert response.json()["instructions"] == "Padded."


def test_an_over_long_description_is_rejected(client):
    response = client.put("/api/guides/1", json={"instructions": "x" * 501})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "GUIDE_TEXT_TOO_LONG"


def test_description_index_must_be_a_stage(client):
    response = client.put("/api/guides/99", json={"instructions": "Nowhere."})
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "STAGE_NOT_FOUND"


def test_removing_the_diagram_keeps_the_description(client):
    """The two fields are edited separately, so one must not discard the other."""
    client.post("/api/guides/1", files={"file": ("a.png", png_bytes(), "image/png")})
    client.put("/api/guides/1", json={"instructions": "Keep me."})

    client.delete("/api/guides/1")

    entry = client.get("/api/guides").json()["guides"][0]
    assert entry["configured"] is False
    assert entry["instructions"] == "Keep me."


def test_replacing_the_diagram_keeps_the_description(client):
    client.put("/api/guides/1", json={"instructions": "Keep me too."})
    client.post("/api/guides/1", files={"file": ("a.png", png_bytes(), "image/png")})
    assert client.get("/api/guides").json()["guides"][0]["instructions"] == "Keep me too."


def test_manifest_stores_descriptions_outside_the_stage_entries(client, env):
    client.post("/api/guides/1", files={"file": ("a.png", png_bytes(), "image/png")})
    client.put("/api/guides/1", json={"instructions": "Written by the operator."})

    manifest = json.loads((env["base"] / "var" / "guides" / "manifest.json").read_text())
    assert manifest["instructions"] == {"1": "Written by the operator."}
    assert "instructions" not in manifest["stages"]["1"]


# -------------------------------------------------------------- guide titles

def test_title_defaults_to_the_yaml_stage_name(client):
    entry = client.get("/api/guides").json()["guides"][0]
    assert entry["name"] == "Stage 1"
    assert entry["name_custom"] is False


def test_title_can_be_written_without_a_diagram(client):
    """The title and the image are independent, either may come first."""
    response = client.put("/api/guides/3", json={"name": "Shared build"})
    assert response.status_code == 200
    body = response.json()
    assert body["configured"] is False
    assert body["name"] == "Shared build"
    assert body["name_custom"] is True


def test_title_overrides_the_config_endpoint(client):
    client.put("/api/guides/2", json={"name": "Front left, wide"})
    stages = client.get("/api/config").json()["stages"]
    assert stages[1]["name"] == "Front left, wide"
    assert stages[0]["name"] == "Stage 1"


def test_title_survives_a_restart(env):
    with TestClient(create_app(env["config"])) as first:
        first.put("/api/guides/1", json={"name": "Written before the restart."})

    with TestClient(create_app(env["config"])) as second:
        assert second.get("/api/guides").json()["guides"][0]["name"] == (
            "Written before the restart."
        )
        assert second.get("/api/config").json()["stages"][0]["name"] == (
            "Written before the restart."
        )


def test_an_empty_title_clears_the_override(client):
    client.put("/api/guides/1", json={"name": "Temporary title."})
    response = client.put("/api/guides/1", json={"name": "   "})
    assert response.status_code == 200
    assert response.json()["name"] == "Stage 1"
    assert response.json()["name_custom"] is False


@pytest.mark.parametrize(
    "written,expected",
    [
        ("  Padded.  ", "Padded."),
        ("Two\nlines", "Two lines"),
        ("Excel\u000bcell", "Excel cell"),
        ("Tabs\tand   runs", "Tabs and runs"),
    ],
)
def test_a_written_title_is_folded_onto_one_line(client, written, expected):
    """A title is rendered inline, so a pasted multi line heading is flattened."""
    response = client.put("/api/guides/1", json={"name": written})
    assert response.json()["name"] == expected


def test_a_hand_edited_name_is_folded_on_load(env):
    """The fold also applies to a manifest edited by hand, not just the API."""
    guides_dir = env["base"] / "var" / "guides"
    guides_dir.mkdir(parents=True, exist_ok=True)
    (guides_dir / "manifest.json").write_text(
        json.dumps({"version": 1, "stages": {}, "names": {"1": "  Two\nlines  "}}),
        encoding="utf-8",
    )

    with TestClient(create_app(env["config"])) as client:
        entry = client.get("/api/guides").json()["guides"][0]
        assert entry["name"] == "Two lines"
        assert entry["name_custom"] is True

    response = client.put("/api/guides/1", json={"name": "x" * 61})
    assert response.status_code == 400
    body = response.json()["error"]
    assert body["code"] == "GUIDE_TEXT_TOO_LONG"
    assert body["detail"]["field"] == "name"


def test_an_over_long_description_names_the_field(client):
    response = client.put("/api/guides/1", json={"instructions": "x" * 501})
    assert response.status_code == 400
    assert response.json()["error"]["detail"]["field"] == "instructions"


def test_title_index_must_be_a_stage(client):
    response = client.put("/api/guides/99", json={"name": "Nowhere."})
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "STAGE_NOT_FOUND"


def test_updating_a_title_leaves_the_description_alone(client):
    """The two fields are edited separately, so one must not discard the other."""
    client.put("/api/guides/1", json={"instructions": "Keep me."})
    client.put("/api/guides/1", json={"name": "Renamed"})

    entry = client.get("/api/guides").json()["guides"][0]
    assert entry["name"] == "Renamed"
    assert entry["instructions"] == "Keep me."

    client.put("/api/guides/1", json={"instructions": "Changed again."})
    entry = client.get("/api/guides").json()["guides"][0]
    assert entry["name"] == "Renamed"
    assert entry["instructions"] == "Changed again."


def test_both_fields_can_be_set_in_one_call(client):
    response = client.put(
        "/api/guides/2", json={"name": "Renamed", "instructions": "And rewritten."}
    )
    body = response.json()
    assert body["name"] == "Renamed"
    assert body["instructions"] == "And rewritten."


def test_an_absent_field_leaves_the_stored_value_alone(client):
    """Presence decides what is applied, so an empty body is a no-op."""
    client.put("/api/guides/1", json={"name": "Renamed", "instructions": "And rewritten."})

    response = client.put("/api/guides/1", json={})

    assert response.status_code == 200
    assert response.json()["name"] == "Renamed"
    assert response.json()["instructions"] == "And rewritten."


def test_an_over_long_title_does_not_apply_the_description(client):
    """Both values are validated before either is written."""
    client.put("/api/guides/1", json={"instructions": "Original."})
    response = client.put(
        "/api/guides/1", json={"name": "x" * 61, "instructions": "Should not land."}
    )
    assert response.status_code == 400
    entry = client.get("/api/guides").json()["guides"][0]
    assert entry["name"] == "Stage 1"
    assert entry["instructions"] == "Original."


def test_removing_the_diagram_keeps_the_title(client):
    client.post("/api/guides/1", files={"file": ("a.png", png_bytes(), "image/png")})
    client.put("/api/guides/1", json={"name": "Keep me."})

    client.delete("/api/guides/1")

    entry = client.get("/api/guides").json()["guides"][0]
    assert entry["configured"] is False
    assert entry["name"] == "Keep me."


def test_replacing_the_diagram_keeps_the_title(client):
    client.put("/api/guides/1", json={"name": "Keep me too."})
    client.post("/api/guides/1", files={"file": ("a.png", png_bytes(), "image/png")})
    assert client.get("/api/guides").json()["guides"][0]["name"] == "Keep me too."


def test_replacing_the_diagram_reports_the_written_title(client):
    """The upload response carries the effective title, not the shipped one."""
    client.put("/api/guides/1", json={"name": "Shared build"})
    body = client.post(
        "/api/guides/1", files={"file": ("a.png", png_bytes(), "image/png")}
    ).json()
    assert body["name"] == "Shared build"
    assert body["name_custom"] is True


def test_manifest_stores_titles_outside_the_stage_entries(client, env):
    client.post("/api/guides/1", files={"file": ("a.png", png_bytes(), "image/png")})
    client.put("/api/guides/1", json={"name": "Written by the operator."})

    manifest = json.loads((env["base"] / "var" / "guides" / "manifest.json").read_text())
    assert manifest["names"] == {"1": "Written by the operator."}
    assert "name" not in manifest["stages"]["1"]


def test_a_manifest_without_the_names_key_needs_no_migration(env):
    """An older manifest simply has no key, which reads as "use the YAML"."""
    guides_dir = env["base"] / "var" / "guides"
    guides_dir.mkdir(parents=True, exist_ok=True)
    (guides_dir / "manifest.json").write_text(
        json.dumps({"version": 1, "stages": {}, "instructions": {}}), encoding="utf-8"
    )

    # A fresh app, so the manifest above goes through the startup read.
    with TestClient(create_app(env["config"])) as client:
        entry = client.get("/api/guides").json()["guides"][0]
        assert entry["name"] == "Stage 1"
        assert entry["name_custom"] is False
        assert client.get("/api/config").json()["stages"][0]["name"] == "Stage 1"


# ---------------------------------------------------------- accepted formats

@pytest.mark.parametrize(
    "fmt,mime",
    [
        ("PNG", "image/png"),
        ("JPEG", "image/jpeg"),
        ("WEBP", "image/webp"),
        ("BMP", "image/bmp"),
        ("GIF", "image/gif"),
    ],
)
def test_common_image_formats_are_accepted(client, fmt, mime):
    response = client.post(
        "/api/guides/1", files={"file": (f"a.{fmt.lower()}", image_bytes(fmt), mime)}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["content_type"] == mime
    assert body["configured"] is True


def test_png_leads_the_default_accepted_type_list(tmp_path):
    """PNG is the expected format, so a config that omits the list still leads
    with it. The picker shows the first entry by default."""
    library = tmp_path / "library"
    library.mkdir()
    config_path = write_config(tmp_path, allow_roots=[str(library)])
    payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    del payload["guides"]["allowed_types"]
    config_path.write_text(yaml.safe_dump(payload), encoding="utf-8")

    assert load_config(config_path).guide_allowed_types[0] == "image/png"


def test_the_shipped_config_leads_with_png():
    """The deployment file must agree with the code default."""
    shipped = BACKEND_DIR / "config" / "config.yaml"
    assert load_config(shipped).guide_allowed_types[0] == "image/png"


def test_a_replaced_diagram_does_not_leave_the_other_extension_behind(client, env):
    guides_dir = env["base"] / "var" / "guides"
    client.post("/api/guides/1", files={"file": ("a.png", png_bytes(), "image/png")})
    client.post("/api/guides/1", files={"file": ("a.jpg", jpeg_bytes(), "image/jpeg")})

    assert not (guides_dir / "stage_01.png").exists()
    assert (guides_dir / "stage_01.jpg").is_file()


def test_a_transparent_diagram_is_flattened_onto_white(client, env):
    """The display copy is always JPEG, so alpha has to become something."""
    wide = Image.new("RGBA", (2000, 1000), (0, 0, 0, 0))
    buffer = io.BytesIO()
    wide.save(buffer, format="PNG")

    client.post("/api/guides/1", files={"file": ("a.png", buffer.getvalue(), "image/png")})

    display = env["base"] / "var" / "guides" / "stage_01.display.jpg"
    assert display.is_file()
    with Image.open(display) as image:
        # Top left pixel is fully transparent in the source. On black it would
        # read as 0, so a white value is the proof the flatten worked.
        assert image.getpixel((0, 0)) == (255, 255, 255)


def test_image_response_is_cacheable(client):
    client.post("/api/guides/1", files={"file": ("a.png", png_bytes(), "image/png")})
    response = client.get("/api/guides/1/image?v=abc12345")
    assert response.status_code == 200
    assert "immutable" in response.headers["cache-control"]
    assert response.headers["etag"]


def test_active_session_is_reported_so_a_new_tab_can_reattach(env):
    """docs/API.md section 4.1. The field was hardcoded to null while sessions were
    unimplemented, which left a running round invisible to a fresh page: the home
    screen offered to start a new one and the backend then refused it."""
    config_path = write_config(
        env["base"], allow_roots=[str(env["library"])], guides_overrides={"seed_defaults": True}
    )
    with session_capable_client(config_path, env["library"] / "ProjectA") as client:
        assert client.get("/api/health").json()["active_session"] is None

        created = client.post("/api/sessions", json={"name": "round_one"}).json()
        active = client.get("/api/health").json()["active_session"]

        assert active is not None
        assert active["session_id"] == created["session_id"]
        assert active["name"] == "round_one"
        assert active["status"] == "in_progress"
        assert active["current_stage"] == 1
        assert active["saved_count"] == 0
        assert active["size_bytes"] == 0
        assert active["data_dir"] == created["data_dir"]
        assert set(active) == {
            "session_id",
            "name",
            "created_at",
            "finished_at",
            "status",
            "current_stage",
            "saved_count",
            "size_bytes",
            "data_dir",
        }


def test_active_session_is_dropped_once_the_round_is_discarded(env):
    config_path = write_config(
        env["base"], allow_roots=[str(env["library"])], guides_overrides={"seed_defaults": True}
    )
    with session_capable_client(config_path, env["library"] / "ProjectA") as client:
        sid = client.post("/api/sessions", json={"name": "round_one"}).json()["session_id"]
        assert client.get("/api/health").json()["active_session"] is not None

        assert client.delete(f"/api/sessions/{sid}").status_code == 200
        assert client.get("/api/health").json()["active_session"] is None


def test_active_session_survives_a_restart(env):
    """The adopted session is what makes a service restart resumable in the UI."""
    config_path = write_config(
        env["base"], allow_roots=[str(env["library"])], guides_overrides={"seed_defaults": True}
    )
    project_root = env["library"] / "ProjectA"
    with session_capable_client(config_path, project_root) as first:
        first.post("/api/sessions", json={"name": "round_one"})

    with session_capable_client(
        config_path, project_root
    ) as second:
        active = second.get("/api/health").json()["active_session"]
        assert active is not None
        assert active["name"] == "round_one"
        assert active["status"] == "in_progress"


# --------------------------------------------------------- default diagrams
def seeded_client(env, **guides_overrides):
    """A client whose configuration seeds the placeholder diagrams."""
    config_path = write_config(
        env["base"],
        allow_roots=[str(env["library"])],
        guides_overrides={"seed_defaults": True, **guides_overrides},
    )
    return create_app(config_path)


@contextmanager
def session_capable_client(config_path, project_root: Path):
    """A client that can create sessions in ``project_root``.

    The suite runs with ``camera.probe`` off, which by design refuses a real
    pipeline, so the device probe is stubbed the same way test_sessions does it.
    Without that the camera is the first precondition create_session checks and
    every session test would fail on 503.
    """
    app = create_app(config_path)
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
    with TestClient(app) as client:
        response = client.put("/api/project", json={"root": str(project_root)})
        assert response.status_code == 200, response.text
        yield client


def test_seed_defaults_is_on_in_the_shipped_config():
    """The rig has to be usable on first run without preparing eight images."""
    config = load_config(BACKEND_DIR / "config" / "config.yaml")
    assert config.guides_seed_defaults is True


def test_seed_defaults_defaults_to_on_when_the_key_is_absent(env):
    guides = load_config(env["config"])
    assert guides.guides_seed_defaults is False, "the test config sets it explicitly"

    raw = yaml.safe_load(env["config"].read_text(encoding="utf-8"))
    del raw["guides"]["seed_defaults"]
    env["config"].write_text(yaml.safe_dump(raw), encoding="utf-8")
    assert load_config(env["config"]).guides_seed_defaults is True


def test_an_empty_guide_directory_gets_the_default_set(env):
    with TestClient(seeded_client(env)) as client:
        body = client.get("/api/guides").json()
        assert body["ready"] is True
        assert body["uploaded"] == 8
        assert body["missing_indices"] == []
        assert [e["index"] for e in body["guides"] if e["configured"]] == list(range(1, 9))
        for entry in body["guides"]:
            assert entry["content_type"] == "image/png"
            assert entry["width"] == diagrams.WIDTH
            assert entry["height"] == diagrams.HEIGHT
            assert entry["original_filename"] == f"default_stage_{entry['index']:02d}.png"
            assert len(entry["sha256"]) == 64


def test_a_default_diagram_is_served_and_decodes(env):
    with TestClient(seeded_client(env)) as client:
        response = client.get("/api/guides/1/image")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("image/png")
        with Image.open(io.BytesIO(response.content)) as image:
            assert image.size == (diagrams.WIDTH, diagrams.HEIGHT)
            assert image.format == "PNG"


def test_a_default_diagram_fits_the_configured_size_limit(env):
    """max_size_mb is 1 in the test config. A default that exceeded it would be
    rejected by the same validation an operator upload goes through."""
    with TestClient(seeded_client(env)) as client:
        for entry in client.get("/api/guides").json()["guides"]:
            assert 0 < entry["size_bytes"] < 1024 * 1024


def test_a_default_diagram_needs_no_downscaled_copy(env):
    """Drawn at display_max_width, so the original is what the screen serves."""
    with TestClient(seeded_client(env)) as client:
        guides_dir = env["base"] / "var" / "guides"
        assert not list(guides_dir.glob(f"*{guides_manifest.DISPLAY_INFIX}*"))
        assert client.get("/api/guides/3/image?size=display").headers["content-type"].startswith(
            "image/png"
        )


def test_seeding_never_touches_a_directory_that_already_holds_a_diagram(env):
    guides_dir = env["base"] / "var" / "guides"
    guides_dir.mkdir(parents=True, exist_ok=True)
    (guides_dir / "stage_01.png").write_bytes(png_bytes())

    with TestClient(seeded_client(env)) as client:
        body = client.get("/api/guides").json()
        assert body["uploaded"] == 1
        assert [e["index"] for e in body["guides"] if e["configured"]] == [1]


def test_seeding_never_touches_a_directory_that_already_holds_a_manifest(env):
    guides_dir = env["base"] / "var" / "guides"
    guides_dir.mkdir(parents=True, exist_ok=True)
    (guides_dir / "manifest.json").write_text('{"version": 1, "stages": {}}', encoding="utf-8")

    with TestClient(seeded_client(env)) as client:
        body = client.get("/api/guides").json()
        assert body["uploaded"] == 0
        assert body["guides"][0]["configured"] is False


def test_deleting_one_default_keeps_it_deleted_across_a_restart(env):
    """The reason seeding is limited to a pristine directory: an operator who
    removes a placeholder to put the real diagram in must not find it back."""
    with TestClient(seeded_client(env)) as first:
        assert first.delete("/api/guides/6").status_code == 200

    with TestClient(seeded_client(env)) as second:
        body = second.get("/api/guides").json()
        assert body["uploaded"] == 7
        assert body["guides"][5]["configured"] is False
        assert body["ready"] is False


def test_wiping_the_directory_brings_the_default_set_back(env):
    guides_dir = env["base"] / "var" / "guides"
    with TestClient(seeded_client(env)) as first:
        for index in range(1, 9):
            first.delete(f"/api/guides/{index}")

    # delete() removes the files but leaves the manifest, which is not pristine.
    with TestClient(seeded_client(env)) as after_deletes:
        assert after_deletes.get("/api/guides").json()["uploaded"] == 0

    shutil.rmtree(guides_dir)
    with TestClient(seeded_client(env)) as after_wipe:
        assert after_wipe.get("/api/guides").json()["uploaded"] == 8


def test_an_upload_replaces_a_default_diagram(env):
    with TestClient(seeded_client(env)) as client:
        before = client.get("/api/guides").json()["guides"][0]["sha256"]
        response = client.post(
            "/api/guides/1", files={"file": ("real.png", png_bytes(400, 300), "image/png")}
        )
        assert response.status_code == 200
        body = response.json()
        assert body["original_filename"] == "real.png"
        assert body["width"] == 400 and body["height"] == 300
        assert body["sha256"] != before
        assert body["ready"] is True

        guides_dir = env["base"] / "var" / "guides"
        assert (guides_dir / "stage_01.png").is_file()
        assert not list(guides_dir.glob("stage_01.display*")), "400px is under the limit"


def test_seed_defaults_off_leaves_the_directory_empty(env):
    with TestClient(create_app(env["config"])) as client:
        body = client.get("/api/guides").json()
        assert body["uploaded"] == 0
        assert body["ready"] is False
        # load() still writes its own empty manifest, which is long standing
        # behaviour. What must be absent is any image.
        images = [
            path
            for path in (env["base"] / "var" / "guides").iterdir()
            if path.suffix.lower() in {".png", ".jpg", ".webp", ".bmp", ".gif"}
        ]
        assert images == []


def test_every_stage_number_gets_its_own_drawing(env):
    """Each diagram states its own stage, so no two stages share a picture."""
    with TestClient(seeded_client(env)) as client:
        digests = {e["sha256"] for e in client.get("/api/guides").json()["guides"]}
        assert len(digests) == 8


# --------------------------------------------------------------- persistence

def test_guides_survive_a_restart(env):
    with TestClient(create_app(env["config"])) as first:
        first.post("/api/guides/1", files={"file": ("a.png", png_bytes(), "image/png")})
        first.post("/api/guides/2", files={"file": ("b.png", png_bytes(), "image/png")})

    with TestClient(create_app(env["config"])) as second:
        body = second.get("/api/guides").json()
        assert body["uploaded"] == 2
        assert [e["index"] for e in body["guides"] if e["configured"]] == [1, 2]


def test_manifest_entry_is_dropped_when_its_file_disappears(env):
    with TestClient(create_app(env["config"])) as first:
        first.post("/api/guides/1", files={"file": ("a.png", png_bytes(), "image/png")})

    (env["base"] / "var" / "guides" / "stage_01.png").unlink()

    with TestClient(create_app(env["config"])) as second:
        body = second.get("/api/guides").json()
        assert body["uploaded"] == 0
        assert body["guides"][0]["configured"] is False


def test_manifest_is_rebuilt_from_files_on_disk(env):
    guides_dir = env["base"] / "var" / "guides"
    guides_dir.mkdir(parents=True, exist_ok=True)
    (guides_dir / "stage_04.png").write_bytes(png_bytes())
    (guides_dir / "stage_05.jpg").write_bytes(jpeg_bytes())

    with TestClient(create_app(env["config"])) as client:
        body = client.get("/api/guides").json()
        configured = [e["index"] for e in body["guides"] if e["configured"]]
        assert configured == [4, 5]
        assert body["uploaded"] == 2

    manifest = json.loads((guides_dir / "manifest.json").read_text())
    assert manifest["stages"]["4"]["content_type"] == "image/png"
    assert manifest["stages"]["5"]["content_type"] == "image/jpeg"
    assert manifest["stages"]["4"]["original_filename"] == "stage_04.png"


def test_previous_manifest_is_backed_up(env):
    guides_dir = env["base"] / "var" / "guides"
    with TestClient(create_app(env["config"])) as client:
        client.post("/api/guides/1", files={"file": ("a.png", png_bytes(), "image/png")})
        client.post("/api/guides/2", files={"file": ("b.png", png_bytes(), "image/png")})
    assert (guides_dir / "manifest.json.bak").is_file()


def test_broken_settings_file_falls_back_to_the_yaml_default(tmp_path):
    library = tmp_path / "library"
    library.mkdir()
    default_root = library / "FromYaml"

    config = write_config(
        tmp_path, allow_roots=[str(library)], default_root=str(default_root)
    )
    var = tmp_path / "var"
    var.mkdir(parents=True, exist_ok=True)
    (var / "settings.json").write_text("{ this is not json", encoding="utf-8")

    with TestClient(create_app(config)) as client:
        body = client.get("/api/project").json()
        assert body["root"] == str(default_root)
        assert default_root.is_dir(), "the service creates a missing default root"


def test_settings_file_overrides_the_yaml_default(tmp_path):
    library = tmp_path / "library"
    library.mkdir()
    chosen = library / "Chosen"

    config = write_config(
        tmp_path, allow_roots=[str(library)], default_root=str(library / "FromYaml")
    )
    with TestClient(create_app(config)) as client:
        client.put("/api/project", json={"root": str(chosen)})

    with TestClient(create_app(config)) as second:
        assert second.get("/api/project").json()["root"] == str(chosen)


def test_project_directory_is_created_at_startup_when_missing(tmp_path):
    library = tmp_path / "library"
    library.mkdir()
    target = library / "Made" / "ByStartup"
    config = write_config(tmp_path, allow_roots=[str(library)], default_root=str(target))

    with TestClient(create_app(config)) as client:
        assert client.get("/api/project").json()["exists"] is True
    assert target.is_dir()


# ------------------------------------------------------------ config checks

def test_stage_table_must_be_contiguous(tmp_path):
    library = tmp_path / "library"
    library.mkdir()
    config_path = write_config(tmp_path, allow_roots=[str(library)])
    payload = yaml.safe_load(config_path.read_text())
    payload["stages"] = payload["stages"][:3] + [
        {"index": 9, "name": "Gap", "instructions": "", "max_duration_s": 10}
    ]
    config_path.write_text(yaml.safe_dump(payload), encoding="utf-8")

    with pytest.raises(ConfigError, match="contiguous"):
        load_config(config_path)


def test_stage_indices_must_be_unique(tmp_path):
    library = tmp_path / "library"
    library.mkdir()
    config_path = write_config(tmp_path, allow_roots=[str(library)])
    payload = yaml.safe_load(config_path.read_text())
    payload["stages"][1]["index"] = 1
    config_path.write_text(yaml.safe_dump(payload), encoding="utf-8")

    with pytest.raises(ConfigError, match="duplicate"):
        load_config(config_path)


def test_stage_needs_a_name(tmp_path):
    library = tmp_path / "library"
    library.mkdir()
    config_path = write_config(tmp_path, allow_roots=[str(library)])
    payload = yaml.safe_load(config_path.read_text())
    payload["stages"][0]["name"] = "  "
    config_path.write_text(yaml.safe_dump(payload), encoding="utf-8")

    with pytest.raises(ConfigError, match="no name"):
        load_config(config_path)


def test_relative_service_paths_resolve_against_the_config_file(tmp_path):
    """Not against the shell's cwd, which is what makes the rig reproducible."""
    library = tmp_path / "library"
    library.mkdir()
    config_path = write_config(tmp_path, allow_roots=[str(library)])
    config = load_config(config_path)

    config_dir = config_path.parent
    assert config.service_dir == (config_dir / "../var").resolve()
    assert config.guide_root == (config_dir / "../var" / "guides").resolve()
    assert config.settings_file == (config_dir / "../var" / "settings.json").resolve()


def test_stage_count_is_not_hardcoded(tmp_path):
    library = tmp_path / "library"
    library.mkdir()
    config_path = write_config(tmp_path, allow_roots=[str(library)], stages=3)

    with TestClient(create_app(config_path)) as client:
        assert client.get("/api/config").json()["total_stages"] == 3
        guides = client.get("/api/guides").json()
        assert guides["total"] == 3
        assert guides["missing_indices"] == [1, 2, 3]
        # A fourth stage no longer exists, so it must be out of range.
        response = client.post("/api/guides/4", files={"file": ("a.png", png_bytes(), "image/png")})
        assert response.status_code == 404
