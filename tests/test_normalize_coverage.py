#!/usr/bin/env python3
"""Tests for normalize_coverage.py.

Every expected number below was computed by hand from the fixture text, not
captured from a previous run. A test that records whatever the code happened
to produce proves only that the code is deterministic.
"""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import normalize_coverage as nc  # noqa: E402


# --------------------------------------------------------------------------
# fixtures
# --------------------------------------------------------------------------

# src/tax.js: 4 lines (3 hit), 5 branches (2 taken)
#   BRDA taken values cover all three forms lcov emits:
#     "5" / "1" -> taken        "-" -> never reached        "0" -> reached, never taken
#   The explicit "0" is the only case that exercises the `> 0` comparison;
#   without it, counting zero as a hit passes every assertion.
# src/util.js: 2 lines (2 hit), no branches
LCOV = """TN:
SF:src/tax.js
DA:1,5
DA:2,5
DA:3,0
DA:4,2
BRDA:2,0,0,5
BRDA:2,0,1,-
BRDA:3,0,0,-
BRDA:3,0,1,0
BRDA:4,0,0,1
BRF:5
BRH:2
LF:4
LH:3
end_of_record
SF:src/util.js
DA:1,1
DA:2,3
LF:2
LH:2
end_of_record
"""

# app/billing.py: 3 lines (2 hit); line 7 has 1 of 2 conditions covered
COBERTURA = """<?xml version="1.0" ?>
<coverage line-rate="0.66" branch-rate="0.5" version="1.9">
  <sources><source>.</source></sources>
  <packages>
    <package name="app">
      <classes>
        <class filename="app/billing.py" name="billing">
          <lines>
            <line number="5" hits="2"/>
            <line number="7" hits="1" branch="true" condition-coverage="50% (1/2)"/>
            <line number="9" hits="0"/>
          </lines>
        </class>
      </classes>
    </package>
  </packages>
</coverage>
"""

# com/acme/Tax.java: LINE 8 covered + 2 missed = 10 found; BRANCH 3 + 1 = 4
JACOCO = """<?xml version="1.0" ?>
<report name="acme">
  <package name="com/acme">
    <sourcefile name="Tax.java">
      <line nr="12" mi="0" ci="4" mb="0" cb="2"/>
      <line nr="15" mi="3" ci="0" mb="1" cb="0"/>
      <counter type="LINE" missed="2" covered="8"/>
      <counter type="BRANCH" missed="1" covered="3"/>
    </sourcefile>
  </package>
</report>
"""

# pkg/tax.go: blocks of 3 (hit), 2 (miss), 4 (repeated: hit THEN miss)
# The repeated block is ordered 1 then 0 on purpose. With 0-then-1 both a
# correct merge and a naive overwrite yield "hit", so the test cannot fail.
GOCOVER = """mode: set
pkg/tax.go:10.20,14.3 3 1
pkg/tax.go:16.2,18.9 2 0
pkg/tax.go:20.2,24.4 4 1
pkg/tax.go:20.2,24.4 4 0
"""

COVERAGEPY = json.dumps({
    "files": {
        "app/orders.py": {
            "summary": {
                "num_statements": 10, "covered_lines": 7,
                "num_branches": 4, "covered_branches": 2,
            },
            "missing_lines": [11, 12, 30],
        }
    }
})


def run(text, fmt=None):
    fmt = fmt or nc.detect_format(text, "fixture")
    return nc.normalize(text, fmt, source="fixture")


def by_path(result):
    return {f["path"]: f for f in result["files"]}


# --------------------------------------------------------------------------
# detection
# --------------------------------------------------------------------------

class TestDetect(unittest.TestCase):
    def test_each_format_is_recognised(self):
        cases = [
            (LCOV, "lcov"), (COBERTURA, "cobertura"), (JACOCO, "jacoco"),
            (GOCOVER, "gocover"), (COVERAGEPY, "coveragepy"),
        ]
        for text, expected in cases:
            with self.subTest(expected=expected):
                self.assertEqual(nc.detect_format(text, "f"), expected)

    def test_info_extension_implies_lcov(self):
        self.assertEqual(nc.detect_format("SF:a.js\nend_of_record\n", "lcov.info"), "lcov")

    def test_unknown_input_raises_rather_than_guessing(self):
        with self.assertRaises(nc.Detect):
            nc.detect_format("just some prose\n", "notes.txt")

    def test_foreign_xml_is_rejected(self):
        with self.assertRaises(nc.Detect):
            nc.detect_format("<project><build/></project>", "pom.xml")


