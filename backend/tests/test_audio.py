"""Audio sidecar tests.

The microphone is replaced with :class:`FakeAudioBackend`, which delivers 16 bit
blocks in real time so the timing behaviour under test matches a real device.
Every test builds a throwaway project, so none of this needs hardware.
"""

from __future__ import annotations

import json
import sys
import time
import wave
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

BACKEND_DIR = Path(__file__).resolve().parent.parent
TESTS_DIR = Path(__file__).resolve().parent
for entry in (BACKEND_DIR, TESTS_DIR):
    if str(entry) not in sys.path:
        sys.path.insert(0, str(entry))

from app.audio import AudioWorker  # noqa: E402
from app.capture import CaptureService  # noqa: E402
from app.camera import CameraWorker  # noqa: E402
from app.errors import ApiError  # noqa: E402
from app.main import create_app  # noqa: E402

from test_sessions import FakeBackend, Harness, write_config  # noqa: E402

AUDIO_CFG = {
    "enabled": True,
    "required": False,
    "device": "",
    "samplerate": 48000,
    "channels": 1,
    "sample_format": "pcm_s16le",
    "block_frames": 480,
}


class FakeAudioBackend:
    """Stand-in input device. Delivers real-time blocks so timing stays honest."""

    fail_open: str | None = None
    silent: bool = False
    amplitude: int = 8000
    # When set, successive blocks cycle through these amplitudes, which is what
    # lets a test see the meter move rather than sit on one value.
    amplitudes: list = []
    fail_after_reads: int | None = None
    opened: list = []

    def __init__(self, device: str = "") -> None:
        self.device = device
        self._open = False
        self._reads = 0

    # ------------------------------------------------------------ protocol

    def check(self, samplerate: int, channels: int, device: str) -> dict:
        if FakeAudioBackend.fail_open:
            raise ApiError("AUDIO_DEVICE_NOT_FOUND", FakeAudioBackend.fail_open)
        return {"name": "Fake Mic"}

    def open(self, samplerate: int, channels: int, device: str) -> None:
        if FakeAudioBackend.fail_open:
            raise ApiError("AUDIO_DEVICE_NOT_FOUND", FakeAudioBackend.fail_open)
        self._open = True
        self._samplerate = samplerate
        self._channels = channels
        FakeAudioBackend.opened.append(device)

    def read(self, frames: int) -> tuple[bytes, bool]:
        if not self._open:
            raise ApiError("AUDIO_RECORD_FAILED", "The fake stream is closed.")
        if FakeAudioBackend.fail_after_reads is not None:
            if self._reads >= FakeAudioBackend.fail_after_reads:
                raise ApiError("AUDIO_RECORD_FAILED", "The fake device disappeared.")
        self._reads += 1
        # Real time, so a take's audio length tracks its video length.
        time.sleep(frames / float(self._samplerate))
        amplitude = 0 if FakeAudioBackend.silent else FakeAudioBackend.amplitude
        if FakeAudioBackend.amplitudes:
            amplitude = FakeAudioBackend.amplitudes[
                (self._reads - 1) % len(FakeAudioBackend.amplitudes)
            ]
        sample = int(amplitude).to_bytes(2, "little", signed=True)
        return sample * (frames * self._channels), False

    def close(self) -> None:
        self._open = False

    def label(self) -> str:
        return "Fake Mic"


@pytest.fixture(autouse=True)
def _reset_fake():
    FakeAudioBackend.fail_open = None
    FakeAudioBackend.silent = False
    FakeAudioBackend.amplitude = 8000
    FakeAudioBackend.amplitudes = []
    FakeAudioBackend.fail_after_reads = None
    FakeAudioBackend.opened = []
    yield


def build(tmp_path: Path, audio: dict | None = None):
    """An app wired to the fake camera and the fake microphone."""
    library = tmp_path / "library"
    library.mkdir(parents=True, exist_ok=True)
    project_dir = library / "Project_A"
    config_path = write_config(
        tmp_path,
        stages=3,
        allow_roots=[str(library)],
        audio=audio if audio is not None else dict(AUDIO_CFG),
    )

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

    config = services.config
    services.camera = CameraWorker(config, backend_factory=FakeBackend)
    services.audio = AudioWorker(config, backend_factory=FakeAudioBackend)
    services.capture = CaptureService(
        config=config,
        sessions=services.sessions,
        project=services.project,
        guides=services.guides,
        device=services.device,
        camera=services.camera,
        audio=services.audio,
    )
    return app, Harness, project_dir


