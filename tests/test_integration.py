#!/usr/bin/env python3
"""End-to-end integration tests: the scripts working *together*.

Every script in `scripts/` has its own unit suite, and all of them pass. That
proves each part is internally consistent; it proves nothing about the seams.
The real product is a chain --

    env_safety_check -> risk_map -> normalize_coverage -> flake_smells
                     -> pr_comment -> session_context

-- and a break at any seam (a renamed schema key, a path that is absolute on
one side and relative on the other, a verdict that is computed but never
honoured downstream) would pass every unit test in the repository.

So these tests run the real scripts, as subprocesses, against the real
fixtures in `examples/fixtures/`, and assert the properties that only show up
when two scripts have to agree with each other:

  * the safety gate actually gates, and never prints the thing it is guarding
  * risk_map ranks the file `MANIFEST.json` says is hottest, with a readable
    reason that does not repeat itself
  * coverage normalizes to hand-computed totals, and "not measured" survives
    as `null` rather than decaying into `0`
  * every weak test the manifest predicts is flagged, with the rule it names,
    and every good-test file is left completely alone
  * the PR comment never publishes an unconfirmed finding or a secret
  * the session preamble stays inside its token budget
  * the `schema` strings are the ones downstream readers were promised
  * running the whole chain over a repository leaves its worktree untouched

Expected values are written out by hand (and shown as arithmetic in the
docstrings) rather than captured from a run, so a regression changes the
result instead of the expectation.

Standard library only, unittest, Python 3.9 compatible.
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SCRIPTS = os.path.join(ROOT, "scripts")
FIXTURES = os.path.join(ROOT, "examples", "fixtures")
MANIFEST_PATH = os.path.join(FIXTURES, "MANIFEST.json")

sys.path.insert(0, SCRIPTS)

REPOS = ("node-ts", "python-pkg", "java-min")


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def run_script(name, *args, **kwargs):
    """Run a script the way a hook or CI job would: as a subprocess, so the
    exit code and the stdout/stderr split are part of what is under test."""
    cmd = [sys.executable, os.path.join(SCRIPTS, name), *args]
    env = dict(os.environ)
    env.update(kwargs.get("env") or {})
    return subprocess.run(cmd, capture_output=True, text=True,
                          cwd=kwargs.get("cwd"), env=env)


def onboard(repo_path):
    """Record that this user onboarded this repo, the way /testsquad:start does.

    session_context.py stays silent otherwise, and deliberately: `.testing/` is
    committed, so without a record outside the repo anyone who got you to clone
    a repo could write your session preamble. Returns the env the child needs.
    """
    data_dir = tempfile.mkdtemp()
    marker = os.path.join(
        data_dir, "onboarded",
        hashlib.sha256(os.path.abspath(repo_path).encode("utf-8")).hexdigest()[:16] + ".json")
    os.makedirs(os.path.dirname(marker), exist_ok=True)
    with open(marker, "w", encoding="utf-8") as fh:
        fh.write("{}")
    return {"CLAUDE_PLUGIN_DATA": data_dir}


def load_manifest():
    try:
        with open(MANIFEST_PATH, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None


def copy_repo(repo, dest_parent, name="subject"):
    """Copy a fixture repository, git history included, into a scratch dir.

    Everything here works on copies. A test that mutated `examples/fixtures/`
    would corrupt the ground truth the whole eval suite is measured against,
    and the damage would not show up until some later run disagreed with the
    manifest for reasons nobody could reconstruct.

    The copy is deliberately *not* named after the fixture: several assertions
    below search the serialized output for fragments of a secret, and a path
    containing the word "fixture" would collide with the fixture's own
    fabricated hostnames and produce a false leak report.
    """
    src = os.path.join(FIXTURES, repo)
    dest = os.path.join(tempfile.mkdtemp(dir=dest_parent), name)
    shutil.copytree(src, dest, symlinks=True)
    return dest


def windows_of(value, size=8):
    """Every `size`-character window of a string.

    Leak checks compare windows rather than whole values on purpose: half a
    Stripe key is still a published secret, and an equality check against the
    full value would wave through a preview that printed all but the last
    character.
    """
    return {value[i:i + size] for i in range(max(0, len(value) - size + 1))}


def assert_no_secret(case, blob, value, label):
    leaked = sorted(w for w in windows_of(value) if w in blob)
    case.assertEqual(
        [], leaked,
        "{0} leaked into the output. Offending 8-character window(s): {1}"
        .format(label, leaked))


def read_text(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def read_json_file(path):
    return json.loads(read_text(path))


def git(repo, *args):
    return subprocess.run(["git", "-C", repo, *args],
                          capture_output=True, text=True)


# --------------------------------------------------------------------------
# fixture availability: FAIL by default, skip only under an explicit opt-in
# --------------------------------------------------------------------------
#
# `TESTSQUAD_ALLOW_MISSING_FIXTURES=1` restores the old behaviour, in which a
# missing or malformed fixture skipped the test instead of failing it. It
# exists for ONE situation: local concurrent work, where a second agent really
# is mid-landing a fixture and you want the rest of the suite's signal in the
# meantime. **It must never be set in CI.** In CI there is no other agent, so
# an absent fixture is not a race, it is a hard error.
#
# The default is FAIL because the skip hid a real defect for an unknown length
# of time. `examples/fixtures/node-ts/.env` -- MANIFEST item NODE-CONFIG-01,
# the file whose entire purpose is to make `env_safety_check.py` return UNSAFE
# -- had never been committed, so every clone was missing it and the scanner
# returned SAFE for it. The suite said "skipped", which reads as "fine". A
# skip is silence, and silence must never look like success.
#
# When the override IS set, the skip reason names the variable, so an
# overridden skip can be told apart from an ordinary one at a glance.
ALLOW_MISSING_FIXTURES = "TESTSQUAD_ALLOW_MISSING_FIXTURES"


def allowing_missing_fixtures():
    """True when the documented local-only escape hatch is engaged."""
    return os.environ.get(ALLOW_MISSING_FIXTURES) == "1"


def fixture_rel(path):
    """A path a human can paste into an editor, relative to the repo root."""
    try:
        return os.path.relpath(path, ROOT).replace(os.sep, "/")
    except ValueError:                                  # different drive
        return path


def fixture_unavailable(case, summary):
    """A fixture is missing or malformed: fail loudly, or skip loudly.

    `summary` must name the thing and its path, so the first line of the
    failure is "examples/fixtures/node-ts/.env is missing (...)" rather than a
    puzzle about which input went away.
    """
    if allowing_missing_fixtures():
        case.skipTest(
            "{0}=1 is set, so a fixture failure was downgraded to a skip: {1} "
            "This override exists for local concurrent work only and must "
            "never be set in CI.".format(ALLOW_MISSING_FIXTURES, summary))
    case.fail(
        "{0} The fixtures in examples/fixtures/ are ground truth; without "
        "this one the test that needed it measures nothing, and would "
        "otherwise pass by saying nothing. Restore it (see examples/fixtures/MANIFEST.json "
        "and examples/fixtures/regen_git_history.sh). If another agent is "
        "genuinely still landing it, set {1}=1 for your local run -- never "
        "in CI.".format(summary, ALLOW_MISSING_FIXTURES))


def require(case, path, what):
    """Fail (or, under the opt-in, skip) when a fixture path is not there."""
    if not os.path.exists(path):
        fixture_unavailable(
            case, "{0} is missing ({1}).".format(fixture_rel(path), what))
    elif os.path.isfile(path) and os.path.getsize(path) == 0:
        fixture_unavailable(
            case, "{0} is malformed: it is an empty file ({1})."
            .format(fixture_rel(path), what))


# Files inside a fixture repository that this suite cannot run without, over
# and above the repository directory itself. A present directory with the
# load-bearing file deleted out of it is exactly the shape of the defect this
# machinery exists to catch, so the directory check alone is not enough.
REQUIRED_FIXTURE_FILES = {
    "node-ts": (
        (".env",
         "MANIFEST item NODE-CONFIG-01, the planted production secrets that "
         "make env_safety_check.py return UNSAFE for this fixture"),
    ),
}


def require_repo(case, repo):
    """Require a fixture repository *and* its load-bearing files."""
    require(case, os.path.join(FIXTURES, repo), "fixture repository " + repo)
    for relpath, what in REQUIRED_FIXTURE_FILES.get(repo, ()):
        require(case, os.path.join(FIXTURES, repo, relpath), what)


class ChainCase(unittest.TestCase):
    """Common scaffolding: a scratch directory per test, never a fixture."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="testsquad-int-")
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def manifest(self):
        """The ground truth, or a loud failure -- never a quiet skip.

        Both branches used to skip. An absent manifest is the missing-fixture
        case; a schema mismatch is worse, because it means the ground truth on
        disk is not the ground truth these tests read, and whispering that as
        a skip turns a loud problem into silence.
        """
        data = load_manifest()
        if data is None:
            fixture_unavailable(
                self, "{0} is missing or unreadable (the fixtures' ground "
                      "truth; nothing here can be checked against it)."
                      .format(fixture_rel(MANIFEST_PATH)))
        if data.get("schema") != "testsquad.fixtures/1":
            fixture_unavailable(
                self, "{0} is malformed: schema is {1!r}, these tests read "
                      "'testsquad.fixtures/1'."
                      .format(fixture_rel(MANIFEST_PATH), data.get("schema")))
        return data

    def write(self, relpath, text):
        path = os.path.join(self.tmp, relpath)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        return path


