#!/usr/bin/env python3
"""Compute per-file defect likelihood from git history.

Likelihood is computed from process metrics, not from code complexity. The
evidence for that choice is in docs/RESEARCH.md §8:

  * Absolute churn is a weak predictor; churn NORMALIZED by file size
    discriminated fault-prone binaries at 89% accuracy (Nagappan & Ball,
    ICSE 2005). So the headline input is churned_lines / total_lines.
  * Process metrics beat product metrics, because complexity suffers from
    "stasis" -- it barely changes between releases, so a complexity-ranked
    list flags the same files forever and developers stop reading it
    (Rahman & Devanbu, ICSE 2013).
  * Recent bug-fixes matter far more than old ones. Google's time-weighted
    risk score decays old fixes on a logistic curve and beat more elaborate
    algorithms on developers' own judgments.

This script deliberately does NOT produce a single "risk" number. Risk is
Impact x Likelihood, and Impact is a business judgment a human supplies. We
compute the half that git can answer and leave the other half blank, because
multiplying two invented ordinals produces fake precision people stop arguing
with (Bach, Heuristic Risk-Based Testing).

Standard library only, so this runs before anything may be installed.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import subprocess
import sys
from collections import defaultdict
from datetime import datetime, timezone

if __package__ in (None, ""):  # run as a script: scripts/ is already sys.path[0]
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from atomic_io import write_text_atomic  # noqa: E402

SCHEMA = "testsquad.riskmap/1"

# A tracked file larger than this is a vendored bundle, a generated fixture or
# a committed binary -- not something a human churns, which is the only thing
# this script measures. 2MB matches env_safety_check.MAX_BYTES on purpose:
# one number for "bigger than hand-written" across the whole plugin.
# Measured on the input that motivated the ceiling: a 49MB generated file cost
# 45s and ~3GB RSS when read whole.
MAX_FILE_BYTES = 2 * 1024 * 1024

# Newlines are counted over a stream, never with fh.read(): current_files()
# only ever wanted an int, and reading a 500MB tracked fixture whole to get it
# is how a line count becomes an out-of-memory kill.
READ_CHUNK = 1024 * 1024

# Conventional-commit "fix:" plus the ways humans actually write it. Kept
# deliberately broad: a missed bug-fix commit silently understates risk, while
# a false positive only adds a little noise to one file's score.
BUGFIX_PATTERN = re.compile(
    r"""(?ix)
    \b(
        fix(e[sd])? | bug(fix)? | defect | regress(ion)? | hotfix | patch
      | broken | crash | fault | issue | incorrect | wrong | fail(ure|ing)?
      | resolve[sd]? | close[sd]? \s* \#\d+
    )\b
    """
)

# Noise that would otherwise dominate churn: lockfiles, generated code,
# vendored dependencies, build output, snapshots.
DEFAULT_EXCLUDES = (
    "node_modules/", "vendor/", "dist/", "build/", "target/", "out/",
    ".min.js", ".min.css", "-lock.json", ".lock", "lock.yaml",
    "__snapshots__/", ".generated.", "_pb2.py", ".pb.go", ".g.dart",
    ".freezed.dart", "migrations/", "/.venv/", "site-packages/",
)

TEST_PATTERN = re.compile(
    r"(^|/)(tests?|spec|__tests__|e2e)/|"
    r"(\.|_|-)(test|spec)\.[a-z]+$|"
    r"(^|/)test_[^/]+\.py$|"
    r"Test[A-Z][^/]*\.(java|kt|cs)$|"
    r"[^/]*Tests?\.(java|kt|cs)$",
    re.IGNORECASE,
)


class GitError(RuntimeError):
    """git was unavailable, or this is not a repository. We stop rather than
    emit a risk map built on nothing."""


# --------------------------------------------------------------------------
# git
# --------------------------------------------------------------------------

def run_git(repo: str, args: list[str]) -> str:
    # core.quotePath=false: by default git renders a non-ASCII path as an
    # escaped, quoted string ("\303\274nicode.py"). `ls-files` and `log
    # --numstat` agree with each other in that form, so the churn still lines
    # up -- but os.path.join(repo, '"\\303\\274nicode.py"') names a file that
    # does not exist, so the file was unreadable, unscored and absent from the
    # map, with nothing said. Turning quoting off makes both commands emit the
    # real path, so the file is counted like any other.
    # errors="replace" rather than the default strict: a filename that is not
    # valid UTF-8 would otherwise raise UnicodeDecodeError out of here. With
    # replace, the mangled path simply fails to open and is reported in
    # skipped_files, which is the same promise this script makes everywhere
    # else -- say what was not read.
    try:
        proc = subprocess.run(
            ["git", "-C", repo, "-c", "core.quotePath=false", *args],
            capture_output=True, text=True, errors="replace", check=False,
        )
    except FileNotFoundError:
        raise GitError("git is not installed or not on PATH")
    if proc.returncode != 0:
        raise GitError(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
    return proc.stdout


def assert_repo(repo: str) -> None:
    out = run_git(repo, ["rev-parse", "--is-inside-work-tree"]).strip()
    if out != "true":
        raise GitError(f"{repo} is not a git repository")


RECORD_SEP = "\x1e"


def read_history(repo: str, since: str | None, max_commits: int,
                 diff_filter: str | None = None) -> list[dict]:
    """One dict per commit: sha, unix timestamp, subject, and per-file churn.

    --no-merges because a merge commit's numstat double-counts the work that
    already appeared on the branch, which would inflate churn for whoever
    merges most often rather than whoever changes the most code.

    diff_filter="M" restricts to modifications of existing files. That is what
    churn means as a defect predictor: Nagappan & Ball measured code *changed*
    between releases. Counting a file's creation as churn would give every
    file in a fresh import a relative_churn of 1.0 -- identical to a file that
    really was rewritten end to end.
    """
    fmt = f"{RECORD_SEP}%H%x00%at%x00%s"
    args = ["log", "--no-merges", "--numstat", f"--format={fmt}",
            f"--max-count={max_commits}"]
    if diff_filter:
        args.append(f"--diff-filter={diff_filter}")
    if since:
        args.append(f"--since={since}")

    commits: list[dict] = []
    for block in run_git(repo, args).split(RECORD_SEP):
        block = block.strip("\n")
        if not block:
            continue
        head, _, body = block.partition("\n")
        parts = head.split("\x00")
        if len(parts) < 3:
            continue
        sha, ts, subject = parts[0], parts[1], parts[2]
        try:
            timestamp = int(ts)
        except ValueError:
            continue

        files: list[tuple[str, int]] = []
        for line in body.splitlines():
            line = line.strip()
            if not line:
                continue
            cols = line.split("\t")
            if len(cols) != 3:
                continue
            added, deleted, path = cols
            if added == "-" or deleted == "-":
                continue  # binary file
            path = _unrename(path)
            try:
                churn = int(added) + int(deleted)
            except ValueError:
                continue
            files.append((path, churn))

        commits.append({
            "sha": sha,
            "timestamp": timestamp,
            "subject": subject,
            "is_bugfix": bool(BUGFIX_PATTERN.search(subject)),
            "files": files,
        })
    return commits


def _unrename(path: str) -> str:
    """git renders renames as 'old => new' or 'dir/{old => new}/file'. We want
    the current path, so later history lines up with the file that exists now."""
    if "=>" not in path:
        return path
    if "{" in path and "}" in path:
        prefix, _, rest = path.partition("{")
        inner, _, suffix = rest.partition("}")
        _, _, new = inner.partition("=>")
        return (prefix + new.strip() + suffix).replace("//", "/")
    _, _, new = path.partition("=>")
    return new.strip()


def count_lines(full: str, *, max_bytes: int = MAX_FILE_BYTES) -> int | None:
    """Newline count for one file, or None when it must not be counted.

    Streamed in READ_CHUNK blocks: this function's whole output is one int, so
    holding the file in memory to produce it buys nothing and costs the file's
    size in RAM. Returns None for a directory, an unreadable file, a binary
    one, or one over `max_bytes` -- the caller reports which.
    """
    try:
        if os.path.getsize(full) > max_bytes:
            return None
        newlines = 0
        last = b""
        with open(full, "rb") as fh:
            first = True
            while True:
                chunk = fh.read(READ_CHUNK)
                if not chunk:
                    break
                if first and b"\x00" in chunk[:8000]:
                    return None  # binary
                first = False
                newlines += chunk.count(b"\n")
                last = chunk
    except (OSError, IsADirectoryError):
        return None
    # A file with no trailing newline still ends in a line.
    return newlines + (1 if last and not last.endswith(b"\n") else 0)


def current_files(repo: str, *, max_bytes: int = MAX_FILE_BYTES
                  ) -> tuple[dict[str, int], list[dict]]:
    """(path -> line count, skipped) for files that exist right now.

    History for deleted files is dropped: a file that no longer exists cannot
    carry risk. A file that exists but was skipped is NOT the same thing, so
    it is returned separately rather than silently missing from the map -- a
    file that vanishes from a risk report reads exactly like a safe one.
    """
    sizes: dict[str, int] = {}
    skipped: list[dict] = []
    for path in run_git(repo, ["ls-files"]).splitlines():
        path = path.strip()
        if not path:
            continue
        full = os.path.join(repo, path)
        lines = count_lines(full, max_bytes=max_bytes)
        if lines is None:
            # Only the over-ceiling case used to be recorded here; every other
            # reason count_lines() can return None -- unreadable, binary,
            # tracked-but-gone, a broken symlink, a gitlink -- fell through a
            # bare `continue`. The file then vanished from the ranking, and a
            # file absent from a risk map reads exactly like a file with no
            # risk. Name every one of them, and say which kind it was.
            skipped.append({"file": path, "reason": skip_reason(full, max_bytes)})
            continue
        sizes[path] = lines
    return sizes, skipped


def human_bytes(n: int) -> str:
    """`2MB`, not `0MB`. Integer-dividing a 1000-byte ceiling by a megabyte
    printed "0MB ceiling", which is a number nobody measured."""
    mb = n / (1024 * 1024)
    if mb >= 1:
        return f"{mb:g}MB"
    kb = n / 1024
    return f"{kb:g}KB" if kb >= 1 else f"{n} bytes"


def oversize_reason(max_bytes: int) -> str:
    return (f"larger than the {human_bytes(max_bytes)} ceiling "
            f"({max_bytes} bytes); raise --max-file-bytes to count it")


# A gitlink is the one entry here that is not a failure to read: a submodule's
# contents belong to another repository and were never in scope. It is still
# listed, because it IS in `git ls-files` and so a reader could otherwise
# wonder where it went -- but it is counted separately from the files this
# script tried and failed to read.
SUBMODULE_REASON = ("a directory (git submodule / gitlink); its contents "
                    "belong to another repository and are not ranked here")


def skip_reason(full: str, max_bytes: int) -> str:
    """Why count_lines() refused this path. Prose, not a code: it is printed.

    Deliberately re-derived from the filesystem rather than threaded back out
    of count_lines(), so that function keeps returning one int and callers
    that only want the count are unaffected.
    """
    try:
        os.lstat(full)
    except FileNotFoundError:
        return "tracked by git but missing from the working tree"
    except OSError as exc:
        return ("tracked by git but not reachable at that path "
                f"({exc.strerror or exc})")
    if os.path.islink(full) and not os.path.exists(full):
        try:
            target = os.readlink(full)
        except OSError:
            target = "?"
        return f"a symbolic link whose target does not exist (-> {target!r})"
    if os.path.isdir(full):
        return ("a symbolic link to a directory" if os.path.islink(full)
                else SUBMODULE_REASON)
    try:
        if os.path.getsize(full) > max_bytes:
            return oversize_reason(max_bytes)
        with open(full, "rb") as fh:
            if b"\x00" in fh.read(8000):
                return ("binary (a NUL byte in the first 8000 bytes); line "
                        "counts and churn-per-line are meaningless for it")
    except OSError as exc:
        return f"unreadable ({exc.strerror or exc})"
    # count_lines() said no and a second look cannot say why. Still reported:
    # an unexplained skip is a worse answer than a named one, but both are
    # better than a file that silently disappears.
    return "could not be read (reason not determined)"


# --------------------------------------------------------------------------
# scoring
# --------------------------------------------------------------------------

def time_weight(timestamp: int, oldest: int, newest: int) -> float:
    """Google's time-weighted risk: 1 / (1 + e^(-12t + 12)), t in [0,1].

    The curve is steeper than it looks. Verified values: t=0 -> 0.000006,
    t=0.5 -> 0.0025, t=0.75 -> 0.047, t=0.9 -> 0.231, t=1 -> 0.5. It tops out
    at 0.5 for the newest commit, and the newest fix outweighs the oldest by
    roughly 81,000x. A fix last month dominates; a fix two years ago is noise.
    Scores are therefore comparable only within one run's window.
    """
    if newest <= oldest:
        return 1.0
    t = (timestamp - oldest) / (newest - oldest)
    return 1.0 / (1.0 + math.exp(-12.0 * t + 12.0))


def is_excluded(path: str, excludes: tuple[str, ...]) -> bool:
    lowered = path.lower()
    return any(token.lower() in lowered for token in excludes)


def build(repo: str, *, since: str | None = None, max_commits: int = 20000,
          excludes: tuple[str, ...] = DEFAULT_EXCLUDES,
          include_tests: bool = False, top: int = 0,
          max_file_bytes: int = MAX_FILE_BYTES) -> dict:
    assert_repo(repo)
    commits = read_history(repo, since, max_commits)
    # Separate pass: modifications only, for the churn numerator.
    modifications = read_history(repo, since, max_commits, diff_filter="M")
    modified_churn: dict[str, int] = defaultdict(int)
    for commit in modifications:
        for path, lines in commit["files"]:
            modified_churn[path] += lines
    created_in_window = {
        path
        for commit in commits
        for path, _ in commit["files"]
    } - set(modified_churn)
    sizes, skipped_files = current_files(repo, max_bytes=max_file_bytes)

    churn: dict[str, int] = defaultdict(int)
    commit_count: dict[str, int] = defaultdict(int)
    bugfix_score: dict[str, float] = defaultdict(float)
    bugfix_count: dict[str, int] = defaultdict(int)
    authors: dict[str, set] = defaultdict(set)
    last_touch: dict[str, int] = {}

    timestamps = [c["timestamp"] for c in commits]
    oldest = min(timestamps) if timestamps else 0
    newest = max(timestamps) if timestamps else 0

    # Authors need a second pass; --numstat and %an in one format is fiddly to
    # parse reliably, and contributor spread is only a tie-breaker.
    author_by_sha = _authors_by_sha(repo, since, max_commits)

    for commit in commits:
        weight = time_weight(commit["timestamp"], oldest, newest)
        author = author_by_sha.get(commit["sha"], "")
        for path, lines in commit["files"]:
            if path not in sizes:
                continue  # deleted or never tracked
            if is_excluded(path, excludes):
                continue
            if not include_tests and TEST_PATTERN.search(path):
                continue
            commit_count[path] += 1
            if author:
                authors[path].add(author)
            last_touch[path] = max(last_touch.get(path, 0), commit["timestamp"])
            if commit["is_bugfix"]:
                bugfix_score[path] += weight
                bugfix_count[path] += 1

    for path, lines in modified_churn.items():
        if path in commit_count:
            churn[path] = lines

    files = []
    for path in sorted(commit_count):
        total_lines = max(sizes.get(path, 0), 1)
        relative_churn = churn.get(path, 0) / total_lines
        files.append({
            "path": path,
            "lines": sizes.get(path, 0),
            "churned_lines": churn.get(path, 0),
            "new_in_window": path in created_in_window,
            "relative_churn": round(relative_churn, 4),
            "commits": commit_count[path],
            "bugfix_commits": bugfix_count[path],
            "time_weighted_risk": round(bugfix_score[path], 4),
            "authors": len(authors[path]),
            "last_touched": datetime.fromtimestamp(
                last_touch.get(path, 0), timezone.utc).date().isoformat()
            if last_touch.get(path) else None,
        })

    _assign_likelihood(files)
    files.sort(key=lambda f: (-f["likelihood_raw"], f["path"]))
    if top:
        files = files[:top]

    # A window that caught nothing produces an empty map, which reads exactly
    # like "this repo is low risk". Say which it is.
    warnings = []
    if not commits:
        warnings.append(
            f"No commits found in the window (since={since!r}). "
            "This is absence of data, not absence of risk -- widen --since."
        )
    elif not files:
        warnings.append(
            "Commits were found but no scoreable files: everything was "
            "excluded, deleted, a test file" +
            (", or could not be read (see skipped_files)."
             if skipped_files else ".")
        )
    if skipped_files:
        # A file that was never read cannot be scored, and an unscored file is
        # absent from the ranking -- which reads exactly like a quiet, safe
        # file. Say it out loud instead. The warning used to claim every skip
        # was an over-ceiling one, which was the only kind it recorded; now it
        # counts what actually happened.
        oversized = sum(1 for s in skipped_files
                        if s["reason"] == oversize_reason(max_file_bytes))
        gitlinks = sum(1 for s in skipped_files
                       if s["reason"] == SUBMODULE_REASON)
        unread = len(skipped_files) - oversized - gitlinks
        parts = []
        if unread:
            parts.append(f"{unread} could not be read")
        if oversized:
            parts.append(f"{oversized} over the {human_bytes(max_file_bytes)} "
                         "ceiling (raise --max-file-bytes)")
        if gitlinks:
            parts.append(f"{gitlinks} submodule(s)")
        warnings.append(
            f"{len(skipped_files)} tracked path(s) carry no score and are "
            f"absent from the ranking: {'; '.join(parts)}. That is 'not "
            "analysed', not 'no risk' -- see skipped_files for each path and "
            "why."
        )

    return {
        "schema": SCHEMA,
        "repo": os.path.abspath(repo),
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "window": {
            "since": since,
            "commits_analyzed": len(commits),
            "oldest": datetime.fromtimestamp(oldest, timezone.utc).date().isoformat() if oldest else None,
            "newest": datetime.fromtimestamp(newest, timezone.utc).date().isoformat() if newest else None,
        },
        "note": (
            "Likelihood only. Risk = Impact x Likelihood, and Impact is a "
            "business judgment that belongs in acceptance-contract.md. These "
            "two are deliberately never multiplied into one number here."
        ),
        "warnings": warnings,
        "skipped_files": skipped_files,
        # Two counts, never one: "ranked 3" alone cannot tell a reader whether
        # the fourth tracked file was safe or simply never opened. The same
        # pair is what arid_nodes.summary.files / files_skipped carries.
        "summary": {
            "files_ranked": len(files),
            "files_skipped": len(skipped_files),
        },
        "files": files,
    }


def _authors_by_sha(repo: str, since: str | None, max_commits: int) -> dict[str, str]:
    args = ["log", "--no-merges", "--format=%H%x00%ae", f"--max-count={max_commits}"]
    if since:
        args.append(f"--since={since}")
    out: dict[str, str] = {}
    for line in run_git(repo, args).splitlines():
        sha, _, email = line.partition("\x00")
        if sha:
            out[sha] = email
    return out


def _assign_likelihood(files: list[dict]) -> None:
    """Map raw scores onto a 1-5 ordinal by percentile within this repo.

    Percentile, not an absolute threshold: "how risky is this file compared to
    the rest of this codebase" is answerable, while an absolute churn cutoff
    would be meaningless across a 500-line script and a 2M-line monorepo.
    """
    if not files:
        return

    churn_vals = sorted(f["relative_churn"] for f in files)
    fix_vals = sorted(f["time_weighted_risk"] for f in files)

    for f in files:
        # Relative churn is the strongest single input, so it carries the most
        # weight. Recent bug-fixes are the second. Everything else is a nudge.
        raw = (
            2.0 * _percentile_of(churn_vals, f["relative_churn"])
            + 1.5 * _percentile_of(fix_vals, f["time_weighted_risk"])
            + 0.3 * min(f["authors"] / 5.0, 1.0)
        )
        f["likelihood_raw"] = round(raw, 4)

    raws = sorted(f["likelihood_raw"] for f in files)
    for f in files:
        pct = _percentile_of(raws, f["likelihood_raw"])
        # 1-5, where 5 is the top ~10% of files in this repo.
        if pct >= 0.90:
            f["likelihood"] = 5
        elif pct >= 0.75:
            f["likelihood"] = 4
        elif pct >= 0.50:
            f["likelihood"] = 3
        elif pct >= 0.25:
            f["likelihood"] = 2
        else:
            f["likelihood"] = 1

        # Percentiles are relative, so in a small or uniformly quiet repo an
        # untouched file can land mid-table purely for lack of competition.
        # A file nobody has modified and nobody has fixed is not a risk,
        # whatever its rank.
        if not f["churned_lines"] and not f["bugfix_commits"]:
            f["likelihood"] = min(f["likelihood"], 2)

        f["why"] = _explain(f)


def _percentile_of(sorted_vals: list[float], value: float) -> float:
    """Fraction of values at or below `value`. Ties share the upper rank so a
    repo where most files score 0 doesn't push every zero into a high band."""
    if not sorted_vals:
        return 0.0
    lo, hi = 0, len(sorted_vals)
    while lo < hi:
        mid = (lo + hi) // 2
        if sorted_vals[mid] <= value:
            lo = mid + 1
        else:
            hi = mid
    return lo / len(sorted_vals)


