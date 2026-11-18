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
import errno
import json
import math
import os
import re
import sys
from collections import Counter

if __package__ in (None, ""):  # run as a script: scripts/ is already sys.path[0]
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from atomic_io import write_text_atomic  # noqa: E402

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

def _as_missing(value, notes: list[str] | None = None) -> list:
    """The missing/unreadable input list, in whatever shape it arrived.

    A single string is a list of one -- a hand-written data file spells it
    that way, and dropping it would be the exact failure this field exists to
    prevent."""
    if value is None or value == "" or value == []:
        return []
    if isinstance(value, list):
        return [item for item in value if item is not None and item != ""]
    return [value]


def verdict_line(health, gates, findings, notes: list[str] | None = None,
                 missing_inputs=None, anything_ran: bool | None = None) -> str:
    """One sentence answering: is anything on fire, and what do I do first?

    `missing_inputs` is what makes this honest. Without it the function can
    only see empty containers, and "every check passed" and "no check ran"
    reach it as the same three empty values -- which is how the headline came
    to say "No new findings on this change." over a run where nothing ran.

    `anything_ran` is True/False when the caller knows (main() counts the
    files it actually read), and None when it does not; None falls back to
    "did any input arrive with content in it".

    Precedence, chosen deliberately:

      0. Nothing readable at all outranks everything. There is no verdict to
         give, and an all-clear here is the single worst string this tool can
         emit.
      1. A real problem found by the inputs that *did* run outranks the
         incompleteness, because it is true and actionable: RED suite, then a
         blocking finding, then a failed gate. The incompleteness is appended
         to that headline, never dropped -- "gates all passed" with
         findings.json missing is not an all-clear either.
      2. Any headline that would otherwise read as an all-clear is replaced
         while an input is missing. A partial run has not earned one.
    """
    health = _as_dict(health, notes, "health")
    gates = _as_rows(gates, notes, "gates")
    findings = _as_rows(findings, notes, "findings")
    missing = _as_missing(missing_inputs, notes)
    if anything_ran is None:
        anything_ran = bool(health or gates or findings)

    if missing and not anything_ran:
        return ("**Nothing was measured on this change.** No testsquad input "
                "could be used, so this is not an all-clear — see below for "
                "what did not run.")

    incomplete = ""
    if missing:
        incomplete = (f" Not everything ran: {len(missing)} input(s) were "
                      "missing, unreadable or not in the shape this comment "
                      "reads, so the picture is incomplete.")

    blocking = [f for f in findings if f.get("severity") in ("S1", "S2")]
    failed = [g for g in gates if not g.get("met")]
    health_verdict = str(health.get("verdict") or "").upper()

    if health_verdict == "RED":
        return ("**The test suite is not healthy.** Coverage figures are withheld "
                "because they would not mean anything until it runs clean."
                + incomplete)
    if blocking:
        worst = min(blocking, key=lambda f: SEVERITY_ORDER.get(f.get("severity"), 9))
        return (f"**{cell(worst.get('severity'), notes, 'a severity')}: "
                f"{cell(_or(worst.get('title'), 'see below'), notes, 'a finding title')}**"
                + incomplete)
    if failed:
        names = ", ".join(cell(_or(g.get("name"), "?"), notes, "a gate name")
                          for g in failed[:2])
        return f"**{len(failed)} quality gate(s) not met:** {names}" + incomplete
    if missing:
        # Everything that ran came back clean -- but not everything ran, and
        # an all-clear from a partial run is the defect this branch exists to
        # prevent. Report what was found without claiming it is the whole
        # picture.
        tail = (f"{len(missing)} input(s) were missing, unreadable or not in "
                "the shape this comment reads — see below for which.")
        if findings:
            return ("**Not everything ran, so this is not an all-clear.** No "
                    "blocking issues in the checks that did run, and some "
                    f"findings worth a look below; {tail}")
        return ("**Not everything ran, so this is not an all-clear.** The checks "
                f"that did run found nothing; {tail}")
    if findings:
        return "No blocking issues. Some findings worth a look below."
    return "No new findings on this change."


