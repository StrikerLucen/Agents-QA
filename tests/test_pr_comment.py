#!/usr/bin/env python3
"""pr_comment.py: the comment must survive its own input.

This script renders the plugin's primary surface. Its input is JSON written by
agents and edited by hand, and its output is a public pull-request comment. So
there are only two ways to fail here, and both are visible to strangers:

  * a traceback in a CI step on somebody's pull request, because a `count`
    said "many";
  * a secret, or a forged table row, published under the tool's name.

Every test below is one of those two. The malformed-input cases assert that
*something useful still renders* and that the comment admits the input was
bad -- a degraded report that claims to be complete is the failure mode worth
more than the crash. The output-safety cases assert on the rendered text, not
on the helper functions, because a renderer that grows one debug line would
pass a unit test of its filter and still leak.

Secrets are checked in eight-character windows, the same rule the safety gate
uses: half a key is still a leak, and a redaction that keeps a recognisable
prefix is not a redaction.
"""

import json
import os
import re
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import pr_comment as prc  # noqa: E402


MALFORMED_NOTICE = "Some input was malformed"


def assert_no_secret(case, body, secret, label=""):
    """No eight-character window of `secret` may appear anywhere in `body`."""
    for i in range(0, max(1, len(secret) - 7)):
        window = secret[i:i + 8]
        if len(window) < 8:
            break
        case.assertNotIn(
            window, body,
            f"{label or 'secret'} leaked: the window {window!r} "
            f"(offset {i}) survived redaction")


def headline(body):
    """The first line under the heading -- the one a reader acts on.

    Asserting on the whole body is exactly what let the worst defect in this
    file live: the `missing_inputs` blockquote reported the truth while the
    headline above it said "No new findings on this change.", and a body-wide
    assertion found both strings and was happy. The headline is the claim.
    """
    lines = [line for line in body.splitlines() if line.strip()]
    for i, line in enumerate(lines):
        if line.strip() == "## testsquad":
            return lines[i + 1].strip() if i + 1 < len(lines) else ""
    raise AssertionError("the comment has no `## testsquad` heading")


def table_rows(body):
    """Every markdown table row in the body, split into its cells.

    Splitting on an *unescaped* pipe is the whole point: a cell that escaped
    its pipe adds no column, and a cell that did not adds one.
    """
    rows = []
    for line in body.splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        cells = re.split(r"(?<!\\)\|", line)
        rows.append([c.strip() for c in cells[1:-1]])
    return rows


