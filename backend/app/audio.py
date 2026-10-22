"""Audio capture alongside the camera pipeline.

The D435i has no microphone and pyrealsense2 exposes no audio stream or format,
so audio can never end up inside ``capture.db3``. It is written as a sidecar WAV
per stage and related to the video by a host clock anchor recorded in
``meta.json``.

Three rules shape this module.

* **The audio stream opens before the camera pipeline and closes after it.** The
  take is then guaranteed to be covered end to end, and the leading and trailing
  excess is harmless because ``offset_ms`` says where the video starts. Audio that
  began late would lose its tail, which cannot be recovered.
* **Nothing is resampled behind the operator's back.** If the device cannot do the
  configured rate the open fails and says so. A silent resample changes both the
  duration and the anchor, which is exactly the class of error nobody notices
  until the dataset is being labelled.
* **The capture thread never dies.** A device unplugged mid take is reported
  through ``last_error`` and the take is marked partial. The video keeps going,
  because it is the primary data.

Writing uses the standard library ``wave`` module and raw int16 blocks from
``sounddevice``, so numpy is not needed.
"""

from __future__ import annotations

import array
import logging
import math
import threading
import time
import wave
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Protocol, Tuple

from .config import Config
from .errors import ApiError

log = logging.getLogger(__name__)

# 16 bit signed little endian is the only format this module writes. Anything
# else would need a conversion step, and a conversion step is another chance to
# change the duration without saying so.
SAMPLE_WIDTH_BYTES = 2
SAMPLE_FORMAT = "pcm_s16le"

# Extra seconds the audio thread allows itself past the stage limit. It is a
# runaway guard only: the authoritative stop is CaptureService.stop_record, which
# the stage timer triggers. Without the margin a take that hits its limit would
# lose the audio tail, because the audio started earlier than the video.
SAFETY_TAIL_S = 60.0

# How long start() waits for the device to open. Opening a stream is tens of
# milliseconds; anything longer is a device problem worth reporting.
OPEN_TIMEOUT_S = 5.0

# Peak thresholds for the input check, in dBFS. The meter uses the same floor.
SILENT_DBFS = -60.0
QUIET_DBFS = -30.0
CLIPPING_DBFS = -1.0

# A block peak above this counts as "something is arriving" for the capture
# screen, which is the question "is the device dead", not "is the level good".
#
# It used to be a hand picked 200, about -44 dBFS, on the assumption that a normal
# microphone idles below that. This one does not: the array in a laptop settles
# around -56 dBFS, so peak 200 was never reached and the screen reported "no sound
# yet" for whole takes while the file was perfectly fine. Anchoring the threshold
# to the digital silence line instead makes the two agree by construction: a
# device delivering zeros is reported, and a device hearing its own noise floor is
# not. Judging whether the level is *good* is what QUIET_DBFS is for.
HEARD_PEAK = int(32768 * 10 ** (SILENT_DBFS / 20.0))

# The live input check holds the device open while somebody watches the meter, so
# it needs two ways out that do not depend on the screen asking politely. This one
# fires when the screen stops polling, which is what happens when the tab closes
# or the operator walks away mid check; without it the microphone would stay open
# until shutdown.
TEST_LINGER_S = 15.0

# Hard stop for a check that is still being polled. Long enough to be useful,
# short enough that a forgotten browser tab cannot hold the device forever.
TEST_MAX_S = 300.0


class AudioBackend(Protocol):
    """The narrow slice of an audio input device this module needs."""

    def check(self, samplerate: int, channels: int, device: str) -> Dict[str, Any]:
        ...

    def open(self, samplerate: int, channels: int, device: str) -> None:
        ...

    def read(self, frames: int) -> Tuple[bytes, bool]:
        ...

    def close(self) -> None:
        ...

    def label(self) -> str:
        ...