def render(data) -> str:
    notes: list[str] = []

    # The whole file can be the wrong shape -- a JSON array, a string, a
    # number. That is an empty report plus a notice, never a traceback in a
    # CI step on somebody's pull request.
    unusable_file = not isinstance(data, dict)
    if unusable_file:
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

    missing = _as_missing(data.get("missing_inputs"), notes)
    if unusable_file:
        # The data file is itself an input, and an unreadable one. Saying so
        # here is what stops the headline below asserting an all-clear over a
        # file nobody could parse.
        missing.insert(0, "the --data file (not a JSON object)")

    # main() counts the inputs it actually read; `None` when nobody told us,
    # and verdict_line falls back to inspecting what arrived. Never guessed
    # into a number -- an absent count stays absent.
    read = data.get("inputs_read")
    if isinstance(read, bool) or not isinstance(read, int):
        read = None
    anything_ran = None if read is None else read > 0

    out = [MARKER, "## testsquad", "",
           verdict_line(health, gates, findings, notes, missing, anything_ran), ""]

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

    if missing:
        # Absence of data is not absence of problems. Say which checks did not
        # run, so nobody reads a short comment as a clean bill of health. The
        # headline above says the same thing; these two must never disagree.
        out.append("> **Not everything ran.** These inputs were missing, "
                   "unreadable, or not in the shape this comment reads, so "
                   "the report above is incomplete:")
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

# The ways a file can fail to be usable. They are different facts about the
# world, so they get different sentences. "It is not there", "it is there and
# I could not read a byte of it", "I read it and it is not JSON", "I read it
# and it is empty" and "I read it and it is the wrong shape" are five states,
# and one message covering several of them is the same conflation as a
# headline calling an unread input clean -- one layer up. "I could not read
# it" said about a file that read perfectly well sends the reader to look for
# a permissions problem that is not there; said about a file that is simply
# absent, it sends them to look for a corruption that is not there either.
#
# `DATA_ABSENT` is spelled differently from `INPUT_ABSENT` on purpose: these
# are facts about a *file*, those are facts about an input inside one, and two
# enums sharing a string value is how a wrong comparison passes quietly.
DATA_ABSENT = "no-such-file"
DATA_UNREADABLE = "unreadable"
DATA_MALFORMED = "malformed"
DATA_NOT_AN_OBJECT = "not-an-object"
DATA_EMPTY = "empty"
# The sixth, and the only one that is a judgement about content rather than
# about the file: readable, non-empty, correctly shaped, and not this tool's
# data at all.
DATA_FOREIGN = "foreign"

# The keys this renderer reads. A `--data` object carrying none of them is not
# this tool's data: every section would be empty and the headline would read
# as an all-clear over a file that was never about this change.
DATA_KEYS = ("health", "coverage", "gates", "findings",
             "missing_inputs", "inputs_read", "pre_existing_count")

# The four measured inputs, the file `--testing-dir` reads each from, and the
# command that writes it. Declared once so neither mode can name a different
# file or a different remedy than the other.
INPUT_SOURCES = {
    "health":   ("health-baseline.json", "/testsquad:health"),
    "coverage": ("coverage-diff.json",   "/testsquad:diagnose"),
    "gates":    ("gates.json",           "/testsquad:verify"),
    "findings": ("findings.json",        "/testsquad:test or :debug"),
}
RENDERED_INPUTS = tuple(INPUT_SOURCES)

# The four answers to "did this input run?".
INPUT_RAN = "ran"
INPUT_ABSENT = "absent"
INPUT_NULL = "null"
INPUT_NO_KEYS = "no-keys"

# `gates.json` and `findings.json` wrap their rows under a key, so a file that
# is not this consumer's input is already caught by that key being absent.
# `health-baseline.json` and `coverage-diff.json` *are* the object: there is
# no wrapper to miss, so before this, every shape counted as "ran" -- and a
# plausible file whose verdict sits one level down
# (`{"health": {"verdict": "RED"}, "commit": "abc"}`) rendered the bold "No
# new findings on this change." and a full coverage table over a RED suite.
#
# This is not an invented schema. It is exactly the keys this renderer reads
# and acts on, which is the same rule the other two files already follow:
#
#   * `verdict` is the only key under `health` that changes the output. It
#     gates the health line, the RED headline and the withholding of the
#     coverage table; the counts beside it are read only once it is there.
#     `session_context.py` already treats a baseline with no top-level
#     `verdict` as "records no verdict ... not the same as a healthy suite",
#     so this is the existing cross-script contract, not a new one.
#   * the five coverage fields are the entire coverage section, and they are
#     the five in the example `skills/diagnose/SKILL.md` gives its writer.
#
# Presence, never truthiness -- the same false-positive line the rest of this
# classifier holds. `{"line_pct": null}` is a measurement that was not taken:
# it renders "not measured" and counts as having run. Only a file with none
# of these keys is one this comment cannot read anything out of.
INPUT_REQUIRED_KEYS = {
    "health": ("verdict",),
    "coverage": ("line_pct", "base_line_pct", "branch_pct",
                 "base_branch_pct", "uncovered_changed_lines"),
}