def _explain(f: dict) -> str:
    """Google's bug prediction ranked correctly and changed no behaviour,
    because developers could not see why a file was flagged. Every score here
    ships with the sentence that justifies it."""
    bits = []

    # Churn: exactly one phrase, chosen by magnitude. Below 1% the percentage
    # rounds to "0% churned", which reads as a contradiction beside a real
    # signal, so give the raw count instead.
    churn, lines = f["relative_churn"], f["churned_lines"]
    if f["new_in_window"] and not lines:
        bits.append("added in this window, never modified since")
    elif churn >= 1.0:
        bits.append(f"rewritten {churn:.1f}x over its own size")
    elif churn >= 0.01:
        bits.append(f"{int(churn * 100)}% of its lines churned")
    elif lines:
        bits.append(f"{lines} line{'s' if lines > 1 else ''} changed")

    if f["bugfix_commits"]:
        recency = "recently" if f["time_weighted_risk"] >= 0.5 else "historically"
        plural = "es" if f["bugfix_commits"] > 1 else ""
        bits.append(f"{f['bugfix_commits']} bug-fix{plural} ({recency})")

    if f["authors"] >= 5:
        bits.append(f"{f['authors']} contributors")

    if not bits:
        bits.append("little recent change")
    return "; ".join(bits)