# --------------------------------------------------------------------------
# per-format parsing
# --------------------------------------------------------------------------

class TestLcov(unittest.TestCase):
    def setUp(self):
        self.files = by_path(run(LCOV))

    def test_line_counts(self):
        tax = self.files["src/tax.js"]
        self.assertEqual((tax["lines_found"], tax["lines_hit"]), (4, 3))
        self.assertEqual(tax["line_pct"], 75.0)

    def test_unreached_and_untaken_branches_are_both_misses(self):
        # Two "-" (never reached) plus one explicit "0" (reached, not taken)
        # against two genuine hits.
        tax = self.files["src/tax.js"]
        self.assertEqual((tax["branches_found"], tax["branches_hit"]), (5, 2))
        self.assertEqual(tax["branch_pct"], 40.0)

    def test_branch_recorded_as_zero_is_not_a_hit(self):
        # lcov writes 0 for an instrumented branch whose arm never executed.
        one = by_path(run("SF:a.js\nBRDA:1,0,0,0\nend_of_record\n", "lcov"))["a.js"]
        self.assertEqual((one["branches_found"], one["branches_hit"]), (1, 0))
        self.assertEqual(one["branch_pct"], 0.0)

    def test_uncovered_line_numbers_are_reported(self):
        self.assertEqual(self.files["src/tax.js"]["uncovered_lines"], [3])

    def test_file_without_branches_reports_none_not_zero(self):
        # 0% would read as "all branches missed" — a gap that does not exist.
        self.assertIsNone(self.files["src/util.js"]["branch_pct"])


class TestCobertura(unittest.TestCase):
    def setUp(self):
        self.files = by_path(run(COBERTURA))

    def test_lines(self):
        f = self.files["app/billing.py"]
        self.assertEqual((f["lines_found"], f["lines_hit"]), (3, 2))
        self.assertEqual(f["uncovered_lines"], [9])

    def test_condition_coverage_parsed_from_parenthesised_counts(self):
        f = self.files["app/billing.py"]
        self.assertEqual((f["branches_found"], f["branches_hit"]), (2, 1))

    def test_branch_without_counts_contributes_nothing(self):
        xml = COBERTURA.replace(' condition-coverage="50% (1/2)"', "")
        f = by_path(run(xml))["app/billing.py"]
        self.assertEqual((f["branches_found"], f["branches_hit"]), (0, 0))


class TestJacoco(unittest.TestCase):
    def setUp(self):
        self.files = by_path(run(JACOCO))

    def test_package_name_becomes_path_prefix(self):
        self.assertIn("com/acme/Tax.java", self.files)

    def test_counters_drive_totals(self):
        f = self.files["com/acme/Tax.java"]
        self.assertEqual((f["lines_found"], f["lines_hit"]), (10, 8))
        self.assertEqual((f["branches_found"], f["branches_hit"]), (4, 3))

    def test_zero_instruction_lines_are_uncovered(self):
        self.assertEqual(self.files["com/acme/Tax.java"]["uncovered_lines"], [15])


class TestGoCover(unittest.TestCase):
    def setUp(self):
        self.files = by_path(run(GOCOVER))

    def test_statements_not_lines_are_counted(self):
        f = self.files["pkg/tax.go"]
        self.assertEqual(f["lines_found"], 9)      # 3 + 2 + 4 (merged once)

    def test_repeated_block_merges_hits_and_a_later_zero_cannot_erase_one(self):
        # The same span appears twice, 1 then 0. Counting it twice would
        # inflate the denominator; letting the later 0 overwrite the 1 would
        # report covered code as uncovered.
        f = self.files["pkg/tax.go"]
        self.assertEqual(f["lines_hit"], 7)        # 3 + 4
        self.assertEqual(f["uncovered_lines"], [16])

    def test_block_hit_only_on_a_later_run_is_still_a_hit(self):
        text = "mode: count\npkg/a.go:1.1,2.1 2 0\npkg/a.go:1.1,2.1 2 3\n"
        f = by_path(run(text, "gocover"))["pkg/a.go"]
        self.assertEqual((f["lines_found"], f["lines_hit"]), (2, 2))


class TestCoveragePy(unittest.TestCase):
    def setUp(self):
        self.files = by_path(run(COVERAGEPY))

    def test_summary_fields(self):
        f = self.files["app/orders.py"]
        self.assertEqual((f["lines_found"], f["lines_hit"]), (10, 7))
        self.assertEqual((f["branches_found"], f["branches_hit"]), (4, 2))
        self.assertEqual(f["uncovered_lines"], [11, 12, 30])

    def test_json_without_files_map_is_rejected(self):
        with self.assertRaises(nc.Detect):
            run(json.dumps({"meta": {}}), "coveragepy")


