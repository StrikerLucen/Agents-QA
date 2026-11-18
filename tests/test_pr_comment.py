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

import contextlib
import io
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
        """The worst possible output: every file present but unusable, and a
        comment that says 'no new findings' with nothing to indicate why.

        It renders no comment at all now, and exits 2. The body was already
        honest -- "Nothing was measured on this change." -- but the exit code
        said success, and `skills/report/SKILL.md` runs this from a shell
        step where that is the half a CI job reads.
        """
        with tempfile.TemporaryDirectory() as tmp:
            testing = os.path.join(tmp, ".testing")
            os.makedirs(testing)
            for name in ("health-baseline.json", "coverage-diff.json",
                         "gates.json", "findings.json"):
                with open(os.path.join(testing, name), "w", encoding="utf-8") as fh:
                    fh.write("[]")
            out = os.path.join(tmp, "c.md")
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                self.assertEqual(2, prc.main(
                    ["--testing-dir", testing, "--out", out]))
            self.assertFalse(os.path.exists(out),
                             "a refusal must not leave a comment behind")
        message = err.getvalue()
        self.assertIn("no usable testsquad data", message)
        for name in ("health-baseline.json", "coverage-diff.json",
                     "gates.json", "findings.json"):
            self.assertIn(f"{name} (not a JSON object", message,
                          "the refusal has to name every file and say what "
                          "was wrong with it")

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

    def test_every_input_present_but_unreadable_is_a_refusal_not_a_comment(self):
        """The worst case the script's own comment names: four files on disk,
        none of them parseable, and -- before the first fix -- a comment
        reading "No new findings on this change." with no notice of any kind.

        Then it rendered an honest "Nothing was measured on this change." and
        **exited 0**, while the same four inputs merely *absent* exited 2.
        Same measurement, two answers, and the exit code is the half a CI
        step can branch on. Both are a refusal now, keyed on the count of
        inputs that produced something rather than on what was on disk.
        """
        with tempfile.TemporaryDirectory() as tmp:
            testing = self.make_testing(tmp, corrupt={
                name: "{not json" for name in VALID_INPUTS})
            out = os.path.join(tmp, "pr-comment.md")
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                self.assertEqual(2, prc.main(
                    ["--testing-dir", testing, "--out", out]))
            self.assertFalse(os.path.exists(out))
        message = err.getvalue()
        self.assertIn("no usable testsquad data", message)
        for name in VALID_INPUTS:
            self.assertIn(name, message, "the unusable file must be named")
            self.assertIn("rerun", message, "and its remedy with it")

    def test_corrupt_and_absent_inputs_reach_the_same_exit_code(self):
        """F7 stated as the property rather than the case: four inputs that
        measured nothing exit the same way whether the files were missing or
        merely unusable. The sentences differ -- the remedies differ -- but
        the code a CI job branches on must not."""
        with tempfile.TemporaryDirectory() as tmp:
            corrupt = self.make_testing(tmp, corrupt={
                name: "{not json" for name in VALID_INPUTS})
            absent = os.path.join(tmp, "empty-testing")
            os.makedirs(absent)
            errs = []
            for testing in (corrupt, absent):
                err = io.StringIO()
                with contextlib.redirect_stderr(err):
                    self.assertEqual(2, prc.main(["--testing-dir", testing]))
                errs.append(err.getvalue())
        self.assertIn("not valid JSON", errs[0])
        self.assertNotIn("not valid JSON", errs[1])
        self.assertIn("(run /testsquad:health)", errs[1],
                      "an absent file is named with the command that writes "
                      "it, not with a complaint about the file")

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
        for text, label, phrase in (
                ("{not json", "malformed JSON", "not valid JSON"),
                ("[1, 2, 3]", "a top-level list", "not a JSON object"),
                ('"a string"', "a top-level string", "not a JSON object"),
                ("", "an empty file", "not valid JSON")):
            for name in VALID_INPUTS:
                with self.subTest(shape=label, input=name):
                    with tempfile.TemporaryDirectory() as tmp:
                        testing = self.make_testing(tmp, corrupt={name: text})
                        body = self.run_main(testing)
                    self.assert_not_an_all_clear(
                        headline(body),
                        f"{name} held {label} and the headline read clean")
                    # Named on the bullet, not just in the boilerplate
                    # sentence above it -- which is what this assertion used
                    # to match, so it would have passed on any wording.
                    self.assertIn(f"> - {name} ({phrase} — rerun", body)

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


