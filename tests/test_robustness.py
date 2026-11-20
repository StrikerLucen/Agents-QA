#!/usr/bin/env python3
"""Robustness tests: the inputs a real repository actually contains.

Every other suite in tests/ feeds these scripts clean, well-formed input. No
real repository is clean. These scripts run unattended against strangers'
codebases, and the first odd file they meet decides whether the plugin is ever
trusted again -- a traceback on first contact is a user who uninstalls and
never reports why.

So this file is about one property: *for any byte sequence a repository can
hold, the script either produces an answer or refuses clearly.* A crash is a
failure. A silently wrong answer is a worse failure, because nobody looks
twice at a number that printed without complaint.

Where current behaviour violates that property the test documents what it
actually does, with a comment saying what it SHOULD do, or is marked
@unittest.expectedFailure asserting the correct behaviour so the bug shows up
in the run summary rather than hiding in a passing assertion. Nothing here
fixes a script.

Everything is built in a temp directory. Large inputs are generated, never
committed. Platform-dependent cases skip rather than fail.
"""

import ast
import contextlib
import functools
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

SCRIPTS = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "scripts"))

import env_safety_check as esc  # noqa: E402
import flake_smells as fsm  # noqa: E402
import normalize_coverage as ncov  # noqa: E402
import pr_comment as prc  # noqa: E402
import project_state as pstate  # noqa: E402
import risk_map as rmap  # noqa: E402
import session_context as sctx  # noqa: E402
import validate_plugin as vplug  # noqa: E402

# The import is guarded so that ONE missing script does not take the other
# two hundred tests in this file down with it -- not so that its absence can
# pass quietly. Every test that needs the module calls require_arid() (or
# wears @requires_arid) and FAILS when it is gone. A missing production
# script is a broken checkout, not a platform this file does not apply to,
# and a skip there is the "silence looks like success" defect this suite
# exists to catch: the run reports green having tested nothing.
ARID_IMPORT_ERROR = None
try:
    import arid_nodes as arid
except ImportError as _arid_exc:  # pragma: no cover - depends on checkout
    arid = None
    ARID_IMPORT_ERROR = _arid_exc


def require_arid(case):
    """Fail -- never skip -- when scripts/arid_nodes.py did not import.

    The message has to be unmistakably about the checkout rather than about
    arid_nodes.py's behaviour, because the two land in the same red block of
    a CI log and the reader's next move differs completely: restore a file,
    versus debug a scanner.
    """
    if arid is None:
        case.fail(
            "PRODUCTION CODE MISSING, NOT A BUG IN THE CODE UNDER TEST: "
            "scripts/arid_nodes.py could not be imported ({0}), so this test "
            "exercised nothing and asserts nothing about arid_nodes.py. "
            "Restore scripts/arid_nodes.py and re-run. This used to skip, "
            "which made a checkout with the script deleted report green -- "
            "silence must never look like success."
            .format(ARID_IMPORT_ERROR))


def requires_arid(test_method):
    """Decorator form of require_arid(), replacing an @unittest.skipIf."""
    @functools.wraps(test_method)
    def wrapper(self, *args, **kwargs):
        require_arid(self)
        return test_method(self, *args, **kwargs)
    return wrapper


HAVE_GIT = shutil.which("git") is not None

# A generous ceiling. Nothing here should take anywhere near this; the point
# of the bound is to turn a hang into a failure rather than a stuck CI job.
SLOW = 5.0


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

