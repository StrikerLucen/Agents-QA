#!/usr/bin/env python3
"""Render the testsquad HTML dashboard deterministically.

Why this exists, and why it is not the reporter's job.

`templates/report/dashboard.html` is 33,633 bytes -- a complete, self-contained
renderer with its own escaping. Until this script, `skills/report/SKILL.md`
told the `reporter` agent to render the dashboard *from* that file, so every
report run paid ~11,760 tokens to load markup the agent then retyped. The
template was never built for that: it carries a data slot
(`<script id="testsquad-data" type="application/json">`) and a worked example,
precisely so the only thing anyone has to produce is the JSON.

So the division here matches the one `pr_comment.py` already draws for the PR
comment: the model writes prose, a script writes the document.

Two things follow from that, and both are the point:

1. **Measured fields are computed here and overwrite whatever the reporter
   supplied.** `agents/reporter.md` forbids stating a number nobody measured;
   that was a rule in a prompt, which is a request. Here it is arithmetic. A
   reporter that hallucinates a coverage count finds the count replaced by the
   one in `.testing/`, and `--explain` names every field that happened to.
2. **The reporter no longer writes HTML.** `skills/report/SKILL.md` claimed
   fork-safety for the `--pr` path only, and the claim was narrower than the
   risk: finding text comes from branch names, test names and assertion
   messages on a contributor's fork, and the dashboard path had a model
   splicing that text into markup by hand, downstream of the template's own
   `esc()`. Here it goes in as JSON, through `_json_for_html()`.

Exit codes match `pr_comment.py` because the question is the same one: 0 for a
rendered document, 2 for a refusal to render a reassuring page over data that
was never read. A partial run renders and exits 0 -- it is the empty one that
must not look like an all-clear.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import errno
import json
import os
import re
import sys

if __package__ in (None, ""):  # run as a script: scripts/ is already sys.path[0]
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from atomic_io import write_text_atomic  # noqa: E402
from pr_comment import (  # noqa: E402
    DATA_ABSENT,
    as_count,
    read_json_object,
    redact,
    verdict_line,
)
from risk_map import GitError, run_git  # noqa: E402

# The slot the template reserves for us. Matched as a pair rather than with one
# greedy regex so a template that has lost its closing tag is a named failure
# instead of a document silently rendered with the rest of the page eaten.
SLOT_OPEN = '<script id="testsquad-data" type="application/json">'
SLOT_CLOSE = "</script>"

# Inputs, and which command rewrites each one. The phrasing is owed to the
# reader of a half-empty dashboard: "health-baseline.json is absent" is a fact,
# "run /testsquad:health" is the fact they can act on.
INPUTS = {
    "health": ("health-baseline.json", "/testsquad:health"),
    "gates": ("gates.json", "/testsquad:verify"),
    "coverage": ("coverage-diff.json", "/testsquad:diagnose"),
    "findings": ("findings.json", "/testsquad:test"),
}

# The reporter's prose. Not counted as a measurement: it is the one input that
# proves nothing ran, since the reporter can write it having read nothing.
PROSE = "report-data.json"

# Keys under which the reporter may supply prose. Anything else it invents is
# dropped rather than passed through to the page -- an unknown key is either a
# typo, in which case silence would hide it, or an attempt to reach the
# renderer with something it was not built to show.
PROSE_KEYS = ("meta", "verdict", "changed", "actions", "findings",
              "coverage", "charters", "appendix")

# Fields whose only legitimate source is a measured input, and which input.
#
# The overlay in `_measured()` is deliberately sparse: an input that did not
# arrive must not blank out the reporter's prose about it. That is right for
# narrative and wrong for a number. Measured 2026-11-17 on a run with an
# unreadable health-baseline.json: the overlay had no trust counts to apply,
# so the reporter's invented `{"healthy": 1, "flaky": 0}` rendered on the page
# as though someone had counted it -- a fabricated measurement surviving
# *because* the measurement failed, which is the worst possible polarity and
# the exact failure this script was written to remove.
#
# So a claim here is only allowed on the page when the input behind it was
# read. When it was not, the field is dropped and `appendix.unverified` says
# which number is missing and why, because a visibly absent count sends the
# reader to `/testsquad:health` and an invented one does not.
UNVERIFIABLE = {
    ("coverage", "trust"): "health",
    ("findings", "totalQualifying"): "findings",
}

MAX_TEXT = 4000      # one pathological finding costs a paragraph, not a page
MAX_ROWS = 50        # the queue is a queue; see templates/report/RULES.md


def _json_for_html(obj) -> str:
    """JSON that cannot end the script element it is embedded in.

    `json.dumps` is not sufficient on its own here. It escapes quotes and
    backslashes, which keeps the JSON valid, but it leaves `<` alone -- and the
    HTML tokenizer stops at the first `</script` inside a script element
    whether or not it sits inside a JSON string. A finding titled
    `</script><img onerror=...>` would therefore close our slot and run as
    markup, which is the whole injection class this script exists to remove.

    `\\u003c` is an ordinary JSON escape and parses back to `<`, so this costs
    the page nothing: `JSON.parse` in the template gets the original text, and
    the template's own `esc()` still runs on it. `>` and `&` go the same way
    to keep the payload inert in any parsing mode it might be read under.
    """
    blob = json.dumps(obj, indent=2, ensure_ascii=False, sort_keys=False)
    return (blob.replace("<", "\\u003c")
                .replace(">", "\\u003e")
                .replace("&", "\\u0026"))


def _clean(value, limit: int = MAX_TEXT):
    """Reporter-supplied text, made safe to embed and bounded.

    Redaction first, truncation second, deliberately: truncating a secret in
    half leaves half a secret, and the scanner cannot match what has already
    been cut. `redact()` is `pr_comment.py`'s, so the PR comment and the
    dashboard cannot disagree about what counts as a credential.
    """
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, list):
        return [_clean(v, limit) for v in value[:MAX_ROWS]]
    if isinstance(value, dict):
        return {k: _clean(v, limit) for k, v in value.items()}
    text = redact(str(value))
    if len(text) > limit:
        text = text[:limit] + f" … [truncated, {len(text) - limit} more chars]"
    return text


def _ask_git(repo: str, args: list) -> str:
    """One git question, with "it had no answer" as a normal reply.

    `run_git` raises `GitError` on a non-zero exit, which is right for
    `risk_map.py` -- there, a failed git call means the history it is about to
    rank cannot be read. Here every question is optional, and git answers "no"
    by exiting non-zero: `config --get remote.origin.url` exits 1 in a
    checkout with no remote, and `rev-parse HEAD` exits 128 before the first
    commit. Both are ordinary states of a real repository, so they must leave
    the field absent rather than take the dashboard down with them.

    Caught in `tests/test_robustness.py`, whose probe repository has no
    remote: the first version of this function let the `GitError` escape and
    the writer produced no file at all.
    """
    try:
        return run_git(repo, args).strip()
    except GitError:
        return ""


def _git_meta(repo: str) -> dict:
    """Repo, branch and commit as git reports them, never as prose claims.

    Every value is independently optional. A detached HEAD has no branch, a
    tree with no commits has no SHA, and a checkout with no remote has no
    name -- none of those is an error worth refusing a dashboard over, so each
    missing field is simply absent and the template renders without it.
    """
    meta = {}
    remote = _ask_git(repo, ["config", "--get", "remote.origin.url"])
    if remote:
        slug = re.sub(r"^.*[:/]([^/]+/[^/]+?)(?:\.git)?$", r"\1", remote)
        meta["repo"] = slug or remote
    branch = _ask_git(repo, ["rev-parse", "--abbrev-ref", "HEAD"])
    if branch and branch != "HEAD":
        meta["branch"] = branch
    commit = _ask_git(repo, ["rev-parse", "--short", "HEAD"])
    if commit:
        meta["commit"] = commit
    return meta


def _trust_counts(health: dict) -> dict:
    """The healthy/flaky/quarantined/broken row, from the health baseline.

    `as_count` is `pr_comment.py`'s: a count that is absent, null or not a
    number comes back 0 rather than raising, and the note it would have made
    is not needed here because `_explain` already reports that the whole input
    was missing. Returning `{}` for a baseline with none of these keys keeps
    "no baseline" distinguishable from "a baseline that counted zero tests",
    which is the distinction the trust row exists to show.
    """
    present = {k: health.get(k) for k in
               ("healthy", "flaky", "quarantined", "broken")
               if health.get(k) is not None}
    if not present:
        return {}
    return {k: as_count(v) for k, v in present.items()}


def _measured(data: dict, repo: str) -> dict:
    """Everything this script can compute, as a sparse overlay.

    Sparse on purpose. A field computed as "absent" must not overwrite the
    reporter's prose with a hole -- the overlay only carries what was actually
    measured, so a missing input leaves the reporter's account of it standing
    and `_explain` says the number behind it is unverified.
    """
    health = data.get("health") or {}
    findings = data.get("findings") or []
    overlay: dict = {"meta": {}, "coverage": {}}

    overlay["meta"].update(_git_meta(repo))
    overlay["meta"]["generatedAt"] = _dt.datetime.now(
        _dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    if isinstance(findings, list) and findings:
        overlay.setdefault("findings", {})["totalQualifying"] = len(findings)

    trust = _trust_counts(health if isinstance(health, dict) else {})
    if trust:
        overlay["coverage"]["trust"] = trust

    return {k: v for k, v in overlay.items() if v}


def _strip_unverifiable(page: dict, data: dict, explained: list) -> list:
    """Remove every numeric claim whose measured source did not arrive.

    Runs *after* the merge, so a field the overlay set from a real measurement
    is already in place and its input is by definition present. What this
    removes is only ever a number the reporter supplied and nothing confirmed.
    Returns the human-readable list for `appendix.unverified`.
    """
    dropped = []
    for (section, key), source in UNVERIFIABLE.items():
        if source in data:
            continue  # the input was read; whatever is here was measured
        block = page.get(section)
        if not isinstance(block, dict) or key not in block:
            continue
        claimed = block.pop(key)
        filename = INPUTS[source][0]
        producer = INPUTS[source][1]
        dropped.append(
            f"{section}.{key} — not shown: {filename} could not be read, so "
            f"nothing confirms it. Rerun {producer}.")
        explained.append(
            f"{section}.{key}: reporter said {claimed!r}, but {filename} "
            "could not be read — dropped rather than shown unverified")
    return dropped


def _merge(prose: dict, overlay: dict, explained: list) -> dict:
    """Overlay wins, one level deep, and says so when it overwrites.

    One level deep is the whole design: the overlay replaces `meta.commit` and
    `coverage.trust` without touching `meta.title` or `coverage.untested`
    beside them. A full deep merge would let a computed leaf reach into a prose
    structure it knows nothing about; a shallow one would discard whole
    sections to set a single field.
    """
    out = dict(prose)
    for section, fields in overlay.items():
        base = dict(out.get(section) or {})
        for key, value in fields.items():
            if key in base and base[key] != value:
                explained.append(
                    f"{section}.{key}: reporter said {base[key]!r}, "
                    f"measured {value!r} — using the measurement")
            base[key] = value
        out[section] = base
    return out


def _read_inputs(testing_dir: str) -> tuple:
    """`(data, read, missing)` — and the count is what keeps this honest.

    `read` counts inputs that parsed to an object, which is the number
    `verdict_line()` needs to tell "every check passed" from "no check ran".
    An unreadable file is counted as missing, not as a check that found
    nothing: that polarity is the one `pr_comment.py` records getting wrong,
    where four corrupt inputs rendered the same all-clear as four clean ones.
    """
    data, read, missing = {}, 0, []
    for key, (filename, producer) in INPUTS.items():
        blob, problem = read_json_object(os.path.join(testing_dir, filename))
        if problem is None:
            data[key] = blob.get(key, blob)
            read += 1
            continue
        why = "is absent" if problem == DATA_ABSENT else f"is {problem}"
        missing.append(f"{filename} ({why} — rerun {producer})")
    return data, read, missing


def render(prose: dict, data: dict, read: int, missing: list,
           template: str, repo: str, explained: list) -> str:
    """The finished HTML: the template with our JSON in its slot.

    The template is never parsed, only spliced. It is a fixed asset with its
    own renderer and its own escaping, and reaching into it with anything
    cleverer than a bounded string replacement would make this script a second
    opinion about markup it does not own.
    """
    page = {k: prose[k] for k in PROSE_KEYS if k in prose}
    page = _clean(page)
    page = _merge(page, _measured(data, repo), explained)

    verdict = dict(page.get("verdict") or {})
    if read == 0:
        # The one case where the reporter's prose is discarded rather than
        # merged. It can write a verdict having read nothing, and a confident
        # sentence over no inputs is the single worst string this tool emits.
        verdict["state"] = "blocked"
        verdict["text"] = verdict_line(
            {}, [], [], missing_inputs=missing, anything_ran=False)
        explained.append(
            "verdict: nothing was measured, so the reporter's verdict was "
            "replaced with the no-data sentence")
    else:
        verdict.setdefault("state", "info")
    page["verdict"] = verdict

    unverified = _strip_unverifiable(page, data, explained)

    if missing or unverified:
        appendix = dict(page.get("appendix") or {})
        if missing:
            appendix["missingInputs"] = missing
        if unverified:
            appendix["unverified"] = unverified
        page["appendix"] = appendix

    start = template.find(SLOT_OPEN)
    if start < 0:
        raise ValueError(
            f"the template has no {SLOT_OPEN!r} data slot, so there is "
            "nowhere to put the run's JSON")
    body = start + len(SLOT_OPEN)
    end = template.find(SLOT_CLOSE, body)
    if end < 0:
        raise ValueError(
            "the template's data slot is never closed; refusing to splice "
            "rather than render a page with the rest of the file inside it")
    return template[:body] + "\n" + _json_for_html(page) + "\n" + template[end:]


def main(argv: list[str] | None = None) -> int:
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ap = argparse.ArgumentParser(
        prog="render_dashboard.py",
        description="Render the testsquad HTML dashboard from .testing/ data.")
    ap.add_argument("--testing-dir", default=".testing",
                    help="where the run's JSON inputs live")
    ap.add_argument("--template",
                    default=os.path.join(here, "templates", "report",
                                         "dashboard.html"),
                    help="the dashboard template carrying the data slot")
    ap.add_argument("--repo", default=".",
                    help="the repository the report is about")
    ap.add_argument("--out", help="write here instead of stdout")
    ap.add_argument("--explain", action="store_true",
                    help="list the fields the measurements overrode")
    args = ap.parse_args(argv)

    prose, problem = read_json_object(os.path.join(args.testing_dir, PROSE))
    if problem is not None:
        why = "is absent" if problem == DATA_ABSENT else f"is {problem}"
        print(f"error: {PROSE} {why} in {args.testing_dir}. The reporter "
              "writes it; nothing to render without it.", file=sys.stderr)
        return 2

    data, read, missing = _read_inputs(args.testing_dir)
    if read == 0:
        # Same rule, same exit code, same reason as pr_comment.py: a dashboard
        # over nothing is worse than no dashboard, because it is a page a CI
        # job will happily publish.
        print(f"error: no usable testsquad data in {args.testing_dir}. "
              "Nothing to report.\n  missing: "
              + "\n           ".join(missing), file=sys.stderr)
        return 2

    try:
        with open(args.template, encoding="utf-8") as fh:
            template = fh.read()
    except OSError as exc:
        print(f"error: cannot read template {args.template}: {exc}",
              file=sys.stderr)
        return 2

    explained: list = []
    try:
        body = render(prose, data, read, missing, template, args.repo,
                      explained)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.explain:
        for line in explained or ["nothing was overridden"]:
            print(f"  {line}", file=sys.stderr)

    if args.out:
        # The contract every --out writer in scripts/ keeps, and the one
        # tests/test_robustness.py asserts both halves of: atomic, and no
        # plain open() of the same path beside it. A mistyped --out stays a
        # diagnosable exit 2 rather than materialising a directory tree.
        try:
            parent = os.path.dirname(os.path.abspath(args.out))
            if not os.path.isdir(parent):
                raise FileNotFoundError(
                    errno.ENOENT, os.strerror(errno.ENOENT), args.out)
            write_text_atomic(args.out, body)
        except OSError as exc:
            print(f"error: cannot write {args.out}: {exc}", file=sys.stderr)
            return 2
        print(f"wrote {args.out} ({len(body)} bytes)", file=sys.stderr)
    else:
        print(body)
    return 0


if __name__ == "__main__":
    sys.exit(main())
