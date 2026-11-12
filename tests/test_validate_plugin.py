#!/usr/bin/env python3
"""Tests for validate_plugin.py -- specifically, for its refusal to go quiet.

A validator that dies on component 3 of 26 has told you nothing about
components 4 to 26, and the only thing worse is a validator that returns
cleanly while having told you nothing. Every case below is a component the
validator cannot read or cannot understand; in every one of them the run must
still reach every other component, and the component it could not judge must
be reported as *unjudged*, which is a different thing from *valid*.

The malformed fixtures here were found by fuzzing the validator with bad
YAML/frontmatter, missing keys, wrong types, unreadable files, non-UTF8 bytes,
empty files, directories in place of files and symlinks -- not taken from a
handoff note. Each one raised an exception out of the check it belonged to.
"""

import json
import os
import shutil
import stat
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import validate_plugin as vp  # noqa: E402


BODY = "x " * 300  # comfortably over MIN_BODY_BYTES, so a good component is clean


def good_agent(name: str) -> str:
    return (f"---\nname: {name}\ndescription: >\n  does one thing, well\n"
            f"tools: Read, Grep\nmodel: sonnet\n---\n\n{BODY}\n")


def good_skill(name: str) -> str:
    return (f"---\nname: {name}\ndescription: >\n  does one thing, well\n"
            f"---\n\n{BODY}\n")


class PluginTreeMixin:
    """A minimal but *valid* plugin, so any finding is the injected one.

    Three agents and two skills, in sorted order, so a test can inject a bad
    component in the middle and assert that the ones after it were reached.
    """

    AGENTS = ("alpha", "bravo", "charlie")
    SKILLS = ("one", "two")

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="vp_test_")
        self.addCleanup(self._cleanup)
        os.makedirs(self.path(".claude-plugin"))
        os.makedirs(self.path("agents"))
        os.makedirs(self.path("skills"))
        self.write_json(".claude-plugin/plugin.json",
                        {"name": "demo", "version": "1.0.0",
                         "description": "a demo", "author": {"name": "someone"}})
        self.write_json(".claude-plugin/marketplace.json",
                        {"name": "demo-market", "owner": {"name": "someone"},
                         "plugins": [{"name": "demo", "source": "."}]})
        for name in self.AGENTS:
            self.write(f"agents/{name}.md", good_agent(name))
        for name in self.SKILLS:
            os.makedirs(self.path(f"skills/{name}"))
            self.write(f"skills/{name}/SKILL.md", good_skill(name))

    def _cleanup(self):
        # chmod back, or a 0o000 fixture defeats rmtree and leaks the tree.
        for root, dirs, files in os.walk(self.tmp):
            for entry in dirs + files:
                try:
                    os.chmod(os.path.join(root, entry), stat.S_IRWXU)
                except OSError:
                    pass
        shutil.rmtree(self.tmp, ignore_errors=True)

    def path(self, rel: str) -> str:
        return os.path.join(self.tmp, *rel.split("/"))

    def write(self, rel: str, data) -> str:
        target = self.path(rel)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        mode = "wb" if isinstance(data, bytes) else "w"
        with open(target, mode) as fh:
            fh.write(data)
        return target

    def write_json(self, rel: str, obj) -> str:
        return self.write(rel, json.dumps(obj))

    def replace_with_dir(self, rel: str) -> str:
        """A directory where a component file should be: the shape a stray
        `mkdir` leaves behind, and one of the five things that raised."""
        target = self.path(rel)
        if os.path.exists(target):
            os.remove(target)
        os.makedirs(target)
        return target

    # -- assertions -------------------------------------------------------

    def assertBaselineIsClean(self):
        report = vp.validate(self.tmp)
        self.assertEqual(report.errors, [])
        self.assertEqual(report.warnings, [])

    def assertSurvivors(self, report, *names):
        """Every named component must still have a verdict of its own."""
        for name in names:
            offenders = [m for m in report.errors + report.unvalidated
                         if name in m]
            self.assertEqual(offenders, [],
                             f"{name} lost its verdict to another component")

    def assertUnvalidated(self, report, needle):
        hits = [m for m in report.unvalidated if needle in m]
        self.assertTrue(hits,
                        f"{needle!r} was not reported as unvalidatable; "
                        f"unvalidated={report.unvalidated!r} "
                        f"errors={report.errors!r}")
        return hits