# --------------------------------------------------------------------------
# chain 1 -- the safety gate actually gates
# --------------------------------------------------------------------------

class SafetyGateTest(ChainCase):
    """`env_safety_check.py` is the first link, and it is a *gate*: everything
    downstream is allowed to run only because it said so. Three properties
    matter and none of them are visible to a unit test of a single detector:
    the verdict, the exit code that carries the verdict to a CI job, and the
    guarantee that the guard never prints the thing it is guarding.
    """

    # The fabricated values planted in examples/fixtures/node-ts/.env. They
    # point at nothing, but the redaction promise has to hold for them exactly
    # as it would for a real key.
    NODE_SECRETS = {
        "the production DATABASE_URL":
            "postgres://app_rw:Fixtur3Pass@db.prod.fixturecorp-notreal.net"
            ":5432/billing_production",
        "the live Stripe key": "sk_live_FIXTUREONLYNeverARealKey4242",
        "the Sentry DSN": "https://fixturekey@o1.ingest.sentry.io/1",
    }

    def scan(self, repo):
        require_repo(self, repo)
        subject = copy_repo(repo, self.tmp)
        proc = run_script("env_safety_check.py", subject)
        self.assertNotEqual(2, proc.returncode,
                            "scanner crashed: " + proc.stderr)
        return proc, json.loads(proc.stdout)

    def test_node_ts_is_unsafe_and_says_why(self):
        """node-ts carries a production .env, so the only honest answer is
        UNSAFE. A verdict with no findings attached would be an unexplained
        refusal -- the kind a team disables within a week -- so the findings
        list must be non-empty and every finding must name a file and a line
        a human can open."""
        proc, result = self.scan("node-ts")
        self.assertEqual("UNSAFE", result["verdict"])
        self.assertTrue(result["findings"],
                        "UNSAFE with an empty findings list is an "
                        "unexplained refusal")
        self.assertEqual(len(result["findings"]), result["counts"]["CRITICAL"],
                         "UNSAFE is defined by CRITICAL findings; the count "
                         "and the list must describe the same thing")
        for finding in result["findings"]:
            self.assertTrue(finding["file"])
            self.assertGreater(finding["line"], 0)
            self.assertTrue(finding["why"],
                            "a refusal with no rationale cannot be acted on")

    # What MANIFEST.json's NODE-CONFIG-01 promises this fixture produces, in
    # the manifest's own words: "3 CRITICAL: stripe_live_key,
    # remote_database_url, external_hostname". Written out by hand rather than
    # captured from a run, so a regression changes the result and not the
    # expectation.
    NODE_CRITICAL_DETECTORS = (
        "external_hostname",
        "remote_database_url",
        "stripe_live_key",
    )

    def test_node_ts_produces_exactly_the_three_declared_critical_findings(self):
        """The count *and* the identities, because `UNSAFE` is not the claim.

        The assertions above are satisfied by any non-empty findings list whose
        length equals `counts["CRITICAL"]` -- they check that the gate is
        self-consistent, never that it found the three specific things this
        fixture exists to contain. That gap was demonstrated, not theorised:
        inserting `xxx` into the fixture's Stripe key (the mask pattern
        MANIFEST.json explicitly warns maintainers about) dropped the detector
        count from 3 to 2 and the suite stayed green. One of the three
        detectors had stopped firing and nothing said so.

        So: exactly three CRITICAL findings, carrying exactly the three
        detector names the manifest declares, one each. Not a superset, not a
        subset, and not three findings from the same detector.
        """
        _, result = self.scan("node-ts")
        detectors = sorted(f["detector"] for f in result["findings"])
        self.assertEqual(
            list(self.NODE_CRITICAL_DETECTORS), detectors,
            "node-ts must produce exactly the three CRITICAL findings "
            "MANIFEST.json (NODE-CONFIG-01) declares. Expected {0}, got {1}. "
            "A detector that stops firing here is a fixture that has silently "
            "stopped testing what it was planted to test -- check the .env "
            "for an accidental mask (3+ x's, 8+ zeros), which "
            "env_safety_check.py suppresses by design."
            .format(list(self.NODE_CRITICAL_DETECTORS), detectors))
        self.assertEqual(
            3, result["counts"]["CRITICAL"],
            "counts.CRITICAL must agree with the three declared findings")
        for severity in ("HIGH", "MEDIUM", "LOW"):
            self.assertEqual(
                0, result["counts"].get(severity, 0),
                "NODE-CONFIG-01 declares CRITICAL findings only; a {0} "
                "finding here is undeclared ground truth".format(severity))

    def test_the_manifest_still_declares_the_three_detectors_this_asserts(self):
        """Guards the hand-written expectation above against manifest drift.

        If NODE-CONFIG-01 is ever rewritten to expect different detectors, the
        test above would keep asserting the old ones and would read as passing
        ground truth while disagreeing with the ground truth. Fails rather than
        skips when the manifest is unreadable: this is a check *about* the
        manifest, and `tests/test_fixture_manifest.py` makes an absent manifest
        loud on its own.
        """
        manifest = load_manifest()
        self.assertIsNotNone(
            manifest, "examples/fixtures/MANIFEST.json is absent or "
                      "unreadable; the expectation in this class cannot be "
                      "cross-checked against ground truth")
        declared = [item for item in manifest.get("items", [])
                    if item.get("id") == "NODE-CONFIG-01"]
        self.assertEqual(
            1, len(declared),
            "MANIFEST.json must carry exactly one NODE-CONFIG-01 item, "
            "found {0}".format(len(declared)))
        why = declared[0].get("why", "")
        missing = [d for d in self.NODE_CRITICAL_DETECTORS if d not in why]
        self.assertEqual(
            [], missing,
            "NODE-CONFIG-01 no longer names {0}. Either the manifest or this "
            "test is stale -- reconcile them, do not assume which."
            .format(missing))

    def test_python_pkg_is_safe(self):
        """python-pkg's .env.test points at localhost. Flagging it would be a
        false alarm, and a gate that cries wolf is a gate that gets bypassed
        with --no-verify."""
        proc, result = self.scan("python-pkg")
        self.assertEqual("SAFE", result["verdict"])
        self.assertEqual([], result["findings"])

    def test_exit_codes_carry_the_verdict_to_ci(self):
        """The JSON is for humans; the exit code is what a CI job branches on.
        If they ever disagree, every pipeline silently stops gating. UNSAFE
        must be a non-zero exit at the default --fail-on=critical, SAFE a
        zero one."""
        unsafe, _ = self.scan("node-ts")
        self.assertEqual(1, unsafe.returncode,
                         "UNSAFE must exit 1 or CI will run the suite anyway")
        safe, _ = self.scan("python-pkg")
        self.assertEqual(0, safe.returncode,
                         "SAFE must exit 0 or every clean repo is blocked")

    def test_no_secret_value_appears_anywhere_in_the_output(self):
        """The whole serialized surface -- JSON on stdout *and* the human
        summary on stderr -- is checked, because stderr is what lands in a CI
        log that a wider audience can read than the artifact.

        Checked in 8-character windows, not by equality: the redaction bug
        this guards against is a preview that keeps growing (`sk_l...` ->
        `sk_live_...`), and an equality check would pass right up to the point
        where the entire key is on screen.
        """
        proc, _ = self.scan("node-ts")
        blob = proc.stdout + proc.stderr
        for label, value in self.NODE_SECRETS.items():
            assert_no_secret(self, blob, value, label)

    def test_findings_are_serializable_for_the_next_link(self):
        """The gate's output is read by later steps, so it has to survive a
        JSON round trip with its schema intact rather than relying on being
        eyeballed in a terminal."""
        _, result = self.scan("node-ts")
        again = json.loads(json.dumps(result))
        self.assertEqual("testsquad.envsafety/1", again["schema"])
        self.assertEqual(result["findings"], again["findings"])


# --------------------------------------------------------------------------
# chain 2 -- risk map feeds prioritisation
# --------------------------------------------------------------------------