class RenderNeverRaises(unittest.TestCase):
    """The four crashes found by robustness testing, plus the shapes next to
    them. Each must render a comment, not a traceback."""

    def body(self, data):
        body = prc.render(data)
        self.assertIsInstance(body, str)
        self.assertIn(prc.MARKER, body,
                      "the marker is how CI finds and updates its own comment")
        return body

    # -- defect 1: a data file that is not an object ------------------------

    def test_top_level_list_renders_an_empty_report(self):
        """An empty report, but never a clean one: the data file is itself an
        input, and one nobody could parse is 'nobody looked', not 'nothing
        found'. This test asserted the opposite until the headline was fixed."""
        body = self.body([1, 2, 3])
        self.assertNotIn("No new findings", body)
        self.assertIn("Nothing was measured", headline(body))
        self.assertIn(MALFORMED_NOTICE, body)

    def test_top_level_scalars_render(self):
        for blob in ("a string", 42, 3.5, True, None):
            with self.subTest(blob=blob):
                self.assertIn(prc.MARKER, prc.render(blob))

    def test_load_rejects_a_non_object_json_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "d.json")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("[1, 2, 3]")
            self.assertEqual({}, prc.load(path))

    def test_main_refuses_a_data_file_that_is_a_list(self):
        """A truthy non-dict used to slip past main()'s `if not data` guard and
        reach render(). The refusal is exit 2 with a message, not a crash."""
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "d.json")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("[1, 2, 3]")
            self.assertEqual(2, prc.main(["--data", path]))

    def test_main_refuses_unparseable_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "d.json")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("{not json")
            self.assertEqual(2, prc.main(["--data", path]))

    # -- defect 2: a coverage percentage that is not a number ---------------

    def test_coverage_percentage_as_a_string_is_read_at_face_value(self):
        """"90.0" is the measurement, typed by hand or built by jq. Reading it
        is not inventing it -- but the type was still wrong, so the comment
        says so."""
        body = self.body({"coverage": {"line_pct": "90.0", "base_line_pct": 80.0}})
        self.assertIn("90.0%", body)
        self.assertIn("+10.0 pts", body)
        self.assertIn(MALFORMED_NOTICE, body)

    def test_unusable_coverage_is_not_measured_rather_than_zero(self):
        """The one substitution that must never happen: a missing or junk
        figure rendered as a plausible number."""
        for value in ("ninety", {"pct": 90}, [90], "", float("nan")):
            with self.subTest(value=value):
                body = self.body({"coverage": {"line_pct": value}})
                self.assertIn("not measured", body)
                self.assertNotIn("0.0%", body)
                self.assertIn(MALFORMED_NOTICE, body)

    def test_a_coverage_boolean_is_not_a_percentage(self):
        body = self.body({"coverage": {"line_pct": True}})
        self.assertIn("not measured", body)
        self.assertNotIn("1.0%", body)

    def test_uncovered_line_count_that_is_junk_omits_the_sentence(self):
        body = self.body({"coverage": {"line_pct": 80.0,
                                       "uncovered_changed_lines": "lots"}})
        self.assertNotIn("changed line(s) are not", body)
        self.assertIn(MALFORMED_NOTICE, body)

    # -- defect 3: a non-numeric finding count ------------------------------

    def test_non_numeric_finding_count_still_renders_the_finding(self):
        body = self.body({"findings": [{"evidence": "reproduced", "severity": "S2",
                                        "title": "boom", "count": "many"}]})
        self.assertIn("boom", body)
        self.assertNotIn("×", body, "no multiplier may be invented from junk")
        self.assertIn(MALFORMED_NOTICE, body)

    def test_a_real_count_still_renders_its_multiplier(self):
        body = self.body({"findings": [{"evidence": "reproduced", "severity": "S2",
                                        "title": "boom", "count": 4}]})
        self.assertIn("(×4)", body)
        self.assertNotIn(MALFORMED_NOTICE, body)

    def test_a_numeric_string_count_is_read_and_declared(self):
        body = self.body({"findings": [{"evidence": "reproduced", "severity": "S2",
                                        "title": "boom", "count": "3"}]})
        self.assertIn("(×3)", body)

    def test_an_infinite_or_nan_count_does_not_crash(self):
        """`json.load` accepts the JavaScript spellings by default, so
        `"count": Infinity` in a hand-written findings.json reaches as_count()
        as a real float. int() raises OverflowError on it -- not a ValueError,
        so it escapes a `except ValueError` and lands in the PR as a
        traceback. NaN is the same story."""
        for literal in ("Infinity", "-Infinity", "NaN"):
            with self.subTest(count=literal):
                blob = ('{"findings": [{"evidence": "reproduced", '
                        '"severity": "S2", "title": "boom", "count": %s}]}'
                        % literal)
                body = self.body(json.loads(blob))
                self.assertIn("boom", body)
                self.assertNotIn("×", body,
                                 "no multiplier may be invented from infinity")
                self.assertIn(MALFORMED_NOTICE, body)

    def test_an_infinite_pre_existing_count_does_not_crash(self):
        body = prc.render(json.loads('{"pre_existing_count": Infinity}'))
        self.assertIn(prc.MARKER, body)
        self.assertNotIn("inf", body.lower())

    def test_sorting_survives_mixed_junk_counts(self):
        body = self.body({"findings": [
            {"evidence": "reproduced", "severity": "S3", "title": "low", "count": {}},
            {"evidence": "reproduced", "severity": "S1", "title": "high", "count": 2},
        ]})
        self.assertLess(body.index("high"), body.index("low"))

    # -- defect 4: containers of the wrong shape ----------------------------

    def test_gates_as_an_object_drops_the_section_and_says_so(self):
        body = self.body({"gates": {"coverage": 80}})
        self.assertNotIn("### Quality gates", body)
        self.assertIn("`gates` was not a list", body)

    def test_findings_as_a_list_of_strings_drops_them_and_says_so(self):
        body = self.body({"findings": ["something went wrong"]})
        self.assertNotIn("something went wrong", body)
        self.assertIn(MALFORMED_NOTICE, body)

    def test_a_mixed_findings_list_keeps_the_good_rows(self):
        """The design rule in one test: one malformed entry degrades itself,
        not the comment."""
        body = self.body({"findings": [
            "junk",
            {"evidence": "reproduced", "severity": "S1", "title": "real finding"},
            None,
        ]})
        self.assertIn("real finding", body)
        self.assertIn("entr(ies) in `findings`", body)

    def test_health_and_coverage_of_the_wrong_type(self):
        for key in ("health", "coverage"):
            for value in ([1, 2], "red", 7):
                with self.subTest(key=key, value=value):
                    body = self.body({key: value})
                    self.assertIn(MALFORMED_NOTICE, body)

    def test_missing_inputs_of_the_wrong_type(self):
        self.assertIn("Not everything ran",
                      self.body({"missing_inputs": "gates.json"}))

    def test_pre_existing_count_that_is_junk_does_not_crash(self):
        self.assertIn(prc.MARKER, prc.render({"pre_existing_count": "lots"}))

    def test_deeply_nested_and_oddly_typed_values_everywhere(self):
        """A single blob with every field the wrong type at once. Nothing here
        should be renderable; everything here must render."""
        body = self.body({
            "health": {"verdict": ["AMBER"], "flaky_count": {}},
            "coverage": {"line_pct": [], "base_line_pct": "x"},
            "gates": [{"name": {}, "target": [], "actual": None, "met": "yes"}],
            "findings": [{"evidence": "reproduced", "severity": 5,
                          "title": {"t": 1}, "file": [], "line": {},
                          "count": None, "repro": {}}],
            "missing_inputs": [{"a": 1}],
            "pre_existing_count": [],
        })
        self.assertIn(MALFORMED_NOTICE, body)

    def test_unicode_and_control_characters_render(self):
        body = self.body({"findings": [{
            "evidence": "observed", "severity": "S3",
            "title": "null \x00 byte ‮ rtl \U0001f600 emoji\ttab"}]})
        self.assertIn("### New findings", body)


