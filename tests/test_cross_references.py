"""Every path a prompt points at must exist, and a template may point at none.

Deduplication replaces prose with a pointer. A pointer that does not resolve is
strictly worse than the prose it replaced: the prose was at least readable, and
a dangling reference is a rule that silently went missing. Nothing in this
repository checked that before 2026-11-18, which is when a dedup pass across
nine prompt files made it matter.

The second half is the one that is easy to get wrong. `templates/agents/*.tmpl`
are filled in and written into a USER's repository. `${CLAUDE_PLUGIN_ROOT}`
does not expand there, and `/start` generates only the agents that fit the
stack, so a sibling template may not exist in that repo at all. A pointer from
a template to a plugin path is therefore guaranteed to dangle on the only
machine where it is ever read.
"""

import glob
import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# A backticked token that looks like a PATH to a file in this repository.
#
# The `/` is required and is the whole discrimination: a bare `RULES.md` is a
# name, and prompts use names freely ("the severity scale in RULES.md"). A
# name cannot dangle, because it points at nothing in particular. Only
# something with a directory in it is making a claim about where a file lives,
# and only that claim can be wrong.
PATH_RE = re.compile(
    r"`(\$\{CLAUDE_PLUGIN_ROOT\}/)?"
    r"([\w.-]+(?:/[\w.-]+)+\.(?:md|py|json|yml|yaml|sh|tmpl|html))`"
)

# Paths that name a file in the USER's repository, not this one. They are
# supposed to be absent here; requiring them to exist would be the opposite
# error, and asserting nothing about them would make the check vacuous.
USER_REPO_PREFIXES = (".testing/", ".claude/", ".github/")

# Generic filenames a prompt may name as a concept rather than as a file in
# this repository.
GENERIC = {
    "package.json", "pyproject.toml", "pom.xml", "build.gradle", "go.mod",
    "Cargo.toml", "tsconfig.json", "vitest.config.ts", "jest.config.js",
    "conftest.py", "pytest.ini", "setup.py", "setup.cfg", "tox.ini",
    "requirements.txt", "Gemfile", "composer.json", "pubspec.yaml",
    "CLAUDE.md", "README.md", "coverage.xml", "lcov.info", "junit.xml",
}


def _read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _candidates(path):
    for m in PATH_RE.finditer(_read(path)):
        yield m.group(1) or "", m.group(2)


def unresolved_plugin_paths(root):
    """Plugin paths named by a prompt that are not on disk under `root`.

    A module-level function taking the tree as an argument, rather than a walk
    inside the test: this project's own flake detector calls the latter
    `live-repo` -- a test that takes its answer from whichever checkout it
    happens to run in. It fired on the first draft of this file, correctly.
    """
    missing = []
    for pattern in ("agents/*.md", os.path.join("skills", "*", "SKILL.md")):
        for src in sorted(glob.glob(os.path.join(root, pattern))):
            for prefix, rel in _candidates(src):
                if rel.startswith(USER_REPO_PREFIXES) or rel in GENERIC:
                    continue
                if not os.path.exists(os.path.join(root, rel)):
                    missing.append(
                        f"{os.path.relpath(src, root)} points at "
                        f"{prefix}{rel}, which does not exist")
    return missing


def template_pointer_offenders(root):
    """Templates under `root` that reference a path only the plugin has."""
    offenders = []
    for src in sorted(glob.glob(os.path.join(root, "templates", "agents", "*.tmpl"))):
        text = _read(src)
        if "${CLAUDE_PLUGIN_ROOT}" in text:
            offenders.append(
                f"{os.path.relpath(src, root)} contains ${{CLAUDE_PLUGIN_ROOT}}, "
                f"which does not expand in the repository this file is "
                f"written into")
        for _, rel in _candidates(src):
            if rel.startswith(USER_REPO_PREFIXES) or rel in GENERIC:
                continue
            if os.path.exists(os.path.join(root, rel)):
                offenders.append(
                    f"{os.path.relpath(src, root)} points at {rel}. That file "
                    f"exists here and will not exist where this template is read")
    return offenders


class PromptPathsResolve(unittest.TestCase):
    """Any plugin file a prompt names must be on disk."""

    def test_every_plugin_path_named_by_an_agent_or_skill_exists(self):
        missing = unresolved_plugin_paths(ROOT)
        self.assertEqual(
            missing, [],
            "A prompt names a file in this plugin that is not there. If this "
            "appeared during a de-duplication pass, the rule that was removed "
            "now exists nowhere: restore it, or point somewhere real.\n  "
            + "\n  ".join(missing))


class TemplatesPointNowhere(unittest.TestCase):
    """A generated agent cannot reach this repository. Ever."""

    def test_no_template_points_at_a_plugin_path(self):
        offenders = template_pointer_offenders(ROOT)
        self.assertEqual(
            offenders, [],
            "A template points at a file only this repository has. The "
            "generated agent runs in the user's repo, where that path is "
            "absent -- so the rule it delegates is unreachable. State the "
            "rule in the template, or accept its ceiling.\n  "
            + "\n  ".join(offenders))


if __name__ == "__main__":
    unittest.main(verbosity=2)