class RiskMapPrioritisationTest(ChainCase):
    """risk_map decides what everything downstream looks at first. The
    manifest pins, per repository, which file that must be -- the fixtures'
    git history was generated specifically so the answer is deterministic.
    """

    def build(self, repo, since=""):
        require_repo(self, repo)
        subject = copy_repo(repo, self.tmp)
        if not os.path.isdir(os.path.join(subject, ".git")):
            fixture_unavailable(
                self, "examples/fixtures/{0}/.git is missing (the fixture's "
                      "git history is part of the fixture -- risk_map.py is "
                      "scored against it -- and is rebuilt by "
                      "examples/fixtures/regen_git_history.sh).".format(repo))
        proc = run_script("risk_map.py", subject, "--since", since)
        self.assertEqual(0, proc.returncode, proc.stderr)
        return json.loads(proc.stdout)

    def test_hottest_file_from_the_manifest_ranks_first(self):
        """Ranking is the entire product of this script. If the file the
        manifest calls riskiest is not first, every downstream budget is spent
        on the wrong file -- and no unit test of the scoring function would
        notice, because each component would still be computed correctly."""
        manifest = self.manifest()
        repos = manifest.get("repos", {})
        for repo in REPOS:
            expected = (repos.get(repo) or {}).get("riskiest_file_expected")
            if not expected:
                fixture_unavailable(
                    self, "examples/fixtures/MANIFEST.json is malformed: "
                          "repos.{0} has no riskiest_file_expected, so the "
                          "ranking this test exists to check has no ground "
                          "truth.".format(repo))
            with self.subTest(repo=repo):
                result = self.build(repo)
                self.assertTrue(result["files"],
                                "no files scored for " + repo)
                self.assertEqual(
                    expected, result["files"][0]["path"],
                    "{0}: manifest says {1!r} is riskiest, risk_map ranked "
                    "{2!r} first".format(repo, expected,
                                         result["files"][0]["path"]))
                self.assertEqual(5, result["files"][0]["likelihood"],
                                 "the hot file is pinned at L5 by the "
                                 "generated history")

    def test_every_why_is_present_and_repeats_no_signal(self):
        """Google's bug-prediction work ranked correctly and changed nothing,
        because nobody could see why a file was flagged. So `why` is not
        decoration; an empty one makes the row unusable.

        The duplicate check is a regression guard: a real bug produced
        "37% churned; 5 bug-fixes; 37% churned", which reads as two
        independent signals agreeing when it is one signal printed twice --
        actively misleading, worse than no explanation.
        """
        churn = re.compile(r"churned|rewritten|lines? changed|"
                           r"added in this window")
        for repo in REPOS:
            with self.subTest(repo=repo):
                result = self.build(repo)
                for entry in result["files"]:
                    why = entry["why"]
                    self.assertTrue(why.strip(),
                                    "empty why for " + entry["path"])
                    parts = [p.strip() for p in why.split(";")]
                    self.assertEqual(
                        len(parts), len(set(parts)),
                        "{0}: repeated clause in why={1!r}"
                        .format(entry["path"], why))
                    for name, pattern in (("churn", churn),
                                          ("bug-fix", re.compile("bug-fix")),
                                          ("authors",
                                           re.compile("contributors"))):
                        hits = [p for p in parts if pattern.search(p)]
                        self.assertLessEqual(
                            len(hits), 1,
                            "{0}: the {1} signal appears {2} times in "
                            "why={3!r}".format(entry["path"], name,
                                               len(hits), why))

    def test_populated_window_warns_about_nothing(self):
        """A warning on a healthy repo is noise, and noise trains people to
        skip the warnings that matter."""
        for repo in REPOS:
            with self.subTest(repo=repo):
                result = self.build(repo)
                self.assertEqual([], result["warnings"])
                self.assertGreater(result["window"]["commits_analyzed"], 0)

    def test_empty_window_warns_instead_of_reporting_low_risk(self):
        """A window that catches no commits produces an empty map, which on
        screen is indistinguishable from "this codebase is low risk". That
        confusion is the failure mode worth a test: absence of data must be
        announced, not quietly rendered as good news."""
        result = self.build("python-pkg", since="2099-01-01")
        self.assertEqual([], result["files"])
        self.assertTrue(result["warnings"],
                        "an empty window produced an empty map and said "
                        "nothing -- it reads as 'no risk here'")
        self.assertIn("absence of risk", " ".join(result["warnings"]),
                      "the warning must distinguish no-data from no-risk")

    def test_paths_are_repo_relative_so_coverage_can_be_joined_to_them(self):
        """The seam: risk_map ranks files, normalize_coverage measures files,
        and a report joins the two. That join only works if both sides spell a
        path the same way. risk_map must emit repo-relative POSIX paths --
        exactly what normalize_coverage's `normalize_path` produces -- and not
        absolute ones."""
        result = self.build("python-pkg")
        top = result["files"][0]["path"]
        self.assertFalse(os.path.isabs(top))
        self.assertNotIn("\\", top)

        import normalize_coverage as nc
        report = json.dumps({"files": {top: {
            "summary": {"num_statements": 10, "covered_lines": 5,
                        "num_branches": 0, "covered_branches": 0},
            "missing_lines": [1, 2, 3, 4, 5]}}})
        normalized = nc.normalize(report, "coveragepy")
        self.assertEqual(
            [top], [f["path"] for f in normalized["files"]],
            "risk_map and normalize_coverage disagree on how to spell a path, "
            "so no report can join them")


# --------------------------------------------------------------------------
# chain 3 -- coverage normalizes to hand-computed totals
# --------------------------------------------------------------------------

LCOV = """TN:
SF:src/a.ts
DA:1,1
DA:2,0
DA:3,4
BRDA:2,0,0,1
BRDA:2,0,1,-
end_of_record
SF:src/b.ts
DA:1,0
end_of_record
"""

COBERTURA = """<?xml version="1.0" ?>
<coverage version="1">
  <packages><package name="shop"><classes>
    <class filename="shop/a.py" name="a">
      <lines>
        <line number="1" hits="1"/>
        <line number="2" hits="0"/>
        <line number="3" hits="2" branch="true" condition-coverage="50% (1/2)"/>
      </lines>
    </class>
  </classes></package></packages>
</coverage>
"""

COVERAGEPY = json.dumps({
    "meta": {"version": "7.0"},
    "files": {
        "shop/a.py": {
            "summary": {"num_statements": 4, "covered_lines": 3,
                        "num_branches": 2, "covered_branches": 2},
            "missing_lines": [7],
        },
    },
})

# A file the tool opened and found nothing measurable in -- a module of pure
# imports, say. Not zero coverage: no coverage.
COVERAGEPY_NOTHING_MEASURED = json.dumps({
    "files": {
        "shop/__init__.py": {
            "summary": {"num_statements": 0, "covered_lines": 0,
                        "num_branches": 0, "covered_branches": 0},
            "missing_lines": [],
        },
    },
})


class CoverageRoundTripTest(ChainCase):
    """Three ecosystems' coverage formats have to land on one shape, because
    the gate that reads them is written once. The numbers below are computed
    by hand from the literals above, so a parser regression changes the
    result rather than the expectation."""

    def normalize_file(self, name, text, *extra):
        path = self.write(name, text)
        proc = run_script("normalize_coverage.py", path, *extra)
        self.assertEqual(0, proc.returncode, proc.stderr)
        return json.loads(proc.stdout)

    def test_lcov_totals_are_hand_computable(self):
        """src/a.ts: DA hits 1, 0, 4 -> 3 lines found, 2 hit, line 2 uncovered.
        src/b.ts: one line, never hit -> 1 found, 0 hit.
        Totals: 4 found, 2 hit = 50.0%.
        Branches, from BRDA on a.ts: 2 found, 1 taken (the `-` was never
        reached) = 50.0%.
        LF/LH summary rows are deliberately absent: DA is authoritative."""
        result = self.normalize_file("lcov.info", LCOV)
        self.assertEqual("lcov", result["source_format"])
        totals = result["totals"]
        self.assertEqual(2, totals["files"])
        self.assertEqual(4, totals["lines_found"])
        self.assertEqual(2, totals["lines_hit"])
        self.assertEqual(50.0, totals["line_pct"])
        self.assertEqual(2, totals["branches_found"])
        self.assertEqual(1, totals["branches_hit"])
        self.assertEqual(50.0, totals["branch_pct"])
        by_path = {f["path"]: f for f in result["files"]}
        self.assertEqual([2], by_path["src/a.ts"]["uncovered_lines"])
        self.assertEqual([1], by_path["src/b.ts"]["uncovered_lines"])
        self.assertIsNone(by_path["src/b.ts"]["branch_pct"],
                          "b.ts has no branches at all; 0% would read as a gap")

    def test_cobertura_totals_are_hand_computable(self):
        """shop/a.py: 3 line rows, hits 1/0/2 -> 3 found, 2 hit = 66.67%.
        Line 3 carries condition-coverage '50% (1/2)' -> 2 branches found,
        1 hit = 50.0%. Line 2 is the only uncovered line."""
        result = self.normalize_file("coverage.xml", COBERTURA)
        self.assertEqual("cobertura", result["source_format"])
        totals = result["totals"]
        self.assertEqual(1, totals["files"])
        self.assertEqual(3, totals["lines_found"])
        self.assertEqual(2, totals["lines_hit"])
        self.assertEqual(66.67, totals["line_pct"])
        self.assertEqual(2, totals["branches_found"])
        self.assertEqual(1, totals["branches_hit"])
        self.assertEqual(50.0, totals["branch_pct"])
        self.assertEqual([2], result["files"][0]["uncovered_lines"])

    def test_coveragepy_totals_are_hand_computable(self):
        """4 statements, 3 covered = 75.0%; 2 branches, both taken = 100.0%."""
        result = self.normalize_file("coverage.json", COVERAGEPY)
        self.assertEqual("coveragepy", result["source_format"])
        totals = result["totals"]
        self.assertEqual(4, totals["lines_found"])
        self.assertEqual(3, totals["lines_hit"])
        self.assertEqual(75.0, totals["line_pct"])
        self.assertEqual(100.0, totals["branch_pct"])
        self.assertEqual([7], result["files"][0]["uncovered_lines"])

    def test_every_format_lands_on_the_same_schema_key(self):
        """One shape, whatever came in. If the schema string drifted per
        parser, the single consumer downstream would have to special-case the
        producer -- which is exactly the coupling normalizing was for."""
        for name, text in (("lcov.info", LCOV),
                           ("coverage.xml", COBERTURA),
                           ("coverage.json", COVERAGEPY)):
            with self.subTest(report=name):
                result = self.normalize_file(name, text)
                self.assertEqual("testsquad.coverage/1", result["schema"])
                self.assertEqual(
                    sorted(["files", "lines_found", "lines_hit",
                            "branches_found", "branches_hit",
                            "line_pct", "branch_pct"]),
                    sorted(result["totals"].keys()))

    def test_nothing_measured_is_null_not_zero(self):
        """A product promise, and the one most easily lost to a well-meaning
        `or 0`. "We measured this file and none of it is covered" and "there
        was nothing here to measure" are different facts, and the second one
        rendered as 0% sends a team to write tests for an empty module.

        Asserted after a real JSON round trip, because the value has to still
        be `null` once it has crossed the process boundary into whatever reads
        it next -- which is where a stringified "None" or a coerced 0 would
        appear."""
        result = self.normalize_file("coverage.json",
                                     COVERAGEPY_NOTHING_MEASURED)
        totals = result["totals"]
        self.assertEqual(0, totals["lines_found"])
        self.assertIsNone(totals["line_pct"])
        self.assertIsNone(totals["branch_pct"])
        self.assertIn('"line_pct": null', json.dumps(result, indent=2))
        self.assertIsNone(result["files"][0]["line_pct"])

    def test_unmeasured_renders_as_words_not_a_number(self):
        """The same promise at the far end of the chain: pr_comment turns the
        normalized total into prose. `None` must become "not measured" there,
        or the distinction preserved by the parser is thrown away by the
        renderer -- the classic seam failure."""
        import pr_comment
        result = self.normalize_file("coverage.json",
                                     COVERAGEPY_NOTHING_MEASURED)
        body = pr_comment.render({"coverage": {
            "line_pct": result["totals"]["line_pct"],
            "branch_pct": result["totals"]["branch_pct"],
        }})
        self.assertIn("not measured", body)
        self.assertNotIn("0.0%", body)


