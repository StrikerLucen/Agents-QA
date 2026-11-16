#!/usr/bin/env python3
"""Measure the project's current state by running it, and report what was found.

Why this file exists: a fresh Claude Code session on another machine has no
memory of how any of this was built. Chat history does not travel; the
repository does. `docs/STATE.md` is the prose handoff, but prose rots. This
script is the live instrument -- it runs the test suite, runs the validator,
counts the tree and reads the review documents, and prints only what it
actually observed.

The one rule it inherits from the rest of the project: never state a number it
did not measure. Where a file is missing or its format has drifted, it says so
in that section rather than guessing, and it never substitutes a remembered
number for an unmeasured one.

Standard library only; Python 3.9+.
"""

from __future__ import annotations

import argparse
import ast
import glob
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone

if __package__ in (None, ""):  # run as a script: scripts/ is already sys.path[0]
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from atomic_io import write_text_atomic  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# A file bigger than this is generated, vendored or binary -- not somewhere a
# human leaves an unfinished marker. Scanning it costs more than it can find.
MAX_SCAN_BYTES = 1 * 1024 * 1024

SCAN_SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv", "venv",
                  "dist", "build", "target", ".testing", "results"}
SCAN_EXTS = {".py", ".md", ".json", ".yml", ".yaml", ".sh", ".tmpl", ".html",
             ".txt", ".toml", ".cfg", ".ts", ".js", ".java", ".kt"}

# The marker words are assembled from halves on purpose: written out whole,
# this scanner would report itself as four unfinished markers on every run,
# and a number that counts the instrument is not a measurement of the project.
MARKERS = tuple(a + b for a, b in (
    ("TO", "DO"), ("FIX", "ME"), ("X", "XX"), ("@unittest.", "expectedFailure"),
))
MARKER_XFAIL = MARKERS[3]

# The one ceiling this script does not set and cannot raise -- the same one
# documented at length in arid_nodes.py and flake_smells.py, which hit it
# first. CPython builds the Python AST objects from the C parse tree with a
# recursive C function, so a deeply NESTED expression costs C stack and past
# some depth `ast.parse` raises `RecursionError`. That depth is NOT
# `sys.getrecursionlimit()`, it moves with the platform and the thread, and on
# this repo's declared floor (CPython 3.9.6, macOS, main thread) there is no
# RecursionError window at all. Those depths were measured for arid_nodes.py,
# by bisecting child processes, and are quoted here rather than re-measured:
# 260,937 nested terms accepted, dead by 262,500 (SIGSEGV, rc -11), with the
# macOS crash report naming "Thread stack size exceeded due to excessive
# recursion". See the block above arid_nodes.AST_TOO_DEEP for the full table.
# So `sys.setrecursionlimit()` and `threading.stack_size()` are
# both the wrong knob here: they would trade a catchable exception for an
# uncatchable crash. Catching RecursionError at the call site needs no depth
# guess, which is why it is the fix.
#
# Residual risk, stated plainly: on an interpreter whose AST builder has no
# counter a deep enough file still kills the process, and nothing here can
# catch that. What this script controls is that where the interpreter DOES
# raise, the state report survives -- and says so. This is the script a fresh
# session runs when nothing else can be trusted; a traceback here leaves the
# reader with no numbers at all, and a quiet fallback leaves them with numbers
# they would wrongly believe.
AST_TOO_DEEP = "ast-nesting-too-deep"
AST_TOO_DEEP_REASON = (
    "ast.parse raised RecursionError: expression nesting is too deep for "
    "CPython's AST builder on this interpreter and stack. No flag raises this "
    "ceiling -- it is the C stack, not sys.setrecursionlimit, and it moves "
    "between platforms. The AST pass did NOT run on this file."
)
AST_TOO_DEEP_LOST = (
    "the decorated test's NAME, and the certainty that each hit is a real "
    "decorator rather than the string quoted in prose or a docstring; a line "
    "scan ran instead, so this file's " + MARKER_XFAIL + " count is a text "
    "match and not a measurement -- it cannot see a decorator reached through "
    "an alias or a line continuation, and it counts one that is only quoted"
)