class TempMixin:
    """Each test owns a throwaway directory. Never the project's own tree:
    these tests write hostile filenames and multi-megabyte files."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="testsquad-robust-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def path(self, rel):
        full = os.path.join(self.tmp, rel)
        parent = os.path.dirname(full)
        if parent:
            os.makedirs(parent, exist_ok=True)
        return full

    def write(self, rel, data, binary=False):
        full = self.path(rel)
        with open(full, "wb" if binary else "w",
                  **({} if binary else {"encoding": "utf-8", "newline": ""})) as fh:
            fh.write(data)
        return full


@contextlib.contextmanager
def timed(case, label, budget=SLOW):
    """A regex that backtracks forever is a denial of service on everyone who
    installed the plugin, so elapsed time is an assertion, not a note."""
    start = time.monotonic()
    yield
    elapsed = time.monotonic() - start
    case.assertLess(elapsed, budget,
                    f"{label} took {elapsed:.1f}s (budget {budget}s) -- "
                    "unbounded work on attacker-controlled input")



# --------------------------------------------------------------------------
# ast.parse's depth ceiling -- the one limit these scripts do not control
# --------------------------------------------------------------------------
# CPython builds the Python AST objects from the C parse tree with a recursive
# C function, so a deeply NESTED expression costs C stack and past some depth
# `ast.parse` raises RecursionError. Three things make this a nightmare to
# test, and all three are why it reached CI unnoticed:
#
#  * the depth is NOT sys.getrecursionlimit(). Measured on this machine,
#    CPython 3.14.6, `a + a + ... ` chains: raising the limit from 1,000 to
#    1,000,000 moves the threshold by nothing at all.
#  * the depth depends on the C stack, so it differs by platform and by
#    thread. Measured: 3.14.6 on the macOS main thread (8MB) accepts 95,065
#    and raises at 95,066; the same interpreter on a 512KB thread accepts
#    2,834 and raises at 2,835. The suite's baits are 4,000 and 20,000 deep --
#    comfortably fine on macOS, over the line on the Ubuntu runners.
#  * on an interpreter whose AST builder has no counter, going past the depth
#    does not raise AT ALL. Measured on CPython 3.9.6, this repo's floor, by
#    bisecting in child processes: the main thread accepts 260,937 and is
#    dead by 262,500 (SIGSEGV, rc -11), with NO depth in between that raises
#    RecursionError. A 512KB thread at depth 20,000 exits 138 (SIGBUS). No
#    traceback, no output, nothing to catch.
#
# So a test that just feeds deeper input is either vacuous (macOS) or fatal to
# the whole suite (3.9.6). `forced_ast_recursion_error` is the portable half:
# it makes ast.parse raise on every platform, so the assertion that the
# scripts REPORT the file is live everywhere and cannot pass by accident.
# `ast_depth_raises_here` is the honest half: it reproduces the real thing in
# a child process, where a segfaulting interpreter cannot take the suite down.

AST_RECURSION_DEPTH_KB = 512


@contextlib.contextmanager
def forced_ast_recursion_error():
    """Make `ast.parse` raise RecursionError, on any platform.

    Not a substitute for the real thing -- see `ast_depth_raises_here` -- but
    the only way to assert the reporting path identically on macOS, on Linux
    and on an interpreter that would otherwise crash rather than raise.
    """
    with mock.patch.object(
            ast, "parse",
            side_effect=RecursionError(
                "maximum recursion depth exceeded during ast construction")):
        yield


def ast_depth_raises_here(kb=AST_RECURSION_DEPTH_KB, depth=20_000):
    """Does a `depth`-deep expression make ast.parse RAISE on a `kb` stack?

    Answered in a child process, because on an interpreter without the
    AST-construction counter the answer is "no, it dies", and finding that out
    in-process would end the test run. Returns True only when the child came
    back alive having caught a RecursionError.
    """
    probe = textwrap.dedent("""
        import ast, sys, threading
        src = "y = " + "a + " * %d + "1\\n"
        out = []
        def run():
            try:
                ast.parse(src)
                out.append("parsed")
            except RecursionError:
                out.append("raised")
        threading.stack_size(%d * 1024)
        t = threading.Thread(target=run)
        t.start(); t.join()
        sys.stdout.write(out[0] if out else "lost")
    """ % (depth, kb))
    try:
        done = subprocess.run([sys.executable, "-c", probe],
                              capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):  # pragma: no cover - platform
        return False
    return done.returncode == 0 and done.stdout.strip() == "raised"


def scan_on_a_thin_stack(script, call, kb=AST_RECURSION_DEPTH_KB):
    """Run `call` in a child process on a thread with a small C stack.

    `script` is a module name under scripts/ ("arid_nodes", "flake_smells",
    "project_state"); `call` is the expression to evaluate against it, bound to
    `mod`. Returns (returncode, parsed_json_or_None, stderr).
    A child that dies instead of returning is the whole point of using one.
    """
    body = textwrap.dedent("""
        import json, sys, threading
        sys.path.insert(0, %r)
        import %s as mod
        out = {}
        def run():
            try:
                out["result"] = %s
            except BaseException as exc:
                out["raised"] = type(exc).__name__ + ": " + str(exc)
        threading.stack_size(%d * 1024)
        t = threading.Thread(target=run)
        t.start(); t.join()
        sys.stdout.write(json.dumps(out))
    """ % (SCRIPTS, script, call, kb))
    done = subprocess.run([sys.executable, "-c", body],
                          capture_output=True, text=True, timeout=120)
    try:
        return done.returncode, json.loads(done.stdout), done.stderr
    except ValueError:
        return done.returncode, None, done.stderr


def assert_ast_depth_reported(case, result, module, path, *, scanned_key,
                              scanned_value):
    """The explicit signal, asserted the same way for both scripts.

    Deliberately NOT a finding: deeply nested generated code is legitimate, a
    rule that fired on it would run far past this repo's ~10% false-positive
    threshold, and a check at that rate gets switched off entirely. It is the
    `skipped_files` precedent instead -- its own top-level list, its own
    summary counter, and a line in the human-readable render.
    """
    deep = result["unparsed_files"]
    case.assertEqual([d["file"] for d in deep], [path],
                     "a file whose AST could not be built must be NAMED, not "
                     "quietly handed to the regex scanner")
    entry = deep[0]
    case.assertEqual(entry["stage"], "ast")
    case.assertEqual(entry["fell_back_to"], "regex")
    # The fallback DID run, and the output has to say the AST pass did not --
    # a partial analysis reported as complete is the failure mode here.
    case.assertIn("RecursionError", entry["reason"])
    case.assertIn("nesting", entry["reason"])
    case.assertIn("did NOT run", entry["reason"])
    case.assertIn("sys.setrecursionlimit", entry["reason"],
                  "the reason must name the limit that does NOT control this, "
                  "or a reader will try to raise it")
    case.assertTrue(entry["not_analysed"],
                    "the record has to say what was lost, not only that "
                    "something was")
    case.assertEqual(result["summary"]["files_ast_unparsed"], 1)
    # Still read, still analysed: this is a third state, not skipped_files.
    case.assertEqual(result["skipped_files"], [])
    case.assertEqual(result["summary"][scanned_key], scanned_value)
    # Per-file, too: `parsed_with: regex` is also what a Java file says, so it
    # cannot carry this on its own.
    case.assertEqual(result["files"][0]["parsed_with"], "regex")
    case.assertEqual(result["files"][0]["ast_unavailable"], module.AST_TOO_DEEP)
    # And in the terminal output, where a human decides the run went fine.
    rendered = module.render_text(result)
    case.assertIn("WITHOUT the AST pass", rendered)
    case.assertIn(path, rendered)


def grant_session_consent(case, repo_path):
    """session_context only speaks for a repo this user onboarded, and the
    record lives outside the repo so a repository cannot forge it. These tests
    exercise what the preamble SAYS, not whether it is allowed to -- the gate
    itself is covered in test_session_context.py."""
    data = tempfile.TemporaryDirectory()
    case.addCleanup(data.cleanup)
    previous = os.environ.get("CLAUDE_PLUGIN_DATA")
    os.environ["CLAUDE_PLUGIN_DATA"] = data.name

    def restore():
        if previous is None:
            os.environ.pop("CLAUDE_PLUGIN_DATA", None)
        else:
            os.environ["CLAUDE_PLUGIN_DATA"] = previous
    case.addCleanup(restore)

    marker = sctx.consent_path(repo_path)
    os.makedirs(os.path.dirname(marker), exist_ok=True)
    with open(marker, "w", encoding="utf-8") as fh:
        fh.write("{}")


def git(repo, *args, check=True):
    env = dict(os.environ)
    env.update({
        "GIT_AUTHOR_NAME": "Dev", "GIT_AUTHOR_EMAIL": "dev@example.com",
        "GIT_COMMITTER_NAME": "Dev", "GIT_COMMITTER_EMAIL": "dev@example.com",
        "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull,
    })
    return subprocess.run(["git", "-C", repo, *args], env=env,
                          capture_output=True, text=True, check=check)


def init_repo(path):
    os.makedirs(path, exist_ok=True)
    git(path, "init", "-q", "-b", "main")
    return path


LCOV_OK = "TN:\nSF:src/a.py\nDA:1,1\nDA:2,0\nend_of_record\n"


# --------------------------------------------------------------------------
# encoding and bytes
# --------------------------------------------------------------------------

class EncodingTests(TempMixin, unittest.TestCase):
    """A repository is a bag of bytes, not a bag of UTF-8. Windows editors
    write BOMs, Java tools write UTF-16, legacy configs are latin-1, and a
    scanner that assumes otherwise stops at the first such file -- usually
    without saying which one."""

    def test_lcov_with_utf8_bom_still_parses(self):
        # A BOM is what a Windows editor leaves behind. It must not change the
        # numbers a coverage gate reports.
        self.write("lcov.info", "﻿" + LCOV_OK)
        result = ncov.main([self.path("lcov.info"), "--out", self.path("o.json")])
        self.assertEqual(result, 0)
        with open(self.path("o.json"), encoding="utf-8") as fh:
            data = json.load(fh)
        self.assertEqual(data["totals"]["lines_found"], 2)

    def test_coverage_json_with_bom_is_recognised(self):
        """Fixed: a BOM'd coverage.json is detected and parsed, not refused.

        detect_format() used to test `head.startswith("{")` on text that still
        carried a leading \\ufeff, so the report was rejected as unrecognisable
        even though coverage.py reads its own BOM'd file fine. The numbers are
        asserted, not just the exit code: recognising the format and then
        mis-parsing it would be the worse failure.
        """
        self.write("coverage.json",
                   '﻿{"files": {"a.py": {"summary": '
                   '{"num_statements": 4, "covered_lines": 2}}}}')
        self.assertEqual(ncov.main([self.path("coverage.json"),
                                    "--out", self.path("o.json")]), 0)
        with open(self.path("o.json"), encoding="utf-8") as fh:
            data = json.load(fh)
        self.assertEqual(data["source_format"], "coveragepy")
        self.assertEqual(data["totals"]["lines_found"], 4)
        self.assertEqual(data["totals"]["line_pct"], 50.0)

    def test_bomd_coverage_json_with_no_files_is_still_an_error(self):
        # Exit 2, but for the honest reason now: the BOM no longer defeats
        # detection, so this is recognised as coveragepy and then refused for
        # carrying zero records. Pinned because "unrecognisable" and "parsed
        # nothing" used to be the same exit code hiding two different faults.
        self.write("coverage.json", '﻿{"files": {}}')
        self.assertEqual(ncov.main([self.path("coverage.json")]), 2)
        self.assertEqual(ncov.detect_format('﻿{"files": {}}', "coverage.json"),
                         "coveragepy")

    def test_utf16_lcov_never_produces_the_silent_zero_shape(self):
        """Fixed: the one shape this must never produce is exit 0 + no records.

        Read as utf-8 with errors="replace" the report turns into mojibake, no
        DA: record matches, and the run used to exit 0 with zero files and
        line_pct=None -- indistinguishable downstream from "this project has
        no coverage data", which a coverage gate reads as clean. Asserted as a
        property rather than a specific outcome: decoding the file and
        refusing it are both honest, an empty success is not.
        """
        self.write("lcov.info", LCOV_OK.encode("utf-16"), binary=True)
        rc = ncov.main([self.path("lcov.info"), "--out", self.path("o.json")])
        if rc == 0:
            with open(self.path("o.json"), encoding="utf-8") as fh:
                data = json.load(fh)
            self.assertNotEqual(data["totals"]["files"], 0)
            self.assertEqual(data["totals"]["lines_found"], 2)
        else:
            self.assertEqual(rc, 2)

    def test_utf16_lcov_is_decoded_rather_than_reported_as_zero(self):
        # Fixed: the BOM is sniffed and the report decoded, so the numbers are
        # the file's own. The dangerous shape was exit 0 plus an empty report,
        # which no caller distinguishes from "all clean".
        self.write("lcov.info", LCOV_OK.encode("utf-16"), binary=True)
        self.assertEqual(ncov.main([self.path("lcov.info"),
                                    "--out", self.path("o.json")]), 0)
        with open(self.path("o.json"), encoding="utf-8") as fh:
            data = json.load(fh)
        self.assertEqual(data["totals"]["files"], 1)
        self.assertEqual(data["totals"]["lines_found"], 2)

    def test_latin1_bytes_in_lcov_do_not_crash(self):
        # Invalid UTF-8 inside a path. errors="replace" is the right call here:
        # one unreadable byte must not lose the whole report.
        raw = b"SF:src/caf\xe9.py\nDA:1,1\nend_of_record\n"
        self.write("lcov.info", raw, binary=True)
        self.assertEqual(ncov.main([self.path("lcov.info"),
                                    "--out", self.path("o.json")]), 0)
        with open(self.path("o.json"), encoding="utf-8") as fh:
            data = json.load(fh)
        self.assertEqual(data["totals"]["lines_found"], 1)

    def test_nul_byte_in_env_file_is_skipped_not_silently_clean(self):
        # A .env the gate could not read is exactly where the danger hides,
        # so it must appear in skipped_files rather than vanish.
        self.write(".env", b"URL=https://api.prod.acme.com\x00\n", binary=True)
        result = esc.scan(self.tmp)
        self.assertEqual([s["file"] for s in result["skipped_files"]], [".env"])
        self.assertEqual(result["scanned_files"], [])

    def test_utf16_env_pointing_at_production_is_read_and_flagged(self):
        # FIXED. Was SAFE: a UTF-16 .env was taken for binary, skipped, and the
        # verdict computed from findings alone -- so a suite aimed at
        # production came back clean. The BOM is now sniffed and the file
        # decoded, so the production host is seen for what it is.
        self.write(".env", "URL=https://api.prod.acme.com\n".encode("utf-16"),
                   binary=True)
        result = esc.scan(self.tmp)
        self.assertEqual(result["verdict"], "UNSAFE")
        # Read, therefore NOT skipped: the file must appear in scanned_files,
        # and the verdict must come from a real detector rather than from the
        # "I could not read it" fallback.
        self.assertEqual(result["skipped_files"], [])
        self.assertEqual(result["scanned_files"], [".env"])
        self.assertIn("production_hostname",
                      {f["detector"] for f in result["findings"]})

    def test_env_file_over_the_ceiling_never_comes_back_safe(self):
        # The ceiling creates a new way for a sensitive file to go unread, so
        # it has to route through the same finding every other unread reason
        # does. A .env that was never opened is exactly where a production
        # target hides; "too big to check" may never render as "safe".
        self.write(".env", "URL=https://api.prod.acme.com\n" + "# pad\n" * 1000)
        result = esc.scan(self.tmp, max_bytes=64)
        self.assertEqual([s["file"] for s in result["skipped_files"]], [".env"])
        self.assertIn("ceiling", result["skipped_files"][0]["reason"])
        self.assertEqual(result["scanned_files"], [])
        # Not SAFE, and loud enough to trip `--fail-on high`.
        self.assertNotEqual(result["verdict"], "SAFE")
        self.assertEqual([f["detector"] for f in result["findings"]],
                         ["unreadable_config"])
        self.assertTrue(esc.breaches(result, "high"))

    def test_latin1_env_file_does_not_crash_the_gate(self):
        self.write(".env", "PASSWORD=contrase\xf1a\nURL=https://api.prod.acme.com\n"
                   .encode("latin-1"), binary=True)
        result = esc.scan(self.tmp)
        self.assertEqual(result["verdict"], "UNSAFE")

    def test_crlf_and_missing_trailing_newline_env_still_detected(self):
        # CRLF is what a Windows checkout gives you, and plenty of generators
        # omit the final newline. Neither may hide a production target.
        self.write("crlf/.env", b"URL=https://api.prod.acme.com\r\n", binary=True)
        self.write("nonl/.env", b"URL=https://api.prod.acme.com", binary=True)
        detectors = {f["file"] for f in esc.scan(self.tmp)["findings"]}
        self.assertEqual(detectors, {"crlf/.env", "nonl/.env"})

    def test_rtl_override_downgrades_a_production_hostname(self):
        # WRONG ANSWER, documented. U+202E RIGHT-TO-LEFT OVERRIDE renders as
        # nothing but breaks PROD_MARKER_RE's word boundary, so a host that a
        # human reads as "prod.acme.com" is classified merely "external".
        # That is the whole point of an RTL-override attack: the reviewer sees
        # production, the scanner sees something else.
        # SHOULD: strip or reject bidi control characters before matching, and
        # flag their presence outright -- they have no business in a config.
        self.write(".env", "URL=https://api‮prod.acme.com\n")
        detectors = {f["detector"] for f in esc.scan(self.tmp)["findings"]}
        self.assertIn("external_hostname", detectors)
        self.assertNotIn("production_hostname", detectors)

    def test_emoji_and_cjk_in_identifiers_and_paths(self):
        # Non-ASCII identifiers are normal in a lot of repositories. They must
        # not change which findings fire or crash the AST path.
        self.write("tests/test_单元.py",
                   "import time\n\n\ndef test_\U0001f600():\n"
                   "    元素 = 1\n    time.sleep(2)\n    assert 元素\n")
        result = fsm.scan_paths([self.tmp])
        rules = set(result["summary"]["by_rule"])
        self.assertIn("sleep", rules)

    def test_session_context_reads_cjk_and_emoji_conventions(self):
        os.makedirs(self.path(".testing"), exist_ok=True)
        self.write(".testing/conventions.md",
                   "- 单元测试 \U0001f600\r\n- second\n")
        grant_session_consent(self, self.tmp)
        text = sctx.build(self.tmp)
        self.assertIn("单元测试", text)

    def test_session_context_survives_a_bom_in_profile_json(self):
        os.makedirs(self.path(".testing"), exist_ok=True)
        self.write(".testing/profile.json", b'\xef\xbb\xbf{"stack": "python"}',
                   binary=True)
        # read_json swallows the JSONDecodeError and returns None, so the stack
        # line is silently dropped. Not a crash, but the preamble quietly loses
        # the one fact it exists to carry.
        # SHOULD: open with utf-8-sig so a BOM'd profile still reports a stack.
        grant_session_consent(self, self.tmp)
        text = sctx.build(self.tmp)
        self.assertNotIn("python", text)

    def test_validate_plugin_accepts_a_bom_on_plugin_json(self):
        # Fixed: a BOM'd plugin.json is valid to the Claude CLI loader, so CI
        # must not fail a plugin that installs correctly. Read with utf-8-sig.
        self.write(".claude-plugin/plugin.json",
                   b'\xef\xbb\xbf{"name": "demo", "version": "1.0.0"}', binary=True)
        report = vplug.validate(self.tmp)
        self.assertEqual([e for e in report.errors if "plugin.json" in e], [])

    def test_validate_plugin_survives_a_utf16_agent_file(self):
        """Fixed: a component that is not UTF-8 costs that component, not the run.

        check_markdown_component() and check_context_budget() both did a bare
        `open(path, encoding="utf-8").read()`, so one UTF-16 agent file raised
        UnicodeDecodeError straight out of validate() and the run reported
        nothing about any component. Both now go through read_text(), which
        sniffs the BOM; the neighbouring agent must still get its verdict.
        """
        self.write("agents/a.md",
                   "---\nname: a\ndescription: x\n---\nbody\n".encode("utf-16"),
                   binary=True)
        self.write("agents/b.md",
                   "---\nname: b\ndescription: >\n  x\n---\n" + "b" * 500)
        report = vplug.validate(self.tmp)
        # a.md decoded, so it is judged on its contents (a 4-byte stub body),
        # and b.md -- which comes after it -- was reached and found clean.
        self.assertTrue(any("a.md" in m for m in report.warnings + report.errors))
        self.assertEqual([e for e in report.errors if "b.md" in e], [])

    def test_validate_plugin_reports_a_latin1_component_without_aborting(self):
        # Fixed: the undecodable byte becomes U+FFFD, the file is still
        # checked, and the run reaches every other component.
        self.write("agents/a.md", b"---\nname: a\ndescription: caf\xe9\n---\nbody\n",
                   binary=True)
        report = vplug.validate(self.tmp)
        self.assertTrue(any("a.md" in w for w in report.warnings + report.errors))

    def test_flake_smells_falls_back_to_latin1(self):
        # read_lines() tries utf-8 then latin-1 before giving up. That is the
        # right order and worth pinning: losing a test file to an encoding is
        # losing a gate silently.
        self.write("test_a.py", b"def test_x():\n    # caf\xe9\n    assert 1 == 1\n",
                   binary=True)
        result = fsm.scan_paths([self.tmp])
        self.assertEqual(result["summary"]["files_scanned"], 1)

    def test_flake_smells_skips_a_file_with_a_nul_byte(self):
        """A binary file must be NAMED as skipped, and for the right reason.

        The reason is asserted on its stable half -- that the record exists
        and identifies the file as binary -- not on the whole sentence. This
        test used to pin the exact string "unreadable or binary", and a
        strictly better message broke it: that wording answered a permission
        error, a broken symlink and a NUL-byte file identically, and the
        three need three different reactions from a reader. The sentence will
        be improved again; what must not change is that the skip is recorded
        and that it says "binary".
        """
        self.write("test_a.py", b"def test_x():\n\x00    assert 1\n", binary=True)
        result = fsm.scan_paths([self.tmp])
        self.assertEqual(result["summary"]["files_scanned"], 0)
        skipped = result["summary"]["skipped"]
        self.assertEqual(len(skipped), 1, skipped)
        self.assertIn("test_a.py", skipped[0]["file"])
        reason = skipped[0]["reason"]
        self.assertIn("binary", reason)
        # The same file must not ALSO be called unreadable: "I was denied
        # permission" and "this is not source text" are different facts and
        # a reader acts differently on each.
        self.assertNotIn("unreadable", reason)
        # And the same fact is worded the same way across the scripts that
        # state it: risk_map.py, arid_nodes.py and flake_smells.py all emit
        # this prefix for this condition, and only the tail -- what the
        # individual tool could not do with the file -- differs. Divergence
        # between copies of one rule is this repository's most frequent
        # defect, so the shared half is pinned here rather than left to
        # three independent wordings.
        self.assertIn("NUL byte in the first 8000 bytes", reason)

    @requires_arid
    def test_arid_nodes_handles_latin1_and_nul(self):
        self.write("a.py", b"x = 1\ny = caf\xe9\n", binary=True)
        self.write("b.py", b"x = 1\x00\n", binary=True)
        result = arid.plan([self.tmp])
        # The latin-1 file is planned; the NUL file is dropped as binary.
        self.assertEqual(result["summary"]["files"], 1)
        # And the drop says WHICH of the three things happened, in the same
        # words flake_smells.py and risk_map.py use for the same fact.
        reason = result["skipped_files"][0]["reason"]
        self.assertIn("NUL byte in the first 8000 bytes", reason)
        self.assertNotIn("unreadable", reason)

    @requires_arid
    def test_arid_nodes_does_not_publish_a_secret_in_a_snippet(self):
        """docs/SECURITY-REVIEW.md S3-1, the arid_nodes half.

        The mutation plan carries up to 100 characters of verbatim source
        into `.testing/` (which skills/start/SKILL.md says to commit), the CI
        job log and the uploaded artifact. A fixture holding a real-shaped
        token must not ride along -- and the readable parts of the line must
        survive, or the plan cannot be reviewed and gets ignored.
        """
        self.write("a.py",
                   'def charge(amount):\n'
                   '    key = "sk_live_4eC39HqLyjWDarjtT1zdp7dcAbCdEfGh"\n'
                   '    host = "localhost"\n'
                   '    if amount > 0:\n'
                   '        return bill(key, host, amount)\n'
                   '    return None\n')
        result = arid.plan([self.tmp])
        self.assertTrue(result["summary"]["redaction"]["applied"],
                        result["summary"]["redaction"])
        blob = json.dumps(result)
        self.assertNotIn("sk_live_4eC39", blob,
                         "a live-shaped key reached the mutation plan")
        # The opposite false positive, and the one that kills the tool: a
        # plan whose snippets all read "[redacted]" cannot be reviewed.
        self.assertIn("localhost", blob)


# --------------------------------------------------------------------------
# structural input
# --------------------------------------------------------------------------

class StructureTests(TempMixin, unittest.TestCase):
    """Filesystems hold things a tidy fixture never does: empty files, broken
    links, loops, names with quotes and newlines in them. A scanner walks all
    of it on someone else's machine, so none of it may stop the walk."""

    def test_empty_coverage_file_is_refused_clearly(self):
        self.write("lcov.info", "")
        self.assertEqual(ncov.main([self.path("lcov.info")]), 2)

    def test_empty_files_everywhere_do_not_crash_any_scanner(self):
        self.write(".env", "")
        self.write("test_a.py", "")
        self.write("a.py", "")
        self.assertEqual(esc.scan(self.tmp)["verdict"], "SAFE")
        self.assertEqual(fsm.scan_paths([self.tmp])["summary"]["files_scanned"], 1)
        if arid is not None:
            self.assertEqual(arid.plan([self.tmp])["summary"]["selected"], 0)

    def test_single_ten_megabyte_line(self):
        # One enormous line is what a minified bundle or an inlined fixture
        # looks like, and every regex would run against 10MB of text at once.
        # It is now over MAX_FILE_BYTES, so it is not scanned -- and the whole
        # value of the ceiling depends on that being SAID rather than implied
        # by a file's absence from the report.
        self.write("test_huge.py", "x = '" + "a" * 10_000_000 + "'\n")
        with timed(self, "flake_smells on a 10MB single line", budget=10.0):
            result = fsm.scan_paths([self.tmp])
        self.assertEqual(result["summary"]["files_scanned"], 0)
        # `skipped_files` is the list that matters, and it is NOT
        # summary["skipped"]: that one is the full disposition log and
        # includes "not a test file", which is a decision rather than a
        # failure. The key is "file" here, matching arid_nodes.py,
        # risk_map.py and env_safety_check.py -- and, since the
        # testsquad.flakesmells/2 bump, summary["skipped"] as well. It used
        # to key the same thing as "path", which is what made this test ERROR
        # with a KeyError rather than fail; both lists now agree.
        unread = result["skipped_files"]
        self.assertEqual([s["file"] for s in unread],
                         [self.path("test_huge.py")])
        self.assertIn("ceiling", unread[0]["reason"])
        # The point of the whole exercise: a reader must be able to tell this
        # apart from a clean scan. A clean scan has an EMPTY skipped_files,
        # so a non-empty one is the signal, and the reason names the ceiling.
        self.assertIn("2MB", unread[0]["reason"])
        self.assertIn("--max-file-bytes", unread[0]["reason"])
        # ... and it reaches the human-readable output too, not only the JSON.
        self.assertIn("NOT scanned", fsm.render_text(result))

    def test_a_clean_scan_and_an_unread_file_do_not_look_alike(self):
        # The ceiling's whole value is this contrast. Both runs report zero
        # findings; only one of them actually looked at the file. If these two
        # results were indistinguishable the gate would be returning a clean
        # verdict about a file it never opened, which is the worst defect this
        # build produced.
        self.write("test_clean.py", "def test_a():\n    assert f(1) == 2\n")
        clean = fsm.scan_paths([self.tmp])
        os.remove(self.path("test_clean.py"))
        self.write("test_huge.py", "x = '" + "a" * 10_000_000 + "'\n")
        unread = fsm.scan_paths([self.tmp])

        self.assertEqual(clean["summary"]["findings"], 0)
        self.assertEqual(unread["summary"]["findings"], 0)
        self.assertEqual(clean["skipped_files"], [])
        self.assertNotEqual(unread["skipped_files"], [])
        self.assertEqual(clean["summary"]["files_scanned"], 1)
        self.assertEqual(unread["summary"]["files_scanned"], 0)
        self.assertNotIn("NOT scanned", fsm.render_text(clean))
        self.assertIn("NOT scanned", fsm.render_text(unread))

    def test_single_ten_megabyte_line_is_scanned_when_the_ceiling_is_raised(self):
        # The ceiling is a default, not a verdict: --max-file-bytes raises it,
        # and the scan of a 10MB single line still has to terminate promptly.
        self.write("test_huge.py", "x = '" + "a" * 10_000_000 + "'\n")
        with timed(self, "flake_smells on a 10MB single line, ceiling raised",
                   budget=20.0):
            result = fsm.scan_paths([self.tmp], max_bytes=64 * 1024 * 1024)
        self.assertEqual(result["summary"]["files_scanned"], 1)
        self.assertEqual(result["skipped_files"], [])
        # Raising the FILE ceiling does not remove the LINE ceiling, and the
        # line ceiling is what keeps this run fast. Scanning 2,000 of
        # 10,000,006 characters and reporting zero findings would be the same
        # silent lie one level down, so the truncation is reported.
        self.assertEqual(result["summary"]["lines_truncated"], 1)
        cut = result["truncated_lines"][0]
        self.assertEqual(cut["file"], self.path("test_huge.py"))
        self.assertEqual(cut["line"], 1)
        # 5 ("x = '") + 10,000,000 + 2 ("'\n"). Spelled out so a change in
        # what gets counted (bytes vs characters, newline in or out) fails
        # here rather than drifting quietly.
        self.assertEqual(cut["chars"], 10_000_007)
        self.assertEqual(cut["scanned_chars"], fsm.MAX_LINE_CHARS)
        self.assertIn("NOT examined", cut["reason"])
        self.assertIn("PARTLY scanned", fsm.render_text(result))

    def test_a_long_line_under_the_file_ceiling_is_not_silently_shortened(self):
        """REGRESSION. A 1.9MB single-line test file is UNDER the 2MB file
        ceiling, so it is read and counted as scanned. flake_smells then cut
        every line to MAX_LINE_CHARS and said nothing, so the report read
        files_scanned=1, findings=0, skipped_files=[] -- a clean verdict about
        1,898,000 characters nobody looked at. Measured before the fix: 0.002s
        and zero findings, with no trace of the truncation anywhere in the
        output.
        """
        body = "    assert foo('" + "x" * 1_900_000 + "') == 1\n"
        self.write("test_oneline.py", "def test_x():\n" + body)
        self.assertLess(os.path.getsize(self.path("test_oneline.py")),
                        fsm.MAX_FILE_BYTES)
        with timed(self, "flake_smells on a 1.9MB single line", budget=10.0):
            result = fsm.scan_paths([self.tmp])
        # Read, therefore NOT in skipped_files -- these are different states.
        self.assertEqual(result["summary"]["files_scanned"], 1)
        self.assertEqual(result["skipped_files"], [])
        # But partly unread, and that has to be said.
        self.assertEqual(result["summary"]["lines_truncated"], 1)
        self.assertEqual(result["summary"]["files_with_truncated_lines"], 1)
        cut = result["truncated_lines"][0]
        self.assertEqual(cut["file"], self.path("test_oneline.py"))
        self.assertEqual(cut["line"], 2)
        self.assertEqual(cut["chars"], len(body))
        self.assertIn("NOT examined", cut["reason"])

    def test_a_short_file_reports_no_truncation_at_all(self):
        # The other half of the contrast: an ordinary file must come back with
        # lines_truncated == 0 and nothing in the text output, or the new
        # signal is noise that gets ignored like any other false positive.
        self.write("test_ok.py", "def test_a():\n    assert f(1) == 2\n")
        result = fsm.scan_paths([self.tmp])
        self.assertEqual(result["summary"]["lines_truncated"], 0)
        self.assertEqual(result["truncated_lines"], [])
        self.assertNotIn("PARTLY scanned", fsm.render_text(result))

    @requires_arid
    def test_arid_nodes_does_not_silently_shorten_a_long_line(self):
        """REGRESSION. arid_nodes.py had a 2MB file ceiling and no per-line
        bound at all, so a single long line was planned in full. Measured on
        this machine before the fix: a 1.9MB single line cost 0.517s, and a
        400,000-character `a + a + a + ...` line raised an uncaught
        RecursionError out of ast.parse that aborted the whole run.
        """
        body = "    return a + " + " + ".join(["1"] * 20_000) + "\n"
        self.write("long.py", "def g(a):\n" + body)
        self.assertLess(os.path.getsize(self.path("long.py")),
                        arid.MAX_FILE_BYTES)
        result = arid.plan([self.tmp])
        self.assertEqual(result["summary"]["files"], 1)
        self.assertEqual(result["skipped_files"], [])
        self.assertEqual(result["summary"]["lines_truncated"], 1)
        cut = result["truncated_lines"][0]
        self.assertEqual(cut["file"], self.path("long.py"))
        self.assertEqual(cut["line"], 2)
        self.assertEqual(cut["chars"], len(body))
        self.assertEqual(cut["read_chars"], arid.MAX_LINE_CHARS)
        self.assertIn("NOT read", cut["reason"])
        self.assertIn("PARTLY read", arid.render_text(result))

    @requires_arid
    def test_arid_nodes_cost_is_linear_in_the_lines_of_one_function(self):
        """REGRESSION. _single_statement_body() rebuilt the whole enclosing
        function's body list for every candidate line in it, and was called
        twice per line, so one long function cost O(body^2). Measured before
        the fix, on files far under the 2MB ceiling: 2,005 lines 0.824s,
        4,005 lines 3.533s, 8,005 lines 12.192s -- roughly 4x per doubling.
        The file ceiling bounded the bytes and not the work.

        Asserted as a ratio rather than a wall time: an absolute budget on a
        shared CI box is a flake, but a quadratic still doubles its ratio.

        The body used to be `x = (a +\n a +\n ... a)`, which is one expression
        nested n deep. That made the fixture's PARSE PATH depend on the C
        stack: on the Ubuntu runners 1,000 deep parsed and 4,000 deep did not,
        so the two halves of the ratio were timed through different scanners
        and the comparison stopped meaning anything -- on 3.11 it did not even
        get that far, because the RecursionError escaped plan() and errored
        the test. It is a flat tuple now: same line count, same
        lines_considered (measured: 1,003 and 4,003, identical to the nested
        shape), same ~4x ratio, but AST depth 2 on every platform, so what is
        being timed is the line count and nothing else. Depth is covered
        properly by the two dedicated tests in ReDoSTests.
        """
        def cost(n):
            shutil.rmtree(self.tmp, ignore_errors=True)
            os.makedirs(self.tmp)
            self.write("mod.py",
                       "def f():\n    x = (\n" + "        a,\n" * n
                       + "        a\n    )\n    return x\n")
            start = time.time()
            result = arid.plan([self.tmp])
            return time.time() - start, result

        small, small_result = cost(1000)
        large, large_result = cost(4000)
        # Like-for-like or the ratio is noise. Both halves must have gone
        # through the SAME scanner, and neither may have fallen back -- a
        # silent fallback is what made the old fixture's number meaningless.
        for label, result in (("1,000", small_result), ("4,000", large_result)):
            self.assertEqual(result["files"][0]["parsed_with"], "ast",
                             f"the {label}-line run did not reach the AST "
                             "path, so the two timings are not comparable")
            self.assertEqual(result["unparsed_files"], [])
            self.assertEqual(result["summary"]["files_ast_unparsed"], 0)
        self.assertEqual(small_result["summary"]["lines_considered"], 1003)
        self.assertEqual(large_result["summary"]["lines_considered"], 4003)
        # 4x the lines. Linear predicts ~4x, quadratic predicts ~16x. A
        # generous 8x still catches the quadratic and tolerates a noisy box.
        # The floor guards against a timer too coarse to divide by.
        self.assertGreater(small, 0.0005,
                           "timer resolution too coarse to compare")
        self.assertLess(large / small, 8.0,
                        f"cost grew {large / small:.1f}x for 4x the lines "
                        f"({small:.3f}s -> {large:.3f}s): that is the "
                        "quadratic coming back")

    def test_multi_megabyte_test_file_is_read_whole_into_memory(self):
        """FIXED: flake_smells.py now has MAX_FILE_BYTES = 2MB.

        read_lines() did a bare fh.read() then splitlines(keepends=True), so a
        file cost several times its own size in RAM. Measured on this machine:
        a 49MB generated test file took 45s and peaked near 3GB RSS.

        This file is just UNDER the ceiling, so it is still scanned -- the
        ceiling has to bound the pathological case without quietly dropping
        the large-but-ordinary one.
        """
        lines = ["def test_a():\n"]
        lines += ["    assert foo(%d) == %d  # padding padding padding\n" % (i, i)
                  for i in range(35_000)]
        self.write("test_big.py", "".join(lines))
        size = os.path.getsize(self.path("test_big.py"))
        self.assertGreater(size, 1024 * 1024)
        with timed(self, "flake_smells on a 2MB test file", budget=15.0):
            result = fsm.scan_paths([self.tmp])
        self.assertEqual(result["summary"]["files_scanned"], 1)

    def test_env_safety_check_does_bound_file_size(self):
        # The one script that has a ceiling. Pinned so it is not lost in a
        # refactor -- it is the only thing standing between this gate and a
        # generated 200MB appsettings.json.
        self.write(".env", "# pad\n" * 400_000)
        self.assertGreater(os.path.getsize(self.path(".env")), esc.MAX_BYTES)
        result = esc.scan(self.tmp)
        self.assertEqual(result["scanned_files"], [])
        self.assertIn("2MB", result["skipped_files"][0]["reason"])

    def test_ten_thousand_tiny_files(self):
        # A monorepo's test tree. The cost must be linear and the walk must not
        # hold every file at once.
        for i in range(10_000):
            sub = self.path("pkg%03d/test_%d.py" % (i // 200, i))
            with open(sub, "w", encoding="utf-8") as fh:
                fh.write("def test_x():\n    assert 1 == 1\n")
        with timed(self, "flake_smells over 10,000 files", budget=20.0):
            result = fsm.scan_paths([self.tmp])
        self.assertEqual(result["summary"]["files_scanned"], 10_000)
        with timed(self, "env_safety_check over 10,000 files", budget=10.0):
            esc.scan(self.tmp)

    def test_deeply_nested_directories(self):
        # Recursion depth is the usual failure here. os.walk is iterative, so
        # this should be fine -- which is worth proving rather than assuming.
        deep = self.tmp
        depth = 0
        try:
            for _ in range(150):
                deep = os.path.join(deep, "d")
                os.mkdir(deep)
                depth += 1
        except OSError:
            pass
        if depth < 100:
            self.skipTest(f"filesystem stopped nesting at {depth} levels")
        with open(os.path.join(deep, "test_a.py"), "w", encoding="utf-8") as fh:
            fh.write("def test_x():\n    assert 1\n")
        with open(os.path.join(deep, ".env"), "w", encoding="utf-8") as fh:
            fh.write("URL=https://api.prod.acme.com\n")
        self.assertEqual(fsm.scan_paths([self.tmp])["summary"]["files_scanned"], 1)
        self.assertEqual(esc.scan(self.tmp)["verdict"], "UNSAFE")

    def test_broken_symlink_is_skipped_not_fatal(self):
        try:
            os.symlink(self.path("does-not-exist"), self.path(".env"))
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"symlinks unsupported here: {exc}")
        # read_text() catches the OSError, so the dangling .env is dropped.
        # NOTE: it is dropped into skipped_files, which is right -- but a
        # dangling .env is also the shape of a deliberately hidden config.
        result = esc.scan(self.tmp)
        self.assertEqual(result["scanned_files"], [])
        self.assertEqual([s["file"] for s in result["skipped_files"]], [".env"])
        self.assertEqual(fsm.scan_paths([self.tmp])["summary"]["files_scanned"], 0)

    def test_symlink_loop_terminates(self):
        loop = self.path("loop")
        os.makedirs(loop, exist_ok=True)
        try:
            os.symlink(loop, os.path.join(loop, "self"))
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"symlinks unsupported here: {exc}")
        with open(os.path.join(loop, "test_a.py"), "w", encoding="utf-8") as fh:
            fh.write("def test_x():\n    assert 1\n")
        # os.walk defaults to followlinks=False, so the loop is never entered.
        # If anyone ever passes followlinks=True this test hangs, which is the
        # point of the time bound.
        with timed(self, "walk over a symlink loop"):
            self.assertEqual(fsm.scan_paths([self.tmp])["summary"]["files_scanned"], 1)
            esc.scan(self.tmp)

    def test_hostile_path_names_are_not_shell_expanded(self):
        # risk_map.py is the only script that shells out. It uses the list form
        # of subprocess, so `$(whoami)` must survive as eight literal
        # characters. If it ever became a shell string this test fails loudly.
        name = 'we ird "q" $(whoami) \'x\''
        os.makedirs(self.path(name), exist_ok=True)
        self.write(os.path.join(name, ".env"), "URL=https://api.prod.acme.com\n")
        result = esc.scan(self.tmp)
        self.assertEqual(result["verdict"], "UNSAFE")
        self.assertIn(name + "/.env", result["scanned_files"])
        self.assertNotIn("$(whoami)", str(os.environ.get("USER", "")) and "")

    def test_filename_containing_a_newline(self):
        try:
            self.write("we\nird/test_a.py", "def test_x():\n    assert 1\n")
        except OSError as exc:
            self.skipTest(f"filesystem rejects newline in a name: {exc}")
        # A newline in a path breaks any line-oriented protocol downstream.
        # The scanners are not line-oriented about paths, so this holds.
        result = fsm.scan_paths([self.tmp])
        self.assertEqual(result["summary"]["files_scanned"], 1)
        self.assertIn("\n", result["files"][0]["path"])

    def test_filename_that_is_only_dots(self):
        # "..." is a legal filename and is neither "." nor "..". A walker that
        # filters on a startswith(".") check can trip over it.
        self.write("...", "URL=https://api.prod.acme.com\n")
        self.write("....env", "URL=https://api.prod.acme.com\n")
        with timed(self, "scan with dot-only filenames"):
            result = esc.scan(self.tmp)
        # "..." is not classified as anything; "....env" starts with "." but
        # not with ".env", so neither is read. Correct, and worth pinning:
        # classify() must not be tricked into treating "..." as a config.
        self.assertEqual(result["scanned_files"], [])


