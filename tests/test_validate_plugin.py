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

# The real plugin, for the handful of tests whose subject is this repository's
# own configuration rather than a fixture tree.
PLUGIN_ROOT = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))


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

    def test_a_file_where_a_skill_directory_should_be_is_reported(self):
        """The same glob misses this too, and `not os.path.isdir(sub)` used to
        skip it before the SKILL.md rule could run. A skill replaced by a file
        vanished from the run: 26 components checked became 25, in silence."""
        shutil.rmtree(self.path("skills/one"))
        self.write("skills/one", "I was a skill directory once")
        report = vp.validate(self.tmp)
        hits = [e for e in report.errors if "skills/one" in e]
        self.assertTrue(hits, f"a file in place of a skill passed: "
                              f"{report.errors!r}")
        self.assertIn("is a file, not a directory", hits[0])
        self.assertSurvivors(report, "two/SKILL.md", "alpha.md")

    def test_a_dangling_symlink_where_a_skill_should_be_is_reported(self):
        shutil.rmtree(self.path("skills/one"))
        os.symlink(self.path("skills/nowhere"), self.path("skills/one"))
        report = vp.validate(self.tmp)
        hits = [e for e in report.errors if "skills/one" in e]
        self.assertTrue(hits, f"a dangling skill symlink passed: "
                              f"{report.errors!r}")
        self.assertIn("resolves to nothing", hits[0])

    def test_a_symlink_loop_where_a_skill_should_be_is_reported(self):
        loop = self.path("skills/one")
        shutil.rmtree(loop)
        os.symlink(loop, loop)
        report = vp.validate(self.tmp)
        self.assertTrue([e for e in report.errors if "skills/one" in e],
                        f"a looping skill symlink passed: {report.errors!r}")

    def test_a_vanished_skill_is_reported_like_its_sibling(self):
        """One rule, two shapes. A directory with no SKILL.md and a file where
        the directory should be both mean the same thing to the loader -- the
        skill does not exist -- so they are both errors and both say why in
        the same words. The hole was in an existing rule, not a missing one."""
        os.makedirs(self.path("skills/ghost"))
        shutil.rmtree(self.path("skills/one"))
        self.write("skills/one", "not a directory")
        report = vp.validate(self.tmp)
        said = [e for e in report.errors
                if "skills/ghost" in e or "skills/one" in e]
        self.assertEqual(len(said), 2, report.errors)
        for message in said:
            self.assertIn("will not load as a skill and no other check in "
                          "this run looked at it", message)

    def test_a_dotfile_under_skills_is_not_mistaken_for_a_skill(self):
        """The false-positive guard. .DS_Store and .gitkeep are real and are
        not components; the rule skipped dotfiles before and still does."""
        self.write("skills/.DS_Store", b"\x00\x01")
        self.write("skills/.gitkeep", "")
        self.assertBaselineIsClean()

    def test_a_utf16_agent_still_gets_a_verdict(self):
        # Decoding succeeds, so this one is judged on its contents rather than
        # reported as unreadable. Pinned because the two must not be confused.
        self.write("agents/bravo.md", good_agent("bravo").encode("utf-16"))
        report = vp.validate(self.tmp)
        self.assertEqual([m for m in report.unvalidated if "bravo.md" in m], [])
        self.assertSurvivors(report, "alpha.md", "charlie.md")


# --------------------------------------------------------------------------
# hole 1: a file whose bytes were never obtained is not a file that passed
# --------------------------------------------------------------------------

