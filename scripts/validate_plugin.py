#!/usr/bin/env python3
"""Structurally validate this plugin before anyone installs it.

`claude plugin validate --strict` has now been run against this repo for the
first time, and it passes -- but it is NOT a superset of this script and must
not be read as one. What it validated was the marketplace manifest. It said
nothing about the other 25 components this script checks: no agent's
frontmatter, no skill's name-to-directory match, no hooks file shape, no
context budget, and nothing at all about whether a component's bytes could be
read. Where the two overlap, the CLI is authoritative and this is the stand-in
that runs in CI where the CLI is absent. Where they do not overlap -- which is
nearly everywhere below -- this script is the only check there is.

Failing early here is cheap. A plugin that fails to load gives the user an
"Errors" tab and no explanation, which is the worst first impression available.

Standard library only.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys

# From the published manifest reference. A name that passes as one of
# Anthropic's own is rejected outright by the loader, so catch it here.
RESERVED_PREFIXES = ("claude-", "anthropic-", "anthropics-", "cc-plugin-", "claudeai-")
RESERVED_EXACT = {"claude", "anthropic", "anthropics", "claude-code", "claude-mods",
                  "inline", "builtin", "skills-dir", "synced", "claude-plugin-test",
                  "npm", "pip", "uv", "cargo", "github", "gh"}
VALID_TOOLS = {"Read", "Grep", "Glob", "Bash", "Write", "Edit", "WebSearch",
               "WebFetch", "Task", "NotebookEdit"}
VALID_MODELS = {"opus", "sonnet", "haiku", "fable", "inherit"}

# An agent or skill whose body is this short is almost certainly a stub. The
# number is a smell threshold, not a quality measure.
MIN_BODY_BYTES = 400


class Report:
    def __init__(self) -> None:
        self.errors: list[str] = []
        self.warnings: list[str] = []
        # Components we have no verdict about. "I could not read it" is not
        # the same statement as "it is wrong", and neither is the same as
        # "it is fine" -- a validator that blurs those three has told you
        # nothing while sounding like it told you something.
        self.unvalidated: list[str] = []
        self.checked = 0
        # Budget coverage. "0 warnings" answers "did anything breach a
        # ceiling", and on its own it reads as "everything fits" -- which is
        # a different claim, and a false one for any file no ceiling applies
        # to. These two lists let the run say how many files it actually
        # weighed, so a zero is interpretable instead of merely reassuring.
        self.budgeted: list[str] = []
        self.unbudgeted: list[str] = []
        # Set when a directory could not be listed while enumerating the
        # above: the counts are then floors, not totals, and must say so.
        self.budget_scan_incomplete = False

    def error(self, where: str, message: str) -> None:
        self.errors.append(f"{where}: {message}")

    def warn(self, where: str, message: str) -> None:
        self.warnings.append(f"{where}: {message}")

    def cannot_validate(self, where: str, message: str) -> None:
        """No verdict for this component, and that is itself a failure.

        Recorded in errors as well, so the run fails exactly as hard as it
        would for a component known to be broken: an unknown verdict that
        exits 0 is how a broken component ships.
        """
        entry = f"{where}: {message}"
        self.unvalidated.append(entry)
        self.errors.append(entry)


class Unreadable(Exception):
    """The bytes of a component could not be obtained at all.

    Distinct from "the bytes were obtained and are wrong": raised only by the
    open() in read_text(), and always turned into a per-component
    cannot_validate() by whoever asked for the file.
    """


def read_text_sniffed(path: str) -> tuple[str, str]:
    """Read a component file, and say which encoding that took.

    A UTF-16 or latin-1 markdown file is a real thing to find in a checkout.
    `open(..., encoding="utf-8").read()` raises UnicodeDecodeError out of
    validate(), which aborts the whole validation and reports nothing about
    the components that were fine. utf-8-sig drops a BOM, errors="replace"
    keeps the undecodable bytes visible as U+FFFD, and whatever is wrong with
    the file then shows up as an error about that file alone.

    The open() itself is the other half. A component that is a directory, a
    broken symlink, a symlink loop or simply mode 000 raised OSError straight
    out of whichever check asked for it -- and for check_context_budget() that
    one file cost every other component its budget verdict. Callers catch
    Unreadable per file and carry on to the next one.

    The encoding comes back with the text because "we decoded it" and "the
    loader will accept it" are two different claims, and only the first one is
    ours to make. See the note in check_markdown_component().
    """
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except OSError as exc:
        raise Unreadable(str(exc)) from exc
    if raw.startswith(b"\xff\xfe"):
        encoding, text = "UTF-16 LE", raw.decode("utf-16", errors="replace")
    elif raw.startswith(b"\xfe\xff"):
        encoding, text = "UTF-16 BE", raw.decode("utf-16", errors="replace")
    elif raw.startswith(b"\xef\xbb\xbf"):
        # A UTF-8 BOM is explicitly tolerated: load_json() already documents
        # that the Claude CLI accepts one on plugin.json, so a file that only
        # differs by a BOM is UTF-8 as far as this validator is concerned.
        encoding, text = "UTF-8", raw.decode("utf-8-sig", errors="replace")
    else:
        encoding, text = "UTF-8", raw.decode("utf-8-sig", errors="replace")
    # Reading bytes loses universal-newline translation, and split_frontmatter()
    # matches a literal "---\n". A Windows checkout must validate identically.
    return text.replace("\r\n", "\n").replace("\r", "\n"), encoding


def read_text(path: str) -> str:
    """read_text_sniffed() for the callers that do not care how it decoded."""
    return read_text_sniffed(path)[0]


def load_json(path: str, where: str, r: Report) -> dict | None:
    """Load a manifest, or report why it could not be used and return None.

    utf-8-sig because the Claude CLI loader accepts a BOM on plugin.json: a
    plugin that installs correctly must not fail CI here. The type check
    because every caller then does `data.get(...)`, and a JSON array would
    otherwise raise AttributeError out of validate().
    """
    try:
        with open(path, "rb") as fh:
            data = json.loads(fh.read().decode("utf-8-sig"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        r.error(where, f"malformed: {exc}")
        return None
    except OSError as exc:
        r.error(where, f"could not be read: {exc}")
        return None
    if not isinstance(data, dict):
        r.error(where, f"top level is a JSON {type(data).__name__}, not an object; "
                       "wrap the manifest fields in { }")
        return None
    return data


def split_frontmatter(text: str) -> tuple[str, str] | None:
    """Return (frontmatter, body), or None when the file has no usable block.

    Deliberately not a YAML parser: PyYAML is not in the standard library, and
    the fields we care about are flat scalars. A file complex enough to defeat
    this is a file that should be simplified.
    """
    if not text.startswith("---\n"):
        return None
    end = text.find("\n---\n", 4)
    if end == -1:
        return None
    return text[4:end], text[end + 5:]


def field(frontmatter: str, name: str) -> str | None:
    m = re.search(rf"^{re.escape(name)}:\s*(.+?)\s*$", frontmatter, re.M)
    return m.group(1) if m else None


def check_plugin_json(root: str, r: Report) -> str | None:
    path = os.path.join(root, ".claude-plugin", "plugin.json")
    if not os.path.exists(path):
        r.warn("plugin.json", "absent; components load from the standard layout")
        return None
    data = load_json(path, "plugin.json", r)
    if data is None:
        return None

    r.checked += 1
    name = data.get("name")
    if not name:
        r.error("plugin.json", "'name' is required")
        return None
    if not isinstance(name, str):
        # Every check below is a string operation. Say what the field is
        # instead of raising AttributeError on it.
        r.error("plugin.json",
                f"'name' must be a string, not a {type(name).__name__}: {name!r}")
        return None

    low = name.lower()
    if low.startswith(RESERVED_PREFIXES) or low in RESERVED_EXACT:
        r.error("plugin.json", f"name {name!r} is reserved and will be refused")
    elif re.search(r"\b(claude|anthropic|anthropics)\b", low):
        r.warn("plugin.json", f"name {name!r} reads as one of Anthropic's own")
    if not re.match(r"^[a-z0-9]+(-[a-z0-9]+)*$", name):
        r.warn("plugin.json", f"name {name!r} is not kebab-case")

    for optional in ("version", "description", "author"):
        if not data.get(optional):
            r.warn("plugin.json", f"no {optional}; --strict will fail on this")
    return name


def check_marketplace(root: str, plugin_name: str | None, r: Report) -> None:
    path = os.path.join(root, ".claude-plugin", "marketplace.json")
    if not os.path.exists(path):
        r.warn("marketplace.json", "absent; the plugin cannot be installed by name")
        return
    data = load_json(path, "marketplace.json", r)
    if data is None:
        return

    r.checked += 1
    for required in ("name", "owner", "plugins"):
        if required not in data:
            r.error("marketplace.json", f"'{required}' is required")
    if not isinstance(data.get("owner"), dict) or not data["owner"].get("name"):
        r.error("marketplace.json", "'owner.name' is required")

    name = data.get("name", "")
    if not isinstance(name, str):
        # re.match() on a non-string raises TypeError; the field is simply
        # the wrong type, and that is what the user needs told.
        r.error("marketplace.json",
                f"'name' must be a string, not a {type(name).__name__}: {name!r}")
        name = ""
    if name and not re.match(r"^[A-Za-z0-9][A-Za-z0-9._-]*$", name):
        r.error("marketplace.json",
                f"name {name!r} may use only letters, digits, '.', '_', '-' "
                "and must start with a letter or digit")
    if name.lower().startswith(RESERVED_PREFIXES) or name.lower() in RESERVED_EXACT:
        r.error("marketplace.json", f"name {name!r} is reserved")

    plugins = data.get("plugins", []) or []
    if not isinstance(plugins, list):
        r.error("marketplace.json",
                f"'plugins' must be a list, not a {type(plugins).__name__}")
        plugins = []

    seen: set[str] = set()
    for i, entry in enumerate(plugins):
        where = f"marketplace.json plugins[{i}]"
        if not isinstance(entry, dict):
            r.error(where, "entry is not an object"); continue
        entry_name = entry.get("name")
        if not entry_name:
            r.error(where, "'name' is required")
        elif not isinstance(entry_name, str):
            # `entry["name"] in seen` raised TypeError on an unhashable name
            # (a JSON array or object), and that one entry cost every later
            # entry its check. The field is simply the wrong type; say so.
            r.error(where, f"'name' must be a string, not a "
                           f"{type(entry_name).__name__}: {entry_name!r}")
        elif entry_name in seen:
            r.error(where, f"duplicate plugin name {entry_name!r}")
        else:
            seen.add(entry_name)

        source = entry.get("source")
        if source is None:
            r.error(where, "'source' is required")
        elif isinstance(source, str):
            # A relative path must start with './', except '.' for the root.
            if source != "." and not source.startswith("./"):
                r.error(where, f"source {source!r} must be '.' or start with './'")
            if ".." in source:
                r.error(where, "source escapes the marketplace root")
            target = os.path.join(root, source)
            if not os.path.isdir(target):
                r.error(where, f"source path does not exist: {source}")
        elif isinstance(source, dict):
            if not source.get("source"):
                r.error(where, "object source needs a 'source' type")

    if plugin_name and seen and plugin_name not in seen:
        r.warn("marketplace.json",
               f"no entry named {plugin_name!r}; users install by the entry name")


def check_markdown_component(path: str, expected_name: str, kind: str, r: Report) -> None:
    try:
        text, encoding = read_text_sniffed(path)
    except Unreadable as exc:
        r.cannot_validate(path, f"could not be read ({exc}); this component has "
                                "no verdict — every other component was still checked")
        return
    if encoding != "UTF-8":
        # WHAT THIS VALIDATOR GUARANTEES ABOUT ENCODING, stated so the limit is
        # explicit rather than accidental: it accepts UTF-8 (BOM or not) and
        # BOM-marked UTF-16, decodes both, and judges the decoded text. A
        # BOM-less UTF-16 file is not accepted -- it decodes to NUL-separated
        # ASCII, fails to start with "---\n", and is reported as missing
        # frontmatter. That is a claim about THIS program and nothing more. The
        # loader that will actually run the plugin has never been measured on a
        # non-UTF-8 component. `claude plugin validate --strict` HAS now been
        # run on this repo and passes, and that changes nothing here: it
        # validates the marketplace manifest, so it has never opened an agent
        # or a skill, let alone judged one's encoding.
        #
        # WHAT WOULD SETTLE IT -- the CLI run is necessary but not sufficient,
        # because a passing `validate` is not evidence the component LOADS:
        #     printf '---\nname: probe\ndescription: >\n  encoding probe\n---\n\nbody\n' \
        #       | iconv -f UTF-8 -t UTF-16 > agents/probe.md
        #     claude plugin validate --strict .    # then: is `probe` dispatchable?
        # If the loader accepts it, delete this warning; it is then noise.
        # If the loader refuses it, this becomes r.error() and the message
        # stops hedging. Until one of those two runs happens a warning is the
        # only honest verdict available: an error asserts knowledge nobody here
        # has, and silence asserts the opposite just as confidently.
        #
        # On warning fatigue: measured at 0 of 26 components on this repo, and a
        # component is non-UTF-8 only by accident -- a Windows editor saving as
        # "Unicode". A rule that fires on nothing legitimate is not fatigue.
        r.warn(path, f"is {encoding}, not UTF-8; it was decoded and checked, but "
                     "whether the real plugin loader accepts this encoding has "
                     "never been measured -- re-save it as UTF-8 unless you have "
                     "verified that it loads")
    if "\ufffd" in text:
        # errors="replace" is what keeps one stray byte from costing the whole
        # file, but a silent substitution is a file validated as something
        # other than what is on disk. The loader will read the real bytes.
        r.error(path, "contains bytes that are not valid UTF-8 (shown as U+FFFD); "
                      "it was checked as the replaced text, not as written")
    parts = split_frontmatter(text)
    if parts is None:
        r.error(path, "missing or unterminated YAML frontmatter")
        return
    fm, body = parts
    r.checked += 1

    name = field(fm, "name")
    if not name:
        r.error(path, "missing 'name'")
    elif name != expected_name:
        r.error(path, f"name {name!r} does not match {expected_name!r}")

    if not re.search(r"^description:\s*(>|\|)", fm, re.M):
        if field(fm, "description"):
            r.warn(path, "description is a single line; a folded '>' block reads better")
        else:
            r.error(path, "missing 'description' -- this is the routing signal")

    if kind == "agent":
        tools = field(fm, "tools")
        if not tools:
            r.warn(path, "no 'tools': the agent inherits everything, including Write")
        else:
            unknown = [t.strip() for t in tools.split(",")
                       if t.strip() and t.strip() not in VALID_TOOLS]
            if unknown:
                r.error(path, f"unknown tools {unknown}")
        model = field(fm, "model")
        if model and model not in VALID_MODELS:
            r.error(path, f"unknown model {model!r}")

    if len(body.strip()) < MIN_BODY_BYTES:
        r.warn(path, f"body is only {len(body.strip())} bytes; likely a stub")


def check_hooks(root: str, r: Report) -> None:
    path = os.path.join(root, "hooks", "hooks.json")
    # lexists, not exists: os.path.exists() follows the link and answers False
    # for a dangling symlink or a symlink loop, so a hooks.json that will
    # certainly fail to load was indistinguishable from a plugin that ships no
    # hooks at all -- the check returned here and the run passed in silence.
    # The open() below turns both into cannot_validate, which is the truth.
    if not os.path.lexists(path):
        return
    try:
        with open(path, "rb") as fh:
            data = json.loads(fh.read().decode("utf-8-sig"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        r.error("hooks/hooks.json", f"malformed: {exc}")
        return
    except OSError as exc:
        # Unreadable, or a directory of that name: the file exists as far as
        # os.path.exists is concerned, so this is reached in a real checkout.
        r.cannot_validate("hooks/hooks.json",
                          f"could not be read ({exc}); the hooks have no verdict")
        return
    r.checked += 1
    # A hooks FILE needs the top-level "hooks" wrapper; only an inline object
    # in plugin.json omits it. Without the wrapper the file silently fails.
    # A scalar or a list has no wrapper either -- and `"hooks" not in 5`
    # raises TypeError -- so the type check comes first and the advice is the
    # same one the user needs in both cases.
    if not isinstance(data, dict) or "hooks" not in data:
        r.error("hooks/hooks.json",
                "needs a top-level 'hooks' key wrapping the event map, "
                "or it will not load")
        return
    # HOW DEEP THIS GOES, AND WHY IT STOPS THERE. Checking only that the key
    # exists passed `{"hooks": 7}` -- a file that cannot load, reported with
    # the same words as a working one. One level further is settled by the
    # JSON type alone: an event map is a mapping of event name to hooks, and
    # an int, string, list or null is not a mapping in any version of that
    # format, so this is a fact about JSON rather than a guess about the
    # loader.
    #
    # The next level down is NOT settled, and that is where this stops.
    # Validating event NAMES would need a list of them, and the nine
    # hooks.json files shipped by plugins on this machine between them use
    # SessionStart, UserPromptSubmit, UserPromptExpansion, PreToolUse,
    # PostToolUse, PostToolUseFailure, PermissionRequest, Stop and
    # StopFailure -- more than the names this script could confidently
    # hardcode, and a hardcoded list would reject a legitimate plugin the day
    # a tenth name ships. Validating each event's VALUE would mean asserting
    # that the loader rejects a shape nobody here has run it on: `claude
    # plugin validate --strict` passes on this repo but validates the
    # marketplace manifest only, so it is no oracle for hooks. That is full
    # schema validation, it duplicates the loader, and it is how a check
    # earns false positives. An empty map, `{"hooks": {}}`, is left silent
    # for the same reason: it loads, it simply does nothing, and the
    # validator has no way to know that was not deliberate.
    events = data["hooks"]
    if not isinstance(events, dict):
        r.error("hooks/hooks.json",
                "'hooks' must be an object mapping event names to their hooks; "
                f"this one is {type(events).__name__}: {events!r}, which cannot "
                "load. Only that shape is checked here -- the event names and "
                "their contents are the loader's business, and this script is "
                "not a schema for it")


def check_layout(root: str, r: Report) -> None:
    """A CLAUDE.md at the plugin root is only a mistake in a packaged plugin.

    The same file means two different things depending on who opens the tree.
    Installed, a plugin's CLAUDE.md is never loaded, so instructions put there
    reach nobody -- that is the warning. Opened as a development checkout, it
    IS loaded, and it is how a fresh session picks up how to work on the repo.
    Warning unconditionally fails --strict for a file doing exactly its job,
    so the check asks which tree this is: a tests/ or .git/ directory means a
    checkout somebody develops in. Do not "simplify" this back to a bare
    os.path.exists -- that is the broken version.
    """
    if os.path.exists(os.path.join(root, "CLAUDE.md")):
        developed_here = any(os.path.isdir(os.path.join(root, d))
                             for d in ("tests", ".git"))
        if not developed_here:
            r.warn("CLAUDE.md", "at the plugin root this is not loaded as context; "
                                "put instructions in a skill instead")
    for component in ("agents", "skills"):
        if not os.path.isdir(os.path.join(root, component)):
            r.warn(component, "directory absent")

    # EMPTY IS LEGITIMATE; UNLISTABLE NEVER IS, and until this probe existed
    # the two were the same output. glob.glob() swallows the error from a
    # directory it cannot list and returns [], so a mode-000 agents/ made
    # every agent disappear: measured on this repo as "Validation passed: 19
    # component(s) checked, 0 warning(s), 0 could not be validated", which is
    # byte-for-byte what an agents/ emptied of its seven files prints. A
    # plugin that ships no agents is a real plugin and must stay silent. A
    # plugin whose agents/ cannot be opened is not a plugin with no agents --
    # it is seven components with no verdict, and the loader will be no more
    # able to read them than this was.
    #
    # One listdir, one directory: skills/ is deliberately not probed here
    # because check_skill_directories() has to list it anyway and reports its
    # own failure, and saying it twice is noise. Zero false-positive risk --
    # the only tree this fires on is one where the bytes of a shipped
    # component are unreachable.
    agents_dir = os.path.join(root, "agents")
    if os.path.isdir(agents_dir):
        try:
            os.listdir(agents_dir)
        except OSError as exc:
            r.cannot_validate("agents", f"directory could not be listed ({exc}); "
                                        "no agent in it was enumerated, so none of "
                                        "them has a verdict and none is counted "
                                        "below -- this is not the same as a plugin "
                                        "that ships no agents")


# --------------------------------------------------------------------------
# context budget
# --------------------------------------------------------------------------
#
# Disk size is free; context is not. Every file below is loaded into a context
# window when its component runs, and it costs tokens on every request that
# touches it. These ceilings exist so the plugin stays light as it grows --
# a limit nobody checks is a limit that drifts.
#
# Roughly 4 characters per token. The numbers are generous enough not to
# punish a thorough agent and tight enough to catch a file turning into an
# essay.
BUDGETS = {
    "agents":           3_500,   # an agent's whole system prompt
    "skills":           3_000,   # loaded when the skill is invoked
    "templates/agents": 1_600,   # the generated agent, per target repo
    "report-rules":     1_200,   # RULES.md: loaded by most skills

    # Per-file ceilings, keyed by path relative to the plugin root. Looked up
    # before the group ceiling above, so one file can differ from its group
    # without moving everybody else's limit.
    #
    # ts-qa-gatekeeper.md.tmpl: 1,800 rather than the 1,600 the other three
    # templates keep. This is not slack, it is a measured floor. A dedup pass
    # over that file recovered 28 -> 65 tokens of headroom without dropping a
    # single rule, and that is as far as it goes. The usual lever -- point at
    # the canonical copy instead of repeating it -- is unavailable here twice
    # over. A .tmpl ships into a *user's* repository, where
    # ${CLAUDE_PLUGIN_ROOT} does not expand; and skills/start/SKILL.md step 9
    # generates only the agents the detected stack needs, so the obvious
    # target (the ~90-token flake table it shares with ts-debugger.md.tmpl)
    # may not exist in that repo at all. A dangling pointer there is strictly
    # worse than 90 tokens of table. A template is self-contained by design,
    # so its duplication has, almost by definition, no reachable canonical
    # home.
    #
    # What this one file has to carry inside its ceiling: frontmatter, a
    # regeneration warning, four stack-specific commands, six non-negotiables,
    # a six-step verification procedure including the flake matrix, the
    # verdict format with a worked example, and the report contract -- and it
    # is the canonical home among the generated agents for the 5-run bar that
    # the other two templates defer to. Those three sit at 456, 593 and 137
    # tokens of headroom because they legitimately delegate more. 1,600 is the
    # right ceiling for the average generated agent and the wrong one for this
    # one. Raise the default instead and the other three stop being held to
    # anything.
    "templates/agents/ts-qa-gatekeeper.md.tmpl": 1_800,
}

# Files deliberately outside the budget, with the reason. Each must be
# something a human reads, or something loaded at most once per onboarding.
#
# Keys are paths relative to the plugin root, and check_exemptions_resolve()
# below holds them to that: an entry naming a file nobody wrote exempts
# nothing while reading like a decision somebody made.
#
# AN ENTRY MUST ALSO BE REACHABLE, which is a second and stricter thing. Two
# sites consult this table and only two: the `rel in EXEMPT` skip in
# check_context_budget(), which sees only what the four budget globs match,
# and the `rel not in EXEMPT` filter in record_budget_coverage(), which sees
# only what lives under COMPONENT_DIRS. A key outside both is dead config --
# it exempts nothing, changes no output, and still reads as a weighed
# decision. `docs/RESEARCH.md` sat here from the initial commit and was never
# reachable by either site: docs/ is not globbed for a budget and is not a
# COMPONENT_DIR, because it is developer documentation that is never loaded
# into a context window. Nothing had ever put it *inside* a budget, so
# exempting it from one was a no-op, and it was removed rather than made
# reachable -- adding docs/ to COMPONENT_DIRS would have taught the coverage
# count to enumerate files that are not shipped components at all.
# test_every_shipped_exemption_is_reachable in tests/test_validate_plugin.py
# measures this by deletion and fails if an entry stops mattering again.
EXEMPT = {
    "templates/report/STYLE.md":     "rationale; only the reporter and humans read it",
}


def readable_size(path: str) -> int:
    """Size in bytes of a file whose bytes can actually be obtained.

    The open() is not redundant with the getsize(). getsize() stats, and stat
    needs no read permission and does not care what kind of thing it found:
    a mode-000 file reports its size happily, and a *directory* named
    `foo.tmpl` reports 64 -- which the budget check turned into "~16 tokens",
    a number measured from nothing, about contents that do not exist.
    Reporting a budget verdict on a file whose bytes cannot be obtained is
    the same defect as reporting any other verdict on it: the size is real,
    and it is still not a statement about the file's contents.

    One byte is enough to establish that the bytes are obtainable, and the
    budget needs nothing but the size, so nothing larger is read.
    """
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as fh:
            fh.read(1)
    except OSError as exc:
        # A broken symlink or a symlink loop in templates/ raised out of the
        # budget check and took the budgets of all 26 components with it.
        raise Unreadable(str(exc)) from exc
    return size


def estimate_tokens(path: str) -> int:
    """Roughly 4 characters per token. Raises Unreadable, never OSError."""
    return readable_size(path) // 4


def check_context_budget(root: str, r: Report) -> None:
    """Flag any loadable file that has grown past its ceiling.

    Every loop below continues past a file it cannot read. The point of this
    check is the *set* of components that are over budget; losing the set
    because one entry is a dangling symlink is the failure this exists to
    avoid.

    What this check claims, and the limit of it: for `templates/agents/*.tmpl`
    and `templates/report/RULES.md` this is the ONLY check in the whole
    program -- nothing else opens them, and they are not counted in
    `Report.checked`. So a passing run says of a template exactly two things:
    its bytes could be obtained, and there are not too many of them. It says
    nothing about placeholders, frontmatter or whether the file is coherent.
    Both halves are load-bearing: a size alone is not a verdict on contents,
    and until estimate_tokens() opened the file a mode-000 template produced
    a clean "Validation passed" about a file nobody had read.
    """
    # The fourth field is whether this is the ONLY check in the program that
    # opens these files. It decides what the messages below are allowed to
    # claim: for an agent, check_markdown_component() and the stack-playbook
    # scan both read it, and saying "nothing else opened it" about an agent
    # would be a confident false statement of exactly the kind this validator
    # exists to stop printing.
    checks = [
        (os.path.join(root, "agents", "*.md"), BUDGETS["agents"], "agent", False),
        (os.path.join(root, "skills", "*", "SKILL.md"),
         BUDGETS["skills"], "skill", False),
        (os.path.join(root, "templates", "agents", "*.tmpl"),
         BUDGETS["templates/agents"], "agent template", True),
        (os.path.join(root, "templates", "report", "RULES.md"),
         BUDGETS["report-rules"], "report rules", True),
    ]
    # EMPTY IS LEGITIMATE; UNLISTABLE NEVER IS -- the same rule check_layout()
    # applies to agents/, applied to the directories the globs above depend
    # on. glob.glob() returns [] for a directory it cannot list exactly as it
    # does for one that is empty, so a locked templates/agents/ deleted all
    # four agent templates from the run and said nothing: measured on this
    # repo as "Validation passed: 26 component(s) checked, 0 warning(s), 0
    # could not be validated" with `chmod 000 templates/agents`, which is
    # byte-for-byte what a tree whose templates are all fine prints. For a
    # template this check is the only one there is, so losing the glob loses
    # the component entirely rather than merely its budget.
    #
    # Only these two are probed. agents/ is probed by check_layout() and
    # skills/ by check_skill_directories(), each of which has to list its
    # directory anyway and reports its own failure; probing them again would
    # report one locked directory twice and inflate the count of components
    # with no verdict. One listdir, one directory.
    #
    # An ABSENT directory stays silent, and that is the shape this must never
    # fire on: a plugin that ships no templates is a real plugin. The only
    # tree this fires on is one where the bytes of a shipped component are
    # unreachable, so the false-positive risk is zero.
    for rel_dir, label in (("templates/agents", "agent template"),
                           ("templates/report", "report rules file")):
        probe = os.path.join(root, *rel_dir.split("/"))
        if not os.path.isdir(probe):
            continue
        try:
            os.listdir(probe)
        except OSError as exc:
            r.cannot_validate(rel_dir, f"directory could not be listed ({exc}); no "
                                       f"{label} in it was enumerated, so none of "
                                       "them has a verdict, none is counted below "
                                       "and nothing else in this run opens these "
                                       "files -- this is not the same as a plugin "
                                       f"that ships no {label}s")

    for pattern, group_ceiling, label, sole_check in checks:
        for path in sorted(glob.glob(pattern)):
            rel = os.path.relpath(path, root)
            if rel in EXEMPT:
                continue
            # A per-file entry in BUDGETS overrides its group. Bound inside
            # the inner loop and never assigned to `group_ceiling`, so one
            # file's override cannot leak onto the files sorted after it --
            # which is exactly how a single exception silently becomes the
            # new default for a whole directory.
            ceiling = BUDGETS.get(rel, group_ceiling)
            try:
                size = readable_size(path)
            except Unreadable as exc:
                alone = (" and nothing else in this run opened it"
                         if sole_check else "")
                r.cannot_validate(rel, f"could not be read ({exc}); this {label} "
                                       f"has no context-budget verdict{alone}")
                continue
            # Weighed: its size is known. That includes the empty and the
            # over-budget cases below, both of which got a real verdict.
            r.budgeted.append(rel)
            if size == 0 and sole_check:
                # Readable, and empty. The budget is satisfied in the most
                # useless way available: an empty file is inside every ceiling
                # there is while being a component that does nothing. "Under
                # budget" is not the same statement as "fit to load", and here
                # this is the only check there is to say it with. An empty
                # agent or skill is already an error about its frontmatter, so
                # saying it again there would be noise, not honesty.
                r.warn(rel, f"is empty; it is inside the {ceiling:,}-token "
                            f"budget because there is nothing in it, which is "
                            f"not the same as a usable {label}")
                continue
            tokens = size // 4
            if tokens > ceiling:
                r.warn(rel, f"~{tokens:,} tokens of context for a {label}, "
                            f"over the {ceiling:,} budget -- trim it or move the "
                            f"detail into a file that is read on demand")

    # A stack playbook is large on purpose, so the rule is not its size but
    # who reads it: /start and the analyst, once, at onboarding. Everything
    # afterwards reads the small .testing/profile.json instead. An agent that
    # reads a playbook at task time pays ~7,000 tokens to look up one command.
    for path in sorted(glob.glob(os.path.join(root, "agents", "*.md"))):
        name = os.path.basename(path)
        if name == "project-analyst.md":
            continue
        rel = os.path.relpath(path, root)
        try:
            text = read_text(path)
        except Unreadable as exc:
            r.cannot_validate(rel, f"could not be read ({exc}); whether it reads a "
                                   "stack playbook at task time is unknown")
            continue
        if "stacks/" in text:
            r.warn(rel,
                   "reads a stack playbook at task time; it should read "
                   ".testing/profile.json, which /start already distilled")


def check_exemptions_resolve(root: str, r: Report) -> None:
    """An exemption for a file nobody wrote exempts nothing.

    EXEMPT reads as a decision: somebody weighed this file and chose to keep
    it off the budget, and wrote down why. An entry whose path does not exist
    reads exactly the same and does nothing at all -- a silent no-op wearing
    the costume of a deliberate choice. Two of the four entries here were
    `templates/agents/README.md` and `examples/fixtures/README.md`, neither of
    which has ever existed in this repository, and nothing said so because
    nothing looked.

    Deliberately narrow, on both axes, because a budget check above roughly
    10% false positives gets switched off and takes the real ceilings with it:

    * Only non-existence is a finding. An entry that exists but that no
      current glob consults is dead in a *recoverable* way -- add the glob and
      the decision takes effect -- and warning about it would be a finding
      against a correct decision. A path that is not there cannot become
      reachable by any glob.
    * An entry is skipped when the directory that would contain it is itself
      absent from this root. This validator runs against trees that are not
      this repository -- its own tests build a dozen of them -- and "this tree
      has no docs/ at all" is a different tree, not a stale exemption. The
      cost of that scoping is stated rather than hidden: in such a root this
      check has no verdict about that entry, which is not the same as having
      cleared it.
    """
    for rel, reason in sorted(EXEMPT.items()):
        target = os.path.join(root, *rel.split("/"))
        parent = os.path.dirname(target)
        if not os.path.isdir(parent):
            continue
        if not os.path.exists(target):
            # "The file is not there" and "the directory holding it could not
            # be opened" are different statements, and os.path.exists()
            # answers False to both: a mode-000 templates/report/ made this
            # tell the reader to delete a live exemption because its file was
            # missing, when nothing had been able to look. The locked
            # directory already gets a no-verdict report of its own in
            # check_context_budget(); anything said about the entry here
            # would be a guess wearing the costume of a finding.
            try:
                os.listdir(parent)
            except OSError:
                continue
            r.warn("EXEMPT",
                   f"{rel} is exempted from the context budget "
                   f"(\"{reason}\"), but {os.path.dirname(rel)}/ exists and "
                   f"that file does not; the entry exempts nothing while "
                   f"reading as a deliberate decision -- delete it, or create "
                   f"the file it was reserving")


# Directories that hold shipped plugin components. Walked only to answer
# "what did the budget check not look at" -- see record_budget_coverage().
COMPONENT_DIRS = ("agents", "skills", "templates", "stacks", "hooks")


def record_budget_coverage(root: str, r: Report) -> None:
    """Enumerate the files under COMPONENT_DIRS that no ceiling applies to.

    `0 warning(s)` answers one question -- did any file breach its ceiling --
    and is routinely read as the answer to a bigger one: does everything fit.
    They differ for every file that has no ceiling, and there are more of
    those than the BUDGETS table suggests. A count of files weighed makes the
    zero interpretable; without it, "nothing warned" means both "checked and
    fine" and "never checked", which is the distinction this whole program
    exists to preserve.

    This is deliberately a *count*, not a finding. Most of these files have
    no ceiling on purpose -- a stack playbook is large by design, a CI
    workflow is executed by a runner that has no context window at all -- so
    warning about them would be a false positive, and a check above ~10%
    false positives gets switched off, taking the real rules with it.
    """
    budgeted = set(r.budgeted)
    unlisted: list[str] = []

    def unlistable(exc: OSError) -> None:
        # os.walk swallows a directory it cannot list and walks on, which
        # would silently shrink the total and make the printed count a
        # confident understatement. Record it instead.
        r.budget_scan_incomplete = True
        name = getattr(exc, "filename", None)
        unlisted.append(os.path.relpath(name, root) if name else "a directory")

    for component in COMPONENT_DIRS:
        base = os.path.join(root, component)
        if not os.path.isdir(base):
            continue
        for dirpath, _dirs, names in os.walk(base, onerror=unlistable):
            for name in sorted(names):
                rel = os.path.relpath(os.path.join(dirpath, name), root)
                if rel not in budgeted and rel not in EXEMPT:
                    r.unbudgeted.append(rel)
    r.unbudgeted.sort()
    if unlisted:
        # A finding, not just a hedge on the printed count. This used to be a
        # bare print() in main() that began with the word "warning:" and was
        # not in r.warnings: it was never counted in the "N warning(s)"
        # summary and --strict passed straight over it, so a directory of
        # shipped components that could not be opened exited 0. A line that
        # calls itself a warning and does not behave like one is the same
        # defect as silence, wearing a label.
        r.warn("budget coverage",
               "could not list " + ", ".join(sorted(set(unlisted))) +
               f" under {'/, '.join(COMPONENT_DIRS)}/, so the count of files "
               "with no ceiling is a floor, not a total -- whatever is inside "
               "was neither weighed nor counted, and this is not the same as "
               "a directory that is empty")


def guarded(where: str, r: Report, fn, *args) -> object:
    """Run one check; turn anything it did not expect into an error about it.

    The whole value of a validator is telling you everything that is wrong in
    one run. A component this script has not been taught about must therefore
    cost you that component's verdict and nothing more -- never the verdicts
    of the components that would have been fine.
    """
    try:
        return fn(*args)
    except Exception as exc:  # noqa: BLE001 - deliberately broad; see above
        r.cannot_validate(where, f"could not be checked ({type(exc).__name__}: "
                                 f"{exc}); the rest of the plugin was still validated")
        return None


def check_skill_directories(root: str, r: Report) -> None:
    """A skills/<name> that is not a loadable skill matches no glob.

    `glob('skills/*/SKILL.md')` is how every other check finds a skill, and a
    skills/<name> it does not match is invisible: the run passes while a skill
    the author believes they shipped will never load. Not finding a component
    is a finding, not a non-event.

    Two shapes miss that glob, and both are reported the same way because the
    consequence is the same. A DIRECTORY with no SKILL.md was already caught.
    A skills/<name> that is a FILE, or a symlink resolving to nothing, was
    not: `not os.path.isdir(sub)` skipped it before the SKILL.md test could
    run, so replacing a skill directory with a file deleted that skill from
    the run in silence -- 26 components checked became 25, with nothing else
    said. It is the same rule with a hole in it rather than a new rule.

    The false-positive question is whether anything legitimate sits directly
    under skills/ that is not a skill directory. Measured, rather than
    assumed: across the 27 plugin skills/ directories installed on this
    machine -- the official Anthropic marketplace plus the synced plugins --
    all 119 entries are directories and all 119 contain a SKILL.md. This repo
    is 16 for 16. Dotfiles are still skipped, because .DS_Store and .gitkeep
    are real and are not components.
    """
    skills_dir = os.path.join(root, "skills")
    if not os.path.isdir(skills_dir):
        return
    try:
        entries = sorted(os.listdir(skills_dir))
    except OSError as exc:
        # Explicit rather than left to guarded(): the backstop would say "could
        # not be checked", which reads as a bug in this script. What happened
        # is that the directory holding every skill could not be opened.
        r.cannot_validate("skills", f"directory could not be listed ({exc}); no "
                                    "skill in it was enumerated, so none of them "
                                    "has a verdict and none is counted below -- "
                                    "this is not the same as a plugin that ships "
                                    "no skills")
        return
    for entry in entries:
        if entry.startswith("."):
            continue
        sub = os.path.join(skills_dir, entry)
        if os.path.isdir(sub):
            if not os.path.isfile(os.path.join(sub, "SKILL.md")):
                r.error(f"skills/{entry}",
                        "has no SKILL.md, so it will not load as a skill and no "
                        "other check in this run looked at it")
            continue
        # Not a directory. Name which shape it is, because the two have
        # different fixes: a file wants turning into a directory, a dangling
        # link wants its target restored.
        shape = ("is a symlink that resolves to nothing"
                 if not os.path.exists(sub) else "is a file, not a directory")
        r.error(f"skills/{entry}",
                f"{shape}; a skill is a directory containing SKILL.md, so it "
                "will not load as a skill and no other check in this run "
                "looked at it")


def validate(root: str) -> Report:
    r = Report()
    plugin_name = guarded("plugin.json", r, check_plugin_json, root, r)
    guarded("marketplace.json", r, check_marketplace, root, plugin_name, r)
    guarded("hooks/hooks.json", r, check_hooks, root, r)
    guarded("layout", r, check_layout, root, r)
    guarded("context budget", r, check_context_budget, root, r)
    guarded("exemptions", r, check_exemptions_resolve, root, r)
    guarded("skills layout", r, check_skill_directories, root, r)
    # After the budget check, so it can see what that check weighed.
    guarded("budget coverage", r, record_budget_coverage, root, r)

    for path in sorted(glob.glob(os.path.join(root, "agents", "*.md"))):
        guarded(path, r, check_markdown_component,
                path, os.path.basename(path)[:-3], "agent", r)
    for path in sorted(glob.glob(os.path.join(root, "skills", "*", "SKILL.md"))):
        guarded(path, r, check_markdown_component,
                path, os.path.basename(os.path.dirname(path)), "skill", r)
    return r


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="validate_plugin.py",
        description="Structurally validate the plugin where the Claude CLI is unavailable.")
    ap.add_argument("root", nargs="?", default=".", help="plugin root")
    ap.add_argument("--strict", action="store_true", help="treat warnings as failures")
    args = ap.parse_args(argv)

    if not os.path.isdir(args.root):
        print(f"error: no such directory: {args.root}", file=sys.stderr)
        return 2

    r = validate(args.root)
    for warning in r.warnings:
        print(f"warning: {warning}", file=sys.stderr)
    # The components with no verdict are listed apart from the ones judged
    # and found wrong. Folded into the error list they read as "this
    # component is broken", when what happened is that nobody looked at it.
    unknown = set(r.unvalidated)
    for error in r.errors:
        if error not in unknown:
            print(f"error:   {error}", file=sys.stderr)
    for item in r.unvalidated:
        print(f"UNKNOWN: {item}", file=sys.stderr)

    # Printed on every run, pass or fail, and kept off the summary line so
    # that line stays quotable. It is what makes "0 warning(s)" readable: a
    # ceiling was applied to this many files and to no others.
    floor = "at least " if r.budget_scan_incomplete else ""
    print(f"Context budgets: {len(r.budgeted)} file(s) weighed against a "
          f"ceiling, {floor}{len(r.unbudgeted)} file(s) under "
          f"{'/, '.join(COMPONENT_DIRS)}/ have no ceiling and were not "
          f"weighed (deliberate; see BUDGETS in {os.path.basename(__file__)}).",
          file=sys.stderr)
    # The "floor, not a total" caveat is a real warning raised by
    # record_budget_coverage() and printed above with the others, not a bare
    # line here: printed here it was uncounted and --strict ignored it.

    if r.errors:
        judged_wrong = len(r.errors) - len(r.unvalidated)
        print(f"\nValidation failed: {judged_wrong} error(s), "
              f"{len(r.unvalidated)} component(s) could not be validated, "
              f"{len(r.warnings)} warning(s), {r.checked} component(s) checked.",
              file=sys.stderr)
        return 1
    if r.warnings and args.strict:
        print(f"\nValidation failed under --strict: {len(r.warnings)} warning(s), "
              f"{len(r.unvalidated)} component(s) could not be validated.",
              file=sys.stderr)
        return 1
    # The zero is said out loud on purpose: "nothing could not be validated"
    # and "nothing was validated" must not both render as silence.
    print(f"Validation passed: {r.checked} component(s) checked, "
          f"{len(r.warnings)} warning(s), "
          f"{len(r.unvalidated)} could not be validated.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
