#!/usr/bin/env python3
"""MANIFEST.json integrity: every ground-truth item must resolve on disk.

`examples/fixtures/MANIFEST.json` is the ground truth the whole eval suite is
scored against. It names, for every planted defect, decoy, weak test and config
file, the repository, the path and the line where that thing lives. Nothing
checked that those paths still point at anything.

That is not hypothetical. `examples/fixtures/node-ts/.env` -- item
NODE-CONFIG-01, the fixture whose entire purpose is to make
`scripts/env_safety_check.py` return UNSAFE -- was declared here but had never
been committed: not tracked, not ignored, simply absent from every clone. The
scanner then returned **SAFE** for it, which is the worst possible failure mode
in this repository: a missing input rendering as a clean bill of health.

So this module asserts the boring thing nobody had asserted. For every item:

  * the repository it names is one the manifest declares, and exists;
  * `repo` + `file` resolves to a real file;
  * the declared `line` is within that file's real line count;

and, per repository, that `riskiest_file_expected` exists -- that one is what
`risk_map.py`'s ranking test is measured against.

**These tests must never skip.** `test_integration.py`'s `require()` helper
calls `skipTest()` when a fixture is absent, so the exact condition this module
exists to catch reads there as "skipped", and a skip is silence. Silence must
never look like success. Nothing in this file may call `skipTest` -- and
`test_the_suite_itself_cannot_be_skipped_away` enforces that against this
file's own source, so a future maintainer cannot quietly restore the behaviour
that hid the defect in the first place.

Standard library only, unittest, Python 3.9 compatible.
"""

from __future__ import annotations

import ast
import json
import os
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
FIXTURES = os.path.join(ROOT, "examples", "fixtures")
MANIFEST_PATH = os.path.join(FIXTURES, "MANIFEST.json")
SCHEMA = "testsquad.fixtures/1"


def rel(path):
    """A path a human can paste into an editor, relative to the repo root."""
    return os.path.relpath(path, ROOT).replace(os.sep, "/")


def count_lines(path):
    """Real line count: the number of lines a `sed -n 'Np'` could address.

    Read as bytes, because a fixture may legitimately not be valid UTF-8 and
    an encoding error here would be indistinguishable from a missing file --
    the confusion this module exists to end. A trailing newline does not add a
    line; a file with no trailing newline still ends in one.
    """
    with open(path, "rb") as fh:
        data = fh.read()
    if not data:
        return 0
    count = data.count(b"\n")
    if not data.endswith(b"\n"):
        count += 1
    return count


class ManifestCase(unittest.TestCase):
    """Loads the manifest, and fails -- never skips -- if it cannot.

    `test_integration.py` skips when the manifest is absent or carries an
    unexpected schema. That is the right call there, where the manifest is a
    secondary input to a test about something else. Here the manifest *is* the
    subject, so an unreadable one is a failure with a cause attached.
    """

    def manifest(self):
        if not os.path.isfile(MANIFEST_PATH):
            self.fail(
                "{0} does not exist. The fixtures' ground truth is gone; "
                "every eval scored against it is meaningless until it is "
                "restored.".format(rel(MANIFEST_PATH)))
        try:
            with open(MANIFEST_PATH, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError) as exc:
            self.fail("{0} could not be read as JSON: {1}: {2}".format(
                rel(MANIFEST_PATH), type(exc).__name__, exc))
        self.assertIsInstance(
            data, dict,
            "{0} must be a JSON object, got {1}".format(
                rel(MANIFEST_PATH), type(data).__name__))
        self.assertEqual(
            SCHEMA, data.get("schema"),
            "{0} declares schema {1!r}; this test reads {2!r}. Either the "
            "manifest or this test is stale -- do not assume which.".format(
                rel(MANIFEST_PATH), data.get("schema"), SCHEMA))
        return data

    def items(self):
        data = self.manifest()
        items = data.get("items")
        self.assertIsInstance(
            items, list,
            "{0} has no 'items' list; there is no ground truth to check "
            "against".format(rel(MANIFEST_PATH)))
        self.assertTrue(
            items,
            "{0} declares zero ground-truth items. An empty manifest would "
            "make every check below vacuously pass.".format(
                rel(MANIFEST_PATH)))
        return items

    def repos(self):
        data = self.manifest()
        repos = data.get("repos")
        self.assertIsInstance(
            repos, dict,
            "{0} has no 'repos' object".format(rel(MANIFEST_PATH)))
        self.assertTrue(
            repos, "{0} declares zero repositories".format(rel(MANIFEST_PATH)))
        return repos

    def assertNoProblems(self, problems, headline):
        """Report every broken item at once, each naming its id and its path.

        One failure per run would mean one round trip per missing fixture. The
        next person should read `NODE-CONFIG-01: examples/fixtures/node-ts/.env
        is missing` and go fix it, not solve a puzzle.
        """
        if problems:
            self.fail("{0} ({1} of them):\n  {2}".format(
                headline, len(problems), "\n  ".join(problems)))