class TemplateContentsTests(PluginTreeMixin, unittest.TestCase):
    """`templates/agents/*.tmpl` is checked by exactly one thing -- the context
    budget -- and that check used os.path.getsize(), which stats rather than
    opens. So a template nobody could read produced "Validation passed", which
    is the defect this branch removed from four other places. The budget is
    allowed to be the only claim made about a template; it is not allowed to be
    made about bytes that cannot be obtained."""

    def setUp(self):
        super().setUp()
        os.makedirs(self.path("templates/agents"))
        self.write("templates/agents/aaa.tmpl", "a" * 2000)
        self.write("templates/agents/mid.tmpl", "m" * 2000)
        # zzz sorts last, so it is reached only if mid.tmpl did not abort.
        self.write("templates/agents/zzz.tmpl", "z" * 2000)

    def test_templates_do_not_disturb_the_clean_baseline(self):
        self.assertBaselineIsClean()

    def test_an_unreadable_template_is_unvalidated_not_passed(self):
        os.chmod(self.path("templates/agents/mid.tmpl"), 0o000)
        if os.access(self.path("templates/agents/mid.tmpl"), os.R_OK):
            self.skipTest("running as a user that ignores file permissions")
        report = vp.validate(self.tmp)
        self.assertUnvalidated(report, "mid.tmpl")
        self.assertSurvivors(report, "aaa.tmpl", "zzz.tmpl", "alpha.md")

    def test_an_unreadable_template_fails_the_run(self):
        os.chmod(self.path("templates/agents/mid.tmpl"), 0o000)
        if os.access(self.path("templates/agents/mid.tmpl"), os.R_OK):
            self.skipTest("running as a user that ignores file permissions")
        # Exit 0 about a file nobody read is the whole bug; --strict is not
        # needed to make it fail, because an unknown verdict is already one.
        self.assertEqual(vp.main([self.tmp]), 1)

    def test_a_directory_named_like_a_template_is_unvalidated(self):
        """getsize() on a directory returns the dirent size -- 64 here -- which
        the budget check turned into a confident '~16 tokens' about contents
        that do not exist. A number measured from nothing is worse than none."""
        self.replace_with_dir("templates/agents/mid.tmpl")
        report = vp.validate(self.tmp)
        self.assertUnvalidated(report, "mid.tmpl")
        self.assertFalse([w for w in report.warnings if "mid.tmpl" in w],
                         f"a directory got a budget number: {report.warnings!r}")
        self.assertSurvivors(report, "aaa.tmpl", "zzz.tmpl")

    def test_a_template_symlinked_to_a_directory_is_unvalidated(self):
        target = self.path("templates/agents/mid.tmpl")
        os.remove(target)
        os.symlink(self.tmp, target)
        report = vp.validate(self.tmp)
        self.assertUnvalidated(report, "mid.tmpl")
        self.assertSurvivors(report, "aaa.tmpl", "zzz.tmpl")

    def test_an_empty_template_is_warned_about_not_passed(self):
        """Under budget because there is nothing in it. The budget is the only
        check a template gets, so it is the only place this can be said."""
        self.write("templates/agents/mid.tmpl", "")
        report = vp.validate(self.tmp)
        self.assertTrue([w for w in report.warnings
                         if "mid.tmpl" in w and "empty" in w],
                        f"an empty template passed silently: {report.warnings!r}")
        self.assertEqual(vp.main([self.tmp, "--strict"]), 1)

    def test_the_unreadable_message_says_the_contents_were_never_checked(self):
        # The point of the message: for a template this is the only check, so
        # "no budget verdict" is the same as "no verdict at all". If it only
        # said "no context-budget verdict" a reader could reasonably assume
        # some other check had looked at the contents. None did.
        os.chmod(self.path("templates/agents/mid.tmpl"), 0o000)
        if os.access(self.path("templates/agents/mid.tmpl"), os.R_OK):
            self.skipTest("running as a user that ignores file permissions")
        hits = self.assertUnvalidated(vp.validate(self.tmp), "mid.tmpl")
        self.assertIn("nothing else in this run opened it", " ".join(hits))

    def test_the_budget_does_not_claim_sole_custody_of_an_agent(self):
        """The other side of the same message. check_markdown_component() and
        the stack-playbook scan both open an agent, so the budget check saying
        'nothing else in this run opened it' about one would be a confident
        false statement -- the exact failure mode this validator exists to
        stop printing, committed by the fix for it."""
        self.replace_with_dir("agents/bravo.md")
        report = vp.validate(self.tmp)
        budget_line = [m for m in report.unvalidated
                       if "bravo.md" in m and "context-budget" in m]
        self.assertTrue(budget_line, report.unvalidated)
        self.assertNotIn("nothing else in this run opened it", budget_line[0])
        # ...and the checks that did open it each said so separately.
        self.assertTrue([m for m in report.unvalidated
                         if "bravo.md" in m and "stack playbook" in m],
                        report.unvalidated)

    def test_an_empty_agent_is_not_double_reported_by_the_budget(self):
        # An empty agent is already an error about its frontmatter. Saying
        # "it is empty" a second time is noise, and a noisy check gets
        # switched off, taking the rest of the rules with it.
        self.write("agents/bravo.md", "")
        report = vp.validate(self.tmp)
        self.assertEqual([w for w in report.warnings
                          if "bravo.md" in w and "empty" in w], [])
        self.assertTrue([e for e in report.errors
                         if "bravo.md" in e and "frontmatter" in e],
                        report.errors)

    def test_a_readable_template_over_budget_is_still_a_plain_warning(self):
        # The readability probe must not have displaced the check it guards.
        self.write("templates/agents/mid.tmpl", "m" * (1_600 * 4 + 400))
        report = vp.validate(self.tmp)
        self.assertTrue([w for w in report.warnings
                         if "mid.tmpl" in w and "over the 1,600 budget" in w],
                        report.warnings)
        self.assertEqual([m for m in report.unvalidated if "mid.tmpl" in m], [])


# --------------------------------------------------------------------------
# "0 warnings" is not "everything fits"
# --------------------------------------------------------------------------

