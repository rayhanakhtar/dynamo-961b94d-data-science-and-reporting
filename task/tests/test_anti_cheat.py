"""Anti-cheat / output-path checks for all five artifacts.

Includes symlink-escape rejection tests that build temporary directory
trees (never touching the real /app/output root): a symlinked output
directory, a symlinked output file, and a parent-directory symlink escape
must all be rejected by the shared validator in conftest.py.
"""

import os
import pathlib
import tempfile

import pytest

from conftest import (OUTPUT_DIR, check_output_files, OUTPUT_FILES,
                      validate_output_paths)


def _make_files(root, names=OUTPUT_FILES):
    root = pathlib.Path(root)
    root.mkdir(parents=True, exist_ok=True)
    for name in names:
        (root / name).write_text("x\n", encoding="utf-8")


def test_output_files_exist():
    check_output_files()


def test_no_symlinks():
    for name in OUTPUT_FILES:
        path = os.path.join(OUTPUT_DIR, name)
        assert not os.path.islink(path), "%s is a symlink" % name
        assert os.path.isfile(path), "%s is not a regular file" % name


def test_no_extra_unexpected_outputs():
    """No unexpected extra regular files in /app/output beyond the contract set."""
    if not os.path.isdir(OUTPUT_DIR):
        return
    expected = set(OUTPUT_FILES)
    present = set()
    for entry in os.listdir(OUTPUT_DIR):
        full = os.path.join(OUTPUT_DIR, entry)
        if os.path.isfile(full) and not os.path.islink(full):
            present.add(entry)
    extra = present - expected
    assert not extra, "unexpected output files: %r" % sorted(extra)


def test_output_paths_contained():
    """All graded outputs resolve inside the real output directory."""
    validate_output_paths(OUTPUT_DIR, OUTPUT_FILES)


def test_output_dir_itself_not_a_symlink():
    assert not os.path.islink(OUTPUT_DIR), \
        "/app/output must not be a symlink"


# ---------------------------------------------------------------------------
# Symlink-escape rejection (temporary trees, not the real output root)
# ---------------------------------------------------------------------------

def test_rejects_symlinked_output_dir():
    with tempfile.TemporaryDirectory() as tmp:
        base = pathlib.Path(tmp)
        real = base / "real"
        _make_files(real)
        link = base / "output"
        link.symlink_to(real, target_is_directory=True)
        with pytest.raises(AssertionError):
            validate_output_paths(str(link), OUTPUT_FILES)


def test_rejects_symlinked_output_file():
    with tempfile.TemporaryDirectory() as tmp:
        base = pathlib.Path(tmp)
        out = base / "output"
        out.mkdir()
        for name in OUTPUT_FILES:
            if name == "effective_bookings.csv":
                continue
            (out / name).write_text("x\n", encoding="utf-8")
        outside = base / "outside"
        _make_files(outside)
        (out / "effective_bookings.csv").symlink_to(
            outside / "effective_bookings.csv")
        with pytest.raises(AssertionError):
            validate_output_paths(str(out), OUTPUT_FILES)


def test_rejects_parent_dir_symlink_escape():
    with tempfile.TemporaryDirectory() as tmp:
        base = pathlib.Path(tmp)
        real_out = base / "real" / "output"
        _make_files(real_out)
        parent_link = base / "parent"
        parent_link.symlink_to(base / "real", target_is_directory=True)
        escaped = parent_link / "output"
        # resolve realpath: output itself is fine, but a parent component is a
        # symlink that could redirect the graded root -> must be rejected.
        with pytest.raises(AssertionError):
            validate_output_paths(str(escaped), OUTPUT_FILES)


def test_accepts_normal_regular_files():
    with tempfile.TemporaryDirectory() as tmp:
        out = pathlib.Path(tmp) / "output"
        _make_files(out)
        canonical = validate_output_paths(str(out), OUTPUT_FILES)
        assert canonical == str(out.resolve())
