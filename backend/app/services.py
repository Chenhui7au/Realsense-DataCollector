"""Service container.

One object holds the long lived pieces so routes can take it as a dependency and
so the stores are built in one place with a defined order. ``bootstrap`` is the
startup sequence from docs/API.md section 8.2, minus the steps that belong to
sessions.
"""

from __future__ import annotations

import logging
from pathlib import Path

from .config import Config
from .device import DeviceProbe
from .fsbrowser import FsBrowser
from .guides import GuideStore
from .project import ProjectStore
from .settings import SettingsStore

log = logging.getLogger(__name__)


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

    # -------------------------------------------------------------- startup

    def bootstrap(self) -> None:
        """Run the startup self check. Order follows docs/API.md section 8.2.

        Steps one to four and step six are implemented here. Steps five and seven
        are inside :meth:`GuideStore.load`. The remaining step, recovering an
        interrupted session, needs the session store and is not built yet.
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