class BudgetCoverageTests(PluginTreeMixin, unittest.TestCase):
    """A ceiling applies to four globs. Every other file under agents/,
    skills/, templates/, stacks/ and hooks/ has none -- 23 of them in this
    repo, including a 8,408-token dashboard.html and ten stack playbooks over
    5,000 each. Most are unbudgeted on purpose, so warning about them would be
    a false positive; but `0 warning(s)` read on its own says "everything
    fits", and for those files nothing was ever weighed."""

    def setUp(self):
        super().setUp()
        os.makedirs(self.path("templates/agents"))
        self.write("templates/agents/aaa.tmpl", "a" * 2000)
        os.makedirs(self.path("stacks"))
        self.write("stacks/js-ts.md", "s" * 40000)      # huge, and no ceiling

    def test_a_file_with_no_ceiling_is_counted_not_warned_about(self):
        report = vp.validate(self.tmp)
        self.assertIn("stacks/js-ts.md", report.unbudgeted)
        self.assertEqual([w for w in report.warnings if "js-ts" in w], [],
                         "a file with no ceiling must not produce a finding")

    def test_a_weighed_file_is_not_counted_as_unweighed(self):
        report = vp.validate(self.tmp)
        self.assertIn("templates/agents/aaa.tmpl", report.budgeted)
        self.assertNotIn("templates/agents/aaa.tmpl", report.unbudgeted)
        for name in ("alpha", "bravo", "charlie"):
            self.assertIn(f"agents/{name}.md", report.budgeted)

    def test_an_exempt_file_is_neither_weighed_nor_counted_as_unweighed(self):
        # EXEMPT records a decision with a reason. A file named there was
        # considered; it is not part of the silence the count measures.
        self.write("templates/report/STYLE.md", "y" * 40000)
        report = vp.validate(self.tmp)
        self.assertIn("templates/report/STYLE.md", vp.EXEMPT)
        self.assertNotIn("templates/report/STYLE.md", report.unbudgeted)
        self.assertNotIn("templates/report/STYLE.md", report.budgeted)

    def test_the_run_states_how_many_files_it_weighed(self):
        import contextlib
        import io
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            self.assertEqual(vp.main([self.tmp]), 0)
        out = buf.getvalue()
        self.assertIn("Context budgets:", out)
        self.assertIn("weighed against a ceiling", out)
        self.assertIn("have no ceiling and were not", out)

    def test_an_unlistable_directory_makes_the_count_a_floor(self):
        """os.walk() swallows a directory it cannot list and walks on, which
        would turn an unknown total into a confident understatement."""
        locked = self.path("templates/locked")
        os.makedirs(locked)
        self.write("templates/locked/thing.md", "x" * 100)
        os.chmod(locked, 0o000)
        if os.access(locked, os.R_OK):
            self.skipTest("running as a user that ignores file permissions")
        import contextlib
        import io
        report = vp.validate(self.tmp)
        self.assertTrue(report.budget_scan_incomplete)
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            vp.main([self.tmp])
        self.assertIn("at least", buf.getvalue())
        self.assertIn("is a floor, not a total", buf.getvalue())

    def test_an_unlistable_directory_is_a_counted_warning(self):
        """The caveat used to be a bare print() in main() beginning with the
        word "warning:" that was not in r.warnings. It was never counted in
        the "N warning(s)" summary and --strict walked straight past it, so a
        directory of shipped components that could not be opened exited 0
        while the run printed a line calling itself a warning. A label is not
        a behaviour."""
        locked = self.path("stacks/locked")
        os.makedirs(locked)
        self.write("stacks/locked/thing.md", "x" * 100)
        os.chmod(locked, 0o000)
        self.addCleanup(os.chmod, locked, 0o700)
        if os.access(locked, os.R_OK):
            self.skipTest("running as a user that ignores file permissions")
        report = vp.validate(self.tmp)
        hits = [w for w in report.warnings if "is a floor, not a total" in w]
        self.assertTrue(hits, f"the caveat is not a counted warning: "
                              f"{report.warnings!r}")
        self.assertIn("stacks/locked", hits[0])
        self.assertEqual(vp.main([self.tmp, "--strict"]), 1,
                         "--strict passed over an unlistable component directory")

    def test_a_listable_tree_does_not_claim_an_incomplete_scan(self):
        # The false-positive guard: "at least" must not appear when the walk
        # saw everything, or the hedge becomes meaningless.
        import contextlib
        import io
        report = vp.validate(self.tmp)
        self.assertFalse(report.budget_scan_incomplete)
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            vp.main([self.tmp])
        self.assertNotIn("at least", buf.getvalue())

    def test_the_quotable_summary_line_is_unchanged(self):
        """docs/SECURITY-REVIEW.md and docs/PROMPT-REVIEW.md quote this line
        verbatim, and neither is this agent's to edit. The coverage statement
        is a separate line for exactly that reason."""
        import contextlib
        import io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.assertEqual(vp.main([self.tmp]), 0)
        self.assertEqual(
            buf.getvalue().strip(),
            "Validation passed: 7 component(s) checked, 0 warning(s), "
            "0 could not be validated.")


# --------------------------------------------------------------------------
# hole 2: what we guarantee about encoding, and what we do not
# --------------------------------------------------------------------------

