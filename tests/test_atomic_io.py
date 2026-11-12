#!/usr/bin/env python3
"""Tests for atomic_io.py.

The module had no direct coverage at all, which is how it shipped silently
narrowing every destination it touched to 0600 and destroying symlinked
destinations. Everything here is measured against a real filesystem -- no
mocked os module -- because every defect this file pins was a property of the
real syscalls (NamedTemporaryFile's 0600, os.replace replacing the name) and a
mock would have reproduced none of them.

The one exception is the directory-fsync test, which has to observe a call
that leaves no trace on the filesystem by design.
"""

import errno
import json
import os
import stat
import sys
import tempfile
import threading
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import atomic_io  # noqa: E402


class AtomicIOCase(unittest.TestCase):
    """A throwaway directory per test. Nothing is shared between tests, so the
    suite behaves the same run alone as run in any order."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = os.path.realpath(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def path(self, name):
        return os.path.join(self.dir, name)

    def mode_of(self, path):
        return stat.S_IMODE(os.stat(path).st_mode)

    def leftover_temps(self, directory=None):
        root = directory or self.dir
        return sorted(n for n in os.listdir(root)
                      if n.startswith(atomic_io.TMP_PREFIX))


# --------------------------------------------------------------------------
# D1 -- destination permissions
# --------------------------------------------------------------------------

class DestinationMode(AtomicIOCase):

    def test_overwriting_an_existing_file_keeps_its_mode(self):
        # The defect: NamedTemporaryFile creates at 0600 and os.replace carries
        # that inode's mode onto the destination, so every re-run of a
        # converted script narrowed its own report from 0644 to 0600.
        target = self.path("report.json")
        with open(target, "w", encoding="utf-8") as fh:
            fh.write("old")
        os.chmod(target, 0o644)

        atomic_io.write_text_atomic(target, "new")

        self.assertEqual(self.mode_of(target), 0o644)
        with open(target, encoding="utf-8") as fh:
            self.assertEqual(fh.read(), "new")

    def test_overwriting_preserves_a_mode_that_is_not_the_default(self):
        # 0755 rather than 0644, so a fix that hardcodes 0644 instead of
        # reading the destination still fails this.
        target = self.path("hook.sh")
        with open(target, "w", encoding="utf-8") as fh:
            fh.write("old")
        os.chmod(target, 0o755)

        atomic_io.write_text_atomic(target, "new")

        self.assertEqual(self.mode_of(target), 0o755)

    def test_a_new_file_is_created_readable_the_way_plain_open_would(self):
        # The comparison is against open() in the same directory under the same
        # umask, so this asserts behavioural equivalence rather than pinning a
        # number that depends on how the suite was invoked.
        reference = self.path("reference.txt")
        with open(reference, "w", encoding="utf-8") as fh:
            fh.write("x")
        expected = self.mode_of(reference)

        created = self.path("created.txt")
        atomic_io.write_text_atomic(created, "x")

        self.assertEqual(self.mode_of(created), expected)
        self.assertEqual(expected & stat.S_IRGRP, stat.S_IRGRP)
        self.assertEqual(expected & stat.S_IROTH, stat.S_IROTH)

    def test_the_destination_is_never_briefly_group_unreadable(self):
        # A fix that chmods AFTER os.replace would leave a window in which a
        # reader in another process sees 0600. Checking the mode of the temp
        # file at the moment of the rename is how that window is observable.
        target = self.path("report.json")
        with open(target, "w", encoding="utf-8") as fh:
            fh.write("old")
        os.chmod(target, 0o644)
        seen = []
        real_replace = os.replace

        def spy(src, dst, *a, **kw):
            seen.append(stat.S_IMODE(os.stat(src).st_mode))
            return real_replace(src, dst, *a, **kw)

        with mock.patch.object(atomic_io.os, "replace", spy):
            atomic_io.write_text_atomic(target, "new")

        self.assertEqual(seen, [0o644])


# --------------------------------------------------------------------------
# D2 -- the rename is only durable once the directory is flushed
# --------------------------------------------------------------------------

class DirectoryFsync(AtomicIOCase):

    def _fsynced_kinds(self, target, text):
        """Run a write and report, for each fsync call, whether the fd was a
        regular file or a directory."""
        kinds = []
        real_fsync = os.fsync

        def spy(fd):
            kinds.append("dir" if stat.S_ISDIR(os.fstat(fd).st_mode)
                         else "file")
            return real_fsync(fd)

        with mock.patch.object(atomic_io.os, "fsync", spy):
            atomic_io.write_text_atomic(target, text)
        return kinds

    def test_both_the_file_and_its_directory_are_fsynced(self):
        kinds = self._fsynced_kinds(self.path("report.json"), "payload")
        self.assertEqual(kinds, ["file", "dir"])

    def test_a_filesystem_that_rejects_directory_fsync_does_not_fail_the_write(self):
        # Documented in `man 2 fsync` (macOS 27.2): EINVAL when the fd refers
        # to a type that does not support the operation. The bytes are already
        # in place by then, so refusing to return would turn a successful
        # write into a failed one.
        target = self.path("report.json")
        real_fsync = os.fsync

        def refuse_directories(fd):
            if stat.S_ISDIR(os.fstat(fd).st_mode):
                raise OSError(errno.EINVAL, "Invalid argument")
            return real_fsync(fd)

        with mock.patch.object(atomic_io.os, "fsync", refuse_directories):
            atomic_io.write_text_atomic(target, "payload")

        with open(target, encoding="utf-8") as fh:
            self.assertEqual(fh.read(), "payload")
        self.assertEqual(self.leftover_temps(), [])


# --------------------------------------------------------------------------
# D3 -- a symlinked destination is followed, not replaced
# --------------------------------------------------------------------------

class SymlinkDestination(AtomicIOCase):

    def setUp(self):
        super().setUp()
        # The real file lives in a different directory from the link, so this
        # also proves the temp file is created beside the file actually being
        # replaced -- which is what keeps os.replace within one filesystem.
        self.store = os.path.join(self.dir, "store")
        self.links = os.path.join(self.dir, "links")
        os.makedirs(self.store)
        os.makedirs(self.links)
        self.real = os.path.join(self.store, "real.json")
        self.link = os.path.join(self.links, "link.json")
        with open(self.real, "w", encoding="utf-8") as fh:
            fh.write("ORIGINAL")
        os.chmod(self.real, 0o644)
        os.symlink(self.real, self.link)

    def test_writing_through_a_symlink_updates_the_target_and_keeps_the_link(self):
        atomic_io.write_text_atomic(self.link, "UPDATED")

        self.assertTrue(os.path.islink(self.link))
        with open(self.real, encoding="utf-8") as fh:
            self.assertEqual(fh.read(), "UPDATED")
        self.assertEqual(os.path.realpath(self.link), self.real)

    def test_a_symlinked_destination_keeps_the_targets_mode(self):
        os.chmod(self.real, 0o640)
        atomic_io.write_text_atomic(self.link, "UPDATED")
        self.assertEqual(self.mode_of(self.real), 0o640)

    def test_no_temp_file_is_left_in_either_directory(self):
        atomic_io.write_text_atomic(self.link, "UPDATED")
        self.assertEqual(self.leftover_temps(self.store), [])
        self.assertEqual(self.leftover_temps(self.links), [])


# --------------------------------------------------------------------------
# temp-file hygiene
# --------------------------------------------------------------------------

class TempFileCleanup(AtomicIOCase):

    def test_a_successful_write_leaves_no_temp_file(self):
        atomic_io.write_text_atomic(self.path("report.json"), "payload")
        self.assertEqual(self.leftover_temps(), [])
        self.assertEqual(sorted(os.listdir(self.dir)), ["report.json"])

    def test_a_failed_rename_leaves_no_temp_file_and_no_partial_output(self):
        # A directory cannot be replaced by a file, so os.replace raises after
        # the temp file has been written in full. That is the shape of the
        # failure that used to litter .testing/ with half-written artifacts a
        # later agent would read as real.
        victim = self.path("a-directory")
        os.makedirs(victim)

        with self.assertRaises(OSError):
            atomic_io.write_text_atomic(victim, "payload")

        self.assertEqual(self.leftover_temps(), [])
        self.assertTrue(os.path.isdir(victim))

    def test_a_failure_mid_write_leaves_the_previous_file_intact(self):
        target = self.path("report.json")
        with open(target, "w", encoding="utf-8") as fh:
            fh.write("GOOD")

        with self.assertRaises(TypeError):
            atomic_io.write_text_atomic(target, object())

        with open(target, encoding="utf-8") as fh:
            self.assertEqual(fh.read(), "GOOD")
        self.assertEqual(self.leftover_temps(), [])

    def test_a_missing_parent_directory_is_created(self):
        target = self.path(os.path.join("deep", "nested", "report.json"))
        atomic_io.write_text_atomic(target, "payload")
        with open(target, encoding="utf-8") as fh:
            self.assertEqual(fh.read(), "payload")


# --------------------------------------------------------------------------
# the point of the module: a concurrent reader never sees half a document
# --------------------------------------------------------------------------

class ConcurrentWriters(AtomicIOCase):

    WRITERS = 4
    ROUNDS = 25
    # Large enough that a truncate-then-write would be observable: a plain
    # open() holds the file at zero bytes, then at a prefix, for as long as it
    # takes to push this much through.
    FILLER = 40_000

    def _document(self, writer):
        return json.dumps({"writer": writer, "filler": "x" * self.FILLER,
                           "tail": writer}) + "\n"

    def test_a_reader_only_ever_sees_a_complete_parseable_document(self):
        target = self.path("report.json")
        documents = {w: self._document(w) for w in range(self.WRITERS)}
        atomic_io.write_text_atomic(target, documents[0])

        stop = threading.Event()
        start = threading.Barrier(self.WRITERS + 1)
        failures = []
        reads = [0]

        def writer(index):
            start.wait()
            try:
                for _ in range(self.ROUNDS):
                    atomic_io.write_text_atomic(target, documents[index])
            except Exception as exc:          # noqa: BLE001 - reported below
                failures.append(("write", index, repr(exc)))

        def reader():
            start.wait()
            while not stop.is_set():
                try:
                    with open(target, encoding="utf-8") as fh:
                        raw = fh.read()
                except OSError as exc:
                    failures.append(("open", None, repr(exc)))
                    return
                try:
                    parsed = json.loads(raw)
                except ValueError:
                    failures.append(("parse", len(raw), raw[:80]))
                    return
                reads[0] += 1
                if parsed["writer"] != parsed["tail"]:
                    failures.append(("torn", parsed["writer"], parsed["tail"]))
                    return
                if raw != documents[parsed["writer"]]:
                    failures.append(("mismatch", parsed["writer"], len(raw)))
                    return

        threads = [threading.Thread(target=writer, args=(i,))
                   for i in range(self.WRITERS)]
        watcher = threading.Thread(target=reader)
        threads.append(watcher)
        for t in threads:
            t.start()
        for t in threads[:-1]:
            t.join()
        stop.set()
        watcher.join()

        self.assertEqual(failures, [])
        # Without this the test would also pass if the reader never ran.
        self.assertGreater(reads[0], 0)
        self.assertEqual(self.leftover_temps(), [])
        with open(target, encoding="utf-8") as fh:
            self.assertIn(fh.read(), documents.values())

    def test_the_surviving_file_after_a_race_is_one_whole_writers_output(self):
        target = self.path("report.json")
        documents = {w: self._document(w) for w in range(self.WRITERS)}
        start = threading.Barrier(self.WRITERS)

        def writer(index):
            start.wait()
            for _ in range(self.ROUNDS):
                atomic_io.write_text_atomic(target, documents[index])

        threads = [threading.Thread(target=writer, args=(i,))
                   for i in range(self.WRITERS)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        with open(target, encoding="utf-8") as fh:
            survivor = fh.read()
        self.assertIn(survivor, documents.values())
        self.assertEqual(self.leftover_temps(), [])


# --------------------------------------------------------------------------
# write_json_atomic -- no production caller yet; covered so it cannot rot
# --------------------------------------------------------------------------

class WriteJsonAtomic(AtomicIOCase):

    def test_it_writes_parseable_json_with_a_trailing_newline(self):
        target = self.path("state.json")
        atomic_io.write_json_atomic(target, {"b": 2, "a": [1, None, True]})

        with open(target, encoding="utf-8") as fh:
            raw = fh.read()
        self.assertTrue(raw.endswith("}\n"))
        self.assertEqual(json.loads(raw), {"b": 2, "a": [1, None, True]})

    def test_it_indents_by_two_so_the_file_is_reviewable_in_a_diff(self):
        target = self.path("state.json")
        atomic_io.write_json_atomic(target, {"a": 1})
        with open(target, encoding="utf-8") as fh:
            self.assertEqual(fh.read(), '{\n  "a": 1\n}\n')

    def test_it_inherits_the_mode_handling_of_the_text_writer(self):
        target = self.path("state.json")
        atomic_io.write_json_atomic(target, {"a": 1})
        os.chmod(target, 0o640)
        atomic_io.write_json_atomic(target, {"a": 2})
        self.assertEqual(self.mode_of(target), 0o640)

    def test_an_unserialisable_object_leaves_no_file_and_no_temp(self):
        target = self.path("state.json")
        with self.assertRaises(TypeError):
            atomic_io.write_json_atomic(target, {"a": object()})
        self.assertFalse(os.path.exists(target))
        self.assertEqual(self.leftover_temps(), [])


if __name__ == "__main__":
    unittest.main()