# --------------------------------------------------------------------------
# chain 4 -- flake smells against the manifest
# --------------------------------------------------------------------------

_RULE_IN_WHY = re.compile(r"flake_smells rule:\s*([a-z0-9-]+)\s*\((block|warn|info)\)")


class FlakeSmellsManifestTest(ChainCase):
    """The highest-value test in this file.

    `MANIFEST.json` is the ground truth the whole eval suite is scored
    against: for every planted weak test it names the rule `flake_smells.py`
    is expected to fire, and for every good test it asserts silence. Those two
    halves measure the two ways a static checker fails -- missing the thing it
    exists to catch, and crying wolf on correct code. A rule rename, a regex
    that stops matching one language, or a new heuristic that starts flagging
    clean tests all show up here and nowhere else.

    Each failure names the manifest id, so the first line of the failure tells
    you which planted item regressed.
    """

    def expectations(self, repo):
        manifest = self.manifest()
        out = []
        for item in manifest.get("items", []):
            if item.get("repo") != repo or item.get("kind") != "weak-test":
                continue
            match = _RULE_IN_WHY.search(item.get("why", ""))
            if not match:
                continue
            out.append((item["id"], item["file"], item["line"],
                        match.group(1), match.group(2)))
        return out

    def scan(self, repo, relpaths):
        require_repo(self, repo)
        subject = copy_repo(repo, self.tmp, name=repo)
        paths = []
        for rel in relpaths:
            full = os.path.join(subject, rel)
            if not os.path.exists(full):
                fixture_unavailable(
                    self, "examples/fixtures/{0}/{1} is missing (MANIFEST.json "
                          "declares it, so the fixture and the ground truth "
                          "disagree).".format(repo, rel))
            paths.append(full)
        proc = run_script("flake_smells.py", *paths)
        self.assertNotEqual(2, proc.returncode, proc.stderr)
        result = json.loads(proc.stdout)
        found = {}
        for entry in result["files"]:
            rel = os.path.relpath(entry["path"], subject).replace("\\", "/")
            for finding in entry["findings"]:
                found.setdefault(rel, []).append(
                    (finding["line"], finding["rule"], finding["severity"]))
        return result, found

    def test_every_manifest_weak_test_is_flagged_with_its_predicted_rule(self):
        """Missing one of these means a planted defect walked past the Tier 0
        gate. The line is allowed one line of slack because the Java rules
        anchor on the `@Test` annotation while the manifest records the method
        declaration below it (see the dedicated test further down); the rule
        itself is matched exactly, because the rule is what the fix text, the
        severity and the gate threshold all hang off."""
        for repo in REPOS:
            expectations = self.expectations(repo)
            if not expectations:
                fixture_unavailable(
                    self, "examples/fixtures/MANIFEST.json declares no "
                          "weak-test items for {0}, so this test would pass "
                          "without checking a single planted defect."
                          .format(repo))
            files = sorted({e[1] for e in expectations})
            _, found = self.scan(repo, files)
            for item_id, relfile, line, rule, severity in expectations:
                with self.subTest(item=item_id):
                    flagged = found.get(relfile, [])
                    matching = [f for f in flagged if f[1] == rule]
                    self.assertTrue(
                        matching,
                        "{0}: manifest predicts rule {1!r} at {2}:{3}, "
                        "flake_smells reported {4!r} for that file"
                        .format(item_id, rule, relfile, line,
                                sorted({f[1] for f in flagged})))
                    self.assertTrue(
                        any(abs(f[0] - line) <= 1 for f in matching),
                        "{0}: rule {1!r} fired on {2} at line(s) {3}, but the "
                        "manifest plants it at line {4}"
                        .format(item_id, rule, relfile,
                                [f[0] for f in matching], line))
                    self.assertTrue(
                        any(f[2] == severity for f in matching),
                        "{0}: manifest predicts severity {1!r} for rule {2!r}, "
                        "flake_smells reported {3}"
                        .format(item_id, severity, rule,
                                sorted({f[2] for f in matching})))

    def test_good_test_files_produce_zero_findings(self):
        """The false-alarm half. These files are deliberately written to look
        interesting -- retries, floating point, seeded randomness, exception
        paths -- while being completely deterministic. One finding here and
        the gate starts blocking correct work, which is how a gate gets turned
        off for good."""
        manifest = self.manifest()
        for repo in REPOS:
            good = sorted({i["file"] for i in manifest.get("items", [])
                           if i.get("repo") == repo
                           and i.get("kind") == "good-test"})
            if not good:
                fixture_unavailable(
                    self, "examples/fixtures/MANIFEST.json declares no "
                          "good-test items for {0}, so the false-alarm half "
                          "of this check has nothing to measure."
                          .format(repo))
            with self.subTest(repo=repo):
                result, found = self.scan(repo, good)
                self.assertEqual(
                    0, result["summary"]["findings"],
                    "{0}: good-test files must be silent, got {1}"
                    .format(repo, found))
                self.assertEqual(
                    0, proc_returncode_floor(result),
                    "clean files must not breach the block threshold")
                self.assertEqual(len(good), result["summary"]["files_scanned"],
                                 "a good-test file was skipped rather than "
                                 "scanned and found clean")

    def test_weak_test_files_fail_the_gate(self):
        """Detecting a weak test and then exiting 0 would be a gate that
        reports and never blocks. The exit code is the only part CI reads."""
        for repo in REPOS:
            expectations = self.expectations(repo)
            if not expectations:
                fixture_unavailable(
                    self, "examples/fixtures/MANIFEST.json declares no "
                          "weak-test items for {0}, so this test would pass "
                          "without checking a single planted defect."
                          .format(repo))
            blocking = sorted({e[1] for e in expectations if e[4] == "block"})
            if not blocking:
                continue
            with self.subTest(repo=repo):
                subject = copy_repo(repo, self.tmp, name=repo + "-gate")
                paths = [os.path.join(subject, b) for b in blocking]
                proc = run_script("flake_smells.py", *paths)
                self.assertEqual(1, proc.returncode,
                                 "{0}: block-severity findings must exit 1"
                                 .format(repo))

    def test_java_no_assertion_line_matches_the_manifest_exactly(self):
        """The manifest and the scanner must anchor a finding on the same
        line, because anything joining manifest ids to findings by
        (file, line) drops the item silently when they drift.

        JAVA-WEAK-01 is recorded in MANIFEST.json at line 14 -- the `@Test`
        annotation, not the `void splitBillRuns()` declaration under it --
        because a finding anchors on the earliest line of the declaration,
        annotations and decorators included. The manifest states that
        convention in its own `why`, and Python behaves the same way on a
        decorated test. Measured 2026-11-12: flake_smells reports
        no-assertion at 14, snippet `@Test`. The other four Java weak-test
        items (21, 27, 33, 42) agree with the manifest too.

        An earlier version of this docstring called that a live defect --
        "the manifest says 15" -- and said the test was marked
        expectedFailure. Neither was ever true: `git show` puts 14 in the
        manifest at the initial commit, and no decorator was ever applied.
        A test that advertises itself as known-broken while passing teaches
        readers to ignore the label, so the label is gone.
        """
        expectations = [e for e in self.expectations("java-min")
                        if e[3] == "no-assertion"]
        if not expectations:
            fixture_unavailable(
                self, "examples/fixtures/MANIFEST.json declares no java-min "
                      "no-assertion weak-test item, which is the ground truth "
                      "this test is written against.")
        item_id, relfile, line, rule, _severity = expectations[0]
        _, found = self.scan("java-min", [relfile])
        lines = [f[0] for f in found.get(relfile, []) if f[1] == rule]
        self.assertIn(line, lines,
                      "{0}: manifest line {1}, reported {2}"
                      .format(item_id, line, lines))


def proc_returncode_floor(result):
    """What `flake_smells.py --fail-on block` would exit with for this report.

    Recomputed here from the summary rather than imported, so a change to the
    script's own `breaches()` cannot make this assertion agree with itself.
    """
    return 1 if result["summary"]["by_severity"]["block"] else 0