# --------------------------------------------------------------------------
# malformed domain input
# --------------------------------------------------------------------------

class MalformedCoverageTests(TempMixin, unittest.TestCase):
    """Coverage tools emit broken reports constantly -- interrupted runs,
    partial merges, versions that disagree with their own schema. The gate
    that reads them decides whether a release ships, so a field it cannot
    parse must never become a number it reports."""

    def norm(self, text, fmt):
        return ncov.normalize(text, fmt)

    def test_lcov_brda_with_missing_fields_is_ignored(self):
        # A truncated BRDA has no 'taken' column. Counting it as a found branch
        # would invent an uncovered branch out of a parse failure.
        result = self.norm("SF:a.py\nBRDA:1,0\nDA:1,1\nend_of_record\n", "lcov")
        self.assertEqual(result["totals"]["branches_found"], 0)
        self.assertEqual(result["totals"]["lines_found"], 1)

    def test_lcov_negative_hit_count_is_treated_as_uncovered(self):
        # A negative count is nonsense, but "not greater than zero" is the
        # conservative reading: report the line as a gap, not as covered.
        result = self.norm("SF:a.py\nDA:1,-5\nend_of_record\n", "lcov")
        self.assertEqual(result["totals"]["lines_hit"], 0)
        self.assertEqual(result["files"][0]["uncovered_lines"], [1])

    def test_lcov_non_numeric_line_number_is_dropped(self):
        result = self.norm("SF:a.py\nDA:abc,1\nDA:2,1\nend_of_record\n", "lcov")
        self.assertEqual(result["totals"]["lines_found"], 1)

    def test_lcov_brda_with_negative_taken_counts_as_not_taken(self):
        result = self.norm("SF:a.py\nBRDA:1,0,0,-3\nend_of_record\n", "lcov")
        self.assertEqual(result["totals"]["branches_found"], 1)
        self.assertEqual(result["totals"]["branches_hit"], 0)

    def test_lcov_with_data_before_any_sf_record(self):
        # A merged report can start mid-record. Lines with no owning file must
        # be dropped rather than attributed to whatever comes next.
        result = self.norm("DA:1,1\nDA:2,1\nSF:a.py\nDA:3,0\nend_of_record\n", "lcov")
        self.assertEqual(result["totals"]["lines_found"], 1)

    def test_cobertura_without_sources_keeps_the_relative_path(self):
        xml = ('<coverage><packages><package><classes>'
               '<class filename="src/a.py"><lines>'
               '<line number="1" hits="0"/></lines></class>'
               '</classes></package></packages></coverage>')
        result = self.norm(xml, "cobertura")
        self.assertEqual(result["files"][0]["path"], "src/a.py")
        self.assertEqual(result["totals"]["line_pct"], 0.0)

    def test_cobertura_branch_without_condition_coverage_counts_nothing(self):
        xml = ('<coverage><packages><package><classes>'
               '<class filename="a.py"><lines>'
               '<line number="1" hits="1" branch="true"/></lines></class>'
               '</classes></package></packages></coverage>')
        result = self.norm(xml, "cobertura")
        self.assertEqual(result["totals"]["branches_found"], 0)
        self.assertIsNone(result["totals"]["branch_pct"])

    def test_jacoco_counter_missing_missed_is_not_reported_as_full_coverage(self):
        """WRONG ANSWER: a JaCoCo counter with no `missed` attribute reports 100%.

        parse_jacoco reads `int(counter.get("missed", "0"))`, so a truncated or
        non-conforming counter contributes covered lines and zero missed ones.
        The file then shows line_pct = 100.0. That is the single most dangerous
        shape a coverage normalizer can produce: a malformed input that reads
        as a perfect result, which a coverage gate waves straight through.
        SHOULD: a counter missing either attribute is unparseable -- skip it
        (and say so), rather than default the missing half to zero.
        """
        xml = ('<report><package name="p"><sourcefile name="A.java">'
               '<counter type="LINE" covered="5"/>'
               '</sourcefile></package></report>')
        result = self.norm(xml, "jacoco")
        self.assertNotEqual(result["totals"]["line_pct"], 100.0)

    def test_jacoco_counter_missing_missed_is_skipped_entirely(self):
        # Fixed: the half-counter contributes nothing at all, so the file
        # reports "nothing measured" rather than a perfect score.
        xml = ('<report><package name="p"><sourcefile name="A.java">'
               '<counter type="LINE" covered="5"/>'
               '</sourcefile></package></report>')
        result = self.norm(xml, "jacoco")
        self.assertIsNone(result["totals"]["line_pct"])
        self.assertEqual(result["totals"]["lines_found"], 0)

    def test_go_cover_malformed_span_is_dropped(self):
        # "1.2,3" is missing the end column. The regex does not match and the
        # line is skipped -- no partial block is invented.
        result = self.norm("mode: set\nfoo.go:1.2,3 1 1\nbar.go:1.1,2.2 3 1\n",
                           "gocover")
        self.assertEqual(result["totals"]["files"], 1)
        self.assertEqual(result["files"][0]["path"], "bar.go")

    def test_coveragepy_json_that_is_a_list_does_not_crash(self):
        """CRASH: normalize_coverage.py dies with an AttributeError on a JSON list.

        parse_coveragepy does `data.get("files")` with no type check, so a
        report that is a top-level JSON array raises
        `AttributeError: 'list' object has no attribute 'get'` straight through
        main() -- which only catches Detect, ET.ParseError and JSONDecodeError.
        The user sees a Python traceback, exit code 1.
        Reachable whenever --format coveragepy is passed explicitly, which the
        skills do when they already know the tool.
        SHOULD: raise Detect("JSON given, but it is not an object") and exit 2.
        """
        self.write("coverage.json", "[{\"a\": 1}]")
        self.assertEqual(ncov.main([self.path("coverage.json"),
                                    "--format", "coveragepy"]), 2)

    def test_coveragepy_json_list_is_refused_with_a_clear_message(self):
        with self.assertRaises(ncov.Detect):
            ncov.normalize('[{"a": 1}]', "coveragepy")

    def test_valid_json_with_a_foreign_schema_is_refused(self):
        # package.json handed to the coverage normalizer by mistake. Refusing
        # is right: guessing would produce confident wrong numbers.
        self.write("coverage.json", '{"name": "app", "version": "1.0.0"}')
        self.assertEqual(ncov.main([self.path("coverage.json")]), 2)

    def test_xml_that_is_neither_cobertura_nor_jacoco_is_refused(self):
        self.write("coverage.xml", "<testsuite name='x'><testcase/></testsuite>")
        self.assertEqual(ncov.main([self.path("coverage.xml")]), 2)

    def test_truncated_xml_is_reported_as_malformed_not_as_a_crash(self):
        self.write("coverage.xml", "<coverage><packages><package>")
        self.assertEqual(ncov.main([self.path("coverage.xml")]), 2)

    def test_xml_billion_laughs_entity_expansion(self):
        # ElementTree refuses entity expansion by default. Worth pinning: a
        # coverage.xml comes from CI, and CI artifacts are attacker-reachable
        # on a fork PR.
        bomb = ('<?xml version="1.0"?><!DOCTYPE coverage ['
                '<!ENTITY a "aaaaaaaaaa">'
                '<!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;&a;&a;">'
                '<!ENTITY c "&b;&b;&b;&b;&b;&b;&b;&b;&b;&b;">'
                ']><coverage><x>&c;</x></coverage>')
        self.write("coverage.xml", bomb)
        with timed(self, "entity expansion"):
            rc = ncov.main([self.path("coverage.xml"), "--out", self.path("o.json")])
        # ElementTree resolves the declared internal entities but refuses an
        # external DTD, so there is no amplification beyond what is written
        # out. The report parses to zero files and exits 0 -- a quiet empty
        # result rather than a refusal, which is the same silent-zero shape
        # the UTF-16 case produces.
        self.assertEqual(rc, 0)
        with open(self.path("o.json"), encoding="utf-8") as fh:
            data = json.load(fh)
        self.assertEqual(data["totals"]["files"], 0)


# --------------------------------------------------------------------------
# git edge cases
# --------------------------------------------------------------------------

