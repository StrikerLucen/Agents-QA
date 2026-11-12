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


def _xfail_tests(text: str) -> "list[dict] | None":
    """(line, test name) for every function actually decorated with
    expectedFailure, via the parser rather than a text match -- the decorator
    is also quoted in prose in these files, and a docstring is not a defect.
    Returns None when the file does not parse, so the caller can fall back."""
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return None
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for dec in node.decorator_list:
            target = dec.func if isinstance(dec, ast.Call) else dec
            name = getattr(target, "attr", None) or getattr(target, "id", None)
            if name == "expectedFailure":
                found.append({"line": dec.lineno, "test": node.name})
    return found


def section_markers() -> dict:
    by_file: "dict[str, dict]" = {}
    totals = {m: 0 for m in MARKERS}
    expected_failures: "list[dict]" = []

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

            xfails = _xfail_tests(text) if ext == ".py" else []
            if xfails is None:  # unparseable python: say what was seen, not what it means
                xfails = [{"line": i + 1, "test": None}
                          for i, line in enumerate(text.splitlines())
                          if line.lstrip().startswith(MARKER_XFAIL)]
            for hit in xfails:
                counts[MARKER_XFAIL] += 1
                totals[MARKER_XFAIL] += 1
                expected_failures.append({"file": rel, **hit})

            if any(counts.values()):
                by_file[rel] = {k: v for k, v in counts.items() if v}

    return {
        "totals": totals,
        "by_file": dict(sorted(by_file.items())),
        "expected_failures": expected_failures,
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
    add(f"  {totals}")
    for path, counts in m["by_file"].items():
        add(f"    {path}: " + ", ".join(f"{k}×{v}" for k, v in counts.items()))
    if m["expected_failures"]:
        add(f"  documented known defects ({MARKER_XFAIL}):")
        for ef in m["expected_failures"]:
            add(f"    {ef['file']}:{ef['line']}  {ef['test'] or '(name unreadable)'}")

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
            directory = os.path.dirname(os.path.abspath(args.out))
            os.makedirs(directory, exist_ok=True)
            with open(args.out, "w", encoding="utf-8") as fh:
                fh.write(text + "\n")
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
