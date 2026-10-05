#!/usr/bin/env python3
"""Enumerate RealSense devices and print one line of JSON on stdout.

This runs as a short lived child process, never imported into the service. That
is deliberate and load bearing.

The SDK on macOS can do two things that would otherwise take down the server.

* Segfault. After a failed open it corrupts its own state badly enough that the
  process dies during teardown with exit code 139. An exception handler cannot
  stop that, only a separate process can.
* Block forever. A native call waiting on USB does not honour Python signals, so
  a timeout has to be enforced from outside.

Both are contained by running the work here and having the parent read the result
with a deadline.

Output contract, exactly one JSON object on the last line of stdout.

    {"ok": true, "devices": [...], "elevated": true, "platform": "darwin"}
    {"ok": false, "kind": "permission", "error": "...", "elevated": true, ...}

The child reports facts, the parent turns them into advice. ``elevated`` matters
because whether the process is already root changes what the operator should do
next, and only this side knows.

Every exit path uses ``os._exit`` after flushing. Letting the interpreter run its
normal teardown after a failed SDK call is what produces the spurious 139, which
then looks like a second unrelated fault in the parent's diagnostics.
"""

from __future__ import annotations

import json
import os
import sys
import traceback
from typing import Any, Dict, List

# Substrings meaning this process cannot touch the camera. On Linux it is usually
# udev rules and the plugdev group. On macOS it is the system UVC driver holding
# the interface, and the SDK's own wording for that case is the power state one.
_PERMISSION_MARKERS = (
    "failed to set power state",
    "failed to claim usb interface",
    "RS2_USB_STATUS_ACCESS",
    "permission",
    "access denied",
    "operation not permitted",
)


def _emit(payload: Dict[str, Any]) -> None:
    """Print the single result line and leave immediately."""
    payload.setdefault("elevated", _elevated())
    payload.setdefault("platform", sys.platform)
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    sys.stdout.flush()
    # Skip teardown on purpose. See the module docstring.
    os._exit(0)


def _elevated() -> bool:
    geteuid = getattr(os, "geteuid", None)
    return bool(geteuid and geteuid() == 0)


def _classify(message: str) -> str:
    lowered = message.lower()
    for marker in _PERMISSION_MARKERS:
        if marker.lower() in lowered:
            return "permission"
    if "no device" in lowered or "no realsense" in lowered:
        return "no_device"
    return "sdk_error"


def _read(device: Any, rs: Any, attribute: str) -> Any:
    field = getattr(rs.camera_info, attribute, None)
    if field is None:
        return None
    try:
        value = device.get_info(field)
    except Exception:
        return None
    return str(value) if value is not None else None


def main() -> int:
    try:
        import pyrealsense2 as rs
    except ImportError as exc:
        _emit({"ok": False, "kind": "sdk_missing", "error": str(exc)})
        return 0

    # Keep the SDK quiet. The parent reports failures through the JSON, and a
    # debug log here would only ever be thrown away.
    try:
        rs.log_to_file(rs.log_severity.warn, os.devnull)
    except Exception:
        pass

    devices: List[Dict[str, Any]] = []
    try:
        context = rs.context()
        found = context.query_devices()
        # Length is safe, indexing is what trips the macOS fault. Read the count
        # first so a permission failure is caught before we touch a handle.
        count = len(found)
        if count == 0:
            _emit({"ok": False, "kind": "no_device", "error": "No device is attached."})
            return 0

        for index in range(count):
            device = found[index]
            devices.append(
                {
                    "name": _read(device, rs, "name"),
                    "serial": _read(device, rs, "serial_number"),
                    "firmware": _read(device, rs, "firmware_version"),
                    "usb_type": _read(device, rs, "usb_type_descriptor"),
                }
            )

        _emit({"ok": True, "devices": devices})
        return 0

    except RuntimeError as exc:
        message = str(exc)
        _emit({"ok": False, "kind": _classify(message), "error": message})
        return 0
    except Exception as exc:  # noqa: BLE001 - the SDK raises whatever it likes
        _emit(
            {
                "ok": False,
                "kind": "sdk_error",
                "error": f"{type(exc).__name__}: {exc}",
                "traceback": traceback.format_exc(limit=3),
            }
        )
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