def harness_for(tmp_path: Path, audio: dict | None = None):
    app, _, project_dir = build(tmp_path, audio)
    client = TestClient(app)
    client.__enter__()
    return app, Harness(client, app.state.services, project_dir), client


@pytest.fixture()
def rig(tmp_path: Path):
    app, harness, client = harness_for(tmp_path)
    yield harness, app
    client.__exit__(None, None, None)


def record_stage(harness, index: int = 1, seconds: float = 0.35):
    started = harness.client.post(
        f"/api/sessions/session_a/stages/{index}/record/start", json={}
    )
    assert started.status_code == 200, started.text
    time.sleep(seconds)
    stopped = harness.client.post(f"/api/sessions/session_a/stages/{index}/record/stop")
    return stopped


# --------------------------------------------------------- the real backend


class _StubStream:
    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs
        self.device = 0
        self.started = False
        self.closed = False

    def start(self) -> None:
        self.started = True

    def read(self, frames: int):
        return b"\x10\x27" * frames, False

    def stop(self) -> None:
        self.started = False

    def close(self) -> None:
        self.closed = True


class _StubSoundDevice:
    """Minimal stand-in for the sounddevice module, injected into sys.modules.

    The fake *backend* used elsewhere replaces the whole class, which means the
    real SoundDeviceBackend never runs in the suite. This exercises it, so a bug
    inside it cannot hide behind the fake.
    """

    def __init__(self) -> None:
        self.default = type("D", (), {"device": [0, 1]})()
        self.opened: list = []
        self.checked: list = []

    def query_devices(self, device=None, kind=None):
        return {"name": "Stub Mic", "max_input_channels": 1}

    def check_input_settings(self, **kwargs):
        self.checked.append(kwargs)

    def RawInputStream(self, **kwargs):
        stream = _StubStream(**kwargs)
        self.opened.append(stream)
        return stream


@pytest.fixture()
def stub_sounddevice(monkeypatch):
    import app.audio as audio_module

    stub = _StubSoundDevice()
    monkeypatch.setitem(sys.modules, "sounddevice", stub)
    # Both caches must be cleared: the module level one inside the class instance
    # is per instance, so a fresh instance is enough.
    return stub


def test_the_lazy_sdk_accessor_stays_callable():
    """An instance attribute named like the accessor method shadows it.

    That mistake turns every call into "'NoneType' object is not callable", and it
    is invisible to any test that swaps the whole backend out.
    """
    from app.audio import SoundDeviceBackend

    backend = SoundDeviceBackend()
    assert callable(backend._sd)


def test_the_real_backend_checks_and_reads(stub_sounddevice):
    from app.audio import SoundDeviceBackend

    backend = SoundDeviceBackend()
    info = backend.check(48000, 1, "")
    assert info["name"] == "Stub Mic"
    assert stub_sounddevice.checked
    assert stub_sounddevice.checked[0]["samplerate"] == 48000
    assert stub_sounddevice.checked[0]["channels"] == 1

    backend.open(48000, 1, "")
    assert backend.label() == "Stub Mic"
    payload, overflowed = backend.read(480)
    assert len(payload) == 960
    assert overflowed is False

    backend.close()
    assert stub_sounddevice.opened[0].closed is True


def test_the_real_backend_reports_a_missing_device(stub_sounddevice):
    from app.audio import SoundDeviceBackend

    def boom(device=None, kind=None):
        raise ValueError("Error querying device -1")

    stub_sounddevice.query_devices = boom
    stub_sounddevice.default = type("D", (), {"device": [None, 1]})()
    with pytest.raises(ApiError) as excinfo:
        SoundDeviceBackend().check(48000, 1, "")
    assert excinfo.value.code == "AUDIO_DEVICE_NOT_FOUND"


def test_the_real_backend_translates_an_unsupported_rate(stub_sounddevice):
    from app.audio import SoundDeviceBackend

    def boom(**kwargs):
        raise ValueError("Invalid sample rate")

    stub_sounddevice.check_input_settings = boom
    with pytest.raises(ApiError) as excinfo:
        SoundDeviceBackend().check(192000, 1, "")
    assert excinfo.value.code == "AUDIO_UNAVAILABLE"
    assert excinfo.value.detail["reason"] == "unsupported_settings"


