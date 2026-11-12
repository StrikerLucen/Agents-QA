"""The consent record is a contract between two prompts, so test it like one.

`/start` step 5 writes the record; `project-analyst` rule 4 reads it and decides
whether it is in the static pass or the executing pass. Neither is code, so nothing
would otherwise notice if the two drifted apart -- and a security gate whose state
one side writes under a name the other side does not read is not a gate.

These tests assert three things the prose must keep true:

1. the normative definition exists, in exactly one file, and its example parses;
2. the four states (never asked / refused / granted / unreadable) are each named,
   so none can collapse into another -- "silence must never look like success";
3. `/start` points at that one definition instead of restating it.

A test that only grepped for the words would pass on a file that says them in the
wrong place, so the JSON example is parsed and its fields are checked by name.
"""

import json
import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ANALYST = os.path.join(ROOT, "agents", "project-analyst.md")
START = os.path.join(ROOT, "skills", "start", "SKILL.md")

KEY = "execute_repo_commands"
SECTION = "# The consent record"


def read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def squash(text):
    """Collapse whitespace, so a reflowed paragraph is still the same sentence.

    These assertions are about what the prose *says*. Pinning them to the
    current line breaks would make every rewrap a false failure, and a check
    with false failures gets deleted rather than fixed.
    """
    return " ".join(text.split())


def json_blocks(text):
    """Every ```json fenced block in `text`, as raw strings."""
    return re.findall(r"```json\n(.*?)```", text, re.DOTALL)


class ConsentRecordIsDefinedOnce(unittest.TestCase):
    """Exactly one file may define the shape. Two copies is the defect."""

    def test_the_normative_section_exists(self):
        self.assertIn(SECTION, read(ANALYST),
                      "agents/project-analyst.md must carry the normative "
                      "'%s' section; nothing else defines the shape" % SECTION)

    def test_only_one_file_defines_the_shape(self):
        """Any prompt may *name* the key; only one may spell out its JSON."""
        definers = []
        for sub in ("agents", "skills"):
            base = os.path.join(ROOT, sub)
            for dirpath, _dirs, files in os.walk(base):
                for name in files:
                    if not name.endswith(".md"):
                        continue
                    path = os.path.join(dirpath, name)
                    if any(KEY in block for block in json_blocks(read(path))):
                        definers.append(os.path.relpath(path, ROOT))
        self.assertEqual(["agents/project-analyst.md"], sorted(definers),
                         "the consent record's JSON shape must be written out in "
                         "exactly one file; a second copy will drift from it")


class TheExampleIsValid(unittest.TestCase):
    """The example is the spec. If it does not parse, there is no spec."""

    def setUp(self):
        blocks = [b for b in json_blocks(read(ANALYST)) if KEY in b]
        self.assertEqual(1, len(blocks),
                         "expected exactly one json block defining the record")
        # The block is an object fragment, as it appears inside preferences.json.
        self.record = json.loads("{" + blocks[0].strip().rstrip(",") + "}")

    def test_the_key_path_is_consent_execute_repo_commands(self):
        self.assertIn("consent", self.record)
        self.assertIn(KEY, self.record["consent"],
                      "the pinned key path is consent.%s" % KEY)

    def test_the_value_carries_state_commands_and_both_timestamps(self):
        entry = self.record["consent"][KEY]
        for field in ("state", "commands", "decided", "expires"):
            self.assertIn(field, entry,
                          "the record must answer '%s'; without it the analyst "
                          "guesses" % field)
        self.assertEqual("granted", entry["state"])
        self.assertIsInstance(entry["commands"], list)
        self.assertTrue(entry["commands"], "the example must show real commands")
        for command in entry["commands"]:
            self.assertIsInstance(command, str,
                                  "commands are verbatim strings, compared "
                                  "byte-for-byte")


class TheFourStatesStayDistinct(unittest.TestCase):
    """never-asked / refused / granted / unreadable are four different facts."""

    def setUp(self):
        text = read(ANALYST)
        self.section = text[text.index(SECTION):]

    def test_each_state_is_named(self):
        squashed = squash(self.section)
        for phrase in ('"granted"', '"refused"', "never asked", "unreadable"):
            self.assertIn(phrase, squashed,
                          "the consent section must name the %s state; two "
                          "states that share a name collapse into one" % phrase)

    def test_refusal_is_not_the_same_record_as_never_having_been_asked(self):
        self.assertIn("key absent", squash(self.section),
                      "an absent key is how 'never asked' is represented, and "
                      "it must be spelled out as distinct from a refusal")

    def test_unreadable_is_not_reported_as_refused_or_as_not_asked(self):
        self.assertIn(
            '"unreadable" must never be reported as "declined" or as "not asked"',
            squash(self.section),
            "the section must forbid collapsing 'unreadable' into either of the "
            "other two; that collapse is the silent-success defect")


class BothFilesFailClosed(unittest.TestCase):
    """An unreadable preferences file must never read as agreement."""

    def test_the_analyst_fails_closed(self):
        self.assertIn(
            "**Fail closed. A missing or malformed consent record is not consent.**",
            squash(read(ANALYST)))

    def test_start_fails_closed_in_the_same_words(self):
        self.assertIn("**A missing or malformed record is not consent**",
                      squash(read(START)),
                      "/start must state the fail-closed rule where it writes the "
                      "record, not only where the analyst reads it")


class StartDefersToTheOneDefinition(unittest.TestCase):

    def test_start_names_the_pinned_key_path(self):
        self.assertIn("consent.%s" % KEY, squash(read(START)))

    def test_start_points_at_the_normative_file_instead_of_restating_it(self):
        text = read(START)
        self.assertIn("agents/project-analyst.md", squash(text),
                      "/start must point at the file that defines the shape")
        self.assertIn('"The consent record"', squash(text),
                      "/start must name the section, so the pointer survives a "
                      "reorganisation of that file")

    def test_rule_four_sends_the_analyst_to_that_section(self):
        self.assertIn("the consent record below", squash(read(ANALYST)),
                      "rule 4 is where the static-vs-executing decision is made; "
                      "it must point at the definition")


if __name__ == "__main__":
    unittest.main()