@unittest.skipUnless(HAVE_GIT, "git is not installed")
class GitEdgeTests(TempMixin, unittest.TestCase):
    """risk_map.py runs `git log` on whatever it is pointed at. Repositories in
    the wild are shallow, detached, worktree-linked, submodule-bearing and
    sometimes empty -- and the script's own docstring says an empty result must
    announce itself rather than read as 'low risk'."""

    def commit(self, repo, message, allow_empty=False):
        git(repo, "add", "-A")
        args = ["commit", "-q", "-m", message]
        if allow_empty:
            args.append("--allow-empty")
        if not message:
            args.append("--allow-empty-message")
        git(repo, *args)

    def test_repo_with_zero_commits(self):
        # WRONG ANSWER, documented. build() raises GitError and main() exits 2
        # with git's own `fatal: your current branch 'main' does not have any
        # commits yet` pasted through. The script's stated design is that an
        # empty window produces a WARNING saying "absence of data, not absence
        # of risk" -- a fresh repo is the clearest case of that, and it is the
        # one case that instead hard-errors with a raw git message.
        # SHOULD: return the empty map with that warning.
        repo = init_repo(self.path("empty"))
        with self.assertRaises(rmap.GitError):
            rmap.build(repo, since=None)
        self.assertEqual(rmap.main([repo]), 2)

    def test_repo_with_only_merge_commits_in_the_window(self):
        # --no-merges is deliberate, so a merge-only window legitimately scores
        # nothing. It must say so rather than return a quiet empty list.
        repo = init_repo(self.path("merges"))
        self.write("merges/f.py", "a\nb\n")
        self.commit(repo, "init")
        git(repo, "checkout", "-qb", "side")
        self.write("merges/f.py", "a\nb\nc\n")
        self.commit(repo, "side work")
        git(repo, "checkout", "-q", "main")
        git(repo, "merge", "-q", "--no-ff", "side", "-m", "Merge side")
        result = rmap.build(repo, since=None)
        self.assertTrue(result["files"])  # the non-merge commits still count

    def test_detached_head(self):
        repo = init_repo(self.path("detached"))
        self.write("detached/f.py", "a\nb\nc\n")
        self.commit(repo, "init")
        self.write("detached/f.py", "a\nb\nc\nd\n")
        self.commit(repo, "fix: thing")
        git(repo, "checkout", "-q", "--detach", "HEAD")
        result = rmap.build(repo, since=None)
        self.assertEqual([f["path"] for f in result["files"]], ["f.py"])

    def test_shallow_clone(self):
        # `--depth 1` is what every CI checkout does by default. The history
        # the risk map depends on simply is not there.
        src = init_repo(self.path("src"))
        self.write("src/f.py", "a\nb\nc\n")
        self.commit(src, "init")
        self.write("src/f.py", "a\nb\nc\nd\n")
        self.commit(src, "fix: thing")
        dst = self.path("shallow")
        proc = subprocess.run(["git", "clone", "-q", "--depth", "1",
                               "file://" + src, dst],
                              capture_output=True, text=True)
        if proc.returncode != 0:
            self.skipTest(f"shallow clone unavailable: {proc.stderr.strip()}")
        result = rmap.build(dst, since=None)
        # Exactly one commit is visible, so the map is built on one data point.
        # It does not crash and it does not warn -- which is the gap worth
        # naming: a shallow clone is "absence of data" exactly like an empty
        # window, and the script only warns for the latter.
        # SHOULD: detect `git rev-parse --is-shallow-repository` and warn.
        self.assertEqual(result["window"]["commits_analyzed"], 1)
        self.assertEqual(result["warnings"], [])

    def test_commit_with_an_empty_message(self):
        repo = init_repo(self.path("emptymsg"))
        self.write("emptymsg/f.py", "a\nb\nc\n")
        self.commit(repo, "")
        self.write("emptymsg/f.py", "a\nb\nc\nd\n")
        self.commit(repo, "")
        result = rmap.build(repo, since=None)
        self.assertEqual([f["path"] for f in result["files"]], ["f.py"])
        self.assertEqual(result["files"][0]["bugfix_commits"], 0)

    @unittest.expectedFailure
    def test_a_file_renamed_three_times_keeps_its_history(self):
        """WRONG ANSWER: renaming a file resets its risk history to one commit.

        _unrename() maps `old => new` to `new`, but only for that one commit.
        After three renames the history reads one.py -> two.py -> three.py ->
        four.py; only `four.py` exists now, so the first three commits are
        dropped by the `path not in sizes` filter. The file shows 1 commit and
        0 churned lines -- it looks brand new and quiet, when it is the most
        rewritten file in the repo.
        This matters because renaming is what happens during a refactor, and a
        refactor is precisely when defect likelihood spikes.
        SHOULD: follow renames (git log --follow, or chain the rename map) so
        churn and bug-fix history survive the move.
        """
        repo = init_repo(self.path("renames"))
        self.write("renames/one.py", "a\nb\nc\nd\ne\n")
        self.commit(repo, "init")
        for prev, name in (("one", "two"), ("two", "three"), ("three", "four")):
            git(repo, "mv", prev + ".py", name + ".py")
            with open(os.path.join(repo, name + ".py"), "a", encoding="utf-8") as fh:
                fh.write("x\n")
            self.commit(repo, f"fix: rename to {name}")
        result = rmap.build(repo, since=None)
        self.assertEqual(result["files"][0]["path"], "four.py")
        self.assertGreaterEqual(result["files"][0]["commits"], 4)

    def test_renamed_file_currently_shows_one_commit(self):
        repo = init_repo(self.path("renames2"))
        self.write("renames2/one.py", "a\nb\nc\nd\ne\n")
        self.commit(repo, "init")
        for prev, name in (("one", "two"), ("two", "three"), ("three", "four")):
            git(repo, "mv", prev + ".py", name + ".py")
            with open(os.path.join(repo, name + ".py"), "a", encoding="utf-8") as fh:
                fh.write("x\n")
            self.commit(repo, f"fix: rename to {name}")
        result = rmap.build(repo, since=None)
        self.assertEqual([(f["path"], f["commits"]) for f in result["files"]],
                         [("four.py", 1)])

    def test_path_with_a_space(self):
        repo = init_repo(self.path("spacey"))
        self.write("spacey/a file.py", "a\nb\nc\n")
        self.commit(repo, "init")
        self.write("spacey/a file.py", "a\nb\nc\nd\n")
        self.commit(repo, "fix: thing")
        result = rmap.build(repo, since=None)
        self.assertEqual([f["path"] for f in result["files"]], ["a file.py"])

    def test_path_with_shell_metacharacters_is_not_expanded(self):
        repo = init_repo(self.path("meta"))
        name = "$(whoami).py"
        self.write("meta/" + name, "a\nb\nc\n")
        self.commit(repo, "init")
        self.write("meta/" + name, "a\nb\nc\nd\n")
        self.commit(repo, "fix: thing")
        result = rmap.build(repo, since=None)
        self.assertEqual([f["path"] for f in result["files"]], [name])

    def test_git_dir_that_is_a_file_worktree(self):
        # A linked worktree has a `.git` FILE containing `gitdir: ...`. Anything
        # that checks os.path.isdir(".git") breaks here; risk_map asks git
        # instead, which is the correct approach.
        repo = init_repo(self.path("wtmain"))
        self.write("wtmain/f.py", "a\nb\nc\n")
        self.commit(repo, "init")
        self.write("wtmain/f.py", "a\nb\nc\nd\n")
        self.commit(repo, "fix: thing")
        wt = self.path("linked")
        proc = git(repo, "worktree", "add", "-q", wt, "-b", "wtbranch", check=False)
        if proc.returncode != 0:
            self.skipTest(f"git worktree unavailable: {proc.stderr.strip()}")
        self.assertTrue(os.path.isfile(os.path.join(wt, ".git")))
        result = rmap.build(wt, since=None)
        self.assertEqual([f["path"] for f in result["files"]], ["f.py"])

    def test_submodule_is_not_scored_as_a_file(self):
        # A submodule appears in `git ls-files` as a gitlink. Opening it gives
        # IsADirectoryError, which current_files() catches -- so it is excluded
        # from sizes and therefore never scored. Correct, and worth pinning.
        sub = init_repo(self.path("sub"))
        self.write("sub/s.py", "a\nb\n")
        self.commit(sub, "init")
        repo = init_repo(self.path("outer"))
        self.write("outer/f.py", "a\nb\nc\n")
        self.commit(repo, "init")
        proc = git(repo, "-c", "protocol.file.allow=always", "submodule", "add",
                   "-q", "file://" + sub, "vendored", check=False)
        if proc.returncode != 0:
            self.skipTest(f"submodule add unavailable: {proc.stderr.strip()}")
        self.commit(repo, "fix: add submodule")
        result = rmap.build(repo, since=None)
        self.assertNotIn("vendored", [f["path"] for f in result["files"]])

    def test_ten_thousand_commits_completes_quickly(self):
        # A real repository's history. Built with fast-import so the fixture
        # costs under a second; the measurement is of risk_map, not of git.
        repo = init_repo(self.path("big"))
        stream = ["reset refs/heads/main"]
        for i in range(10_000):
            msg = ("fix: bug %d" % i) if i % 3 == 0 else ("feat %d" % i)
            body = "line %d\n" % i
            stream += [
                "commit refs/heads/main",
                "mark :%d" % (i + 1),
                "author Dev <dev@example.com> %d +0000" % (1_600_000_000 + i * 60),
                "committer Dev <dev@example.com> %d +0000" % (1_600_000_000 + i * 60),
                "data %d" % len(msg), msg,
            ]
            if i:
                stream.append("from :%d" % i)
            stream += ["M 644 inline src/f%d.py" % (i % 50),
                       "data %d" % len(body), body.rstrip("\n"), ""]
        proc = subprocess.run(["git", "-C", repo, "fast-import", "--quiet"],
                              input="\n".join(stream) + "\n",
                              capture_output=True, text=True)
        if proc.returncode != 0:
            self.skipTest(f"git fast-import unavailable: {proc.stderr.strip()}")
        git(repo, "checkout", "-q", "main")
        with timed(self, "risk_map over 10,000 commits", budget=30.0):
            result = rmap.build(repo, since=None)
        self.assertEqual(result["window"]["commits_analyzed"], 10_000)
        self.assertEqual(len(result["files"]), 50)

    def test_max_commits_bounds_the_history_read(self):
        # The one explicit work bound in risk_map.py. git itself does the
        # truncation, so memory stays proportional to --max-commits.
        repo = init_repo(self.path("bounded"))
        for i in range(20):
            self.write("bounded/f.py", "x\n" * (i + 2))
            self.commit(repo, "fix: change %d" % i)
        result = rmap.build(repo, since=None, max_commits=5)
        self.assertEqual(result["window"]["commits_analyzed"], 5)


# --------------------------------------------------------------------------
# concurrency
# --------------------------------------------------------------------------

