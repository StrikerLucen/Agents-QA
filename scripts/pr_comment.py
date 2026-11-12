#!/usr/bin/env python3
"""Render the pull-request comment. Deterministic, with no model involved.

Findings delivered against the change under review get acted on roughly 70% of
the time; the same findings in a batch list get close to 0%. So this is the
primary surface and the dashboard is secondary -- which also means this output
has to be trustworthy enough that nobody learns to scroll past it.

No LLM runs here, on purpose:

  * It is exact. Every number comes from a file a human can open.
  * It is free, and it runs on every push without anyone costing it out.
  * It is safe on forks. A pull request from a fork must never be able to make
    a model read attacker-controlled text with repository credentials in scope.

What it may contain is deliberately narrow: contract gates, coverage change on
the changed lines, new reproduced findings, and the health verdict. It must
never contain a secret, and never an unreproduced suspicion -- a pull request
comment can be public, and a guess published under a tool's name is worse than
no comment at all.

Two rules follow from "the input is JSON that agents and humans write by hand":

  1. A malformed field degrades its own row and nothing else. The comment is
     the most useful thing available precisely when something upstream is
     broken, so a bad `count` must not take the whole report with it. Where a
     value is unusable it renders as `?` or the row is dropped, and the
     comment says an input was malformed -- the same honesty as the
     "Not everything ran" notice. A plausible-looking number is never
     invented to fill a hole.
  2. Every string that reaches the output is redacted and escaped first. The
     titles are written by agents, the output is public, and a title that
     quotes a config line can carry a live token. A `|` in a title would
     otherwise forge table columns and let a finding write its own verdict.

Standard library only.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
from collections import Counter

MARKER = "<!-- testsquad:pr-comment -->"
MAX_ROWS = 10
# One pathological title must not blow up the comment. Long enough for a real
# finding title, short enough that a megabyte of text costs a line, not a page.
MAX_CELL = 160
MAX_REPRO = 500
MAX_NOTES = 6

SEVERITY_ORDER = {"S1": 0, "S2": 1, "S3": 2, "S4": 3}
# Only these may be shown. Anything unproven stays out of a public comment.
PUBLISHABLE = {"reproduced", "observed"}


# --------------------------------------------------------------------------
# redaction -- the output is public, and the input is written by agents
# --------------------------------------------------------------------------
#
# The shapes below duplicate what env_safety_check.py already knows. That
# duplication is deliberate and load-bearing: this renderer is the last useful
# surface when something upstream is broken, so it must not acquire an import
# that can fail. The sibling module is still consulted when it imports
# cleanly, so a new detector added there is picked up here for free -- the
# local set is the floor, not the ceiling.

_LOCAL_SECRET_RES = (
    (re.compile(r"\b[sprk]k_live_[A-Za-z0-9]{8,}"), "Stripe live key"),
    (re.compile(r"\b(?:AKIA|ASIA|AGPA|AIDA|AROA|ANPA)[0-9A-Z]{16}\b"), "AWS key id"),
    (re.compile(r"\bghp_[A-Za-z0-9]{20,}"), "GitHub token"),
    (re.compile(r"\bgh[osur]_[A-Za-z0-9]{20,}"), "GitHub token"),
    (re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}"), "GitHub token"),
    (re.compile(r"\bglpat-[A-Za-z0-9_\-]{15,}"), "GitLab token"),
    (re.compile(r"\bxox[baprs]-[A-Za-z0-9\-]{10,}"), "Slack token"),
    (re.compile(r"\bSG\.[A-Za-z0-9_\-]{15,}\.[A-Za-z0-9_\-]{15,}"), "SendGrid key"),
    (re.compile(r"\bAIza[A-Za-z0-9_\-]{30,}"), "Google API key"),
    (re.compile(r"\bnpm_[A-Za-z0-9]{30,}"), "npm token"),
    (re.compile(r"\bshpat_[a-f0-9]{30,}"), "Shopify token"),
    (re.compile(r"\bdop_v1_[a-f0-9]{40,}"), "DigitalOcean token"),
    (re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]*"),
     "signed JWT"),
    (re.compile(r"(?i)AccountKey=[A-Za-z0-9+/=]{20,}"), "Azure account key"),
    (re.compile(r"(?i)SharedAccessKey=[A-Za-z0-9+/=]{20,}"), "Azure access key"),
    (re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----"),
     "private key"),
)

# A URL with credentials in its userinfo: postgres://user:pass@host. The whole
# thing goes, host included -- a connection string is a secret in one piece.
_CRED_URL_RE = re.compile(r"\b[a-zA-Z][a-zA-Z0-9+.\-]{1,20}://[^\s/@:]+:[^\s/@]+@\S*")

# `Authorization: Bearer <blob>` and friends.
_BEARER_RE = re.compile(r"(?i)\b(bearer|token)\s+([A-Za-z0-9._\-+/=]{16,})")

# A long run on a key-looking field. The key name is kept (it is the useful
# half of the message); only the value goes. Without the key-name anchor this
# would redact ordinary identifiers and make every finding unreadable.
_KEYISH_RE = re.compile(
    r"(?i)\b([A-Za-z0-9_\-]*"
    r"(?:api[_\-]?key|secret|token|password|passwd|pwd|credential|"
    r"access[_\-]?key|private[_\-]?key|auth|authorization|dsn)"
    r"[A-Za-z0-9_\-]*)\s*[:=]\s*[\"']?([^\s\"',;)\]}]{16,})"
)

_VENDOR_RES: tuple[tuple[re.Pattern, str], ...] = _LOCAL_SECRET_RES
try:  # Reuse the scanner's shapes when they are reachable; never depend on it.
    import env_safety_check as _esc  # type: ignore

    _VENDOR_RES = _LOCAL_SECRET_RES + tuple(
        (p, "credential") for p, _ in getattr(_esc, "VENDOR_TOKEN_RES", ())
    ) + tuple(
        (p, "credential") for p in (
            getattr(_esc, "AWS_KEY_RE", None),
            getattr(_esc, "STRIPE_LIVE_RE", None),
            getattr(_esc, "AZURE_CONN_RE", None),
        ) if p is not None
    )
except Exception:  # pragma: no cover - depends on how this module is imported
    pass


def _entropy(s: str) -> float:
    if not s:
        return 0.0
    counts = Counter(s)
    n = len(s)
    return -sum((c / n) * math.log2(c / n) for c in counts.values())


def _secret_shaped(v: str) -> bool:
    """Length, charset spread and entropy together, as the scanner does it.

    Each alone is useless: `supersecretpassword` is long, `a1b2` is mixed, and
    a hash of a public file is high-entropy. All three together keeps
    developer-obvious values (`localhost`, `change-me-later`) readable.
    """
    if len(v) < 16 or len(v) > 500:
        return False
    classes = sum((any(c.islower() for c in v),
                   any(c.isupper() for c in v),
                   any(c.isdigit() for c in v)))
    if classes < 2:
        return False
    return _entropy(v) >= 3.0


def redact(text: str) -> str:
    """Remove anything secret-shaped from a string bound for the comment.

    Whole matches are removed, prefix included. Half a key is still a leak, so
    nothing recognisable survives -- not even the four-character preview the
    scanner prints, because here there is no file and line to pair it with and
    the audience is the whole internet.
    """
    if not text:
        return text
    out = _CRED_URL_RE.sub("[redacted credential URL]", text)
    for pattern, label in _VENDOR_RES:
        out = pattern.sub("[redacted: %s]" % label, out)
    out = _BEARER_RE.sub(
        lambda m: m.group(1) + " [redacted]"
        if _secret_shaped(m.group(2)) else m.group(0), out)
    out = _KEYISH_RE.sub(
        lambda m: m.group(1) + "=[redacted]"
        if _secret_shaped(m.group(2)) else m.group(0), out)
    return out


# --------------------------------------------------------------------------
# escaping -- a finding must not be able to write its own table row
# --------------------------------------------------------------------------

def _escape_md(text: str) -> str:
    """Neutralise the markup a cell could otherwise inject.

    `|` forges columns, a leading `#`/`>` escapes the table into a heading or
    a quote, backticks open code spans that swallow the rest of the row, and
    raw HTML is rendered by every forge that shows these comments.
    """
    text = text.replace("\\", "\\\\")
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    text = text.replace("|", "\\|").replace("`", "\\`")
    # Newlines end a table row outright; collapse all whitespace to spaces.
    text = re.sub(r"\s+", " ", text).strip()
    if text[:1] in ("#", "-", "+", "="):
        text = "\\" + text
    return text


def _stringify(value) -> str | None:
    """A cell value that is not a scalar is not renderable. Say so, loudly."""
    if value is None:
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (str, int, float)):
        return str(value)
    return None


def cell(value, notes: list[str] | None = None, what: str = "a field") -> str:
    """Everything that reaches a table cell goes through here."""
    raw = _stringify(value)
    if raw is None:
        if value is not None:
            _note(notes, f"{what} was not a string or a number")
        return "?"
    text = _escape_md(redact(raw))
    if len(text) > MAX_CELL:
        text = text[:MAX_CELL].rstrip() + "…"
    return text or "?"


def code_cell(value) -> str:
    """A path-shaped cell, rendered in backticks.

    Escaping a backtick inside a code span does not work, so they are dropped
    rather than escaped -- a file path has no business containing one.
    """
    raw = _stringify(value) or ""
    text = redact(raw).replace("`", "").replace("|", "\\|")
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > MAX_CELL:
        text = text[:MAX_CELL].rstrip() + "…"
    return f"`{text}`" if text else "—"


def _or(value, fallback):
    """Default only on a genuinely absent value. A gate actual of 0 is a
    measurement, not a blank."""
    return fallback if value is None or value == "" else value


def _note(notes: list[str] | None, text: str) -> None:
    if notes is None:
        return
    if text not in notes:
        notes.append(text)


# --------------------------------------------------------------------------
# coercion -- a wrong type degrades one row
# --------------------------------------------------------------------------

def as_number(value, notes: list[str] | None = None, what: str = "a number"):
    """A number, or None when the value cannot be one.

    A numeric string is read at face value: `"90.0"` is the measurement, just
    typed by hand or built by jq, and reading it is not inventing it. Anything
    else returns None, which renders as "not measured" rather than a zero --
    the distinction the whole coverage chain exists to preserve.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        _note(notes, f"{what} was a boolean, not a number")
        return None
    if isinstance(value, (int, float)):
        if math.isnan(value) or math.isinf(value):
            _note(notes, f"{what} was not a finite number")
            return None
        return float(value)
    if isinstance(value, str):
        try:
            parsed = float(value.strip())
        except ValueError:
            _note(notes, f"{what} was not a number")
            return None
        if math.isnan(parsed) or math.isinf(parsed):
            _note(notes, f"{what} was not a finite number")
            return None
        _note(notes, f"{what} was a string, not a number")
        return parsed
    _note(notes, f"{what} was not a number")
    return None


