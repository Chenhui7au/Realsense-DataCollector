"""Tests for the non camera endpoints.

Run with the conda base interpreter, from the repository root:

    python -m pytest backend/tests -q

Every test builds a throwaway config in a tmp directory, so nothing touches the
real service directory and the suite can run on a machine with no camera.
"""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient
from PIL import Image

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

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
        "app": {"title": "D435i Capture", "host": "127.0.0.1", "port": 8123},
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
    assert body["app_title"] == "D435i Capture"
    assert body["total_stages"] == 8
    assert body["recording"] == {"min_duration_s": 1, "max_duration_s_default": 300}
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


def test_unknown_api_route_uses_the_envelope(client):
    response = client.get("/api/does-not-exist")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


def test_pending_camera_routes_answer_501(client):
    response = client.post("/api/sessions")
    assert response.status_code == 501
    assert response.json()["error"]["code"] == "NOT_IMPLEMENTED"


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


def test_shortcuts_offer_only_reachable_places(tmp_path):
    """A shortcut that would be refused as out of range is worse than no shortcut."""
    library = tmp_path / "library"
    library.mkdir()
    config = write_config(tmp_path, allow_roots=["~", str(library)])

    with TestClient(create_app(config)) as client:
        body = client.get("/api/fs/list", params={"path": str(library)}).json()
        names = [item["name"] for item in body["shortcuts"]]
        assert "Home" in names
        # Checked against the range, not against the host's permission model. A
        # macOS TCC restricted folder can still answer 403 DIR_NOT_READABLE, and
        # that is not what this test is about.
        for shortcut in body["shortcuts"]:
            response = client.get("/api/fs/list", params={"path": shortcut["path"]})
            assert response.status_code != 403 or (
                response.json()["error"]["code"] != "PATH_NOT_ALLOWED"
            )


def test_configured_project_appears_as_a_shortcut(client, env):
    target = env["library"] / "ProjectA"
    assert client.put("/api/project", json={"root": str(target)}).status_code == 200
    body = client.get("/api/fs/list", params={"path": str(env["library"])}).json()
    shortcut = next(item for item in body["shortcuts"] if item["name"] == "Project")
    assert shortcut["path"] == str(target)
    assert str(Path.home()) not in [item["path"] for item in body["shortcuts"]]


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


# ------------------------------------------------------- guide descriptions

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