# --------------------------------------------------------------------------
# totals, options, CLI
# --------------------------------------------------------------------------

class TestTotals(unittest.TestCase):
    def test_totals_sum_across_files(self):
        t = run(LCOV)["totals"]
        self.assertEqual(t["files"], 2)
        self.assertEqual((t["lines_found"], t["lines_hit"]), (6, 5))
        # pct() rounds to 2dp, so this compares against a value the code
        # itself produced, not against a decimal the hardware cannot hold.
        self.assertEqual(t["line_pct"], 83.33)  # testsquad:ignore float-equality -- rounded upstream
        self.assertEqual((t["branches_found"], t["branches_hit"]), (5, 2))

    def test_empty_report_yields_none_not_a_hundred_percent(self):
        # An empty suite must never look like a perfect one.
        result = run("TN:\n", "lcov")
        self.assertEqual(result["totals"]["files"], 0)
        self.assertIsNone(result["totals"]["line_pct"])


class TestOptions(unittest.TestCase):
    def test_strip_prefix(self):
        result = nc.normalize(LCOV, "lcov", strip_prefix="src")
        self.assertIn("tax.js", by_path(result))

    def test_uncovered_lines_are_capped_and_the_remainder_counted(self):
        body = "".join(f"DA:{i},0\n" for i in range(1, 51))
        f = by_path(nc.normalize(f"SF:a.js\n{body}end_of_record\n", "lcov",
                                 max_uncovered=10))["a.js"]
        self.assertEqual(len(f["uncovered_lines"]), 10)
        self.assertEqual(f["uncovered_lines_truncated"], 40)