def as_count(value, notes: list[str] | None = None) -> int:
    """An occurrence count. Unusable means "no multiplier shown", never a 1
    presented as a measurement."""
    if value is None:
        return 1
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        _note(notes, "a finding count was not a number")
        return 1
    try:
        parsed = float(str(value).strip())
    except (TypeError, ValueError):
        _note(notes, "a finding count was not a number")
        return 1
    # `json.load` accepts the JavaScript spellings `Infinity` and `NaN` by
    # default, so both reach here from a real data file. int() raises on them
    # -- OverflowError, which is not a ValueError and would escape as a
    # traceback in the CI step that comments on the pull request.
    if math.isnan(parsed) or math.isinf(parsed):
        _note(notes, "a finding count was not a finite number")
        return 1
    return int(parsed)


def pct(value) -> str:
    """A measurement that was not taken is 'not measured', never 0%."""
    return "not measured" if value is None else f"{value:.1f}%"


def delta(now, before) -> str:
    if now is None or before is None:
        return "—"
    diff = now - before
    if abs(diff) < 0.05:
        return "no change"
    return f"{diff:+.1f} pts"


def _as_dict(value, notes: list[str] | None, what: str) -> dict:
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    _note(notes, f"`{what}` was not an object")
    return {}


def _as_rows(value, notes: list[str] | None, what: str) -> list[dict]:
    """A list of objects, with anything else dropped and declared."""
    if value is None:
        return []
    if not isinstance(value, list):
        _note(notes, f"`{what}` was not a list")
        return []
    rows = [item for item in value if isinstance(item, dict)]
    if len(rows) != len(value):
        _note(notes, f"{len(value) - len(rows)} entr(ies) in `{what}` "
                     "were not objects and were dropped")
    return rows