class OutputSafety(unittest.TestCase):
    """The two defects that matter more than the crashes: what reaches a
    public comment."""

    # -- defect 5: redaction ------------------------------------------------

    SECRETS = {
        "stripe": "sk_live_FIXTUREONLYNeverARealKey4242",
        "stripe_pub": "pk_live_FIXTUREONLYNeverARealKey4242",
        "aws": "AKIAIOSFODNN7EXAMPLE",
        "github_pat": "ghp_" + "A" * 36,
        "github_oauth": "gho_" + "B" * 36,
        "gitlab": "glpat-" + "C" * 20,
        "slack": "xoxb-1234567890-ABCDEFGHIJKLMNOP",
        "google": "AIza" + "D" * 35,
        "npm": "npm_" + "E" * 36,
        "jwt": ("eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
                "eyJzdWIiOiIxMjM0NTY3ODkwIn0.dBjftJeZ4CVPmB92K27uhbUJU1p1r_wW1g"),
    }

    def render_title(self, text):
        return prc.render({"findings": [
            {"evidence": "reproduced", "severity": "S1", "title": text}]})

    def test_every_known_shape_is_redacted_out_of_a_title(self):
        for name, secret in self.SECRETS.items():
            with self.subTest(shape=name):
                body = self.render_title(f"config leaks {secret} on line 3")
                assert_no_secret(self, body, secret, name)
                self.assertIn("[redacted", body)
                self.assertIn("config leaks", body,
                              "redaction must keep the finding readable")

    def test_secrets_are_redacted_in_every_field_that_reaches_the_output(self):
        """A title is the obvious one. A repro command is where a token is
        likeliest to actually be, and a file path can carry a query string."""
        secret = self.SECRETS["stripe"]
        data = {
            "health": {"verdict": secret},
            "gates": [{"name": secret, "target": secret, "actual": secret}],
            "findings": [{"evidence": "reproduced", "severity": "S1",
                          "title": secret, "file": secret, "line": secret,
                          "repro": f"curl -H 'Authorization: Bearer {secret}'"}],
            "missing_inputs": [secret],
        }
        assert_no_secret(self, prc.render(data), secret, "every field")

    def test_a_credential_url_is_redacted_whole(self):
        url = "postgres://admin:hunter2CorrectHorse@db.prod.example.com:5432/app"
        body = self.render_title(f"test uses {url}")
        assert_no_secret(self, body, "hunter2CorrectHorse", "the password")
        self.assertNotIn("db.prod.example.com", body,
                         "a connection string is a secret in one piece")

    def test_a_bearer_token_is_redacted(self):
        token = "Zm9vYmFyYmF6cXV4MTIzNDU2Nzg5MEFCQ0RFRg"
        body = self.render_title(f"sends Authorization: Bearer {token}")
        assert_no_secret(self, body, token, "the bearer token")

    def test_a_high_entropy_value_on_a_key_looking_field_is_redacted(self):
        value = "Xq7vP2mLz9KdR4twYbN8sGhJ3fCeA6uE"
        for key in ("API_KEY", "db_password", "SESSION_SECRET", "access_key"):
            with self.subTest(key=key):
                body = self.render_title(f"fixture sets {key}={value} inline")
                assert_no_secret(self, body, value, f"the value of {key}")
                self.assertIn(key, body,
                              "the key name is the useful half of the message")

    def test_ordinary_titles_are_left_alone(self):
        """A redaction pass that eats real findings is worse than none: the
        reader stops reading. These must survive verbatim."""
        for title in (
            "discount applied before tax in checkout",
            "test_user_login asserts on a hard-coded timestamp",
            "retry loop races with the connection pool",
            "password field is logged at debug level",
            "api_key is read from the environment, not hard-coded",
        ):
            with self.subTest(title=title):
                body = self.render_title(title)
                self.assertIn(title, body)
                self.assertNotIn("[redacted", body)

    def test_redaction_runs_before_truncation(self):
        """A long title is capped. If the cap ran first, a secret past the cap
        would be dropped by luck rather than by rule -- and one just inside it
        would survive. Put the secret at the very front to prove the order."""
        secret = self.SECRETS["github_pat"]
        body = self.render_title(secret + " " + "padding " * 200)
        assert_no_secret(self, body, secret, "a secret in a capped title")

    # -- defect 6: markdown escaping ----------------------------------------

    def test_a_pipe_in_a_title_cannot_forge_a_column(self):
        body = prc.render({"findings": [
            {"evidence": "reproduced", "severity": "S4",
             "title": "x | ✅ passed | everything fine"}]})
        self.assertNotIn("| ✅ passed |", body,
                         "the title forged two columns of its own verdict")
        finding_rows = [r for r in table_rows(body) if "S4" in r]
        self.assertTrue(finding_rows, "the finding row disappeared entirely")
        for row in finding_rows:
            self.assertEqual(4, len(row),
                             f"a title added columns to the row: {row}")

    def test_a_pipe_in_every_other_cell_cannot_forge_a_column(self):
        body = prc.render({
            "gates": [{"name": "a | b", "target": "c | d", "actual": "e | f",
                       "met": False}],
            "findings": [{"evidence": "observed", "severity": "S3",
                          "title": "t", "file": "src/a|b.py", "line": "1|2"}],
        })
        for row in table_rows(body):
            if row and not set("".join(row)) <= set("-: "):
                self.assertEqual(4, len(row), f"forged columns: {row}")

    def test_a_newline_in_a_title_cannot_end_the_row(self):
        body = prc.render({"findings": [
            {"evidence": "reproduced", "severity": "S4",
             "title": "innocent\n| S1 | forged | ✅ reproduced | `x` |"}]})
        self.assertNotIn("forged | ✅ reproduced", body)
        self.assertEqual(1, body.count("S1"),
                         "a forged severity row reached the table")

    def test_html_in_a_title_is_escaped(self):
        body = self.render_title_only("<img src=x onerror=alert(1)>")
        self.assertNotIn("<img", body)
        self.assertIn("&lt;img", body)

    def test_a_leading_hash_or_quote_cannot_escape_the_table(self):
        for title in ("# Everything is fine", "> quoted verdict",
                      "- a list item"):
            with self.subTest(title=title):
                body = self.render_title_only(title)
                for line in body.splitlines():
                    self.assertFalse(
                        line.startswith("# ") and "Everything is fine" in line,
                        "a title became a heading")
                    self.assertFalse(
                        line.startswith("> ") and "quoted verdict" in line,
                        "a title became a block quote")

    def test_backticks_in_a_title_cannot_open_a_code_span(self):
        body = self.render_title_only("a ` b ``` c")
        self.assertNotIn("```", body.split("### New findings")[1]
                         if "### New findings" in body else body)

    def test_a_repro_command_cannot_close_its_own_fence(self):
        body = prc.render({"findings": [
            {"evidence": "reproduced", "severity": "S1", "title": "t",
             "repro": "pytest -k x\n```\n## FORGED HEADING\n```bash\n"}]})
        # Inside an intact fence the text is literal, so the fence count is
        # the property that matters: a repro that could close its own fence
        # would put the rest of its payload back into markdown.
        self.assertEqual(2, body.count("```"),
                         "the repro block opened or closed extra fences")
        before, _, rest = body.partition("```bash")
        inside, _, after = rest.partition("```")
        self.assertIn("FORGED HEADING", inside)
        self.assertNotIn("FORGED HEADING", before + after)

    def test_a_pathological_title_is_capped(self):
        body = self.render_title_only("A" * 50_000)
        self.assertLess(len(body), 5_000,
                        "one title blew up the whole comment")
        self.assertIn("…", body)

    def test_a_pathological_repro_is_capped(self):
        body = prc.render({"findings": [
            {"evidence": "reproduced", "severity": "S1", "title": "t",
             "repro": "pytest " + "x" * 50_000}]})
        self.assertLess(len(body), 5_000)

    def test_many_findings_are_capped_and_the_remainder_disclosed(self):
        body = prc.render({"findings": [
            {"evidence": "reproduced", "severity": "S3", "title": f"f{i}"}
            for i in range(60)]})
        self.assertIn("more in the full report", body)

    def render_title_only(self, text):
        return prc.render({"findings": [
            {"evidence": "reproduced", "severity": "S4", "title": text}]})


