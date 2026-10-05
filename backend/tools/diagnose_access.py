#!/usr/bin/env python3
"""Diagnose why the SDK cannot open an attached RealSense camera.

This is the escalation step after ``enumerate_devices.py`` reports ``permission``.
It answers the next question, is something else holding the USB interface, and
does releasing it fix the problem.

    python backend\\tools\\diagnose_access.py

On macOS the usual culprit is ``VDCAssistant``, the CoreMediaIO helper that opens
UVC cameras on behalf of the system. The D435i advertises UVC interfaces for its
RGB and depth streams, so macOS claims them, and libusb then cannot. Killing the
helper only helps if nothing reopens the camera in the gap, which is why the
release and the probe happen in one process with no delay between them.

Three rules this script follows, each learned from a hang or a crash.

1. No Python log callback. ``rs.log_to_callback`` is invoked from SDK worker
   threads while the main thread waits on enumeration, and the two deadlock.
   ``rs.log_to_console`` writes from the SDK itself and is safe.
2. Announce each step before running it, so a segfault still shows where it died.
3. Read the device count before touching any handle. Length does not open the
   device, indexing does.
"""

from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

TIMEOUT_S = 25
_current_step = "startup"

# Processes known to claim UVC cameras. avconferenced is the video conferencing
# daemon, VDCAssistant is the CoreMediaIO helper. Neither is needed to capture
# from a RealSense through libusb.
CAMERA_HOLDERS = ("VDCAssistant", "avconferenced")


def _on_alarm(_signum, _frame) -> None:
    sys.stdout.write(
        f"\nFAIL  timed out after {TIMEOUT_S}s while: {_current_step}\n"
        "      the SDK call never returned.\n"
    )
    sys.stdout.flush()
    os._exit(5)


def step(label: str) -> None:
    global _current_step
    _current_step = label
    print(f"  ..  {label}", flush=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Diagnose camera access.")
    parser.add_argument(
        "--no-kill",
        action="store_true",
        help="Do not release camera holders first. Use to see the baseline failure.",
    )
    parser.add_argument("--timeout", type=int, default=TIMEOUT_S)
    return parser.parse_args()


def find_holders() -> list[tuple[int, str]]:
    """Every running process that is known to claim a camera."""
    found: list[tuple[int, str]] = []
    for name in CAMERA_HOLDERS:
        try:
            result = subprocess.run(
                ["pgrep", "-x", name], capture_output=True, text=True, timeout=5
            )
        except (OSError, subprocess.SubprocessError):
            continue
        for line in (result.stdout or "").split():
            try:
                found.append((int(line), name))
            except ValueError:
                continue
    return found


def release_holders() -> None:
    """Ask camera holders to exit, then confirm, then escalate to SIGKILL."""
    holders = find_holders()
    if not holders:
        print("      nothing is holding a camera")
        return

    for pid, name in holders:
        print(f"      asking {name} (pid {pid}) to exit")
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError as exc:
            print(f"        could not signal it: {exc}")

    # A short settle so the SIGKILL pass does not race the SIGTERM pass.
    for _ in range(20):
        if not find_holders():
            print("      all holders have exited")
            return
        sys.stdout.flush()
        time.sleep(0.1)

    for pid, name in find_holders():
        print(f"      forcing {name} (pid {pid}) down")
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError as exc:
            print(f"        could not kill it: {exc}")

    time.sleep(0.3)
    remaining = find_holders()
    if remaining:
        print(f"      still running: {[name for _, name in remaining]}")
        print("      macOS may restart these on demand. If the probe below still")
        print("      fails, they are not the cause.")
    else:
        print("      all holders have exited")


def main() -> int:
    args = parse_args()
    signal.signal(signal.SIGALRM, _on_alarm)
    signal.alarm(args.timeout)

    if sys.platform == "darwin" and os.geteuid() != 0:
        print("FAIL  run this with sudo on macOS, libusb needs root here")
        return 2

    try:
        step("import pyrealsense2")
        import pyrealsense2 as rs
    except ImportError as exc:
        signal.alarm(0)
        print(f"FAIL  pyrealsense2 is not importable: {exc}")
        return 2

    print(f"      elevated  {'yes' if os.geteuid() == 0 else 'no'}")
    print(f"      platform  {sys.platform}")
    print()

    if args.no_kill:
        print("--- camera holders, left alone as asked ---")
        holders = find_holders()
        print(f"      {[name for _, name in holders] or 'none'}")
    else:
        print("--- releasing camera holders ---")
        release_holders()
    print()

    # SDK logs its own debug output to stderr. Safe, see the module docstring.
    step("enable SDK console logging")
    try:
        rs.log_to_console(rs.log_severity.debug)
        print("      stderr now carries the SDK trace")
    except Exception as exc:
        print(f"      console logging unavailable ({exc})")
    print()

    # Everything from here down is the actual experiment. The steps are ordered so
    # that the count is read first, because reading it does not open the device.
    try:
        step("create context")
        context = rs.context()

        step("count devices")
        found = context.query_devices()
        count = len(found)
        print(f"      device count = {count}")
        if count == 0:
            signal.alarm(0)
            print()
            print("RESULT  no device. The holder was not the problem.")
            return 4

        step("open device 0, this is the step that failed before")
        device = found[0]
        print(f"      OPENED  {device}")

        step("read camera info")
        for label, attribute in (
            ("name", "name"),
            ("serial", "serial_number"),
            ("firmware", "firmware_version"),
            ("usb_type", "usb_type_descriptor"),
        ):
            field = getattr(rs.camera_info, attribute, None)
            value = None
            if field is not None:
                try:
                    value = device.get_info(field)
                except Exception:
                    value = None
            print(f"      {label:10} {value if value is not None else '--'}")

        step("list sensors")
        for sensor in device.sensors:
            try:
                name = sensor.get_info(rs.camera_info.name)
            except Exception:
                name = "?"
            print(f"      sensor: {name}")

    except RuntimeError as exc:
        signal.alarm(0)
        print(f"\nFAIL  {exc}")
        print()
        print("RESULT  the device still cannot be opened.")
        print("        Read the SDK trace above, it says which call failed.")
        print("        If it is still RS2_USB_STATUS_ACCESS with no holder left,")
        print("        this macOS build will not hand the interface to libusb.")
        return 3
    except Exception as exc:
        signal.alarm(0)
        print(f"\nFAIL  {type(exc).__name__}: {exc}")
        return 3

    signal.alarm(0)
    print()
    print("RESULT  the camera opened. A resident UVC helper was the blocker.")
    print("        Restart the service the same way you ran this, and keep the")
    print("        holder out of the way for the duration of a capture session.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