class SoundDeviceBackend:
    """Wrapper over ``sounddevice``, imported lazily.

    Keeping the import inside :meth:`_sd` means the service starts, every other
    endpoint works and the whole test suite runs on a machine with no PortAudio.
    A missing package then surfaces as a specific reported reason rather than an
    import error at module load.
    """

    def __init__(self, device: str = "") -> None:
        self.device = device
        # Deliberately not called `_sd`: that is the accessor method name, and an
        # instance attribute would shadow it, which makes every call fail with
        # "'NoneType' object is not callable".
        self._module: Any = None
        self._stream: Any = None
        self._label = ""

    def _sd(self) -> Any:
        if self._module is None:
            try:
                import sounddevice as sd  # deliberate lazy import
            except ImportError as exc:  # pragma: no cover - host dependent
                raise ApiError(
                    "AUDIO_DEVICE_NOT_FOUND",
                    "The sounddevice package is not installed for this interpreter.",
                    detail={"reason": "sdk_missing"},
                ) from exc
            self._module = sd
        return self._module

    def check(self, samplerate: int, channels: int, device: str) -> Dict[str, Any]:
        """Validate the requested settings without opening a stream."""
        sd = self._sd()
        target = device or self.device or None
        try:
            if target:
                info = sd.query_devices(target, "input")
            else:
                index = sd.default.device[0]
                if index is None or index < 0:
                    raise ApiError(
                        "AUDIO_DEVICE_NOT_FOUND",
                        "No default audio input device is configured on the host.",
                        detail={"reason": "no_default_device"},
                    )
                info = sd.query_devices(index, "input")
            sd.check_input_settings(
                device=target, samplerate=samplerate, channels=channels, dtype="int16"
            )
        except ApiError:
            raise
        except Exception as exc:
            raise self.translate(exc) from exc
        return {"name": str(info.get("name") or target or "audio input")}

    def open(self, samplerate: int, channels: int, device: str) -> None:
        sd = self._sd()
        target = device or self.device or None
        try:
            stream = sd.RawInputStream(
                samplerate=samplerate,
                channels=channels,
                dtype="int16",
                device=target,
                blocksize=0,
            )
            stream.start()
            info = sd.query_devices(stream.device if hasattr(stream, "device") else target, "input")
        except Exception as exc:
            raise self.translate(exc) from exc
        self._stream = stream
        self._label = str(info.get("name") or target or "audio input")

    def read(self, frames: int) -> Tuple[bytes, bool]:
        stream = self._stream
        if stream is None:
            raise ApiError("AUDIO_RECORD_FAILED", "The audio stream is not open.")
        try:
            payload, overflowed = stream.read(frames)
        except Exception as exc:
            raise self.translate(exc) from exc
        return bytes(payload), bool(overflowed)

    def close(self) -> None:
        stream, self._stream = self._stream, None
        if stream is None:
            return
        try:
            stream.stop()
            stream.close()
        except Exception as exc:  # pragma: no cover - teardown is best effort
            log.warning("audio stream close reported %s", exc)

    def label(self) -> str:
        return self._label

    @staticmethod
    def translate(exc: Exception) -> ApiError:
        """Map a PortAudio failure onto the documented error codes."""
        message = str(exc)
        lowered = message.lower()
        if "invalid sample rate" in lowered or "invalid number of channels" in lowered:
            return ApiError(
                "AUDIO_UNAVAILABLE",
                "The audio device does not support the configured sample rate or channel "
                "count. Check the audio section of the configuration.",
                detail={"reason": "unsupported_settings", "portaudio": message},
            )
        if "busy" in lowered or "unavailable" in lowered or "host error" in lowered:
            return ApiError(
                "AUDIO_DEVICE_BUSY",
                "The audio input is held by another program, or the device refused to open.",
                detail={"reason": "busy", "portaudio": message},
            )
        if "error querying device" in lowered or "no default" in lowered:
            return ApiError(
                "AUDIO_DEVICE_NOT_FOUND",
                "No audio input device was found on the host.",
                detail={"reason": "no_device", "portaudio": message},
            )
        return ApiError(
            "AUDIO_RECORD_FAILED",
            "The audio device reported an error.",
            detail={"portaudio": message},
        )


