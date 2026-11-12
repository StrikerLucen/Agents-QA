#!/usr/bin/env python3
"""Structurally validate this plugin before anyone installs it.

`claude plugin validate` is the authoritative check and this does not replace it.
This runs where the Claude CLI is not available -- notably in CI -- and it also
checks things the CLI does not, such as whether an agent's body is substantial
enough to be worth loading, and whether a skill's name matches its directory.

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


def read_text(path: str) -> str:
    """Read any component file without letting its encoding stop the run.

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
    """
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except OSError as exc:
        raise Unreadable(str(exc)) from exc
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        text = raw.decode("utf-16", errors="replace")
    else:
        text = raw.decode("utf-8-sig", errors="replace")
    # Reading bytes loses universal-newline translation, and split_frontmatter()
    # matches a literal "---\n". A Windows checkout must validate identically.
    return text.replace("\r\n", "\n").replace("\r", "\n")


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
        text = read_text(path)
    except Unreadable as exc:
        r.cannot_validate(path, f"could not be read ({exc}); this component has "
                                "no verdict — every other component was still checked")
        return
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
    if not os.path.exists(path):
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
}

# Files deliberately outside the budget, with the reason. Each must be
# something a human reads, or something loaded at most once per onboarding.
EXEMPT = {
    "docs/RESEARCH.md":              "human reference; never loaded at runtime",
    "templates/report/STYLE.md":     "rationale; only the reporter and humans read it",
    "templates/agents/README.md":    "human reference",
    "examples/fixtures/README.md":   "human reference",
}


def estimate_tokens(path: str) -> int:
    try:
        return os.path.getsize(path) // 4
    except OSError as exc:
        # A broken symlink or a symlink loop in templates/ raised out of the
        # budget check and took the budgets of all 26 components with it.
        raise Unreadable(str(exc)) from exc


def check_context_budget(root: str, r: Report) -> None:
    """Flag any loadable file that has grown past its ceiling.

    Every loop below continues past a file it cannot read. The point of this
    check is the *set* of components that are over budget; losing the set
    because one entry is a dangling symlink is the failure this exists to
    avoid.
    """
    checks = [
        (os.path.join(root, "agents", "*.md"), BUDGETS["agents"], "agent"),
        (os.path.join(root, "skills", "*", "SKILL.md"), BUDGETS["skills"], "skill"),
        (os.path.join(root, "templates", "agents", "*.tmpl"),
         BUDGETS["templates/agents"], "agent template"),
        (os.path.join(root, "templates", "report", "RULES.md"),
         BUDGETS["report-rules"], "report rules"),
    ]
    for pattern, ceiling, label in checks:
        for path in sorted(glob.glob(pattern)):
            rel = os.path.relpath(path, root)
            if rel in EXEMPT:
                continue
            try:
                tokens = estimate_tokens(path)
            except Unreadable as exc:
                r.cannot_validate(rel, f"its size could not be read ({exc}); "
                                       f"this {label} has no context-budget verdict")
                continue
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
    """A skills/<name>/ with no SKILL.md matches no glob and so is invisible.

    The run then passes while a skill the author believes they shipped will
    never load. Not finding a component is a finding, not a non-event.
    """
    skills_dir = os.path.join(root, "skills")
    if not os.path.isdir(skills_dir):
        return
    for entry in sorted(os.listdir(skills_dir)):
        sub = os.path.join(skills_dir, entry)
        if not os.path.isdir(sub) or entry.startswith("."):
            continue
        if not os.path.isfile(os.path.join(sub, "SKILL.md")):
            r.error(f"skills/{entry}",
                    "has no SKILL.md, so it will not load as a skill and no "
                    "other check in this run looked at it")


def validate(root: str) -> Report:
    r = Report()
    plugin_name = guarded("plugin.json", r, check_plugin_json, root, r)
    guarded("marketplace.json", r, check_marketplace, root, plugin_name, r)
    guarded("hooks/hooks.json", r, check_hooks, root, r)
    guarded("layout", r, check_layout, root, r)
    guarded("context budget", r, check_context_budget, root, r)
    guarded("skills layout", r, check_skill_directories, root, r)

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