class EncodingGuaranteeTests(PluginTreeMixin, unittest.TestCase):
    """The validator accepts UTF-8 (BOM or not) and BOM-marked UTF-16. Whether
    the real plugin loader does is still unmeasured. `claude plugin validate
    --strict` has now been run on this repo and passes, and that settles
    nothing here: it validates the marketplace manifest, so it has never
    opened an agent or a skill, let alone judged one's encoding. So a non-UTF-8
    component gets a WARNING that says the uncertainty out loud. An error would
    assert knowledge nobody has; silence would assert the opposite. See the
    note in check_markdown_component()."""

    def test_a_utf16_agent_is_warned_about_by_encoding(self):
        self.write("agents/bravo.md", good_agent("bravo").encode("utf-16"))
        report = vp.validate(self.tmp)
        hits = [w for w in report.warnings if "bravo.md" in w and "UTF-16" in w]
        self.assertTrue(hits, f"the encoding went unsaid: {report.warnings!r}")
        self.assertIn("never been measured", hits[0])

    def test_the_utf16_warning_is_not_an_error_and_not_an_unknown(self):
        # The file decoded and its contents were judged. Promoting that to an
        # error would state as fact something nobody has run the CLI to learn.
        self.write("agents/bravo.md", good_agent("bravo").encode("utf-16"))
        report = vp.validate(self.tmp)
        self.assertEqual([e for e in report.errors if "bravo.md" in e], [])
        self.assertEqual([m for m in report.unvalidated if "bravo.md" in m], [])
        self.assertEqual(report.checked, 7)   # it was checked, like any other

    def test_a_utf16_be_agent_is_warned_about_too(self):
        self.write("agents/bravo.md",
                   b"\xfe\xff" + good_agent("bravo").encode("utf-16-be"))
        report = vp.validate(self.tmp)
        self.assertTrue([w for w in report.warnings
                         if "bravo.md" in w and "UTF-16 BE" in w],
                        report.warnings)

    def test_a_utf16_skill_is_warned_about_too(self):
        self.write("skills/one/SKILL.md", good_skill("one").encode("utf-16"))
        report = vp.validate(self.tmp)
        self.assertTrue([w for w in report.warnings
                         if "SKILL.md" in w and "UTF-16" in w], report.warnings)

    def test_a_utf8_bom_agent_is_not_warned_about(self):
        """The false-positive guard. load_json() documents that the Claude CLI
        accepts a BOM on plugin.json, so a BOM is UTF-8 here and warning about
        it would fire on a file that is known-good."""
        self.write("agents/bravo.md",
                   b"\xef\xbb\xbf" + good_agent("bravo").encode("utf-8"))
        self.assertBaselineIsClean()

    def test_plain_utf8_components_produce_no_encoding_warning(self):
        # The measured firing rate of this rule on well-formed components is
        # zero; a rule above ~10% false positives gets switched off entirely.
        report = vp.validate(self.tmp)
        self.assertEqual([w for w in report.warnings if "UTF" in w], [])

    def test_a_utf16_file_without_a_bom_is_not_accepted(self):
        """The other half of the stated guarantee. Without a BOM the bytes
        decode as NUL-separated ASCII, which is valid UTF-8 and so raises no
        U+FFFD, but does not start with '---\\n'. It is reported as missing
        frontmatter -- not accepted, and not silently passed."""
        self.write("agents/bravo.md", good_agent("bravo").encode("utf-16-le"))
        report = vp.validate(self.tmp)
        self.assertTrue([e for e in report.errors
                         if "bravo.md" in e and "frontmatter" in e],
                        report.errors)

    def test_read_text_sniffed_names_the_encoding_it_used(self):
        for raw, expected in (
            (good_agent("x").encode("utf-8"), "UTF-8"),
            (b"\xef\xbb\xbf" + good_agent("x").encode("utf-8"), "UTF-8"),
            (good_agent("x").encode("utf-16"), "UTF-16 LE"),
            (b"\xfe\xff" + good_agent("x").encode("utf-16-be"), "UTF-16 BE"),
        ):
            path = self.write("agents/probe.md", raw)
            self.assertEqual(vp.read_text_sniffed(path)[1], expected)


# --------------------------------------------------------------------------
# a hooks.json that cannot resolve is not a plugin without hooks
# --------------------------------------------------------------------------

class HooksPresenceTests(PluginTreeMixin, unittest.TestCase):
    def test_a_dangling_symlink_hooks_file_is_unvalidated_not_absent(self):
        """os.path.exists() follows the link and says False, so a hooks.json
        that will certainly fail to load read as 'this plugin ships no hooks'
        and the run passed in silence. lexists() tells the two apart."""
        os.makedirs(self.path("hooks"), exist_ok=True)
        os.symlink(self.path("hooks/nowhere.json"), self.path("hooks/hooks.json"))
        report = vp.validate(self.tmp)
        self.assertUnvalidated(report, "hooks")
        self.assertSurvivors(report, "alpha.md", "one/SKILL.md")

    def test_a_symlink_loop_hooks_file_is_unvalidated_not_absent(self):
        os.makedirs(self.path("hooks"), exist_ok=True)
        loop = self.path("hooks/hooks.json")
        os.symlink(loop, loop)
        report = vp.validate(self.tmp)
        self.assertUnvalidated(report, "hooks")

    def test_a_plugin_with_no_hooks_at_all_is_still_silent(self):
        # The false-positive guard for the lexists change: absent must stay
        # absent, which is the legitimate shape for most plugins.
        self.assertFalse(os.path.exists(self.path("hooks")))
        self.assertBaselineIsClean()