class AReadableInputMissingItsKeyIsNotAnEmptyResult(unittest.TestCase):
    """The residual of the headline defect, one notch subtler: an input that
    parses perfectly and still is not the input this comment reads.

    The product decision this class pins, because guessing it wrong costs a
    false positive on every clean run:

      * `{"gates": []}` is a **legitimate empty result** -- gates ran, and
        none are defined or none failed. It stays silent. A notice that fires
        on every clean run is a notice nobody reads, and it would take the
        real one with it.
      * `{"name": "coverage"}` has no `gates` key at all, and `{"gates": null}`
        has no value under it. The producer wrote something, but not what this
        consumer reads. That is a **contract mismatch, not an empty result**,
        so it is surfaced exactly the way an unreadable input is and does not
        count as an input that ran.

    Before this, `{"name": "coverage"}` rendered "No new findings on this
    change." with nothing anywhere in the body to say the gate section was
    rendered from a file that never mentioned gates.
    """

    GATES_ABSENT = ('> - gates.json (readable, but its "gates" key is absent '
                    "— rerun /testsquad:verify)")
    GATES_NULL = ('> - gates.json (readable, but its "gates" key is null '
                  "— rerun /testsquad:verify)")
    FINDINGS_ABSENT = ('> - findings.json (readable, but its "findings" key is '
                       "absent — rerun /testsquad:test or :debug)")

    def body_for(self, overrides, drop=(), utf16=()):
        """The rendered comment for a .testing/ of the four real inputs, with
        `overrides` replacing some of them, `drop` leaving some out, and
        `utf16` writing some as UTF-16 — valid JSON in bytes this reader
        cannot decode, which is "unreadable" without needing a permission
        bit, and so reads the same for root and for everyone else."""
        with tempfile.TemporaryDirectory() as tmp:
            testing = os.path.join(tmp, ".testing")
            os.makedirs(testing)
            blobs = dict(VALID_INPUTS)
            blobs.update(overrides)
            for name, blob in blobs.items():
                if name in drop:
                    continue
                path = os.path.join(testing, name)
                if name in utf16:
                    with open(path, "wb") as fh:
                        fh.write(json.dumps(blob).encode("utf-16"))
                    continue
                with open(path, "w", encoding="utf-8") as fh:
                    if isinstance(blob, str):
                        fh.write(blob)
                    else:
                        json.dump(blob, fh)
            out = os.path.join(tmp, "pr-comment.md")
            self.assertEqual(0, prc.main(
                ["--testing-dir", testing, "--out", out]),
                "a comment that can still be rendered must exit 0")
            with open(out, encoding="utf-8") as fh:
                return fh.read()

    # -- the regression that matters most: a clean run stays clean ----------

    def test_an_explicitly_empty_gates_list_keeps_the_clean_headline(self):
        """`{"gates": []}` is the shape `/testsquad:verify` writes for a
        contract with nothing failing. If this test ever goes red, the fix has
        become a false positive on every clean run -- which is worse than the
        defect it closes."""
        body = self.body_for({"gates.json": {"gates": []}})
        self.assertEqual(CLEAN_HEADLINE, headline(body))
        self.assertNotIn("Not everything ran", body)
        self.assertNotIn(MALFORMED_NOTICE, body)
        self.assertNotIn("gates.json", body)

    def test_an_explicitly_empty_findings_list_keeps_the_clean_headline(self):
        body = self.body_for({"findings.json": {"findings": []}})
        self.assertEqual(CLEAN_HEADLINE, headline(body))
        self.assertNotIn("Not everything ran", body)
        self.assertNotIn("findings.json", body)

    def test_both_inputs_explicitly_empty_together_keep_the_clean_headline(self):
        body = self.body_for({"gates.json": {"gates": []},
                              "findings.json": {"findings": []}})
        self.assertEqual(CLEAN_HEADLINE, headline(body))
        self.assertNotIn("Not everything ran", body)
        self.assertNotIn(MALFORMED_NOTICE, body)

    def test_an_empty_gate_list_beside_other_data_is_still_silent(self):
        """The empty list must stay silent even when the rest of the run has
        something to say, so the notice cannot be blamed on it."""
        body = self.body_for({
            "gates.json": {"gates": []},
            "findings.json": {"findings": [
                {"evidence": "reproduced", "severity": "S3", "title": "meh"}]}})
        self.assertEqual("No blocking issues. Some findings worth a look below.",
                         headline(body))
        self.assertNotIn("Not everything ran", body)

    # -- the new state: readable, but not this consumer's input ------------

    def test_gates_json_without_its_key_is_surfaced_not_counted_as_empty(self):
        """The reported case verbatim: `{"name": "coverage"}` on disk."""
        body = self.body_for({"gates.json": {"name": "coverage"}})
        self.assertNotIn("No new findings", headline(body))
        self.assertIn("Not everything ran", body)
        self.assertIn(self.GATES_ABSENT, body)

    def test_findings_json_without_its_key_is_surfaced(self):
        body = self.body_for({"findings.json": {"count": 0}})
        self.assertNotIn("No new findings", headline(body))
        self.assertIn(self.FINDINGS_ABSENT, body)

    def test_a_wholly_empty_object_file_is_surfaced(self):
        """`{}` says nothing about gates at all, so it is not the empty result
        `{"gates": []}` is -- it is a file that never carried the key."""
        body = self.body_for({"gates.json": {}})
        self.assertIn(self.GATES_ABSENT, body)
        self.assertNotIn("No new findings", headline(body))

    def test_a_null_key_is_surfaced_and_says_null_not_absent(self):
        """`null` is "not measured", never an empty result -- the rule the
        coverage chain already follows. The notice says which it was."""
        body = self.body_for({"gates.json": {"gates": None}})
        self.assertIn(self.GATES_NULL, body)
        self.assertNotIn(self.GATES_ABSENT, body)

    def test_the_headline_counts_it_as_one_input_that_did_not_run(self):
        body = self.body_for({"gates.json": {"name": "coverage"}})
        self.assertIn("1 input(s)", headline(body))
        self.assertIn("The checks that did run found nothing", headline(body))

    def test_a_blocking_finding_still_leads_but_the_gap_is_still_flagged(self):
        """Precedence is unchanged: a real problem keeps the headline and the
        incompleteness is appended, never dropped."""
        body = self.body_for({
            "gates.json": {"name": "coverage"},
            "findings.json": {"findings": [
                {"evidence": "reproduced", "severity": "S1", "title": "boom"}]}})
        line = headline(body)
        self.assertTrue(line.startswith("**S1: boom**"), line)
        self.assertIn("Not everything ran: 1 input(s)", line)

    # -- the three per-input states must read differently ------------------

    def test_absent_unreadable_and_wrong_shape_are_four_different_lines(self):
        """The house rule applied per input: "it is not there", "I could not
        read a byte of it", "I read it and it is not JSON" and "I read it and
        it does not carry the key" must never render as the same sentence.

        All four at once means nothing was measured, so this reads them off
        the refusal rather than off a comment — the same sentences either
        way, because `describe()` produces both. The rendered-bullet form of
        each is asserted by the tests above and below this one.

        `health-baseline.json` is written as UTF-16 for the unreadable case:
        bytes this reader cannot turn into text at all, which needs no
        permission bit and so behaves the same for every user running it.
        """
        with tempfile.TemporaryDirectory() as tmp:
            testing = os.path.join(tmp, ".testing")
            os.makedirs(testing)
            with open(os.path.join(testing, "health-baseline.json"), "wb") as fh:
                fh.write(json.dumps({"verdict": "GREEN"}).encode("utf-16"))
            with open(os.path.join(testing, "coverage-diff.json"), "w",
                      encoding="utf-8") as fh:
                fh.write("{not json")
            with open(os.path.join(testing, "gates.json"), "w",
                      encoding="utf-8") as fh:
                json.dump({"name": "coverage"}, fh)
            # findings.json is simply not written.
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                self.assertEqual(2, prc.main(["--testing-dir", testing]))
        message = err.getvalue()
        self.assertIn("findings.json (run /testsquad:test or :debug)", message)
        self.assertIn("health-baseline.json (unreadable — rerun "
                      "/testsquad:health)", message)
        self.assertIn("coverage-diff.json (not valid JSON — rerun "
                      "/testsquad:diagnose)", message)
        self.assertIn(self.GATES_ABSENT.replace("> - ", ""), message)
        self.assertNotIn("gates.json (unreadable", message)
        self.assertNotIn("health-baseline.json (not valid JSON", message)

    def test_an_unreadable_input_beside_a_usable_one_is_named_in_the_body(self):
        """The same sentence in the surface that gets published, so the
        refusal and the comment cannot drift apart."""
        body = self.body_for({}, utf16=("health-baseline.json",))
        self.assertIn("> - health-baseline.json (unreadable — rerun "
                      "/testsquad:health)", body)
        self.assertNotIn("No new findings", headline(body))

    def test_a_file_that_is_a_json_list_says_so_rather_than_unreadable(self):
        """The fifth state. `[]` in gates.json read perfectly well; telling
        its author it was unreadable sent them to look for corruption in a
        file their editor would format without complaint."""
        body = self.body_for({"gates.json": "[]"})
        self.assertIn("> - gates.json (not a JSON object — rerun "
                      "/testsquad:verify)", body)
        self.assertNotIn("gates.json (unreadable", body)

    def test_nothing_usable_at_all_is_a_refusal(self):
        """Two files that parse and carry no key, and nothing else on disk: a
        run where every input was readable and none of it was usable.

        It used to render the honest "Nothing was measured on this change."
        and exit 0. The body was never the problem; the exit code was. Zero
        inputs measured is zero inputs measured however the files failed.
        """
        with tempfile.TemporaryDirectory() as tmp:
            testing = os.path.join(tmp, ".testing")
            os.makedirs(testing)
            for name, blob in (("gates.json", {"name": "coverage"}),
                               ("findings.json", {"count": 0})):
                with open(os.path.join(testing, name), "w",
                          encoding="utf-8") as fh:
                    json.dump(blob, fh)
            out = os.path.join(tmp, "c.md")
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                self.assertEqual(2, prc.main(
                    ["--testing-dir", testing, "--out", out]))
            self.assertFalse(os.path.exists(out))
        message = err.getvalue()
        self.assertIn("no usable testsquad data", message)
        self.assertIn('gates.json (readable, but its "gates" key is absent',
                      message)

    # -- the two files that *are* the object, and have no key to miss -------

    def test_a_health_baseline_with_no_verdict_is_surfaced(self):
        """F5, reported verbatim: a plausible `health-baseline.json` whose
        verdict sits one level down under its own `health` wrapper. Every
        key-presence check passed, because the whole file is assigned to
        `health` — so a RED suite rendered "No new findings on this change."
        under a full coverage table."""
        body = self.body_for({"health-baseline.json": {
            "health": {"verdict": "RED"}, "commit": "abc"}})
        self.assertNotIn("No new findings", headline(body))
        self.assertIn('> - health-baseline.json (readable, but it records no '
                      '"verdict" — rerun /testsquad:health)', body)

    def test_a_coverage_diff_with_no_coverage_figure_is_surfaced(self):
        """The same hole in the other unwrapped file. `{"files": [...]}` is
        a coverage document; it is not the one this section reads."""
        body = self.body_for({"coverage-diff.json": {
            "files": [{"path": "a.py"}], "generated_at": "2026-11-16"}})
        self.assertNotIn("No new findings", headline(body))
        self.assertIn("> - coverage-diff.json (readable, but it records no "
                      "coverage figure", body)
        # The section still renders, and every cell in it reads "not
        # measured" -- no number is invented from a file that holds none.
        # What changed is that the reader is now told which file that was.
        self.assertNotIn("0.0%", body)
        self.assertIn("not measured", body)

    def test_a_health_baseline_carrying_only_its_verdict_stays_silent(self):
        """The false-positive boundary, and the reason the required key is
        `verdict` and nothing else: the one field this renderer acts on is
        enough. A baseline with no commit, no counts and no timestamp is
        still a verdict this comment can publish."""
        body = self.body_for({"health-baseline.json": {"verdict": "GREEN"}})
        self.assertEqual(CLEAN_HEADLINE, headline(body))
        self.assertNotIn("health-baseline.json", body)

    def test_a_coverage_figure_of_null_still_counts_as_having_run(self):
        """Presence, never truthiness — the rule the whole coverage chain
        follows. `null` is a measurement that was not taken, which renders
        "not measured"; it is not a file that never ran."""
        body = self.body_for({"coverage-diff.json": {
            "line_pct": None, "base_line_pct": None, "branch_pct": None,
            "base_branch_pct": None}})
        self.assertEqual(CLEAN_HEADLINE, headline(body))
        self.assertNotIn("coverage-diff.json", body)
        self.assertIn("not measured", body)

    def test_a_key_of_the_wrong_type_keeps_its_existing_malformed_notice(self):
        """The boundary of the change: `{"gates": "none"}` has the key, so it
        counts as an input that ran and degrades through the malformed-notice
        channel it already used. Routing it through the missing-input channel
        as well would report one fault twice."""
        body = self.body_for({"gates.json": {"gates": "none"}})
        self.assertIn("`gates` was not a list", body)
        self.assertNotIn(self.GATES_ABSENT, body)
        self.assertNotIn("Not everything ran", body)