# What to call that absence, per input, in the one sentence the reader acts
# on. Named rather than generic: "it records no verdict" tells the writer
# which field to add, and "it is not the shape this comment reads" does not.
MISSING_KEYS_PHRASE = {
    "health": 'records no "verdict"',
    "coverage": "records no coverage figure (line_pct, branch_pct, …)",
}


def input_outcome(data: dict, key: str) -> str:
    """**The** definition of "this input ran". Both modes call this one.

    It is one function on purpose. The defect this closes was the two modes
    disagreeing: `--testing-dir` applied the rule per input and the `--data`
    branch checked only that *some* recognised key existed, so a file
    carrying `health` alone rendered as though all four inputs had run and
    found nothing. A second copy of the rule in the `--data` branch would
    drift apart again the next time one side is edited.

    The false-positive line lives here, and it is the delicate part: a key
    that is **present** has run, whatever it holds. `{"gates": []}` means
    gates ran and none failed -- the overwhelmingly common clean run -- and
    stays silent. Only a key that is absent, or explicitly `null`, was never
    supplied. A notice that fired on every clean run would be worse than the
    bug it was added for.

    The one exception is `INPUT_REQUIRED_KEYS`, and it is the same rule rather
    than a second one: for the two inputs that *are* the object, "the key this
    comment reads is there" is spelled out field by field, because there is no
    wrapper key for its absence to show up in. A value of the wrong type keeps
    its existing malformed notice instead -- `{"health": "GREEN"}` has
    supplied something, and reporting one fault through two channels is how a
    reader learns to ignore both.
    """
    if key not in data:
        return INPUT_ABSENT
    value = data[key]
    if value is None:
        return INPUT_NULL
    required = INPUT_REQUIRED_KEYS.get(key)
    if required and isinstance(value, dict) \
            and not any(k in value for k in required):
        return INPUT_NO_KEYS
    return INPUT_RAN


def account_for_inputs(data: dict, describe) -> tuple:
    """`(inputs_read, missing_inputs)` over the four rendered inputs.

    `describe(key, outcome)` is the *only* thing the two modes vary. What
    counts as having run is shared; how to word the remedy is not, because
    the remedies genuinely differ -- `--testing-dir` sends the reader to a
    file on disk and the command that writes it, `--data` sends them to the
    key their assembler did not put in the blob.
    """
    read, missing = 0, []
    for key in RENDERED_INPUTS:
        outcome = input_outcome(data, key)
        if outcome == INPUT_RAN:
            read += 1
        else:
            missing.append(describe(key, outcome))
    return read, missing

DATA_ERRORS = {
    DATA_ABSENT: ("error: no such file: {path} — there is nothing to read, "
                  "and a comment rendered from nothing is not an all-clear."),
    DATA_UNREADABLE: ("error: could not read {path} — the file is there, but "
                      "its bytes could not be read as text (permissions, a "
                      "directory, a non-UTF-8 encoding, or an I/O error)."),
    DATA_MALFORMED: ("error: read {path}, but it is not valid JSON — nothing "
                     "could be parsed out of it. Check it with "
                     "`python3 -m json.tool`."),
    DATA_EMPTY: ("error: read {path}, but it is empty — there is nothing to "
                 "report, and an empty comment is not an all-clear."),
    DATA_NOT_AN_OBJECT: ("error: read {path}, but it is not a JSON object — "
                         "expected an object with health/coverage/gates/"
                         "findings keys."),
    DATA_FOREIGN: ("error: read {path}, but it has none of the keys testsquad "
                   "reads — expected health/coverage/gates/findings, found: "
                   "{found}."),
}

# The same facts, worded for one file among four under `--testing-dir`, where
# the sentence is a bullet in the comment rather than the whole refusal.
# `DATA_ABSENT` is not here: an absent file is named with the command that
# writes it ("run X"), not with a complaint about the file.
FILE_PROBLEM_PHRASE = {
    DATA_UNREADABLE: "unreadable",
    DATA_MALFORMED: "not valid JSON",
    DATA_NOT_AN_OBJECT: "not a JSON object",
}


