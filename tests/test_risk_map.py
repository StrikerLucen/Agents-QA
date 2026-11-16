#!/usr/bin/env python3
"""Tests for risk_map.py.

These build a real git repository with a known history, so the assertions are
about behaviour rather than about a mocked subprocess. Expected values are
derived from the fixture by hand.
"""

import contextlib
import io
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import risk_map as rm  # noqa: E402


# --------------------------------------------------------------------------
# fixture repo
# --------------------------------------------------------------------------

class Repo:
    """A throwaway git repo. Commit dates are set explicitly so time-weighting
    is deterministic rather than dependent on when the suite runs."""

    def __init__(self, path):
        self.path = path
        self._run(["init", "-q", "-b", "main"])
        self._run(["config", "user.email", "dev@example.com"])
        self._run(["config", "user.name", "Dev"])
        self.clock = 1_600_000_000  # 2020-09-13

    def _run(self, args, env=None):
        full = dict(os.environ)
        full.update(env or {})
        subprocess.run(["git", "-C", self.path, *args], check=True,
                       capture_output=True, text=True, env=full)

    def write(self, rel, lines):
        full = os.path.join(self.path, rel)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8") as fh:
            fh.write("\n".join(f"line {i}" for i in range(lines)) + "\n")

    def commit(self, message, *, author="dev@example.com", days=1):
        self.clock += days * 86400
        stamp = f"{self.clock} +0000"
        self._run(["add", "-A"])
        self._run(["commit", "-q", "-m", message, "--author", f"X <{author}>"],
                  env={"GIT_AUTHOR_DATE": stamp, "GIT_COMMITTER_DATE": stamp})

    def remove(self, rel):
        self._run(["rm", "-q", rel])


def build_fixture(path):
    """Known history:
      src/hot.py      many commits, 3 of them bug-fixes, several authors
      src/calm.py     one commit, never touched again
      src/gone.py     created then deleted -> must not appear
      tests/test_x.py test file -> excluded unless asked for
      node_modules/   vendored -> always excluded
    """
    r = Repo(path)

    r.write("src/hot.py", 100)
    r.write("src/calm.py", 100)
    r.write("tests/test_x.py", 50)
    r.write("node_modules/dep/index.js", 500)
    r.write("src/gone.py", 40)
    r.commit("initial commit")

    # calm.py is never touched again.
    for i in range(6):
        r.write("src/hot.py", 100 + (i + 1) * 20)
        r.write("tests/test_x.py", 50 + i)
        r.write("node_modules/dep/index.js", 500 + i)
        message = "fix: wrong total on invoice" if i % 2 == 0 else "feat: add field"
        r.commit(message, author=f"dev{i % 3}@example.com")

    r.remove("src/gone.py")
    r.commit("chore: remove dead module")
    return r


class RiskMapTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        build_fixture(cls._tmp.name)
        cls.result = rm.build(cls._tmp.name, since=None)
        cls.files = {f["path"]: f for f in cls.result["files"]}

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()


# --------------------------------------------------------------------------
# time weighting
# --------------------------------------------------------------------------

class TestTimeWeight(unittest.TestCase):
    """Expected values come from the published formula 1/(1+e^(-12t+12)),
    computed independently. Note it tops out at 0.5, not 1.0 -- an easy thing
    to assume wrongly, and the reason these are pinned to exact values."""

    def test_oldest_commit_is_almost_weightless(self):
        self.assertAlmostEqual(rm.time_weight(0, 0, 1000), 0.000006, places=6)

    def test_newest_commit_tops_out_at_one_half(self):
        self.assertAlmostEqual(rm.time_weight(1000, 0, 1000), 0.5, places=6)

    def test_curve_is_steep_not_linear(self):
        # Half way through the window carries almost no weight; the mass sits
        # in the final quarter. This is the property that makes the score
        # track "recently troubled" rather than "historically troubled".
        self.assertAlmostEqual(rm.time_weight(500, 0, 1000), 0.002473, places=6)
        self.assertAlmostEqual(rm.time_weight(750, 0, 1000), 0.047426, places=6)
        self.assertAlmostEqual(rm.time_weight(900, 0, 1000), 0.231475, places=6)

    def test_newest_outweighs_oldest_by_four_orders_of_magnitude(self):
        ratio = rm.time_weight(1000, 0, 1000) / rm.time_weight(0, 0, 1000)
        self.assertGreater(ratio, 10_000)

    def test_monotonically_increasing(self):
        vals = [rm.time_weight(t, 0, 1000) for t in range(0, 1001, 100)]
        self.assertEqual(vals, sorted(vals))

    def test_single_commit_window_does_not_divide_by_zero(self):
        self.assertEqual(rm.time_weight(5, 5, 5), 1.0)


