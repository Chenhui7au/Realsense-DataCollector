"""Tests for the camera probe and its child process containment.

Nothing here needs a camera. The enumerator is injectable, so each case runs a
tiny stub script that prints whatever the case is about. That matters because the
real failure modes cannot be reproduced on demand, they depend on the host and on
which driver has claimed the camera.

The case worth understanding is the crash. On macOS the SDK raises and then
faults during teardown, so a stub that prints valid JSON and then kills itself
with SIGSEGV is not a contrived test, it mirrors the actual behaviour.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
import yaml

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.config import load_config  # noqa: E402
from app.device import ENUMERATOR, REASONS, DeviceProbe  # noqa: E402


def build_config(tmp_path: Path, probe: bool = True, serial: str = ""):
    """Minimal config. Only the camera section matters for these tests."""
    conf = tmp_path / "conf"
    conf.mkdir(parents=True, exist_ok=True)
    payload = {
        "app": {"title": "t"},
        "paths": {
            "service_dir": "../var",
            "guide_root": "../var/guides",
            "log_dir": "../var/logs",
            "settings_file": "../var/settings.json",
        },
        "project": {"default_root": ""},
        "fs": {"allow_roots": ["~"]},
        "guides": {},
        "camera": {"probe": probe, "serial": serial},
        "stages": [
            {"index": 1, "name": "One", "instructions": "", "max_duration_s": 30},
        ],
    }
    path = conf / "config.yaml"
    path.write_text(yaml.safe_dump(payload), encoding="utf-8")
    return load_config(path)


def stub(tmp_path: Path, body: str) -> Path:
    """Write a throwaway enumerator and return its path."""
    path = tmp_path / "stub_enumerator.py"
    path.write_text(body, encoding="utf-8")
    return path


OK_DEVICE = (
    '{"ok": true, "devices": [{"name": "Intel RealSense D435i", "serial": "0123456789",'
    ' "firmware": "5.13.0.50", "usb_type": "3.2"}]}'
)


# ------------------------------------------------------------ the disabled switch

def test_probe_disabled_reports_no_device_without_running_anything(tmp_path):
    config = build_config(tmp_path, probe=False)
    # Point at a path that does not exist. It must never be consulted, otherwise
    # the short circuit is not a short circuit.
    probe = DeviceProbe(config, enumerator=tmp_path / "does-not-exist.py")

    info = probe.info()
    assert info["connected"] is False
    assert info["reason"] == REASONS["disabled"]
    assert info["name"] is None and info["serial"] is None


def test_real_config_ships_with_probing_on():
    """The shipped YAML must probe. The switch is for tests and frontend work."""
    config = load_config(BACKEND_DIR / "config" / "config.yaml")
    assert config.camera_probe is True


def test_enumerator_script_exists_and_compiles():
    assert ENUMERATOR.is_file(), f"missing enumerator at {ENUMERATOR}"
    compile(ENUMERATOR.read_text(encoding="utf-8"), str(ENUMERATOR), "exec")


def _load_enumerator():
    """Import the enumerator module without running its main().

    Importing is safe, the entry point is guarded. This is how the pure helpers
    get tested, because running the script for real is not deterministic on a
    machine with a camera attached. The SDK's crash point moves between runs, so
    sometimes it prints its JSON first and sometimes it dies silently. Testing the
    functions directly is the only way to assert the contract reliably.
    """
    import importlib.util

    spec = importlib.util.spec_from_file_location("enumerate_devices", ENUMERATOR)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_enumerator_reports_elevation_and_platform():
    """The parent cannot know either of these, so the child has to say them."""
    module = _load_enumerator()
    assert isinstance(module._elevated(), bool)
    if os.name == "nt":
        # Windows has no euid, so the child always reports not elevated.
        assert module._elevated() is False
    else:
        # Elevated here means euid 0, which is what the service should report on.
        assert module._elevated() == (os.geteuid() == 0)


@pytest.mark.parametrize(
    "message, expected",
    [
        ("failed to set power state", "permission"),
        ("failed to claim usb interface: 0, error: RS2_USB_STATUS_ACCESS", "permission"),
        ("Operation not permitted", "permission"),
        ("No device connected", "no_device"),
        ("something entirely different", "sdk_error"),
    ],
)
def test_enumerator_classifies_sdk_messages(message, expected):
    """The mapping from SDK wording to a kind, which is what the parent reads."""
    assert _load_enumerator()._classify(message) == expected


def test_every_permission_marker_is_matched_case_insensitively():
    module = _load_enumerator()
    for marker in module._PERMISSION_MARKERS:
        assert module._classify(marker) == "permission"
        assert module._classify(marker.upper()) == "permission"


# ----------------------------------------------------------------- happy path

def test_reports_a_connected_device(tmp_path):
    config = build_config(tmp_path)
    probe = DeviceProbe(config, enumerator=stub(tmp_path, f"print({OK_DEVICE!r})"))

    info = probe.info()
    assert info["connected"] is True
    assert info["name"] == "Intel RealSense D435i"
    assert info["serial"] == "0123456789"
    assert info["firmware"] == "5.13.0.50"
    assert info["usb_type"] == "3.2"
    assert info["reason"] is None


def test_usb_2_link_is_still_connected(tmp_path):
    """A slow link is a warning, not a failure. The operator still gets a camera."""
    payload = (
        '{"ok": true, "devices": [{"name": "D435i", "serial": "s",'
        ' "firmware": "f", "usb_type": "2.1"}]}'
    )
    config = build_config(tmp_path)
    probe = DeviceProbe(config, enumerator=stub(tmp_path, f"print({payload!r})"))

    info = probe.info()
    assert info["connected"] is True
    assert info["usb_type"] == "2.1"


def test_configured_serial_selects_the_matching_device(tmp_path):
    payload = (
        '{"ok": true, "devices": ['
        '{"name": "Wrong", "serial": "AAA", "firmware": "f", "usb_type": "3.2"},'
        '{"name": "Wanted", "serial": "BBB", "firmware": "f", "usb_type": "3.2"}]}'
    )
    config = build_config(tmp_path, serial="BBB")
    probe = DeviceProbe(config, enumerator=stub(tmp_path, f"print({payload!r})"))

    info = probe.info()
    assert info["connected"] is True
    assert info["serial"] == "BBB"
    assert info["name"] == "Wanted"


def test_configured_serial_that_matches_nothing_is_not_connected(tmp_path):
    payload = '{"ok": true, "devices": [{"name": "Other", "serial": "AAA"}]}'
    config = build_config(tmp_path, serial="ZZZ")
    probe = DeviceProbe(config, enumerator=stub(tmp_path, f"print({payload!r})"))

    info = probe.info()
    assert info["connected"] is False
    assert info["reason"] == REASONS["sdk_error"]


# --------------------------------------------------------------- failure paths

def test_permission_failure_maps_to_the_uvc_reason(tmp_path):
    """Not elevated. The operator can act on this, so the reason says how."""
    payload = (
        '{"ok": false, "kind": "permission", "error": "failed to set power state",'
        ' "elevated": false}'
    )
    config = build_config(tmp_path)
    probe = DeviceProbe(config, enumerator=stub(tmp_path, f"print({payload!r})"))

    info = probe.info()
    assert info["connected"] is False
    assert info["reason"] == REASONS["permission"]
    assert "sudo" in info["reason"]


def test_permission_failure_while_already_rooted_gives_different_advice(tmp_path):
    """Measured behaviour. sudo does not help on this macOS build, so do not say it.

    Reporting the plain permission reason here would send the operator to re-run
    with sudo, which was already tried and failed.
    """
    payload = (
        '{"ok": false, "kind": "permission", "error": "failed to set power state",'
        ' "elevated": true}'
    )
    config = build_config(tmp_path)
    probe = DeviceProbe(config, enumerator=stub(tmp_path, f"print({payload!r})"))

    info = probe.info()
    assert info["connected"] is False
    assert info["reason"] == REASONS["permission_rooted"]
    assert "sudo" not in info["reason"]
    assert "macOS" in info["reason"]


def test_elevated_does_not_change_other_failure_kinds(tmp_path):
    payload = '{"ok": false, "kind": "no_device", "elevated": true}'
    config = build_config(tmp_path)
    probe = DeviceProbe(config, enumerator=stub(tmp_path, f"print({payload!r})"))
    assert probe.info()["reason"] == REASONS["no_device"]


def test_missing_sdk_maps_to_the_install_reason(tmp_path):
    payload = '{"ok": false, "kind": "sdk_missing", "error": "No module named pyrealsense2"}'
    config = build_config(tmp_path)
    probe = DeviceProbe(config, enumerator=stub(tmp_path, f"print({payload!r})"))

    info = probe.info()
    assert info["reason"] == REASONS["sdk_missing"]
    assert "pyrealsense2" in info["reason"]


def test_no_device_attached(tmp_path):
    payload = '{"ok": false, "kind": "no_device", "error": "nothing here"}'
    config = build_config(tmp_path)
    probe = DeviceProbe(config, enumerator=stub(tmp_path, f"print({payload!r})"))

    assert probe.info()["reason"] == REASONS["no_device"]


def test_json_is_read_even_when_the_child_then_crashes(tmp_path):
    """The real macOS behaviour. Valid output, then SIGSEGV during teardown.

    Contains the crash, and takes the verdict from the JSON rather than from the
    exit code, since by then the child has already said what happened.
    """
    body = (
        "import os, sys\n"
        f"print({OK_DEVICE!r})\n"
        "sys.stdout.flush()\n"
        "os._exit(139)\n"
    )
    config = build_config(tmp_path)
    probe = DeviceProbe(config, enumerator=stub(tmp_path, body))

    info = probe.info()
    assert info["connected"] is True, "the JSON was printed, the exit code is noise"


def test_a_crash_without_output_is_reported_as_a_crash(tmp_path):
    config = build_config(tmp_path)
    probe = DeviceProbe(config, enumerator=stub(tmp_path, "import os\nos._exit(139)\n"))

    info = probe.info()
    assert info["connected"] is False
    assert info["reason"] == REASONS["crashed"]
    assert "exit=139" in info["reason"] or info["reason"] == REASONS["crashed"]


def test_a_wedged_child_is_timed_out(tmp_path):
    config = build_config(tmp_path)
    probe = DeviceProbe(
        config, enumerator=stub(tmp_path, "import time\ntime.sleep(30)\n"), timeout_s=1.0
    )

    info = probe.info()
    assert info["connected"] is False
    assert info["reason"] == REASONS["timeout"]


def test_unparseable_output_is_reported_as_an_sdk_error(tmp_path):
    config = build_config(tmp_path)
    probe = DeviceProbe(config, enumerator=stub(tmp_path, "print('not json at all')\n"))

    info = probe.info()
    assert info["connected"] is False
    assert info["reason"] == REASONS["sdk_error"]


def test_a_missing_enumerator_is_reported_not_raised(tmp_path):
    config = build_config(tmp_path)
    probe = DeviceProbe(config, enumerator=tmp_path / "absent.py")

    info = probe.info()
    assert info["connected"] is False
    assert info["reason"] == REASONS["sdk_error"]


def test_every_failure_kind_has_a_reason_an_operator_can_read():
    for kind in ("sdk_missing", "permission", "permission_rooted", "no_device",
                 "bus_error", "sdk_error", "timeout", "crashed", "disabled"):
        reason = REASONS[kind]
        assert reason and reason[0].isupper() or reason[0].isalpha()
        assert len(reason) > 20, f"{kind} reason is too terse to be useful"


# -------------------------------------------------------------------- parsing

@pytest.mark.parametrize(
    "stdout, expected_ok",
    [
        ('{"ok": true, "devices": []}', True),
        ('noise\n{"ok": true, "devices": []}\nmore noise', True),
        ('{"partial": 1}\n{"ok": false, "kind": "no_device"}', False),
        ("", None),
        ("only noise", None),
        ("{malformed", None),
    ],
)
def test_parse_takes_the_last_json_object(stdout, expected_ok):
    parsed = DeviceProbe._parse(stdout)
    if expected_ok is None:
        assert parsed is None
    else:
        assert parsed is not None
        assert parsed["ok"] is expected_ok


# -------------------------------------------------------------------- caching

def test_repeated_reads_within_the_ttl_do_not_respawn(tmp_path):
    """Otherwise every health poll would pay for a cold SDK start."""
    marker = tmp_path / "runs.txt"
    body = (
        "from pathlib import Path\n"
        f"p = Path({str(marker)!r})\n"
        "p.write_text((p.read_text() if p.exists() else '') + 'x')\n"
        f"print({OK_DEVICE!r})\n"
    )
    config = build_config(tmp_path)
    probe = DeviceProbe(config, enumerator=stub(tmp_path, body))

    for _ in range(5):
        assert probe.info()["connected"] is True

    assert marker.read_text() == "x", "the child ran more than once inside the TTL"


def test_force_bypasses_the_cache(tmp_path):
    marker = tmp_path / "runs.txt"
    body = (
        "from pathlib import Path\n"
        f"p = Path({str(marker)!r})\n"
        "p.write_text((p.read_text() if p.exists() else '') + 'x')\n"
        f"print({OK_DEVICE!r})\n"
    )
    config = build_config(tmp_path)
    probe = DeviceProbe(config, enumerator=stub(tmp_path, body))

    probe.info()
    probe.info(force=True)
    assert marker.read_text() == "xx"