class ExistingBehaviourStillHolds(unittest.TestCase):
    """The properties that were already right. Hardening must not cost any of
    them -- each one is load-bearing for whether the comment is trusted."""

    def test_unconfirmed_findings_are_excluded_and_counted(self):
        body = prc.render({"findings": [
            {"severity": "S1", "title": "sql built by concatenation",
             "evidence": "code-read"},
            {"severity": "S2", "title": "possible race", "evidence": "suspicion"},
            {"severity": "S2", "title": "discount applied before tax",
             "evidence": "reproduced"},
        ]})
        self.assertNotIn("sql built by concatenation", body)
        self.assertNotIn("possible race", body)
        self.assertIn("discount applied before tax", body)
        self.assertIn("2 unconfirmed item(s) withheld", body)

    def test_an_unknown_evidence_value_is_treated_as_unproven(self):
        """The gate is an allowlist, not a denylist. A typo, a new evidence
        kind, or an attacker-chosen string all fail closed."""
        for evidence in ("REPRODUCED!", "confirmed", "", None, 42, ["reproduced"]):
            with self.subTest(evidence=evidence):
                body = prc.render({"findings": [
                    {"severity": "S1", "title": "unproven claim",
                     "evidence": evidence}]})
                self.assertNotIn("unproven claim", body)

    def test_evidence_casing_still_publishes(self):
        body = prc.render({"findings": [
            {"severity": "S1", "title": "real one", "evidence": "Reproduced"}]})
        self.assertIn("real one", body)

    def test_red_health_withholds_the_coverage_numbers(self):
        body = prc.render({
            "health": {"verdict": "RED", "flaky_count": 3},
            "coverage": {"line_pct": 81.0, "base_line_pct": 80.0},
        })
        self.assertIn("not healthy", body)
        self.assertNotIn("81.0%", body)
        self.assertNotIn("+1.0 pts", body)

    def test_red_health_still_says_coverage_was_measured_but_withheld(self):
        body = prc.render({"health": {"verdict": "RED"},
                           "coverage": {"line_pct": 81.0}})
        self.assertIn("Coverage on the lines this PR changed", body)
        self.assertIn("Not shown", body)

    def test_missing_inputs_are_named_with_their_producing_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            testing = os.path.join(tmp, ".testing")
            os.makedirs(testing)
            with open(os.path.join(testing, "health-baseline.json"), "w",
                      encoding="utf-8") as fh:
                json.dump({"verdict": "GREEN"}, fh)
            out = os.path.join(tmp, "c.md")
            self.assertEqual(0, prc.main(["--testing-dir", testing, "--out", out]))
            with open(out, encoding="utf-8") as fh:
                body = fh.read()
        self.assertIn("Not everything ran", body)
        self.assertIn("gates.json (run /testsquad:verify)", body)
        self.assertIn("findings.json (run /testsquad:test or :debug)", body)

    def test_an_absent_testing_directory_refuses_rather_than_reassures(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "c.md")
            self.assertEqual(2, prc.main(
                ["--testing-dir", os.path.join(tmp, "nope"), "--out", out]))
            self.assertFalse(os.path.exists(out))

    def test_a_malformed_input_file_does_not_produce_a_reassuring_comment(self):
        """The worst possible output: every file present but unreadable, and a
        comment that says 'no new findings' with nothing to indicate why."""
        with tempfile.TemporaryDirectory() as tmp:
            testing = os.path.join(tmp, ".testing")
            os.makedirs(testing)
            for name in ("health-baseline.json", "coverage-diff.json",
                         "gates.json", "findings.json"):
                with open(os.path.join(testing, name), "w", encoding="utf-8") as fh:
                    fh.write("[]")
            out = os.path.join(tmp, "c.md")
            self.assertEqual(0, prc.main(["--testing-dir", testing, "--out", out]))
            with open(out, encoding="utf-8") as fh:
                body = fh.read()
        self.assertIn(prc.MARKER, body)
        # The assertion this test was named for, and did not make: a comment
        # is still rendered, but its headline says nothing was measured.
        self.assertNotIn("No new findings", headline(body))
        self.assertIn("Nothing was measured", headline(body))

    def test_unmeasured_coverage_is_words_not_a_number(self):
        body = prc.render({"coverage": {"line_pct": None, "branch_pct": None}})
        self.assertIn("not measured", body)
        self.assertNotIn("0.0%", body)

    def test_a_clean_run_carries_no_malformed_notice(self):
        """The notice must mean something. If it appeared on well-formed input
        it would be noise and get ignored, which is how the real one gets
        missed."""
        body = prc.render({
            "health": {"verdict": "GREEN", "flaky_count": 0},
            "coverage": {"line_pct": 81.0, "base_line_pct": 80.0,
                         "branch_pct": 70.0, "base_branch_pct": 70.0,
                         "uncovered_changed_lines": 2},
            "gates": [{"name": "coverage", "target": "80%", "actual": "81%",
                       "met": True}],
            "findings": [{"evidence": "reproduced", "severity": "S2",
                          "title": "discount applied before tax",
                          "file": "src/checkout.py", "line": 42,
                          "repro": "pytest -k discount"}],
            "pre_existing_count": 3,
        })
        self.assertNotIn(MALFORMED_NOTICE, body)
        self.assertIn("81.0%", body)
        self.assertIn("no change", body)
        self.assertIn("2 changed line(s) are not covered", body)
        self.assertIn("`src/checkout.py:42`", body)
        self.assertIn("pytest -k discount", body)
        self.assertIn("3 pre-existing finding(s)", body)

    def test_a_gate_actual_of_zero_is_a_measurement_not_a_blank(self):
        body = prc.render({"gates": [{"name": "flake rate", "target": 0,
                                      "actual": 0, "met": True}]})
        self.assertIn("| 0 |", body)

    def test_output_is_deterministic(self):
        """No model, no clock, no dict-ordering surprise: the same input must
        render byte-identically, or the CI comment churns on every push."""
        data = {"findings": [{"evidence": "reproduced", "severity": "S1",
                              "title": "x", "count": 2},
                             {"evidence": "observed", "severity": "S1",
                              "title": "y", "count": 5}],
                "coverage": {"line_pct": 80.0}}
        first = prc.render(json.loads(json.dumps(data)))
        for _ in range(5):
            self.assertEqual(first, prc.render(json.loads(json.dumps(data))))