# ------------------------------------------------------------------ the take


def test_a_take_writes_a_wav_next_to_the_bag(rig):
    harness, _ = rig
    harness.set_project()
    harness.create()

    response = record_stage(harness)
    assert response.status_code == 200, response.text
    artifact = response.json()["artifact"]

    audio = artifact["audio"]
    assert audio is not None
    assert audio["file"] == "audio.wav"
    assert audio["samplerate"] == 48000
    assert audio["channels"] == 1
    assert audio["partial"] is False
    assert audio["size_bytes"] > 0
    # Started before the camera and stopped after it, so the wav covers the take.
    assert audio["duration_s"] >= artifact["duration_s"] * 0.8

    stage_dir = harness.project_dir / "session_a" / "stage_01"
    wav_path = stage_dir / "audio.wav"
    assert wav_path.is_file()
    with wave.open(str(wav_path), "rb") as handle:
        assert handle.getnchannels() == 1
        assert handle.getsampwidth() == 2
        assert handle.getframerate() == 48000
        assert handle.getnframes() > 0

    # The bag stays a camera artifact: audio must not leak into the stream list.
    assert "audio" not in artifact["streams"]


def test_the_offset_is_recorded_with_a_direction_and_an_error_bound(rig):
    harness, _ = rig
    harness.set_project()
    harness.create()
    artifact = record_stage(harness).json()["artifact"]

    sync = artifact["audio"]["sync"]
    assert sync["method"] == "monotonic_anchor"
    assert isinstance(sync["offset_ms"], int)
    # Audio starts first on purpose, so the offset is normally negative. A large
    # positive value would mean the anchor was taken from the wrong side.
    assert sync["offset_ms"] < 500
    assert sync["estimated_error_ms"] > 0


def test_the_wav_is_described_in_meta_json(rig):
    harness, _ = rig
    harness.set_project()
    harness.create()
    record_stage(harness)

    meta = json.loads(
        (harness.project_dir / "session_a" / "stage_01" / "meta.json").read_text(
            encoding="utf-8"
        )
    )
    assert meta["audio"]["file"] == "audio.wav"
    assert meta["audio"]["sync"]["offset_ms"] == meta["audio"]["sync"]["offset_ms"]
    assert "review" in meta


def test_the_peak_level_is_reported_for_silence_detection(rig):
    harness, _ = rig
    harness.set_project()
    harness.create()
    audio = record_stage(harness).json()["artifact"]["audio"]
    # 8000 of 32768 is about -12 dBFS, comfortably above the silence threshold.
    assert audio["peak_dbfs"] is not None
    assert -20.0 < audio["peak_dbfs"] < 0.0


def test_the_live_view_reports_the_level_while_a_take_runs(rig):
    """The capture screen's meter reads this field, so it has to be there.

    A linear ratio would park ordinary speech near zero; the screens need the
    logarithmic reading to draw a bar that looks alive.
    """
    harness, _ = rig
    harness.set_project()
    harness.create()
    harness.client.post("/api/sessions/session_a/stages/1/record/start", json={})
    try:
        time.sleep(0.2)
        live = harness.client.get("/api/audio").json()
        assert live["capturing"] is True
        assert live["level_dbfs"] is not None
        assert -20.0 < live["level_dbfs"] < 0.0
        assert 0.0 < live["level"] < 1.0
    finally:
        harness.client.post("/api/sessions/session_a/stages/1/record/stop")


def test_a_laptop_level_of_input_counts_as_heard(tmp_path):
    """A quiet microphone must still report that something is arriving.

    The threshold used to be a hand picked peak of 200, about -44 dBFS. A laptop
    microphone array idles near -56 dBFS and speech on it peaks around -45, so the
    take reported "no sound yet" from start to finish while the WAV was perfectly
    good. This pins the threshold below a realistic noise floor instead.
    """
    # Peak 60 is about -54.7 dBFS: a real laptop array's own noise floor.
    FakeAudioBackend.amplitude = 60
    app, harness, client = harness_for(tmp_path)
    try:
        harness.set_project()
        harness.create()
        harness.client.post("/api/sessions/session_a/stages/1/record/start", json={})
        try:
            time.sleep(0.3)
            live = harness.client.get("/api/audio").json()
            assert live["capturing"] is True
            assert live["heard"] is True, live
        finally:
            harness.client.post("/api/sessions/session_a/stages/1/record/stop")
    finally:
        client.__exit__(None, None, None)


