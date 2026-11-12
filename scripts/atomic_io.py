#!/usr/bin/env python3
"""One atomic writer, shared by every script that has an `--out`.

Why this file exists: skills/start/SKILL.md and templates/knowledge/README.md
both promise the user that "writes are atomic (temp file, then rename) and
there is one file per item, so parallel agents cannot clobber each other or
leave a half-written baseline". That promise was false -- every `--out` path
in scripts/ was a plain `open(path, "w")`, which truncates first and writes
after. The orchestrator dispatches agents in parallel and they all write under
.testing/, so two of them aimed at the same path could interleave and BOTH
EXIT 0. A clobbered report is indistinguishable from a correct one, which is
the one failure shape this project exists to prevent.

Why it is not called `_io.py` (which the brief suggested): `_io` is a CPython
*builtin* module, and BuiltinImporter sits ahead of PathFinder on
sys.meta_path, so `import _io` would always return the C extension and never
this file -- the helper would be silently dead and every caller would get the
stdlib module instead. Named `atomic_io` it is actually importable.

The mechanics, each part load-bearing:

  * The temp file is created in the DESTINATION DIRECTORY, not /tmp. os.replace
    is only atomic within one filesystem; a rename across a mount point is a
    copy, which is exactly the half-written window we are removing.
  * flush() + os.fsync() before the rename, so the bytes are on the disk and
    not just in the page cache when the rename makes the name point at them.
    Without it a crash can leave the new name pointing at an empty file.
  * os.replace(), not os.rename(): replace overwrites an existing destination
    atomically on both POSIX and Windows. os.rename raises on Windows when the
    target exists, which would make this fail on exactly the retry case.
  * The temp file is removed on any failure, so a crash leaves the previous
    good report in place and no `.tmp` litter in .testing/ for the next agent
    to mistake for a real artifact.

Standard library only: these scripts run before anything may be installed.
"""

from __future__ import annotations

import json
import os
import tempfile

# Visible in the directory listing for the moment it exists, so a human who
# catches one mid-write knows what it is and which tool to blame.
TMP_PREFIX = ".testsquad-tmp-"


def write_text_atomic(path: str, text: str) -> None:
    """Write `text` to `path` so a concurrent reader sees old or new, never half.

    Raises OSError on failure, same as open() would, and leaves the previous
    contents of `path` untouched when it does.
    """
    directory = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(directory, exist_ok=True)
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
        os.replace(tmp_name, path)
        tmp_name = None
    finally:
        if tmp_name is not None:
            # Best effort: we are already on the failure path and the real
            # exception is the one worth propagating.
            try:
                os.unlink(tmp_name)
            except OSError:
                pass


def write_json_atomic(path: str, obj) -> None:
    """Serialise `obj` and write it atomically, with the trailing newline every
    caller in this tree was already adding by hand."""
    write_text_atomic(path, json.dumps(obj, indent=2) + "\n")