# --------------------------------------------------------------------------
# bug-fix detection
# --------------------------------------------------------------------------

class TestBugfixDetection(unittest.TestCase):
    def test_recognises_common_phrasings(self):
        for subject in [
            "fix: null guard on total", "Fixed the crash", "fixes #123",
            "bugfix for tax rounding", "hotfix: restore login",
            "resolve incorrect balance", "closes #42",
            "correct a regression in sync", "patch broken import",
        ]:
            with self.subTest(subject=subject):
                self.assertTrue(rm.BUGFIX_PATTERN.search(subject), subject)

    def test_does_not_flag_ordinary_work(self):
        for subject in [
            "feat: add discount field", "docs: update readme",
            "refactor the parser", "chore: bump deps", "add tests for tax",
        ]:
            with self.subTest(subject=subject):
                self.assertFalse(rm.BUGFIX_PATTERN.search(subject), subject)

    def test_prefix_words_do_not_false_positive(self):
        # \b should stop "prefix" and "suffix" matching "fix".
        self.assertFalse(rm.BUGFIX_PATTERN.search("add prefix to log lines"))


# --------------------------------------------------------------------------
# path handling
# --------------------------------------------------------------------------

class TestPaths(unittest.TestCase):
    def test_simple_rename_resolves_to_new_path(self):
        self.assertEqual(rm._unrename("old.py => new.py"), "new.py")

    def test_braced_rename_resolves_to_new_path(self):
        self.assertEqual(rm._unrename("src/{old => new}/file.py"), "src/new/file.py")

    def test_plain_path_is_untouched(self):
        self.assertEqual(rm._unrename("src/app.py"), "src/app.py")

    def test_vendored_and_generated_paths_are_excluded(self):
        for path in ["node_modules/x/i.js", "dist/app.min.js", "a/package-lock.json",
                     "src/__snapshots__/a.snap", "api/user_pb2.py"]:
            with self.subTest(path=path):
                self.assertTrue(rm.is_excluded(path, rm.DEFAULT_EXCLUDES), path)

    def test_source_paths_are_not_excluded(self):
        self.assertFalse(rm.is_excluded("src/billing/tax.py", rm.DEFAULT_EXCLUDES))

    def test_test_files_are_recognised_across_conventions(self):
        for path in ["tests/test_a.py", "src/a.test.ts", "spec/b_spec.rb",
                     "src/__tests__/c.js", "app/TaxTest.java", "e2e/login.js"]:
            with self.subTest(path=path):
                self.assertTrue(rm.TEST_PATTERN.search(path), path)

    def test_production_file_is_not_mistaken_for_a_test(self):
        for path in ["src/latest.py", "src/contest.js", "src/protest/a.py"]:
            with self.subTest(path=path):
                self.assertFalse(rm.TEST_PATTERN.search(path), path)


# --------------------------------------------------------------------------
# scoring over the fixture
# --------------------------------------------------------------------------