def test_digital_silence_is_still_reported(tmp_path):
    """The tolerance above must not swallow the case the warning exists for."""
    FakeAudioBackend.amplitude = 0
    app, harness, client = harness_for(tmp_path)
    try:
        harness.set_project()
        harness.create()
        harness.client.post("/api/sessions/session_a/stages/1/record/start", json={})
        try:
            time.sleep(0.3)
            live = harness.client.get("/api/audio").json()
            assert live["heard"] is False
        finally:
            harness.client.post("/api/sessions/session_a/stages/1/record/stop")
    finally:
        client.__exit__(None, None, None)


def test_the_heard_threshold_sits_below_a_laptop_noise_floor():
    """Guard the constant itself, not just the behaviour that depends on it."""
    from app.audio import HEARD_PEAK, SILENT_DBFS, dbfs

    level = dbfs(HEARD_PEAK)
    assert level is not None
    # At or below the digital silence line, and far below a working device's floor.
    assert abs(level - SILENT_DBFS) < 1.0, level
    assert level < -55.0, level


def test_the_live_view_reports_no_level_for_silent_blocks(tmp_path):
    FakeAudioBackend.silent = True
    app, harness, client = harness_for(tmp_path)
    try:
        harness.set_project()
        harness.create()
        harness.client.post("/api/sessions/session_a/stages/1/record/start", json={})
        try:
            time.sleep(0.2)
            live = harness.client.get("/api/audio").json()
            # Null rather than a floor value, so the meter can tell "quiet" from
            # "no signal at all" instead of showing a level that never moves.
            assert live["level_dbfs"] is None
            assert live["heard"] is False
        finally:
            harness.client.post("/api/sessions/session_a/stages/1/record/stop")
    finally:
        client.__exit__(None, None, None)


def test_a_slow_bag_close_does_not_extend_the_audio(tmp_path):
    """The WAV must not keep recording while the video bag is being finalised.

    Closing a multi gigabyte bag takes seconds. The audio worker used to be
    stopped after that call returned, so every one of those seconds was appended
    to the WAV: an 11 s take produced a 16 s file. The take end is now signalled
    from inside the camera's own stop, and this pins it.
    """
    FakeBackend.close_delay = 0.8
    app, harness, client = harness_for(tmp_path)
    try:
        harness.set_project()
        harness.create()
        started = harness.client.post("/api/sessions/session_a/stages/1/record/start", json={})
        assert started.status_code == 200, started.text
        time.sleep(0.6)
        body = harness.client.post("/api/sessions/session_a/stages/1/record/stop").json()
        audio = body["artifact"]["audio"]
        video = body["artifact"]["duration_s"]

        # The 0.8 s the fake backend spends closing must not appear in the WAV.
        # What is left is the pre-roll plus the device's own buffer period, both
        # of which are sub second.
        assert audio["duration_s"] < video + 1.0, (audio["duration_s"], video)
        # The audio still has to cover the take rather than stop early.
        assert audio["duration_s"] > video - 0.5, (audio["duration_s"], video)
    finally:
        client.__exit__(None, None, None)


# ------------------------------------------------------------------ failures


def test_a_missing_device_does_not_block_a_take_when_audio_is_optional(tmp_path):
    FakeAudioBackend.fail_open = "no input device"
    app, harness, client = harness_for(tmp_path)
    try:
        harness.set_project()
        harness.create()
        response = record_stage(harness)
        assert response.status_code == 200, response.text
        artifact = response.json()["artifact"]
        assert artifact["audio"] is None
        # The video is still the dataset and must be complete.
        assert artifact["size_bytes"] > 0
        assert artifact["streams"]
        stage_dir = harness.project_dir / "session_a" / "stage_01"
        assert (stage_dir / "capture.db3").is_file()
        assert not (stage_dir / "audio.wav").exists()

        health = harness.client.get("/api/health").json()
        # Optional audio must not degrade the status.
        assert health["audio"]["enabled"] is True
        assert health["audio"]["available"] is False
    finally:
        client.__exit__(None, None, None)