class BaselineTest(PluginTreeMixin, unittest.TestCase):
    def test_the_fixture_plugin_validates_clean(self):
        # Without this, every test below could pass against a tree that was
        # failing for an unrelated reason.
        self.assertBaselineIsClean()

    def test_a_clean_run_says_nothing_could_not_be_validated(self):
        report = vp.validate(self.tmp)
        self.assertEqual(report.unvalidated, [])
        self.assertEqual(report.checked, 7)   # 2 manifests + 3 agents + 2 skills


# --------------------------------------------------------------------------
# the five crash sites, found by fuzzing
# --------------------------------------------------------------------------

class CrashSiteTests(PluginTreeMixin, unittest.TestCase):
    """Five distinct lines in validate_plugin.py raised out of the check that
    called them. Each costs you a whole check's verdict, and for
    check_context_budget() that is the budget of all 26 components."""

    # -- site 1: read_text() open(), from check_markdown_component ---------

    def test_an_unreadable_agent_costs_only_its_own_verdict(self):
        os.chmod(self.write("agents/bravo.md", good_agent("bravo")), 0o000)
        if os.access(self.path("agents/bravo.md"), os.R_OK):
            self.skipTest("running as a user that ignores file permissions")
        report = vp.validate(self.tmp)
        self.assertUnvalidated(report, "bravo.md")
        self.assertSurvivors(report, "alpha.md", "charlie.md",
                             "one/SKILL.md", "two/SKILL.md")

    def test_a_broken_symlink_agent_is_unvalidated_not_a_crash(self):
        os.remove(self.path("agents/bravo.md"))
        os.symlink(os.path.join(self.tmp, "nowhere", "gone.md"),
                   self.path("agents/bravo.md"))
        report = vp.validate(self.tmp)
        self.assertUnvalidated(report, "bravo.md")
        self.assertSurvivors(report, "alpha.md", "charlie.md")

    def test_a_directory_where_an_agent_should_be_is_unvalidated(self):
        self.replace_with_dir("agents/bravo.md")
        report = vp.validate(self.tmp)
        self.assertUnvalidated(report, "bravo.md")
        self.assertSurvivors(report, "alpha.md", "charlie.md")

    def test_a_symlink_loop_is_unvalidated_not_a_crash(self):
        loop = self.path("agents/bravo.md")
        os.remove(loop)
        os.symlink(loop, loop)
        report = vp.validate(self.tmp)
        self.assertUnvalidated(report, "bravo.md")
        self.assertSurvivors(report, "alpha.md", "charlie.md")

    def test_an_unreadable_skill_costs_only_its_own_verdict(self):
        os.chmod(self.write("skills/one/SKILL.md", good_skill("one")), 0o000)
        if os.access(self.path("skills/one/SKILL.md"), os.R_OK):
            self.skipTest("running as a user that ignores file permissions")
        report = vp.validate(self.tmp)
        self.assertUnvalidated(report, "one/SKILL.md")
        self.assertSurvivors(report, "two/SKILL.md", "alpha.md")

    # -- site 2: read_text() open(), from check_context_budget -------------

    def test_one_unreadable_agent_does_not_blank_every_context_budget(self):
        """The stack-playbook scan re-reads every agent file. One unreadable
        file raised out of check_context_budget(), so the budgets of every
        other agent, skill and template went unchecked -- and the run said so
        once, about 'context budget', naming none of them."""
        self.write("agents/charlie.md",
                   good_agent("charlie").replace("well", "well, reads stacks/js-ts.md"))
        os.chmod(self.write("agents/bravo.md", good_agent("bravo")), 0o000)
        if os.access(self.path("agents/bravo.md"), os.R_OK):
            self.skipTest("running as a user that ignores file permissions")
        report = vp.validate(self.tmp)
        # charlie.md comes after bravo.md and breaks the stacks/ rule; the
        # check must still reach it.
        self.assertTrue(any("charlie.md" in w and "stack playbook" in w
                            for w in report.warnings),
                        f"the budget scan stopped at bravo.md: {report.warnings!r}")

    # -- site 3: estimate_tokens() / os.path.getsize -----------------------

    def test_a_broken_symlink_template_does_not_blank_the_budget_check(self):
        os.makedirs(self.path("templates/agents"))
        os.symlink(os.path.join(self.tmp, "nowhere.tmpl"),
                   self.path("templates/agents/aaa.tmpl"))
        # zzz sorts after aaa, so it is only reached if aaa did not abort.
        self.write("templates/agents/zzz.tmpl", "z" * (1_600 * 4 + 400))
        report = vp.validate(self.tmp)
        self.assertUnvalidated(report, "aaa.tmpl")
        self.assertTrue(any("zzz.tmpl" in w for w in report.warnings),
                        f"the budget scan stopped at aaa.tmpl: {report.warnings!r}")

    # -- site 4: check_marketplace, `entry["name"] in seen` ----------------

    def test_a_marketplace_entry_name_that_is_not_a_string_is_an_error(self):
        """`entry["name"] in seen` raised TypeError: unhashable type 'list',
        which cost every later entry its check."""
        self.write_json(".claude-plugin/marketplace.json",
                        {"name": "demo-market", "owner": {"name": "someone"},
                         "plugins": [{"name": ["demo"], "source": "."},
                                     {"name": "later", "source": "./nope"}]})
        report = vp.validate(self.tmp)
        joined = "\n".join(report.errors)
        self.assertIn("plugins[0]", joined)
        self.assertIn("must be a string", joined)
        # The entry after the bad one was still checked.
        self.assertIn("plugins[1]", joined)

    def test_an_unhashable_entry_name_does_not_abort_the_whole_check(self):
        self.write_json(".claude-plugin/marketplace.json",
                        {"name": "demo-market", "owner": {"name": "someone"},
                         "plugins": [{"name": {"a": 1}, "source": "."}]})
        report = vp.validate(self.tmp)
        self.assertEqual(
            [m for m in report.unvalidated if "marketplace" in m], [],
            "an entry with a wrong-typed name is understood, not unjudgeable")
        self.assertTrue(any("must be a string" in e for e in report.errors))

    # -- site 5: check_hooks(), open() -------------------------------------

    def test_an_unreadable_hooks_file_is_unvalidated_not_a_crash(self):
        os.chmod(self.write("hooks/hooks.json", '{"hooks": {}}'), 0o000)
        if os.access(self.path("hooks/hooks.json"), os.R_OK):
            self.skipTest("running as a user that ignores file permissions")
        report = vp.validate(self.tmp)
        # "could not be read" is check_hooks' own wording. guarded() would
        # also catch this and say "could not be checked", so asserting on the
        # specific phrasing is what distinguishes the handler being right
        # from the blanket backstop papering over it.
        self.assertIn("could not be read",
                      " ".join(self.assertUnvalidated(report, "hooks")))
        self.assertSurvivors(report, "alpha.md", "one/SKILL.md")

    def test_a_directory_named_hooks_json_is_unvalidated_not_a_crash(self):
        os.makedirs(self.path("hooks/hooks.json"))
        report = vp.validate(self.tmp)
        self.assertIn("could not be read",
                      " ".join(self.assertUnvalidated(report, "hooks")))
        self.assertSurvivors(report, "alpha.md", "one/SKILL.md")


