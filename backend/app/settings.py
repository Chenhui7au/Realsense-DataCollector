"""Persistence of the operator's settings.

Today this holds one value, the project directory. It lives in the service
directory rather than the project directory, so copying a project folder away
carries only capture data. docs/API.md section 8.4 spells out that split.

Priority is settings file over YAML ``project.default_root``. A broken settings
file never stops the service, it falls back to the YAML default and logs a
warning. One bad file should not take the capture rig offline.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import threading
from pathlib import Path
from typing import Any, Dict, Optional

log = logging.getLogger(__name__)

SETTINGS_VERSION = 1


def now_iso() -> str:
    """Local time as ISO 8601 with an offset, the format used on the wire."""
    from datetime import datetime

    return datetime.now().astimezone().isoformat(timespec="seconds")


class SettingsStore:
    """Reads and writes ``settings.json`` with an atomic replace."""

    def __init__(self, path: Path, keep_backup: bool = True) -> None:
        self.path = Path(path)
        self.backup_path = self.path.with_name(self.path.name + ".bak")
        self.keep_backup = keep_backup
        # docs/API.md section 9.2 asks for a dedicated settings lock so a project
        # directory probe never waits behind the session state machine.
        self.lock = threading.RLock()
        self._data: Dict[str, Any] = {}

    # ---------------------------------------------------------------- load

    def load(self) -> Dict[str, Any]:
        """Read the file, tolerating absence and corruption."""
        with self.lock:
            self._data = self._read_from_disk()
            return dict(self._data)

    def _read_from_disk(self) -> Dict[str, Any]:
        if not self.path.is_file():
            return {}
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            log.warning("settings file %s is unreadable, falling back: %s", self.path, exc)
            return {}
        if not isinstance(raw, dict):
            log.warning("settings file %s is not a JSON object, ignoring it", self.path)
            return {}
        version = raw.get("version")
        if isinstance(version, int) and version > SETTINGS_VERSION:
            log.warning(
                "settings file %s has version %s, newer than this build understands",
                self.path,
                version,
            )
        return raw

    # --------------------------------------------------------------- write

    def save(self, data: Dict[str, Any]) -> None:
        """Write atomically. A crash mid write leaves the previous file intact."""
        with self.lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            payload = dict(data)
            payload["version"] = SETTINGS_VERSION
            payload["updated_at"] = now_iso()

            tmp_path = self.path.with_name(self.path.name + ".tmp")
            text = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
            try:
                with open(tmp_path, "w", encoding="utf-8") as handle:
                    handle.write(text)
                    handle.flush()
                    os.fsync(handle.fileno())
                if self.keep_backup and self.path.is_file():
                    shutil.copy2(self.path, self.backup_path)
                os.replace(tmp_path, self.path)
            except OSError:
                try:
                    tmp_path.unlink(missing_ok=True)
                except OSError:
                    pass
                raise
            self._data = payload

    # ------------------------------------------------------------ accessors

    @property
    def project_root(self) -> Optional[str]:
        value = self._data.get("project_root")
        if isinstance(value, str) and value.strip():
            return value.strip()
        return None

    def set_project_root(self, root: Optional[str]) -> None:
        with self.lock:
            data = dict(self._data)
            if root:
                data["project_root"] = root
            else:
                data.pop("project_root", None)
            self.save(data)
