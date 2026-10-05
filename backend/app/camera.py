"""Exclusive camera access: preview streaming and bag recording.

One thread owns the pipeline for the whole process lifetime. That is not a style
choice, D435i is an exclusive device and docs/DESIGN.md section 3.1 makes the
capture thread the single owner. Callers submit work as commands and wait on an
event, exactly as section 9.2 describes.

Five facts about the SDK that shape this module, all measured against a real
D435I, SDK 2.58, firmware 5.17.3.10, rather than assumed.

* **The recording file must end in ``.db3``.** ``enable_record_to_file`` rejects
  every other extension with "Output file must have .db3 extension". The docs
  call the artifact a bag file, which it is in the rosbag2 sense, but the name on
  disk has to be ``capture.db3``. See :data:`app.sessions.BAG_NAME`.
* **The recorder is attached at ``pipe.start()``.** It cannot be added to or
  removed from a running pipeline, so starting and stopping a recording are both
  a pipeline restart. Measured: 0.3 s to open, 1.1 s to close. That matches the
  "about one second" docs/DESIGN.md section 6.2 promises, and is why the frontend
  covers the preview with an overlay.
* **The accelerometer offers 100, 200 and 400 Hz on this firmware.** The shipped
  YAML asked for 63 Hz, which makes the whole request unresolvable and the
  pipeline refuse to start. :meth:`CameraWorker.validate_plan` turns that class of
  mistake into an error naming the supported rates, before the device is asked
  for anything.
* **Frame data arrives as a ``BufData`` buffer, not an ndarray.** ``bytes()`` on it
  gives the raw payload, which is all the encoder needs.
* **Dimensions come from the profile, not the frame.** Iterating a frameset yields
  base ``frame`` objects, and only video streams carry a size. A motion sample is
  12 bytes of three floats.

JPEG encoding uses Pillow, already a dependency for the stage diagrams.
docs/DESIGN.md section 6.3 sketches ``cv2.imencode``, but OpenCV appears nowhere in
requirements.txt and Pillow produces the same MJPEG payload, so this avoids adding
a heavy dependency for one call.
"""

from __future__ import annotations

import io
import logging
import queue
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Protocol, Tuple

from PIL import Image

from .config import Config
from .errors import ApiError

log = logging.getLogger(__name__)

# Logical stream names, fixed by docs/API.md section 3.3.
IMAGE_STREAMS: Tuple[str, ...] = ("depth", "color", "infrared_1", "infrared_2")
MOTION_STREAMS: Tuple[str, ...] = ("accel", "gyro")

# Motion rates the D400 series actually advertises, queried from the device.
# accel 63 is absent on purpose: firmware 5.17.3.10 offers 100, 200 and 400, and
# asking for 63 makes the entire stream request unresolvable.
MOTION_RATES: Dict[str, Tuple[int, ...]] = {
    "accel": (100, 200, 400),
    "gyro": (200, 400),
}

_FORMAT_NAMES: Tuple[str, ...] = ("z16", "bgr8", "y8", "motion_xyz32f")


def require_db3(path: str) -> None:
    """Reject any recording path that is not a ``.db3`` file.

    Measured against SDK 2.58 on a D435I: ``enable_record_to_file`` refuses every
    other extension outright, and the shipped convention used to ask for
    ``capture.bag``. Raising here means the message names the real cause instead
    of echoing the SDK's "Output file must have .db3 extension" verbatim.
    """
    if not str(path).lower().endswith(".db3"):
        raise ApiError(
            "CAMERA_ERROR",
            "A recording file must end in .db3.",
            detail={"path": str(path)},
        )

# Longest the worker blocks on frames before it looks at its command queue again.
# This is the worst case command latency while streaming, an order of magnitude
# below the pipeline restart cost, so it is invisible in practice.
FRAME_WAIT_MS = 60

# A pipeline that delivers nothing for this long is treated as a fault. The SDK
# blocks inside wait_for_frames, so the only available signal is the absence of
# data rather than an exception.
FRAME_STALL_S = 5.0