class AudioWorker:
    """Owns one audio input device and writes one WAV per take."""

    def __init__(
        self,
        config: Config,
        backend_factory: Optional[Callable[[str], AudioBackend]] = None,
    ) -> None:
        self.config = config
        self._backend_factory = backend_factory or (lambda device: SoundDeviceBackend())

        self._lock = threading.RLock()
        self._thread: Optional[threading.Thread] = None
        self._stop_event: Optional[threading.Event] = None
        self._ready: Optional[threading.Event] = None
        self._active = False

        self._path: Optional[Path] = None
        self._device = ""
        self._current_backend: Optional[AudioBackend] = None
        self._started = False
        self._started_mono: Optional[float] = None
        self._stopped_mono: Optional[float] = None
        self._samples = 0
        self._overruns = 0
        self._peak = 0
        self._level = 0
        self._heard = False
        self._error: Optional[ApiError] = None

        # Cached probe result, so /api/health does not enumerate devices on every
        # poll. start() and the input check always ask the device directly.
        self._probe_cache: Optional[Dict[str, Any]] = None

        # Live input check, kept apart from the take fields so a check in progress
        # can never be mistaken for a recording.
        self._test_thread: Optional[threading.Thread] = None
        self._test_stop_event: Optional[threading.Event] = None
        self._test_ready: Optional[threading.Event] = None
        self._test_active = False
        self._test_device = ""
        self._test_started_mono: Optional[float] = None
        self._test_last_poll = 0.0
        self._test_samples = 0
        self._test_overruns = 0
        self._test_level = 0
        self._test_peak = 0
        self._test_heard = False
        self._test_error: Optional[ApiError] = None

    # ------------------------------------------------------------- properties

    @property
    def enabled(self) -> bool:
        return bool(self.config.audio_enabled)

    @property
    def required(self) -> bool:
        return bool(self.config.audio_required)

    @property
    def capturing(self) -> bool:
        with self._lock:
            alive = self._thread is not None and self._thread.is_alive()
            return self._active and alive

    @property
    def last_error(self) -> Optional[ApiError]:
        with self._lock:
            return self._error

    # ------------------------------------------------------------------ probe

    def probe(self, force: bool = False) -> Dict[str, Any]:
        """Whether the configured input can be opened. Cached for the health poll."""
        if not self.enabled:
            return {
                "enabled": False,
                "required": False,
                "available": False,
                "device": None,
                "reason": "Audio capture is switched off in this configuration.",
            }

        with self._lock:
            if self._probe_cache is not None and not force:
                return dict(self._probe_cache)

        result: Dict[str, Any]
        try:
            info = self._backend_factory(self._device).check(
                int(self.config.audio_samplerate),
                int(self.config.audio_channels),
                str(self.config.audio_device or ""),
            )
            result = {
                "enabled": True,
                "required": self.required,
                "available": True,
                "device": info.get("name"),
                "reason": None,
            }
        except ApiError as exc:
            result = {
                "enabled": True,
                "required": self.required,
                "available": False,
                "device": None,
                "reason": exc.message,
            }
        except Exception as exc:  # noqa: BLE001 - a probe must never raise
            log.exception("audio probe failed")
            # Not an ApiError, so this is a fault in the audio layer rather than a
            # device state. Say so, and point at the log, instead of showing a bare
            # exception that reads like a configuration problem.
            result = {
                "enabled": True,
                "required": self.required,
                "available": False,
                "device": None,
                "reason": (
                    "The audio layer reported an internal error "
                    f"({type(exc).__name__}: {exc}). See the service log."
                ),
            }

        result["samplerate"] = int(self.config.audio_samplerate)
        result["channels"] = int(self.config.audio_channels)
        with self._lock:
            self._probe_cache = dict(result)
        return result

    # -------------------------------------------------------------- recording

    def start(self, path: Path, limit_s: float) -> Dict[str, Any]:
        """Open the device and begin writing. Raises when the device refuses.

        The caller starts this before the camera pipeline so the take is covered
        end to end. Returning only after the device is genuinely delivering means
        ``required = true`` can refuse a round instead of discovering the problem
        after a take has been recorded.
        """
        if not self.enabled:
            raise ApiError("AUDIO_DISABLED", detail={"reason": "disabled"})

        with self._lock:
            if self._active:
                raise ApiError("AUDIO_DEVICE_BUSY", "Audio is already being captured.")

        # A live input check holds the device. A take always wins: the check keeps
        # no data, so closing it costs nothing, while refusing the take over a
        # test button somebody left running would lose the recording.
        self._end_test()

        with self._lock:
            self._device = str(self.config.audio_device or "")
            self._started_mono = None
            self._stopped_mono = None
            self._samples = 0
            self._overruns = 0
            self._peak = 0
            self._level = 0
            self._heard = False
            self._error = None
            self._started = False
            self._active = True
            self._stop_event = threading.Event()
            self._ready = threading.Event()
            stop_event = self._stop_event
            ready = self._ready

        thread = threading.Thread(
            target=self._run,
            args=(Path(path), float(limit_s), stop_event, ready),
            name="audio-worker",
            daemon=True,
        )
        with self._lock:
            self._thread = thread
        thread.start()

        if not ready.wait(OPEN_TIMEOUT_S):
            with self._lock:
                self._active = False
            self._request_stop()
            raise ApiError(
                "AUDIO_UNAVAILABLE",
                "The audio device did not start in time.",
                detail={"reason": "open_timeout", "timeout_s": OPEN_TIMEOUT_S},
            )

        with self._lock:
            error = self._error
            started = self._started
        if not started:
            # The thread failed before the first block. Surface the real cause and
            # release the take slot so the next attempt is not refused as busy.
            with self._lock:
                self._active = False
                self._thread = None
            if error is not None:
                raise error
            raise ApiError("AUDIO_UNAVAILABLE", "The audio device could not be opened.")

        with self._lock:
            self._probe_cache = None
            return {
                "file": Path(path).name,
                "device": self._device_label(),
                "samplerate": int(self.config.audio_samplerate),
                "channels": int(self.config.audio_channels),
            }

    def stop(self) -> Optional[Dict[str, Any]]:
        """Close the take and return what was written. ``None`` when idle."""
        with self._lock:
            if not self._active:
                return None
            thread = self._thread

        self._request_stop()
        if thread is not None:
            thread.join(timeout=5.0)

        with self._lock:
            # The thread sets these in its finally block. Reading them after the
            # join means the numbers describe the closed file, not a moving one.
            samples = self._samples
            overruns = self._overruns
            peak = self._peak
            started = self._started_mono
            stopped = self._stopped_mono
            error = self._error
            device = self._device_label()
            samplerate = int(self.config.audio_samplerate)
            channels = int(self.config.audio_channels)
            self._active = False
            self._thread = None

        if started is None:
            # The device never delivered a block. Nothing was written, so this is
            # not an artifact, only a failure to report.
            path = self._path
            if path is not None:
                try:
                    path.unlink(missing_ok=True)
                except OSError as exc:
                    log.warning("could not remove the empty audio file %s: %s", path, exc)
            return {
                "file": None,
                "device": device,
                "duration_s": 0.0,
                "samples": 0,
                "overruns": 0,
                "peak": 0,
                "partial": True,
                "error": error,
                "started_mono": None,
                "stopped_mono": None,
                "samplerate": samplerate,
                "channels": channels,
            }

        duration = samples / float(samplerate) if samplerate else 0.0
        name = self._path.name if self._path is not None else "audio.wav"
        return {
            "file": name,
            "device": device,
            "duration_s": round(duration, 3),
            "samples": samples,
            "overruns": overruns,
            "peak": peak,
            "partial": error is not None,
            "error": error,
            "started_mono": started,
            "stopped_mono": stopped,
            "samplerate": samplerate,
            "channels": channels,
        }

    def request_stop(self) -> None:
        """Ask the worker to finish, without waiting for it.

        Used as the camera's take end callback: the WAV has to stop at the same
        instant as the picture, and the camera only gets to that instant from
        inside its own stop, seconds before the bag is finalised. Signalling here
        and joining in :meth:`stop` keeps the audio tail to one read block.
        """
        self._request_stop()

    def _request_stop(self) -> None:
        with self._lock:
            event = self._stop_event
        if event is not None:
            event.set()

    def _device_label(self) -> str:
        backend = getattr(self, "_current_backend", None)
        if backend is None:
            return str(self.config.audio_device or "system default")
        try:
            return backend.label() or str(self.config.audio_device or "system default")
        except Exception:  # noqa: BLE001 - a label is never worth raising for
            return str(self.config.audio_device or "system default")

    # ------------------------------------------------------------ the thread

    def _run(
        self,
        path: Path,
        limit_s: float,
        stop_event: threading.Event,
        ready: threading.Event,
    ) -> None:
        samplerate = int(self.config.audio_samplerate)
        channels = int(self.config.audio_channels)
        block = max(1, int(self.config.audio_block_frames))
        block_seconds = block / float(samplerate)
        # Runaway guard only, see SAFETY_TAIL_S.
        hard_limit = (limit_s + SAFETY_TAIL_S) if limit_s > 0 else 0.0

        backend: Optional[AudioBackend] = None
        handle: Optional[wave.Wave_write] = None
        try:
            backend = self._backend_factory(str(self.config.audio_device or ""))
            self._current_backend = backend
            backend.open(samplerate, channels, str(self.config.audio_device or ""))
            path.parent.mkdir(parents=True, exist_ok=True)
            handle = wave.open(str(path), "wb")
            handle.setnchannels(channels)
            handle.setsampwidth(SAMPLE_WIDTH_BYTES)
            handle.setframerate(samplerate)
            with self._lock:
                self._started = True
            ready.set()

            while not stop_event.is_set():
                payload, overflowed = backend.read(block)
                now = time.monotonic()
                with self._lock:
                    if self._started_mono is None:
                        # The block just returned covers [now - block, now], so
                        # the first sample sits one block earlier. Using `now`
                        # would bias the anchor late by exactly one block.
                        self._started_mono = now - block_seconds
                    if overflowed:
                        self._overruns += 1
                    if payload:
                        handle.writeframes(payload)
                        self._samples += len(payload) // (SAMPLE_WIDTH_BYTES * channels)
                        peak = _block_peak(payload)
                        self._level = peak
                        if peak > HEARD_PEAK:
                            self._heard = True
                        if peak > self._peak:
                            self._peak = peak
                    elapsed = now - (self._started_mono or now)
                if hard_limit and elapsed >= hard_limit:
                    log.warning("audio hit its runaway guard at %.0fs", elapsed)
                    break
        except ApiError as exc:
            with self._lock:
                self._error = exc
            ready.set()
        except Exception as exc:  # noqa: BLE001 - the thread must never die silently
            log.exception("audio capture failed")
            with self._lock:
                self._error = ApiError(
                    "AUDIO_RECORD_FAILED", detail={"error": f"{type(exc).__name__}: {exc}"}
                )
            ready.set()
        finally:
            if handle is not None:
                try:
                    handle.close()
                except Exception as exc:  # noqa: BLE001
                    log.warning("could not close the audio file: %s", exc)
            if backend is not None:
                backend.close()
            with self._lock:
                self._stopped_mono = time.monotonic()
                self._level = 0
            ready.set()

    # ---------------------------------------------------------------- status

    def status(self) -> Dict[str, Any]:
        """Live view for the capture screen banner, one call per second at most."""
        with self._lock:
            capturing = self._active
            level = self._level
            samples = self._samples
            overruns = self._overruns
            heard = self._heard
            error = self._error
            samplerate = int(self.config.audio_samplerate)

        if not self.enabled:
            return {
                "enabled": False,
                "required": False,
                "capturing": False,
                "available": False,
                "device": None,
                "samplerate": None,
                "channels": None,
                "duration_s": 0.0,
                "level": 0.0,
                "level_dbfs": None,
                "heard": False,
                "overruns": 0,
                "error": None,
                "reason": "Audio capture is switched off in this configuration.",
            }

        return {
            "enabled": True,
            "required": self.required,
            "capturing": capturing,
            "available": error is None,
            "device": self._device_label(),
            "samplerate": samplerate,
            "channels": int(self.config.audio_channels),
            "duration_s": round(samples / float(samplerate), 2) if samplerate else 0.0,
            "level": _normalize(level),
            # The same peak in dBFS, because the screens draw a level on a
            # logarithmic scale: a linear ratio parks ordinary speech at about
            # three percent and makes a working microphone look dead.
            "level_dbfs": dbfs(level),
            # Whether any block since the take started rose above the noise floor.
            # A single block of silence between words must not look like a dead
            # microphone, so the banner keys off this rather than the live level.
            "heard": heard,
            "overruns": overruns,
            "error": error.message if error is not None else None,
            "reason": None,
        }

    # ------------------------------------------------------- live input check

    def test_start(self) -> Dict[str, Any]:
        """Open the input for a live level meter and keep it open until stopped.

        The home screen's Test microphone button used to record a fixed two seconds
        and report one peak. That answered "is the device dead" but not "is the
        level sensible", and a collector holding a prototype microphone wants to
        see the meter move as they speak. So this opens the device and leaves it
        open; the screen polls :meth:`test_status` for the level and calls
        :meth:`test_stop` when done.

        Two guards keep a forgotten check from holding the device: the thread
        stops itself when nothing has polled for :data:`TEST_LINGER_S`, and it
        never runs longer than :data:`TEST_MAX_S`.
        """
        if not self.enabled:
            raise ApiError("AUDIO_DISABLED", detail={"reason": "disabled"})

        with self._lock:
            if self._active:
                raise ApiError(
                    "AUDIO_DEVICE_BUSY",
                    "Audio is being captured for a take. Run the check outside a recording.",
                )
            alive = self._test_thread is not None and self._test_thread.is_alive()
            if self._test_active and alive:
                # Pressing the button twice must not open a second stream on the
                # same device, so a running check is returned rather than restarted.
                return self._test_status_locked()

            self._test_thread = None
            self._test_stop_event = threading.Event()
            self._test_ready = threading.Event()
            self._test_active = True
            self._test_device = ""
            self._test_started_mono = None
            self._test_last_poll = time.monotonic()
            self._test_samples = 0
            self._test_overruns = 0
            self._test_level = 0
            self._test_peak = 0
            self._test_heard = False
            self._test_error = None
            stop_event = self._test_stop_event
            ready = self._test_ready

        thread = threading.Thread(
            target=self._run_test,
            args=(stop_event, ready),
            name="audio-test",
            daemon=True,
        )
        with self._lock:
            self._test_thread = thread
        thread.start()

        if not ready.wait(OPEN_TIMEOUT_S):
            with self._lock:
                self._test_thread = None
                self._test_active = False
            if stop_event is not None:
                stop_event.set()
            raise ApiError(
                "AUDIO_UNAVAILABLE",
                "The audio device did not start in time.",
                detail={"reason": "open_timeout", "timeout_s": OPEN_TIMEOUT_S},
            )

        with self._lock:
            started = self._test_started_mono is not None
            error = self._test_error
            status = self._test_status_locked()
            if not started:
                self._test_thread = None
                self._test_active = False
        if not started:
            if error is not None:
                raise error
            raise ApiError("AUDIO_UNAVAILABLE", "The audio device could not be opened.")
        return status

    def test_status(self) -> Dict[str, Any]:
        """The live level for the meter. Polled several times a second, so cheap.

        Updating the poll clock here, rather than relying on the caller, is what
        makes the linger guard work: a screen that stops polling releases the
        device on its own.
        """
        with self._lock:
            self._test_last_poll = time.monotonic()
            return self._test_status_locked()

    def test_stop(self) -> Dict[str, Any]:
        """Close the check and report the loudest sample seen since it started.

        Stopping twice is a no-op rather than an error, because the screen sends
        this on unmount whether or not it ever managed to start.
        """
        result = self._end_test()
        if result is not None:
            return result
        samplerate = int(self.config.audio_samplerate)
        return {
            "device": str(self.config.audio_device or "system default"),
            "samplerate": samplerate,
            "channels": int(self.config.audio_channels),
            "duration_s": 0.0,
            "overruns": 0,
            "peak": 0,
            "peak_dbfs": None,
            "verdict": "silent",
            "error": None,
        }

    def _test_status_locked(self) -> Dict[str, Any]:
        with self._lock:
            thread = self._test_thread
            running = bool(self._test_active and thread is not None and thread.is_alive())
            level = self._test_level
            samplerate = int(self.config.audio_samplerate)
            samples = self._test_samples
            return {
                "running": running,
                "device": self._test_device or str(self.config.audio_device or "system default"),
                "samplerate": samplerate or None,
                "channels": int(self.config.audio_channels),
                "duration_s": round(samples / float(samplerate), 2) if samplerate else 0.0,
                "level": _normalize(level),
                # Amplitude alone makes a poor meter: normal speech sits around
                # 0.1 of full scale and would barely lift the bar. The same peak
                # in dBFS lets the screen draw a scale from silence to clipping.
                "level_dbfs": dbfs(level),
                "peak": self._test_peak,
                "peak_dbfs": dbfs(self._test_peak),
                "heard": self._test_heard,
                "overruns": self._test_overruns,
                "error": self._test_error.message if self._test_error is not None else None,
            }

    def _end_test(self) -> Optional[Dict[str, Any]]:
        """Close the check. ``None`` when nothing was running."""
        with self._lock:
            thread = self._test_thread
            stop_event = self._test_stop_event
            self._test_active = False
        if thread is None:
            return None
        if stop_event is not None:
            stop_event.set()
        thread.join(timeout=5.0)
        with self._lock:
            peak = self._test_peak
            samples = self._test_samples
            overruns = self._test_overruns
            error = self._test_error
            device = self._test_device or str(self.config.audio_device or "system default")
            samplerate = int(self.config.audio_samplerate)
            channels = int(self.config.audio_channels)
            self._test_thread = None
            self._test_stop_event = None
            self._test_ready = None
            self._test_level = 0
        if error is not None and samples == 0:
            log.warning("the live input check failed: %s", error.message)
        level_dbfs = dbfs(peak)
        return {
            "device": device,
            "samplerate": samplerate,
            "channels": channels,
            "duration_s": round(samples / float(samplerate), 2) if samplerate else 0.0,
            "overruns": overruns,
            "peak": peak,
            "peak_dbfs": level_dbfs,
            "verdict": verdict_for(level_dbfs),
            "error": error.message if error is not None else None,
        }

    def _run_test(self, stop_event: threading.Event, ready: threading.Event) -> None:
        samplerate = int(self.config.audio_samplerate)
        channels = int(self.config.audio_channels)
        block = max(1, int(self.config.audio_block_frames))
        opened_mono = time.monotonic()

        backend: Optional[AudioBackend] = None
        try:
            backend = self._backend_factory(str(self.config.audio_device or ""))
            backend.open(samplerate, channels, str(self.config.audio_device or ""))
            with self._lock:
                self._test_device = backend.label()
                self._test_started_mono = time.monotonic()
                self._test_last_poll = time.monotonic()
            ready.set()

            while not stop_event.is_set():
                payload, overflowed = backend.read(block)
                now = time.monotonic()
                with self._lock:
                    if overflowed:
                        self._test_overruns += 1
                    if payload:
                        self._test_samples += len(payload) // (SAMPLE_WIDTH_BYTES * channels)
                        peak = _block_peak(payload)
                        self._test_level = peak
                        if peak > HEARD_PEAK:
                            self._test_heard = True
                        if peak > self._test_peak:
                            self._test_peak = peak
                    idle_for = now - self._test_last_poll
                if idle_for > TEST_LINGER_S:
                    log.info("live input check closed after %.0fs without a poll", idle_for)
                    break
                if now - opened_mono >= TEST_MAX_S:
                    log.warning("the live input check hit its %.0fs guard", TEST_MAX_S)
                    break
        except ApiError as exc:
            with self._lock:
                self._test_error = exc
            ready.set()
        except Exception as exc:  # noqa: BLE001 - the thread must never die silently
            log.exception("the live input check failed")
            with self._lock:
                self._test_error = ApiError(
                    "AUDIO_RECORD_FAILED", detail={"error": f"{type(exc).__name__}: {exc}"}
                )
            ready.set()
        finally:
            if backend is not None:
                backend.close()
            with self._lock:
                self._test_level = 0
                self._test_active = False
            ready.set()

    def shutdown(self) -> None:
        """Release the device. A take in progress is closed and left on disk."""
        self._end_test()
        result = self.stop()
        if result is not None:
            log.info("audio closed at shutdown after %.1fs", result["duration_s"])