# --------------------------------------------------------------------------
# chain 5 -- the PR comment never leaks
# --------------------------------------------------------------------------

LEAKY_DATA = {
    "health": {"verdict": "GREEN", "flaky_count": 0},
    "coverage": {"line_pct": 81.0, "base_line_pct": 80.0},
    "gates": [{"name": "diff coverage", "target": "80%", "actual": "81%",
               "met": True}],
    "findings": [
        {"severity": "S1", "title": "SQL built by string concatenation",
         "evidence": "code-read", "file": "shop/db.py", "line": 41},
        {"severity": "S2", "title": "possible race in the retry loop",
         "evidence": "suspicion", "file": "shop/retry.py", "line": 12},
        {"severity": "S2", "title": "discount applied before tax",
         "evidence": "reproduced", "file": "shop/invoice.py", "line": 29,
         "repro": "pytest tests/test_invoice.py -k discount"},
    ],
    # A field nobody meant to render, of the shape a renderer must never echo.
    "env_note": "STRIPE_SECRET_KEY=sk_live_FIXTUREONLYNeverARealKey4242",
}


class PrCommentLeakTest(ChainCase):
    """A PR comment can be public, and on a fork it is attacker-adjacent. Two
    things must never reach it: an unproven finding (a guess published under
    the tool's name is worse than silence) and anything secret-shaped. Both
    are properties of the rendered text, which is why they are tested on the
    output of the real script rather than on the filter function."""

    def render(self, data):
        path = self.write("data.json", json.dumps(data))
        proc = run_script("pr_comment.py", "--data", path)
        self.assertEqual(0, proc.returncode, proc.stderr)
        return proc.stdout

    def test_unconfirmed_findings_do_not_appear(self):
        """`code-read` and `suspicion` are both "I think so". Only
        `reproduced` and `observed` have been demonstrated. The titles are
        distinctive strings, so their absence is checkable without knowing the
        layout."""
        body = self.render(LEAKY_DATA)
        self.assertNotIn("SQL built by string concatenation", body)
        self.assertNotIn("possible race in the retry loop", body)
        self.assertNotIn("code-read", body)
        self.assertNotIn("suspicion", body)
        self.assertIn("discount applied before tax", body,
                      "the reproduced finding must still be published, or "
                      "the filter is just a mute button")

    def test_the_withheld_count_is_disclosed(self):
        """Silently dropping findings makes the comment a liar by omission:
        a reader would conclude nothing else was found. Say the number, never
        the content. Two of the three findings above are unconfirmed."""
        body = self.render(LEAKY_DATA)
        self.assertIn("2 unconfirmed item(s) withheld", body)

    def test_no_secret_shaped_value_is_echoed(self):
        """Checked in 8-character windows for the same reason as the safety
        gate: a renderer that grew a debug line printing part of the blob
        would still be a leak."""
        body = self.render(LEAKY_DATA)
        assert_no_secret(self, body,
                         "sk_live_FIXTUREONLYNeverARealKey4242",
                         "the secret-shaped env_note")
        self.assertNotIn("env_note", body)

    def test_a_blob_of_only_unconfirmed_findings_publishes_none_of_them(self):
        """The degenerate case, where the pressure to show *something* is
        highest: every finding is a guess. The right output is a comment that
        says it found nothing publishable and names the count."""
        data = {"findings": [
            {"severity": "S1", "title": "guess one", "evidence": "code-read"},
            {"severity": "S2", "title": "guess two", "evidence": "suspicion"},
        ]}
        body = self.render(data)
        self.assertNotIn("guess one", body)
        self.assertNotIn("guess two", body)
        self.assertIn("2 unconfirmed item(s) withheld", body)

    def test_red_health_withholds_the_coverage_numbers(self):
        """Does a RED verdict actually withhold coverage downstream?

        Measured 2026-11-12: yes. `render()` gates the verdict line *and*
        the coverage block on RED (scripts/pr_comment.py), so the comment
        says the figures are withheld and then does not print them. Saying
        "withheld" above a table reading "81.0% | 80.0% | +1.0 pts" would be
        worse than printing nothing, because the number is the part people
        read.

        The assertion discriminates: the same payload with a GREEN verdict
        renders "81.0%", and with RED it does not.

        An earlier version of this docstring called the ungated block a live
        defect and said the test was marked expectedFailure. Neither was ever
        true: the gate is in `pr_comment.py` at the initial commit and no
        decorator was ever applied.
        """
        data = dict(LEAKY_DATA)
        data["health"] = {"verdict": "RED", "flaky_count": 3}
        body = self.render(data)
        self.assertIn("withheld", body)
        self.assertNotIn("81.0%", body)
        self.assertNotIn("+1.0 pts", body)

    def test_red_health_at_least_says_the_numbers_are_untrustworthy(self):
        """The other half of the same promise: a RED comment must say *why*
        the figures are missing, not merely omit them. Kept separate from the
        withholding test above so each half fails on its own terms."""
        data = dict(LEAKY_DATA)
        data["health"] = {"verdict": "RED", "flaky_count": 3}
        body = self.render(data)
        self.assertIn("The test suite is not healthy.", body)
        self.assertIn("Suite health: **RED**", body)


# --------------------------------------------------------------------------
# chain 6 -- the session preamble stays cheap
# --------------------------------------------------------------------------

CONVENTION_LINE = (
    "Tests must run with no database: mock the data layer completely, and "
    "`pytest -q` must pass with no services started at all, on a laptop with "
    "the network switched off entirely, which is a very long rule indeed"
)


class SessionContextBudgetTest(ChainCase):
    """This text is prepended to every request in the repository, exactly like
    CLAUDE.md. It is charged per request, forever, so its cost is a property
    worth a test rather than a habit worth trusting -- and the caps only work
    if they are enforced against hostile input, which is what a `.testing/`
    full of over-long rows is."""

    def populate(self, root, conventions, blockers):
        testing = os.path.join(root, ".testing")
        os.makedirs(testing, exist_ok=True)
        with open(os.path.join(testing, "profile.json"), "w",
                  encoding="utf-8") as fh:
            json.dump({"stack": "Python / pytest / coverage",
                       "commands": {"test": "pytest -q"}}, fh)
        with open(os.path.join(testing, "health-baseline.json"), "w",
                  encoding="utf-8") as fh:
            json.dump({"verdict": "AMBER", "generated_at":
                       "2026-11-10T00:00:00+00:00"}, fh)
        with open(os.path.join(testing, "conventions.md"), "w",
                  encoding="utf-8") as fh:
            fh.write("# Conventions\n\n")
            fh.write("".join("{0}. {1}\n".format(i + 1, c)
                             for i, c in enumerate(conventions)))
        with open(os.path.join(testing, "known-issues.md"), "w",
                  encoding="utf-8") as fh:
            fh.write("# Known issues\n\n")
            fh.write("".join("- {0}\n".format(b) for b in blockers))
        return testing

    def test_output_fits_the_per_request_budget(self):
        """Budget, derived from the caps in the script rather than measured:

            1 intro line                              ~  45 chars
            1 stack line                              ~  60
            1 health line                             ~ 100
            1 'conventions here:' label               ~  25
            MAX_CONVENTIONS (5) x (4 + MAX_LINE_CHARS 140)   = 720
            1 'known issues' label                    ~  50
            MAX_BLOCKERS (3) x 144                            = 432
            1 trailer line                            ~  70
                                                        ------
                                                        ~1502

        2000 characters is that with room to breathe, and roughly 500 tokens.
        Fed ten conventions and six blockers of 200 characters each -- more
        than any of the caps allow -- so this fails loudly if a cap is removed
        rather than only when someone writes a verbose file."""
        root = os.path.join(self.tmp, "repo")
        os.makedirs(root)
        self.populate(root,
                      conventions=[CONVENTION_LINE] * 10,
                      blockers=["KI-{0:02d} ".format(i) + CONVENTION_LINE
                                for i in range(6)])
        proc = run_script("session_context.py", root, env=onboard(root))
        self.assertEqual(0, proc.returncode, proc.stderr)
        body = proc.stdout
        self.assertTrue(body.strip(), "a populated .testing/ printed nothing")
        self.assertLessEqual(
            len(body), 2000,
            "the session preamble is {0} characters and is charged on every "
            "request in this repo".format(len(body)))

    def test_no_unfilled_placeholder_reaches_the_session(self):
        """`.testing/` is seeded from templates full of
        `{{RULE, one line, specific and checkable}}` rows. Any that survive
        into the preamble are presented to the model as though they were real
        conventions -- noise with the authority of repository policy, on every
        single request.

        Run against the real templates in `templates/knowledge/`, because that
        is the exact text a freshly started repository has."""
        root = os.path.join(self.tmp, "repo")
        testing = os.path.join(root, ".testing")
        os.makedirs(testing)
        templates = os.path.join(ROOT, "templates", "knowledge")
        require(self, templates, "templates/knowledge")
        copied = 0
        for name in ("conventions.md", "known-issues.md"):
            src = os.path.join(templates, name)
            if os.path.exists(src):
                shutil.copy(src, os.path.join(testing, name))
                copied += 1
        if not copied:
            fixture_unavailable(
                self, "templates/knowledge/ is missing conventions.md and "
                      "known-issues.md, the exact text a freshly started "
                      "repository gets.")
        with open(os.path.join(testing, "profile.json"), "w",
                  encoding="utf-8") as fh:
            json.dump({"stack": "TypeScript / Vitest"}, fh)

        proc = run_script("session_context.py", root, env=onboard(root))
        self.assertEqual(0, proc.returncode, proc.stderr)
        self.assertNotRegex(
            proc.stdout, r"\{\{[^}]*\}\}",
            "an unfilled template placeholder reached the session preamble")
        self.assertLessEqual(len(proc.stdout), 2000)

    def test_a_repo_without_testing_is_untouched(self):
        """The hook ships enabled, so it runs in every repository, including
        ones that have never heard of this plugin. Printing anything there --
        or exiting non-zero -- would make the plugin something people
        uninstall."""
        root = os.path.join(self.tmp, "bare")
        os.makedirs(root)
        proc = run_script("session_context.py", root, env=onboard(root))
        self.assertEqual(0, proc.returncode)
        self.assertEqual("", proc.stdout)