def test_a_missing_device_blocks_the_take_when_audio_is_required(tmp_path):
    FakeAudioBackend.fail_open = "no input device"
    config = dict(AUDIO_CFG)
    config["required"] = True
    app, harness, client = harness_for(tmp_path, config)
    try:
        harness.set_project()
        harness.create()
        response = harness.client.post(
            "/api/sessions/session_a/stages/1/record/start", json={}
        )
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "AUDIO_DEVICE_NOT_FOUND"

        # Nothing may be left recording, and the stage stays idle.
        stage = harness.client.get("/api/sessions/session_a").json()["stages"][0]
        assert stage["state"] == "idle"

        health = harness.client.get("/api/health").json()
        assert health["status"] == "degraded"
    finally:
        client.__exit__(None, None, None)


def test_a_device_that_dies_mid_take_keeps_the_video(tmp_path):
    FakeAudioBackend.fail_after_reads = 3
    app, harness, client = harness_for(tmp_path)
    try:
        harness.set_project()
        harness.create()
        response = record_stage(harness, seconds=0.45)
        assert response.status_code == 200, response.text
        artifact = response.json()["artifact"]
        # The audio stops early and says so, but the take is still usable video.
        assert artifact["audio"]["partial"] is True
        assert artifact["size_bytes"] > 0
    finally:
        client.__exit__(None, None, None)


def test_discarding_a_take_removes_the_wav(rig):
    harness, _ = rig
    harness.set_project()
    harness.create()
    record_stage(harness)

    stage_dir = harness.project_dir / "session_a" / "stage_01"
    assert (stage_dir / "audio.wav").is_file()

    response = harness.client.post("/api/sessions/session_a/stages/1/record/discard")
    assert response.status_code == 200
    assert "audio.wav" in response.json()["deleted"]
    assert not (stage_dir / "audio.wav").exists()


def test_a_recovered_session_still_advertises_its_audio(tmp_path):
    app, harness, client = harness_for(tmp_path)
    try:
        harness.set_project()
        harness.create()
        record_stage(harness)
    finally:
        client.__exit__(None, None, None)

    # Rebuild the app: this is the restart path that reads meta.json.
    second, harness2, client2 = harness_for(tmp_path)
    try:
        stage = harness2.client.get("/api/sessions/session_a").json()["stages"][0]
        assert stage["artifact"]["audio"]["file"] == "audio.wav"
    finally:
        client2.__exit__(None, None, None)


def test_a_wav_deleted_by_hand_is_no_longer_advertised(tmp_path):
    app, harness, client = harness_for(tmp_path)
    try:
        harness.set_project()
        harness.create()
        record_stage(harness)
        (harness.project_dir / "session_a" / "stage_01" / "audio.wav").unlink()
    finally:
        client.__exit__(None, None, None)

    second, harness2, client2 = harness_for(tmp_path)
    try:
        stage = harness2.client.get("/api/sessions/session_a").json()["stages"][0]
        # meta.json still has the block, but the file is gone, so it must not be
        # offered as if it were there.
        assert stage["artifact"]["audio"] is None
    finally:
        client2.__exit__(None, None, None)


# -------------------------------------------------------- the input check


def test_the_input_check_reports_silence(tmp_path):
    FakeAudioBackend.silent = True
    app, harness, client = harness_for(tmp_path)
    try:
        started = harness.client.post("/api/audio/test")
        assert started.status_code == 200, started.text
        assert started.json()["running"] is True

        time.sleep(0.2)
        body = harness.client.post("/api/audio/test/stop").json()
        assert body["verdict"] == "silent"
        assert body["peak"] == 0
        assert body["peak_dbfs"] is None
        assert body["error"] is None
    finally:
        client.__exit__(None, None, None)


def test_the_input_check_reports_a_healthy_input(tmp_path):
    app, harness, client = harness_for(tmp_path)
    try:
        assert harness.client.post("/api/audio/test").status_code == 200
        time.sleep(0.2)
        body = harness.client.post("/api/audio/test/stop").json()
        assert body["verdict"] == "ok"
        assert body["device"] == "Fake Mic"
        # The check now runs for as long as the screen watches it, so the
        # reported duration is the wall clock time, not a fixed two seconds.
        assert 0.1 <= body["duration_s"] <= 3.0
    finally:
        client.__exit__(None, None, None)