class TestCli(unittest.TestCase):
    def _write(self, tmp, name, text):
        path = os.path.join(tmp, name)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        return path

    def test_writes_json_file_and_exits_zero(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            src = self._write(tmp, "lcov.info", LCOV)
            out = os.path.join(tmp, "out.json")
            self.assertEqual(nc.main([src, "--out", out]), 0)
            with open(out) as fh:
                data = json.load(fh)
            self.assertEqual(data["schema"], nc.SCHEMA)
            self.assertEqual(data["totals"]["files"], 2)

    def test_missing_file_exits_nonzero(self):
        self.assertEqual(nc.main(["/nope/missing.info"]), 2)

    def test_empty_file_exits_nonzero(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(nc.main([self._write(tmp, "e.info", "  \n")]), 2)

    def test_malformed_xml_exits_nonzero_rather_than_reporting_zero(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            bad = self._write(tmp, "coverage.xml", "<coverage><classes></coverage>")
            self.assertEqual(nc.main([bad]), 2)


# --------------------------------------------------------------------------
# malformed input: never a confident number
# --------------------------------------------------------------------------
#
# Each case below produced either a crash or a confident wrong number before
# the encoding/type checks landed. The expectations are computed from the
# fixture text by hand, as everywhere else in this file.

class TestEncoding(unittest.TestCase):
    def _write_bytes(self, tmp, name, raw):
        path = os.path.join(tmp, name)
        with open(path, "wb") as fh:
            fh.write(raw)
        return path

    def test_utf16_lcov_is_decoded_not_read_as_zero_coverage(self):
        # Read as UTF-8 this is mojibake: no DA: record matches and the run
        # ends with 0 files and line_pct None -- indistinguishable from "this
        # project has no coverage data", which a gate waves through.
        import tempfile
        for encoding in ("utf-16", "utf-16-le", "utf-16-be"):
            with self.subTest(encoding=encoding), tempfile.TemporaryDirectory() as tmp:
                raw = LCOV.encode(encoding)
                if encoding != "utf-16":  # the BOM is what we sniff
                    raw = (b"\xff\xfe" if encoding.endswith("le") else b"\xfe\xff") + raw
                src = self._write_bytes(tmp, "lcov.info", raw)
                out = os.path.join(tmp, "o.json")
                self.assertEqual(nc.main([src, "--out", out]), 0)
                with open(out, encoding="utf-8") as fh:
                    data = json.load(fh)
                # The same numbers the UTF-8 fixture produces: 6 lines, 5 hit.
                self.assertEqual(data["totals"]["files"], 2)
                self.assertEqual((data["totals"]["lines_found"],
                                  data["totals"]["lines_hit"]), (6, 5))

    def test_utf8_bom_on_coverage_json_is_recognised(self):
        # coverage.py reads its own BOM'd report fine, so refusing it would
        # reject a file the ecosystem considers valid.
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            src = self._write_bytes(tmp, "coverage.json",
                                    b"\xef\xbb\xbf" + COVERAGEPY.encode("utf-8"))
            out = os.path.join(tmp, "o.json")
            self.assertEqual(nc.main([src, "--out", out]), 0)
            with open(out, encoding="utf-8") as fh:
                data = json.load(fh)
            self.assertEqual(data["source_format"], "coveragepy")
            self.assertEqual(data["totals"]["lines_found"], 10)

    def test_detect_format_sees_through_a_bom(self):
        # \ufeff is not whitespace, so lstrip() alone leaves it in front of
        # the '{' that the sniff keys on.
        self.assertEqual(nc.detect_format("\ufeff" + COVERAGEPY, "coverage.json"),
                         "coveragepy")

    def test_a_single_undecodable_byte_still_yields_the_report(self):
        # The UTF-8 fallback keeps errors="replace" on purpose: one bad byte
        # in a path must cost that character, not the whole report.
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            src = self._write_bytes(tmp, "lcov.info",
                                    b"SF:src/caf\xe9.js\nDA:1,1\nend_of_record\n")
            out = os.path.join(tmp, "o.json")
            self.assertEqual(nc.main([src, "--out", out]), 0)
            with open(out, encoding="utf-8") as fh:
                data = json.load(fh)
            self.assertEqual(data["totals"]["lines_found"], 1)

    def test_a_record_format_that_parsed_nothing_is_an_error(self):
        # "parsed nothing" and "measured nothing" must not render identically.
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, "lcov.info")
            with open(src, "w", encoding="utf-8") as fh:
                fh.write("TN:\nTN:\n")
            self.assertEqual(nc.main([src]), 2)


class TestMalformedJacoco(unittest.TestCase):
    def test_counter_missing_missed_is_not_a_perfect_score(self):
        # covered="5" with no missed= used to read as 5/5 = 100%: a malformed
        # input rendering as a flawless result.
        xml = ('<report><package name="p"><sourcefile name="A.java">'
               '<counter type="LINE" covered="5"/>'
               '</sourcefile></package></report>')
        result = run(xml, "jacoco")
        self.assertEqual(result["totals"]["lines_found"], 0)
        self.assertIsNone(result["totals"]["line_pct"])

    def test_counter_missing_covered_is_skipped_too(self):
        xml = ('<report><package name="p"><sourcefile name="A.java">'
               '<counter type="BRANCH" missed="3"/>'
               '</sourcefile></package></report>')
        result = run(xml, "jacoco")
        self.assertEqual(result["totals"]["branches_found"], 0)
        self.assertIsNone(result["totals"]["branch_pct"])

    def test_well_formed_counters_beside_a_broken_one_still_count(self):
        # The file's totals reflect exactly the counters that parsed: 2 of 8
        # lines covered, and nothing from the half-written BRANCH counter.
        xml = ('<report><package name="p"><sourcefile name="A.java">'
               '<counter type="LINE" missed="6" covered="2"/>'
               '<counter type="BRANCH" covered="4"/>'
               '</sourcefile></package></report>')
        result = run(xml, "jacoco")
        self.assertEqual((result["totals"]["lines_found"],
                          result["totals"]["lines_hit"]), (8, 2))
        self.assertEqual(result["totals"]["line_pct"], 25.0)
        self.assertEqual(result["totals"]["branches_found"], 0)


class TestMalformedCoveragePy(unittest.TestCase):
    def test_a_top_level_json_array_is_refused_not_a_traceback(self):
        with self.assertRaises(nc.Detect):
            nc.normalize('[{"a": 1}]', "coveragepy")

    def test_a_top_level_json_scalar_is_refused(self):
        for text in ('"hello"', "42", "null"):
            with self.subTest(text=text), self.assertRaises(nc.Detect):
                nc.normalize(text, "coveragepy")

    def test_the_cli_exits_two_on_a_json_array(self):
        # Reachable whenever --format coveragepy is passed explicitly, which
        # the skills do when they already know which tool wrote the report.
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, "coverage.json")
            with open(src, "w", encoding="utf-8") as fh:
                fh.write('[{"a": 1}]')
            self.assertEqual(nc.main([src, "--format", "coveragepy"]), 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