# --------------------------------------------------------------------------
# rendering
# --------------------------------------------------------------------------

def verdict_line(health, gates, findings, notes: list[str] | None = None) -> str:
    """One sentence answering: is anything on fire, and what do I do first?"""
    health = _as_dict(health, notes, "health")
    gates = _as_rows(gates, notes, "gates")
    findings = _as_rows(findings, notes, "findings")

    blocking = [f for f in findings if f.get("severity") in ("S1", "S2")]
    failed = [g for g in gates if not g.get("met")]
    health_verdict = str(health.get("verdict") or "").upper()

    if health_verdict == "RED":
        return ("**The test suite is not healthy.** Coverage figures are withheld "
                "because they would not mean anything until it runs clean.")
    if blocking:
        worst = min(blocking, key=lambda f: SEVERITY_ORDER.get(f.get("severity"), 9))
        return (f"**{cell(worst.get('severity'), notes, 'a severity')}: "
                f"{cell(_or(worst.get('title'), 'see below'), notes, 'a finding title')}**")
    if failed:
        names = ", ".join(cell(_or(g.get("name"), "?"), notes, "a gate name")
                          for g in failed[:2])
        return f"**{len(failed)} quality gate(s) not met:** {names}"
    if findings:
        return "No blocking issues. Some findings worth a look below."
    return "No new findings on this change."