def test_the_meter_follows_the_input_level(tmp_path):
    FakeAudioBackend.amplitudes = [100, 3000, 20000, 400]
    app, harness, client = harness_for(tmp_path)
    try:
        harness.client.post("/api/audio/test")
        seen = []
        for _ in range(12):
            body = harness.client.get("/api/audio/test").json()
            assert body["running"] is True
            seen.append(body["level_dbfs"])
            time.sleep(0.02)
        levels = {value for value in seen if value is not None}
        # A meter that never moves is the bug this guards: the old check recorded
        # a burst and reported one number, so nothing on screen could change.
        assert len(levels) >= 3, seen
        assert max(levels) > -10
        assert min(levels) < -35
        assert harness.client.post("/api/audio/test/stop").status_code == 200
    finally:
        client.__exit__(None, None, None)


def test_the_meter_reports_the_loudest_block_as_the_peak(tmp_path):
    FakeAudioBackend.amplitudes = [300, 24000, 500]
    app, harness, client = harness_for(tmp_path)
    try:
        harness.client.post("/api/audio/test")
        time.sleep(0.25)
        live = harness.client.get("/api/audio/test").json()
        assert live["heard"] is True
        assert live["peak"] > 20000
        stopped = harness.client.post("/api/audio/test/stop").json()
        assert stopped["peak"] >= live["peak"]
        assert stopped["verdict"] == "ok"
    finally:
        client.__exit__(None, None, None)


def test_the_check_closes_the_device_when_stopped(tmp_path):
    app, harness, client = harness_for(tmp_path)
    try:
        harness.client.post("/api/audio/test")
        time.sleep(0.1)
        worker = app.state.services.audio
        assert worker.test_status()["running"] is True

        harness.client.post("/api/audio/test/stop")
        assert worker.test_status()["running"] is False
        # The device is released, so a take can open it right away. A check left
        # holding the input would surface here as AUDIO_DEVICE_BUSY.
        harness.set_project()
        harness.create()
        started = harness.client.post("/api/sessions/session_a/stages/1/record/start", json={})
        assert started.status_code == 200, started.text
        harness.client.post("/api/sessions/session_a/stages/1/record/stop")
    finally:
        client.__exit__(None, None, None)


def test_stopping_twice_is_harmless(tmp_path):
    app, harness, client = harness_for(tmp_path)
    try:
        harness.client.post("/api/audio/test")
        time.sleep(0.1)
        assert harness.client.post("/api/audio/test/stop").status_code == 200
        again = harness.client.post("/api/audio/test/stop")
        assert again.status_code == 200
        assert again.json()["duration_s"] == 0.0
    finally:
        client.__exit__(None, None, None)


def test_the_check_is_not_restarted_by_a_second_press(tmp_path):
    app, harness, client = harness_for(tmp_path)
    try:
        first = harness.client.post("/api/audio/test").json()
        second = harness.client.post("/api/audio/test").json()
        assert second["running"] is True
        assert len(FakeAudioBackend.opened) == 1, FakeAudioBackend.opened
        # Both calls describe the same check, not two devices.
        assert first["device"] == second["device"]
        harness.client.post("/api/audio/test/stop")
    finally:
        client.__exit__(None, None, None)


def test_the_check_is_refused_while_a_take_is_running(rig):
    harness, _ = rig
    harness.set_project()
    harness.create()
    harness.client.post("/api/sessions/session_a/stages/1/record/start", json={})
    try:
        response = harness.client.post("/api/audio/test")
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "AUDIO_DEVICE_BUSY"
    finally:
        harness.client.post("/api/sessions/session_a/stages/1/record/stop")


def test_a_take_pre_empts_a_check_left_running(rig):
    harness, app = rig
    harness.set_project()
    harness.create()
    harness.client.post("/api/audio/test")
    time.sleep(0.1)
    try:
        started = harness.client.post("/api/sessions/session_a/stages/1/record/start", json={})
        # The check holds no data, so the take must win rather than fail busy.
        assert started.status_code == 200, started.text
    finally:
        harness.client.post("/api/sessions/session_a/stages/1/record/stop")
    assert app.state.services.audio.test_status()["running"] is False


