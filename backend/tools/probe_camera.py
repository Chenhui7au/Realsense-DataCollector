#!/usr/bin/env python3
"""Camera bring-up probe.

Run this before the service when standing up a new rig or a new machine. It
answers three questions in one shot, is the camera reachable, what does the SDK
think it is, and which stream profiles does it advertise.

    # macOS 12 and newer. libusb has to displace the system UVC driver.
    sudo /Users/ch7au/miniconda3/bin/python backend/tools/probe_camera.py

    # Linux
    /Users/ch7au/miniconda3/bin/python backend/tools/probe_camera.py

Why the probe is a separate script rather than something the service does. The
service must never claim the camera just to report on it, see the note in
``app/device.py``. This script does claim it, so it is run by hand and never by
the server.

Two hard rules learned the hard way, both about not hanging the terminal.

1. Never hand the SDK a Python log callback. It invokes the callback from its own
   worker threads while the calling thread waits on device enumeration, and the
   two deadlock. Logging goes to a file through the SDK itself instead.
2. Every step is bounded by an alarm and announced before it starts. A native
   call that blocks forever cannot be interrupted from Python, so the alarm
   handler exits the process outright and the last printed step says where it
   died.
"""

from __future__ import annotations

import argparse
import os
import signal
import sys
import time
from pathlib import Path

DEFAULT_LOG = Path("/tmp/realsense-probe.log")
TIMEOUT_S = 25

# SIGALRM and os.geteuid are POSIX only. Guarded so this script still runs on
# Windows, where the watchdog is simply absent and the caller Ctrl-C's instead.
HAS_ALARM = hasattr(signal, "SIGALRM")

# Set by the alarm handler so the closing message can name the stuck step.
_current_step = "startup"


class ProbeTimeout(Exception):
    pass


def _on_alarm(_signum, _frame) -> None:
    # A blocked native call cannot be unwound, so leave immediately. os._exit skips
    # cleanup on purpose, running atexit handlers from a signal handler is unsafe.
    sys.stdout.write(
        f"\nFAIL  timed out after {TIMEOUT_S}s while: {_current_step}\n"
        "      the SDK call did not return. Re-run with sudo on macOS, and check that\n"
        "      nothing else has the camera open.\n"
    )
    sys.stdout.flush()
    os._exit(5)


def arm_timeout(seconds: int) -> None:
    if not HAS_ALARM:
        return
    signal.signal(signal.SIGALRM, _on_alarm)
    signal.alarm(seconds)


def cancel_timeout() -> None:
    if HAS_ALARM:
        signal.alarm(0)


def is_elevated() -> bool:
    """True when running with the privileges the SDK needs on macOS."""
    geteuid = getattr(os, "geteuid", None)
    if geteuid is None:
        return False
    return geteuid() == 0


