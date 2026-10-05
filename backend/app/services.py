"""Service container.

One object holds the long lived pieces so routes can take it as a dependency and
so the stores are built in one place with a defined order. ``bootstrap`` is the
startup sequence from docs/API.md section 8.2, minus the steps that belong to
sessions.
"""

from __future__ import annotations

import logging
from pathlib import Path

from .camera import CameraWorker
from .capture import CaptureService
from .config import Config
from .device import DeviceProbe
from .errors import ApiError
from .fsbrowser import FsBrowser
from .guides import GuideStore
from .project import ProjectStore
from .sessions import SessionStore
from .settings import SettingsStore

log = logging.getLogger(__name__)


class _NoCameraBackend:
    """Backend used when ``camera.probe`` is false.

    The configuration promises that turning probing off stops the service
    touching the camera at all, so the worker is refused a real pipeline rather
    than relying on the routes to stay away. Without this the test suite and the
    frontend only configuration would still open the SDK whenever a session was
    started.
    """

    def __init__(self, serial: str = "") -> None:
        self.serial = serial

    def open(self, plan) -> None:  # noqa: ANN001 - matches the protocol
        raise ApiError(
            "DEVICE_NOT_FOUND",
            "Camera access is switched off in this configuration.",
            detail={"reason": "probe_disabled"},
        )

    def close(self) -> None:
        return None

    def wait(self, timeout_ms: int):  # noqa: ANN201
        return None

    def device_info(self) -> dict:
        return {}


class Services:
    def __init__(self, config: Config) -> None:
        self.config = config

        self.settings = SettingsStore(
            Path(config.settings_file), keep_backup=True
        )
        self.project = ProjectStore(config, self.settings)
        self.fsbrowser = FsBrowser(config, self.project)
        self.guides = GuideStore(config)
        self.device = DeviceProbe(config)
        self.sessions = SessionStore(config)
        self.camera = CameraWorker(
            config,
            backend_factory=None if config.camera_probe else _NoCameraBackend,
        )
        self.capture = CaptureService(
            config=config,
            sessions=self.sessions,
            project=self.project,
            guides=self.guides,
            device=self.device,
            camera=self.camera,
        )

    # -------------------------------------------------------------- startup

    def bootstrap(self) -> None:
        """Run the startup self check. Order follows docs/API.md section 8.2.

        Steps one to four are here. Steps five to seven are inside
        :meth:`GuideStore.load`. Step eight, recovering an interrupted session and
        starting the capture thread, is the last call so that a failure earlier in
        the sequence does not leave a thread owning the camera.
        """
        self.config.ensure_service_dirs()
        log.info("service directory %s", self.config.service_dir)
        log.info("guide directory   %s", self.config.guide_root)

        self.project.bootstrap()
        self.guides.load()

        # Probing once at startup puts the outcome in the log, which is a lot
        # easier to find than the home screen when the rig is on a bench.
        device = self.device.info(force=True)
        if device["connected"]:
            log.info(
                "camera ready: %s serial %s firmware %s on USB %s",
                device["name"],
                device["serial"],
                device["firmware"],
                device["usb_type"],
            )
        else:
            log.warning("no camera: %s", device["reason"])

        project = self.project.info()
        if project["configured"]:
            log.info("project directory %s, %.1f GB free", project["root"], project["free_gb"])
        else:
            log.warning("no project directory configured")

        readiness = self.guides.readiness()
        log.info(
            "guides %d of %d configured%s",
            readiness["uploaded"],
            readiness["total"],
            "" if readiness["ready"] else f", missing {readiness['missing_indices']}",
        )

        # Last, so an earlier failure does not leave a thread holding the camera.
        self.capture.bootstrap()

    def shutdown(self) -> None:
        """Release the device. Anything still recording is left on disk for the
        next startup to recover, per docs/API.md section 8.2."""
        self.capture.shutdown()