def read_json_object(path: str):
    """`(object, problem)` — the parsed top-level object, or which failure.

    `problem` is None when the file parsed to an object (including an empty
    one, which is a question for the caller, not for the parser), and
    otherwise one of `DATA_ABSENT`, `DATA_UNREADABLE`, `DATA_MALFORMED` or
    `DATA_NOT_AN_OBJECT`. Keeping those four apart is what lets the callers
    say which happened instead of blaming the reader for a file it read
    perfectly well, or blaming the writer for a file that was never there.

    `ENOENT` rather than an `os.path.exists()` pre-check: the open has
    already happened, so there is no window between the question and the
    answer, and a file deleted mid-run reports absence rather than a race.
    `UnicodeDecodeError` is a `ValueError`, so it is caught ahead of the JSON
    errors — a UTF-16 file is bytes this reader cannot read, not JSON that
    failed to parse.
    """
    try:
        with open(path, encoding="utf-8") as fh:
            blob = json.load(fh)
    except FileNotFoundError:
        return None, DATA_ABSENT
    except UnicodeDecodeError:
        return None, DATA_UNREADABLE
    except OSError as exc:
        return None, (DATA_ABSENT if exc.errno == errno.ENOENT
                      else DATA_UNREADABLE)
    except (json.JSONDecodeError, ValueError, RecursionError):
        return None, DATA_MALFORMED
    if not isinstance(blob, dict):
        return None, DATA_NOT_AN_OBJECT
    return blob, None


def read_object(path: str) -> dict | None:
    """The parsed object, or **None** when the file could not be read as one.

    The None matters. `load()` flattens "unreadable" and "empty" into the same
    `{}`, which is precisely the distinction this renderer exists to keep: a
    file nobody could parse must not be counted as a check that ran and found
    nothing. Callers that need to tell those apart use this; callers that only
    need a dict use `load()`.
    """
    return read_json_object(path)[0]