class DisabledAudio:
    """Stand-in used when no audio worker was supplied.

    Mirrors the slice of :class:`AudioWorker` the capture service uses, so the
    service never has to branch on ``self.audio is None``. It reports audio as
    disabled rather than as a fault, because a caller that did not wire audio in
    did not ask for it.
    """

    def __init__(self, config: Config) -> None:
        self.config = config

    enabled = False
    required = False
    capturing = False
    last_error = None

    def probe(self, force: bool = False) -> Dict[str, Any]:
        return {
            "enabled": False,
            "required": False,
            "available": False,
            "device": None,
            "reason": "No audio worker is attached to this service.",
        }

    def start(self, path: Path, limit_s: float) -> Dict[str, Any]:
        raise ApiError("AUDIO_DISABLED", detail={"reason": "no_worker"})

    def stop(self) -> Optional[Dict[str, Any]]:
        return None

    def status(self) -> Dict[str, Any]:
        return {
            "enabled": False,
            "required": False,
            "capturing": False,
            "available": False,
            "device": None,
            "samplerate": None,
            "channels": None,
            "duration_s": 0.0,
            "level": 0.0,
            "level_dbfs": None,
            "heard": False,
            "overruns": 0,
            "error": None,
            "reason": "No audio worker is attached to this service.",
        }

    def test_start(self) -> Dict[str, Any]:
        raise ApiError("AUDIO_DISABLED", detail={"reason": "no_worker"})

    def test_status(self) -> Dict[str, Any]:
        return {
            "running": False,
            "device": None,
            "samplerate": None,
            "channels": None,
            "duration_s": 0.0,
            "level": 0.0,
            "level_dbfs": None,
            "peak": 0,
            "peak_dbfs": None,
            "heard": False,
            "overruns": 0,
            "error": None,
        }

    def test_stop(self) -> Dict[str, Any]:
        return {
            "device": "",
            "samplerate": int(self.config.audio_samplerate),
            "channels": int(self.config.audio_channels),
            "duration_s": 0.0,
            "overruns": 0,
            "peak": 0,
            "peak_dbfs": None,
            "verdict": "silent",
            "error": None,
        }

    def shutdown(self) -> None:
        return None


def _block_peak(payload: bytes) -> int:
    """Largest absolute 16 bit sample in one block."""
    if len(payload) < SAMPLE_WIDTH_BYTES:
        return 0
    if len(payload) % 2:
        payload = payload[:-1]
    samples = array.array("h")
    samples.frombytes(payload)
    try:
        return max(max(samples), -min(samples))
    except ValueError:
        return 0


def dbfs(peak: int) -> Optional[float]:
    if peak <= 0:
        return None
    return round(20.0 * math.log10(peak / 32768.0), 1)


def _normalize(peak: int) -> float:
    return round(min(1.0, peak / 32768.0), 3)


def verdict_for(level_dbfs: Optional[float]) -> str:
    """The four states the check reports. Shared by every caller that stops one."""
    if level_dbfs is None or level_dbfs <= SILENT_DBFS:
        return "silent"
    if level_dbfs >= CLIPPING_DBFS:
        return "clipping"
    if level_dbfs <= QUIET_DBFS:
        return "quiet"
    return "ok"