class HooksShapeTests(PluginTreeMixin, unittest.TestCase):
    """How deep the hooks check goes, and the line it stops at.

    `{"hooks": 7}` passed: the wrapper key existed, and presence was all that
    was checked, so a file that cannot possibly load printed the same summary
    as a working one. One level further is decided by the JSON type alone --
    an event map is a mapping, and an int, list, string or null is not -- so
    it is a fact rather than a guess.

    It stops there on purpose. Event NAMES cannot be validated without a list
    of them, and the nine plugin hooks.json files installed on this machine
    use nine distinct names between them, including PostToolUseFailure,
    StopFailure, UserPromptExpansion and PermissionRequest; a hardcoded list
    would reject a legitimate plugin as soon as a tenth ships. Each event's
    VALUE cannot be validated without asserting what the loader rejects, and
    nobody here has run it on a bad one -- `claude plugin validate --strict`
    passes on this repo but validates the marketplace manifest only. That is
    full schema validation, and it is how a check earns false positives.
    """

    def _hooks(self, text: str):
        self.write("hooks/hooks.json", text)
        return vp.validate(self.tmp)

    def test_a_hooks_wrapper_that_is_a_number_is_an_error(self):
        report = self._hooks('{"hooks": 7}')
        hits = [e for e in report.errors if "hooks.json" in e]
        self.assertTrue(hits, f"a hooks file that cannot load passed: "
                              f"{report.errors!r}")
        self.assertIn("must be an object mapping event names", hits[0])

    def test_a_hooks_wrapper_that_is_a_list_or_null_is_an_error(self):
        for body in ('{"hooks": []}', '{"hooks": null}', '{"hooks": "all"}'):
            with self.subTest(body=body):
                report = self._hooks(body)
                self.assertTrue([e for e in report.errors if "hooks.json" in e],
                                f"{body} passed: {report.errors!r}")

    def test_the_bad_wrapper_does_not_cost_any_other_component(self):
        report = self._hooks('{"hooks": 7}')
        self.assertSurvivors(report, "alpha.md", "bravo.md", "one/SKILL.md")
        self.assertEqual([m for m in report.unvalidated if "hooks" in m], [],
                         "a wrong-shaped wrapper is understood, not unjudgeable")

    def test_an_event_map_is_accepted_without_judging_its_contents(self):
        """The false-positive guard, and the stated limit. An event name this
        script has never heard of must pass: real plugins ship names it does
        not know, and rejecting one would be asserting knowledge of a loader
        that has never been run here."""
        self.write("hooks/hooks.json", json.dumps(
            {"hooks": {"SomeFutureEvent": [{"hooks": [
                {"type": "command", "command": "echo hi"}]}],
                "PostToolUseFailure": "not even a list"}}))
        self.assertBaselineIsClean()

    def test_an_empty_event_map_is_deliberately_left_silent(self):
        """It loads; it simply does nothing. This validator has no way to tell
        a placeholder from a mistake, and a warning it cannot justify is the
        false positive that gets the whole check switched off."""
        self.write("hooks/hooks.json", '{"hooks": {}}')
        self.assertBaselineIsClean()


# --------------------------------------------------------------------------
# hole 3: an empty component directory is legitimate; an unlistable one is not
# --------------------------------------------------------------------------

class ComponentDirectoryTests(PluginTreeMixin, unittest.TestCase):
    """glob() swallows the error from a directory it cannot list and returns
    [], so a mode-000 agents/ deleted every agent from the run and the run
    passed. Measured on this repo: 26 components checked became 19, with no
    error, no warning and nothing unvalidated -- byte-for-byte what an
    agents/ emptied of its seven files prints. A plugin that ships no agents
    is legitimate and must stay silent; a plugin whose agents/ cannot be
    opened is seven components with no verdict."""

    def test_an_unlistable_agents_directory_is_reported(self):
        os.chmod(self.path("agents"), 0o000)
        self.addCleanup(os.chmod, self.path("agents"), 0o700)
        if os.access(self.path("agents"), os.R_OK):
            self.skipTest("running as a user that ignores file permissions")
        report = vp.validate(self.tmp)
        hits = self.assertUnvalidated(report, "agents")
        self.assertIn("could not be listed", " ".join(hits))
        self.assertSurvivors(report, "one/SKILL.md", "two/SKILL.md")

    def test_an_unlistable_agents_directory_fails_the_run(self):
        os.chmod(self.path("agents"), 0o000)
        self.addCleanup(os.chmod, self.path("agents"), 0o700)
        if os.access(self.path("agents"), os.R_OK):
            self.skipTest("running as a user that ignores file permissions")
        # Not --strict: an unknown verdict is already a failure.
        self.assertEqual(vp.main([self.tmp]), 1)

    def test_an_empty_agents_directory_stays_silent(self):
        """The false-positive guard, and the whole judgement in one test. A
        plugin may ship no agents at all. If this ever goes red the check has
        started reporting a legitimate plugin, which costs more than the miss
        it was closing."""
        for name in self.AGENTS:
            os.remove(self.path(f"agents/{name}.md"))
        report = vp.validate(self.tmp)
        self.assertEqual(report.errors, [])
        self.assertEqual(report.warnings, [])
        self.assertEqual(report.unvalidated, [])

    def test_an_absent_agents_directory_is_still_only_a_warning(self):
        # The other legitimate shape, and it was already handled: absent is a
        # warning about layout, not a component with no verdict.
        shutil.rmtree(self.path("agents"))
        report = vp.validate(self.tmp)
        self.assertEqual(report.unvalidated, [])
        self.assertTrue([w for w in report.warnings if "agents" in w],
                        report.warnings)

    def test_an_unlistable_skills_directory_is_reported_by_its_own_check(self):
        """check_skill_directories() has to list skills/ anyway, so it owns
        this one and agents/ is not probed twice. Asserting the specific
        wording is what distinguishes its own handler from guarded() papering
        over it with 'could not be checked'."""
        os.chmod(self.path("skills"), 0o000)
        self.addCleanup(os.chmod, self.path("skills"), 0o700)
        if os.access(self.path("skills"), os.R_OK):
            self.skipTest("running as a user that ignores file permissions")
        report = vp.validate(self.tmp)
        hits = self.assertUnvalidated(report, "skills")
        self.assertIn("could not be listed", " ".join(hits))
        self.assertEqual(len(hits), 1,
                         f"one unlistable directory, one report: {hits!r}")
        self.assertSurvivors(report, "alpha.md", "bravo.md", "charlie.md")

    def test_an_empty_skills_directory_stays_silent(self):
        for name in self.SKILLS:
            shutil.rmtree(self.path(f"skills/{name}"))
        report = vp.validate(self.tmp)
        self.assertEqual(report.errors, [])
        self.assertEqual(report.warnings, [])
        self.assertEqual(report.unvalidated, [])


