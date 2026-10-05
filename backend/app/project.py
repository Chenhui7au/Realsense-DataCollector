"""Project directory state, validation and probing.

The project directory is where capture data lands. It is separate from the
service directory, which holds the guides, logs and settings. docs/API.md
section 9.1 keeps this store and the (not yet written) session store apart. This
one answers whether the project directory is usable at all, the other manages
what sits inside it.

The validation order in :meth:`ProjectStore.set_root` is load bearing and
matches docs/API.md section 4.4. Everything that does not touch the disk happens
first, so a rejected path never leaves a stray directory behind.
"""

from __future__ import annotations

import logging
import os
import threading
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import paths
from .config import Config
from .errors import ApiError
from .settings import SettingsStore

log = logging.getLogger(__name__)


class ProjectStore:
    """Owns the configured project directory and reports on its health."""

    def __init__(self, config: Config, settings: SettingsStore) -> None:
        self.config = config
        self.settings = settings
        self.lock = settings.lock
        # Resolved once at startup. Kept in memory so a path that only differs by
        # a trailing separator is not repeatedly re-resolved on every request.
        self._root: Optional[str] = None

    # ------------------------------------------------------- startup wiring

    def bootstrap(self) -> None:
        """Pick the starting root and make sure the service can use it.

        Settings file wins over the YAML default. A missing directory is created.
        Failure is recorded rather than raised, because docs/API.md section 8.2
        wants the service to boot anyway and report the problem on the home
        screen for the operator to fix.
        """
        with self.lock:
            self.settings.load()
            root = self.settings.project_root or self.config.default_root
            if root:
                root = paths.expand(root)
                try:
                    self._ensure_directory(root)
                    self._root = root
                    self._warn_if_unsuitable(root)
                except ApiError as exc:
                    log.warning("configured project directory %s is unusable: %s", root, exc.message)
                    self._root = root
            else:
                self._root = None
                log.info("no project directory configured, the home screen will ask for one")

    def _warn_if_unsuitable(self, root: str) -> None:
        if not os.access(root, os.W_OK):
            log.warning("project directory %s is not writable", root)
        free = paths.format_gb(paths.free_bytes(root))
        if free < self.config.min_free_space_gb:
            log.warning(
                "project directory %s has only %.1f GB free, %s GB is required",
                root,
                free,
                self.config.min_free_space_gb,
            )

    # ------------------------------------------------------------ accessors

    @property
    def root(self) -> Optional[str]:
        return self._root

    def allowed_extra_roots(self) -> List[str]:
        """The configured root always counts as in range.

        docs/API.md section 4.5 asks for this so tightening ``fs.allow_roots``
        never locks the operator out of the folder already in use.
        """
        return [self._root] if self._root else []

    def usable_root(self) -> str:
        """Return the root, or raise the code that says why it cannot be used."""
        info = self.info()
        if not info["configured"]:
            raise ApiError("PROJECT_NOT_CONFIGURED")
        if not info["exists"]:
            raise ApiError("PROJECT_PATH_INVALID", "The project folder does not exist.")
        if not info["writable"]:
            raise ApiError("PROJECT_PATH_NOT_WRITABLE")
        if not info["enough"]:
            raise ApiError(
                "DISK_SPACE_LOW",
                f"Only {info['free_gb']:.1f} GB free on the project volume.",
            )
        assert self._root is not None
        return self._root

    def info(self) -> Dict[str, Any]:
        """Snapshot used by ``GET /api/project`` and the health check."""
        with self.lock:
            root = self._root

        if not root:
            return {
                "configured": False,
                "root": None,
                "name": None,
                "exists": False,
                "writable": False,
                "free_gb": 0.0,
                "enough": False,
                "error": "No project folder is set. Recordings have nowhere to go.",
            }

        exists = os.path.isdir(root)
        writable = exists and os.access(root, os.W_OK)
        free_gb = paths.format_gb(paths.free_bytes(root)) if exists else 0.0
        enough = free_gb >= self.config.min_free_space_gb

        error: Optional[str] = None
        if not exists:
            error = "That folder does not exist on the host."
        elif not writable:
            error = "That folder is not writable by the capture service."
        elif not enough:
            error = f"Only {free_gb:.1f} GB free. At least {self.config.min_free_space_gb:g} GB is needed."

        return {
            "configured": True,
            "root": root,
            "name": os.path.basename(root.rstrip("/")) or root,
            "exists": exists,
            "writable": writable,
            "free_gb": free_gb,
            "enough": enough,
            "error": error,
        }

    # ---------------------------------------------------------------- write

    def set_root(self, raw: str) -> Dict[str, Any]:
        """Validate and persist a new project directory.

        Order follows docs/API.md section 4.4. A failure at any step leaves the
        previous value untouched, so a mistyped path cannot break a working setup.
        """
        if not isinstance(raw, str) or not raw.strip():
            raise ApiError("PROJECT_PATH_INVALID", "A project folder path is required.")

        # 1. Structural checks. Nothing here touches the disk.
        candidate = raw.strip()
        if not paths.is_absolute_like(candidate):
            raise ApiError(
                "PROJECT_PATH_INVALID",
                "Use an absolute path, or one starting with ~.",
                detail={"root": candidate},
            )
        if paths.has_parent_reference(candidate):
            raise ApiError(
                "PROJECT_PATH_INVALID",
                "The path must not contain ..",
                detail={"root": candidate},
            )
        if len(candidate) > paths.MAX_PATH_LENGTH:
            raise ApiError("PROJECT_PATH_INVALID", "That path is too long.")

        # 2. Expand and normalise.
        resolved = paths.expand(candidate)

        if paths.is_root(resolved):
            raise ApiError(
                "PROJECT_PATH_INVALID",
                "Pick a folder inside the volume, not the root of it.",
                detail={"root": resolved},
            )

        with self.lock:
            # 3. Range check, using the same configuration as the folder picker.
            allowed = self.config.allow_roots + self.allowed_extra_roots()
            if not paths.is_within_any(resolved, allowed):
                raise ApiError(
                    "PATH_NOT_ALLOWED",
                    "That path is outside the range this service may write to.",
                    detail={"root": resolved, "allow_roots": allowed},
                )

            # 4. Create it, including missing parents.
            self._ensure_directory(resolved)

            # 5. Prove writability by actually writing, not by trusting a mode bit.
            self._probe_writable(resolved)

            # 6. Free space on that volume.
            free_gb = paths.format_gb(paths.free_bytes(resolved))
            if free_gb < self.config.min_free_space_gb:
                raise ApiError(
                    "DISK_SPACE_LOW",
                    f"Only {free_gb:.1f} GB free. At least "
                    f"{self.config.min_free_space_gb:g} GB is needed.",
                    detail={"free_gb": free_gb, "required_gb": self.config.min_free_space_gb},
                )

            # 7. Persist, then adopt. If the write fails the old value stands.
            previous = self._root
            try:
                self.settings.set_project_root(resolved)
            except OSError as exc:
                self._root = previous
                raise ApiError(
                    "PROJECT_PATH_NOT_WRITABLE",
                    f"The service could not save its settings file: {exc}",
                ) from exc
            self._root = resolved

        log.info("project directory set to %s", resolved)
        return self.info()

    # -------------------------------------------------------------- helpers

    def _ensure_directory(self, path: str) -> None:
        target = Path(path)
        if target.is_dir():
            return
        if target.exists() and not target.is_dir():
            raise ApiError(
                "PROJECT_PATH_INVALID",
                "That path exists but is a file, not a folder.",
                detail={"root": path},
            )
        try:
            target.mkdir(parents=True, exist_ok=True)
        except PermissionError as exc:
            raise ApiError(
                "PROJECT_PATH_NOT_WRITABLE",
                f"The service may not create {path}.",
                detail={"root": path},
            ) from exc
        except OSError as exc:
            raise ApiError(
                "PROJECT_PATH_NOT_WRITABLE",
                f"Could not create {path}: {exc}",
                detail={"root": path},
            ) from exc

    def _probe_writable(self, path: str) -> None:
        probe = os.path.join(path, f".write_probe_{uuid.uuid4().hex[:8]}")
        try:
            with open(probe, "w", encoding="utf-8") as handle:
                handle.write("probe")
        except OSError as exc:
            raise ApiError(
                "PROJECT_PATH_NOT_WRITABLE",
                "That folder is not writable by the capture service.",
                detail={"root": path, "reason": str(exc)},
            ) from exc
        finally:
            try:
                os.unlink(probe)
            except OSError:
                pass