CLEAN_HEADLINE = "No new findings on this change."

VALID_INPUTS = {
    "health-baseline.json": {"verdict": "GREEN"},
    "coverage-diff.json": {"line_pct": 81.0, "base_line_pct": 80.0},
    "gates.json": {"gates": [{"name": "coverage", "target": "80%",
                              "actual": "81%", "met": True}]},
    "findings.json": {"findings": []},
}


class TheHeadlineNeverClaimsAnAllClear(unittest.TestCase):
    """The defect this class exists for: `verdict_line()` could not see
    `missing_inputs`, so a run where **nothing ran** produced the bold first
    line "No new findings on this change." The body said one thing and the
    headline said the opposite, and the headline is the part people read.

    Three states must be distinguishable, which is the house rule stated as a
    test: "I could not read it", "nobody ran this" and "there is nothing
    wrong" must never render the same. Every assertion below is on the
    headline specifically -- a body that merely mentions the missing file
    somewhere is what the defect already did.
    """

    # -- fixtures ----------------------------------------------------------

    def make_testing(self, tmp, drop=(), corrupt=None):
        """A .testing/ with the four real inputs, minus `drop`, with every
        name in `corrupt` overwritten by that unreadable text."""
        testing = os.path.join(tmp, ".testing")
        os.makedirs(testing)
        for name, blob in VALID_INPUTS.items():
            if name in drop:
                continue
            with open(os.path.join(testing, name), "w", encoding="utf-8") as fh:
                if corrupt and name in corrupt:
                    fh.write(corrupt[name])
                else:
                    json.dump(blob, fh)
        return testing

    def run_main(self, testing, expect_rc=0):
        with tempfile.TemporaryDirectory() as out_dir:
            out = os.path.join(out_dir, "pr-comment.md")
            self.assertEqual(expect_rc, prc.main(
                ["--testing-dir", testing, "--out", out]))
            if expect_rc != 0:
                return None
            with open(out, encoding="utf-8") as fh:
                return fh.read()

    def assert_not_an_all_clear(self, line, why=""):
        self.assertNotEqual(CLEAN_HEADLINE, line, why)
        self.assertNotIn("No new findings", line, why)
        lowered = line.lower()
        self.assertTrue(
            "not everything ran" in lowered or "nothing was measured" in lowered,
            f"the headline does not flag the incomplete run: {line!r}")

    # -- state 1: everything ran, nothing found ----------------------------

    def test_all_inputs_present_and_clean_keeps_the_unchanged_headline(self):
        """The string that must survive the fix verbatim. If this drifts, the
        notice below stops meaning anything, because it fires on every run."""
        with tempfile.TemporaryDirectory() as tmp:
            body = self.run_main(self.make_testing(tmp))
        self.assertEqual(CLEAN_HEADLINE, headline(body))
        self.assertNotIn("Not everything ran", body)
        self.assertNotIn(MALFORMED_NOTICE, body)

    # -- state 2: everything ran, findings exist ---------------------------

    def test_a_complete_run_with_findings_is_unchanged(self):
        for data, expected in (
            ({"findings": [{"evidence": "reproduced", "severity": "S1",
                            "title": "boom"}]}, "**S1: boom**"),
            ({"findings": [{"evidence": "observed", "severity": "S3",
                            "title": "meh"}]},
             "No blocking issues. Some findings worth a look below."),
            ({"gates": [{"name": "coverage", "met": False}]},
             "**1 quality gate(s) not met:** coverage"),
            ({"health": {"verdict": "RED"}},
             "**The test suite is not healthy.** Coverage figures are withheld "
             "because they would not mean anything until it runs clean."),
        ):
            with self.subTest(data=data):
                self.assertEqual(expected, headline(prc.render(data)))

    # -- state 3a: nothing ran at all --------------------------------------

    def test_no_inputs_at_all_through_main_is_a_refusal_not_a_comment(self):
        """Documented behaviour, re-asserted here because it is the other half
        of the same promise: an absent .testing/ exits 2 and writes nothing."""
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "c.md")
            self.assertEqual(2, prc.main(
                ["--testing-dir", os.path.join(tmp, "nope"), "--out", out]))
            self.assertFalse(os.path.exists(out))

    def test_every_input_present_but_unreadable_says_nothing_was_measured(self):
        """The worst case the script's own comment names: four files on disk,
        none of them parseable, and -- before the fix -- a comment reading
        "No new findings on this change." with no notice of any kind."""
        with tempfile.TemporaryDirectory() as tmp:
            testing = self.make_testing(tmp, corrupt={
                name: "{not json" for name in VALID_INPUTS})
            body = self.run_main(testing)
        line = headline(body)
        self.assert_not_an_all_clear(line)
        self.assertIn("Nothing was measured", line)
        self.assertIn("not an all-clear", line)
        for name in VALID_INPUTS:
            self.assertIn(name, body, "the unreadable file must be named")

    def test_nothing_ran_is_reachable_through_render_alone(self):
        line = headline(prc.render({"missing_inputs": ["gates.json"],
                                    "inputs_read": 0}))
        self.assertIn("Nothing was measured", line)

    # -- state 3b: each input missing in isolation -------------------------

    def test_each_single_missing_input_denies_the_all_clear(self):
        for name in VALID_INPUTS:
            with self.subTest(missing=name):
                with tempfile.TemporaryDirectory() as tmp:
                    body = self.run_main(self.make_testing(tmp, drop=(name,)))
                self.assert_not_an_all_clear(
                    headline(body),
                    f"{name} was absent and the headline still read clean")
                self.assertIn(name, body)

    def test_each_single_unreadable_input_denies_the_all_clear(self):
        """Two ways to be unreadable, and the second is the quiet one: a file
        whose JSON parses fine but whose top level is a list. `load()` returns
        {} for both, which is indistinguishable from 'ran, found nothing'
        unless main() keeps them apart."""
        for text, label in (("{not json", "malformed JSON"),
                            ("[1, 2, 3]", "a top-level list"),
                            ('"a string"', "a top-level string"),
                            ("", "an empty file")):
            for name in VALID_INPUTS:
                with self.subTest(shape=label, input=name):
                    with tempfile.TemporaryDirectory() as tmp:
                        testing = self.make_testing(tmp, corrupt={name: text})
                        body = self.run_main(testing)
                    self.assert_not_an_all_clear(
                        headline(body),
                        f"{name} held {label} and the headline read clean")
                    self.assertIn("unreadable", body)
                    self.assertIn(name, body)

    # -- state 3c: partial runs, and what outranks what --------------------

    def test_gates_all_passing_with_findings_missing_is_not_an_all_clear(self):
        """The partial case that is easiest to get wrong: everything that ran
        came back green, so every branch of the old verdict_line fell through
        to the clean headline -- while the one input that finds bugs was the
        one that never ran."""
        with tempfile.TemporaryDirectory() as tmp:
            body = self.run_main(self.make_testing(tmp, drop=("findings.json",)))
        line = headline(body)
        self.assert_not_an_all_clear(line)
        self.assertIn("did run found nothing", line)
        self.assertIn("1 input(s)", line)

    def test_a_real_problem_still_leads_but_the_gap_is_still_flagged(self):
        """Precedence: a true, actionable problem keeps the headline, because
        burying an S1 behind a bookkeeping notice would be its own defect. The
        incompleteness is appended, never dropped."""
        for data, lead in (
            ({"health": {"verdict": "RED"}}, "**The test suite is not healthy.**"),
            ({"findings": [{"evidence": "reproduced", "severity": "S1",
                            "title": "boom"}]}, "**S1: boom**"),
            ({"gates": [{"name": "coverage", "met": False}]},
             "**1 quality gate(s) not met:**"),
        ):
            with self.subTest(lead=lead):
                blob = dict(data, missing_inputs=["findings.json (run x)",
                                                  "gates.json (run y)"])
                line = headline(prc.render(blob))
                self.assertTrue(line.startswith(lead),
                                f"the real problem lost the headline: {line!r}")
                self.assertIn("Not everything ran: 2 input(s)", line,
                              "the incomplete run was not flagged at all")

    def test_a_partial_run_with_non_blocking_findings_says_both(self):
        line = headline(prc.render({
            "findings": [{"evidence": "observed", "severity": "S3",
                          "title": "meh"}],
            "missing_inputs": ["gates.json (run /testsquad:verify)"]}))
        self.assert_not_an_all_clear(line)
        self.assertIn("findings worth a look below", line)

    def test_a_missing_input_given_as_a_bare_string_still_counts(self):
        """A hand-written data file spells a one-item list as a string. The
        headline must not fall back to the all-clear because of a type."""
        self.assert_not_an_all_clear(
            headline(prc.render({"missing_inputs": "gates.json"})))

    def test_the_headline_and_the_body_never_disagree(self):
        """The defect in one sentence. Whenever the body carries the "Not
        everything ran" notice, the headline must not read as an all-clear --
        and whenever it does not, the clean headline is allowed."""
        cases = [
            {},
            {"missing_inputs": ["a"]},
            {"missing_inputs": ["a", "b"], "health": {"verdict": "GREEN"}},
            {"missing_inputs": ["a"], "gates": [{"name": "g", "met": True}]},
            {"missing_inputs": ["a"], "gates": [{"name": "g", "met": False}]},
            {"missing_inputs": ["a"], "findings": [
                {"evidence": "reproduced", "severity": "S2", "title": "t"}]},
            {"health": {"verdict": "GREEN"}},
        ]
        for data in cases:
            with self.subTest(data=data):
                body = prc.render(data)
                if "Not everything ran." in body.split("## testsquad")[1]:
                    self.assert_not_an_all_clear(headline(body))
                else:
                    self.assertNotIn("Not everything ran", headline(body))

    # -- the --out writer --------------------------------------------------

    def test_an_unwritable_out_path_exits_2_rather_than_raising(self):
        """`.testing/reports/` is what the skill passes and nothing guarantees
        it exists. Every other --out writer in scripts/ answers OSError with a
        message and exit 2 (risk_map.py, env_safety_check.py); this one let
        FileNotFoundError out as a traceback and exited 1."""
        with tempfile.TemporaryDirectory() as tmp:
            testing = self.make_testing(tmp)
            for label, out in (
                ("a parent directory that does not exist",
                 os.path.join(tmp, "reports", "pr-comment.md")),
                ("a path that is a directory", tmp),
            ):
                with self.subTest(case=label):
                    self.assertEqual(2, prc.main(
                        ["--testing-dir", testing, "--out", out]))

    def test_a_writable_out_path_still_exits_0_and_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            testing = self.make_testing(tmp)
            out = os.path.join(tmp, "pr-comment.md")
            self.assertEqual(0, prc.main(
                ["--testing-dir", testing, "--out", out]))
            with open(out, encoding="utf-8") as fh:
                self.assertIn(prc.MARKER, fh.read())


if __name__ == "__main__":
    unittest.main()