# Logical name -> stream index. Infrared uses the stereo module's index 1 and 2
# for the left and right imagers.
_STREAM_INDEX = {
    "depth": 0,
    "color": 0,
    "infrared_1": 1,
    "infrared_2": 2,
    "accel": 0,
    "gyro": 0,
}

CMD_OPEN = "open"
CMD_CLOSE = "close"
CMD_SHUTDOWN = "shutdown"


@dataclass
class Frame:
    """One stream's sample, already copied out of SDK owned memory."""

    stream: str
    format: str
    width: int
    height: int
    data: bytes


@dataclass
class FrameSet:
    frames: List[Frame] = field(default_factory=list)

    def get(self, stream: str) -> Optional[Frame]:
        for frame in self.frames:
            if frame.stream == stream:
                return frame
        return None


@dataclass
class PipelinePlan:
    """What to ask the device for. A ``record_path`` means recording."""

    mode: str
    streams: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    record_path: Optional[str] = None
    serial: str = ""

    @property
    def recording(self) -> bool:
        return self.record_path is not None


class CaptureBackend(Protocol):
    """The narrow slice of the SDK this module needs. Replaced in tests."""

    def open(self, plan: PipelinePlan) -> None:
        ...

    def close(self) -> None:
        ...

    def wait(self, timeout_ms: int) -> Optional[FrameSet]:
        ...

    def device_info(self) -> Dict[str, Any]:
        ...


# ------------------------------------------------------------------- real SDK


