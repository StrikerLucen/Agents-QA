#!/usr/bin/env python3
"""Tests for risk_map.py.

These build a real git repository with a known history, so the assertions are
about behaviour rather than about a mocked subprocess. Expected values are
derived from the fixture by hand.
"""

import json
import os
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