# --------------------------------------------------------------------------
# "could not be validated" must look nothing like "valid"
# --------------------------------------------------------------------------

class VerdictVisibilityTests(PluginTreeMixin, unittest.TestCase):
    def test_an_unvalidatable_component_is_not_counted_as_checked(self):
        self.replace_with_dir("agents/bravo.md")
        report = vp.validate(self.tmp)
        self.assertEqual(report.checked, 6)       # one fewer than the baseline 7
        # Two checks wanted this file -- the component check and the
        # stack-playbook scan -- and both say so about bravo.md and only
        # bravo.md. Nothing else lost a verdict.
        self.assertTrue(report.unvalidated)
        self.assertTrue(all("bravo.md" in m for m in report.unvalidated),
                        report.unvalidated)

    def test_an_unvalidatable_component_fails_the_run(self):
        self.replace_with_dir("agents/bravo.md")
        self.assertEqual(vp.main([self.tmp]), 1)

    def test_the_output_names_the_components_it_could_not_judge(self):
        import contextlib
        import io
        self.replace_with_dir("agents/bravo.md")
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            vp.main([self.tmp])
        out = buf.getvalue()
        self.assertIn("bravo.md", out)
        self.assertIn("could not be validated", out)

    def test_a_clean_run_states_that_nothing_went_unjudged(self):
        # Silence is the failure mode: "passed" must say how many components
        # it could not reach, even when the answer is none.
        import contextlib
        import io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.assertEqual(vp.main([self.tmp]), 0)
        self.assertIn("could not be validated", buf.getvalue())

    def test_guarded_still_records_an_unexpected_exception_as_an_error(self):
        # test_robustness.py depends on this; an unknown verdict is a failure
        # as well as an unknown, and must appear in both lists.
        r = vp.Report()

        def boom():
            raise RuntimeError("unanticipated")

        self.assertIsNone(vp.guarded("widget", r, boom))
        self.assertTrue(any("widget" in e and "RuntimeError" in e
                            for e in r.errors))
        self.assertTrue(any("widget" in u for u in r.unvalidated))


