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
        """BUG: a BOM on coverage.json makes auto-detection give up.

        detect_format() tests `head.startswith("{")`, and the file is opened as
        plain utf-8, so the BOM survives as a leading \\ufeff and the report is
        rejected as unrecognisable. coverage.py itself reads the file fine, so
        the plugin refuses a report its own ecosystem considers valid.
        SHOULD: strip the BOM (open with utf-8-sig) and parse normally.
        """
        self.write("coverage.json",
                   '﻿{"files": {"a.py": {"summary": '
                   '{"num_statements": 4, "covered_lines": 2}}}}')
        self.assertEqual(ncov.main([self.path("coverage.json"),
                                    "--out", self.path("o.json")]), 0)

    def test_coverage_json_with_bom_currently_exits_two(self):
        # Current behaviour, pinned so the fix above is visible when it lands.
        # Exiting 2 is at least honest; it does not invent a number.
        self.write("coverage.json", '﻿{"files": {}}')
        self.assertEqual(ncov.main([self.path("coverage.json")]), 2)

    def test_utf16_lcov_is_refused_rather_than_reported_as_empty(self):
        """BUG: a UTF-16 lcov report is silently reported as zero coverage.

        The file is read as utf-8 with errors="replace", so every line turns
        into mojibake, no DA: record matches, and normalize exits 0 with
        totals of zero files and line_pct=None. Downstream that is
        indistinguishable from "this project has no coverage data" and a
        coverage gate reads it as a clean, empty report.
        SHOULD: detect the UTF-16 BOM and either decode it or exit 2.
        """
        self.write("lcov.info", LCOV_OK.encode("utf-16"), binary=True)
        rc = ncov.main([self.path("lcov.info"), "--out", self.path("o.json")])
        if rc == 0:
            with open(self.path("o.json"), encoding="utf-8") as fh:
                data = json.load(fh)
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
        # WRONG ANSWER, documented: a UTF-16 .env naming a production host is
        # treated as binary, so the verdict is SAFE while the suite is aimed at
        # production. The file is listed in skipped_files, but nothing in the
        # CLI exit code reflects it -- `--fail-on` only looks at findings.
        # SHOULD: decode UTF-16, or treat an unreadable .env as at least a
        # HIGH finding so the gate does not pass on a file it never read.
        self.write(".env", "URL=https://api.prod.acme.com\n".encode("utf-16"),
                   binary=True)
        result = esc.scan(self.tmp)
        # Was SAFE: a UTF-16 .env read as binary, skipped, and the verdict
        # computed from findings alone. "I could not read it" rendering as
        # "it is safe" is the failure this gate exists to prevent.
        self.assertEqual(result["verdict"], "UNSAFE")
        self.assertTrue(result["skipped_files"])

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
        """CRASH: validate_plugin.py dies on any component file that is not UTF-8.

        check_markdown_component() and check_context_budget() both do a bare
        `open(path, encoding="utf-8").read()`. A UTF-16 or latin-1 agent/skill
        markdown file raises UnicodeDecodeError straight out of validate(),
        so the whole validation run aborts with a traceback and reports
        nothing about the other components.
        SHOULD: decode with errors="replace" and report the file as unreadable.
        """
        self.write("agents/a.md",
                   "---\nname: a\ndescription: x\n---\nbody\n".encode("utf-16"),
                   binary=True)
        vplug.validate(self.tmp)

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
        # looks like. Every regex on it runs against 10MB of text at once.
        self.write("test_huge.py", "x = '" + "a" * 10_000_000 + "'\n")
        with timed(self, "flake_smells on a 10MB single line", budget=10.0):
            result = fsm.scan_paths([self.tmp])
        self.assertEqual(result["summary"]["files_scanned"], 1)

    def test_multi_megabyte_test_file_is_read_whole_into_memory(self):
        """RESOURCE, documented: flake_smells.py has no size ceiling at all.

        read_lines() does a bare fh.read() and then splitlines(keepends=True),
        so a file costs several times its own size in RAM. Measured on this
        machine: a 49MB generated test file took 45s and peaked near 3GB RSS.
        env_safety_check.py has MAX_BYTES = 2MB for exactly this reason;
        flake_smells.py, arid_nodes.py and normalize_coverage.py have nothing.
        SHOULD: skip (and report as skipped) anything over a few MB.

        This test uses a ~2MB file so the suite stays fast, and asserts only the
        bound that makes the extrapolation honest.
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

    def test_scripts_do_not_write_atomically(self):
        """GAP, documented: no script uses temp-file-then-rename for --out.

        Every `--out` path in the tree is a plain `open(path, "w")`, which
        truncates first and writes after. Two scripts told to write the same
        .testing/ file -- which is exactly what two parallel agents following
        the skills will do -- can interleave, and any reader that opens the
        file mid-write sees a truncated JSON document.
        The skills promise atomicity (skills/start/SKILL.md: "Writes are atomic
        (temp file, then rename)"); the scripts the skills invoke do not
        implement it.
        SHOULD: write to `path + ".tmp"` and os.replace() onto the target.
        """
        offenders = []
        for name in sorted(os.listdir(SCRIPTS)):
            if not name.endswith(".py"):
                continue
            with open(os.path.join(SCRIPTS, name), encoding="utf-8") as fh:
                src = fh.read()
            if re.search(r'open\(\s*args\.out', src) and "os.replace" not in src:
                offenders.append(name)
        self.assertTrue(offenders, "expected at least one --out writer")
        # Pinned as the current state. When atomic writes land, this list
        # shrinks and the assertion below is what tells you.
        self.assertNotIn("os.replace", offenders)

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
        """RESOURCE, documented: three scripts read whole files unbounded.

        flake_smells.py and arid_nodes.py both do `fh.read()` in read_lines()
        with no getsize() check; normalize_coverage.py reads the entire report
        with `fh.read()`; risk_map.current_files() reads every tracked file
        whole just to count its newlines. Measured: flake_smells on a 49MB
        generated test file took 45s and peaked near 3GB RSS.
        SHOULD: each of these needs env_safety_check's MAX_BYTES treatment --
        a ceiling, and a 'skipped because too large' entry in the report.
        """
        unbounded = []
        for name in ("flake_smells.py", "arid_nodes.py", "normalize_coverage.py",
                     "risk_map.py"):
            if arid is None and name == "arid_nodes.py":
                continue
            src = self.source(name)
            if "getsize" not in src and "MAX_BYTES" not in src:
                unbounded.append(name)
        expected = ["flake_smells.py", "normalize_coverage.py", "risk_map.py"]
        if arid is not None:
            expected.insert(1, "arid_nodes.py")
        self.assertEqual(unbounded, expected)

    def test_risk_map_reads_each_tracked_file_whole_to_count_lines(self):
        # current_files() needs a newline count and nothing else, so it could
        # stream in chunks. It does fh.read() instead, so a repo with one 500MB
        # tracked fixture costs 500MB of RAM in a script that only wanted an int.
        # SHOULD: count newlines over a chunked read.
        src = self.source("risk_map.py")
        self.assertIn("chunk = fh.read()", src)
        self.assertNotIn("while True", src)

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
            esc.scan(self.tmp)

    def _email_sub_cost(self, n):
        # real_hosts_in() is the hot path; drive it through the public scanner
        # so the measurement is of a real scan, not of one regex.
        line = "A=" + "a" * n + "\n"
        start = time.monotonic()
        esc.scan_text(line, rel=".env", kind="env")
        return time.monotonic() - start

    @unittest.expectedFailure
    def test_env_safety_check_cost_is_linear_in_value_length(self):
        """DoS: EMAIL_RE.sub() is quadratic in the length of a config value.

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
            esc.scan(self.tmp)

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
            esc.scan(self.tmp)

    def test_env_safety_check_on_many_pathological_lines(self):
        # 1,000 lines of bait, each under the per-line shapes above. The point
        # is the product: per-line cost times line count is the real bound.
        body = "".join("K%d=%s\n" % (i, "a." * 100 + "-" * 100) for i in range(1000))
        self.write(".env", body)
        with timed(self, "env_safety_check on 1,000 bait lines"):
            esc.scan(self.tmp)

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
            fsm.scan_paths([self.tmp])

    def test_flake_smells_on_assertion_and_weak_oracle_bait(self):
        lines = [
            "    assert" + "A" * 50_000 + "\n",
            "    assert " + "a.b" * 20_000 + "\n",
            "    expect(" + "x" * 50_000 + ").toBeTruthy()\n",
            "    assert " + "a" * 50_000 + " is not None\n",
        ]
        self.write("test_bait2.py", "def test_x():\n" + "".join(lines))
        with timed(self, "flake_smells on assertion bait"):
            fsm.scan_paths([self.tmp])

    def test_flake_smells_on_java_method_signature_bait(self):
        # JVM_METHOD_RE is `\w+\([^;]*\)\s*(?:throws\s[\w.,\s]+)?\{\s*$` -- two
        # adjacent greedy character classes that can both eat whitespace.
        sig = "void foo(" + "," * 3000 + ") throws " + "a, " * 3000 + "{\n"
        self.write("FooTest.java", "class FooTest {\n    @Test\n    " + sig + "    }\n}\n")
        with timed(self, "flake_smells on JVM signature bait"):
            fsm.scan_paths([self.tmp])

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
            fsm.scan_paths([self.tmp])

    @unittest.skipIf(arid is None, "arid_nodes.py is not present in this checkout")
    def test_arid_nodes_on_regex_bait(self):
        lines = [
            "logger.info(" + "'a', " * 5000 + ")\n",
            "x = " + "(" * 200 + "1" + ")" * 200 + "\n",
            "y = " + "a + " * 20_000 + "1\n",
        ]
        self.write("bait.py", "".join(lines))
        with timed(self, "arid_nodes on regex bait"):
            arid.plan([self.tmp])


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