class TemplateDirectoryTests(PluginTreeMixin, unittest.TestCase):
    """The same hole, one directory over, and this one survived.

    agents/ was probed; the directories the *template* globs depend on were
    not. Measured on this repository before this class existed: `chmod 000
    templates/agents` printed "Validation passed: 26 component(s) checked, 0
    warning(s), 0 could not be validated" and exited 0, while the number of
    files weighed against a ceiling silently fell from 28 to 24. All four
    agent templates had vanished from the run.

    It is worse here than for agents/. check_context_budget() is the ONLY
    check in the program that opens a template, so a glob that comes back
    empty does not cost those files their budget verdict -- it costs them
    every verdict there is, and the run says the same words it says about a
    tree where all four are fine.
    """

    def setUp(self):
        super().setUp()
        os.makedirs(self.path("templates/agents"))
        self.write("templates/agents/aaa.tmpl", "a" * 2000)
        self.write("templates/report/RULES.md", "r" * 2000)
        # The real EXEMPT names this file, and check_exemptions_resolve()
        # reports an exemption whose directory exists without it. Writing it
        # keeps the baseline clean, so a finding below is the injected one.
        self.write("templates/report/STYLE.md", "y" * 4000)

    def _lock(self, rel):
        os.chmod(self.path(rel), 0o000)
        self.addCleanup(os.chmod, self.path(rel), 0o700)
        if os.access(self.path(rel), os.R_OK):
            self.skipTest("running as a user that ignores file permissions")

    def test_the_fixture_with_templates_is_clean(self):
        # Without this the tests below could pass against a tree that was
        # already failing for a reason that has nothing to do with listing.
        self.assertBaselineIsClean()

    def test_an_unlistable_template_directory_is_reported(self):
        self._lock("templates/agents")
        report = vp.validate(self.tmp)
        hits = self.assertUnvalidated(report, "templates/agents")
        self.assertIn("could not be listed", " ".join(hits))
        self.assertSurvivors(report, "alpha.md", "one/SKILL.md")

    def test_an_unlistable_template_directory_fails_the_run(self):
        # Not --strict. A component with no verdict is already a failure, and
        # exit 0 about four files nobody opened is the entire defect.
        self._lock("templates/agents")
        self.assertEqual(vp.main([self.tmp]), 1)

    def test_the_message_says_the_templates_were_never_opened(self):
        # For a template the budget is the only check, so "not enumerated"
        # means "no verdict at all". A reader must not be left assuming some
        # other check looked at the contents; none did.
        self._lock("templates/agents")
        hits = self.assertUnvalidated(vp.validate(self.tmp), "templates/agents")
        self.assertIn("nothing else in this run opens these files",
                      " ".join(hits))

    def test_one_unlistable_directory_is_reported_once(self):
        self._lock("templates/agents")
        report = vp.validate(self.tmp)
        self.assertEqual(
            len([m for m in report.unvalidated if "templates/agents" in m]), 1,
            f"one unlistable directory, one report: {report.unvalidated!r}")

    def test_an_unlistable_report_directory_is_reported(self):
        self._lock("templates/report")
        report = vp.validate(self.tmp)
        hits = self.assertUnvalidated(report, "templates/report")
        self.assertIn("could not be listed", " ".join(hits))

    def test_a_locked_directory_does_not_make_an_exemption_look_stale(self):
        """os.path.exists() says False both for a file that is not there and
        for one inside a directory that cannot be opened. The second was
        reported as the first -- "that file does not [exist] -- delete it, or
        create the file it was reserving" about a live exemption, printed by
        the check whose whole purpose is to catch claims nobody verified."""
        self.write("templates/report/STYLE.md", "y" * 4000)
        self.exempt_table({"templates/report/STYLE.md": "rationale"})
        self._lock("templates/report")
        report = vp.validate(self.tmp)
        self.assertEqual([w for w in report.warnings if w.startswith("EXEMPT:")],
                         [], "a locked directory was read as a missing file")

    def exempt_table(self, mapping):
        original = vp.EXEMPT
        vp.EXEMPT = mapping
        self.addCleanup(setattr, vp, "EXEMPT", original)

    # -- the false-positive guards, which are the whole judgement -----------

    def test_an_empty_template_directory_stays_silent(self):
        """A plugin may ship no templates. If this goes red the check has
        started reporting a legitimate plugin, which costs more than the miss
        it closed."""
        os.remove(self.path("templates/agents/aaa.tmpl"))
        report = vp.validate(self.tmp)
        self.assertEqual(report.unvalidated, [])
        self.assertEqual(report.errors, [])
        self.assertEqual(report.warnings, [])

    def test_an_absent_template_directory_stays_silent(self):
        shutil.rmtree(self.path("templates"))
        report = vp.validate(self.tmp)
        self.assertEqual(report.unvalidated, [])
        self.assertEqual(report.warnings, [])

    def test_a_listable_template_directory_stays_silent(self):
        self.assertEqual(vp.validate(self.tmp).unvalidated, [])