# The test suite is run exactly the way HANDOFF.md documents it: module names
# taken from tests/test_*.py, with tests/ as the working directory. Discovery
# finds a slightly different set, and two different numbers for "the suite"
# is precisely the confusion this script exists to end.
TEST_RUNNER = r"""
import json, os, sys, unittest

names = sorted(
    f[:-3] for f in os.listdir(".")
    if f.startswith("test_") and f.endswith(".py")
)
loader = unittest.TestLoader()
suite = loader.loadTestsFromNames(names)
result = unittest.TextTestRunner(stream=open(os.devnull, "w"), verbosity=0).run(suite)
print("@@STATE@@" + json.dumps({
    "modules": names,
    "load_errors": [str(e) for e in getattr(loader, "errors", [])],
    "total": result.testsRun,
    "failures": [str(t) for t, _ in result.failures],
    "errors": [str(t) for t, _ in result.errors],
    "skipped": [str(t) for t, _ in result.skipped],
    "expected_failures": [str(t) for t, _ in result.expectedFailures],
    "unexpected_successes": [str(t) for t in result.unexpectedSuccesses],
}))
"""


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def read_text(path: str) -> "str | None":
    """File contents, or None when it is missing or unreadable. Callers report
    the None rather than treating it as empty -- a missing review document and
    a review document with nothing open are not the same state."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return None


def run(cmd: "list[str]", cwd: str, timeout: int) -> dict:
    try:
        proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                              timeout=timeout, check=False)
    except FileNotFoundError as exc:
        return {"ran": False, "reason": f"{exc}"}
    except subprocess.TimeoutExpired:
        return {"ran": False, "reason": f"timed out after {timeout}s"}
    return {"ran": True, "returncode": proc.returncode,
            "stdout": proc.stdout, "stderr": proc.stderr}


def count_glob(pattern: str) -> int:
    return len(glob.glob(os.path.join(ROOT, pattern)))


# --------------------------------------------------------------------------
# sections
# --------------------------------------------------------------------------

def section_git() -> dict:
    out: dict = {"available": False}
    probe = run(["git", "rev-parse", "--is-inside-work-tree"], ROOT, 20)
    if not probe.get("ran") or probe.get("returncode") != 0:
        out["note"] = "not a git repository, or git is unavailable"
        return out
    out["available"] = True

    branch = run(["git", "rev-parse", "--abbrev-ref", "HEAD"], ROOT, 20)
    out["branch"] = branch["stdout"].strip() if branch.get("ran") else None

    status = run(["git", "status", "--porcelain"], ROOT, 30)
    if status.get("ran"):
        dirty = [ln for ln in status["stdout"].splitlines() if ln.strip()]
        out["clean"] = not dirty
        out["dirty_files"] = [ln[3:] for ln in dirty]
    else:
        out["clean"] = None
        out["dirty_files"] = []

    log = run(["git", "log", "-1", "--format=%h%x00%s%x00%ad", "--date=iso"], ROOT, 20)
    if log.get("ran") and log["returncode"] == 0 and log["stdout"].strip():
        sha, _, rest = log["stdout"].strip().partition("\x00")
        subject, _, date = rest.partition("\x00")
        out["last_commit"] = {"sha": sha, "subject": subject, "date": date}
    else:
        out["last_commit"] = None

    remote = run(["git", "remote", "get-url", "origin"], ROOT, 20)
    out["origin"] = remote["stdout"].strip() if remote.get("ran") and remote["returncode"] == 0 else None
    return out


def section_tests(skip: bool) -> dict:
    tests_dir = os.path.join(ROOT, "tests")
    if not os.path.isdir(tests_dir):
        return {"ran": False, "reason": "tests/ does not exist"}
    if skip:
        return {"ran": False, "reason": "skipped (--no-tests)"}

    res = run([sys.executable, "-c", TEST_RUNNER], tests_dir, 900)
    if not res.get("ran"):
        return {"ran": False, "reason": res["reason"]}

    marker = res["stdout"].rfind("@@STATE@@")
    if marker < 0:
        return {"ran": False,
                "reason": "the test runner produced no parseable result",
                "stderr_tail": res["stderr"][-800:]}
    try:
        data = json.loads(res["stdout"][marker + len("@@STATE@@"):])
    except json.JSONDecodeError as exc:
        return {"ran": False, "reason": f"unparseable runner output: {exc}"}

    data["ran"] = True
    data["green"] = not data["failures"] and not data["errors"] and not data["load_errors"]
    return data


def section_validator() -> dict:
    script = os.path.join(ROOT, "scripts", "validate_plugin.py")
    if not os.path.isfile(script):
        return {"ran": False, "reason": "scripts/validate_plugin.py is missing"}
    res = run([sys.executable, script, ".", "--strict"], ROOT, 180)
    if not res.get("ran"):
        return {"ran": False, "reason": res["reason"]}
    lines = [ln for ln in (res["stdout"] + res["stderr"]).splitlines() if ln.strip()]
    return {
        "ran": True,
        "passed": res["returncode"] == 0,
        "returncode": res["returncode"],
        "output": lines,
        "warnings": [ln for ln in lines if "warn" in ln.lower()],
    }


def section_inventory() -> dict:
    fixtures = sorted(
        d for d in glob.glob(os.path.join(ROOT, "examples", "fixtures", "*"))
        if os.path.isdir(d)
    )
    fixture_rows = [
        {"name": os.path.basename(d), "has_git": os.path.exists(os.path.join(d, ".git"))}
        for d in fixtures
    ]
    missing = [f["name"] for f in fixture_rows if not f["has_git"]]
    return {
        "agents": count_glob("agents/*.md"),
        "skills": len([d for d in glob.glob(os.path.join(ROOT, "skills", "*"))
                       if os.path.isfile(os.path.join(d, "SKILL.md"))]),
        "stacks": count_glob("stacks/*.md"),
        "scripts": count_glob("scripts/*.py"),
        "test_files": count_glob("tests/test_*.py"),
        "eval_cases": len([d for d in glob.glob(os.path.join(ROOT, "evals", "*"))
                           if os.path.isfile(os.path.join(d, "case.yaml"))]),
        "agent_templates": count_glob("templates/agents/*.tmpl"),
        "hook_examples": count_glob("hooks/examples/*.json"),
        "fixtures": fixture_rows,
        "fixtures_missing_git": missing,
        "fixture_action": (
            "run `cd examples/fixtures && ./regen_git_history.sh` -- the fixture "
            "git history is NOT committed and several tests depend on it"
            if missing else None
        ),
    }


def _xfail_tests(text: str) -> "tuple[list[dict] | None, str | None]":
    """(hits, why_not) -- hits is (line, test name) for every function actually
    decorated with expectedFailure, via the parser rather than a text match --
    the decorator is also quoted in prose in these files, and a docstring is
    not a defect. hits is None when the file does not parse, so the caller can
    fall back to a line scan.

    `why_not` exists because those refusals are not one thing. A SyntaxError is
    an ordinary, expected state: the file is half-written, the line scan is the
    right answer and saying so on every run would be noise. A RecursionError is
    not: nothing about the file is wrong, the limit is the interpreter's C
    stack (see AST_TOO_DEEP above), it differs per platform, and the caller
    MUST be able to tell the reader that the count for that file came out of a
    text match rather than the parser. Falling back silently there would be the
    same silence-looks-like-success defect this script exists to measure.

    This function never raises. The docstring used to promise that while the
    code only caught SyntaxError and ValueError, so a deeply nested expression
    anywhere in the scanned tree killed section_markers() and with it the whole
    state report.
    """
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return None, "syntax-error"
    except RecursionError:
        # Caught here and NOT re-raised, but the caller is handed the reason so
        # it can name the file in ast_unparsed_files and mark the total as not
        # exact. Catch-and-continue on its own would be silence in a fix's
        # clothes.
        return None, AST_TOO_DEEP
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for dec in node.decorator_list:
            target = dec.func if isinstance(dec, ast.Call) else dec
            name = getattr(target, "attr", None) or getattr(target, "id", None)
            if name == "expectedFailure":
                found.append({"line": dec.lineno, "test": node.name})
    return found, None


def section_markers() -> dict:
    by_file: "dict[str, dict]" = {}
    totals = {m: 0 for m in MARKERS}
    expected_failures: "list[dict]" = []
    ast_unparsed: "list[dict]" = []

    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in SCAN_SKIP_DIRS]
        for name in filenames:
            ext = os.path.splitext(name)[1]
            if ext not in SCAN_EXTS:
                continue
            full = os.path.join(dirpath, name)
            try:
                if os.path.getsize(full) > MAX_SCAN_BYTES:
                    continue
            except OSError:
                continue
            text = read_text(full)
            if text is None:
                continue
            rel = os.path.relpath(full, ROOT)
            counts = {m: 0 for m in MARKERS}

            for line in text.splitlines():
                for marker in MARKERS[:3]:
                    if marker in line:
                        counts[marker] += 1
                        totals[marker] += 1

            xfails, why = _xfail_tests(text) if ext == ".py" else ([], None)
            if xfails is None:  # unparseable python: say what was seen, not what it means
                xfails = [{"line": i + 1, "test": None, "counted_by": "line-scan",
                           "why_unnamed": why}
                          for i, line in enumerate(text.splitlines())
                          if line.lstrip().startswith(MARKER_XFAIL)]
                if why == AST_TOO_DEEP:
                    # A half-written file (syntax-error) is expected and stays
                    # a silent fallback. This is not that: the file is valid
                    # Python the interpreter could not build an AST for, the
                    # count below is a text match, and the reader has to be
                    # able to tell those apart from a parsed, measured count.
                    ast_unparsed.append({
                        "file": rel,
                        "stage": "ast",
                        "fell_back_to": "line-scan",
                        "reason": AST_TOO_DEEP_REASON,
                        "not_measured": AST_TOO_DEEP_LOST,
                        "line_scan_hits": len(xfails),
                    })
            for hit in xfails:
                counts[MARKER_XFAIL] += 1
                totals[MARKER_XFAIL] += 1
                expected_failures.append({"file": rel, **hit})

            if any(counts.values()):
                by_file[rel] = {k: v for k, v in counts.items() if v}

    return {
        "totals": totals,
        # False means at least one .py was counted by the line scanner because
        # its AST could not be built, so `totals` for MARKER_XFAIL is a text
        # match for those files. A consumer that reads `totals` without reading
        # this flag is reading a number nobody measured.
        "totals_exact": not ast_unparsed,
        "by_file": dict(sorted(by_file.items())),
        "expected_failures": expected_failures,
        "ast_unparsed_files": ast_unparsed,
        "files_ast_unparsed": len(ast_unparsed),
    }


def _prompt_review(text: str) -> dict:
    match = re.search(
        r"Counts:\s*\*?\*?\s*(\d+)\s*critical[^.\n]*?(\d+)\s*high[^.\n]*?"
        r"(\d+)\s*medium[^.\n]*?(\d+)\s*low", text, re.IGNORECASE)
    if not match:
        return {"parsed": False,
                "note": "no 'Counts: n critical, n high, n medium, n low' line found"}
    return {
        "parsed": True,
        "counts": {"critical": int(match.group(1)), "high": int(match.group(2)),
                   "medium": int(match.group(3)), "low": int(match.group(4))},
        "meaning": "findings raised by the review, not an open/closed tally",
    }


def _security_review(text: str) -> dict:
    rows = []
    for line in text.splitlines():
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 4 or not re.fullmatch(r"S\d+-\d+", cells[0]):
            continue
        fixed = cells[-1].lower()
        if fixed.startswith("yes"):
            status = "fixed"
        elif fixed.startswith("partial"):
            status = "partial"
        elif fixed.startswith("no"):
            status = "open"
        else:
            status = "unknown"
        rows.append({"id": cells[0], "severity": cells[1], "status": status,
                     "what": cells[2]})
    if not rows:
        return {"parsed": False, "note": "no 'S<n>-<n> | severity | ... | fixed?' summary rows found"}
    by_status: "dict[str, list[str]]" = {}
    for row in rows:
        by_status.setdefault(row["status"], []).append(row["id"])
    return {"parsed": True, "total": len(rows),
            "by_status": {k: sorted(v) for k, v in sorted(by_status.items())},
            "open_ids": sorted(r["id"] for r in rows if r["status"] in ("open", "partial")),
            "rows": rows}


def _citation_audit(text: str) -> dict:
    found: "dict[str, str]" = {}
    for line in text.splitlines():
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) != 2:
            continue
        label = re.sub(r"[*`]", "", cells[0]).split("—")[0].split("--")[0].strip()
        if re.match(r"^\d+", cells[1]) and label:
            found[label] = cells[1]
    if not found:
        return {"parsed": False, "note": "no 'Classification | count' summary table found"}
    return {"parsed": True, "classifications": found,
            "unsourced": found.get("UNSOURCED")}


def section_findings() -> dict:
    out: dict = {}
    for key, rel, parser in (
        ("prompt_review", "docs/PROMPT-REVIEW.md", _prompt_review),
        ("security_review", "docs/SECURITY-REVIEW.md", _security_review),
        ("citation_audit", "docs/CITATION-AUDIT.md", _citation_audit),
    ):
        path = os.path.join(ROOT, rel)
        text = read_text(path)
        if text is None:
            out[key] = {"present": False, "path": rel,
                        "note": "file is missing -- no counts available"}
            continue
        try:
            parsed = parser(text)
        except Exception as exc:  # a format drift must never take the report down
            parsed = {"parsed": False, "note": f"could not parse ({exc.__class__.__name__})"}
        parsed["present"] = True
        parsed["path"] = rel
        parsed["lines"] = len(text.splitlines())
        out[key] = parsed
    return out


def section_evals() -> dict:
    rel = "evals/SCORECARD.md"
    text = read_text(os.path.join(ROOT, rel))
    if text is None:
        return {"present": False, "path": rel,
                "note": "file is missing -- cannot tell whether evals have run"}
    rate_placeholders = len(re.findall(r"<n\.n%>", text))
    all_placeholders = len(re.findall(r"<[^<>\n]{1,40}>", text))
    return {
        "present": True,
        "path": rel,
        "rate_placeholders": rate_placeholders,
        "placeholders_total": all_placeholders,
        "ever_run": rate_placeholders == 0,
        "note": ("still a template: no behavioural claim has been measured"
                 if rate_placeholders else
                 "placeholders replaced -- check the run date before trusting it"),
        "results_dir_present": os.path.isdir(os.path.join(ROOT, "evals", "results")),
    }


# --------------------------------------------------------------------------
# report
# --------------------------------------------------------------------------

def collect(skip_tests: bool) -> dict:
    return {
        "schema": "testsquad.projectstate/1",
        "root": ROOT,
        "measured_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "python": sys.version.split()[0],
        "git": section_git(),
        "tests": section_tests(skip_tests),
        "validator": section_validator(),
        "inventory": section_inventory(),
        "markers": section_markers(),
        "findings": section_findings(),
        "evals": section_evals(),
    }


def _hdr(title: str) -> str:
    return f"\n{title}\n{'-' * len(title)}"


def render(state: dict) -> str:
    out: "list[str]" = []
    add = out.append

    add("testsquad — measured project state")
    add(f"root: {state['root']}")
    add(f"measured: {state['measured_at']}  (python {state['python']})")
    add("Every number below was produced by running something just now.")

    # git
    g = state["git"]
    add(_hdr("Git"))
    if not g.get("available"):
        add(f"  {g.get('note', 'unavailable')}")
    else:
        add(f"  branch:      {g.get('branch')}")
        clean = g.get("clean")
        add(f"  working tree: {'clean' if clean else 'DIRTY' if clean is False else 'unknown'}"
            + (f" ({len(g['dirty_files'])} changed)" if g.get("dirty_files") else ""))
        for path in g.get("dirty_files", [])[:12]:
            add(f"      M {path}")
        if len(g.get("dirty_files", [])) > 12:
            add(f"      ... and {len(g['dirty_files']) - 12} more")
        commit = g.get("last_commit")
        if commit:
            add(f"  last commit: {commit['sha']}  {commit['date']}")
            add(f"               {commit['subject']}")
        if g.get("origin"):
            add(f"  origin:      {g['origin']}")

    # tests
    t = state["tests"]
    add(_hdr("Test suite  (cd tests && python3 -m unittest over test_*.py)"))
    if not t.get("ran"):
        add(f"  NOT RUN: {t.get('reason')}")
    else:
        add(f"  {t['total']} tests across {len(t['modules'])} modules — "
            f"{'GREEN' if t['green'] else 'RED'}")
        add(f"  failures {len(t['failures'])}  errors {len(t['errors'])}  "
            f"skipped {len(t['skipped'])}  expected failures {len(t['expected_failures'])}  "
            f"unexpected successes {len(t['unexpected_successes'])}")
        for label, key in (("FAIL", "failures"), ("ERROR", "errors"),
                           ("UNEXPECTED PASS", "unexpected_successes")):
            for name in t[key]:
                add(f"    {label}: {name}")
        for name in t["expected_failures"]:
            add(f"    known defect (expectedFailure): {name}")
        for name in t["skipped"]:
            add(f"    skipped: {name}")
        for err in t.get("load_errors", []):
            add(f"    LOAD ERROR: {err.splitlines()[0] if err else err}")

    # validator
    v = state["validator"]
    add(_hdr("Plugin validation  (scripts/validate_plugin.py . --strict)"))
    if not v.get("ran"):
        add(f"  NOT RUN: {v.get('reason')}")
    else:
        add(f"  {'PASS' if v['passed'] else 'FAIL'} (exit {v['returncode']})")
        for line in v["output"][:20]:
            add(f"    {line}")
    add("  note: `claude plugin validate --strict` is the authoritative check "
        "and is not run here.")

    # inventory
    inv = state["inventory"]
    add(_hdr("Inventory"))
    add(f"  agents {inv['agents']}   skills {inv['skills']}   stacks {inv['stacks']}   "
        f"scripts {inv['scripts']}")
    add(f"  test files {inv['test_files']}   eval cases {inv['eval_cases']}   "
        f"agent templates {inv['agent_templates']}   hook examples {inv['hook_examples']}")
    if not inv["fixtures"]:
        add("  fixture repos: none found under examples/fixtures/")
    else:
        for f in inv["fixtures"]:
            add(f"  fixture {f['name']}: git history {'present' if f['has_git'] else 'MISSING'}")
    if inv["fixture_action"]:
        add(f"  ACTION REQUIRED: {inv['fixture_action']}")

    # markers
    m = state["markers"]
    add(_hdr("Unfinished markers"))
    totals = ", ".join(f"{k} {v}" for k, v in m["totals"].items())
    unparsed = m.get("ast_unparsed_files") or []
    add(f"  {totals}")
    if unparsed:
        # The one number on this line that is NOT a measurement has to say so
        # where the number is, not in a footnote further down.
        add(f"  NOT EXACT: the {MARKER_XFAIL} total includes "
            f"{sum(d['line_scan_hits'] for d in unparsed)} hit(s) from "
            f"{len(unparsed)} file(s) counted WITHOUT the AST pass — see below")
    for path, counts in m["by_file"].items():
        add(f"    {path}: " + ", ".join(f"{k}×{v}" for k, v in counts.items()))
    if m["expected_failures"]:
        add(f"  documented known defects ({MARKER_XFAIL}):")
        for ef in m["expected_failures"]:
            add(f"    {ef['file']}:{ef['line']}  {ef['test'] or '(name unreadable)'}")
    for d in unparsed:
        add(f"  COULD NOT PARSE, counted WITHOUT the AST pass: {d['file']}")
        add(f"    {d['reason']}")
        add(f"    fell back to a {d['fell_back_to']} that found "
            f"{d['line_scan_hits']} hit(s); not measured: {d['not_measured']}")

    # findings
    f = state["findings"]
    add(_hdr("Open findings from the review documents"))

    pr = f["prompt_review"]
    if not pr.get("present"):
        add(f"  {pr['path']}: {pr['note']}")
    elif pr.get("parsed"):
        c = pr["counts"]
        add(f"  {pr['path']} ({pr['lines']} lines): "
            f"{c['critical']} critical, {c['high']} high, {c['medium']} medium, {c['low']} low")
        add(f"    ({pr['meaning']} — open vs fixed is tracked in HANDOFF.md, not here)")
    else:
        add(f"  {pr['path']} ({pr['lines']} lines): {pr['note']}")

    sr = f["security_review"]
    if not sr.get("present"):
        add(f"  {sr['path']}: {sr['note']}")
    elif sr.get("parsed"):
        add(f"  {sr['path']} ({sr['lines']} lines): {sr['total']} findings — "
            + ", ".join(f"{k} {len(v)}" for k, v in sr["by_status"].items()))
        add("    not fully fixed: " + ", ".join(sr["open_ids"]) if sr["open_ids"]
            else "    all rows marked fixed")
    else:
        add(f"  {sr['path']} ({sr['lines']} lines): {sr['note']}")

    ca = f["citation_audit"]
    if not ca.get("present"):
        add(f"  {ca['path']}: {ca['note']}")
    elif ca.get("parsed"):
        add(f"  {ca['path']} ({ca['lines']} lines): "
            + ", ".join(f"{k} {v}" for k, v in ca["classifications"].items()))
    else:
        add(f"  {ca['path']} ({ca['lines']} lines): {ca['note']}")

    # evals
    e = state["evals"]
    add(_hdr("Evaluations"))
    if not e.get("present"):
        add(f"  {e['path']}: {e['note']}")
    else:
        add(f"  {e['path']}: {e['rate_placeholders']} unreplaced `<n.n%>` rate "
            f"placeholders ({e['placeholders_total']} placeholders of any kind)")
        add(f"  evals ever run: {'YES' if e['ever_run'] else 'NO'} — {e['note']}")
        add(f"  evals/results/ present: {'yes' if e['results_dir_present'] else 'no'}")

    add("")
    add("Read docs/STATE.md next for what this project is and what to do with it;")
    add("this script is the authority on the numbers, that document on the intent.")
    return "\n".join(out)


def main(argv: "list[str] | None" = None) -> int:
    ap = argparse.ArgumentParser(
        prog="project_state.py",
        description="Measure and report the current state of this repository.")
    ap.add_argument("--json", action="store_true", help="emit the structured report")
    ap.add_argument("--out", help="write the report to this file instead of stdout")
    ap.add_argument("--no-tests", action="store_true",
                    help="skip the test run (everything else finishes in under a second)")
    args = ap.parse_args(argv)

    state = collect(skip_tests=args.no_tests)
    text = json.dumps(state, indent=2) if args.json else render(state)

    if args.out:
        try:
            # No os.makedirs() here: write_text_atomic() already creates
            # the parent directory, which is why .testing/ not existing
            # yet is still not an error for this writer.
            write_text_atomic(args.out, text + "\n")
        except OSError as exc:
            print(f"error: cannot write {args.out}: {exc}", file=sys.stderr)
            return 2
        print(f"wrote {args.out}", file=sys.stderr)
    else:
        print(text)

    # Exit code is about this script succeeding, not about the project being
    # green: a red suite is a finding to report, not a crash.
    return 0


if __name__ == "__main__":
    sys.exit(main())