class TestScoring(RiskMapTest):
    def test_churned_file_outranks_untouched_file(self):
        self.assertGreater(self.files["src/hot.py"]["likelihood"],
                           self.files["src/calm.py"]["likelihood"])

    def test_file_creation_is_not_counted_as_churn(self):
        # calm.py was created with 100 lines and never touched again. Counting
        # its creation would give relative_churn 1.0 -- identical to a file
        # genuinely rewritten end to end, which is the whole signal we rely on.
        calm = self.files["src/calm.py"]
        self.assertEqual(calm["churned_lines"], 0)
        self.assertEqual(calm["relative_churn"], 0)
        self.assertTrue(calm["new_in_window"])

    def test_untouched_file_is_capped_low_regardless_of_percentile(self):
        # In a two-file repo percentiles would otherwise push it mid-table.
        self.assertLessEqual(self.files["src/calm.py"]["likelihood"], 2)

    def test_reason_says_plainly_that_nothing_happened(self):
        self.assertIn("never modified", self.files["src/calm.py"]["why"])

    def test_modified_file_reports_real_churn(self):
        hot = self.files["src/hot.py"]
        self.assertGreater(hot["churned_lines"], 0)
        self.assertFalse(hot["new_in_window"])

    def test_relative_churn_is_normalised_by_size_not_absolute(self):
        # node_modules churns the most in absolute terms but is excluded;
        # the point of the metric is churn relative to the file's own size.
        hot = self.files["src/hot.py"]
        self.assertGreater(hot["relative_churn"], 0)
        self.assertAlmostEqual(
            hot["relative_churn"], hot["churned_lines"] / hot["lines"], places=3)

    def test_bugfix_commits_are_counted(self):
        self.assertEqual(self.files["src/hot.py"]["bugfix_commits"], 3)
        self.assertEqual(self.files["src/calm.py"]["bugfix_commits"], 0)

    def test_time_weighted_risk_is_positive_only_where_fixes_happened(self):
        self.assertGreater(self.files["src/hot.py"]["time_weighted_risk"], 0)
        self.assertEqual(self.files["src/calm.py"]["time_weighted_risk"], 0)

    def test_contributor_spread_is_counted(self):
        self.assertGreaterEqual(self.files["src/hot.py"]["authors"], 3)

    def test_deleted_file_is_absent(self):
        self.assertNotIn("src/gone.py", self.files)

    def test_vendored_directory_is_absent(self):
        self.assertFalse([p for p in self.files if p.startswith("node_modules/")])

    def test_test_files_excluded_by_default_but_includable(self):
        self.assertNotIn("tests/test_x.py", self.files)
        with_tests = rm.build(self._tmp.name, since=None, include_tests=True)
        self.assertIn("tests/test_x.py", {f["path"] for f in with_tests["files"]})

    def test_results_are_ordered_riskiest_first(self):
        raws = [f["likelihood_raw"] for f in self.result["files"]]
        self.assertEqual(raws, sorted(raws, reverse=True))

    def test_likelihood_is_an_ordinal_in_range(self):
        for f in self.result["files"]:
            self.assertIn(f["likelihood"], {1, 2, 3, 4, 5})

    def test_every_file_carries_a_human_readable_reason(self):
        # A score without visible reasoning gets read as a false positive.
        for f in self.result["files"]:
            self.assertTrue(f["why"].strip(), f["path"])

    def test_each_signal_appears_exactly_once(self):
        # A chain that re-tests churn in a later branch prints it twice:
        # "37% of its lines churned; 5 bug-fixes; 37% of its lines churned".
        f = {"relative_churn": 0.37, "churned_lines": 37, "bugfix_commits": 5,
             "time_weighted_risk": 0.9, "authors": 2, "new_in_window": False}
        why = rm._explain(f)
        self.assertEqual(why.count("churned"), 1, why)
        self.assertEqual(why.count("bug-fix"), 1, why)

    def test_no_signal_is_repeated_across_the_plausible_range(self):
        for churn in (0.0, 0.004, 0.05, 0.37, 0.99, 1.5):
            for authors in (1, 5, 9):
                for fixes in (0, 1, 5):
                    f = {"relative_churn": churn,
                         "churned_lines": int(churn * 100),
                         "bugfix_commits": fixes, "time_weighted_risk": 0.9,
                         "authors": authors, "new_in_window": churn == 0}
                    why = rm._explain(f)
                    with self.subTest(churn=churn, authors=authors, fixes=fixes):
                        self.assertLessEqual(why.count("churned"), 1, why)
                        self.assertLessEqual(why.count("changed"), 1, why)
                        self.assertLessEqual(why.count("contributors"), 1, why)
                        self.assertTrue(why.strip())

    def test_tiny_churn_reports_a_count_not_a_rounded_zero_percent(self):
        # "0% of its lines churned" beside "1 bug-fix (recently)" reads as a
        # contradiction and invites the reader to discount the whole row.
        f = {"relative_churn": 0.004, "churned_lines": 1, "bugfix_commits": 1,
             "time_weighted_risk": 0.9, "authors": 1, "new_in_window": False}
        why = rm._explain(f)
        self.assertNotIn("0%", why)
        self.assertIn("1 line changed", why)

    def test_churn_above_one_percent_is_still_a_percentage(self):
        f = {"relative_churn": 0.54, "churned_lines": 54, "bugfix_commits": 0,
             "time_weighted_risk": 0, "authors": 1, "new_in_window": False}
        self.assertIn("54% of its lines churned", rm._explain(f))

    def test_reason_mentions_bugfixes_when_there_were_some(self):
        self.assertIn("bug-fix", self.files["src/hot.py"]["why"])

    def test_impact_is_never_computed_and_risk_is_never_collapsed(self):
        # Impact is a business judgment; emitting one here would be inventing
        # the half of the model git cannot see.
        for f in self.result["files"]:
            self.assertNotIn("impact", f)
            self.assertNotIn("risk", f)
        self.assertIn("Impact", self.result["note"])

    def test_top_limits_output(self):
        limited = rm.build(self._tmp.name, since=None, top=1)
        self.assertEqual(len(limited["files"]), 1)
        self.assertEqual(limited["files"][0]["path"], "src/hot.py")

    def test_extra_excludes_are_honoured(self):
        trimmed = rm.build(self._tmp.name, since=None,
                           excludes=rm.DEFAULT_EXCLUDES + ("hot.py",))
        self.assertNotIn("src/hot.py", {f["path"] for f in trimmed["files"]})