def load(path: str | None) -> dict:
    """Always a dict. A JSON array or scalar at the top level is not data this
    renderer can use, and a truthy non-dict slipping past main()'s guard is
    exactly how a traceback reaches a pull request."""
    if not path or not os.path.exists(path):
        return {}
    blob = read_object(path)
    return {} if blob is None else blob


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
        # Six distinguishable refusals, all exit 2. The exit code is the same
        # because the answer is the same -- refusing beats rendering a
        # reassuring comment over a file that holds nothing -- but the
        # sentence is not, because "I could not read it" said about a file
        # that read perfectly well sends the reader to the wrong problem, and
        # said about a file that is not there sends them to a corruption that
        # does not exist either.
        data, problem = read_json_object(args.data)
        if problem is None and not data:
            problem = DATA_EMPTY
        elif problem is None and not any(k in data for k in DATA_KEYS):
            problem = DATA_FOREIGN
        if problem is not None:
            found = ", ".join(sorted(data)[:5]) if data else ""
            print(DATA_ERRORS[problem].format(path=args.data, found=found),
                  file=sys.stderr)
            return 2

        # The file is usable, and that is a statement about the file, not
        # about the four inputs inside it. Each one is now classified by the
        # same function `--testing-dir` uses, because the two modes must not
        # disagree about what "this input ran" means.
        #
        # It is deliberately NOT a refusal. A `--data` blob is hand- or
        # CI-assembled, so a key it does not carry legitimately means "this
        # consumer only supplied part of the picture" -- which is worth
        # saying out loud, and is not worth throwing the whole comment away
        # for. Exit 2 stays reserved for a file that is unusable as a whole,
        # so a partially-supplied file renders and exits 0, exactly as it did
        # before; only the headline and the notice change.
        name = os.path.basename(args.data) or args.data

        def describe(key, outcome):
            if outcome == INPUT_NO_KEYS:
                # Supplied, and still not readable by this comment. Naming
                # the field is the difference between a notice the assembler
                # can act on and one it can only be annoyed by.
                return (f'"{key}" {MISSING_KEYS_PHRASE[key]} in {name} — '
                        f"supply it, or rerun {INPUT_SOURCES[key][1]}")
            why = "is null" if outcome == INPUT_NULL else "is absent"
            return (f'"{key}" {why} in {name} — supply it, or rerun '
                    f"{INPUT_SOURCES[key][1]}")

        read, missing = account_for_inputs(data, describe)
        # An assembler that named what it could not collect has done this
        # accounting already and knows more about why than this scan can;
        # adding a second list on top would double-count into a headline
        # number nobody measured. So its list stands, and only its *count* is
        # replaced when it did not give a usable one.
        if not _as_missing(data.get("missing_inputs")) and missing:
            data["missing_inputs"] = missing
        supplied = data.get("inputs_read")
        if isinstance(supplied, bool) or not isinstance(supplied, int):
            data["inputs_read"] = read
    else:
        # Each input, and the command that produces it. A missing file must be
        # diagnosable: "no findings" and "nobody looked" render identically,
        # and the second one shipped as the first is the worst output this
        # tool could produce -- a reassuring comment that means nothing.
        data, file_problem = {}, {}
        for key in RENDERED_INPUTS:
            filename, producer = INPUT_SOURCES[key]
            path = os.path.join(args.testing_dir, filename)
            blob, problem = read_json_object(path)
            if problem == DATA_ABSENT:
                file_problem[key] = f"{filename} (run {producer})"
                continue
            if problem is not None:
                # Present but unusable is not "ran and found nothing". It has
                # to be declared exactly as loudly as an absent file, or four
                # broken files render as a clean bill of health -- and which
                # way it is broken decides what the reader does next, so the
                # same five states the `--data` refusal distinguishes are
                # distinguished here too.
                file_problem[key] = (f"{filename} ({FILE_PROBLEM_PHRASE[problem]}"
                                     f" — rerun {producer})")
                continue
            # gates.json and findings.json wrap their rows under their own
            # key; health-baseline.json and coverage-diff.json *are* the
            # object. Either way, what lands in `data` is exactly what
            # render() will read -- which is what lets one classifier judge
            # this mode and `--data` alike. A key the file never carried is
            # left out rather than defaulted, so the classifier can still
            # tell "absent" from "null" from an empty result.
            if key in ("gates", "findings"):
                if key in blob:
                    data[key] = blob[key]
            else:
                data[key] = blob

        def describe(key, outcome):
            # A file-level failure outranks the key-level one: "it is not
            # there" and "I could not read it" are different facts from "I
            # read it and it does not carry the key", and the house rule is
            # that all three read differently.
            if key in file_problem:
                return file_problem[key]
            filename, producer = INPUT_SOURCES[key]
            if outcome == INPUT_NO_KEYS:
                # The file with no wrapper key, so there is no `"x" key is
                # absent` to say. Name the field the renderer reads instead.
                return (f"{filename} (readable, but it "
                        f"{MISSING_KEYS_PHRASE[key]} — rerun {producer})")
            why = "is null" if outcome == INPUT_NULL else "is absent"
            return (f'{filename} (readable, but its "{key}" key {why} '
                    f"— rerun {producer})")

        # Counted, not guessed: render() needs to tell "nothing ran" from
        # "everything ran and found nothing", and only this scan knows.
        read, missing = account_for_inputs(data, describe)

        # Keyed on what was *measured*, not on what happened to be on disk.
        # It used to be `every file is absent`, which made four corrupt
        # files exit 0 while four missing ones exited 2 -- the same "Nothing
        # was measured on this change." body either way, and the worse
        # polarity of the two: `skills/report/SKILL.md` drives this from a
        # shell step, so a CI job branching on the exit code read four
        # unreadable inputs as success.
        #
        # `read == 0` is the same count the headline already uses to decide
        # that nothing was measured, so the exit code and the sentence can no
        # longer disagree. It is not a wider refusal than it looks: one
        # usable input out of four is a partial run, which still renders and
        # still exits 0, exactly as before.
        if read == 0:
            print("error: no usable testsquad data in "
                  f"{args.testing_dir}. Nothing to report.\n  missing: "
                  + "\n           ".join(missing), file=sys.stderr)
            return 2
        data["inputs_read"] = read
        if missing:
            data["missing_inputs"] = missing

    body = render(data)
    if args.out:
        # Same contract as every other --out writer in scripts/ (compare
        # risk_map.py and env_safety_check.py): a write that cannot happen is
        # a diagnosable exit 2, never a traceback in the CI step that comments
        # on somebody's pull request. The skill writes to
        # .testing/reports/pr-comment.md, a directory nothing guarantees
        # exists.
        try:
            # write_text_atomic() creates a missing parent directory; this
            # writer deliberately does not, so a mistyped --out stays the
            # diagnosable exit 2 it already was rather than silently
            # materialising a directory tree nobody asked for. Raised, not
            # printed inline, so there is still exactly one failure path.
            parent = os.path.dirname(os.path.abspath(args.out))
            if not os.path.isdir(parent):
                raise FileNotFoundError(
                    errno.ENOENT, os.strerror(errno.ENOENT), args.out)
            write_text_atomic(args.out, body + "\n")
        except OSError as exc:
            print(f"error: cannot write {args.out}: {exc}", file=sys.stderr)
            return 2
        print(f"wrote {args.out} ({len(body)} bytes)", file=sys.stderr)
    else:
        print(body)
    return 0


if __name__ == "__main__":
    sys.exit(main())