def test_the_check_releases_the_device_when_the_screen_stops_polling(tmp_path, monkeypatch):
    """A closed tab must not hold the microphone until shutdown."""
    from app import audio as audio_module

    monkeypatch.setattr(audio_module, "TEST_LINGER_S", 0.2)
    app, harness, client = harness_for(tmp_path)
    try:
        worker = app.state.services.audio
        harness.client.post("/api/audio/test")
        time.sleep(0.1)
        assert worker.test_status()["running"] is True
        # Stop polling, exactly like a tab that was closed.
        time.sleep(0.5)
        assert worker.test_status()["running"] is False
    finally:
        client.__exit__(None, None, None)


def test_the_check_reports_a_device_that_fails_midway(tmp_path):
    FakeAudioBackend.fail_after_reads = 3
    app, harness, client = harness_for(tmp_path)
    try:
        assert harness.client.post("/api/audio/test").status_code == 200
        time.sleep(0.3)
        live = harness.client.get("/api/audio/test").json()
        assert live["running"] is False
        assert live["error"]
        stopped = harness.client.post("/api/audio/test/stop").json()
        assert stopped["error"], stopped
    finally:
        client.__exit__(None, None, None)


def test_the_check_is_refused_when_audio_is_disabled(tmp_path):
    app, harness, client = harness_for(tmp_path, {"enabled": False})
    try:
        refused = harness.client.post("/api/audio/test")
        assert refused.status_code == 409
        assert refused.json()["error"]["code"] == "AUDIO_DISABLED"
        # Polling a check that cannot run reports idle rather than failing, so a
        # screen that lost the race does not fill the log with errors.
        idle = harness.client.get("/api/audio/test")
        assert idle.status_code == 200
        assert idle.json()["running"] is False
        stopped = harness.client.post("/api/audio/test/stop")
        assert stopped.status_code == 200
        assert stopped.json()["verdict"] == "silent"
    finally:
        client.__exit__(None, None, None)


# ------------------------------------------------------------------ disabled


def test_disabled_audio_is_reported_and_never_written(tmp_path):
    app, harness, client = harness_for(tmp_path, {"enabled": False})
    try:
        harness.set_project()
        harness.create()
        artifact = record_stage(harness).json()["artifact"]
        assert artifact["audio"] is None
        assert not (harness.project_dir / "session_a" / "stage_01" / "audio.wav").exists()

        health = harness.client.get("/api/health").json()
        assert health["audio"]["enabled"] is False
        assert health["audio"]["available"] is False
    finally:
        client.__exit__(None, None, None)


def test_the_capture_config_exposes_the_audio_output_name(rig):
    harness, _ = rig
    body = harness.client.get("/api/config").json()
    assert body["audio"]["enabled"] is True
    assert body["audio"]["output_name"] == "audio.wav"
    assert body["audio"]["samplerate"] == 48000


# ------------------------------------------------------------------- config


@pytest.mark.parametrize(
    "override",
    [
        {"sample_format": "pcm_f32le"},
        {"channels": 4},
        {"samplerate": 4000},
        {"block_frames": 0},
    ],
)
def test_an_unusable_audio_setting_stops_the_service(tmp_path, override):
    """A silent resample or a weird channel count is a config error, not a warning.

    Changing the format would need a conversion step that also changes the file
    duration, which would invalidate the offset recorded in meta.json.
    """
    from app.config import ConfigError, load_config

    library = tmp_path / "library"
    library.mkdir(parents=True, exist_ok=True)
    config = dict(AUDIO_CFG)
    config.update(override)
    path = write_config(tmp_path, allow_roots=[str(library)], audio=config)
    with pytest.raises(ConfigError):
        load_config(path)


def test_audio_counts_towards_the_disk_rate_estimate(tmp_path):
    """The gauge on the capture screen has to include the sidecar.

    Small next to the video, but the number exists to be trusted, so a missing
    contribution is still a wrong number.
    """
    from app.config import load_config

    library = tmp_path / "library"
    library.mkdir(parents=True, exist_ok=True)
    off = write_config(tmp_path / "off", allow_roots=[str(library)])
    on = write_config(
        tmp_path / "on", allow_roots=[str(library)], audio=dict(AUDIO_CFG)
    )
    without = load_config(off).recording_bytes_per_s
    with_audio = load_config(on).recording_bytes_per_s
    # 48000 Hz, mono, 16 bit is 96000 bytes per second.
    assert with_audio - without == 96000