class TheDataRefusalSaysWhichFailureItWas(unittest.TestCase):
    """`--data` had one sentence for three different facts.

    A file holding a valid but empty `{}` exited 2 saying "could not read" --
    the file read fine, it was empty. Refusing is right (an empty comment is
    not an all-clear), so the exit code stays 2 for every shape below; what
    changes is that the sentence no longer sends the reader to look for a
    permissions or JSON problem that is not there.
    """

    def refuse(self, text, expect_rc=2):
        """`(rc, stderr)` for a --data file holding exactly `text`."""
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "d.json")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(text)
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                rc = prc.main(["--data", path])
            self.assertEqual(expect_rc, rc)
            return err.getvalue().strip(), path

    def refuse_bytes(self, blob, expect_rc=2):
        """`(stderr, path)` for a --data file holding exactly these bytes."""
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "d.json")
            with open(path, "wb") as fh:
                fh.write(blob)
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                self.assertEqual(expect_rc, prc.main(["--data", path]))
            return err.getvalue().strip(), path

    def test_a_file_that_is_not_valid_json_says_that_and_not_unreadable(self):
        """It read fine. Calling it unreadable sent its author looking for a
        permissions problem that was not there, which is the same conflation
        one layer up -- and the one this file's own comment had already
        written down while the code still made it."""
        message, path = self.refuse("{not json")
        self.assertEqual(
            f"error: read {path}, but it is not valid JSON — nothing could "
            "be parsed out of it. Check it with `python3 -m json.tool`.",
            message)
        self.assertNotIn("could not read", message)

    def test_a_file_that_is_not_there_says_so_rather_than_blaming_the_read(self):
        """The other half of the same conflation: an absent file sent the
        reader to look for a corruption that does not exist either. The
        remedy is to produce the file, and the sentence has to say so."""
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "nope.json")
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                self.assertEqual(2, prc.main(["--data", path]))
            self.assertEqual(
                f"error: no such file: {path} — there is nothing to read, "
                "and a comment rendered from nothing is not an all-clear.",
                err.getvalue().strip())
            self.assertNotIn("could not read", err.getvalue())

    def test_a_file_whose_bytes_are_not_text_is_the_one_called_unreadable(self):
        """UTF-16: valid JSON, and not bytes this reader can decode. This is
        what "could not read it" is reserved for now."""
        message, path = self.refuse_bytes(
            json.dumps({"gates": []}).encode("utf-16"))
        self.assertEqual(
            f"error: could not read {path} — the file is there, but its "
            "bytes could not be read as text (permissions, a directory, a "
            "non-UTF-8 encoding, or an I/O error).", message)

    @unittest.skipIf(os.geteuid() == 0, "root can read anything")
    def test_a_file_this_process_may_not_open_is_unreadable_too(self):
        """The canonical case, kept beside the UTF-16 one because they are
        the same fact about the world and must read the same."""
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "d.json")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write('{"gates": []}')
            os.chmod(path, 0o000)
            # chmod back inside the temporary directory's lifetime, or a
            # 0o000 fixture defeats the cleanup and leaks the tree.
            try:
                err = io.StringIO()
                with contextlib.redirect_stderr(err):
                    self.assertEqual(2, prc.main(["--data", path]))
            finally:
                os.chmod(path, 0o600)
        self.assertIn("could not read", err.getvalue())
        self.assertNotIn("not valid JSON", err.getvalue())

    def test_an_empty_object_says_it_was_read_and_is_empty(self):
        message, path = self.refuse("{}")
        self.assertEqual(
            f"error: read {path}, but it is empty — there is nothing to "
            "report, and an empty comment is not an all-clear.", message)
        self.assertNotIn("could not read", message)

    def test_a_non_object_says_it_was_read_and_is_the_wrong_shape(self):
        for text in ("[1, 2, 3]", '"a string"', "42", "true", "null"):
            with self.subTest(text=text):
                message, path = self.refuse(text)
                self.assertEqual(
                    f"error: read {path}, but it is not a JSON object — "
                    "expected an object with health/coverage/gates/findings "
                    "keys.", message)
                self.assertNotIn("could not read", message)

    def test_an_object_with_none_of_the_keys_names_what_it_found(self):
        """The `--data` face of the same contract mismatch: an object that is
        readable, not empty, and not this tool's data. Every section would be
        blank and the headline would read as an all-clear."""
        message, path = self.refuse('{"name": "coverage", "version": 2}')
        self.assertEqual(
            f"error: read {path}, but it has none of the keys testsquad reads "
            "— expected health/coverage/gates/findings, found: name, version.",
            message)
        self.assertNotIn("could not read", message)

    def test_the_six_refusals_are_six_different_sentences(self):
        """The rule stated as a test: absence, silence, malformation,
        emptiness, the wrong shape and somebody else's data must never render
        the same string. The first three were one sentence between them."""
        messages = []
        for text in ("{not json", "{}", "[1, 2, 3]", '{"a": 1}'):
            message, path = self.refuse(text)
            # The path differs per temporary directory and is not the part
            # under test; the sentence around it is.
            messages.append(message.replace(path, "<path>"))
        unreadable, path = self.refuse_bytes("{}".encode("utf-16"))
        messages.append(unreadable.replace(path, "<path>"))
        with tempfile.TemporaryDirectory() as tmp:
            gone = os.path.join(tmp, "nope.json")
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                self.assertEqual(2, prc.main(["--data", gone]))
            messages.append(err.getvalue().strip().replace(gone, "<path>"))
        self.assertEqual(6, len(set(messages)), messages)
        self.assertEqual(1, sum("could not read" in m for m in messages),
                         "only the unreadable file may be called unreadable")
        self.assertEqual(1, sum("no such file" in m for m in messages),
                         "only the absent file may be called absent")
        self.assertEqual(1, sum("not valid JSON" in m for m in messages),
                         "only the unparseable file may be called malformed")

    def test_every_refusal_is_still_exit_2(self):
        """`tests/test_robustness.py` asserts this path for adjacent shapes,
        and refusing beats reassuring -- the exit code was never the bug."""
        for text in ("{not json", "", "{}", "[1, 2, 3]", '"s"',
                     '{"name": "coverage"}'):
            with self.subTest(text=text):
                with tempfile.TemporaryDirectory() as tmp:
                    path = os.path.join(tmp, "d.json")
                    with open(path, "w", encoding="utf-8") as fh:
                        fh.write(text)
                    with contextlib.redirect_stderr(io.StringIO()):
                        self.assertEqual(2, prc.main(["--data", path]))

    def test_a_data_file_of_explicitly_empty_results_still_renders_clean(self):
        """The false-positive guard for the refusal above: `{"gates": [],
        "findings": []}` is a real, complete, empty result. It must render the
        unchanged clean headline, not a refusal.

        `coverage` was added to this blob when `--data` started classifying
        each input the way `--testing-dir` already did. Without it the file
        supplies three inputs out of four, which is a genuinely partial run
        and now says so -- this test is about the *empty lists* being silent,
        so it has to be complete in every other respect or it is pinning the
        wrong thing. The partial case has its own test below.
        """
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "d.json")
            with open(path, "w", encoding="utf-8") as fh:
                json.dump({"gates": [], "findings": [],
                           "coverage": {"line_pct": 81.0},
                           "health": {"verdict": "GREEN"}}, fh)
            out = os.path.join(tmp, "c.md")
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(0, prc.main(["--data", path, "--out", out]))
            with open(out, encoding="utf-8") as fh:
                body = fh.read()
        self.assertEqual(CLEAN_HEADLINE, headline(body))
        self.assertNotIn("Not everything ran", body)
        self.assertNotIn(MALFORMED_NOTICE, body)

    def test_a_data_file_with_only_one_section_still_renders(self):
        """One recognised key is enough. Refusing a partial but genuine data
        file would be the false positive this check must not become."""
        for blob in ({"health": {"verdict": "RED"}},
                     {"coverage": {"line_pct": 81.0}},
                     {"missing_inputs": ["gates.json (run /testsquad:verify)"]},
                     {"pre_existing_count": 3}):
            with self.subTest(blob=blob):
                with tempfile.TemporaryDirectory() as tmp:
                    path = os.path.join(tmp, "d.json")
                    with open(path, "w", encoding="utf-8") as fh:
                        json.dump(blob, fh)
                    out = os.path.join(tmp, "c.md")
                    with contextlib.redirect_stderr(io.StringIO()):
                        self.assertEqual(0, prc.main(
                            ["--data", path, "--out", out]))
                    with open(out, encoding="utf-8") as fh:
                        self.assertIn(prc.MARKER, fh.read())