def render(data) -> str:
    notes: list[str] = []

    # The whole file can be the wrong shape -- a JSON array, a string, a
    # number. That is an empty report plus a notice, never a traceback in a
    # CI step on somebody's pull request.
    if not isinstance(data, dict):
        _note(notes, "the data file was not a JSON object")
        data = {}

    health = _as_dict(data.get("health"), notes, "health")
    gates = _as_rows(data.get("gates"), notes, "gates")
    coverage = _as_dict(data.get("coverage"), notes, "coverage")
    raw_findings = _as_rows(data.get("findings"), notes, "findings")

    # Publishable only. An unreproduced suspicion in a public comment is the
    # fastest way to lose a reader permanently.
    findings = [f for f in raw_findings
                if str(f.get("evidence") or "").lower() in PUBLISHABLE]
    withheld = len(raw_findings) - len(findings)

    counts = {id(f): as_count(f.get("count"), notes) for f in findings}
    findings.sort(key=lambda f: (SEVERITY_ORDER.get(f.get("severity"), 9),
                                 -counts[id(f)]))

    out = [MARKER, "## testsquad", "",
           verdict_line(health, gates, findings, notes), ""]

    if health.get("verdict"):
        bits = [f"Suite health: **{cell(health.get('verdict'), notes, 'the health verdict')}**"]
        for key, label in (("flaky_count", "flaky"),
                           ("skipped_count", "skipped"),
                           ("quarantined_count", "quarantined")):
            if health.get(key):
                bits.append(f"{cell(health.get(key), notes, f'{key}')} {label}")
        out += [" · ".join(bits), ""]

    # A RED suite means the coverage figure is not trustworthy, so it is not
    # shown. Saying "withheld" and then printing the number is worse than
    # either alone: the reader takes the number and ignores the caveat.
    if str(health.get("verdict") or "").upper() == "RED":
        if coverage:
            out += ["### Coverage on the lines this PR changed", "",
                    "_Not shown. The suite is not healthy, so a coverage figure "
                    "measured against it would not mean anything. Fix the suite "
                    "first, then this fills in._", ""]
    elif coverage:
        caveat = (" _Measured under an AMBER suite — read with the caveats above._"
                  if str(health.get("verdict") or "").upper() == "AMBER" else "")
        line = as_number(coverage.get("line_pct"), notes, "`coverage.line_pct`")
        base_line = as_number(coverage.get("base_line_pct"), notes,
                              "`coverage.base_line_pct`")
        branch = as_number(coverage.get("branch_pct"), notes, "`coverage.branch_pct`")
        base_branch = as_number(coverage.get("base_branch_pct"), notes,
                                "`coverage.base_branch_pct`")
        out += [
            "### Coverage on the lines this PR changed" + caveat, "",
            "| | This PR | Base | Change |",
            "|---|---|---|---|",
            f"| Lines | {pct(line)} | {pct(base_line)} | {delta(line, base_line)} |",
            f"| Branches | {pct(branch)} | {pct(base_branch)} "
            f"| {delta(branch, base_branch)} |",
            "",
        ]
        if coverage.get("uncovered_changed_lines"):
            uncovered = as_number(coverage.get("uncovered_changed_lines"), notes,
                                  "`coverage.uncovered_changed_lines`")
            if uncovered is not None:
                out += [f"{int(uncovered)} changed line(s) are not "
                        "covered by any test.", ""]

    if gates:
        out += ["### Quality gates", "", "| Gate | Target | Actual | |",
                "|---|---|---|---|"]
        for g in gates[:MAX_ROWS]:
            mark = "✅" if g.get("met") else "❌"
            out.append(f"| {cell(_or(g.get('name'), '?'), notes, 'a gate name')} "
                       f"| {cell(_or(g.get('target'), '—'), notes, 'a gate target')} "
                       f"| {cell(_or(g.get('actual'), '—'), notes, 'a gate actual')} "
                       f"| {mark} |")
        out.append("")

    if findings:
        out += [f"### New findings ({len(findings)})", "",
                "| Severity | Finding | Evidence | Where |", "|---|---|---|---|"]
        for f in findings[:MAX_ROWS]:
            badge = "✅ reproduced" \
                if str(f.get("evidence") or "").lower() == "reproduced" \
                else "👁 observed"
            n = counts.get(id(f), 1)
            suffix = f" (×{n})" if n > 1 else ""
            where = _stringify(f.get("file")) or ""
            if f.get("line") is not None:
                where = f"{where}:{_stringify(f.get('line')) or '?'}"
            out.append(f"| {cell(_or(f.get('severity'), '—'), notes, 'a severity')} "
                       f"| {cell(_or(f.get('title'), '?'), notes, 'a finding title')}"
                       f"{suffix} | {badge} | {code_cell(where)} |")
        out.append("")
        if len(findings) > MAX_ROWS:
            out.append(f"_{len(findings) - MAX_ROWS} more in the full report._")
            out.append("")
        repro = next((_stringify(f.get("repro")) for f in findings
                      if _stringify(f.get("repro"))), None)
        if repro:
            # A fenced block needs no markdown escaping, but it does need the
            # redaction pass -- a repro command is where a token is likeliest
            # to be -- and it must not be able to close its own fence.
            safe = redact(repro).replace("`", "'")
            if len(safe) > MAX_REPRO:
                safe = safe[:MAX_REPRO].rstrip() + " …"
            out += ["Reproduce the first one:", "", "```bash", safe, "```", ""]

    if withheld:
        # Say the number, never the content. Hiding that anything was withheld
        # would be its own kind of dishonesty.
        out.append(f"_{withheld} unconfirmed item(s) withheld from this comment "
                   "— see the full report._")
        out.append("")

    missing = data.get("missing_inputs")
    if missing:
        # Absence of data is not absence of problems. Say which checks did not
        # run, so nobody reads a short comment as a clean bill of health.
        if not isinstance(missing, list):
            missing = [missing]
        out.append("> **Not everything ran.** These inputs were missing, so the "
                   "report above is incomplete:")
        for item in missing[:MAX_ROWS]:
            out.append(f"> - {cell(item, notes, 'a missing-input name')}")
        out.append("")

    if notes:
        # Same contract as "Not everything ran": a comment rendered over broken
        # input must say so, or a reader takes a degraded report for a full
        # one. The affected parts above read `?` or are absent -- no number is
        # ever guessed to fill the gap.
        out.append("> **Some input was malformed.** The comment above was rendered "
                   "anyway; the affected parts are shown as `?` or left out:")
        for item in notes[:MAX_NOTES]:
            out.append(f"> - {item}")
        if len(notes) > MAX_NOTES:
            out.append(f"> - …and {len(notes) - MAX_NOTES} more.")
        out.append("")

    pre_existing = as_count(data.get("pre_existing_count")) \
        if data.get("pre_existing_count") else 0
    if pre_existing:
        out.append(f"_{pre_existing} pre-existing finding(s) are in the "
                   "backlog and do not block this PR._")
        out.append("")

    out.append("<sub>Generated without a model. Every number above is computed from "
               "`.testing/`. Something wrong? Mark it not-useful in the report — "
               "checks above a 10% false-positive rate are demoted automatically.</sub>")
    return "\n".join(out)


