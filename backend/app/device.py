"""Camera availability probe.

Answers one question only, is a RealSense attached and usable. The home screen
and (later) session creation both need it. Streaming, recording and the exclusive
pipeline belong to a CameraWorker that does not exist yet.

Two design points that are not obvious.

**The work runs in a child process.** ``backend/tools/enumerate_devices.py`` is
spawned with the interpreter running the service and its JSON result is read with
a deadline. This is not caution for its own sake. On macOS, when the system UVC
driver holds the camera, the SDK raises ``failed to set power state`` and then
faults during teardown, killing the process with exit code 139. A ``try`` cannot
catch that. Nor can a Python signal interrupt a native USB call that has blocked.
A child process contains both, and turns each into a specific reported reason.

**The service never claims the camera just to report on it.** Enumeration opens
the device. That is why it is cached, kept off the request path beyond the cache
window, and done once explicitly at startup.
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config import Config

log = logging.getLogger(__name__)

# A probe spawns a process and opens the device, so it is not cheap. The camera
# state changes only when someone plugs or unplugs it, so this is deliberately
# measured in seconds. Without it every health poll would block on a cold SDK
# start, which takes over two seconds on macOS.
PROBE_TTL_S = 5.0

# Cold SDK start plus enumeration measured at about 2.2s on macOS. This allows
# generous headroom for a loaded host while still catching a wedged USB stack,
# which never returns at all.
PROBE_TIMEOUT_S = 10.0

ENUMERATOR = Path(__file__).resolve().parent.parent / "tools" / "enumerate_devices.py"

# Reason per failure kind. Written for the operator, not for a log reader, because
# the home screen shows it verbatim.
#
# The two permission entries exist because the advice differs. Not being elevated
# is fixable by the operator. Being elevated and still refused is the known
# upstream macOS fault, documented in the community wheel's README as "a bug in
# the realsense framework", and repeating "use sudo" there sends them round a loop
# that has already been proven not to help.
REASONS = {
    "sdk_missing": (
        "The pyrealsense2 SDK is not available to this interpreter. Install it "
        "into the environment running the service to enable capture."
    ),
    "permission": (
        "The camera is present but the service may not open it. On macOS run the "
        "service with sudo, and check that no video app is holding the camera. On "
        "Linux add the user to the plugdev group and install the udev rules."
    ),
    "permission_rooted": (
        "The camera is present but macOS refused to hand the USB interface to the "
        "SDK, even with elevated privileges. This is a known limitation of the "
        "RealSense SDK on macOS, not a permission problem here. Capture works on "
        "Linux and Windows, so deploy there to record."
    ),
    "no_device": "No RealSense device is attached.",
    "bus_error": "The camera refused the request. Check the cable and the USB port.",
    "sdk_error": "The camera SDK reported an error. See the service log for detail.",
    "timeout": (
        "The camera did not respond in time. The USB stack may be wedged, unplug "
        "and re-seat the camera."
    ),
    "crashed": (
        "The camera SDK crashed while opening the device. On macOS this is a known "
        "bug that happens after the interface is released, and another attempt may "
        "succeed. See the service log for the SDK trace."
    ),
    "disabled": (
        "Camera probing is switched off in this configuration, so the service "
        "reports no device. Set camera.probe to true to enable it."
    ),
}


class DeviceProbe:
    """Detects the camera. Never raises, always returns a reportable object.

    ``enumerator`` and ``timeout_s`` are injectable so the containment behaviour
    can be tested against stub scripts instead of a real camera. The service
    always uses the defaults from this module.
    """

    def __init__(
        self,
        config: Config,
        enumerator: Optional[Path] = None,
        timeout_s: float = PROBE_TIMEOUT_S,
    ) -> None:
        self.config = config
        self.enumerator = Path(enumerator) if enumerator else ENUMERATOR
        self.timeout_s = timeout_s
        # Switched off in tests and in frontend-only development, so neither has
        # to own a camera or wait on a subprocess per bootstrap.
        self.enabled = bool(getattr(config, "camera_probe", True))
        self._lock = threading.Lock()
        self._cached: Optional[Dict[str, Any]] = None
        self._cached_at = 0.0
        # Only log a repeat of the same reason once, so a 1 Hz health poll cannot
        # bury real errors. Keyed on the kind as well as the detail.
        self._last_logged_kind: Optional[str] = None
        self._last_logged_detail: Optional[str] = None

    # ----------------------------------------------------------------- probe

    def info(self, force: bool = False) -> Dict[str, Any]:
        with self._lock:
            now = time.monotonic()
            if not force and self._cached and (now - self._cached_at) < PROBE_TTL_S:
                return dict(self._cached)
            result = self._probe()
            self._cached = result
            self._cached_at = now
            return dict(result)

    @property
    def connected(self) -> bool:
        return bool(self.info()["connected"])

    # --------------------------------------------------------------- internals

    def _probe(self) -> Dict[str, Any]:
        if not self.enabled:
            # Short circuit before touching the filesystem so a disabled probe is
            # completely free, not merely cheap.
            return self._report("disabled", "probing is disabled by configuration")

        if not self.enumerator.is_file():
            return self._report(
                "sdk_error", f"the device enumerator is missing at {self.enumerator}"
            )

        try:
            completed = subprocess.run(
                [sys.executable, str(self.enumerator)],
                capture_output=True,
                text=True,
                timeout=self.timeout_s,
                cwd=str(self.enumerator.parent.parent),
            )
        except subprocess.TimeoutExpired:
            return self._report("timeout", "the enumerator did not return in time")
        except OSError as exc:
            return self._report("sdk_error", f"could not start the enumerator: {exc}")

        payload = self._parse(completed.stdout)
        if payload is None:
            # No JSON. Either the process died before printing or it printed
            # something unexpected. A non zero return code means it was killed,
            # and 139 is the macOS SDK fault described in the module docstring.
            detail = (
                f"exit={completed.returncode} "
                f"stderr={(completed.stderr or '').strip()[:200] or '<empty>'}"
            )
            kind = "crashed" if completed.returncode != 0 else "sdk_error"
            return self._report(kind, detail)

        if payload.get("ok"):
            return self._connected(payload.get("devices") or [])

        return self._report(
            self._refine(str(payload.get("kind") or "sdk_error"), payload),
            str(payload.get("error") or ""),
        )

    @staticmethod
    def _refine(kind: str, payload: Dict[str, Any]) -> str:
        """Narrow a failure kind using facts only the child process knows.

        A refused open means different things depending on whether the process was
        already elevated. Misreporting that sends the operator to try sudo on a
        setup that is already root, which we have measured not to help on macOS.
        """
        if kind == "permission" and payload.get("elevated"):
            return "permission_rooted"
        return kind

    @staticmethod
    def _parse(stdout: str) -> Optional[Dict[str, Any]]:
        """Read the last JSON object the child printed, tolerating stray output."""
        for line in reversed((stdout or "").splitlines()):
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                value = json.loads(line)
            except ValueError:
                continue
            if isinstance(value, dict):
                return value
        return None

    def _connected(self, devices: List[Dict[str, Any]]) -> Dict[str, Any]:
        wanted = self.config.camera_serial
        chosen = None
        for device in devices:
            if not wanted or device.get("serial") == wanted:
                chosen = device
                break

        if chosen is None:
            found = [device.get("serial") for device in devices]
            return self._report(
                "sdk_error",
                f"attached serials are {found}, none matches the configured {wanted}",
            )

        usb_type = chosen.get("usb_type")
        if usb_type and not str(usb_type).startswith("3"):
            # Worth saying out loud. At USB 2.0 the depth stream drops resolution
            # and frame rate, and the operator cannot see that from the screen.
            log.warning(
                "camera %s is linked at USB %s, depth quality will be reduced",
                chosen.get("serial"),
                usb_type,
            )

        self._last_logged_kind = None
        self._last_logged_detail = None
        return {
            "connected": True,
            "name": chosen.get("name"),
            "serial": chosen.get("serial"),
            "firmware": chosen.get("firmware"),
            "usb_type": usb_type,
            "reason": None,
        }

    def _report(self, kind: str, detail: str) -> Dict[str, Any]:
        reason = REASONS.get(kind, REASONS["sdk_error"])
        if detail and self._last_logged_kind != kind:
            log.warning("camera unavailable (%s): %s", kind, detail)
        elif detail and self._last_logged_detail != detail:
            log.debug("camera still unavailable (%s): %s", kind, detail)
        self._last_logged_kind = kind
        self._last_logged_detail = detail
        return {
            "connected": False,
            "name": None,
            "serial": None,
            "firmware": None,
            "usb_type": None,
            "reason": reason,
        }
