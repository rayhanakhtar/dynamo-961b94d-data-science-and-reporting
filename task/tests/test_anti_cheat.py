"""Anti-cheat / output-path checks for all five artifacts."""

import os

from conftest import OUTPUT_DIR, check_output_files, OUTPUT_FILES


def test_output_files_exist():
    check_output_files()


def test_no_symlinks():
    for name in OUTPUT_FILES:
        path = os.path.join(OUTPUT_DIR, name)
        assert not os.path.islink(path), "%s is a symlink" % name


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
    for name in OUTPUT_FILES:
        path = os.path.join(OUTPUT_DIR, name)
        assert os.path.realpath(path).startswith(
            os.path.realpath(OUTPUT_DIR) + os.sep), \
            "%s resolves outside %s" % (path, OUTPUT_DIR)