class ManifestShapeTest(ManifestCase):
    """Before checking that paths resolve, check that there are paths."""

    def test_every_item_declares_an_id_repo_file_and_line(self):
        """An item missing `file` would sail through the existence check below
        -- there would be nothing to look for. The gap has to be a failure, not
        a silently skipped iteration."""
        problems = []
        seen = set()
        for index, item in enumerate(self.items()):
            if not isinstance(item, dict):
                problems.append("items[{0}] is {1}, not an object".format(
                    index, type(item).__name__))
                continue
            ident = item.get("id") or "items[{0}] (no id)".format(index)
            for field in ("id", "repo", "file", "line"):
                if item.get(field) in (None, ""):
                    problems.append("{0}: no {1!r}".format(ident, field))
            line = item.get("line")
            if line is not None and not isinstance(line, int):
                problems.append("{0}: line is {1!r}, not an integer".format(
                    ident, line))
            if item.get("id"):
                if item["id"] in seen:
                    problems.append(
                        "{0}: duplicate id -- two items cannot share "
                        "one".format(item["id"]))
                seen.add(item["id"])
        self.assertNoProblems(problems, "MANIFEST.json items are malformed")

    def test_every_item_names_a_repository_the_manifest_declares(self):
        """A typo in `repo` would make the file look missing for the wrong
        reason, and send whoever reads the failure hunting for a file that was
        never supposed to be at that path."""
        declared = set(self.repos())
        problems = []
        for item in self.items():
            if not isinstance(item, dict):
                continue
            repo = item.get("repo")
            if repo and repo not in declared:
                problems.append(
                    "{0}: repo {1!r} is not one of {2}".format(
                        item.get("id"), repo, sorted(declared)))
        self.assertNoProblems(problems, "MANIFEST.json names unknown repos")

    def test_every_declared_repository_exists_as_a_directory(self):
        problems = []
        for repo in sorted(self.repos()):
            path = os.path.join(FIXTURES, repo)
            if not os.path.isdir(path):
                problems.append("{0}: {1} is missing".format(repo, rel(path)))
        self.assertNoProblems(problems, "fixture repositories are missing")