# --------------------------------------------------------------------------
# cli
# --------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="risk_map.py",
        description="Rank files by defect likelihood using git history.",
    )
    ap.add_argument("repo", nargs="?", default=".", help="repository path")
    ap.add_argument("--since", default="2 years ago",
                    help="history window (git --since syntax, default '2 years ago')")
    ap.add_argument("--max-commits", type=int, default=20000)
    ap.add_argument("--top", type=int, default=0, help="keep only the N riskiest files")
    ap.add_argument("--include-tests", action="store_true",
                    help="score test files too (off by default)")
    ap.add_argument("--exclude", action="append", default=[],
                    help="extra path substring to ignore; repeatable")
    ap.add_argument("--out", help="write JSON here instead of stdout")
    ap.add_argument("--max-file-bytes", type=int, default=MAX_FILE_BYTES,
                    help="skip tracked files larger than this when counting "
                         f"lines (default {MAX_FILE_BYTES}, i.e. 2MB); skipped "
                         "files are listed in skipped_files")
    args = ap.parse_args(argv)

    try:
        result = build(
            args.repo,
            since=args.since or None,
            max_commits=args.max_commits,
            excludes=DEFAULT_EXCLUDES + tuple(args.exclude),
            include_tests=args.include_tests,
            top=args.top,
            max_file_bytes=args.max_file_bytes,
        )
    except GitError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    payload = json.dumps(result, indent=2)
    if args.out:
        # Atomic: parallel agents all write under .testing/, and a half-written
        # risk map that still parses is worse than no risk map at all.
        try:
            write_text_atomic(args.out, payload + "\n")
        except OSError as exc:
            print(f"error: cannot write {args.out}: {exc}", file=sys.stderr)
            return 2
        print(f"{len(result['files'])} files scored from "
              f"{result['window']['commits_analyzed']} commits "
              f"-> {args.out}",
              file=sys.stderr)
        for f in result["files"][:5]:
            print(f"  L{f['likelihood']}  {f['path']}  ({f['why']})", file=sys.stderr)
    else:
        print(payload)

    for warning in result["warnings"]:
        print(f"warning: {warning}", file=sys.stderr)
    render_skipped(result, sys.stderr)
    return 0


def render_skipped(result: dict, stream) -> None:
    """The ranking's companion line: what is NOT in it, and why.

    Both output modes get this. Without it `3 files scored` is the same
    sentence whether the fourth tracked file was safe or was never opened --
    and the one a reader must act on is the second.
    """
    skipped = result.get("skipped_files") or []
    summary = result.get("summary") or {}
    ranked = summary.get("files_ranked", len(result.get("files") or []))
    if not skipped:
        return
    print(f"{ranked} file(s) ranked; {len(skipped)} tracked path(s) NOT "
          "ranked (see skipped_files):", file=stream)
    for entry in skipped[:5]:
        print(f"  skipped {entry['file']}: {entry['reason']}", file=stream)
    if len(skipped) > 5:
        print(f"  ... and {len(skipped) - 5} more", file=stream)


if __name__ == "__main__":
    sys.exit(main())