# --------------------------------------------------------------------------
# entry point
# --------------------------------------------------------------------------

def load(path: str | None) -> dict:
    """Always a dict. A JSON array or scalar at the top level is not data this
    renderer can use, and a truthy non-dict slipping past main()'s guard is
    exactly how a traceback reaches a pull request."""
    if not path or not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as fh:
            blob = json.load(fh)
    except (OSError, json.JSONDecodeError, ValueError, RecursionError):
        return {}
    return blob if isinstance(blob, dict) else {}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="pr_comment.py",
        description="Render the deterministic testsquad PR comment.")
    ap.add_argument("--data", help="JSON file with health/gates/coverage/findings")
    ap.add_argument("--testing-dir", default=".testing",
                    help="read health and coverage from here when --data is absent")
    ap.add_argument("--out", help="write here instead of stdout")
    args = ap.parse_args(argv)

    if args.data:
        data = load(args.data)
        if not data:
            print(f"error: could not read {args.data}", file=sys.stderr)
            return 2
    else:
        # Each input, and the command that produces it. A missing file must be
        # diagnosable: "no findings" and "nobody looked" render identically,
        # and the second one shipped as the first is the worst output this
        # tool could produce -- a reassuring comment that means nothing.
        sources = {
            "health":   ("health-baseline.json", "/testsquad:health"),
            "coverage": ("coverage-diff.json",   "/testsquad:diagnose"),
            "gates":    ("gates.json",           "/testsquad:verify"),
            "findings": ("findings.json",        "/testsquad:test or :debug"),
        }
        data, missing = {}, []
        for key, (filename, producer) in sources.items():
            path = os.path.join(args.testing_dir, filename)
            if not os.path.exists(path):
                missing.append(f"{filename} (run {producer})")
                data[key] = [] if key in ("gates", "findings") else {}
                continue
            blob = load(path)
            data[key] = blob.get(key, []) if key in ("gates", "findings") else blob

        if len(missing) == len(sources):
            print("error: no testsquad data in "
                  f"{args.testing_dir}. Nothing to report.\n  missing: "
                  + "\n           ".join(missing), file=sys.stderr)
            return 2
        if missing:
            data["missing_inputs"] = missing

    body = render(data)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(body + "\n")
        print(f"wrote {args.out} ({len(body)} bytes)", file=sys.stderr)
    else:
        print(body)
    return 0


if __name__ == "__main__":
    sys.exit(main())