# --------------------------------------------------------------------------
# what is NOT in the map
# --------------------------------------------------------------------------

class TestUnrankedFilesAreNamed(unittest.TestCase):
    """A tracked file that cannot be counted must not vanish from the map.

    F10: `chmod 000 bad.py` produced one ranked file, an empty
    `skipped_files` and an empty `warnings` -- a map missing a third of the
    repository, rendered exactly like a complete one. Only the over-ceiling
    case was ever recorded; unreadable, binary, gone-from-disk and broken
    symlinks each fell through a bare `continue`.

    `skills/diagnose/SKILL.md` tells a reader "an empty risk map means no
    data, not no risk". A silently INCOMPLETE map is worse: it is the list a
    team ranks its testing effort against.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._cleanup)
        self.repo = Repo(self._tmp.name)

    def _cleanup(self):
        # chmod 000 would otherwise defeat the cleanup on some platforms.
        # Order is irrelevant here: every file gets the same chmod.
        for root, _dirs, names in os.walk(self._tmp.name):  # testsquad:ignore fs-order -- cleanup
            for name in sorted(names):
                try:
                    os.chmod(os.path.join(root, name), stat.S_IRUSR | stat.S_IWUSR)
                except OSError:
                    pass
        self._tmp.cleanup()

    def _two_file_history(self):
        self.repo.write("good.py", 10)
        self.repo.write("bad.py", 10)
        self.repo.commit("initial commit")
        self.repo.write("good.py", 20)
        self.repo.write("bad.py", 20)
        self.repo.commit("fix: touch both")

    def _skipped(self, result):
        return {s["file"]: s["reason"] for s in result["skipped_files"]}

    def test_an_unreadable_tracked_file_is_named_not_dropped(self):
        self._two_file_history()
        target = os.path.join(self._tmp.name, "bad.py")
        os.chmod(target, 0)
        if os.access(target, os.R_OK):  # running as root, or a lenient FS
            self.skipTest("chmod 000 did not make the file unreadable here")
        result = rm.build(self._tmp.name, since=None)

        self.assertNotIn("bad.py", [f["path"] for f in result["files"]],
                         "an unreadable file cannot be scored")
        self.assertIn("bad.py", self._skipped(result),
                      "...and a file that cannot be scored must still be "
                      "named: absent from the ranking reads as low risk")
        self.assertIn("unreadable", self._skipped(result)["bad.py"])
        self.assertTrue(result["warnings"],
                        "the JSON consumer only reads warnings and files")
        self.assertEqual(result["summary"]["files_skipped"], 1)
        self.assertEqual(result["summary"]["files_ranked"],
                         len(result["files"]))

    def test_a_complete_map_and_an_incomplete_one_do_not_look_alike(self):
        # The whole point. Both runs rank exactly one file; only one of them
        # read everything it was asked to.
        self._two_file_history()
        self.repo.remove("bad.py")
        self.repo.commit("chore: drop it")
        complete = rm.build(self._tmp.name, since=None)

        other = tempfile.TemporaryDirectory()
        self.addCleanup(other.cleanup)
        second = Repo(other.name)
        second.write("good.py", 10)
        second.write("bad.py", 10)
        second.commit("initial commit")
        second.write("good.py", 20)
        second.write("bad.py", 20)
        second.commit("fix: touch both")
        target = os.path.join(other.name, "bad.py")
        os.chmod(target, 0)
        if os.access(target, os.R_OK):
            self.skipTest("chmod 000 did not make the file unreadable here")
        incomplete = rm.build(other.name, since=None)

        self.assertEqual([f["path"] for f in complete["files"]], ["good.py"])
        self.assertEqual([f["path"] for f in incomplete["files"]], ["good.py"])
        # Same ranking, and these are the four places they must differ.
        self.assertEqual(complete["skipped_files"], [])
        self.assertEqual(complete["warnings"], [])
        self.assertEqual(complete["summary"]["files_skipped"], 0)
        self.assertTrue(incomplete["skipped_files"])
        self.assertTrue(incomplete["warnings"])
        self.assertEqual(incomplete["summary"]["files_skipped"], 1)
        # ...and in the text a human actually reads.
        self.assertEqual(self._render(complete), "")
        self.assertIn("NOT ranked", self._render(incomplete))
        self.assertIn("bad.py", self._render(incomplete))

    def _render(self, result):
        buf = io.StringIO()
        rm.render_skipped(result, buf)
        return buf.getvalue()

    def test_a_binary_file_says_it_was_binary(self):
        self.repo.write("good.py", 10)
        with open(os.path.join(self._tmp.name, "blob.bin"), "wb") as fh:
            fh.write(b"\x7fELF\x00\x00payload\n")
        self.repo.commit("initial commit")
        self.repo.write("good.py", 20)
        with open(os.path.join(self._tmp.name, "blob.bin"), "wb") as fh:
            fh.write(b"\x7fELF\x00\x00payload2\n")
        self.repo.commit("fix: touch both")
        result = rm.build(self._tmp.name, since=None)
        self.assertIn("binary", self._skipped(result).get("blob.bin", ""))

    def test_a_tracked_file_deleted_from_the_working_tree_says_so(self):
        # `git rm` would drop it from ls-files; this is the other case -- git
        # still tracks it, the file is simply not there.
        self._two_file_history()
        os.remove(os.path.join(self._tmp.name, "bad.py"))
        result = rm.build(self._tmp.name, since=None)
        self.assertIn("missing from the working tree",
                      self._skipped(result).get("bad.py", ""))

    def test_a_broken_symlink_says_its_target_is_missing(self):
        self.repo.write("good.py", 10)
        link = os.path.join(self._tmp.name, "dangling.link")
        os.symlink("/nonexistent/target", link)
        self.repo.commit("initial commit")
        self.repo.write("good.py", 20)
        self.repo.commit("fix: touch it")
        result = rm.build(self._tmp.name, since=None)
        reason = self._skipped(result).get("dangling.link", "")
        self.assertIn("symbolic link", reason)
        self.assertIn("does not exist", reason)

    def test_an_over_ceiling_file_still_names_the_ceiling(self):
        # The one case that was already reported. It must keep its hint.
        self._two_file_history()
        result = rm.build(self._tmp.name, since=None, max_file_bytes=20)
        reasons = self._skipped(result)
        self.assertTrue(reasons, "a 20-byte ceiling skips everything")
        self.assertIn("--max-file-bytes", next(iter(reasons.values())))
        self.assertTrue(any("ceiling" in w for w in result["warnings"]),
                        result["warnings"])
        # Nothing was scoreable, and the reason is not "this repo is quiet":
        # the files that would have been scored were never read.
        self.assertTrue(any("skipped_files" in w for w in result["warnings"]),
                        result["warnings"])

    def test_a_small_ceiling_is_not_reported_as_zero_megabytes(self):
        # `max_bytes // (1024*1024)` printed "0MB ceiling" -- a number nobody
        # measured, in the sentence that is supposed to explain the skip.
        self.assertNotIn("0MB", rm.oversize_reason(1000))
        self.assertIn("1000 bytes", rm.oversize_reason(1000))
        self.assertEqual(rm.human_bytes(2 * 1024 * 1024), "2MB")

    def test_a_non_ascii_path_is_ranked_rather_than_quietly_lost(self):
        # git quotes it as "\303\274nicode.py" by default, which names no
        # file on disk: the file was unreadable, unranked and unmentioned.
        name = "\u00fcnicode.py"
        self.repo.write(name, 10)
        self.repo.write("good.py", 10)
        self.repo.commit("initial commit")
        self.repo.write(name, 20)
        self.repo.write("good.py", 20)
        self.repo.commit("fix: touch both")
        result = rm.build(self._tmp.name, since=None)
        self.assertIn(name, [f["path"] for f in result["files"]])
        self.assertEqual(result["skipped_files"], [])

    def test_nothing_skipped_stays_completely_silent(self):
        # A check that speaks when there is nothing to say gets switched off,
        # and takes the real signal with it.
        self._two_file_history()
        result = rm.build(self._tmp.name, since=None)
        self.assertEqual(result["skipped_files"], [])
        self.assertEqual(result["warnings"], [])
        self.assertEqual(result["summary"]["files_skipped"], 0)
        self.assertEqual(self._render(result), "")

    def test_the_cli_prints_the_unranked_files_in_both_modes(self):
        self._two_file_history()
        target = os.path.join(self._tmp.name, "bad.py")
        os.chmod(target, 0)
        if os.access(target, os.R_OK):
            self.skipTest("chmod 000 did not make the file unreadable here")
        err = io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(err):
            self.assertEqual(rm.main([self._tmp.name, "--since", ""]), 0)
        self.assertIn("bad.py", err.getvalue())
        self.assertIn("NOT ranked", err.getvalue())

        with tempfile.TemporaryDirectory() as out_dir:
            out = os.path.join(out_dir, "risk.json")
            err = io.StringIO()
            with contextlib.redirect_stdout(io.StringIO()), \
                    contextlib.redirect_stderr(err):
                self.assertEqual(
                    rm.main([self._tmp.name, "--since", "", "--out", out]), 0)
            self.assertIn("bad.py", err.getvalue())
            self.assertIn("NOT ranked", err.getvalue())
            with open(out) as fh:
                self.assertTrue(json.load(fh)["skipped_files"])


class TestWorkingSymlinksAreMeasuredAsLinks(unittest.TestCase):
    """A tracked symlink whose target exists was followed by open(), so its
    `lines` counted the TARGET -- possibly a file outside the repository.

    This is not a silent drop: the entry was ranked and visible. It is the
    other failure the project's rules name, a number that describes something
    other than the thing it is attached to. git stores a symlink as a blob
    holding the target path, so `git log --numstat` already measured the
    churn numerator on the link; only the denominator came from elsewhere,
    and `relative_churn` divided one file's churn by another file's size.

    The broken and directory cases were already named in `skipped_files` and
    hand a reader no number at all, so they keep the skip entry they have.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repo = Repo(self._tmp.name)
        self._outside = tempfile.TemporaryDirectory()
        self.addCleanup(self._outside.cleanup)

    def _link(self, name, target):
        os.symlink(target, os.path.join(self._tmp.name, name))

    def _ranked(self, result):
        return {f["path"]: f for f in result["files"]}

    def _history_with(self, link_name, target):
        """One commit creating the link, one modifying a sibling, so both the
        link and the sibling appear in the ranking."""
        self.repo.write("good.py", 10)
        self._link(link_name, target)
        self.repo.commit("initial commit")
        self.repo.write("good.py", 20)
        self.repo.commit("fix: touch the sibling")
        return rm.build(self._tmp.name, since=None)

    def test_a_link_inside_the_repo_is_not_given_its_targets_line_count(self):
        self.repo.write("big.py", 400)
        result = self._history_with("alias.py", "big.py")
        entry = self._ranked(result)["alias.py"]
        self.assertEqual(entry["lines"], 1,
                         "a symlink's own content is the target path git "
                         "stores, which is one line")
        self.assertNotEqual(entry["lines"], 400,
                            "400 is big.py's size, not alias.py's")

    def test_a_link_outside_the_repo_never_reports_the_outside_files_size(self):
        # The case that matters most: git has never seen this file, it is not
        # in the ranking under any path, and nothing else in the map would
        # let a reader notice the number came from off the map entirely.
        outside = os.path.join(self._outside.name, "elsewhere.txt")
        with open(outside, "w", encoding="utf-8") as fh:
            fh.write("x\n" * 137)
        result = self._history_with("outward.link", outside)
        entry = self._ranked(result)["outward.link"]
        self.assertEqual(entry["lines"], 1)
        self.assertNotEqual(entry["lines"], 137,
                            "137 lines live in a file outside this repository")

    def test_the_entry_says_it_is_a_link_and_names_the_target(self):
        self.repo.write("big.py", 400)
        result = self._history_with("alias.py", "big.py")
        entry = self._ranked(result)["alias.py"]
        self.assertEqual(entry["symlink"], "big.py")
        self.assertIn("symbolic link", entry["why"],
                      "a one-line link and a one-line file are "
                      "indistinguishable in a ranking unless the row says so")
        self.assertIn("big.py", entry["why"])

    def test_an_ordinary_file_carries_no_link_target(self):
        # The marker must stay absent where there is nothing to mark, or it
        # becomes noise on every row of every map.
        result = self._history_with("alias.py", "good.py")
        self.assertIsNone(self._ranked(result)["good.py"]["symlink"])

    def test_churn_and_size_are_measured_on_the_same_blob(self):
        # Repointing the link is 1 insertion + 1 deletion on a 1-line blob:
        # numerator and denominator both describe the link. Before the fix
        # the denominator was the target's size, so the ratio was in no unit
        # at all.
        self.repo.write("one.py", 10)
        self.repo.write("two.py", 900)
        self._link("alias.py", "one.py")
        self.repo.commit("initial commit")
        os.remove(os.path.join(self._tmp.name, "alias.py"))
        self._link("alias.py", "two.py")
        self.repo.commit("fix: repoint the alias")
        entry = self._ranked(rm.build(self._tmp.name, since=None))["alias.py"]
        self.assertEqual(entry["lines"], 1)
        self.assertEqual(entry["symlink"], "two.py")
        self.assertEqual(entry["relative_churn"],
                         round(entry["churned_lines"] / 1, 4))

    def test_a_link_to_a_directory_keeps_its_named_skip(self):
        # Adjacent case, deliberately unchanged: it hands a reader no number,
        # so it is not the defect above, and it is already reported.
        os.mkdir(os.path.join(self._tmp.name, "pkg"))
        self.repo.write("pkg/mod.py", 10)
        result = self._history_with("dirlink", "pkg")
        skipped = {s["file"]: s["reason"] for s in result["skipped_files"]}
        self.assertIn("dirlink", skipped)
        self.assertIn("symbolic link to a directory", skipped["dirlink"])
        self.assertNotIn("dirlink", self._ranked(result))

    def test_count_lines_does_not_follow_a_link(self):
        self.repo.write("big.py", 400)
        self._link("alias.py", "big.py")
        self.assertEqual(
            rm.count_lines(os.path.join(self._tmp.name, "big.py")), 400)
        self.assertEqual(
            rm.count_lines(os.path.join(self._tmp.name, "alias.py")), 1)

    def test_the_ceiling_does_not_skip_a_link_to_an_oversized_file(self):
        # getsize() followed the link too, so a link to a 3MB file was
        # reported as "larger than the 2MB ceiling" -- a true sentence about
        # the wrong file, and a tracked path dropped from the ranking for it.
        big = os.path.join(self._outside.name, "huge.txt")
        with open(big, "w", encoding="utf-8") as fh:
            fh.write("y\n" * 50)
        self._history_with("huge.link", big)  # commits it; rebuilt below
        result = rm.build(self._tmp.name, since=None, max_file_bytes=20)
        entry = self._ranked(result).get("huge.link")
        self.assertIsNotNone(entry, "the link is 8 bytes; the ceiling is the "
                                    "target's business, not the link's")
        self.assertEqual(entry["lines"], 1)