# --------------------------------------------------------------------------
# chain 7 -- schema compatibility across scripts
# --------------------------------------------------------------------------

class SchemaCompatibilityTest(ChainCase):
    """Every JSON-emitting script stamps its output with a `schema` string,
    and everything downstream -- the dashboard, the gate, the report
    templates, a user's own jq -- keys off it. A silent rename is the cheapest
    possible way to break the entire chain, and not one unit test would
    notice, because each script would still agree with itself.

    The expected values are written out as literals here on purpose. Reading
    them from the module's own SCHEMA constant would make this test assert
    that a constant equals itself."""

    EXPECTED = {
        "arid_nodes": "testsquad.aridnodes/1",
        "env_safety_check": "testsquad.envsafety/1",
        "flake_smells": "testsquad.flakesmells/1",
        "normalize_coverage": "testsquad.coverage/1",
        "risk_map": "testsquad.riskmap/1",
    }

    def test_every_emitter_declares_its_documented_schema(self):
        for module_name, expected in sorted(self.EXPECTED.items()):
            with self.subTest(module=module_name):
                try:
                    module = __import__(module_name)
                except ImportError as exc:
                    self.fail(
                        "scripts/{0}.py could not be imported ({1}). This is "
                        "production code, not a fixture: a consumer keyed on "
                        "its schema string has nothing to read, and skipping "
                        "here would report that as a clean run."
                        .format(module_name, exc))
                self.assertEqual(
                    expected, getattr(module, "SCHEMA", None),
                    "{0}.SCHEMA changed; every consumer keyed on the old "
                    "value is now silently reading an unknown document"
                    .format(module_name))

    def test_the_schema_key_survives_into_real_output(self):
        """A constant in the module is worth nothing if the builder forgets to
        put it in the payload. These are real runs, over real inputs, checked
        on the serialized bytes."""
        require_repo(self, "python-pkg")
        subject = copy_repo("python-pkg", self.tmp)
        out = os.path.join(self.tmp, "out")
        os.makedirs(out)
        report = self.write("coverage.json", COVERAGEPY)

        runs = (
            ("env_safety_check.py", (subject,), "testsquad.envsafety/1"),
            ("risk_map.py", (subject, "--since", ""), "testsquad.riskmap/1"),
            ("normalize_coverage.py", (report,), "testsquad.coverage/1"),
            ("flake_smells.py", (os.path.join(subject, "tests"),),
             "testsquad.flakesmells/1"),
            ("arid_nodes.py", (os.path.join(subject, "shop"),),
             "testsquad.aridnodes/1"),
        )
        for script, args, expected in runs:
            require(self, os.path.join(SCRIPTS, script),
                    "the production script this chain step runs")
            with self.subTest(script=script):
                proc = run_script(script, *args)
                self.assertNotEqual(2, proc.returncode, proc.stderr)
                payload = json.loads(proc.stdout)
                self.assertEqual(expected, payload.get("schema"))

    def test_the_fixture_manifest_declares_its_own_schema(self):
        """The ground truth is part of the contract too: these tests read
        `testsquad.fixtures/1` and would quietly stop checking anything if the
        manifest's shape changed under them."""
        manifest = self.manifest()
        self.assertEqual("testsquad.fixtures/1", manifest["schema"])
        self.assertEqual(71, len(manifest["items"]),
                         "the manifest is the scoreboard; an item count "
                         "change means ground truth moved")
        ids = [i["id"] for i in manifest["items"]]
        self.assertEqual(len(ids), len(set(ids)), "duplicate manifest id")
        for item in manifest["items"]:
            self.assertIn(item["repo"], REPOS)
            for key in ("id", "kind", "file", "expected_verdict"):
                self.assertIn(key, item, "item {0} is missing {1!r}"
                              .format(item.get("id"), key))


# --------------------------------------------------------------------------
# chain 8 -- the whole chain, on one fixture, leaving no trace
# --------------------------------------------------------------------------