class RealSenseBackend:
    """Wrapper over pyrealsense2, imported lazily.

    Keeping the import inside :meth:`_sdk` means the service starts, every non
    camera endpoint works and the whole test suite runs on a machine with no SDK
    installed. A missing SDK then surfaces as a specific reported reason instead
    of an import error at module load.
    """

    def __init__(self, serial: str = "") -> None:
        self.serial = serial
        self._rs: Any = None
        self._pipe: Any = None

    # ------------------------------------------------------------- helpers

    def _sdk(self) -> Any:
        if self._rs is None:
            try:
                import pyrealsense2 as rs  # deliberate lazy import
            except ImportError as exc:  # pragma: no cover - host dependent
                raise ApiError(
                    "DEVICE_NOT_FOUND",
                    "The pyrealsense2 SDK is not available to this interpreter.",
                    detail={"reason": "sdk_missing"},
                ) from exc
            self._rs = rs
        return self._rs

    def _stream_enum(self, name: str) -> Any:
        rs = self._sdk()
        table = {
            "color": rs.stream.color,
            "depth": rs.stream.depth,
            "infrared_1": rs.stream.infrared,
            "infrared_2": rs.stream.infrared,
            "accel": rs.stream.accel,
            "gyro": rs.stream.gyro,
        }
        if name not in table:
            raise ApiError(
                "CAMERA_ERROR",
                f"Unknown stream {name!r} in the recording configuration.",
                detail={"stream": name, "known": sorted(table)},
            )
        return table[name]

    def _format_enum(self, name: str) -> Any:
        rs = self._sdk()
        table = {
            "z16": rs.format.z16,
            "bgr8": rs.format.bgr8,
            "y8": rs.format.y8,
            "motion_xyz32f": rs.format.motion_xyz32f,
        }
        key = str(name or "").lower()
        if key not in table:
            raise ApiError(
                "CAMERA_ERROR",
                f"Unknown pixel format {name!r} in the recording configuration.",
                detail={"format": name, "known": sorted(table)},
            )
        return table[key]

    # ------------------------------------------------------------ lifecycle

    def open(self, plan: PipelinePlan) -> None:
        rs = self._sdk()
        config = rs.config()
        serial = plan.serial or self.serial
        if serial:
            config.enable_device(serial)

        for name, spec in plan.streams.items():
            stream = self._stream_enum(name)
            index = _STREAM_INDEX.get(name, 0)
            fmt = self._format_enum(spec.get("format"))
            fps = int(spec.get("fps") or 30)
            if name in IMAGE_STREAMS:
                # Positional, the binding offers no keyword names for these.
                config.enable_stream(
                    stream,
                    index,
                    int(spec.get("width") or 640),
                    int(spec.get("height") or 480),
                    fmt,
                    fps,
                )
            else:
                config.enable_stream(stream, index, fmt, fps)

        if plan.record_path:
            require_db3(str(plan.record_path))
            config.enable_record_to_file(str(plan.record_path))

        pipe = rs.pipeline()
        try:
            pipe.start(config)
        except RuntimeError as exc:
            raise self._translate_open_error(str(exc)) from exc
        self._pipe = pipe

    @staticmethod
    def _translate_open_error(message: str) -> ApiError:
        """Map an SDK startup failure onto the right error code.

        "Couldn't resolve requests" is worth special casing. It means the stream
        combination is not one the device offers, which is a configuration
        mistake rather than a hardware fault, so no amount of cable reseating
        will fix it.
        """
        lowered = message.lower()
        if "resolve requests" in lowered:
            return ApiError(
                "CAMERA_ERROR",
                "The device does not offer that combination of streams. Check "
                "camera.recording.streams in the configuration.",
                detail={"reason": "unresolvable_streams", "sdk": message},
            )
        if "no device" in lowered:
            return ApiError("DEVICE_NOT_FOUND", detail={"sdk": message})
        if "access" in lowered or "busy" in lowered or "power state" in lowered:
            return ApiError("DEVICE_BUSY", detail={"sdk": message})
        return ApiError("CAMERA_ERROR", detail={"sdk": message})

    def close(self) -> None:
        pipe, self._pipe = self._pipe, None
        if pipe is None:
            return
        try:
            pipe.stop()
        except RuntimeError as exc:  # pragma: no cover - teardown is best effort
            log.warning("pipeline stop reported %s", exc)

    # -------------------------------------------------------------- sampling

    def wait(self, timeout_ms: int) -> Optional[FrameSet]:
        pipe = self._pipe
        if pipe is None:
            return None
        try:
            frames = pipe.wait_for_frames(timeout_ms)
        except RuntimeError as exc:
            # A short wait times out routinely at 30 fps, and the SDK reports
            # that as an exception rather than an empty frameset. Only a genuine
            # failure is worth tearing the pipeline down for, so the timeout
            # message is recognised and turned back into "nothing yet".
            message = str(exc)
            if "didn't arrive within" in message:
                return None
            raise ApiError("CAMERA_ERROR", detail={"sdk": message}) from exc

        collected: List[Frame] = []
        for frame in frames:
            profile = frame.get_profile()
            stream = self._logical_name(profile)
            if stream is None:
                continue
            payload = bytes(frame.get_data())
            if stream in IMAGE_STREAMS:
                video = profile.as_video_stream_profile()
                collected.append(
                    Frame(
                        stream=stream,
                        format=str(profile.format()).split(".")[-1].lower(),
                        width=int(video.width()),
                        height=int(video.height()),
                        data=payload,
                    )
                )
            else:
                collected.append(
                    Frame(stream=stream, format="motion_xyz32f", width=0, height=0, data=payload)
                )
        return FrameSet(frames=collected)

    @staticmethod
    def _logical_name(profile: Any) -> Optional[str]:
        """Map an SDK profile onto the fixed stream names of docs/API.md 3.3.

        The infrared imagers report themselves as "Infrared 1" and "Infrared 2"
        rather than a bare "Infrared" with an index, so the name is matched by
        prefix. Verified against SDK 2.58 on a D435I.
        """
        name = str(profile.stream_name())
        if name == "Color":
            return "color"
        if name == "Depth":
            return "depth"
        if name.startswith("Infrared"):
            return "infrared_1" if int(profile.stream_index()) == 1 else "infrared_2"
        if name == "Accel":
            return "accel"
        if name == "Gyro":
            return "gyro"
        return None

    def device_info(self) -> Dict[str, Any]:
        """Identify the device the open pipeline is using. Feeds the session record."""
        pipe = self._pipe
        if pipe is None:
            return {}
        try:
            device = pipe.get_active_profile().get_device()
            rs = self._sdk()
            return {
                "name": str(device.get_info(rs.camera_info.name)),
                "serial": str(device.get_info(rs.camera_info.serial_number)),
                "firmware": str(device.get_info(rs.camera_info.firmware_version)),
            }
        except Exception as exc:  # pragma: no cover - identification is optional
            log.debug("could not read device info: %s", exc)
            return {}


