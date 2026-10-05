"""Path normalisation and name rules.

Both :mod:`app.project` and :mod:`app.fsbrowser` touch host paths, so the rules
live here once rather than being written twice. docs/API.md section 9.1 calls
this out explicitly.

Two different treatments of ``..`` exist on purpose and are easy to confuse.

* ``PUT /api/project`` **rejects** a path containing ``..``. The operator typed
  it, so telling them it is ambiguous beats guessing what they meant.
* ``GET /api/fs/list`` **resolves** ``..``, because a picker walking upward
  produces those segments mechanically and rejecting them would break the Up
  button. docs/API.md sections 4.4 and 4.5 specify both behaviours.
"""

from __future__ import annotations

import os
import re
from typing import List, Optional

# Letters, digits, dot, dash, underscore. First character must be a letter or a
# digit. Used for both session folder names and newly created folders, per
# docs/API.md section 4.6 where the two rules are deliberately identical.
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")

MAX_PATH_LENGTH = 512


def expand(path: str) -> str:
    """Expand a leading ``~`` then collapse redundant separators and dot segments."""
    return normalize(os.path.expanduser(path))


def normalize(path: str) -> str:
    """Textual normalisation. No symlink resolution, no existence requirement.

    ``//a`` and ``/a`` collapse to the same value, ``.`` segments are dropped and
    trailing separators are removed, matching what docs/API.md section 4.5
    promises about the ``path`` field of a listing.
    """
    if not path:
        return path
    collapsed = re.sub(r"/{2,}", "/", path)
    if len(collapsed) > 1 and collapsed.endswith("/"):
        collapsed = collapsed.rstrip("/") or "/"
    normalized = os.path.normpath(collapsed)
    return normalized or "/"


def has_parent_reference(path: str) -> bool:
    """True when any segment is literally ``..``."""
    return ".." in path.replace("\\", "/").split("/")


def is_absolute_like(path: str) -> bool:
    """Absolute, or written with a leading ``~`` that will expand to one."""
    if path.startswith("/") or path == "~" or path.startswith("~/"):
        return True
    # Windows drive letters and UNC shares reach here. os.path.isabs knows about
    # them, the checks above do not.
    return os.path.isabs(path)


def is_root(path: str) -> bool:
    r"""True for a filesystem root or a bare top level system folder.

    ``/`` is the obvious case. ``/Users`` and ``/Volumes`` are also refused, so a
    project directory can never be the mount point itself. That is the reading
    docs/API.md section 4.4 intends with "must not be a filesystem root".

    Splits on either separator so ``C:\Users`` counts as two segments and is not
    mistaken for a root, while ``C:\`` is one and is. The docstring is raw because
    a bare backslash-U in a normal one would be read as a unicode escape.
    """
    parts = [part for part in re.split(r"[\\/]+", normalize(path)) if part]
    return len(parts) < 2


def parent_of(path: str) -> Optional[str]:
    """Parent directory, or ``None`` when ``path`` is already a volume root.

    ``os.path.dirname`` returns the path itself for a root, which is how a
    Windows drive like ``C:\\`` is told apart from a folder. Testing for ``/``
    alone missed both ``C:\\`` and the ``\\`` that ``/`` normalises to on
    Windows, so the picker's Up button never disabled at the top of a drive.
    """
    normalized = normalize(path)
    parent = os.path.dirname(normalized)
    if not parent or parent == normalized:
        return None
    return parent


def is_within(path: str, root: str) -> bool:
    """True when ``path`` is ``root`` itself or sits underneath it."""
    candidate = normalize(path)
    boundary = normalize(root)
    if candidate == boundary:
        return True
    # Tolerate either separator. A Windows root like D:\ must still match D:\work,
    # and rstrip("/") alone would leave the trailing backslash in place and fail.
    trimmed = boundary.rstrip("/\\")
    return candidate.startswith(trimmed + "/") or candidate.startswith(trimmed + "\\")


def is_within_any(path: str, roots: List[str]) -> bool:
    return any(is_within(path, root) for root in roots)


def valid_name(name: str) -> bool:
    """Folder and session name rule, shared by both call sites."""
    return bool(name) and bool(NAME_RE.match(name))


def name_error(name: str) -> str:
    """Reason a name failed, phrased for the operator."""
    if not name or not name.strip():
        return "A name is required."
    if len(name) > 64:
        return "Keep the name to 64 characters or fewer."
    if not re.match(r"^[A-Za-z0-9]", name):
        return "The name must start with a letter or a digit."
    if not NAME_RE.match(name):
        return "Use only letters, digits, dot, dash and underscore."
    return "The name is not valid."


def format_gb(free_bytes: int) -> float:
    """Bytes to GB, binary units, so it lines up with the frontend formatter."""
    return round(free_bytes / (1024 ** 3), 1)


def free_bytes(path: str) -> int:
    """Free space on the volume holding ``path``, or 0 when it cannot be read.

    ``os.statvfs`` is Unix only. On Windows the attribute does not exist at all,
    so looking it up raises AttributeError rather than OSError, which is why both
    are caught. Catching only OSError here would let the service die on Windows
    instead of falling through to the portable path.
    """
    try:
        stat = os.statvfs(path)
        return stat.f_bavail * stat.f_frsize
    except (AttributeError, OSError):
        pass

    try:
        import shutil

        return shutil.disk_usage(path).free
    except (AttributeError, OSError):
        return 0