# --------------------------------------------------------------------------
# silence must never look like success
# --------------------------------------------------------------------------

class SilentHoleTests(PluginTreeMixin, unittest.TestCase):
    def test_a_component_with_undecodable_bytes_is_reported(self):
        """errors='replace' turns a latin-1 byte into U+FFFD and the file then
        validates as if it were fine. The loader will not see what you wrote,
        so the substitution has to be said out loud."""
        self.write("agents/bravo.md",
                   b"---\nname: bravo\ndescription: >\n  caf\xe9 au lait\n"
                   b"tools: Read\n---\n\n" + BODY.encode("utf-8"))
        report = vp.validate(self.tmp)
        self.assertTrue(any("bravo.md" in m and
                            ("not valid UTF-8" in m or "undecodable" in m)
                            for m in report.errors + report.warnings),
                        f"the replacement went unreported: {report.errors!r} "
                        f"{report.warnings!r}")
        self.assertSurvivors(report, "alpha.md", "charlie.md")

    def test_a_skill_directory_without_a_skill_md_is_reported(self):
        """glob('skills/*/SKILL.md') simply does not match it, so the
        directory is invisible and the run passes: a skill that will never
        load, reported as a clean plugin."""
        os.makedirs(self.path("skills/ghost"))
        self.write("skills/ghost/README.md", "notes for a skill nobody wrote")
        report = vp.validate(self.tmp)
        self.assertTrue(any("ghost" in m for m in report.errors),
                        f"a skill directory with no SKILL.md passed: "
                        f"{report.errors!r}")

    def test_a_utf16_agent_still_gets_a_verdict(self):
        # Decoding succeeds, so this one is judged on its contents rather than
        # reported as unreadable. Pinned because the two must not be confused.
        self.write("agents/bravo.md", good_agent("bravo").encode("utf-16"))
        report = vp.validate(self.tmp)
        self.assertEqual([m for m in report.unvalidated if "bravo.md" in m], [])
        self.assertSurvivors(report, "alpha.md", "charlie.md")


if __name__ == "__main__":
    unittest.main(verbosity=2)