# ------------------------------------------------------------ frame encoding


def encode_jpeg(frame: Frame, quality: int) -> Optional[bytes]:
    """Encode one frame as JPEG. ``None`` for motion frames or unknown layouts."""
    try:
        if frame.format == "bgr8":
            image = Image.frombytes(
                "RGB", (frame.width, frame.height), frame.data, "raw", "BGR"
            )
        elif frame.format == "y8":
            image = Image.frombytes("L", (frame.width, frame.height), frame.data)
        elif frame.format == "z16":
            # Depth is 16 bit, so it is stretched to 8 bit to make a legible
            # thumbnail. A colour mapped depth view is explicitly out of scope,
            # camera.recording.colorize_depth stays false.
            samples = memoryview(frame.data).cast("H")
            low = min(samples)
            high = max(samples)
            span = max(1, high - low)
            scaled = bytes(int((value - low) * 255 / span) for value in samples)
            image = Image.frombytes("L", (frame.width, frame.height), scaled)
        else:
            return None
    except (ValueError, TypeError) as exc:
        log.debug("cannot encode a %s frame as JPEG: %s", frame.stream, exc)
        return None

    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=quality, optimize=True)
    return buffer.getvalue()


# --------------------------------------------------------------- the worker


class CameraWorker:
    """Owns the pipeline on its own thread and serves frames to the preview."""

    def __init__(
        self,
        config: Config,
        backend_factory: Optional[Callable[[str], CaptureBackend]] = None,
    ) -> None:
        self.config = config
        self._backend_factory = backend_factory or RealSenseBackend
        self._backend: Optional[CaptureBackend] = None

        self._commands: "queue.Queue[tuple]" = queue.Queue()
        self._thread: Optional[threading.Thread] = None
        self._owner_lock = threading.Lock()

        # docs/DESIGN.md section 3.1: one slot holding only the newest frame, plus
        # a sequence number so the stream endpoint can wait for a change instead
        # of polling. This is what keeps the preview from lagging behind reality.
        self._frame_cond = threading.Condition()
        self._latest_jpeg: Optional[bytes] = None
        self._frame_seq = 0

        self._plan: Optional[PipelinePlan] = None
        self._recording = False
        self._frame_counts: Dict[str, int] = {}
        self._last_frame_at: Optional[float] = None
        self._last_error: Optional[ApiError] = None
        self._thumbnail: Optional[bytes] = None
        self._thumbnail_stream: Optional[str] = None
        self._auto_stop_timer: Optional[threading.Timer] = None
        self._on_auto_stop: Optional[Callable[[], None]] = None
        # Monotonic bounds of the last take. Wall clock ISO stamps are only
        # accurate to the second, and the pipeline restart either side of a take
        # would otherwise be counted as recording time, so the duration comes from
        # here instead.
        self._record_started_at: Optional[float] = None
        self._record_stopped_at: Optional[float] = None

    # ------------------------------------------------------------- lifecycle

    def start(self) -> None:
        """Start the owner thread. Called once from the service bootstrap."""
        with self._owner_lock:
            if self._thread is not None:
                return
            self._thread = threading.Thread(
                target=self._run, name="camera-worker", daemon=True
            )
            self._thread.start()

    def shutdown(self) -> None:
        with self._owner_lock:
            thread = self._thread
            self._thread = None
        if thread is None:
            return
        done = threading.Event()
        self._commands.put((CMD_SHUTDOWN, None, done, {}))
        done.wait(10.0)
        thread.join(timeout=10.0)

    # ------------------------------------------------------------ public API

    @property
    def streaming(self) -> bool:
        return self._plan is not None

    @property
    def recording(self) -> bool:
        return self._recording

    @property
    def last_error(self) -> Optional[ApiError]:
        return self._last_error

    @property
    def frame_counts(self) -> Dict[str, int]:
        return dict(self._frame_counts)

    def set_auto_stop_callback(self, callback: Optional[Callable[[], None]]) -> None:
        """The service registers what to do when a take runs past its limit."""
        self._on_auto_stop = callback

    def validate_plan(self, plan: PipelinePlan) -> None:
        """Check a plan before the device sees it.

        Catches the mistakes that would otherwise surface only when a collector
        presses Record: an unknown stream or format, and a motion rate the SDK
        will refuse. Because the shipped configuration once asked for accel at a
        rate this device does not offer, the message names the supported values.
        """
        for name, spec in plan.streams.items():
            if name not in IMAGE_STREAMS + MOTION_STREAMS:
                raise ApiError(
                    "CAMERA_ERROR",
                    f"Unknown stream {name!r} in the recording configuration.",
                    detail={"stream": name, "known": list(IMAGE_STREAMS + MOTION_STREAMS)},
                )
            if str(spec.get("format", "")).lower() not in _FORMAT_NAMES:
                raise ApiError(
                    "CAMERA_ERROR",
                    f"Unknown pixel format for stream {name!r}.",
                    detail={"stream": name, "format": spec.get("format")},
                )
            try:
                fps = int(spec.get("fps") or 0)
            except (TypeError, ValueError) as exc:
                raise ApiError(
                    "CAMERA_ERROR",
                    f"Frame rate for stream {name!r} is not a number.",
                    detail={"stream": name, "fps": spec.get("fps")},
                ) from exc
            if fps <= 0:
                raise ApiError(
                    "CAMERA_ERROR",
                    f"Frame rate for stream {name!r} must be greater than zero.",
                    detail={"stream": name, "fps": fps},
                )
            allowed = MOTION_RATES.get(name)
            if allowed and fps not in allowed:
                raise ApiError(
                    "CAMERA_ERROR",
                    f"Stream {name!r} does not support {fps} frames per second. "
                    f"The device offers {', '.join(str(rate) for rate in allowed)}.",
                    detail={"stream": name, "fps": fps, "supported": list(allowed)},
                )

    def start_preview(self) -> None:
        """Idempotent. Calling it while already previewing does nothing."""
        if self._plan is not None and not self._recording and self._plan.mode == "preview":
            return
        self._apply(
            PipelinePlan(
                mode="preview",
                streams={
                    "color": {
                        "width": self.config.preview_width,
                        "height": self.config.preview_height,
                        "format": "bgr8",
                        "fps": self.config.preview_fps,
                    }
                },
                serial=self.config.camera_serial,
            )
        )

    def start_recording(self, record_path: Path, limit_s: float) -> None:
        """Restart the pipeline with the recorder attached and arm the timer."""
        plan = PipelinePlan(
            mode="record",
            streams={name: dict(spec) for name, spec in self.config.recording_streams.items()},
            record_path=str(record_path),
            serial=self.config.camera_serial,
        )
        self.validate_plan(plan)
        self._apply(plan)
        self._arm_auto_stop(limit_s)

    def stop_recording(self) -> Dict[str, int]:
        """Close the recording pipeline and return the per stream frame counts.

        Restoring the preview is deliberately left to the caller. The stop is the
        moment the take ends, and the caller stamps the end time before the
        preview pipeline restarts, otherwise the one second restart would be
        counted as part of the recording and a short take could look long enough
        to pass the minimum duration check.
        """
        self._cancel_auto_stop()
        if self._recording:
            self._apply(None)
        return dict(self._frame_counts)

    def stop(self) -> None:
        """Release the device entirely."""
        self._cancel_auto_stop()
        self._apply(None)

    # -------------------------------------------------------- frame plumbing

    def wait_for_frame(self, since_seq: int, timeout: float) -> Optional[Tuple[int, bytes]]:
        """Block until a frame newer than ``since_seq`` exists. Drives MJPEG."""
        with self._frame_cond:
            if self._frame_seq <= since_seq:
                self._frame_cond.wait(timeout)
            if self._latest_jpeg is None or self._frame_seq <= since_seq:
                return None
            return self._frame_seq, self._latest_jpeg

    def latest_frame(self) -> Optional[bytes]:
        with self._frame_cond:
            return self._latest_jpeg

    def clear_frames(self) -> None:
        with self._frame_cond:
            self._latest_jpeg = None
            self._frame_cond.notify_all()

    def last_recording_seconds(self) -> float:
        """Length of the most recent take, measured with a monotonic clock.

        Survives the preview restart that follows a stop, so the caller can read
        it after the camera is back in preview mode.
        """
        if self._record_started_at is None:
            return 0.0
        end = self._record_stopped_at if self._record_stopped_at is not None else time.monotonic()
        return max(0.0, end - self._record_started_at)

    def take_thumbnail(self) -> Optional[bytes]:
        """Newest recorded image frame, so a take gets a poster frame."""
        return self._thumbnail or self.latest_frame()

    def device_info(self) -> Dict[str, Any]:
        backend = self._backend
        return backend.device_info() if backend is not None else {}

    # ---------------------------------------------------------- command path

    def _apply(self, plan: Optional[PipelinePlan]) -> None:
        """Open ``plan``, or close the device when it is ``None``."""
        command = CMD_CLOSE if plan is None else CMD_OPEN
        thread = self._thread
        if thread is None:
            # No owner thread yet, so run it inline. Keeps unit tests that drive
            # the worker directly honest without starting a thread.
            error = self._execute(command, plan)
        else:
            done = threading.Event()
            box: Dict[str, Any] = {}
            self._commands.put((command, plan, done, box))
            if not done.wait(20.0):
                raise ApiError("CAMERA_ERROR", "The camera did not respond in time.")
            error = box.get("error")
        if error is not None:
            raise error

    # -------------------------------------------------------------- the loop

    def _run(self) -> None:
        while True:
            timeout = 0.2 if self._plan is None else 0.0
            try:
                command, plan, done, box = self._commands.get(timeout=timeout)
            except queue.Empty:
                self._pump()
                continue

            if command == CMD_SHUTDOWN:
                self._execute(CMD_CLOSE, None)
                done.set()
                return

            box["error"] = self._execute(command, plan)
            done.set()

    def _execute(self, command: str, plan: Optional[PipelinePlan]) -> Optional[ApiError]:
        try:
            self._close_backend()
            if command == CMD_OPEN and plan is not None:
                self._open_backend(plan)
        except ApiError as exc:
            self._last_error = exc
            self._close_backend()
            return exc
        except Exception as exc:  # noqa: BLE001 - the worker must never die
            # A failure the SDK reports as something other than ApiError still has
            # to come back as a reported fault. Letting it escape here would kill
            # the worker thread and turn every later request into a twenty second
            # "did not respond in time", which names the wrong cause.
            log.exception("camera pipeline command %s failed", command)
            error = ApiError("CAMERA_ERROR", detail={"sdk": f"{type(exc).__name__}: {exc}"})
            self._last_error = error
            self._close_backend()
            return error
        return None

    def _open_backend(self, plan: PipelinePlan) -> None:
        backend = self._backend_factory(self.config.camera_serial)
        backend.open(plan)
        self._backend = backend
        self._plan = plan
        self._recording = plan.recording
        self._frame_counts = {}
        self._thumbnail = None
        self._thumbnail_stream = None
        self._last_error = None
        self._last_frame_at = time.monotonic()
        if plan.recording:
            self._record_started_at = time.monotonic()
            self._record_stopped_at = None
        log.info(
            "camera pipeline open in %s mode (%s)",
            plan.mode,
            ", ".join(sorted(plan.streams)) or "no streams",
        )

    def _close_backend(self) -> None:
        backend, self._backend = self._backend, None
        was_recording = self._recording
        self._plan = None
        self._recording = False
        if was_recording:
            # The take ends when the pipeline closes, which is when the SDK
            # flushes the bag. Stamped here so the caller cannot accidentally
            # include the following preview restart.
            self._record_stopped_at = time.monotonic()
        if backend is not None:
            backend.close()

    # ------------------------------------------------------------- auto stop

    def _arm_auto_stop(self, limit_s: float) -> None:
        """Stop and save a take that outruns its limit, per docs/DESIGN.md 6.2.

        A timer rather than a check inside the frame loop, because that loop waits
        on the SDK and a stalled stream would postpone the stop indefinitely.
        """
        self._cancel_auto_stop()
        if limit_s <= 0 or self._on_auto_stop is None:
            return
        timer = threading.Timer(limit_s, self._fire_auto_stop)
        timer.daemon = True
        self._auto_stop_timer = timer
        timer.start()

    def _cancel_auto_stop(self) -> None:
        timer, self._auto_stop_timer = self._auto_stop_timer, None
        if timer is not None:
            timer.cancel()

    def _fire_auto_stop(self) -> None:
        callback = self._on_auto_stop
        if callback is None:
            return
        log.info("take reached its time limit, stopping and saving automatically")
        try:
            callback()
        except Exception:  # pragma: no cover - the service reports its own errors
            log.exception("automatic stop failed")

    # ------------------------------------------------------------ frame pump

    def _pump(self) -> None:
        """Fetch one frameset and publish it. Never raises.

        The body is wrapped as a whole, not just the SDK call, because this runs
        on the worker thread. Anything escaping here would kill the thread and
        turn a decoding problem into a permanently silent preview.
        """
        try:
            self._pump_once()
        except ApiError as exc:
            self._fail(exc)
        except Exception as exc:  # noqa: BLE001 - the worker must never die
            log.exception("camera worker loop failed")
            self._fail(ApiError("CAMERA_ERROR", detail={"sdk": f"{type(exc).__name__}: {exc}"}))

    def _pump_once(self) -> None:
        backend = self._backend
        if backend is None:
            return
        frames = backend.wait(FRAME_WAIT_MS)
        if frames is None:
            self._check_stall()
            return

        self._last_frame_at = time.monotonic()
        for frame in frames.frames:
            if self._recording:
                self._frame_counts[frame.stream] = self._frame_counts.get(frame.stream, 0) + 1
            if frame.stream == "color":
                payload = encode_jpeg(frame, self.config.preview_jpeg_quality)
                if payload:
                    self._publish(payload)
                    if self._recording:
                        self._keep_thumbnail(payload, "color")
            elif self._recording:
                # A plan without a colour stream still needs a poster frame, so
                # any image frame will do, but colour wins when it is available.
                if self._thumbnail_stream == "color":
                    continue
                payload = encode_jpeg(frame, self.config.preview_jpeg_quality)
                if payload:
                    self._keep_thumbnail(payload, frame.stream)

    def _keep_thumbnail(self, payload: bytes, stream: str) -> None:
        """Hold the newest frame, preferring colour over depth or infrared.

        Without the preference the last stream to arrive wins, and depth is both
        the smallest and the least useful as a poster frame.
        """
        if self._thumbnail_stream == "color" and stream != "color":
            return
        self._thumbnail = payload
        self._thumbnail_stream = stream

    def _publish(self, payload: bytes) -> None:
        with self._frame_cond:
            self._latest_jpeg = payload
            self._frame_seq += 1
            self._frame_cond.notify_all()

    def _check_stall(self) -> None:
        if self._last_frame_at is None:
            return
        if time.monotonic() - self._last_frame_at < FRAME_STALL_S:
            return
        self._fail(
            ApiError(
                "CAMERA_ERROR",
                "The camera stopped delivering frames. Check the USB connection.",
                detail={"reason": "stream_stalled"},
            )
        )

    def _fail(self, error: ApiError) -> None:
        log.warning("camera worker fault: %s %s", error.message, error.detail or "")
        self._last_error = error
        self._close_backend()
        self.clear_frames()
