"""Host directory browsing for the project folder picker.

This exists because a browser has no way to learn a path on the machine running
the service, and a native folder dialog would browse the machine running the
browser instead. docs/API.md section 4.5 makes that argument in full.

The interface is read only apart from :meth:`FsBrowser.mkdir`. Listing a
directory must never create it. Creation happens only here and in
``PUT /api/project``.
"""

from __future__ import annotations

import logging
import os
import re
import stat
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import paths
from .config import Config
from .errors import ApiError
from .project import ProjectStore

log = logging.getLogger(__name__)

SESSION_MARKER = "session.json"
_DIGITS = re.compile(r"(\d+)")


def natural_key(name: str):
    """Sort key that puts folder 2 before folder 10."""
    return [int(part) if part.isdigit() else part.lower() for part in _DIGITS.split(name)]


class FsBrowser:
    """Lists and creates directories inside the configured allowed range."""

    def __init__(self, config: Config, project: ProjectStore) -> None:
        self.config = config
        self.project = project

    # -------------------------------------------------------------- range

    def allowed_roots(self) -> List[str]:
        """Configured roots plus the project directory currently in use.

        The shipped Windows config lists the drives capture data may live on, so
        this is exactly the configured range. Adding every existing drive
        automatically was rejected: it made the picker offer folders that
        ``PUT /api/project`` then refused, because that check only ever used
        ``fs.allow_roots``. docs/API.md section 4.5 promises the two use the same
        range.
        """
        roots: List[str] = []
        for root in self.config.allow_roots + self.project.allowed_extra_roots():
            normalized = paths.normalize(root)
            if normalized and normalized not in roots:
                roots.append(normalized)
        return roots

    def is_allowed(self, path: str) -> bool:
        return paths.is_within_any(path, self.allowed_roots())

    def _require_allowed(self, path: str) -> None:
        if not self.is_allowed(path):
            raise ApiError(
                "PATH_NOT_ALLOWED",
                "That path is outside the range this service may browse.",
                detail={"path": path, "allow_roots": self.allowed_roots()},
            )

    # ----------------------------------------------------------- shortcuts

    def volume_roots(self) -> List[str]:
        """Allowed roots that are whole volumes present on the host, in config order.

        These are what the picker offers as jump points. An allowed root that is a
        folder rather than a volume, such as a home directory, is not one: the
        operator can reach it by descending from its volume, and listing it as a
        peer of ``C:\\`` suggests it sits outside the volume it is on.

        A configured volume that is not currently mounted is left out. ``allow_roots``
        is a standing permission, so it may name a removable drive that is away, and
        offering a jump point that always fails is worse than not offering it.
        """
        return [
            root
            for root in self.allowed_roots()
            if paths.is_root(root) and os.path.isdir(root)
        ]

    def _shortcuts(self) -> List[Dict[str, str]]:
        """Jump points for the picker sidebar. The service decides these.

        Only the volumes capture data may live on. Person-centric folders such as
        Home, Desktop and Downloads were removed: this picker exists to choose
        where recordings go, so offering the operator's desktop is noise, and it
        made the list long enough that the drives were lost among it.

        Frontend hardcoding was rejected because the set depends on the host. A
        deployment without ``D:\\`` should not offer it.
        """
        return [
            {"name": self._volume_label(root), "path": root} for root in self.volume_roots()
        ]

    @staticmethod
    def _volume_label(root: str) -> str:
        """Sidebar label for a volume root: ``C:`` on Windows, ``/`` elsewhere."""
        stripped = root.rstrip("\\/")
        if not stripped:
            return root
        if len(stripped) == 2 and stripped[1] == ":":
            return stripped.upper()
        return stripped

    def preferred_root(self) -> str:
        """Where the picker opens when the caller has no preference.

        The configured project directory when there is one. Otherwise the first
        allowed volume that is not the system volume, because capture data belongs
        on a data volume. Falling back to the first allowed volume and then to the
        service home covers a range made up of folders only, which is what a
        deployment that never listed a drive would have.
        """
        if self.project.root:
            return paths.normalize(self.project.root)
        volumes = self.volume_roots()
        system = self._system_volume()
        for root in volumes:
            if root != system:
                return root
        if volumes:
            return volumes[0]
        return self.home()

    @staticmethod
    def _system_volume() -> str:
        r"""The volume the operating system is installed on, ``C:\`` on Windows.

        Empty elsewhere, which makes the "not the system volume" preference fall
        through to the first allowed volume.
        """
        drive = os.environ.get("SystemDrive") or os.environ.get("SystemRoot", "")[:2]
        if not drive:
            return ""
        return paths.normalize(f"{drive}\\")

    @staticmethod
    def home() -> str:
        return paths.normalize(os.path.expanduser("~"))

    # ----------------------------------------------------------------- list

    def list_directory(self, raw_path: Optional[str], show_hidden: bool = False) -> Dict[str, Any]:
        """List subdirectories of ``raw_path``, defaulting to the preferred root.

        The default is docs/API.md section 4.5: the project directory when one is
        configured, otherwise the data volume the picker should open on. It used to
        be the service user's home directory, which put the operator somewhere
        recordings must never be written.
        """
        if raw_path is None or not str(raw_path).strip():
            target = self.preferred_root()
        else:
            target = paths.expand(str(raw_path).strip())

        if paths.has_parent_reference(target):
            # Resolution happens in expand, but a trailing .. would be gone by
            # now. This guard is for the odd absolute path that survives.
            target = paths.expand(target)

        self._require_allowed(target)

        if not os.path.isdir(target):
            raise ApiError(
                "DIR_NOT_FOUND",
                "That folder does not exist on the host.",
                detail={"path": target},
            )

        try:
            scandir_entries = list(os.scandir(target))
        except PermissionError as exc:
            raise ApiError(
                "DIR_NOT_READABLE",
                "That folder exists but the service may not read it.",
                detail={"path": target},
            ) from exc
        except OSError as exc:
            raise ApiError(
                "DIR_NOT_READABLE",
                f"Could not read that folder: {exc}",
                detail={"path": target},
            ) from exc

        entries: List[Dict[str, Any]] = []
        for entry in scandir_entries:
            name = entry.name
            if not show_hidden and name.startswith("."):
                continue
            try:
                # Symlinks are reported but never followed, so a link cycle
                # cannot make the picker spin forever.
                is_symlink = entry.is_symlink()
                if is_symlink:
                    if not entry.is_dir(follow_symlinks=True):
                        continue
                elif not entry.is_dir(follow_symlinks=False):
                    continue
            except OSError:
                continue

            child = paths.normalize(os.path.join(target, name))
            if not self.is_allowed(child):
                continue

            entries.append(
                {
                    "name": name,
                    "path": child,
                    "writable": os.access(child, os.W_OK),
                    "is_symlink": is_symlink,
                    "looks_like_session": self._looks_like_session(child),
                }
            )

        entries.sort(key=lambda item: natural_key(item["name"]))
        return self.listing(target, entries)

    def listing(
        self, target: str, entries: Optional[List[Dict[str, Any]]] = None
    ) -> Dict[str, Any]:
        """Build the catalogue object for one directory, without a range check.

        Used by ``mkdir`` to return the parent listing so the frontend does not
        have to re-request it, and by :meth:`list_directory` itself.
        """
        if entries is None:
            return self.list_directory(target)

        writable = self._is_writable(target)
        free_gb = paths.format_gb(paths.free_bytes(target))
        enough = free_gb >= self.config.min_free_space_gb

        parent = paths.parent_of(target)
        if parent is None or not self.is_allowed(parent):
            # Reaching the edge of the allowed range reads exactly like reaching
            # the filesystem root, so the Up button disables the same way.
            parent = None

        return {
            "path": target,
            "parent": parent,
            "name": "/" if paths.normalize(target) == "/" else os.path.basename(target.rstrip("/")) or target,
            "home": self.home(),
            "shortcuts": self._shortcuts(),
            "readable": True,
            "writable": writable,
            "free_gb": free_gb,
            "enough": enough,
            "error": self._unusable_reason(target, writable, enough),
            "entries": entries,
        }

    # ---------------------------------------------------------------- mkdir

    def make_directory(self, parent: str, name: str) -> Dict[str, Any]:
        """Create one subdirectory. Not recursive, matching the picker's button."""
        if not paths.valid_name(name):
            raise ApiError(
                "DIR_NAME_INVALID",
                paths.name_error(name),
                detail={"name": name},
            )

        target_parent = paths.expand(parent) if parent and parent.strip() else self.home()
        self._require_allowed(target_parent)

        if not os.path.isdir(target_parent):
            raise ApiError(
                "DIR_NOT_FOUND",
                "That parent folder does not exist on the host.",
                detail={"parent": target_parent},
            )
        if not self._is_writable(target_parent):
            raise ApiError(
                "DIR_NOT_WRITABLE",
                "The parent folder is not writable by the capture service.",
                detail={"parent": target_parent},
            )

        child = paths.normalize(os.path.join(target_parent, name))
        if os.path.exists(child):
            raise ApiError(
                "DIR_EXISTS",
                "A folder with that name already exists here.",
                detail={"path": child},
            )

        try:
            os.mkdir(child)
        except FileExistsError as exc:
            raise ApiError("DIR_EXISTS", detail={"path": child}) from exc
        except OSError as exc:
            raise ApiError(
                "DIR_NOT_WRITABLE",
                f"Could not create that folder: {exc}",
                detail={"path": child},
            ) from exc

        log.info("created directory %s", child)
        return {"path": child, "listing": self.list_directory(target_parent)}

    # -------------------------------------------------------------- helpers

    def _unusable_reason(self, target: str, writable: bool, enough: bool) -> Optional[str]:
        """Why this directory cannot serve as the project folder, or None.

        The frontend shows this verbatim so the picker does not have to grow its
        own copy of the rules, and so the "use this folder" button can disable
        itself for a reason the operator can read.
        """
        if paths.is_root(target):
            return "This is the root of a volume. Pick a folder inside it."
        if not writable:
            return "That folder is not writable by the capture service."
        if not enough:
            return f"Only {paths.format_gb(paths.free_bytes(target)):.1f} GB free. " \
                   f"At least {self.config.min_free_space_gb:g} GB is needed."
        return None

    @staticmethod
    def _is_writable(path: str) -> bool:
        try:
            mode = os.stat(path)
        except OSError:
            return False
        if mode.st_mode & stat.S_IWUSR or mode.st_mode & stat.S_IWGRP or mode.st_mode & stat.S_IWOTH:
            return os.access(path, os.W_OK)
        return False

    @staticmethod
    def _looks_like_session(directory: str) -> bool:
        """True when the directory already holds a session.json.

        Computed here rather than by the frontend, which would otherwise need one
        request per child directory to warn "this folder already has rounds in it".
        """
        try:
            return os.path.isfile(os.path.join(directory, SESSION_MARKER))
        except OSError:
            return False


def parent_listing(browser: FsBrowser, directory: str) -> Dict[str, Any]:
    """Helper kept for readability at call sites that only have a child path."""
    parent = paths.parent_of(directory)
    return browser.list_directory(parent if parent else directory)


__all__ = ["FsBrowser", "natural_key", "parent_listing"]