# --------------------------------------------------------------------------
# a per-file ceiling is an exception for one file, not a new default
# --------------------------------------------------------------------------

class PerFileBudgetTests(PluginTreeMixin, unittest.TestCase):
    """BUDGETS carries one entry keyed by a full relative path --
    `templates/agents/ts-qa-gatekeeper.md.tmpl` at 1,800 where its group sits
    at 1,600. The risk in that shape is not the number, it is leakage: if the
    override is assigned to the loop variable it silently becomes the ceiling
    for every file sorted after it, and three templates stop being held to
    anything while the run still prints 0 warnings."""

    OVERRIDDEN = "templates/agents/ts-qa-gatekeeper.md.tmpl"

    def setUp(self):
        super().setUp()
        os.makedirs(self.path("templates/agents"))

    def test_the_override_is_the_ceiling_for_its_own_file(self):
        # Over the 1,600 group ceiling, under its own 1,800: silent.
        self.assertEqual(vp.BUDGETS[self.OVERRIDDEN], 1_800)
        self.write(self.OVERRIDDEN, "q" * (1_700 * 4))
        report = vp.validate(self.tmp)
        self.assertEqual([w for w in report.warnings if "gatekeeper" in w], [],
                         f"the per-file ceiling did not apply: {report.warnings!r}")

    def test_the_override_still_has_a_ceiling(self):
        # A named exception is not an exemption. Past 1,800 it warns, and the
        # message has to quote 1,800 or the reader trims to the wrong target.
        self.write(self.OVERRIDDEN, "q" * (1_900 * 4))
        report = vp.validate(self.tmp)
        hits = [w for w in report.warnings if "gatekeeper" in w]
        self.assertTrue(hits, report.warnings)
        self.assertIn("over the 1,800 budget", hits[0])

    def test_the_override_does_not_leak_onto_the_files_after_it(self):
        """`zzz.tmpl` sorts after the overridden file, so it is the one that
        inherits a leaked ceiling. 1,700 tokens is over 1,600 and under 1,800:
        it must warn, and it must warn about 1,600."""
        self.write(self.OVERRIDDEN, "q" * (1_700 * 4))
        self.write("templates/agents/zzz.tmpl", "z" * (1_700 * 4))
        report = vp.validate(self.tmp)
        hits = [w for w in report.warnings if "zzz.tmpl" in w]
        self.assertTrue(hits, f"zzz.tmpl inherited the override: {report.warnings!r}")
        self.assertIn("over the 1,600 budget", hits[0])

    def test_the_override_does_not_leak_onto_the_files_before_it(self):
        # aaa.tmpl sorts first; a ceiling cannot travel backwards, but the
        # group entry could have been mutated in place rather than shadowed.
        self.write("templates/agents/aaa.tmpl", "a" * (1_700 * 4))
        self.write(self.OVERRIDDEN, "q" * (1_700 * 4))
        report = vp.validate(self.tmp)
        hits = [w for w in report.warnings if "aaa.tmpl" in w]
        self.assertTrue(hits, report.warnings)
        self.assertIn("over the 1,600 budget", hits[0])

    def test_a_template_with_no_override_is_unaffected(self):
        # The false-positive guard: the other three templates keep 1,600 and
        # a file comfortably under it stays silent.
        self.write("templates/agents/zzz.tmpl", "z" * (1_500 * 4))
        report = vp.validate(self.tmp)
        self.assertEqual([w for w in report.warnings if "zzz.tmpl" in w], [],
                         report.warnings)


# --------------------------------------------------------------------------
# an exemption naming a file nobody wrote exempts nothing
# --------------------------------------------------------------------------