def step(label: str) -> None:
    """Announce a step before running it, so a hang is attributable."""
    global _current_step
    _current_step = label
    print(f"  ..  {label}", flush=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Probe a RealSense camera.")
    parser.add_argument(
        "--log",
        default=str(DEFAULT_LOG),
        help=f"Where the SDK writes its own log. Defaults to {DEFAULT_LOG}.",
    )
    parser.add_argument(
        "--profiles",
        action="store_true",
        help="Also list every stream profile. Verbose, useful once.",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=TIMEOUT_S,
        help=f"Seconds before giving up. Defaults to {TIMEOUT_S}.",
    )
    return parser.parse_args()


def describe_failure(exc: BaseException) -> None:
    print(f"FAIL  {type(exc).__name__}: {exc}")
    print()
    print("On macOS, RS2_USB_STATUS_ACCESS here means the system UVC driver holds")
    print("the camera. Check it is visible to the OS at all, then check what holds it:")
    print("  system_profiler SPCameraDataType")
    print("  pgrep -fl VDCAssistant")


def main() -> int:
    args = parse_args()
    arm_timeout(args.timeout)

    try:
        step("import pyrealsense2")
        import pyrealsense2 as rs
    except ImportError as exc:
        cancel_timeout()
        print(f"FAIL  pyrealsense2 is not importable: {exc}")
        print()
        print("Install it into the interpreter you are running this with:")
        print("  macOS   pip install pyrealsense2-macosx")
        print("  Windows pip install pyrealsense2")
        print("  Linux   pip install pyrealsense2")
        return 2

    print(f"      python    {sys.version.split()[0]} at {sys.executable}")
    print(f"      elevated  {'yes' if is_elevated() else 'no'}")
    print(f"      platform  {sys.platform}")
    if sys.platform == "darwin" and not is_elevated():
        print("WARN  macOS 12 and newer needs root for libusb to displace the UVC driver")
        print("      if enumeration fails below, re-run this script with sudo")
    if not HAS_ALARM:
        print("NOTE  no alarm available on this platform, a wedged call needs Ctrl-C")
    print()

    # Let the SDK log to a file itself. Do not pass a Python callable, see the
    # module docstring for why that deadlocks.
    try:
        step("enable SDK file logging")
        rs.log_to_file(rs.log_severity.debug, str(args.log))
        print(f"      SDK log   {args.log}")
    except Exception as exc:
        print(f"      SDK file logging unavailable ({exc}), continuing")
    print()

    try:
        step("create context")
        context = rs.context()

        step("query devices")
        found = context.query_devices()
        print(f"      reported  {len(found)} device(s)")

        step("instantiate device handles")
        devices = list(found)

        if not devices:
            cancel_timeout()
            print()
            print("FAIL  no device found. Check the cable and the port.")
            return 4

        print(f"OK    {len(devices)} device(s) usable")
        print()

        for number, device in enumerate(devices, start=1):
            step(f"read info for device {number}")
            print(f"      --- device {number} ---")
            for label, attribute in (
                ("name", "name"),
                ("serial", "serial_number"),
                ("firmware", "firmware_version"),
                ("usb_type", "usb_type_descriptor"),
                ("product_line", "product_line"),
                ("physical_port", "physical_port"),
            ):
                field = getattr(rs.camera_info, attribute, None)
                value = None
                if field is not None:
                    try:
                        value = device.get_info(field)
                    except Exception:
                        value = None
                print(f"      {label:15} {value if value is not None else '--'}")

            usb_type = None
            try:
                usb_type = device.get_info(rs.camera_info.usb_type_descriptor)
            except Exception:
                pass
            if usb_type and not str(usb_type).startswith("3"):
                print()
                print("      WARN  linked at USB 2.0. Depth resolution and frame rate are cut")
                print("            back. Use a USB 3 data cable on a port that is not shared.")

            step(f"enumerate sensors for device {number}")
            print("      sensors")
            for sensor in device.sensors:
                try:
                    name = sensor.get_info(rs.camera_info.name)
                except Exception:
                    name = "?"
                print(f"        - {name}")
                if not args.profiles:
                    continue
                step(f"list profiles for {name}")
                for profile in sensor.get_stream_profiles():
                    try:
                        video = profile.as_video_stream_profile()
                        detail = f"{video.width()}x{video.height()} {video.format()} {video.fps()}fps"
                    except Exception:
                        detail = f"{profile.stream_type()} {profile.format()} {profile.fps()}fps"
                    print(f"            {detail}")
            print()

    except ProbeTimeout:
        # Never reached, the alarm handler exits the process. Kept so a future
        # refactor that raises instead of exits still behaves correctly.
        cancel_timeout()
        return 5
    except RuntimeError as exc:
        cancel_timeout()
        describe_failure(exc)
        return 3
    except Exception as exc:
        cancel_timeout()
        describe_failure(exc)
        return 3

    cancel_timeout()
    print(f"OK    finished at {time.strftime('%H:%M:%S')}")
    print(f"      SDK log at {args.log}, attach it when reporting a camera problem")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