COMPLETE_DATA = {
    "health": {"verdict": "GREEN"},
    "coverage": {"line_pct": 81.0, "base_line_pct": 80.0},
    "gates": [],
    "findings": [],
}


class BothModesAgreeOnWhatCountsAsHavingRun(unittest.TestCase):
    """`--data` applying a weaker rule than `--testing-dir` was the defect.

    Directory mode classifies each of the four inputs and refuses to call an
    unsupplied one "ran and found nothing". The `--data` branch checked only
    that the blob was an object holding *at least one* recognised key, so
    `{"health": {...}}` -- one input out of four -- rendered the bold
    "No new findings on this change." with nothing anywhere in the body to
    say that gates, coverage and findings had never been supplied.

    The product decisions this class pins:

      * **The definition is shared.** One `input_outcome()`, called by both
        branches. A second copy in the `--data` branch is how the
        atomic-write promise, the fixture, the step numbering and an
        evidence-level rule all broke in this repo: two copies of a rule
        drift apart, and the one nobody is looking at is the one that rots.
      * **The wording differs, on purpose.** The remedies are not the same.
        Directory mode sends the reader to a file on disk; `--data` sends
        them to a key their assembler did not put in the blob.
      * **A partial `--data` file is not a refusal.** It renders, and exits
        0, exactly as before. A blob is hand- or CI-assembled, so a key it
        does not carry legitimately means "this consumer supplied part of
        the picture" -- worth saying, not worth discarding the comment for.
        Exit 2 stays for a file unusable as a whole.
      * **A present key has run, whatever it holds.** `{"gates": []}` is the
        clean run and stays silent. That boundary is tested harder than the
        new behaviour below it, because a notice that fires on every clean
        run is worse than the bug it closes.
    """

    def run_data(self, blob, expect_rc=0):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "pr.json")
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(blob, fh)
            out = os.path.join(tmp, "c.md")
            with contextlib.redirect_stderr(io.StringIO()):
                rc = prc.main(["--data", path, "--out", out])
            self.assertEqual(expect_rc, rc)
            if rc != 0:
                return None
            with open(out, encoding="utf-8") as fh:
                return fh.read()

    def without(self, key):
        blob = dict(COMPLETE_DATA)
        del blob[key]
        return blob

    # -- the boundary that must not move: a complete, empty run is silent --

    def test_a_complete_data_file_of_empty_results_keeps_the_clean_headline(self):
        """All four inputs supplied, two of them explicitly empty. This is the
        overwhelmingly common clean run. If it ever goes red, the fix has
        become a false positive on every clean run, which costs more than the
        defect it closes."""
        body = self.run_data(COMPLETE_DATA)
        self.assertEqual(CLEAN_HEADLINE, headline(body))
        self.assertNotIn("Not everything ran", body)
        self.assertNotIn(MALFORMED_NOTICE, body)

    def test_an_explicitly_empty_list_is_never_named_as_missing(self):
        """Not just the headline: the empty inputs must not appear in the
        blockquote either, or the body contradicts a clean headline."""
        body = self.run_data(COMPLETE_DATA)
        self.assertNotIn('"gates"', body)
        self.assertNotIn('"findings"', body)
        self.assertNotIn("supply it", body)

    def test_an_empty_container_under_a_wrapper_key_counts_as_having_run(self):
        """`[]` under `gates` is the producer saying it ran and had nothing to
        report. Falsy is not absent, and this is the overwhelmingly common
        clean run."""
        body = self.run_data(dict(COMPLETE_DATA, gates=[], findings=[]))
        self.assertEqual(CLEAN_HEADLINE, headline(body))
        self.assertNotIn("Not everything ran", body)

    def test_an_empty_health_or_coverage_object_does_not_count_as_having_run(self):
        """The boundary F5 moved, and the one place "falsy is not absent"
        does not reach.

        `gates` and `findings` are *under* a key: `{"gates": []}` is a
        producer that ran. `health` and `coverage` **are** the object, so
        `{}` is not an empty result under a key — there is no key, and
        nothing the renderer reads. It prints no health line, no coverage
        table and no verdict, and before this it counted as an input that
        ran and found nothing. One `{}` of the four and the comment still
        claimed an all-clear for a check that never reported.
        """
        for key in ("health", "coverage"):
            with self.subTest(key=key):
                body = self.run_data(dict(COMPLETE_DATA, **{key: {}}))
                self.assertNotIn("No new findings", headline(body))
                self.assertIn("1 input(s)", headline(body))
                self.assertIn(f'"{key}" records no', body)

    def test_a_health_object_of_the_wrong_type_keeps_its_malformed_notice(self):
        """The boundary of the boundary: `{"health": "GREEN"}` supplied
        something. It degrades through the malformed channel it already used,
        and is not reported a second time as an input that never ran."""
        body = self.run_data(dict(COMPLETE_DATA, health="GREEN"))
        self.assertIn("`health` was not an object", body)
        self.assertNotIn('"health" records no', body)
        self.assertNotIn("Not everything ran", body)

    def test_an_empty_gate_list_stays_silent_beside_a_real_finding(self):
        """The empty list must stay silent even when the run has something to
        say, so the notice can never be blamed on it."""
        blob = dict(COMPLETE_DATA)
        blob["findings"] = [{"evidence": "reproduced", "severity": "S3",
                             "title": "meh"}]
        body = self.run_data(blob)
        self.assertEqual("No blocking issues. Some findings worth a look below.",
                         headline(body))
        self.assertNotIn("Not everything ran", body)

    def test_the_shared_rule_itself_calls_an_empty_container_supplied(self):
        """The boundary at the unit, where it is decided once for both modes."""
        for value in ([], {}, 0, "", False):
            with self.subTest(value=value):
                self.assertEqual(prc.INPUT_RAN,
                                 prc.input_outcome({"gates": value}, "gates"))
        self.assertEqual(prc.INPUT_NULL,
                         prc.input_outcome({"gates": None}, "gates"))
        self.assertEqual(prc.INPUT_ABSENT, prc.input_outcome({}, "gates"))

    # -- the gap that was open: a partial --data file claimed an all-clear --

    def test_the_reported_case_a_data_file_of_only_health(self):
        """Verbatim: a blob carrying `health` alone used to render
        "No new findings on this change." as though all four had run."""
        body = self.run_data({"health": {"verdict": "GREEN"}})
        self.assertNotIn("No new findings", headline(body))
        self.assertIn("Not everything ran", headline(body))
        self.assertIn("3 input(s)", headline(body))

    def test_each_single_absent_key_denies_the_all_clear(self):
        """One at a time, so no single key is carrying the whole result."""
        for key in ("health", "coverage", "gates", "findings"):
            with self.subTest(key=key):
                body = self.run_data(self.without(key))
                line = headline(body)
                self.assertNotIn("No new findings", line)
                self.assertIn("1 input(s)", line)
                self.assertIn(f'"{key}" is absent', body)

    def test_a_null_key_says_null_rather_than_absent(self):
        """`null` is "not measured", never an empty result -- and the two are
        different facts, so they get different sentences."""
        blob = dict(COMPLETE_DATA, gates=None)
        body = self.run_data(blob)
        self.assertIn('"gates" is null', body)
        self.assertNotIn('"gates" is absent', body)

    def test_the_notice_names_the_data_file_and_the_producing_command(self):
        """A notice the reader cannot act on is noise. It has to say which
        blob, which key, and what to run."""
        body = self.run_data(self.without("gates"))
        self.assertIn('"gates" is absent in pr.json', body)
        self.assertIn("/testsquad:verify", body)

    def test_a_partially_supplied_data_file_is_not_a_refusal(self):
        """Exit codes are unchanged. A blob with one recognised key still
        renders and still exits 0 -- turning a partial file into exit 2 would
        break every consumer that assembles part of the picture on purpose."""
        for blob in ({"health": {"verdict": "RED"}},
                     {"coverage": {"line_pct": 81.0}},
                     {"gates": []},
                     {"findings": []},
                     {"pre_existing_count": 3}):
            with self.subTest(blob=blob):
                body = self.run_data(blob, expect_rc=0)
                self.assertIn(prc.MARKER, body)

    def test_a_data_file_with_no_measured_input_says_nothing_was_measured(self):
        """`{"pre_existing_count": 3}` passes the "is this our data" check and
        measures nothing at all. It is not an empty result; it is an absence
        of results, and the three states must read differently."""
        body = self.run_data({"pre_existing_count": 3})
        self.assertIn("Nothing was measured", headline(body))
        self.assertNotIn("No new findings", headline(body))

    def test_a_blocking_finding_still_leads_and_the_gap_is_appended(self):
        """Precedence is unchanged: a real problem keeps the headline and the
        incompleteness is appended, never dropped."""
        blob = {"health": {"verdict": "GREEN"},
                "findings": [{"evidence": "reproduced", "severity": "S1",
                              "title": "boom"}]}
        line = headline(self.run_data(blob))
        self.assertTrue(line.startswith("**S1: boom**"), line)
        self.assertIn("Not everything ran: 2 input(s)", line)

    # -- the assembler's own accounting is respected, not double-counted ---

    def test_an_assembler_that_named_what_it_missed_is_not_second_guessed(self):
        """`missing_inputs` exists so a CI assembler can report what it could
        not collect. Deriving a second list on top of it would double-count
        into a headline number nobody measured."""
        blob = {"health": {"verdict": "GREEN"},
                "missing_inputs": ["coverage-diff.json (run /testsquad:diagnose)"]}
        body = self.run_data(blob)
        self.assertIn("1 input(s)", headline(body))
        self.assertIn("coverage-diff.json", body)
        self.assertNotIn('"gates" is absent', body)

    def test_an_assemblers_own_read_count_is_kept(self):
        """It knows more than this scan does -- it may have read a file that
        turned out to carry nothing."""
        blob = dict(COMPLETE_DATA, inputs_read=4)
        self.assertEqual(CLEAN_HEADLINE, headline(self.run_data(blob)))

    def test_a_junk_read_count_is_replaced_by_the_measured_one(self):
        """"four" is not a number. The count that reaches the headline is
        always one this script counted."""
        blob = {"health": {"verdict": "GREEN"}, "inputs_read": "four"}
        self.assertIn("3 input(s)", headline(self.run_data(blob)))

    # -- there is one rule, and both modes are wired to it -----------------

    def test_both_modes_route_through_the_same_classifier(self):
        """A structural assertion, because "they agree today" is not the
        property worth having -- "there is only one of them" is.

        `input_outcome()` is replaced with one that calls every input absent.
        If either branch held its own copy of the rule, that branch would go
        on rendering a clean comment while the other reported nothing
        measured, and this test would catch exactly that divergence.
        """
        real = prc.input_outcome
        self.addCleanup(setattr, prc, "input_outcome", real)
        prc.input_outcome = lambda data, key: prc.INPUT_ABSENT

        data_body = self.run_data(COMPLETE_DATA)
        self.assertIn("Nothing was measured", headline(data_body),
                      "the --data branch did not use the shared classifier")

        with tempfile.TemporaryDirectory() as tmp:
            testing = os.path.join(tmp, ".testing")
            os.makedirs(testing)
            for name, blob in VALID_INPUTS.items():
                with open(os.path.join(testing, name), "w",
                          encoding="utf-8") as fh:
                    json.dump(blob, fh)
            out = os.path.join(tmp, "c.md")
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                # Four perfectly good files, and a classifier that says none
                # of them ran: directory mode refuses, because its refusal is
                # now keyed on that same count. A branch holding its own copy
                # of the rule would render a clean comment and exit 0 here,
                # which is exactly the divergence this test exists to catch.
                self.assertEqual(2, prc.main(
                    ["--testing-dir", testing, "--out", out]),
                    "the --testing-dir branch did not use the shared "
                    "classifier")
            self.assertFalse(os.path.exists(out))
        self.assertIn("no usable testsquad data", err.getvalue())

    def test_the_same_gap_counts_the_same_in_both_modes(self):
        """One input missing is one input missing, whichever way the renderer
        was fed. The sentences differ because the remedies differ; the
        arithmetic in the headline must not."""
        data_line = headline(self.run_data(self.without("gates")))
        with tempfile.TemporaryDirectory() as tmp:
            testing = os.path.join(tmp, ".testing")
            os.makedirs(testing)
            for name, blob in VALID_INPUTS.items():
                if name == "gates.json":
                    continue
                with open(os.path.join(testing, name), "w",
                          encoding="utf-8") as fh:
                    json.dump(blob, fh)
            out = os.path.join(tmp, "c.md")
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(0, prc.main(
                    ["--testing-dir", testing, "--out", out]))
            with open(out, encoding="utf-8") as fh:
                dir_line = headline(fh.read())
        self.assertEqual(dir_line, data_line)
        self.assertIn("1 input(s)", data_line)

    def test_the_two_modes_name_different_remedies(self):
        """The deliberate difference, pinned so it cannot be flattened by
        accident: directory mode points at a file that is not on disk,
        `--data` points at a key that is not in the blob."""
        data_body = self.run_data(self.without("gates"))
        with tempfile.TemporaryDirectory() as tmp:
            testing = os.path.join(tmp, ".testing")
            os.makedirs(testing)
            for name, blob in VALID_INPUTS.items():
                if name == "gates.json":
                    continue
                with open(os.path.join(testing, name), "w",
                          encoding="utf-8") as fh:
                    json.dump(blob, fh)
            out = os.path.join(tmp, "c.md")
            with contextlib.redirect_stderr(io.StringIO()):
                prc.main(["--testing-dir", testing, "--out", out])
            with open(out, encoding="utf-8") as fh:
                dir_body = fh.read()
        self.assertIn("gates.json (run /testsquad:verify)", dir_body)
        self.assertNotIn('"gates" is absent', dir_body)
        self.assertIn('"gates" is absent in pr.json', data_body)
        self.assertNotIn("gates.json (run", data_body)

    # -- exit codes, checked rather than assumed ---------------------------

    def test_the_four_whole_file_refusals_are_still_exit_2(self):
        """Unchanged by the classification above: those are facts about the
        file, not about the inputs inside it."""
        for text in ("", "   ", "{not json", "[1, 2, 3]", "{}",
                     '{"name": "coverage"}'):
            with self.subTest(text=text):
                with tempfile.TemporaryDirectory() as tmp:
                    path = os.path.join(tmp, "d.json")
                    with open(path, "w", encoding="utf-8") as fh:
                        fh.write(text)
                    with contextlib.redirect_stderr(io.StringIO()):
                        self.assertEqual(2, prc.main(["--data", path]))

    def test_the_directory_refusal_is_keyed_on_what_was_measured(self):
        """The refusal used to be keyed on *file absence*: an empty
        `.testing/` exited 2, and four files that existed and said nothing
        exited 0 — with the same "Nothing was measured on this change." body.

        Four `{}` files are not four empty inputs. `gates.json` and
        `findings.json` never carried their wrapper key, and
        `health-baseline.json` and `coverage-diff.json` carry no field this
        comment reads. Nothing ran, by the same count the headline uses, so
        it is the same refusal as an empty directory.
        """
        with tempfile.TemporaryDirectory() as tmp:
            testing = os.path.join(tmp, ".testing")
            os.makedirs(testing)
            out = os.path.join(tmp, "c.md")
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(2, prc.main(
                    ["--testing-dir", testing, "--out", out]))
            for name in VALID_INPUTS:
                with open(os.path.join(testing, name), "w",
                          encoding="utf-8") as fh:
                    fh.write("{}")
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                self.assertEqual(2, prc.main(
                    ["--testing-dir", testing, "--out", out]))
            self.assertFalse(os.path.exists(out))
        message = err.getvalue()
        self.assertIn('gates.json (readable, but its "gates" key is absent',
                      message)
        self.assertIn('health-baseline.json (readable, but it records no '
                      '"verdict"', message)

    def test_one_usable_input_out_of_four_still_renders_and_exits_0(self):
        """The false-positive guard on the refusal above, and the thing that
        keeps it from being a wider gate than it looks: a partial run is not
        a refusal. One input that measured something is a comment worth
        publishing, with the other three named as having not run."""
        with tempfile.TemporaryDirectory() as tmp:
            testing = os.path.join(tmp, ".testing")
            os.makedirs(testing)
            for name in VALID_INPUTS:
                with open(os.path.join(testing, name), "w",
                          encoding="utf-8") as fh:
                    fh.write("{}" if name != "findings.json"
                             else json.dumps(VALID_INPUTS[name]))
            out = os.path.join(tmp, "c.md")
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(0, prc.main(
                    ["--testing-dir", testing, "--out", out]))
            with open(out, encoding="utf-8") as fh:
                body = fh.read()
        self.assertIn(prc.MARKER, body)
        self.assertIn("3 input(s)", headline(body))
        self.assertNotIn("No new findings", headline(body))


if __name__ == "__main__":
    unittest.main()