class ExemptionResolutionTests(PluginTreeMixin, unittest.TestCase):
    """EXEMPT once carried `templates/agents/README.md` and
    `examples/fixtures/README.md`. Neither has ever existed here. Both read as
    a decision somebody made and did nothing whatever, and no run said so
    because no run looked -- the same silent-no-op shape this validator exists
    to print instead of swallow."""

    def exempt(self, mapping):
        """Swap EXEMPT for the duration of one test, restoring it after."""
        original = vp.EXEMPT
        vp.EXEMPT = mapping
        self.addCleanup(setattr, vp, "EXEMPT", original)

    def test_an_exemption_whose_file_is_missing_is_reported(self):
        os.makedirs(self.path("templates/report"))
        self.exempt({"templates/report/GHOST.md": "human reference"})
        report = vp.validate(self.tmp)
        hits = [w for w in report.warnings if "GHOST.md" in w]
        self.assertTrue(hits, f"a dead exemption passed silently: {report.warnings!r}")
        self.assertIn("exempts nothing", hits[0])

    def test_the_reason_is_quoted_so_the_entry_can_be_found(self):
        os.makedirs(self.path("templates/report"))
        self.exempt({"templates/report/GHOST.md": "reserved for the style guide"})
        report = vp.validate(self.tmp)
        hits = [w for w in report.warnings if "GHOST.md" in w]
        self.assertIn("reserved for the style guide", hits[0])

    def test_an_exemption_that_resolves_is_silent(self):
        # The false-positive guard that matters: this fires on every run of
        # every tree, so a wrong one costs --strict for everybody.
        self.write("templates/report/STYLE.md", "y" * 40000)
        self.exempt({"templates/report/STYLE.md": "rationale"})
        report = vp.validate(self.tmp)
        self.assertEqual([w for w in report.warnings if "STYLE.md" in w], [],
                         report.warnings)

    def test_a_root_without_the_directory_at_all_is_not_a_stale_exemption(self):
        """The scoping rule, asserted rather than left implicit: this
        validator is run against trees that are not this repository, and
        `docs/` being absent from one of them is a different tree, not an
        exemption that went stale."""
        self.assertFalse(os.path.isdir(self.path("docs")))
        self.exempt({"docs/RESEARCH.md": "human reference"})
        report = vp.validate(self.tmp)
        self.assertEqual([w for w in report.warnings if "RESEARCH.md" in w], [],
                         report.warnings)

    def test_every_shipped_exemption_resolves_in_this_repository(self):
        """The regression guard on the real EXEMPT, not on a fixture. Two
        entries here named files that had never been written; this is the
        check that says so if it happens again."""
        missing = [rel for rel in vp.EXEMPT
                   if not os.path.exists(os.path.join(PLUGIN_ROOT,
                                                      *rel.split("/")))]
        self.assertEqual(missing, [],
                         f"EXEMPT names files that do not exist: {missing!r}")

    @staticmethod
    def _snapshot(root):
        """Everything a run of `root` reports, except the EXEMPT warnings.

        Those are excluded deliberately. check_exemptions_resolve() reports on
        the entries themselves, so an entry naming a file nobody wrote would
        otherwise prove itself "reachable" by producing a warning about its
        own deadness -- the test would then pass on exactly the config it
        exists to catch.
        """
        r = vp.validate(root)
        return (sorted(r.budgeted), sorted(r.unbudgeted),
                sorted(w for w in r.warnings if not w.startswith("EXEMPT:")),
                sorted(r.errors), sorted(r.unvalidated), r.checked)

    def test_every_shipped_exemption_is_reachable(self):
        """A file that exists is not the same as an exemption that does
        anything, and this is the stricter half.

        Only two sites consult EXEMPT: the `rel in EXEMPT` skip in
        check_context_budget(), which sees only what the four budget globs
        match, and the `rel not in EXEMPT` filter in record_budget_coverage(),
        which sees only what lives under COMPONENT_DIRS. A key outside both
        exempts nothing, changes no output, and still reads as a decision
        somebody weighed -- dead config, which is a lie about what this gate
        enforces.

        `docs/RESEARCH.md` was such a key from the initial commit: docs/ is
        not globbed for a budget and is not a COMPONENT_DIR, so no ceiling was
        ever applied to it and there was nothing to exempt it from. Two more
        entries in that first table named files that had never been written.
        Three of the original four did nothing, and nothing said so.

        Measured by deletion rather than argued from the source, so it stays
        true when the sites move: an entry is reachable when removing it
        changes what a run of this repository reports.
        """
        original = dict(vp.EXEMPT)
        self.exempt(original)        # restores the real table when this ends
        self.assertTrue(original, "EXEMPT is empty; nothing is being enforced")
        baseline = self._snapshot(PLUGIN_ROOT)
        for rel in original:
            with self.subTest(entry=rel):
                vp.EXEMPT = {k: v for k, v in original.items() if k != rel}
                try:
                    without = self._snapshot(PLUGIN_ROOT)
                finally:
                    vp.EXEMPT = original
                self.assertNotEqual(
                    without, baseline,
                    f"EXEMPT entry {rel!r} is dead: deleting it changes "
                    f"nothing this validator reports about {PLUGIN_ROOT}. "
                    f"Either no check consults it, and the entry should go, "
                    f"or the check that did has moved and the entry should "
                    f"follow it. Leaving it is config that lies.")


if __name__ == "__main__":
    unittest.main(verbosity=2)