class WholeChainTest(ChainCase):
    """Run the chain end to end against python-pkg in a scratch copy: every
    step's exit code checked, every step's output parsed, and the output of
    one step fed to the next where the product does so.

    The second half is the one no unit test covers at all: afterwards the
    repository's git worktree must be byte-for-byte what it was. These scripts
    run inside other people's repositories, often unattended from a hook. A
    stray report file, a `git add`, a rewritten config -- any of it turns a
    diagnostic tool into something that shows up in someone's `git status`
    during an unrelated review. Every artifact is written to a separate output
    directory for exactly that reason.
    """

    def setUp(self):
        super().setUp()
        require_repo(self, "python-pkg")
        self.repo = copy_repo("python-pkg", self.tmp)
        if not os.path.isdir(os.path.join(self.repo, ".git")):
            fixture_unavailable(
                self, "examples/fixtures/python-pkg/.git is missing (rebuild "
                      "it with examples/fixtures/regen_git_history.sh).")
        self.out = os.path.join(self.tmp, "artifacts")
        os.makedirs(self.out)

    def artifact(self, name):
        return os.path.join(self.out, name)

    def test_the_chain_runs_in_order_and_each_link_feeds_the_next(self):
        # 1. The gate. python-pkg is SAFE, so the chain is permitted to run.
        gate = run_script("env_safety_check.py", self.repo,
                          "--out", self.artifact("safety.json"))
        self.assertEqual(0, gate.returncode,
                         "python-pkg is SAFE; a non-zero exit stops the chain")
        safety = read_json_file(self.artifact("safety.json"))
        self.assertEqual("SAFE", safety["verdict"])

        # 2. Suite health stands in for the health baseline the real flow
        #    computes; flake_smells is the Tier 0 input to it.
        smells = run_script("flake_smells.py", os.path.join(self.repo, "tests"),
                            "--out", self.artifact("smells.json"))
        self.assertEqual(1, smells.returncode,
                         "python-pkg plants block-severity weak tests; the "
                         "gate must fail")
        smell_report = read_json_file(self.artifact("smells.json"))
        self.assertGreater(smell_report["summary"]["findings"], 0)

        # 3. Risk map. Its top path is what the next step measures.
        risk = run_script("risk_map.py", self.repo, "--since", "",
                          "--out", self.artifact("risk.json"))
        self.assertEqual(0, risk.returncode, risk.stderr)
        risk_report = read_json_file(self.artifact("risk.json"))
        self.assertEqual("shop/invoice.py", risk_report["files"][0]["path"])

        # 4. Coverage, reported for the file risk_map put first. 6 of 8
        #    statements covered = 75.0%, by hand.
        hot = risk_report["files"][0]["path"]
        raw = json.dumps({"files": {hot: {
            "summary": {"num_statements": 8, "covered_lines": 6,
                        "num_branches": 4, "covered_branches": 3},
            "missing_lines": [29, 30]}}})
        with open(self.artifact("coverage.json"), "w", encoding="utf-8") as fh:
            fh.write(raw)
        cov = run_script("normalize_coverage.py", self.artifact("coverage.json"),
                         "--out", self.artifact("coverage.norm.json"))
        self.assertEqual(0, cov.returncode, cov.stderr)
        normalized = read_json_file(self.artifact("coverage.norm.json"))
        self.assertEqual(75.0, normalized["totals"]["line_pct"])
        self.assertEqual(75.0, normalized["totals"]["branch_pct"])
        self.assertEqual(
            hot, normalized["files"][0]["path"],
            "the coverage row no longer joins to the risk_map row")

        # 5. The PR comment, assembled from the three reports above. This is
        #    the seam that matters most: the renderer reads the keys the
        #    earlier scripts actually emit, not keys invented for a unit test.
        blob = {
            "health": {"verdict": "AMBER",
                       "flaky_count": smell_report["summary"]
                       ["by_category"]["flake"]},
            "coverage": {"line_pct": normalized["totals"]["line_pct"],
                         "base_line_pct": 70.0,
                         "branch_pct": normalized["totals"]["branch_pct"],
                         "uncovered_changed_lines":
                             len(normalized["files"][0]["uncovered_lines"])},
            "gates": [{"name": "safety", "target": "SAFE",
                       "actual": safety["verdict"],
                       "met": safety["verdict"] == "SAFE"}],
            "findings": [
                {"severity": "S2", "title": "weak test accepted by the suite",
                 "evidence": "observed", "file": "tests/test_weak.py",
                 "line": 8},
            ],
        }
        with open(self.artifact("pr.json"), "w", encoding="utf-8") as fh:
            json.dump(blob, fh)
        comment = run_script("pr_comment.py", "--data", self.artifact("pr.json"),
                             "--out", self.artifact("comment.md"))
        self.assertEqual(0, comment.returncode, comment.stderr)
        body = read_text(self.artifact("comment.md"))
        self.assertIn("<!-- testsquad:pr-comment -->", body,
                      "the marker is how CI finds and updates its own comment")
        self.assertIn("75.0%", body)
        self.assertIn("2 changed line(s) are not covered", body)

        # 6. Session context, from a .testing/ built out of those artifacts.
        home = os.path.join(self.tmp, "workspace")
        testing = os.path.join(home, ".testing")
        os.makedirs(testing)
        shutil.copy(self.artifact("coverage.norm.json"),
                    os.path.join(testing, "coverage.json"))
        with open(os.path.join(testing, "health-baseline.json"), "w",
                  encoding="utf-8") as fh:
            json.dump({"verdict": "AMBER",
                       "generated_at": "2026-11-11T00:00:00+00:00"}, fh)
        with open(os.path.join(testing, "profile.json"), "w",
                  encoding="utf-8") as fh:
            json.dump({"stack": "Python / pytest",
                       "commands": {"test": "pytest -q"}}, fh)
        #    The consent record is the last link in the chain and it lives
        #    OUTSIDE the repository, so it has to be seeded here exactly the
        #    way /testsquad:start seeds it. Without it the hook is correctly
        #    silent -- which is what this step used to be asserting against,
        #    reading a working consent gate as a broken health fixture.
        context = run_script("session_context.py", home, env=onboard(home))
        self.assertEqual(0, context.returncode, context.stderr)
        self.assertIn("AMBER", context.stdout)
        self.assertLessEqual(len(context.stdout), 2000)
        self.assertEqual(
            "", context.stderr,
            "the hook read everything it was given; it must not also be "
            "complaining on stderr")

        # 6b. The same call without the consent record. The preamble is
        #     withheld, and that is correct -- but an empty stdout alone is
        #     indistinguishable from "this repo is fine", which is how the
        #     failure above was misdiagnosed for a whole handoff. The reason
        #     must be on stderr.
        ungated = run_script("session_context.py", home)
        self.assertEqual(0, ungated.returncode,
                         "a hook that breaks session start is worse than a "
                         "quiet one; the exit code stays 0")
        self.assertEqual("", ungated.stdout,
                         "the consent gate must still withhold the preamble")
        self.assertNotEqual(
            "", ungated.stderr,
            "silent stdout AND silent stderr makes a withheld preamble look "
            "exactly like a repo with nothing to report")
        self.assertIn("onboard", ungated.stderr.lower())

        # 6c. And the case the whole gate exists to keep honest: the health
        #     baseline is there and unreadable. Saying nothing about it would
        #     render exactly like a suite with no problems.
        with open(os.path.join(testing, "health-baseline.json"), "w",
                  encoding="utf-8") as fh:
            fh.write("{ truncated mid-write")
        broken = run_script("session_context.py", home, env=onboard(home))
        self.assertEqual(0, broken.returncode, broken.stderr)
        self.assertNotEqual("", broken.stdout,
                            "an unreadable health baseline must not silence "
                            "the whole preamble")
        self.assertIn("could not be read", broken.stdout)
        self.assertNotIn("AMBER", broken.stdout,
                         "it must not report a verdict it could not read")
        self.assertIn("health-baseline.json", broken.stderr)

        # 6d. And the sharpest case of all: the hook crashes. It must still
        #     exit 0 -- a hook that breaks session start is worse than a quiet
        #     one -- but exit 0 with nothing on either stream is a crash
        #     wearing the costume of a clean repo. A profile.json holding a
        #     JSON array is a real, documented way to make build() raise.
        with open(os.path.join(testing, "profile.json"), "w",
                  encoding="utf-8") as fh:
            fh.write('["python"]')
        crashed = run_script("session_context.py", home, env=onboard(home))
        self.assertEqual(0, crashed.returncode,
                         "the session must still start")
        self.assertEqual("", crashed.stdout)
        self.assertNotEqual(
            "", crashed.stderr,
            "a crash that prints nothing at all is indistinguishable from a "
            "repo with nothing to report")
        self.assertIn("AttributeError", crashed.stderr,
                      "the stderr note must name what actually went wrong")

    def test_the_repository_is_unchanged_after_the_whole_chain(self):
        """Nothing in this chain may dirty a user's repository: not an
        untracked report, not a staged file, not a moved HEAD. `git status
        --porcelain` is empty for a clean tree, so the expected value is the
        empty string, and HEAD must be the commit we started on."""
        before_head = git(self.repo, "rev-parse", "HEAD").stdout.strip()
        before_status = git(self.repo, "status", "--porcelain").stdout
        self.assertEqual("", before_status,
                         "the fixture copy was already dirty; the rest of "
                         "this assertion would prove nothing")

        for script, args in (
            ("env_safety_check.py", (self.repo,)),
            ("risk_map.py", (self.repo, "--since", "")),
            ("flake_smells.py", (os.path.join(self.repo, "tests"),)),
            ("arid_nodes.py", (os.path.join(self.repo, "shop"),)),
            ("session_context.py", (self.repo,)),
        ):
            require(self, os.path.join(SCRIPTS, script),
                    "the production script this chain step runs")
            proc = run_script(script, *args, cwd=self.repo)
            self.assertNotEqual(2, proc.returncode,
                                "{0} failed: {1}".format(script, proc.stderr))

        after_status = git(self.repo, "status", "--porcelain").stdout
        self.assertEqual(
            "", after_status,
            "the chain left changes in the worktree:\n" + after_status)
        self.assertEqual(before_head,
                         git(self.repo, "rev-parse", "HEAD").stdout.strip(),
                         "the chain moved HEAD")

    def test_the_fixture_itself_was_never_touched(self):
        """Belt and braces for the rule every test in this file depends on:
        the originals under examples/fixtures/ are read-only ground truth. If
        a future test forgets to copy, this catches it in the same run rather
        than in a confusing eval failure days later."""
        for repo in REPOS:
            path = os.path.join(FIXTURES, repo)
            require(self, os.path.join(path, ".git"),
                    "the fixture's git history, rebuilt by "
                    "examples/fixtures/regen_git_history.sh")
            with self.subTest(repo=repo):
                self.assertEqual(
                    "", git(path, "status", "--porcelain").stdout,
                    "examples/fixtures/{0} is dirty -- a test wrote to the "
                    "real fixture instead of a copy".format(repo))


# --------------------------------------------------------------------------
# chain 9 -- the policy itself: a missing fixture must never read as success
# --------------------------------------------------------------------------

def probe_case():
    """A throwaway TestCase to point the fixture helpers at.

    The helpers end the *calling* case, so they cannot be called on `self` --
    the test would die of the thing it is trying to observe. The probe absorbs
    the failure and lets us read the message.

    `unittest.TestCase("run")` rather than a subclass of our own: a subclass
    carrying a `runTest` method is collected by the loader as a test that
    asserts nothing and can never fail, which is precisely the thing this
    project exists to stop shipping.
    """
    return unittest.TestCase("run")