class ManifestItemsResolveTest(ManifestCase):
    """The check that would have caught the missing `.env` on the first run."""

    def test_every_ground_truth_item_exists_on_disk(self):
        """`repo` + `file` must resolve to a real file.

        This is the whole point of the module. NODE-CONFIG-01 pointed at
        `examples/fixtures/node-ts/.env` for the entire life of the fixture
        while that file existed in no clone, and the only symptom was
        `env_safety_check.py` returning SAFE for a repository that exists to be
        UNSAFE.
        """
        problems = []
        for item in self.items():
            if not isinstance(item, dict):
                continue
            repo, name = item.get("repo"), item.get("file")
            if not repo or not name:
                continue
            path = os.path.join(FIXTURES, repo, name)
            if os.path.isdir(path):
                problems.append("{0}: {1} is a directory, not a file".format(
                    item.get("id"), rel(path)))
            elif not os.path.isfile(path):
                problems.append("{0}: {1} is missing".format(
                    item.get("id"), rel(path)))
        self.assertNoProblems(
            problems, "ground-truth files named in MANIFEST.json are missing")

    def test_every_declared_line_is_inside_its_file(self):
        """A line number past the end of the file is ground truth that has
        drifted: the defect was edited or moved and the manifest was not. It
        reads as precise and points at nothing."""
        problems = []
        for item in self.items():
            if not isinstance(item, dict):
                continue
            repo, name, line = (
                item.get("repo"), item.get("file"), item.get("line"))
            if not repo or not name or not isinstance(line, int):
                continue
            path = os.path.join(FIXTURES, repo, name)
            if not os.path.isfile(path):
                continue  # already reported by the existence test above
            total = count_lines(path)
            if line < 1 or line > total:
                problems.append(
                    "{0}: {1} declares line {2}, but the file has {3} "
                    "line(s)".format(item.get("id"), rel(path), line, total))
        self.assertNoProblems(
            problems, "MANIFEST.json line numbers point past end of file")

    def test_each_repository_riskiest_file_expected_exists(self):
        """`risk_map.py`'s ranking test asserts this file ranks first. If the
        path does not exist, that test is measuring a ranking against a file
        that cannot appear in it."""
        problems = []
        for repo, meta in sorted(self.repos().items()):
            if not isinstance(meta, dict):
                problems.append("{0}: repo entry is not an object".format(repo))
                continue
            expected = meta.get("riskiest_file_expected")
            if not expected:
                problems.append(
                    "{0}: no riskiest_file_expected -- risk_map's ranking "
                    "test has nothing to assert against".format(repo))
                continue
            path = os.path.join(FIXTURES, repo, expected)
            if not os.path.isfile(path):
                problems.append("{0}: riskiest_file_expected {1} is "
                                "missing".format(repo, rel(path)))
        self.assertNoProblems(
            problems, "riskiest_file_expected paths do not resolve")


class SilenceIsNotSuccessTest(ManifestCase):
    """Guards the guard."""

    def test_the_suite_itself_cannot_be_skipped_away(self):
        """The defect this module catches was invisible because the test that
        touched it skipped. If someone later wraps these checks in a skip "so
        CI is green while the fixture is being restored", the missing fixture
        goes quiet again and this file stops being worth anything. So: no skip
        machinery in this file, enforced against its own source.

        Checked by parsing this file, not by grepping it: a text search would
        match the word in this very docstring, and a test that fails on its own
        prose is noise.
        """
        with open(os.path.abspath(__file__), encoding="utf-8") as fh:
            tree = ast.parse(fh.read(), filename=__file__)

        def name_of(node):
            if isinstance(node, ast.Attribute):
                return node.attr
            if isinstance(node, ast.Name):
                return node.id
            return None

        banned = {"skipTest", "skip", "skipIf", "skipUnless", "SkipTest"}
        offenders = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                called = name_of(node.func)
                if called in banned:
                    offenders.append("line {0}: call to {1}()".format(
                        node.lineno, called))
            elif isinstance(node, ast.Raise) and node.exc is not None:
                raised = node.exc
                if isinstance(raised, ast.Call):
                    raised = raised.func
                if name_of(raised) == "SkipTest":
                    offenders.append(
                        "line {0}: raises SkipTest".format(node.lineno))
            elif isinstance(node, (ast.FunctionDef, ast.ClassDef)):
                for decorator in node.decorator_list:
                    target = decorator.func if isinstance(
                        decorator, ast.Call) else decorator
                    if name_of(target) in banned:
                        offenders.append(
                            "line {0}: {1} decorated with @{2}".format(
                                decorator.lineno, node.name, name_of(target)))
        self.assertEqual(
            [], offenders,
            "this module must fail loudly, never skip. Found skip "
            "machinery:\n  " + "\n  ".join(offenders))

    def test_the_fixture_root_is_present(self):
        """If `examples/fixtures/` itself were gone, the loops above would have
        nothing to iterate and could only report every item as missing. Say the
        real cause once, plainly, instead."""
        self.assertTrue(
            os.path.isdir(FIXTURES),
            "{0} does not exist -- the fixture tree is gone, not merely "
            "incomplete".format(rel(FIXTURES)))


if __name__ == "__main__":
    unittest.main()
