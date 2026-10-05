"""Stage diagram storage.

Diagrams are a global asset. They belong to no round and are read only when a
guide screen is shown, so this store is completely independent of sessions.
docs/API.md section 5 says so at the top, and nothing here may touch a project
directory.

Four decisions worth naming.

* The manifest is the single index. Files are never discovered by globbing at
  request time, only at startup to recover from a hand copied folder.
* Writes go to a temporary file then rename, so a kill mid write cannot leave a
  half written manifest. The previous manifest is kept as ``.bak``.
* Originals are never overwritten by a derived file. The downscaled copy lives
  beside it with ``.display.`` in the name.
* On replace, a stale file with the other extension is removed, otherwise a PNG
  replaced by a JPEG would leave the PNG reachable through ``size=original``.
* The per-stage description lives in the same manifest but outside the ``stages``
  map, so removing a diagram does not throw away the text that goes with it.
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import os
import re
import shutil
import threading
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from PIL import Image, UnidentifiedImageError

from . import paths
from .config import Config
from .errors import ApiError

log = logging.getLogger(__name__)

MANIFEST_VERSION = 1
MANIFEST_NAME = "manifest.json"
DISPLAY_INFIX = ".display."
# Top level manifest key holding the operator written descriptions, as a map of
# stage index to text. Deliberately a sibling of "stages" and not a field inside
# an entry: the text outlives the image, so deleting a diagram keeps it.
INSTRUCTIONS_KEY = "instructions"
# Quality for the guide screen copy. Not in the YAML because it is not something
# an operator needs to tune, it only trades bytes for smoothness on a projector.
DISPLAY_JPEG_QUALITY = 85

# Pillow format name -> MIME type. Only these may be stored. A file whose
# declared type is on the whitelist but whose real format is not gets rejected,
# so the manifest never claims a content type the bytes do not have.
#
# PNG is the expected format. The rest are the common ones a browser can render
# back to the operator; TIFF and SVG are absent on purpose, the first does not
# display in browsers and the second cannot be verified by decoding it.
FORMAT_TO_MIME = {
    "PNG": "image/png",
    "JPEG": "image/jpeg",
    "WEBP": "image/webp",
    "BMP": "image/bmp",
    "GIF": "image/gif",
}
MIME_TO_EXTENSION = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
    "image/bmp": ".bmp",
    "image/gif": ".gif",
}
# Every extension an original may carry. Used to sweep the other one on replace.
ALL_EXTENSIONS = tuple(MIME_TO_EXTENSION.values())

_STAGE_FILE_RE = re.compile(
    r"^stage_(\d{1,2})\.(png|jpe?g|webp|bmp|gif)$", re.IGNORECASE
)


class GuideStore:
    """Manifest backed storage for the eight stage diagrams."""

    def __init__(self, config: Config) -> None:
        self.config = config
        self.root = Path(config.guide_root)
        self.manifest_path = self.root / MANIFEST_NAME
        self.backup_path = self.root / (MANIFEST_NAME + ".bak")
        # docs/API.md section 9.2: its own lock, so uploading a large image never
        # blocks the capture thread.
        self.lock = threading.RLock()
        # index -> manifest entry. The only in-memory source of truth.
        self._entries: Dict[int, Dict[str, Any]] = {}
        # index -> operator written description. Absent means "use the YAML".
        self._instructions: Dict[int, str] = {}

    # --------------------------------------------------------------- startup

    def load(self) -> None:
        """Read the manifest and reconcile it with what is actually on disk."""
        with self.lock:
            self.root.mkdir(parents=True, exist_ok=True)
            raw, from_disk = self._read_manifest()
            entries = self._parse_entries(raw)
            instructions = self._parse_instructions(raw)
            dirty = from_disk is False

            # Branch two: an entry whose file vanished. Drop it and say so,
            # rather than serving a broken image URL.
            kept: Dict[int, Dict[str, Any]] = {}
            for index, entry in entries.items():
                if self._entry_files_present(entry):
                    kept[index] = entry
                else:
                    log.warning(
                        "guide %d points at %s which is missing, marking it unconfigured",
                        index,
                        entry.get("file"),
                    )
                    dirty = True
            entries = kept

            # Branch three: files exist with no manifest entry, for example after
            # someone copied a prepared set of diagrams into the directory.
            if self.config.guides_rebuild_from_dir:
                rebuilt = self._rebuild_from_directory(entries)
                if rebuilt:
                    log.info("rebuilt %d guide manifest entries from files on disk", rebuilt)
                    dirty = True

            self._entries = entries
            self._instructions = instructions
            if dirty:
                self._write_manifest()

    def _read_manifest(self) -> Tuple[Dict[str, Any], bool]:
        if not self.manifest_path.is_file():
            if self.config.guides_rebuild_from_dir:
                log.info("no guide manifest at %s, will look for files to rebuild from", self.manifest_path)
            return {}, False
        try:
            raw = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            log.warning("guide manifest %s is unreadable, treating it as empty: %s", self.manifest_path, exc)
            return {}, False
        if not isinstance(raw, dict):
            log.warning("guide manifest %s is not a JSON object", self.manifest_path)
            return {}, False

        # Branch four: an older schema. v1 is the first version, so migration is
        # only a version stamp today. The hook exists so the next schema change
        # does not need a new branch discovered at runtime.
        version = raw.get("version")
        if not isinstance(version, int) or version < MANIFEST_VERSION:
            log.info("migrating guide manifest from version %s to %s", version, MANIFEST_VERSION)
            raw = dict(raw)
            raw["version"] = MANIFEST_VERSION
        return raw, True

    @staticmethod
    def _parse_entries(raw: Dict[str, Any]) -> Dict[int, Dict[str, Any]]:
        stages = raw.get("stages")
        if not isinstance(stages, dict):
            return {}
        entries: Dict[int, Dict[str, Any]] = {}
        for key, value in stages.items():
            if not isinstance(value, dict):
                continue
            try:
                index = int(key)
            except (TypeError, ValueError):
                continue
            if index < 1:
                continue
            entries[index] = value
        return entries

    def _parse_instructions(self, raw: Dict[str, Any]) -> Dict[int, str]:
        """Read the description map, discarding anything that is not a string.

        A manifest written before descriptions existed simply has no key, which
        is why the field needs no migration: an empty map means every stage falls
        back to the instructions in the YAML.
        """
        stored = raw.get(INSTRUCTIONS_KEY)
        if not isinstance(stored, dict):
            return {}
        instructions: Dict[int, str] = {}
        for key, value in stored.items():
            try:
                index = int(key)
            except (TypeError, ValueError):
                continue
            if not self.config.has_stage(index) or not isinstance(value, str):
                continue
            text = value.strip()
            if text:
                instructions[index] = text
        return instructions
    def _entry_files_present(self, entry: Dict[str, Any]) -> bool:
        original = entry.get("file")
        if not isinstance(original, str) or not original:
            return False
        if not (self.root / original).is_file():
            return False
        display = entry.get("display_file")
        if isinstance(display, str) and display and not (self.root / display).is_file():
            # The original is what matters. A missing copy is regenerated lazily.
            log.info("display copy %s is missing, it will be regenerated", display)
            return True
        return True

    def _rebuild_from_directory(self, existing: Dict[int, Dict[str, Any]]) -> int:
        """Fill manifest gaps from files already in the guide directory."""
        try:
            names = sorted(os.listdir(self.root))
        except OSError:
            return 0

        rebuilt = 0
        for name in names:
            match = _STAGE_FILE_RE.match(name)
            if not match:
                continue
            index = int(match.group(1))
            if not self.config.has_stage(index) or index in existing:
                continue
            candidate = self.root / name
            try:
                entry = self._describe_file(candidate, original_filename=name)
            except (OSError, UnidentifiedImageError) as exc:
                log.warning("cannot rebuild guide %d from %s: %s", index, name, exc)
                continue
            existing[index] = entry
            rebuilt += 1
        return rebuilt

    def _describe_file(self, path: Path, original_filename: str) -> Dict[str, Any]:
        """Build a manifest entry by inspecting a file on disk."""
        data = path.read_bytes()
        with Image.open(io.BytesIO(data)) as image:
            image.load()
            pillow_format = (image.format or "").upper()
            width, height = image.size
        mime = FORMAT_TO_MIME.get(pillow_format)
        if mime is None:
            raise UnidentifiedImageError(f"{path.name} is {pillow_format or 'unknown'}")

        display_name = None
        if width > self.config.guide_display_max_width:
            display_name = self._write_display_copy(path, mime)

        return {
            "file": path.name,
            "display_file": display_name,
            "original_filename": original_filename,
            "content_type": mime,
            "size_bytes": len(data),
            "width": width,
            "height": height,
            "sha256": hashlib.sha256(data).hexdigest(),
            "uploaded_at": self._now(),
        }

    # ------------------------------------------------------------ read model

    def readiness(self) -> Dict[str, Any]:
        """Whole catalogue plus the ready verdict, per docs/API.md section 5.1."""
        with self.lock:
            guides: List[Dict[str, Any]] = []
            missing: List[int] = []
            for index in self.config.stage_indices():
                entry = self._guide_object(index)
                guides.append(entry)
                if not entry["configured"]:
                    missing.append(index)

            total = self.config.total_stages
            uploaded = total - len(missing)
            return {
                "required": self.config.guides_required,
                "ready": len(missing) == 0,
                "total": total,
                "uploaded": uploaded,
                "missing_indices": missing,
                "guides": guides,
            }

    def _guide_object(self, index: int) -> Dict[str, Any]:
        """One guide entry. Unconfigured stages carry nulls, not empty strings."""
        stage = self.config.stage_config(index)
        name = stage["name"] if stage else str(index)
        default_instructions = stage["instructions"] if stage else ""
        entry = self._entries.get(index)
        override = self._instructions.get(index)
        instructions = override if override else default_instructions
        if entry is None:
            return {
                "index": index,
                "name": name,
                "configured": False,
                "image_url": None,
                "original_filename": None,
                "content_type": None,
                "size_bytes": None,
                "width": None,
                "height": None,
                "uploaded_at": None,
                "sha256": None,
                "instructions": instructions,
                "instructions_custom": override is not None,
            }
        return {
            "index": index,
            "name": name,
            "configured": True,
            "image_url": f"/api/guides/{index}/image",
            "original_filename": entry.get("original_filename"),
            "content_type": entry.get("content_type"),
            "size_bytes": entry.get("size_bytes"),
            "width": entry.get("width"),
            "height": entry.get("height"),
            "uploaded_at": entry.get("uploaded_at"),
            "sha256": entry.get("sha256"),
            "instructions": instructions,
            "instructions_custom": override is not None,
        }

    def set_instructions(self, index: int, text: Optional[str]) -> Dict[str, Any]:
        """Store or clear the description for one stage.

        An empty value clears the override instead of storing an empty string, so
        the stage falls back to the YAML instructions again. That doubles as the
        reset action, which is why there is no separate endpoint for it.

        docs/API.md section 5.6. Nothing here touches a project directory: this is
        the same service side store as the diagrams.
        """
        if not self.config.has_stage(index):
            raise ApiError(
                "STAGE_NOT_FOUND",
                detail={"stage_index": index, "total_stages": self.config.total_stages},
            )

        cleaned = (text or "").strip()
        limit = self.config.guide_instructions_max_length
        if len(cleaned) > limit:
            raise ApiError(
                "GUIDE_TEXT_TOO_LONG",
                f"Keep the description to {limit} characters or fewer.",
                detail={"index": index, "length": len(cleaned), "max_length": limit},
            )

        with self.lock:
            if cleaned:
                self._instructions[index] = cleaned
            else:
                self._instructions.pop(index, None)
            self._write_manifest()
            result = self._guide_object(index)

        log.info(
            "guide %d description %s", index, "set" if cleaned else "cleared"
        )
        return result

    def effective_instructions(self, index: int) -> str:
        """Text a guide screen should show. Override when set, YAML otherwise."""
        with self.lock:
            override = self._instructions.get(index)
            if override:
                return override
            stage = self.config.stage_config(index)
            return stage["instructions"] if stage else ""

    def file_for(self, index: int, size: str = "display") -> Tuple[Path, str, str]:
        """Resolve the file to serve. Returns path, content type and sha256."""
        with self.lock:
            entry = self._entries.get(index)
            if entry is None:
                raise ApiError("GUIDE_NOT_FOUND", detail={"index": index})

            original_name = str(entry.get("file") or "")
            original_type = str(entry.get("content_type") or "application/octet-stream")
            sha = str(entry.get("sha256") or "")
            original = self.root / original_name

            if size == "display":
                display_name = entry.get("display_file")
                if isinstance(display_name, str) and display_name:
                    display = self.root / display_name
                    if display.is_file():
                        return display, "image/jpeg", sha
                # Fall back to the original when no copy was generated, so a small
                # PNG still renders on the guide screen.
            if not original.is_file():
                raise ApiError("GUIDE_NOT_FOUND", detail={"index": index})
            return original, original_type, sha

    # ---------------------------------------------------------------- upload

    def upload(
        self,
        index: int,
        filename: Optional[str],
        content_type: Optional[str],
        data: bytes,
    ) -> Dict[str, Any]:
        """Validate and store one diagram. Replace semantics for a stage already set.

        Validation order is docs/API.md section 5.2. Nothing is written until all
        four checks have passed, so a rejected upload leaves the previous diagram
        in place.
        """
        # 1. Index in range.
        if not self.config.has_stage(index):
            raise ApiError(
                "STAGE_NOT_FOUND",
                detail={"stage_index": index, "total_stages": self.config.total_stages},
            )

        # 2. Declared content type on the whitelist.
        declared = (content_type or "").split(";")[0].strip().lower()
        if declared not in self.config.guide_allowed_types:
            raise ApiError(
                "GUIDE_UNSUPPORTED_TYPE",
                f"Only {', '.join(self.config.guide_allowed_types)} are accepted.",
                detail={"index": index, "content_type": declared or None},
            )

        # 3. Size within the limit.
        if len(data) > self.config.guide_max_size_bytes:
            raise ApiError(
                "GUIDE_TOO_LARGE",
                f"That file is over the {self.config.guide_max_size_bytes // (1024 * 1024)} MB limit.",
                detail={
                    "index": index,
                    "size_bytes": len(data),
                    "max_size_bytes": self.config.guide_max_size_bytes,
                },
            )
        if not data:
            raise ApiError("GUIDE_INVALID_IMAGE", detail={"index": index})

        # 4. Decodable. The real format decides the stored file name and content
        # type, so a GIF renamed to .png does not sneak in under a PNG label.
        try:
            with Image.open(io.BytesIO(data)) as image:
                image.load()
                pillow_format = (image.format or "").upper()
                width, height = image.size
        except (UnidentifiedImageError, OSError, ValueError):
            raise ApiError(
                "GUIDE_INVALID_IMAGE",
                "That file cannot be decoded as an image.",
                detail={"index": index},
            )

        mime = FORMAT_TO_MIME.get(pillow_format)
        if mime is None or mime not in self.config.guide_allowed_types:
            raise ApiError(
                "GUIDE_UNSUPPORTED_TYPE",
                f"The file is a {pillow_format or 'unknown'} image, which is not accepted.",
                detail={"index": index},
            )

        extension = MIME_TO_EXTENSION[mime]
        target = self.root / f"stage_{index:02d}{extension}"
        display_name: Optional[str] = None

        with self.lock:
            self.root.mkdir(parents=True, exist_ok=True)

            previous = self._entries.get(index) or {}
            previous_file = previous.get("file")
            previous_display = previous.get("display_file")

            self._atomic_write_bytes(target, data)

            if width > self.config.guide_display_max_width:
                display_name = self._write_display_copy(target, mime)
            else:
                # Width is fine, drop any copy left over from a wider original.
                stale = self.root / f"stage_{index:02d}{DISPLAY_INFIX}jpg"
                self._unlink_quietly(stale)

            # Remove the other extensions' originals if this replace changed type.
            for other_ext in ALL_EXTENSIONS:
                if other_ext == extension:
                    continue
                self._unlink_quietly(self.root / f"stage_{index:02d}{other_ext}")
            if previous_file and previous_file != target.name:
                self._unlink_quietly(self.root / str(previous_file))
            if previous_display and previous_display != display_name:
                self._unlink_quietly(self.root / str(previous_display))

            self._entries[index] = {
                "file": target.name,
                "display_file": display_name,
                "original_filename": filename or target.name,
                "content_type": mime,
                "size_bytes": len(data),
                "width": width,
                "height": height,
                "sha256": hashlib.sha256(data).hexdigest(),
                "uploaded_at": self._now(),
            }
            self._write_manifest()
            readiness = self.readiness()

        result = self._guide_object(index)
        result["generated_preview"] = display_name is not None
        result["ready"] = readiness["ready"]
        # Progress rides along so the upload screen can refresh its bar without a
        # second request, per docs/API.md section 5.2.
        result["uploaded"] = readiness["uploaded"]
        result["missing_indices"] = readiness["missing_indices"]
        log.info("guide %d stored as %s (%dx%d)", index, target.name, width, height)
        return result

    def upload_batch(self, items: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Apply several uploads, each independent, no rollback.

        docs/API.md section 5.3 is explicit that a failure in one part does not
        undo another. The single upload path validates everything before writing
        because there is only one file, which has no partial concept. This one is
        a convenience call, not a transaction.
        """
        applied: List[int] = []
        failed: List[Dict[str, Any]] = []

        for item in items:
            index = item["index"]
            try:
                self.upload(index, item.get("filename"), item.get("content_type"), item["data"])
                applied.append(index)
            except ApiError as exc:
                failed.append({"index": index, "code": exc.code, "message": exc.message})
            except Exception as exc:  # pragma: no cover - defensive
                log.exception("unexpected failure storing guide %d", index)
                failed.append(
                    {"index": index, "code": "GUIDE_INVALID_IMAGE", "message": str(exc)}
                )

        return {
            "applied": sorted(applied),
            "failed": failed,
            "guides": self.readiness(),
        }

    def delete(self, index: int) -> Dict[str, Any]:
        """Remove a diagram and its display copy. Returns the refreshed counters."""
        if not self.config.has_stage(index):
            raise ApiError("STAGE_NOT_FOUND", detail={"stage_index": index})

        with self.lock:
            entry = self._entries.pop(index, None)
            if entry is None:
                raise ApiError("GUIDE_NOT_FOUND", detail={"index": index})

            self._unlink_quietly(self.root / str(entry.get("file") or ""))
            display = entry.get("display_file")
            if isinstance(display, str) and display:
                self._unlink_quietly(self.root / display)
            # Also sweep leftovers so a hand managed directory does not keep a
            # file that the manifest no longer knows about.
            for pattern in (*ALL_EXTENSIONS, f"{DISPLAY_INFIX}jpg"):
                self._unlink_quietly(self.root / f"stage_{index:02d}{pattern}")

            self._write_manifest()
            readiness = self.readiness()

        log.info("guide %d deleted", index)
        return {
            "index": index,
            "configured": False,
            "ready": readiness["ready"],
            "uploaded": readiness["uploaded"],
        }

    # --------------------------------------------------------------- helpers

    def _write_display_copy(self, source: Path, mime: str) -> str:
        """Downscale for the guide screen. Returns the created file's name.

        The copy is always JPEG, which has no alpha channel. A transparent source
        (PNG, WebP, GIF) is flattened onto white rather than black, so a diagram
        drawn on a transparent background still reads as a drawing.
        """
        index = int(_STAGE_FILE_RE.match(source.name).group(1))
        destination = self.root / f"stage_{index:02d}{DISPLAY_INFIX}jpg"
        with Image.open(source) as image:
            if image.mode in ("RGBA", "LA", "P"):
                rgba = image.convert("RGBA")
                converted = Image.new("RGB", rgba.size, (255, 255, 255))
                converted.paste(rgba, mask=rgba.split()[-1])
            else:
                converted = image.convert("RGB")
            ratio = self.config.guide_display_max_width / float(converted.width)
            target_size = (
                self.config.guide_display_max_width,
                max(1, int(round(converted.height * ratio))),
            )
            resized = converted.resize(target_size, Image.LANCZOS)
            buffer = io.BytesIO()
            resized.save(buffer, format="JPEG", quality=DISPLAY_JPEG_QUALITY, optimize=True)
        self._atomic_write_bytes(destination, buffer.getvalue())
        return destination.name

    def _write_manifest(self) -> None:
        if not self.config.guides_persist:
            # Debug mode. The images still hit the disk so they can be served, but
            # the index is not written and is lost on restart.
            return

        payload = {
            "version": MANIFEST_VERSION,
            "updated_at": self._now(),
            "stages": {str(index): entry for index, entry in sorted(self._entries.items())},
            INSTRUCTIONS_KEY: {
                str(index): text for index, text in sorted(self._instructions.items())
            },
        }
        text = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
        self.root.mkdir(parents=True, exist_ok=True)

        if self.config.guides_keep_backup and self.manifest_path.is_file():
            try:
                shutil.copy2(self.manifest_path, self.backup_path)
            except OSError as exc:
                log.warning("could not back up guide manifest: %s", exc)

        tmp = self.manifest_path.with_name(f"{MANIFEST_NAME}.{uuid.uuid4().hex[:8]}.tmp")
        try:
            with open(tmp, "w", encoding="utf-8") as handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, self.manifest_path)
        except OSError as exc:
            log.error("could not write guide manifest: %s", exc)
            self._unlink_quietly(tmp)
            raise ApiError("INTERNAL_ERROR", f"Could not save the guide manifest: {exc}") from exc

    def _atomic_write_bytes(self, destination: Path, data: bytes) -> None:
        tmp = destination.with_name(f".{destination.name}.{uuid.uuid4().hex[:8]}.tmp")
        try:
            with open(tmp, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, destination)
        except OSError as exc:
            self._unlink_quietly(tmp)
            raise ApiError(
                "INTERNAL_ERROR",
                f"Could not store the diagram: {exc}",
                detail={"file": destination.name},
            ) from exc

    @staticmethod
    def _unlink_quietly(path: Path) -> None:
        if not path or not str(path) or str(path) == ".":
            return
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            log.warning("could not remove %s: %s", path, exc)

    @staticmethod
    def _now() -> str:
        from datetime import datetime

        return datetime.now().astimezone().isoformat(timespec="seconds")

    # ------------------------------------------------------------ diagnostics

    def stats(self) -> Dict[str, Any]:
        with self.lock:
            return {
                "root": str(self.root),
                "configured": sorted(self._entries.keys()),
                "customised": sorted(self._instructions.keys()),
                "persist": self.config.guides_persist,
                "backup_present": self.backup_path.is_file(),
            }


__all__ = ["GuideStore", "MANIFEST_NAME", "MANIFEST_VERSION"]
