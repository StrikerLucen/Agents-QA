#!/usr/bin/env python3
"""One atomic writer, used by all 7 of the scripts that have an `--out`.

Converted as of 2026-11-12: env_safety_check.py, flake_smells.py,
arid_nodes.py, risk_map.py, normalize_coverage.py, pr_comment.py,
project_state.py. The promise quoted below is now kept in full.
tests/test_robustness.py::test_every_out_writer_is_atomic asserts both halves
of that -- every `--out` script calls write_text_atomic, and no plain
`open(args.out` survives beside it -- so a regression names the script.

Why this file exists: skills/start/SKILL.md promises the user that
"writes are atomic (temp file, then rename) and there is one file per item, so
parallel agents cannot clobber each other or leave a half-written baseline".
That promise was false -- every `--out` path in scripts/ was a plain
`open(path, "w")`, which truncates first and writes after. The orchestrator
dispatches agents in parallel and they all write under .testing/, so two of
them aimed at the same path could interleave and BOTH EXIT 0. A clobbered
report is indistinguishable from a correct one, which is the one failure shape
this project exists to prevent.

Why it is not called `_io.py` (which the brief suggested): `_io` is a CPython
*builtin* module, and BuiltinImporter sits ahead of PathFinder on
sys.meta_path, so `import _io` would always return the C extension and never
this file -- the helper would be silently dead and every caller would get the
stdlib module instead. Named `atomic_io` it is actually importable.

The mechanics, each part load-bearing:

  * The path is resolved with os.path.realpath first. os.replace replaces the
    NAME, so writing to a symlink without resolving it would delete the link
    and leave whatever it pointed at holding stale content -- measured: after
    one such write islink() was False, the real target still held the old
    bytes, and the exit code was 0. A plain open() wrote through the link, so
    resolving first is what keeps the behaviour the callers already had.
    Resolving also guarantees the temp file lands on the same filesystem as
    the file actually being replaced.
  * The temp file is created in the DESTINATION DIRECTORY, not /tmp. os.replace
    is only atomic within one filesystem; a rename across a mount point is a
    copy, which is exactly the half-written window we are removing.
  * flush() + os.fsync() before the rename, so the bytes have been handed to
    the disk and are not just in the page cache when the rename makes the name
    point at them. Without it a crash can leave the new name pointing at an
    empty file.
  * The destination mode is set DELIBERATELY before the rename. NamedTemporary
    File creates at 0600 and os.replace carries that inode's mode to the
    destination, so without this the writer silently narrowed every report it
    touched from 0644 to 0600 (measured on CPython 3.9.6 and 3.14.6, macOS):
    a plain open() produced 0644, this function produced 0600, and overwriting
    an existing 0644 file left it 0600 -- which breaks any consumer running as
    a different user, such as a CI artifact upload step. An existing
    destination's mode is reused; otherwise 0644. Deliberately NOT os.umask(0)
    plus restore: umask is process-wide, so a thread writing concurrently
    would create its files under the temporarily cleared mask.
  * os.replace(), not os.rename(): replace overwrites an existing destination
    atomically on both POSIX and Windows. os.rename raises on Windows when the
    target exists, which would make this fail on exactly the retry case.
  * The containing directory is fsynced after the rename, BEST EFFORT. A
    rename is directory metadata, so the new name surviving a crash depends on
    the directory being flushed, not on the file fsync above. It is best
    effort because fsync on a directory fd is not portable -- `man 2 fsync`
    (macOS 27.2) documents EINVAL for file types that do not support the
    operation -- so an OSError here is swallowed rather than failing a write
    that already succeeded.
  * The temp file is removed on any failure, so a crash leaves the previous
    good report in place and no `.tmp` litter in .testing/ for the next agent
    to mistake for a real artifact.

What this does NOT promise: that the bytes have reached the platters. The
macOS fsync(2) page is explicit that fsync hands data to the drive and the
drive may still reorder or lose it on power failure; F_FULLFSYNC is the
stronger primitive and is not used here. The guarantee offered is the one the
callers need -- a concurrent READER sees either the whole old file or the
whole new one, never a half-written one -- plus a best-effort attempt at
making the rename durable.

Standard library only: these scripts run before anything may be installed.
"""

from __future__ import annotations

import json
import os
import stat
import tempfile

# Visible in the directory listing for the moment it exists, so a human who
# catches one mid-write knows what it is and which tool to blame.
TMP_PREFIX = ".testsquad-tmp-"

# Used only when the destination does not already exist. Matches what a plain
# open(path, "w") produced under this project's umask; see the module
# docstring for why the umask is not consulted directly.
DEFAULT_MODE = 0o644


def _destination_mode(path: str) -> int:
    """The mode to give `path`: its current one if it exists, else the default.

    An unreadable or vanished destination falls back rather than raising --
    the write itself is what is allowed to fail, not the mode lookup.
    """
    try:
        return stat.S_IMODE(os.stat(path).st_mode)
    except OSError:
        return DEFAULT_MODE


def _fsync_directory(directory: str) -> None:
    """Flush the directory entry so the rename survives a crash. Best effort:
    some filesystems reject fsync on a directory fd with EINVAL, and the write
    has already succeeded by the time this is called."""
    try:
        fd = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def write_text_atomic(path: str, text: str) -> None:
    """Write `text` to `path` so a concurrent reader sees old or new, never half.

    A symlink is followed, not replaced: the real file behind it is what gets
    the new bytes. The destination keeps the mode it already had, or gets 0644
    if it is new.

    Raises OSError on failure, same as open() would, and leaves the previous
    contents of `path` untouched when it does.
    """
    target = os.path.realpath(os.path.abspath(path))
    directory = os.path.dirname(target) or "."
    os.makedirs(directory, exist_ok=True)
    mode = _destination_mode(target)
    tmp_name = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", newline="",
            dir=directory, prefix=TMP_PREFIX,
            suffix=".tmp", delete=False,
        ) as fh:
            tmp_name = fh.name
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        # Before the rename, so the destination is never briefly 0600: after
        # os.replace the name already points at this inode.
        os.chmod(tmp_name, mode)
        os.replace(tmp_name, target)
        tmp_name = None
    finally:
        if tmp_name is not None:
            # Best effort: we are already on the failure path and the real
            # exception is the one worth propagating.
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
    _fsync_directory(directory)


def write_json_atomic(path: str, obj) -> None:
    """Serialise `obj` and write it atomically, with the trailing newline every
    caller in this tree was already adding by hand.

    No caller in scripts/ uses this yet (measured 2026-11-12: zero references
    repo-wide outside this file and its tests). It is kept because the three
    unconverted `--out` writers all emit JSON and are the queued callers;
    tests/test_atomic_io.py covers it so it cannot rot unnoticed.
    """
    write_text_atomic(path, json.dumps(obj, indent=2) + "\n")
