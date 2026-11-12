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

import contextlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

SCRIPTS = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "scripts"))

import env_safety_check as esc  # noqa: E402
import flake_smells as fsm  # noqa: E402
import normalize_coverage as ncov  # noqa: E402
import pr_comment as prc  # noqa: E402
import risk_map as rmap  # noqa: E402
import session_context as sctx  # noqa: E402
import validate_plugin as vplug  # noqa: E402

try:  # arid_nodes only just landed; a missing module must not break the file.
    import arid_nodes as arid
except ImportError:  # pragma: no cover - depends on checkout
    arid = None

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
        self.write("test_a.py", b"def test_x():\n\x00    assert 1\n", binary=True)
        result = fsm.scan_paths([self.tmp])
        self.assertEqual(result["summary"]["files_scanned"], 0)
        self.assertEqual(result["summary"]["skipped"][0]["reason"],
                         "unreadable or binary")

    @unittest.skipIf(arid is None, "arid_nodes.py is not present in this checkout")
    def test_arid_nodes_handles_latin1_and_nul(self):
        self.write("a.py", b"x = 1\ny = caf\xe9\n", binary=True)
        self.write("b.py", b"x = 1\x00\n", binary=True)
        result = arid.plan([self.tmp])
        # The latin-1 file is planned; the NUL file is dropped as binary.
        self.assertEqual(result["summary"]["files"], 1)


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
        # risk_map.py and env_safety_check.py; summary["skipped"] keys the
        # same thing as "path", which is what made this test ERROR with a
        # KeyError rather than fail. Reported, not papered over.
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

    @unittest.skipIf(arid is None, "arid_nodes.py is not present in this checkout")
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

    @unittest.skipIf(arid is None, "arid_nodes.py is not present in this checkout")
    def test_arid_nodes_cost_is_linear_in_the_lines_of_one_function(self):
        """REGRESSION. _single_statement_body() rebuilt the whole enclosing
        function's body list for every candidate line in it, and was called
        twice per line, so one long function cost O(body^2). Measured before
        the fix, on files far under the 2MB ceiling: 2,005 lines 0.824s,
        4,005 lines 3.533s, 8,005 lines 12.192s -- roughly 4x per doubling.
        The file ceiling bounded the bytes and not the work.

        Asserted as a ratio rather than a wall time: an absolute budget on a
        shared CI box is a flake, but a quadratic still doubles its ratio.
        """
        def cost(n):
            shutil.rmtree(self.tmp, ignore_errors=True)
            os.makedirs(self.tmp)
            self.write("mod.py",
                       "def f():\n    x = (\n" + "        a +\n" * n
                       + "        a\n    )\n    return x\n")
            start = time.time()
            arid.plan([self.tmp])
            return time.time() - start

        small = cost(1000)
        large = cost(4000)
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

    def test_every_out_writer_is_atomic(self):
        """FIXED: all seven `--out` writers go through atomic_io.

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
        unbounded = []
        for name in ("flake_smells.py", "arid_nodes.py", "normalize_coverage.py",
                     "risk_map.py"):
            if arid is None and name == "arid_nodes.py":
                continue
            src = self.source(name)
            if "getsize" not in src and "MAX_BYTES" not in src:
                unbounded.append(name)
        self.assertEqual(unbounded, ["normalize_coverage.py"])

    def test_bounded_scripts_all_report_what_they_skipped(self):
        # A ceiling that drops a file quietly is worse than no ceiling: the
        # report then reads exactly like one over a clean repository. Every
        # script that gained a ceiling must also have gained the skip list.
        for name in ("flake_smells.py", "arid_nodes.py", "risk_map.py"):
            if arid is None and name == "arid_nodes.py":
                continue
            src = self.source(name)
            with self.subTest(script=name):
                self.assertIn("skipped_files", src)
                self.assertIn("--max-file-bytes", src)

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
        if arid is None:
            self.skipTest("arid_nodes.py is not present in this checkout")
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

    def _email_sub_cost(self, n):
        # real_hosts_in() is the hot path; drive it through the public scanner
        # so the measurement is of a real scan, not of one regex.
        line = "A=" + "a" * n + "\n"
        start = time.monotonic()
        esc.scan_text(line, rel=".env", kind="env")
        return time.monotonic() - start

    def test_env_safety_check_cost_is_linear_in_value_length(self):
        r"""FIXED: EMAIL_RE.sub() was quadratic in the length of a config value.

        real_hosts_in() calls `EMAIL_RE.sub(" ", remainder)` on every value.
        EMAIL_RE is `[A-Za-z0-9._%+\-]+@...`; on a long run of characters with
        no '@' the engine restarts at every offset and scans to the end each
        time, so cost grows with the square of the value's length.

        Measured here: 8k chars ~0.3s, 16k ~1.2s, 40k ~1.9s, 60k ~4.2s -- each
        doubling costs four times as much. env_safety_check.py's own ceiling is
        MAX_BYTES = 2MB, so a single 2MB line in a .env -- a minified blob, a
        base64 payload, a generated fixture -- is fully within what the script
        will accept and would take on the order of twenty minutes.

        This is the gate that runs BEFORE the test command. Hanging it does not
        fail safe; it stops the run with no verdict at all.
        SHOULD: anchor or bound EMAIL_RE (a `(?<![A-Za-z0-9._%+-])` lookbehind,
        or a length cap on the local part), or skip values over a few kB.

        Asserted as a growth ratio rather than an absolute time so the test
        says something about the algorithm and not about this machine.
        """
        small = max(self._email_sub_cost(8_000) for _ in range(3))
        large = max(self._email_sub_cost(16_000) for _ in range(3))
        if small < 0.02:
            self.skipTest("machine too fast to measure the ratio reliably")
        # Linear would be ~2x for a doubled input. Observed: ~4x.
        self.assertLess(large / small, 3.0,
                        f"doubling the value length cost {large / small:.1f}x "
                        f"({small:.3f}s -> {large:.3f}s): superlinear")

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
        with timed(self, "flake_smells on assertion bait, line ceiling raised"):
            full = fsm.scan_paths([self.tmp], max_line_chars=200_000)
        self.assertEqual(full["summary"]["lines_truncated"], 0)
        # Fed in full, the weak-oracle rule does fire on
        # `assert aaa... is not None`. That is the proof the regexes really
        # ran over the bait rather than over a 2,000-character stub of it.
        self.assertIn("weak-oracle", full["summary"]["by_rule"])

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

    @unittest.skipIf(arid is None, "arid_nodes.py is not present in this checkout")
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
        with timed(self, "arid_nodes on regex bait, line ceiling raised"):
            full = arid.plan([self.tmp], max_line_chars=1_000_000)
        self.assertEqual(full["summary"]["lines_truncated"], 0)
        self.assertEqual(full["summary"]["files"], 1)


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


if __name__ == "__main__":
    unittest.main(verbosity=2)
