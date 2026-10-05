"""Tests for the path rules that differ between POSIX and Windows.

These do not require Windows. The separator handling is pure string work, so the
Windows shapes are asserted directly. They exist because the original code split
on ``/`` alone and normalised with ``os.path.dirname``, which silently produced
wrong answers for drive letter paths, and nothing on this machine would have
noticed until the rig was redeployed.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app import paths  # noqa: E402


# ----------------------------------------------------------------- is_root

@pytest.mark.parametrize("candidate", ["/", "//", "/Users", "/Volumes", "/media", "/mnt"])
def test_posix_roots_are_refused(candidate):
    assert paths.is_root(candidate) is True


@pytest.mark.parametrize("candidate", ["/Users/ch7au", "/Users/a/b", "/home/user/data"])
def test_posix_non_roots_are_allowed(candidate):
    assert paths.is_root(candidate) is False


@pytest.mark.parametrize("candidate", ["C:\\", "D:\\", "C:", "D:/"])
def test_windows_drive_roots_are_refused(candidate):
    assert paths.is_root(candidate) is True


@pytest.mark.parametrize("candidate", ["C:\\Users", "C:\\Users\\name", "D:\\data\\capture"])
def test_windows_folders_below_a_drive_are_allowed(candidate):
    """A drive followed by a folder is two segments, so it is not a bare root."""
    assert paths.is_root(candidate) is False


# --------------------------------------------------------------- is_within

def test_within_matches_the_root_itself():
    assert paths.is_within("/a/b", "/a/b") is True


def test_within_matches_a_descendant():
    assert paths.is_within("/a/b/c", "/a/b") is True


def test_within_rejects_a_sibling_with_a_shared_prefix():
    """The classic prefix bug. /a/bc must not count as inside /a/b."""
    assert paths.is_within("/a/bc", "/a/b") is False


def test_within_handles_a_trailing_separator_on_the_root():
    assert paths.is_within("/a/b/c", "/a/b/") is True


@pytest.mark.skipif(sys.platform != "win32", reason="drive semantics are Windows only")
def test_within_handles_a_windows_drive_root():
    assert paths.is_within("D:\\work\\run1", "D:\\") is True


@pytest.mark.skipif(sys.platform != "win32", reason="drive semantics are Windows only")
def test_within_handles_a_trailing_backslash_on_the_root():
    assert paths.is_within("D:\\work", "D:\\work\\") is True


@pytest.mark.skipif(sys.platform != "win32", reason="drive semantics are Windows only")
def test_within_rejects_another_drive():
    assert paths.is_within("E:\\work", "D:\\") is False


# ---------------------------------------------------------- is_absolute_like

@pytest.mark.parametrize("candidate", ["/Users/a", "~", "~/Documents"])
def test_posix_absolute_like(candidate):
    assert paths.is_absolute_like(candidate) is True


@pytest.mark.parametrize("candidate", ["relative/thing", "./here", "", "a"])
def test_posix_relative_is_not_absolute_like(candidate):
    assert paths.is_absolute_like(candidate) is False


@pytest.mark.skipif(sys.platform != "win32", reason="drive semantics are Windows only")
@pytest.mark.parametrize("candidate", ["C:\\Users\\a", "D:/data"])
def test_windows_drive_paths_are_absolute_like(candidate):
    assert paths.is_absolute_like(candidate) is True


# ------------------------------------------------------- parent references

@pytest.mark.parametrize(
    "candidate",
    ["/a/../b", "..", "a/..", "C:\\a\\..\\b", "C:/a/../b"],
)
def test_parent_references_are_detected_on_both_separators(candidate):
    assert paths.has_parent_reference(candidate) is True


@pytest.mark.parametrize("candidate", ["/a/b", "C:\\a\\b", "a..b", "/a/..b"])
def test_parent_references_are_not_over_detected(candidate):
    """A literal ``..`` segment only. ``a..b`` is a legal folder name."""
    assert paths.has_parent_reference(candidate) is False


# ------------------------------------------------------------- free space

def test_free_bytes_is_positive_for_an_existing_directory(tmp_path):
    assert paths.free_bytes(str(tmp_path)) > 0


def test_free_bytes_returns_zero_rather_than_raising(tmp_path):
    """os.statvfs does not exist on Windows, so the lookup raises AttributeError.

    Catching only OSError there would take the service down on Windows. This
    guards the portable fallback.
    """
    missing = tmp_path / "definitely" / "not" / "here"
    assert paths.free_bytes(str(missing)) == 0


def test_free_bytes_survives_a_missing_statvfs(tmp_path, monkeypatch):
    """Simulate the Windows shape where the attribute is absent entirely.

    On Windows ``os.statvfs`` does not exist, so the lookup raises AttributeError
    rather than OSError. The property under test is that nothing escapes to the
    caller, which is what would take the service down. The value is not asserted
    because removing statvfs also disables shutil's POSIX path, so there is no
    portable number to expect here.
    """
    monkeypatch.delattr(paths.os, "statvfs", raising=False)
    result = paths.free_bytes(str(tmp_path))  # must not raise
    assert isinstance(result, int)


# ---------------------------------------------------------------- names

@pytest.mark.parametrize("name", ["session_a", "Run1", "a.b-c_d", "A" * 64])
def test_valid_names(name):
    assert paths.valid_name(name) is True


@pytest.mark.parametrize("name", ["", ".hidden", "-lead", "has space", "a/b", "A" * 65, ".."])
def test_invalid_names(name):
    assert paths.valid_name(name) is False