class TestPercentile(unittest.TestCase):
    def test_bounds(self):
        vals = [1.0, 2.0, 3.0, 4.0]
        self.assertEqual(rm._percentile_of(vals, 0.0), 0.0)
        self.assertEqual(rm._percentile_of(vals, 4.0), 1.0)

    def test_ties_take_the_upper_rank(self):
        # A repo where most files score zero must not push every zero high.
        vals = [0.0, 0.0, 0.0, 1.0]
        self.assertEqual(rm._percentile_of(vals, 0.0), 0.75)

    def test_empty_input(self):
        self.assertEqual(rm._percentile_of([], 1.0), 0.0)


# --------------------------------------------------------------------------
# cli
# --------------------------------------------------------------------------

class TestCli(RiskMapTest):
    def test_writes_valid_json(self):
        with tempfile.TemporaryDirectory() as out_dir:
            out = os.path.join(out_dir, "risk.json")
            # --since "" disables the window; the fixture's commits are dated
            # 2020 and would fall outside the default two-year window.
            self.assertEqual(rm.main([self._tmp.name, "--since", "", "--out", out]), 0)
            with open(out) as fh:
                data = json.load(fh)
            self.assertEqual(data["schema"], rm.SCHEMA)
            self.assertTrue(data["files"])
            self.assertEqual(data["warnings"], [])

    def test_empty_window_warns_instead_of_reporting_no_risk(self):
        # An empty result reads exactly like "this repo is safe". It must say
        # which it is, or the roadmap silently recommends nothing.
        result = rm.build(self._tmp.name, since="2037-01-01")
        self.assertEqual(result["files"], [])
        self.assertTrue(result["warnings"])
        self.assertIn("absence of risk", result["warnings"][0])

    def test_healthy_run_carries_no_warnings(self):
        self.assertEqual(self.result["warnings"], [])

    def test_non_repository_exits_nonzero_rather_than_emitting_an_empty_map(self):
        with tempfile.TemporaryDirectory() as plain:
            self.assertEqual(rm.main([plain]), 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