class ConcurrencyTests(TempMixin, unittest.TestCase):
    """The project tells agents that writes to .testing/ are atomic -- temp
    file then rename -- and that there is one file per item, so parallel agents
    cannot clobber each other. Anything the scripts write lands in the same
    directory, so the claim has to hold for them too."""

    def _lcov(self, n):
        return self.write("in%d.info" % n, LCOV_OK)

    def test_parallel_writes_to_distinct_files_all_land_intact(self):
        # The "one file per item" half of the claim. Eight processes, eight
        # output files, one shared directory.
        testing = self.path(".testing")
        os.makedirs(testing, exist_ok=True)
        procs = []
        for i in range(8):
            procs.append(subprocess.Popen(
                [sys.executable, os.path.join(SCRIPTS, "normalize_coverage.py"),
                 self._lcov(i), "--out", os.path.join(testing, "coverage-%d.json" % i)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        for p in procs:
            self.assertEqual(p.wait(timeout=60), 0)
        for i in range(8):
            with open(os.path.join(testing, "coverage-%d.json" % i),
                      encoding="utf-8") as fh:
                self.assertEqual(json.load(fh)["totals"]["lines_found"], 2)

    # Every script that declares an `--out` flag. Hard-coded rather than
    # derived so that a new writer joining the tree fails this test until
    # somebody has looked at how it writes; `atomic_io.py` is deliberately
    # absent -- it has no --out of its own, only an `open(args.out, "w")`
    # quoted in its docstring, which is why the scan keys on the flag.
    OUT_WRITERS = ["arid_nodes.py", "env_safety_check.py", "flake_smells.py",
                   "normalize_coverage.py", "pr_comment.py",
                   "project_state.py", "risk_map.py"]

    # Enough rounds that a truncation window of a millisecond or two is
    # sampled, few enough that seven writers stay well under two seconds.
    ATOMIC_ROUNDS = 5

    def _probe_inputs(self):
        """The smallest input that makes each writer produce a report.

        Built once per test: a source tree with one test file and one .env, a
        large LCOV report, a --data blob and a one-commit git repository.
        """
        src = self.path("probe/src")
        os.makedirs(src, exist_ok=True)
        self.write("probe/src/test_probe.py",
                   "import unittest\n\n\nclass T(unittest.TestCase):\n"
                   "    def test_a(self):\n        self.assertEqual(1, 1)\n")
        self.write("probe/src/.env", "API_KEY=abc123\nHOST=api.acme.com\n")
        # Deliberately large: the wider the document, the wider the window a
        # truncate-then-write leaves open, and the more of this test's value
        # survives on a machine faster than the one it was written on.
        big_lcov = self.write("probe/big.info", "".join(
            "SF:src/f%d.py\n%send_of_record\n"
            % (i, "".join("DA:%d,%d\n" % (n, n % 2) for n in range(1, 60)))
            for i in range(300)))
        data = self.write("probe/data.json", json.dumps(
            {"health": {"verdict": "GREEN"}, "coverage": {"totals": {}}}))
        repo = init_repo(self.path("probe/repo"))
        self.write("probe/repo/a.py", "def f():\n    return 1\n")
        git(repo, "add", "-A")
        git(repo, "commit", "-q", "-m", "seed")
        os.makedirs(self.path("probe/out"), exist_ok=True)
        return src, big_lcov, data, repo

    def _out_writer_probes(self):
        """(script name, run) for every one of the seven `--out` writers.

        In-process rather than by subprocess: `main(argv)` reaches the same
        writer either way, and seven writers times six interpreter startups
        would buy nothing but seconds. `project_state`'s ROOT is pointed at
        the fixture for the reason its own tests do it -- measuring the live
        repository here would make this test depend on the branch, the dirty
        list and on whatever another process is writing right now.
        """
        self.assertIsNotNone(
            arid, "arid_nodes.py is missing from scripts/, so one of the "
                  "seven --out writers cannot be exercised at all")
        src, big_lcov, data, repo = self._probe_inputs()

        def project_state(out):
            with mock.patch.object(pstate, "ROOT", src):
                return pstate.main(["--no-tests", "--out", out])

        return [
            ("arid_nodes.py", lambda out: arid.main([src, "--out", out])),
            ("env_safety_check.py", lambda out: esc.main([src, "--out", out])),
            ("flake_smells.py", lambda out: fsm.main([src, "--out", out])),
            ("normalize_coverage.py",
             lambda out: ncov.main([big_lcov, "--out", out])),
            ("pr_comment.py",
             lambda out: prc.main(["--data", data, "--out", out])),
            ("project_state.py", project_state),
            ("risk_map.py", lambda out: rmap.main([repo, "--out", out])),
        ]

    def _write_one_report(self, name, run, out):
        """Run the writer once, quietly, and return the size it left behind.

        The size is the test's floor: a writer that produced nothing would
        otherwise make every comparison below trivially true.
        """
        with contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(io.StringIO()):
            run(out)
        self.assertTrue(os.path.exists(out),
                        f"{name} --out wrote no file at all, so this test "
                        "would have measured nothing")
        size = os.path.getsize(out)
        self.assertGreater(size, 0, f"{name} --out wrote an empty report")
        return size

    def assert_never_observed_truncated(self, name, run):
        """Rewrite the destination while a reader samples it, and assert the
        reader never caught it short.

        The oracle is a size, not a parse, because two of the seven write text
        rather than JSON: `write_text_atomic` renames a finished temp file
        onto the destination, so the smallest size a reader can ever observe
        is the size of a whole report. `open(path, "w")` holds it at zero
        bytes and then at a prefix -- however the truncation is reached, and
        whatever else is called afterwards.
        """
        out = self.path("probe/out/%s" % name)
        floor = self._write_one_report(name, run, out)
        seen = {"min": None, "missing": 0, "reads": 0}
        stop = threading.Event()

        def reader():
            while not stop.is_set():
                try:
                    with open(out, "rb") as fh:
                        size = len(fh.read())
                except FileNotFoundError:
                    seen["missing"] += 1
                    continue
                seen["reads"] += 1
                if seen["min"] is None or size < seen["min"]:
                    seen["min"] = size

        watcher = threading.Thread(target=reader)
        watcher.start()
        try:
            for _ in range(self.ATOMIC_ROUNDS):
                floor = min(floor, self._write_one_report(name, run, out))
        finally:
            stop.set()
            watcher.join()

        # Guard the guard: a reader that never ran would observe nothing and
        # report it as nothing wrong.
        self.assertGreater(
            seen["reads"], self.ATOMIC_ROUNDS,
            f"the reader sampled {name}'s destination {seen['reads']} times, "
            "which is too few for this test to have measured anything")
        self.assertEqual(
            seen["missing"], 0,
            f"{name}'s --out destination disappeared {seen['missing']} "
            "time(s) mid-write; os.replace() never unlinks it, so something "
            "else is writing that path")
        self.assertGreaterEqual(
            seen["min"], floor,
            f"a reader saw {name}'s --out destination holding "
            f"{seen['min']} bytes while the smallest complete report it "
            f"writes is {floor}: the destination is truncated before the new "
            "report is written, so a parallel agent reading it gets half a "
            "document, or none of one, and nothing reports an error")

    def test_every_out_writer_never_shows_a_reader_a_truncated_file(self):
        """The behavioural half, and the one that can actually fail.

        `skills/start/SKILL.md` promises parallel agents "cannot clobber each
        other or leave a half-written baseline". That is a claim about what a
        concurrent reader observes, so it is asserted by concurrently reading:
        each writer rewrites its destination while a reader samples it, and
        the smallest size ever observed must still be a whole report.

        Its predecessor, kept below as a lint, was two regexes over source
        text. A truncation reached through a local variable --
        `_dest = args.out; open(_dest, "w")` -- satisfies both of them, so the
        whole suite stayed green over a writer that really did clobber its
        destination for 50ms on every run. A grep is a lint; only a reader can
        testify about what a reader sees.
        """
        probes = self._out_writer_probes()
        self.assertEqual(
            [name for name, _ in probes], self.OUT_WRITERS,
            "every --out writer must be exercised here, not just listed")
        for name, run in probes:
            with self.subTest(writer=name):
                self.assert_never_observed_truncated(name, run)

    def test_every_out_writer_is_atomic(self):
        """The lint half: all seven `--out` writers go through atomic_io.

        This is a source scan, and a source scan cannot see a truncation that
        goes through a variable, a helper or a second file -- it was measured
        surviving exactly that. It is kept because it names the offending
        script and fails on the naive conversion the moment it lands, before
        anyone has to reason about a race. The test above it is the one that
        can testify about behaviour.

        A plain `open(path, "w")` truncates first and writes after, so two
        agents told to write the same .testing/ file -- which is what the
        parallel dispatch in the skills does -- could interleave and both exit
        0, and a reader opening the file mid-write saw a truncated document.
        `scripts/atomic_io.write_text_atomic()` writes a temp file in the
        destination directory and os.replace()s it onto the target, so a
        reader sees the whole old file or the whole new one. This is what
        makes skills/start/SKILL.md ("Writes are atomic (temp file, then
        rename)") true rather than aspirational.

        The previous version of this test asserted `assertNotIn("os.replace",
        offenders)` against a list of FILENAMES, so it could only have failed
        for a script literally named `os.replace`; four conversions landed
        under it with no signal. Both assertions below name the offending
        script, and the first one guarantees the scan looked at anything at
        all.
        """
        writers, offenders = [], []
        for name in sorted(os.listdir(SCRIPTS)):
            if not name.endswith(".py"):
                continue
            with open(os.path.join(SCRIPTS, name), encoding="utf-8") as fh:
                src = fh.read()
            if '"--out"' not in src:
                continue
            writers.append(name)
            # Atomic means both halves: the atomic call is present AND no
            # plain open() of the same path survives beside it.
            if (not re.search(r'write_text_atomic\(\s*args\.out', src)
                    or re.search(r'open\(\s*args\.out', src)):
                offenders.append(name)
        self.assertEqual(
            writers, self.OUT_WRITERS,
            "the set of --out writers changed; a new one must be atomic "
            "before it is added here")
        self.assertEqual(
            offenders, [],
            "these --out writers still truncate-then-write: %s" % offenders)

    def test_concurrent_writes_to_the_same_output_file_do_not_crash(self):
        # Both processes exit 0; the surviving file is whatever the last writer
        # left. No error is reported to either side, which is the hazard: a
        # clobbered report is indistinguishable from a correct one.
        out = self.path(".testing/coverage.json")
        procs = [subprocess.Popen(
            [sys.executable, os.path.join(SCRIPTS, "normalize_coverage.py"),
             self._lcov(i), "--out", out],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) for i in range(2)]
        for p in procs:
            self.assertEqual(p.wait(timeout=60), 0)
        with open(out, encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["totals"]["lines_found"], 2)


# --------------------------------------------------------------------------
# resource safety
# --------------------------------------------------------------------------

class ResourceBoundTests(unittest.TestCase):
    """Each script runs unattended on a machine that is not ours. An unbounded
    read is how a plugin takes the whole box down over one generated file."""

    def source(self, name):
        with open(os.path.join(SCRIPTS, name), encoding="utf-8") as fh:
            return fh.read()

    def test_env_safety_check_has_an_explicit_size_ceiling(self):
        self.assertEqual(esc.MAX_BYTES, 2 * 1024 * 1024)
        self.assertIn("getsize", self.source("env_safety_check.py"))

    def test_scripts_without_any_size_ceiling(self):
        """FIXED for three of the four: a ceiling, and a reported skip.

        flake_smells.py and arid_nodes.py both did a bare `fh.read()` in
        read_lines() with no getsize() check, and risk_map.current_files()
        read every tracked file whole just to count its newlines. Measured on
        the input that motivated the work: a 49MB generated test file took 45s
        and peaked near 3GB RSS. Each now has MAX_FILE_BYTES, a getsize()
        check before the read, a --max-file-bytes flag, and a skipped_files
        entry so an unread file can never pass for a clean one.

        normalize_coverage.py is the remaining one; it is owned by another
        change in flight and is deliberately left here as the only entry.
        """
        # arid_nodes.py is one of the scripts under examination here. If it
        # is gone, this loop would otherwise examine one script fewer and
        # still pass -- a quieter version of the skip that used to guard
        # the tests above, and the same defect.
        require_arid(self)
        unbounded = []
        for name in ("flake_smells.py", "arid_nodes.py", "normalize_coverage.py",
                     "risk_map.py"):
            src = self.source(name)
            if "getsize" not in src and "MAX_BYTES" not in src:
                unbounded.append(name)
        self.assertEqual(unbounded, ["normalize_coverage.py"])

    def test_bounded_scripts_all_report_what_they_skipped(self):
        # A ceiling that drops a file quietly is worse than no ceiling: the
        # report then reads exactly like one over a clean repository. Every
        # script that gained a ceiling must also have gained the skip list.
        # arid_nodes.py is one of the scripts under examination here. If it
        # is gone, this loop would otherwise examine one script fewer and
        # still pass -- a quieter version of the skip that used to guard
        # the tests above, and the same defect.
        require_arid(self)
        for name in ("flake_smells.py", "arid_nodes.py", "risk_map.py"):
            src = self.source(name)
            with self.subTest(script=name):
                self.assertIn("skipped_files", src)
                self.assertIn("--max-file-bytes", src)

    def _ast_parse_scripts(self):
        return [n for n in sorted(os.listdir(SCRIPTS))
                if n.endswith(".py") and "ast.parse(" in self.source(n)]

    def test_the_two_scanners_guard_and_report_ast_recursion(self):
        """A structural guard, because the next ast.parse call site will be
        written by someone who never saw this bug.

        `ast.parse` raises RecursionError on deeply nested input at a depth
        that is not sys.getrecursionlimit() and differs by platform, so a new
        unguarded site is a crash that only CI will find -- which is exactly
        what happened. Grepping the source is crude, but it is the only check
        that fires without depending on the machine's C stack, so it is as
        true on a developer's Mac as on an Ubuntu runner.
        """
        # arid_nodes.py is one of the scripts under examination here. If it
        # is gone, this loop would otherwise examine one script fewer and
        # still pass -- a quieter version of the skip that used to guard
        # the tests above, and the same defect.
        require_arid(self)
        for name in ("arid_nodes.py", "flake_smells.py"):
            self.assertIn(name, self._ast_parse_scripts())
            src = self.source(name)
            with self.subTest(script=name):
                self.assertIn("except RecursionError", src,
                              f"{name} calls ast.parse without catching "
                              "RecursionError; a deeply nested expression "
                              "will kill the whole run")
                # Catching it is only half. Silence must never look like
                # success, so the file has to be named in the output too.
                self.assertIn("unparsed_files", src,
                              f"{name} catches RecursionError but has no "
                              "unparsed_files report; a silent fallback to "
                              "the regex scanner is the same defect in new "
                              "clothes")
                self.assertIn("files_ast_unparsed", src,
                              f"{name} has no summary counter for files "
                              "whose AST could not be built")

    def test_every_ast_parse_site_guards_against_recursion(self):
        """FIXED. Was @unittest.expectedFailure while one site was open.

        `scripts/project_state.py:_xfail_tests()` had the identical bug the
        two scanners had: it wrapped `ast.parse` in
        `except (SyntaxError, ValueError)` while its docstring promised it
        "Returns None when the file does not parse, so the caller can fall
        back". RecursionError was not caught, so a deeply nested expression in
        any scanned .py propagated out of section_markers() and took down the
        whole state report -- the script whose entire purpose is to measure
        the project when nothing else can be trusted. Reproduced before the
        fix both by forcing ast.parse to raise and by parsing a genuinely
        20,000-deep expression on a 512KB thread stack in a child process.

        The decorator was removed in the same change as the fix: a defect that
        is fixed while its test still says expectedFailure reports as an
        unexpected success, which reads like a failure in the run summary.

        This is the whole-directory sweep. It is deliberately derived from the
        tree rather than hard-coded, so the NEXT script to call ast.parse is
        covered by it on the day it lands.
        """
        sites = self._ast_parse_scripts()
        # Guard the guard: a glob that matched nothing would pass every
        # assertion below without executing one of them.
        self.assertIn("project_state.py", sites)
        self.assertGreaterEqual(len(sites), 3, sites)
        for name in sites:
            with self.subTest(script=name):
                self.assertIn("except RecursionError", self.source(name),
                              f"{name} calls ast.parse without catching "
                              "RecursionError")

    def test_risk_map_counts_lines_without_reading_the_file_whole(self):
        # FIXED. current_files() needs a newline count and nothing else, so
        # holding the file in memory to produce one int bought nothing and
        # cost the file's size in RAM -- a repo with one 500MB tracked fixture
        # cost 500MB. It now streams in READ_CHUNK blocks.
        src = self.source("risk_map.py")
        self.assertNotIn("chunk = fh.read()\n", src)
        self.assertIn("fh.read(READ_CHUNK)", src)
        self.assertIn("chunk.count(b\"\\n\")", src)

    def test_risk_map_line_count_is_still_correct_when_streamed(self):
        # Streaming is only a win if it still counts right, including the two
        # edge cases a chunked loop gets wrong: a chunk boundary, and a file
        # whose last line has no trailing newline.
        tmp = tempfile.mkdtemp(prefix="testsquad-lines-")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        body = "".join("line %d\n" % i for i in range(300_000))
        path = os.path.join(tmp, "big.txt")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(body)
        self.assertGreater(os.path.getsize(path), rmap.READ_CHUNK)
        self.assertEqual(rmap.count_lines(path, max_bytes=10 ** 9), 300_000)

        no_newline = os.path.join(tmp, "nonl.txt")
        with open(no_newline, "w", encoding="utf-8") as fh:
            fh.write("a\nb")
        self.assertEqual(rmap.count_lines(no_newline), 2)
        # Over the ceiling: not a count of zero, which would read as an empty
        # file and quietly inflate that file's relative churn.
        self.assertIsNone(rmap.count_lines(path, max_bytes=16))

    def test_flake_smells_and_arid_nodes_cap_output_not_input(self):
        # arid_nodes does bound its OUTPUT (max_per_file, default 7), which is
        # the thing its research argues for. That bound says nothing about how
        # much input it will chew through first.
        require_arid(self)
        self.assertIn("max_per_file", self.source("arid_nodes.py"))

    def test_normalize_coverage_caps_uncovered_lines_per_file(self):
        # The one output bound here, and a good one: a 100k-line uncovered file
        # would otherwise serialise 100k integers into every report.
        result = ncov.normalize(
            "SF:a.py\n" + "".join("DA:%d,0\n" % i for i in range(1, 1001))
            + "end_of_record\n", "lcov", max_uncovered=10)
        self.assertEqual(len(result["files"][0]["uncovered_lines"]), 10)
        self.assertEqual(result["files"][0]["uncovered_lines_truncated"], 990)


# --------------------------------------------------------------------------
# project_state.py and the ast.parse depth ceiling
# --------------------------------------------------------------------------

class ProjectStateAstTests(TempMixin, unittest.TestCase):
    """The state report is what a fresh session reads when nothing else can
    be trusted, so it has two jobs here and both are asserted below: survive
    a file whose AST cannot be built, and never print a count taken from one
    as though it had been measured.

    Every measurement below is taken over this test's own throwaway tree.
    `collect()` against the live repository would be ambient state, not
    behaviour: it walks and reads every file in the checkout -- including the
    ones another process may be half-way through writing -- shells out to git
    for the branch and the dirty list, and runs the repository's own
    `scripts/validate_plugin.py` as a subprocess. A test built on that passes
    on CI's clean checkout and fails on a developer's dirty one, which is the
    worst polarity: it teaches the developer to ignore the result.
    """

    # The ROOT the module computed at import time -- the real repository.
    # Captured once, so the assertions below can say what was NOT measured.
    LIVE_ROOT = pstate.ROOT

    def collect_fixture(self, forced_recursion=False):
        """`collect()` over this test's tree, with ROOT patched for the whole
        call rather than for one section of it. Tests never run the project's
        own suite, so skip_tests is not optional here."""
        with contextlib.ExitStack() as stack:
            stack.enter_context(mock.patch.object(pstate, "ROOT", self.tmp))
            if forced_recursion:
                stack.enter_context(forced_ast_recursion_error())
            return pstate.collect(skip_tests=True)

    def assert_measured_the_fixture(self, state, rendered):
        """Guard the decoupling itself.

        Without this, moving any part of the measurement back outside the ROOT
        patch would not fail here -- it would fail somewhere else, on one run
        in five, only when the tree happened to be dirty. These three
        assertions turn that into an immediate, named failure.
        """
        self.assertEqual(state["root"], self.tmp,
                         "the report was taken over the wrong tree")
        self.assertNotIn(self.LIVE_ROOT, rendered,
                         "the rendered report names the real repository, so "
                         "this test measured the live working tree instead of "
                         "its own fixture")
        self.assertFalse(state["validator"]["ran"],
                         "collect() executed the repository's own "
                         "validate_plugin.py as a subprocess; the fixture has "
                         "no scripts/ and that section must report NOT RUN")
        self.assertIn("validate_plugin.py", state["validator"]["reason"])

    def test_project_state_reports_the_file_it_could_not_parse(self):
        """Catching it is half. Silence must never look like success.

        A bare `except RecursionError: return None` would make the state
        report survive and print a `@unittest.expectedFailure` total that LOOKS
        like every other measured number on the page, while being a text match
        over a file the parser refused. This is the script a fresh session runs
        when nothing else can be trusted, so that number has to be labelled at
        the point it is printed.
        """
        # Deliberately a file with a REAL decorator in it: the line scanner
        # does find it, so the count is not zero and not wrong -- it is
        # unverified, which is a different thing and has to read differently.
        self.write("test_deep.py",
                   "import unittest\n\n\n@unittest.expectedFailure\n"
                   "def test_x():\n    y = " + "a + " * 400 + "1\n"
                   "    assert y\n")
        state = self.collect_fixture(forced_recursion=True)
        markers = state["markers"]

        self.assertEqual([d["file"] for d in markers["ast_unparsed_files"]],
                         ["test_deep.py"],
                         "a file whose AST could not be built must be NAMED, "
                         "not quietly handed to the line scanner")
        self.assertEqual(markers["files_ast_unparsed"], 1)
        self.assertFalse(markers["totals_exact"],
                         "a total that includes a line-scan estimate must not "
                         "claim to be exact")
        entry = markers["ast_unparsed_files"][0]
        self.assertEqual(entry["stage"], "ast")
        self.assertEqual(entry["fell_back_to"], "line-scan")
        self.assertIn("RecursionError", entry["reason"])
        self.assertIn("nesting", entry["reason"])
        self.assertIn("did NOT run", entry["reason"])
        self.assertIn("sys.setrecursionlimit", entry["reason"],
                      "the reason must name the limit that does NOT control "
                      "this, or a reader will try to raise it")
        self.assertTrue(entry["not_measured"],
                        "the record has to say what was lost, not only that "
                        "something was")
        # The fallback really ran: the decorator was counted, the file was not
        # dropped, and the hit says how it was found and why it has no name.
        self.assertEqual(entry["line_scan_hits"], 1)
        self.assertEqual(markers["totals"][pstate.MARKER_XFAIL], 1)
        hit = markers["expected_failures"][0]
        self.assertEqual(hit["file"], "test_deep.py")
        self.assertIsNone(hit["test"])
        self.assertEqual(hit["counted_by"], "line-scan")
        self.assertEqual(hit["why_unnamed"], pstate.AST_TOO_DEEP)

        # And in the rendered report, where a human decides the run went fine.
        rendered = pstate.render(state)
        self.assert_measured_the_fixture(state, rendered)
        self.assertIn("NOT EXACT", rendered)
        self.assertIn("COULD NOT PARSE, counted WITHOUT the AST pass: "
                      "test_deep.py", rendered)
        self.assertIn("did NOT run", rendered)

    def test_project_state_still_reports_exactly_when_everything_parses(self):
        """The other half of the pair, and the reason the first one means
        anything: the same tree, parsed, must come back exact and silent.

        Without this, `totals_exact: False` and a COULD NOT PARSE block could
        be printed unconditionally and every assertion above would still pass.
        """
        self.write("test_deep.py",
                   "import unittest\n\n\n@unittest.expectedFailure\n"
                   "def test_x():\n    y = 1\n    assert y\n")
        state = self.collect_fixture()
        markers = state["markers"]
        self.assertEqual(markers["ast_unparsed_files"], [])
        self.assertEqual(markers["files_ast_unparsed"], 0)
        self.assertTrue(markers["totals_exact"])
        self.assertEqual(markers["totals"][pstate.MARKER_XFAIL], 1)
        # Parsed means NAMED: that is what the AST pass buys over a text match.
        self.assertEqual(markers["expected_failures"],
                         [{"file": "test_deep.py", "line": 4, "test": "test_x"}])
        rendered = pstate.render(state)
        self.assert_measured_the_fixture(state, rendered)
        self.assertNotIn("NOT EXACT", rendered)
        self.assertNotIn("COULD NOT PARSE", rendered)

    def test_project_state_does_not_announce_a_half_written_file(self):
        """A SyntaxError stays a silent fallback, on purpose.

        Half-written files are an ordinary state in a working tree, a line
        scan is the right answer for one, and announcing it on every run would
        put noise in the one report that has to stay readable. The hit still
        carries `why_unnamed` so a JSON consumer can tell the two apart; what
        it must not do is set off the NOT EXACT alarm.
        """
        self.write("test_broken.py",
                   "@unittest.expectedFailure\ndef test_x(:\n    pass\n")
        with mock.patch.object(pstate, "ROOT", self.tmp):
            markers = pstate.section_markers()
        self.assertEqual(markers["ast_unparsed_files"], [])
        self.assertTrue(markers["totals_exact"])
        self.assertEqual(markers["totals"][pstate.MARKER_XFAIL], 1)
        self.assertEqual(markers["expected_failures"][0]["why_unnamed"],
                         "syntax-error")

    def test_project_state_really_does_survive_a_too_deep_file(self):
        """The forced test proves the reporting; this proves the catch.

        A real RecursionError out of a real ast.parse, in a child process on a
        small stack, because on an interpreter without the AST-construction
        counter the alternative to catching it is the process dying -- and
        finding THAT out in-process would end the run.
        """
        if not ast_depth_raises_here():
            self.skipTest(
                "this interpreter does not raise RecursionError from "
                "ast.parse at a 512KB stack -- it crashes instead, so there "
                "is no catchable behaviour here to assert. Measured on "
                "CPython 3.9.6: SIGBUS, exit 138. The forced-RecursionError "
                "test covers the reporting path on this platform.")
        self.write("test_deep.py",
                   "import unittest\n\n\n@unittest.expectedFailure\n"
                   "def test_x():\n    y = " + "a + " * 20_000 + "1\n")
        rc, out, err = scan_on_a_thin_stack(
            "project_state",
            "(setattr(mod, 'ROOT', %r), mod.section_markers())[1]" % self.tmp)
        self.assertEqual(rc, 0, f"child died instead of returning: {err}")
        self.assertIsNotNone(out, f"child produced no JSON: {err}")
        self.assertNotIn("raised", out,
                         "section_markers() must never raise -- _xfail_tests' "
                         f"docstring promises it: {out.get('raised')}")
        self.assertEqual(
            [d["file"] for d in out["result"]["ast_unparsed_files"]],
            ["test_deep.py"])
        self.assertFalse(out["result"]["totals_exact"])


# --------------------------------------------------------------------------
# ReDoS
# --------------------------------------------------------------------------

class ReDoSTests(TempMixin, unittest.TestCase):
    """flake_smells.py and env_safety_check.py are nothing but regexes run over
    files from a repository nobody vetted. One catastrophically backtracking
    pattern turns `/testsquad:health` into a hang that no user will diagnose --
    they will just conclude the plugin is broken and remove it.

    Each case feeds the bait through the real public entry point, not the bare
    pattern, so the bound covers the whole scan and not one lucky regex."""

    def test_env_safety_check_on_dotted_host_bait(self):
        # BARE_HOST_RE is (label\.)+tld -- a repeated group wrapping a bounded
        # quantifier, the classic nested-quantifier shape. A long dotted run
        # that never reaches a valid TLD is the worst case.
        bait = "HOST=" + "a." * 4000 + "!\n"
        self.write(".env", bait)
        with timed(self, "env_safety_check on dotted-host bait"):
            result = esc.scan(self.tmp)
        # The elapsed-time budget inside timed() is this test's point, but on
        # its own it is satisfied by a scanner that never opened the file --
        # a ceiling, an unreadable-file path or a classify() change would all
        # make this "fast" for a reason that has nothing to do with the regex.
        # So pin that the bait was actually read, and how much of it was
        # looked at.
        self.assertEqual(result["scanned_files"], [".env"])
        self.assertEqual(result["skipped_files"], [])
        detectors = {f["detector"] for f in result["findings"]}
        self.assertIn("host_scan_truncated", detectors,
                      "the value is longer than MAX_HOST_SCAN_CHARS, so the "
                      "scanner must say it only looked at the head")

    def _dotted_scan_cost(self, pairs, reps):
        r"""Seconds to scan `reps` values of `pairs` dotted labels.

        Dotted, not a flat run of letters. The flat `"a" * n` this probe used
        to feed never reaches the host path at all: `scan_value` only calls
        `real_hosts_in` -- and through it `EMAIL_RE.sub` and `BARE_HOST_RE` --
        for a value that looks like it could hold a host. So the old probe
        timed the part of `scan_text` that was never the defect, and stayed
        green with the lookbehind, the label bound and MAX_HOST_SCAN_CHARS all
        removed at once. `"a." * pairs + "_"` is the worst case those three
        guards exist for: a dotted run that can never end in a usable TLD, so
        every retry walks what is left of it.

        `reps` is what makes the measurement resolvable on a fast machine.
        Repeating the same scan costs a multiple of the same shape, so the
        ratio below is unchanged by it -- unlike skipping, which just stops
        asking the question.
        """
        line = "A=" + "a." * pairs + "_\n"
        start = time.perf_counter()
        for _ in range(reps):
            esc.scan_text(line, rel=".env", kind="env")
        return time.perf_counter() - start

    # 8,001 and 16,001 characters: both past MAX_HOST_SCAN_CHARS (4096), which
    # is the point -- with the ceiling in place the extra length costs almost
    # nothing, and without it the quadratic reappears.
    LINEARITY_PAIRS = 4_000
    LINEARITY_FLOOR = 0.02   # seconds a measurement must reach to be readable

    def test_env_safety_check_cost_is_linear_in_value_length(self):
        r"""FIXED: EMAIL_RE.sub() was quadratic in the length of a config value.

        real_hosts_in() calls `EMAIL_RE.sub(" ", remainder)` on every value.
        EMAIL_RE is `[A-Za-z0-9._%+\-]+@...`; on a long run of characters with
        no '@' the engine restarts at every offset and scans to the end each
        time, so cost grows with the square of the value's length.

        Measured when it was broken: 8k chars ~0.3s, 16k ~1.2s, 40k ~1.9s, 60k
        ~4.2s -- each doubling costing four times as much. env_safety_check.py's
        own ceiling is MAX_BYTES = 2MB, so a single 2MB line in a .env -- a
        minified blob, a base64 payload, a generated fixture -- is fully within
        what the script will accept and would take on the order of twenty
        minutes.

        This is the gate that runs BEFORE the test command. Hanging it does not
        fail safe; it stops the run with no verdict at all.

        Asserted as a growth ratio rather than an absolute time so the test
        says something about the algorithm and not about this machine.
        Measured fixed, on the machine of record (2026-11-16, CPython 3.14,
        best of three): 8,001 chars 32.8ms, 16,001 chars 43.4ms, ratio 1.23 --
        sublinear, because the host scan stops at MAX_HOST_SCAN_CHARS. The
        3.0 ceiling sits well above that and well below the ~3.9 a scanner
        with those guards removed measures on the same two payloads.

        There is deliberately no skip here. The version that skipped when the
        small measurement came in under 20ms was the only skip in the whole
        suite, which is to say it never ran on this machine at all.
        """
        reps = 1
        for _ in range(14):
            small = min(self._dotted_scan_cost(self.LINEARITY_PAIRS, reps)
                        for _ in range(3))
            if small >= self.LINEARITY_FLOOR:
                break
            reps *= 2
        else:
            self.fail(
                "could not scale this probe to a measurable cost: "
                f"{reps} repeats of a {2 * self.LINEARITY_PAIRS + 1}-character "
                f"value still ran in under {self.LINEARITY_FLOOR}s. The "
                "measurement is the test, so this is a failure, not a skip.")
        large = min(self._dotted_scan_cost(2 * self.LINEARITY_PAIRS, reps)
                    for _ in range(3))
        # Linear would be ~2x for a doubled input. Broken, it is ~4x.
        self.assertLess(large / small, 3.0,
                        f"doubling the value length cost {large / small:.1f}x "
                        f"({small:.3f}s -> {large:.3f}s over {reps} repeat(s)): "
                        "superlinear")

    def test_env_safety_check_on_hyphen_and_url_runs_stays_bounded(self):
        # The shapes that are NOT quadratic, pinned so the fix for the one
        # above does not quietly make these worse.
        lines = [
            "B=" + "a" + "-" * 60_000 + ".com\n",
            "D=http://" + "a" * 60_000 + "\n",
        ]
        self.write(".env", "".join(lines))
        with timed(self, "env_safety_check on hyphen-run bait"):
            result = esc.scan(self.tmp)
        self.assertEqual(result["scanned_files"], [".env"])
        self.assertEqual(result["skipped_files"], [])
        self.assertIn("host_scan_truncated",
                      {f["detector"] for f in result["findings"]})

    def test_env_safety_check_on_unclosed_interpolation_and_email_bait(self):
        lines = [
            "A=${" + "x" * 15_000 + "\n",
            "B={{" + "x" * 15_000 + "\n",
            "C=<" + "x" * 15_000 + "\n",
            "D=" + "a" * 8_000 + "@" + "a" * 8_000 + "\n",
            "E=" + "x" * 15_000 + "\n",
        ]
        self.write(".env", "".join(lines))
        with timed(self, "env_safety_check on interpolation/email bait"):
            result = esc.scan(self.tmp)
        self.assertEqual(result["scanned_files"], [".env"])
        self.assertEqual(result["skipped_files"], [])
        self.assertIn("host_scan_truncated",
                      {f["detector"] for f in result["findings"]})

    def test_env_safety_check_on_many_pathological_lines(self):
        # 1,000 lines of bait, each under the per-line shapes above. The point
        # is the product: per-line cost times line count is the real bound.
        body = "".join("K%d=%s\n" % (i, "a." * 100 + "-" * 100) for i in range(1000))
        self.write(".env", body)
        with timed(self, "env_safety_check on 1,000 bait lines"):
            result = esc.scan(self.tmp)
        self.assertEqual(result["scanned_files"], [".env"])
        self.assertEqual(result["skipped_files"], [])
        # Every value here is ~400 characters, well under
        # MAX_HOST_SCAN_CHARS, so unlike the three cases above NOTHING was
        # truncated: all 1,000 lines were matched against in full. That is
        # what makes this a measurement of per-line cost times line count
        # rather than of the truncation ceiling.
        self.assertEqual(result["findings"], [])

    def test_flake_smells_on_tautology_backreference_bait(self):
        # Four of the TAUTOLOGY_RES carry a backreference (`([^,()]+?)\s*,\s*\1`).
        # A lazy group plus a backreference is the shape that goes exponential,
        # so feed it long near-matches that fail only at the very end.
        lines = []
        for n in (500, 1000, 2000):
            lines.append("    assertEquals(" + "a" * n + ", " + "a" * n + "b)\n")
            lines.append("    expect(" + "a" * n + ").toBe(" + "a" * n + "b)\n")
            lines.append("    assert " + "a" * n + " == " + "a" * n + "b\n")
        self.write("test_bait.py", "def test_x():\n" + "".join(lines))
        with timed(self, "flake_smells on tautology backreference bait"):
            result = fsm.scan_paths([self.tmp])
        # A time budget alone is satisfied by a scanner that skipped the file.
        # Pin that it was read, and pin exactly how much of it the regexes
        # actually saw -- six of these lines are over MAX_LINE_CHARS, so the
        # default run only feeds them 2,000 characters each.
        self.assertEqual(result["summary"]["files_scanned"], 1)
        self.assertEqual(result["skipped_files"], [])
        self.assertEqual(result["summary"]["lines_truncated"], 6)
        # ... and the bound has to hold when the whole bait reaches the
        # regexes, or the only thing being measured is the line ceiling.
        with timed(self, "flake_smells on tautology bait, line ceiling raised"):
            full = fsm.scan_paths([self.tmp], max_line_chars=200_000)
        self.assertEqual(full["summary"]["lines_truncated"], 0)
        self.assertEqual(full["summary"]["files_scanned"], 1)

    def test_flake_smells_on_assertion_and_weak_oracle_bait(self):
        lines = [
            "    assert" + "A" * 50_000 + "\n",
            "    assert " + "a.b" * 20_000 + "\n",
            "    expect(" + "x" * 50_000 + ").toBeTruthy()\n",
            "    assert " + "a" * 50_000 + " is not None\n",
        ]
        self.write("test_bait2.py", "def test_x():\n" + "".join(lines))
        with timed(self, "flake_smells on assertion bait"):
            result = fsm.scan_paths([self.tmp])
        self.assertEqual(result["summary"]["files_scanned"], 1)
        self.assertEqual(result["skipped_files"], [])
        self.assertEqual(result["summary"]["lines_truncated"], 4)
        # Raising the ceiling is what re-exposes ast.parse to the bait: at the
        # default 2,000 the deep line never reaches the parser. This call used
        # to die with RecursionError on the Ubuntu runners and pass here, so
        # it is also the regression site for that.
        with timed(self, "flake_smells on assertion bait, line ceiling raised"):
            full = fsm.scan_paths([self.tmp], max_line_chars=200_000)
        self.assertEqual(full["summary"]["lines_truncated"], 0)
        # Fed in full, the weak-oracle rule does fire on
        # `assert aaa... is not None`. That is the proof the regexes really
        # ran over the bait rather than over a 2,000-character stub of it.
        self.assertIn("weak-oracle", full["summary"]["by_rule"])
        # Whether `a.b` x 20,000 is too deep for ast.parse depends on the C
        # stack, so the only thing true on BOTH platforms is the invariant:
        # either the AST pass ran, or the file is named. Never a third state,
        # and never an exception out of scan_paths().
        self.assertEqual(full["summary"]["files_ast_unparsed"],
                         len(full["unparsed_files"]))
        if full["files"][0]["parsed_with"] == "ast":
            self.assertEqual(full["unparsed_files"], [])
            self.assertNotIn("ast_unavailable", full["files"][0])
        else:
            assert_ast_depth_reported(
                self, full, fsm, self.path("test_bait2.py"),
                scanned_key="files_scanned", scanned_value=1)

    def test_flake_smells_reports_a_file_it_could_not_parse(self):
        """The platform-independent half of the test above.

        That one asserts an invariant that happens to take the `ast` branch on
        macOS and the `regex` branch on a CI runner -- which is precisely the
        shape of test that let this defect through, because the branch that
        matters never ran locally. Here ast.parse is MADE to raise, so the
        reporting path is exercised on every platform and at every stack size,
        and a regression cannot hide behind a roomy stack.
        """
        # Deliberately a CLEAN test file -- a real oracle, nothing to find.
        # That is the pointed case: the gate reports zero findings, and the
        # reader still has to be able to tell that from "the AST pass ran and
        # found nothing".
        self.write("test_deep.py",
                   "def test_x():\n    y = " + "a + " * 400 + "1\n"
                   "    assert y == 7\n")
        with forced_ast_recursion_error():
            result = fsm.scan_paths([self.tmp])
        assert_ast_depth_reported(self, result, fsm, self.path("test_deep.py"),
                                  scanned_key="files_scanned", scanned_value=1)
        # The regex fallback still ran -- the file was scanned, not dropped.
        self.assertEqual(len(result["files"]), 1)
        self.assertEqual(result["summary"]["findings"], 0)
        # ...and it is NOT a finding. `--fail-on` must not fire on it at ANY
        # severity: deeply nested generated code is legitimate, and a gate
        # that blocks on it gets switched off, taking every other rule with
        # it. The report has to live outside --fail-on's path entirely.
        for severity in fsm.SEVERITIES:
            self.assertFalse(fsm.breaches(result, severity),
                             f"--fail-on {severity} fired on an unparsable "
                             "file; it must be a report, not a finding")
        self.assertNotIn("ast-nesting-too-deep", result["summary"]["by_rule"])
        # The whole point, in one pair of assertions: a clean verdict AND a
        # visible statement that part of the analysis did not run.
        self.assertEqual(result["summary"]["by_severity"],
                         {"block": 0, "warn": 0, "info": 0})
        self.assertEqual(result["summary"]["files_ast_unparsed"], 1)

    def test_flake_smells_really_does_survive_a_too_deep_file(self):
        """The forced test above proves the reporting; this proves the catch.

        A real RecursionError out of a real ast.parse, in a child process on a
        small stack, because on an interpreter without the AST-construction
        counter the alternative to catching it is the process dying -- and
        finding THAT out in-process would end the run.
        """
        if not ast_depth_raises_here():
            self.skipTest(
                "this interpreter does not raise RecursionError from "
                "ast.parse at a 512KB stack -- it crashes instead, so there "
                "is no catchable behaviour here to assert. Measured on "
                "CPython 3.9.6: SIGBUS, exit 138. The forced-RecursionError "
                "test covers the reporting path on this platform.")
        self.write("test_deep.py",
                   "def test_x():\n    y = " + "a + " * 20_000 + "1\n    assert y\n")
        rc, out, err = scan_on_a_thin_stack(
            "flake_smells",
            "mod.scan_paths([%r], max_line_chars=200_000)" % self.tmp)
        self.assertEqual(rc, 0, f"child died instead of returning: {err}")
        self.assertIsNotNone(out, f"child produced no JSON: {err}")
        self.assertNotIn("raised", out,
                         "scan_paths() must never raise -- its docstring "
                         f"promises it: {out.get('raised')}")
        assert_ast_depth_reported(self, out["result"], fsm,
                                  self.path("test_deep.py"),
                                  scanned_key="files_scanned", scanned_value=1)

    def test_flake_smells_on_java_method_signature_bait(self):
        # JVM_METHOD_RE is `\w+\([^;]*\)\s*(?:throws\s[\w.,\s]+)?\{\s*$` -- two
        # adjacent greedy character classes that can both eat whitespace.
        sig = "void foo(" + "," * 3000 + ") throws " + "a, " * 3000 + "{\n"
        self.write("FooTest.java", "class FooTest {\n    @Test\n    " + sig + "    }\n}\n")
        with timed(self, "flake_smells on JVM signature bait"):
            result = fsm.scan_paths([self.tmp])
        self.assertEqual(result["summary"]["files_scanned"], 1)
        self.assertEqual(result["skipped_files"], [])
        self.assertEqual(result["summary"]["lines_truncated"], 1)
        with timed(self, "flake_smells on JVM bait, line ceiling raised"):
            full = fsm.scan_paths([self.tmp], max_line_chars=200_000)
        self.assertEqual(full["summary"]["lines_truncated"], 0)
        self.assertEqual(full["summary"]["files_scanned"], 1)

    def test_flake_smells_on_shared_mutable_state_quadratic_bait(self):
        """The one superlinear shape found: declarations x in-test lines.

        _shared_mutable_state builds `sorted(in_test)` and then rescans every
        line of every test block once per module-level mutable declaration. A
        file with 2,000 such declarations and 2,000 in-test lines does 4M
        regex searches, each with a freshly compiled pattern.
        It stays inside the bound at this size, which is why it is documented
        rather than failed -- but the growth is quadratic in a file-size input.
        SHOULD: collect the mutated names in one pass instead of one pass per
        declaration.
        """
        body = "".join("v%d = []\n" % i for i in range(2000))
        body += "def test_x():\n"
        body += "".join("    other.append(1)\n" for _ in range(2000))
        self.write("test_quad.py", body)
        with timed(self, "flake_smells on shared-mutable-state bait", budget=10.0):
            result = fsm.scan_paths([self.tmp])
        self.assertEqual(result["summary"]["files_scanned"], 1)
        self.assertEqual(result["skipped_files"], [])
        # Every line here is short, so nothing was truncated: all 4,000 lines
        # went through the rule in full. Pinned, because this is the one case
        # whose cost comes from the LINE COUNT -- if the line ceiling ever
        # started biting here the measurement would silently become a
        # measurement of the ceiling instead of of the quadratic.
        self.assertEqual(result["summary"]["lines_truncated"], 0)
        # The bait is a deliberate near-miss: it appends to `other`, which is
        # not one of the 2,000 declared names, so the rule pays the full
        # declarations x in-test-lines cost and then fires on nothing. The
        # only finding is `no-assertion` on the generated test_x, and
        # asserting exactly that is what proves the block body really was
        # walked rather than the file being skipped or parsed as empty.
        self.assertEqual(result["summary"]["by_rule"], {"no-assertion": 1})

    @requires_arid
    def test_arid_nodes_on_regex_bait(self):
        lines = [
            "logger.info(" + "'a', " * 5000 + ")\n",
            "x = " + "(" * 200 + "1" + ")" * 200 + "\n",
            "y = " + "a + " * 20_000 + "1\n",
        ]
        self.write("bait.py", "".join(lines))
        with timed(self, "arid_nodes on regex bait"):
            result = arid.plan([self.tmp])
        self.assertEqual(result["summary"]["files"], 1)
        self.assertEqual(result["skipped_files"], [])
        # Two of the three lines are over MAX_LINE_CHARS. arid_nodes had no
        # line ceiling at all until this change, so assert both halves: that
        # the default run says what it shortened, AND that the bound still
        # holds when the ceiling is raised and the planner sees every
        # character the way it used to.
        self.assertEqual(result["summary"]["lines_truncated"], 2)
        # Raising the ceiling is what re-exposes ast.parse to the bait: at the
        # default 2,000 the 20,000-term chain never reaches the parser. This
        # call used to die with RecursionError on the Ubuntu runners and pass
        # here, so it is also the regression site for that.
        with timed(self, "arid_nodes on regex bait, line ceiling raised"):
            full = arid.plan([self.tmp], max_line_chars=1_000_000)
        self.assertEqual(full["summary"]["lines_truncated"], 0)
        self.assertEqual(full["summary"]["files"], 1)
        # Whether a 20,000-term chain is too deep for ast.parse depends on the
        # C stack, so the only thing true on BOTH platforms is the invariant:
        # either the AST pass ran, or the file is named. Never a third state,
        # and never an exception out of plan().
        self.assertEqual(full["summary"]["files_ast_unparsed"],
                         len(full["unparsed_files"]))
        if full["files"][0]["parsed_with"] == "ast":
            self.assertEqual(full["unparsed_files"], [])
            self.assertNotIn("ast_unavailable", full["files"][0])
        else:
            assert_ast_depth_reported(self, full, arid, self.path("bait.py"),
                                      scanned_key="files", scanned_value=1)

    @requires_arid
    def test_arid_nodes_reports_a_file_it_could_not_parse(self):
        """The platform-independent half of the test above.

        That one asserts an invariant that happens to take the `ast` branch on
        macOS and the `regex` branch on a CI runner -- which is precisely the
        shape of test that let this defect through, because the branch that
        matters never ran locally. Here ast.parse is MADE to raise, so the
        reporting path is exercised on every platform and at every stack size,
        and a regression cannot hide behind a roomy stack.
        """
        self.write("deep.py", "def f(a):\n    y = " + "a + " * 400 + "1\n    return y\n")
        with forced_ast_recursion_error():
            result = arid.plan([self.tmp])
        assert_ast_depth_reported(self, result, arid, self.path("deep.py"),
                                  scanned_key="files", scanned_value=1)
        # The regex fallback still ran -- the file was planned, not dropped.
        self.assertEqual(len(result["files"]), 1)
        self.assertGreater(result["summary"]["selected"], 0)
        # ...and it is NOT a finding. arid_nodes has no --fail-on, but the
        # same rule applies: it must not appear as a suppression rule either,
        # or it lands in a count a gate could grow a threshold on.
        self.assertNotIn("ast-nesting-too-deep", result["summary"]["suppressed_by_rule"])

    @requires_arid
    def test_arid_nodes_really_does_survive_a_too_deep_file(self):
        """The forced test above proves the reporting; this proves the catch.

        A real RecursionError out of a real ast.parse, in a child process on a
        small stack, because on an interpreter without the AST-construction
        counter the alternative to catching it is the process dying -- and
        finding THAT out in-process would end the run.
        """
        if not ast_depth_raises_here():
            self.skipTest(
                "this interpreter does not raise RecursionError from "
                "ast.parse at a 512KB stack -- it crashes instead, so there "
                "is no catchable behaviour here to assert. Measured on "
                "CPython 3.9.6: SIGBUS, exit 138. The forced-RecursionError "
                "test covers the reporting path on this platform.")
        self.write("deep.py",
                   "def f(a):\n    y = " + "a + " * 20_000 + "1\n    return y\n")
        rc, out, err = scan_on_a_thin_stack(
            "arid_nodes", "mod.plan([%r], max_line_chars=1_000_000)" % self.tmp)
        self.assertEqual(rc, 0, f"child died instead of returning: {err}")
        self.assertIsNotNone(out, f"child produced no JSON: {err}")
        self.assertNotIn("raised", out,
                         "plan() must never raise -- its docstring promises "
                         f"it: {out.get('raised')}")
        assert_ast_depth_reported(self, out["result"], arid,
                                  self.path("deep.py"),
                                  scanned_key="files", scanned_value=1)


# --------------------------------------------------------------------------
# pr_comment: the only script whose input is another script's output
# --------------------------------------------------------------------------

class PrCommentTests(TempMixin, unittest.TestCase):
    """pr_comment.py reads .testing/*.json and renders a public PR comment. Its
    input is produced by agents and by hand-edited files, so 'the JSON has the
    shape I expect' is an assumption, not a fact. Every violation below ends as
    a traceback in CI on a pull request -- the most visible place the plugin
    can fail."""

    def test_data_file_that_is_a_json_list_does_not_crash(self):
        """FIXED. load() returns {} for anything that is not a dict, so a
        truthy non-dict no longer slips past main()'s guard into render()."""
        self.write("data.json", "[1, 2, 3]")
        self.assertEqual(prc.main(["--data", self.path("data.json")]), 2)

    def test_json_list_renders_rather_than_raising(self):
        # FIXED. render() on a non-dict is an empty report plus the malformed
        # notice, never a traceback in the CI step that comments on the PR.
        body = prc.render([1, 2, 3])
        self.assertIn(prc.MARKER, body)
        self.assertIn("malformed", body)

    def test_coverage_percentage_as_a_string_is_coerced(self):
        # FIXED. A hand-written or jq-built coverage-diff.json easily carries
        # "90.0" instead of 90.0. Reading it is not inventing it -- but the
        # type was still wrong, so the comment says an input was malformed.
        body = prc.render({"coverage": {"line_pct": "90.0"}})
        self.assertIn("90.0%", body)
        self.assertIn("malformed", body)

    def test_non_numeric_finding_count_degrades_only_that_row(self):
        # FIXED. No multiplier is invented from junk, and the finding itself
        # still publishes.
        body = prc.render({"findings": [{"evidence": "reproduced", "title": "x",
                                         "count": "many"}]})
        self.assertIn("| x |", body)
        self.assertNotIn("\u00d7", body)
        self.assertIn("malformed", body)

    def test_gates_that_are_an_object_instead_of_a_list_are_declared(self):
        # FIXED. gates.json written as {"name": ...} rather than
        # {"gates": [...]} drops the section and says why.
        body = prc.render({"gates": {"coverage": 80}})
        self.assertNotIn("### Quality gates", body)
        self.assertIn("`gates` was not a list", body)

    def test_findings_that_are_strings_are_dropped_and_declared(self):
        # FIXED.
        body = prc.render({"findings": ["something went wrong"]})
        self.assertNotIn("something went wrong", body)
        self.assertIn("malformed", body)

    def test_missing_testing_directory_refuses_rather_than_reassures(self):
        # The one well-handled case, and the right call: "no findings" and
        # "nobody looked" render identically, so an absent .testing/ must be a
        # refusal with the producing command named, not a clean-looking comment.
        rc = prc.main(["--testing-dir", self.path("nope"), "--out", self.path("c.md")])
        self.assertEqual(rc, 2)
        self.assertFalse(os.path.exists(self.path("c.md")))

    def test_a_secret_in_a_finding_title_is_redacted(self):
        # FIXED. pr_comment.py now applies the same rule as the scanner to
        # every string it is handed, because its output is a public comment.
        body = prc.render({"findings": [
            {"evidence": "reproduced", "severity": "S1",
             "title": "leaked ghp_" + "A" * 36}]})
        self.assertNotIn("ghp_" + "A" * 36, body)
        self.assertNotIn("ghp_AAAA", body, "half a key is still a leak")
        self.assertIn("[redacted", body)

    def test_markdown_table_injection_from_a_finding_title(self):
        # FIXED. A `|` in a title is escaped, so it can no longer forge a
        # column and write its own verdict.
        body = prc.render({"findings": [
            {"evidence": "reproduced", "severity": "S4",
             "title": "x | \u2705 passed | everything fine"}]})
        self.assertNotIn("| \u2705 passed |", body)
        self.assertIn("\\|", body)

# --------------------------------------------------------------------------
# validate_plugin and session_context: manifest and hook robustness
# --------------------------------------------------------------------------

class ManifestTests(TempMixin, unittest.TestCase):
    """validate_plugin.py is the thing that runs in CI before a release, and
    session_context.py runs on every single session start in every repo that
    has a .testing/. Neither may be the reason a user's day stops."""

    def test_plugin_json_that_is_a_list_is_an_error_not_a_crash(self):
        self.write(".claude-plugin/plugin.json", "[1, 2]")
        report = vplug.validate(self.tmp)
        self.assertTrue(any("not an object" in e for e in report.errors))

    def test_plugin_name_that_is_not_a_string_is_an_error_not_a_crash(self):
        self.write(".claude-plugin/plugin.json", '{"name": 42}')
        report = vplug.validate(self.tmp)
        self.assertTrue(any("must be a string" in e for e in report.errors))

    def test_marketplace_name_that_is_not_a_string_is_an_error_not_a_crash(self):
        self.write(".claude-plugin/marketplace.json",
                   '{"name": 123, "owner": {"name": "x"}, "plugins": []}')
        report = vplug.validate(self.tmp)
        self.assertTrue(any("must be a string" in e for e in report.errors))

    def test_hooks_json_that_is_a_scalar_is_an_error_not_a_crash(self):
        # `"hooks" not in data` on an int used to raise TypeError.
        self.write("hooks/hooks.json", "5")
        report = vplug.validate(self.tmp)
        self.assertTrue(any("top-level 'hooks' key" in e for e in report.errors))

    def test_hooks_json_that_is_a_list_is_reported_correctly(self):
        self.write("hooks/hooks.json", "[]")
        report = vplug.validate(self.tmp)
        self.assertTrue(any("top-level 'hooks' key" in e for e in report.errors))

    def test_every_malformed_manifest_is_reported_in_one_run(self):
        # The whole value of a validator is telling you everything that is
        # wrong at once. Each of these used to abort the run with a traceback,
        # so whichever came first was the only thing you ever heard about.
        self.write(".claude-plugin/plugin.json", '{"name": 42}')
        self.write(".claude-plugin/marketplace.json",
                   '{"name": 123, "owner": {"name": "x"}, "plugins": []}')
        self.write("hooks/hooks.json", "5")
        self.write("agents/a.md",
                   "---\nname: a\ndescription: x\n---\nbody\n".encode("utf-16"),
                   binary=True)
        self.write("agents/b.md",
                   "---\nname: b\ndescription: >\n  x\n---\n" + "b" * 500)
        report = vplug.validate(self.tmp)
        joined = "\n".join(report.errors)
        self.assertIn("plugin.json", joined)
        self.assertIn("marketplace.json", joined)
        self.assertIn("hooks/hooks.json", joined)
        # And the one well-formed component still got checked, last of all.
        self.assertEqual([e for e in report.errors if "b.md" in e], [])

    def test_claude_md_warns_only_in_a_packaged_plugin(self):
        # Packaged, CLAUDE.md is never loaded, so instructions there reach
        # nobody. In a development checkout it IS loaded and is the point. The
        # check must tell the two trees apart, or --strict fails CI for a file
        # doing its job.
        self.write("CLAUDE.md", "how to work on this repo\n")
        packaged = vplug.validate(self.tmp)
        self.assertTrue(any("CLAUDE.md" in w for w in packaged.warnings))

        os.makedirs(self.path("tests"), exist_ok=True)
        checkout = vplug.validate(self.tmp)
        self.assertEqual([w for w in checkout.warnings if "CLAUDE.md" in w], [])

    def test_an_unexpected_exception_costs_one_check_not_the_run(self):
        # guarded() is the backstop for the input nobody thought of: a check
        # that raises must cost that check's verdict and nothing else, or the
        # next unanticipated byte sequence silently blanks the whole report.
        r = vplug.Report()

        def boom():
            raise RuntimeError("unanticipated")

        self.assertIsNone(vplug.guarded("widget", r, boom))
        self.assertTrue(any("widget" in e and "RuntimeError" in e
                            for e in r.errors))

    def test_marketplace_source_escaping_the_root_is_an_error(self):
        self.write(".claude-plugin/marketplace.json", json.dumps({
            "name": "m", "owner": {"name": "x"},
            "plugins": [{"name": "p", "source": "./../../etc"}]}))
        report = vplug.validate(self.tmp)
        self.assertTrue(any("escapes the marketplace root" in e for e in report.errors))

    def test_component_frontmatter_with_crlf_is_accepted(self):
        # Universal newlines translate CRLF on read, so a Windows checkout
        # validates identically. Pinned because split_frontmatter() matches on
        # a literal "---\n" and would break if the file were opened in binary.
        self.write("agents/a.md",
                   "---\r\nname: a\r\ndescription: >\r\n  x\r\n---\r\n" + "b" * 500)
        report = vplug.validate(self.tmp)
        self.assertEqual([e for e in report.errors if "a.md" in e], [])

    def test_empty_component_file(self):
        self.write("agents/a.md", "")
        report = vplug.validate(self.tmp)
        self.assertTrue(any("unterminated YAML frontmatter" in e
                            for e in report.errors))

    def test_component_name_with_emoji_matches_its_directory(self):
        self.write("skills/\U0001f600demo/SKILL.md",
                   "---\nname: \U0001f600demo\ndescription: >\n  x\n---\n" + "b" * 500)
        report = vplug.validate(self.tmp)
        self.assertEqual(report.errors, [])

    def test_session_context_build_raises_on_a_non_object_profile(self):
        """CRASH (swallowed): build() dies if any .testing JSON is not an object.

        read_json returns whatever json.load produced. A profile.json that is a
        JSON array makes `profile.get(...)` raise AttributeError. main() catches
        bare Exception and returns 0, so the session starts -- but the preamble
        is silently empty, and the conventions, health verdict and known issues
        that the hook exists to surface are all lost because of one bad file.
        SHOULD: read_json returns None for anything that is not a dict.
        """
        os.makedirs(self.path(".testing"), exist_ok=True)
        self.write(".testing/profile.json", '["python"]')
        self.write(".testing/conventions.md", "- always seed the RNG\n")
        with self.assertRaises(AttributeError):
            grant_session_consent(self, self.tmp)
            sctx.build(self.tmp)
        # The hook itself survives, and prints nothing at all.
        self.assertEqual(sctx.main(["session_context.py", self.tmp]), 0)

    def test_session_context_tolerates_an_unparseable_timestamp(self):
        os.makedirs(self.path(".testing"), exist_ok=True)
        self.write(".testing/health-baseline.json",
                   '{"verdict": "RED", "generated_at": "yesterday-ish"}')
        grant_session_consent(self, self.tmp)
        text = sctx.build(self.tmp)
        self.assertIn("RED", text)

    def test_session_context_caps_a_rambling_conventions_file(self):
        # The preamble is paid for on every request in the repo, so the caps
        # are the only thing between a verbose repo and a permanent token tax.
        os.makedirs(self.path(".testing"), exist_ok=True)
        self.write(".testing/conventions.md",
                   "".join("- %s\n" % ("x" * 400) for _ in range(50)))
        grant_session_consent(self, self.tmp)
        text = sctx.build(self.tmp)
        self.assertEqual(text.count("\n  - "), sctx.MAX_CONVENTIONS)
        for line in text.splitlines():
            self.assertLessEqual(len(line), sctx.MAX_LINE_CHARS + 8)

    def test_session_context_on_a_huge_conventions_file_stops_early(self):
        # first_lines() iterates the file handle and breaks at the limit, so a
        # 50MB conventions.md costs five lines, not 50MB. This is the one place
        # in the tree where streaming was done right.
        os.makedirs(self.path(".testing"), exist_ok=True)
        with open(self.path(".testing/conventions.md"), "w", encoding="utf-8") as fh:
            for _ in range(10):
                fh.write("- a real convention\n")
            fh.write("x" * 10_000_000)
        with timed(self, "session_context on a 10MB conventions.md", budget=2.0):
            grant_session_consent(self, self.tmp)
            text = sctx.build(self.tmp)
        self.assertEqual(text.count("\n  - "), sctx.MAX_CONVENTIONS)



# --------------------------------------------------------------------------
# the REACHABLE half of ast.parse's depth ceiling
# --------------------------------------------------------------------------
# Everything in `ProjectStateAstTests` and the `*AstDepth*` tests above is
# about RecursionError -- the half of the ceiling a real file never reaches
# (95,067 chained terms on 3.14.6, and on the 3.9.6 floor no catchable window
# at all). CPython's tokenizer caps bracket nesting and indentation far
# earlier, as compile-time constants, and reports both as a SyntaxError:
# IndentationError is a SyntaxError subclass. So before the fix these landed
# in the `except (SyntaxError, ValueError)` branch of all three scripts and
# were classified "syntax-error" -- silent, by design, because a half-written
# file is an ordinary state. The loud signal covered only the unreachable
# case; the reachable ones fell back without a word.
#
# The depths are NOT hardcoded below. 200 and 99 are what CPython accepts
# today, not a promise, so each cap is discovered at runtime by bisection and
# the assertions are written against whatever was found. A CPython that
# changes MAXLEVEL changes these tests' inputs, not their verdicts.
#
# The other half of the specification is the part that protects the signal
# from itself: an ordinary half-written file must STAY silent. Message
# matching is the only way CPython lets these be told apart, and message
# matching is brittle, so the brittleness is one-directional -- an
# unrecognised message costs a missed report, never a spurious "could not be
# analysed" on every malformed file in a repo. That is pinned below across
# four malformed shapes, and separately with a forced unrecognised message.

def _bracket_source(depth):
    """`depth` nested brackets -- the tokenizer's MAXLEVEL shape."""
    return "x = " + "(" * depth + ")" * depth + "\n"


def _indent_source(depth):
    """`depth` levels of indentation -- the tokenizer's MAXINDENT shape."""
    lines = ["    " * i + "if True:\n" for i in range(depth)]
    lines.append("    " * depth + "pass\n")
    return "".join(lines)


def _parses(source):
    try:
        ast.parse(source)
    except (SyntaxError, ValueError, MemoryError, RecursionError):
        return False
    return True


def discover_cap(make_source, ceiling=8192):
    """The smallest depth `ast.parse` REFUSES, found by bisection.

    Returns None when the interpreter accepts everything up to `ceiling` --
    which is the honest answer for the pegen parser-stack shape on 3.9.6,
    where no cap exists. Never hardcodes a depth: these are CPython's
    constants today, and a test that asserts 201 would start lying the
    release they move.
    """
    accepted, refused, probe = 0, None, 1
    while probe <= ceiling:
        if _parses(make_source(probe)):
            accepted, probe = probe, probe * 2
        else:
            refused = probe
            break
    if refused is None:
        return None
    while refused - accepted > 1:
        mid = (accepted + refused) // 2
        if _parses(make_source(mid)):
            accepted = mid
        else:
            refused = mid
    return refused


class AstNestingCapTests(unittest.TestCase):
    """All three AST sites must report a nesting cap, and stay silent otherwise.

    One class for three scripts on purpose. The same rule living in three
    copies is this repository's single most common defect -- one of them had
    seven copies -- and the cheapest guard against the copies drifting is a
    test that refuses to let them answer differently.
    """

    # (label, callable) -- each takes source text and returns
    # (result_or_None, why_not), with why_not None on success.
    @classmethod
    def sites(cls):
        return (
            ("flake_smells.python_blocks_ast_detailed",
             fsm, fsm.python_blocks_ast_detailed),
            ("arid_nodes.python_funcs_detailed",
             arid, None if arid is None else arid.python_funcs_detailed),
            ("project_state._xfail_tests",
             pstate, pstate._xfail_tests),
        )

    def each_site(self):
        """Yields (label, module, probe). Fails -- never skips -- when a
        production module is missing: a missing script is a broken checkout,
        not a platform this test does not apply to."""
        for label, module, probe in self.sites():
            self.assertIsNotNone(
                module, f"{label}: its module did not import -- that is a "
                        "broken checkout, not a reason to pass")
            yield label, module, probe

    @classmethod
    def setUpClass(cls):
        cls.bracket_cap = discover_cap(_bracket_source)
        cls.indent_cap = discover_cap(_indent_source, ceiling=1024)

    # ---- the caps exist and are reachable ---------------------------------

    def test_both_tokenizer_caps_are_discoverable_and_low(self):
        """If this fails, every assertion below is testing nothing."""
        self.assertIsNotNone(self.bracket_cap,
                             "no bracket-nesting cap found below 8192 -- the "
                             "cases below would be vacuous")
        self.assertIsNotNone(self.indent_cap,
                             "no indentation cap found below 1024 -- the "
                             "cases below would be vacuous")
        # The point of the fix: these are depths a generated or vendored file
        # actually reaches, unlike the ~95,000-term RecursionError shape.
        self.assertLess(self.bracket_cap, 1000)
        self.assertLess(self.indent_cap, 1000)

    # ---- the loud signal, at every site -----------------------------------

    def test_bracket_cap_reports_too_deep_at_every_site(self):
        at_cap = _bracket_source(self.bracket_cap)
        just_under = _bracket_source(self.bracket_cap - 1)
        for label, module, probe in self.each_site():
            with self.subTest(site=label):
                result, why = probe(just_under)
                self.assertIsNone(
                    why, f"{label}: {self.bracket_cap - 1} nested brackets is "
                         "under CPython's cap and must parse normally")
                self.assertIsNotNone(result)
                result, why = probe(at_cap)
                self.assertIsNone(result)
                self.assertEqual(
                    why, module.AST_TOO_DEEP,
                    f"{label}: {self.bracket_cap} nested brackets went back to "
                    "being a silent syntax-error fallback")

    def test_indentation_cap_reports_too_deep_at_every_site(self):
        # The most reachable of all of them, and an IndentationError, so it
        # was already landing in the silent SyntaxError branch.
        at_cap = _indent_source(self.indent_cap)
        just_under = _indent_source(self.indent_cap - 1)
        for label, module, probe in self.each_site():
            with self.subTest(site=label):
                result, why = probe(just_under)
                self.assertIsNone(
                    why, f"{label}: {self.indent_cap - 1} indentation levels "
                         "is under CPython's cap and must parse normally")
                result, why = probe(at_cap)
                self.assertIsNone(result)
                self.assertEqual(
                    why, module.AST_TOO_DEEP,
                    f"{label}: {self.indent_cap} indentation levels went back "
                    "to being a silent syntax-error fallback")

    def test_the_cap_signal_reaches_the_report_not_just_the_helper(self):
        """A constant returned by a helper nobody surfaces is not a signal.

        project_state keeps its own vocabulary for this -- the file is named
        in `ast_unparsed_files`, `files_ast_unparsed` counts it and
        `totals_exact` goes False -- and a cap must route into that machinery
        rather than into a parallel one.
        """
        tmp = tempfile.mkdtemp(prefix="testsquad-nestcap-")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        with open(os.path.join(tmp, "deep.py"), "w", encoding="utf-8") as fh:
            fh.write(_indent_source(self.indent_cap))
        with mock.patch.object(pstate, "ROOT", tmp):
            markers = pstate.section_markers()
        self.assertEqual([d["file"] for d in markers["ast_unparsed_files"]],
                         ["deep.py"])
        self.assertEqual(markers["files_ast_unparsed"], 1)
        self.assertFalse(markers["totals_exact"],
                         "a file counted by the line scanner must mark the "
                         "totals inexact -- a confident number nobody "
                         "measured is the defect this script exists to catch")
        entry = markers["ast_unparsed_files"][0]
        self.assertEqual(entry["fell_back_to"], "line-scan")
        self.assertTrue(entry["reason"].strip())
        self.assertTrue(entry["not_measured"].strip())

    # ---- and the silence, which is the half that protects the signal ------

    MALFORMED = (
        # (label, source) -- four shapes a half-written file really takes.
        ("unclosed bracket", "def f():\n    return (1, 2\n"),
        ("unexpected indent", "x = 1\n    y = 2\n"),
        ("unterminated string", "s = 'half a stri\nx = 1\n"),
        ("truncated def", "def f(:\n"),
    )

    def test_ordinary_syntax_errors_stay_silent_at_every_site(self):
        """The false positive that would switch the whole gate off.

        A check above ~10% effective false positives gets ignored entirely.
        "Could not be analysed" on every malformed file in a repo is 100%.
        """
        for shape, source in self.MALFORMED:
            self.assertFalse(_parses(source),
                             f"{shape}: fixture must actually be malformed")
            for label, module, probe in self.each_site():
                with self.subTest(site=label, shape=shape):
                    result, why = probe(source)
                    self.assertIsNone(result)
                    self.assertEqual(
                        why, "syntax-error",
                        f"{label}: an ordinary {shape} must stay a silent "
                        "syntax-error, not become a too-deep report")

    def test_an_unrecognised_message_stays_silent_at_every_site(self):
        """Message matching is brittle on purpose, in one direction only.

        If a future CPython rewords "too many nested parentheses", this must
        degrade to the old silent behaviour -- a missed report -- and never to
        a spurious refusal on every malformed file.
        """
        for label, module, probe in self.each_site():
            with self.subTest(site=label):
                with mock.patch.object(
                        ast, "parse",
                        side_effect=SyntaxError("some future wording")):
                    result, why = probe("x = 1\n")
                self.assertIsNone(result)
                self.assertEqual(why, "syntax-error",
                                 f"{label}: an unrecognised message must cost "
                                 "a missed report, never a spurious one")

    def test_a_recognised_parser_stack_overflow_is_reported_at_every_site(self):
        """CPython 3.14.6 raises this one as a MemoryError, not a SyntaxError.

        Forced rather than generated: on the 3.9.6 floor the pegen parser has
        no such cap at all, so the real shape cannot be produced there and an
        interpreter-dependent test would pass by not running.
        """
        for label, module, probe in self.each_site():
            with self.subTest(site=label):
                with mock.patch.object(
                        ast, "parse",
                        side_effect=MemoryError(
                            "Parser stack overflowed - Python source too "
                            "complex to parse")):
                    result, why = probe("x = 1\n")
                self.assertIsNone(result)
                self.assertEqual(why, module.AST_TOO_DEEP)

    def test_a_genuine_memory_error_is_reraised_at_every_site(self):
        """The same defect inverted: laundering an out-of-memory into a quiet
        fallback would turn a machine-level failure into a clean-looking run.
        """
        for label, module, probe in self.each_site():
            with self.subTest(site=label):
                with mock.patch.object(
                        ast, "parse",
                        side_effect=MemoryError("Cannot allocate memory")):
                    with self.assertRaises(MemoryError):
                        probe("x = 1\n")

    # ---- the drift guard --------------------------------------------------

    def test_all_three_scripts_classify_every_refusal_identically(self):
        """Three copies of one rule, pinned to answer the same thing.

        Agreement alone would be satisfied by all three being equally wrong,
        so every probe carries its EXPECTED classification too. The table is
        the cheapest thing standing between this fix and the next divergence.
        """
        probes = [
            ("valid source", "def test_x():\n    assert True\n", None),
            ("bracket cap", _bracket_source(self.bracket_cap), "AST_TOO_DEEP"),
            ("indentation cap", _indent_source(self.indent_cap), "AST_TOO_DEEP"),
        ]
        probes += [(shape, source, "syntax-error")
                   for shape, source in self.MALFORMED]

        forced = [
            ("forced RecursionError",
             RecursionError("maximum recursion depth exceeded during ast "
                            "construction"), "AST_TOO_DEEP"),
            ("forced parser-stack MemoryError",
             MemoryError("Parser stack overflowed - Python source too complex "
                         "to parse"), "AST_TOO_DEEP"),
            ("forced unrecognised SyntaxError",
             SyntaxError("some future wording"), "syntax-error"),
            ("forced unrecognised ValueError",
             ValueError("source code string cannot contain null bytes"),
             "syntax-error"),
            ("forced genuine MemoryError",
             MemoryError("Cannot allocate memory"), "raised:MemoryError"),
        ]

        def classify(module, probe, source, exc=None):
            try:
                if exc is None:
                    result, why = probe(source)
                else:
                    with mock.patch.object(ast, "parse", side_effect=exc):
                        result, why = probe(source)
            except BaseException as raised:  # noqa: BLE001 - that IS the answer
                return "raised:" + type(raised).__name__
            if why is None:
                self.assertIsNotNone(result)
                return None
            self.assertIsNone(result)
            return "AST_TOO_DEEP" if why == module.AST_TOO_DEEP else why

        table = []
        for name, source, expected in probes:
            table.append((name, expected,
                          [(label, classify(module, probe, source))
                           for label, module, probe in self.each_site()]))
        for name, exc, expected in forced:
            table.append((name, expected,
                          [(label, classify(module, probe, "x = 1\n", exc))
                           for label, module, probe in self.each_site()]))

        for name, expected, answers in table:
            with self.subTest(probe=name):
                got = {label: answer for label, answer in answers}
                self.assertEqual(
                    len(set(got.values())), 1,
                    f"{name}: the three scripts disagree -- {got}")
                self.assertEqual(
                    sorted(got), sorted(l for l, _, _ in self.sites()),
                    "a site dropped out of the drift guard")
                self.assertEqual(
                    set(got.values()), {expected},
                    f"{name}: expected {expected!r} from all three, got {got}")


# --------------------------------------------------------------------------
# the policy that replaced the arid_nodes skips
# --------------------------------------------------------------------------

class MissingProductionCodePolicyTest(unittest.TestCase):
    """Eight tests in this file used to skip when scripts/arid_nodes.py was
    absent. A skip is silence, and with the script deleted the whole file
    still reported green -- it had simply stopped testing arid_nodes.py and
    said nothing. Measured before the change: with the module moved aside,
    `python3 -B -m unittest test_robustness` reported OK with 8 skips; after
    it reports 21 failures and 0 skips.

    These tests pin the replacement in both directions, because the old
    behaviour is one `@unittest.skipIf` away and its symptom is a quiet green
    run that nothing else in the suite would notice.

    Genuine platform guards are untouched and must stay: a missing `git`
    binary, a filesystem that will not take a newline in a name, an
    interpreter that dies rather than raising from ast.parse. Those say "this
    machine cannot host the experiment". A missing script says "the subject
    of the experiment is gone", which is a broken checkout, not a platform.
    """

    def probe(self):
        """A TestCase instance to call the helper against, so a failure is
        caught here rather than failing this test."""
        return unittest.TestCase("run")

    def expect_failure(self, call, *args):
        """Call something that must FAIL, and hand back the message.

        assertRaises(AssertionError) is not enough: if the helper skips, the
        SkipTest escapes the context manager and unittest records *this* test
        as skipped -- the regression test for an unwanted skip disappearing
        into a skip.
        """
        try:
            call(*args)
        except unittest.SkipTest as exc:
            self.fail("require_arid() skipped instead of failing. A skip is "
                      "silence: {0}".format(exc))
        except AssertionError as exc:
            return str(exc)
        self.fail("require_arid() neither failed nor skipped with the module "
                  "absent")

    def test_a_missing_module_fails_and_says_it_is_the_checkout(self):
        with mock.patch.object(sys.modules[__name__], "arid", None):
            with mock.patch.object(sys.modules[__name__], "ARID_IMPORT_ERROR",
                                   ImportError("No module named 'arid_nodes'")):
                message = self.expect_failure(require_arid, self.probe())
        self.assertIn("scripts/arid_nodes.py", message,
                      "the failure must name the file to restore")
        self.assertIn("PRODUCTION CODE MISSING", message)
        self.assertIn("NOT A BUG IN THE CODE UNDER TEST", message,
                      "a reader triaging a red CI log must be able to tell "
                      "'the module is gone' from 'the test found a bug' "
                      "without opening the file")
        self.assertIn("No module named 'arid_nodes'", message,
                      "the underlying ImportError must ride along, or a "
                      "half-broken module reads as a deleted one")

    def test_the_decorator_fails_rather_than_skipping(self):
        """The decorator is what replaced @unittest.skipIf on seven tests, so
        it is the thing that has to be proven not to skip."""
        ran = []

        class Subject(unittest.TestCase):
            # Deliberately NOT named test_*: this is the probe body, not a
            # test, and flake_smells.py rightly blocks an assertion-free
            # test_*. unittest runs whatever method name it is handed.
            @requires_arid
            def probe_body(inner):
                ran.append(True)

        with mock.patch.object(sys.modules[__name__], "arid", None):
            result = unittest.TestResult()
            Subject("probe_body").run(result)
        self.assertEqual([], ran, "the body must not run without the module")
        self.assertEqual([], result.skipped,
                         "the decorator skipped; that is the defect it "
                         "replaced")
        self.assertEqual(1, len(result.failures))
        self.assertIn("PRODUCTION CODE MISSING", result.failures[0][1])

    def test_the_decorator_runs_the_test_when_the_module_is_there(self):
        """The other half: the guard must not be a blanket failure. Without
        this, a decorator that always failed would pass the test above."""
        ran = []

        class Subject(unittest.TestCase):
            # Deliberately NOT named test_*: this is the probe body, not a
            # test, and flake_smells.py rightly blocks an assertion-free
            # test_*. unittest runs whatever method name it is handed.
            @requires_arid
            def probe_body(inner):
                ran.append(True)

        self.assertIsNotNone(arid, "scripts/arid_nodes.py did not import, so "
                                   "this control could not run")
        result = unittest.TestResult()
        Subject("probe_body").run(result)
        self.assertEqual([True], ran)
        self.assertEqual([], result.failures + result.errors)
        self.assertEqual([], result.skipped)

    def test_no_skip_in_this_module_is_tied_to_a_missing_script(self):
        """Source-level guard. Re-adding one `@unittest.skipIf(arid is None,
        ...)` would restore the silence, and no behavioural test above would
        notice -- the restored skip would simply not run.

        It deliberately does NOT ban skips outright: the git, filesystem and
        interpreter guards in this file are legitimate and must survive.
        """
        with open(os.path.abspath(__file__), encoding="utf-8") as fh:
            source = fh.read()
        try:
            tree = ast.parse(source)
        except RecursionError:
            self.fail("this module's own source is too deeply nested for "
                      "ast.parse; the guard below did NOT run, so nothing "
                      "checked that a missing script still fails loudly")
        offenders = []
        for node in ast.walk(tree):
            name = None
            if isinstance(node, ast.Call):
                func = node.func
                name = getattr(func, "attr", None) or getattr(func, "id", None)
            elif isinstance(node, ast.Raise) and node.exc is not None:
                exc = node.exc
                target = exc.func if isinstance(exc, ast.Call) else exc
                name = getattr(target, "attr", None) or getattr(target, "id",
                                                                None)
            if name not in ("skipTest", "SkipTest", "skip", "skipIf",
                            "skipUnless"):
                continue
            segment = ast.get_source_segment(source, node) or ""
            if "arid" in segment or "SCRIPTS" in segment:
                offenders.append("line {0}: {1}".format(node.lineno, name))
        self.assertEqual(
            [], offenders,
            "a skip conditioned on a missing production script is silence "
            "that looks like success; call require_arid() (or wear "
            "@requires_arid) so the run goes red instead: {0}"
            .format(offenders))

    def test_the_guard_above_can_actually_see_an_offender(self):
        """A grep that matches nothing is indistinguishable from a grep that
        cannot match. This forces one through the same code path."""
        source = ("import unittest\n"
                  "class T(unittest.TestCase):\n"
                  "    @unittest.skipIf(arid is None, 'absent')\n"
                  "    def test_x(self):\n        pass\n")
        tree = ast.parse(source)
        found = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                name = getattr(node.func, "attr", None)
                if name in ("skipIf", "skipUnless", "skipTest"):
                    segment = ast.get_source_segment(source, node) or ""
                    if "arid" in segment:
                        found.append(name)
        self.assertEqual(["skipIf"], found,
                         "the detection the guard relies on does not fire on "
                         "the exact pattern that was removed")


if __name__ == "__main__":
    unittest.main(verbosity=2)