class FixtureAvailabilityPolicyTest(unittest.TestCase):
    """`require()` and `manifest()` used to call `skipTest()` when a fixture
    was absent. That is how `examples/fixtures/node-ts/.env` -- the file whose
    entire purpose is to make the safety gate say UNSAFE -- stayed missing
    from every clone while this suite reported success.

    These tests pin the replacement policy in both directions: FAIL by
    default, and skip *audibly* under the documented opt-in. Without them the
    old behaviour could come back as a one-word edit, and the symptom would
    again be a quiet green run.
    """

    def setUp(self):
        self.probe = probe_case()
        self.override(None)

    def override(self, value):
        """Set (or clear) the escape hatch for this test only."""
        previous = os.environ.get(ALLOW_MISSING_FIXTURES)

        def restore():
            if previous is None:
                os.environ.pop(ALLOW_MISSING_FIXTURES, None)
            else:
                os.environ[ALLOW_MISSING_FIXTURES] = previous

        self.addCleanup(restore)
        if value is None:
            os.environ.pop(ALLOW_MISSING_FIXTURES, None)
        else:
            os.environ[ALLOW_MISSING_FIXTURES] = value

    def expect_failure(self, call, *args):
        """Call a helper that must FAIL, and hand back the failure message.

        `assertRaises(AssertionError)` is not enough here. If the helper
        *skips* instead of failing, the SkipTest escapes the context manager
        and unittest records this very test as skipped -- the regression test
        for an unwanted skip, disappearing into a skip. That is not a
        theory: reverting `case.fail(...)` to `case.skipTest(...)` in
        `fixture_unavailable()` left this class reporting "OK (skipped=10)".
        """
        label = getattr(call, "__name__", str(call))
        try:
            call(*args)
        except unittest.SkipTest as exc:
            self.fail("{0}() skipped instead of failing with the opt-in "
                      "unset. A skip is silence: {1}".format(label, exc))
        except AssertionError as exc:
            return str(exc)
        self.fail("{0}() neither failed nor skipped on an unavailable "
                  "fixture".format(label))

    def absent(self):
        return os.path.join(self.probe_dir(), "node-ts", ".env")

    def probe_dir(self):
        root = tempfile.mkdtemp(prefix="testsquad-policy-")
        self.addCleanup(shutil.rmtree, root, True)
        os.makedirs(os.path.join(root, "node-ts"))
        return root

    # -- fail by default ---------------------------------------------------

    def test_a_missing_fixture_fails_and_names_the_path(self):
        """The whole point. A reader must get the path, not a puzzle."""
        # A path under the real fixtures tree that is not there, so the
        # message is rendered exactly as a reader would see it for a genuinely
        # absent .env -- repo-relative, not an absolute temp path.
        missing = os.path.join(FIXTURES, "node-ts", ".env.absent-in-this-test")
        message = self.expect_failure(
            require, self.probe, missing, "MANIFEST item NODE-CONFIG-01")
        self.assertIn(
            "examples/fixtures/node-ts/.env.absent-in-this-test is missing",
            message, "the failure must name the path verbatim, relative to "
                     "the repository root")
        self.assertIn("NODE-CONFIG-01", message,
                      "the failure must name the manifest item")

    def test_a_missing_fixture_points_at_the_escape_hatch(self):
        """A hard failure with no way forward gets worked around badly. The
        message has to say the hatch exists *and* that CI may not use it."""
        message = self.expect_failure(
            require, self.probe, self.absent(), "the planted .env")
        self.assertIn(ALLOW_MISSING_FIXTURES, message)
        self.assertIn("never", message.lower())
        self.assertIn("CI", message)

    def test_an_empty_fixture_file_is_malformed_and_fails(self):
        """Present but empty is the same defect wearing a filename: the
        scanner reads it, finds nothing, and reports a clean bill of health."""
        path = os.path.join(self.probe_dir(), "node-ts", ".env")
        with open(path, "w", encoding="utf-8"):
            pass
        message = self.expect_failure(
            require, self.probe, path, "the planted .env")
        self.assertIn("is malformed", message)
        self.assertIn("node-ts/.env", message)

    def test_the_node_env_is_a_declared_requirement_of_its_repository(self):
        """`require_repo` must check the file, not just the directory. The
        directory was always there; the .env was the thing that was not."""
        required = [rel for rel, _ in REQUIRED_FIXTURE_FILES.get("node-ts", ())]
        self.assertIn(".env", required,
                      "node-ts/.env (NODE-CONFIG-01) must be required "
                      "explicitly, or a fixture repository missing it reads "
                      "as available")

    def test_require_repo_fails_when_only_the_declared_file_is_missing(self):
        """End to end through the wiring an integration test actually calls,
        with a fixtures root whose node-ts/ exists but is empty."""
        fake_root = self.probe_dir()
        real_fixtures = globals()["FIXTURES"]
        globals()["FIXTURES"] = fake_root
        self.addCleanup(globals().__setitem__, "FIXTURES", real_fixtures)
        message = self.expect_failure(require_repo, self.probe, "node-ts")
        self.assertIn("node-ts/.env is missing", message)

    def test_a_malformed_manifest_schema_fails_rather_than_whispering(self):
        """A schema mismatch means the ground truth on disk is not the ground
        truth these tests read. That is a loud problem; it used to skip."""
        path = os.path.join(self.probe_dir(), "MANIFEST.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"schema": "testsquad.fixtures/99", "items": []}, fh)
        real = globals()["MANIFEST_PATH"]
        globals()["MANIFEST_PATH"] = path
        self.addCleanup(globals().__setitem__, "MANIFEST_PATH", real)
        message = self.expect_failure(ChainCase("run").manifest)
        self.assertIn("is malformed", message)
        self.assertIn("testsquad.fixtures/99", message)

    # -- the escape hatch --------------------------------------------------

    def test_the_override_restores_the_skip(self):
        self.override("1")
        with self.assertRaises(unittest.SkipTest):
            require(self.probe, self.absent(), "the planted .env")

    def test_an_overridden_skip_names_the_variable_and_the_fixture(self):
        """A silent skip under an override is the same defect wearing a
        permission slip. The reason has to be distinguishable from an
        ordinary skip at a glance."""
        self.override("1")
        with self.assertRaises(unittest.SkipTest) as caught:
            require(self.probe, self.absent(), "MANIFEST item NODE-CONFIG-01")
        reason = str(caught.exception)
        self.assertIn(ALLOW_MISSING_FIXTURES, reason,
                      "an overridden skip must name the variable that caused "
                      "it, or it reads as an ordinary skip")
        self.assertIn("node-ts/.env is missing", reason,
                      "an overridden skip must still name what is missing")
        self.assertIn("never be set in CI", reason)

    def test_only_the_documented_value_engages_the_override(self):
        """Fail closed. Anything other than the documented `1` -- including
        the unset case, `0`, and an empty string -- leaves the gate on."""
        for value in (None, "", "0", "no", "false"):
            with self.subTest(value=value):
                self.override(value)
                self.assertFalse(
                    allowing_missing_fixtures(),
                    "{0}={1!r} must not disable the fixture gate"
                    .format(ALLOW_MISSING_FIXTURES, value))
                self.expect_failure(
                    require, self.probe, self.absent(), "the planted .env")

    # -- it cannot quietly revert ------------------------------------------

    def test_every_skip_in_this_module_goes_through_the_opt_in(self):
        """Source-level guard. One `self.skipTest("fixture absent")` added
        back anywhere in this file would restore the exact silence that hid
        NODE-CONFIG-01, and no behavioural test would notice."""
        source = read_text(os.path.abspath(__file__))
        try:
            tree = ast.parse(source)
        except RecursionError:
            # Not protection: the input is this module's own committed source,
            # and a raise here is already a loud red error. What the handler
            # buys is the sentence. A bare RecursionError traceback under this
            # test's name is about parser depth and says nothing about skips,
            # so a reader can triage it as unrelated infrastructure noise and
            # never notice that the skip guard below went unevaluated.
            self.fail("this module's own source is too deeply nested for "
                      "ast.parse; the source-level guard below did NOT run, "
                      "so nothing checked that every skip still goes through "
                      "fixture_unavailable()")
        allowed = None
        for node in ast.walk(tree):
            if (isinstance(node, ast.FunctionDef)
                    and node.name == "fixture_unavailable"):
                allowed = (node.lineno, node.end_lineno)
        self.assertIsNotNone(
            allowed, "fixture_unavailable() is gone; the fixture policy is "
                     "no longer enforced anywhere in this module")
        offenders = []
        for node in ast.walk(tree):
            name = None
            if isinstance(node, ast.Call):
                func = node.func
                if isinstance(func, ast.Attribute):
                    name = func.attr
                elif isinstance(func, ast.Name):
                    name = func.id
            elif isinstance(node, ast.Raise) and node.exc is not None:
                exc = node.exc
                target = exc.func if isinstance(exc, ast.Call) else exc
                if isinstance(target, ast.Attribute):
                    name = target.attr
                elif isinstance(target, ast.Name):
                    name = target.id
            if name in ("skipTest", "SkipTest", "skip", "skipIf",
                        "skipUnless"):
                if not allowed[0] <= node.lineno <= allowed[1]:
                    offenders.append("line {0}: {1}".format(node.lineno, name))
        self.assertEqual(
            [], offenders,
            "a skip outside fixture_unavailable() is silence that looks like "
            "success; route it through the helper so it fails by default and "
            "says {0} when it does not: {1}"
            .format(ALLOW_MISSING_FIXTURES, offenders))

    def test_an_unparseable_own_source_says_the_guard_did_not_run(self):
        """Covers the RecursionError handler above, which no committed source
        can reach -- on the 3.9.6 floor ast.parse goes straight from accepting
        a very deep expression to SIGSEGV, so the handler cannot be reached by
        depth there at all. A handler nobody can prove is a handler nobody
        should trust, so it is forced to fire here instead."""
        with mock.patch.object(ast, "parse",
                               side_effect=RecursionError("forced")):
            with self.assertRaises(self.failureException) as caught:
                self.test_every_skip_in_this_module_goes_through_the_opt_in()
        message = str(caught.exception)
        self.assertIn("did NOT run", message)
        self.assertNotIn("silence that looks like success", message,
                         "the parse never happened, so the real assertion "
                         "below it must not have reported a verdict")

    def test_no_ci_workflow_sets_the_override(self):
        """The hatch is for a human running the suite next to another agent.
        A CI job that sets it turns every missing fixture back into a green
        run -- which is the failure mode this whole policy exists to end.

        Both locations are checked. `.github/workflows/` holds this repo's
        own CI (`ci.yml`, added 2026-11-12); `templates/ci/` holds what gets
        generated into a *user's* repo (`testsquad-pr.yml`). Neither may set
        the override, and a new workflow in either place is covered without
        anyone remembering to add it here.
        """
        candidates = []
        for directory in (os.path.join(ROOT, "templates", "ci"),
                          os.path.join(ROOT, ".github", "workflows")):
            if not os.path.isdir(directory):
                continue
            for name in sorted(os.listdir(directory)):
                if name.endswith((".yml", ".yaml")):
                    candidates.append(os.path.join(directory, name))
        self.assertTrue(
            candidates,
            "no CI workflow found in templates/ci/ or .github/workflows/; "
            "this check cannot confirm the override is unset, and reporting "
            "that as a pass would be the silence this module exists to end")
        for path in candidates:
            with self.subTest(workflow=fixture_rel(path)):
                self.assertNotIn(
                    ALLOW_MISSING_FIXTURES, read_text(path),
                    "{0} mentions {1}. CI must never set it: in CI there is "
                    "no other agent landing a fixture, so an absent fixture "
                    "is a hard error, not a race."
                    .format(fixture_rel(path), ALLOW_MISSING_FIXTURES))


if __name__ == "__main__":
    unittest.main()
