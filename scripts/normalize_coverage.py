#!/usr/bin/env python3
"""Normalize coverage reports from any supported tool into one JSON shape.

Supported inputs (auto-detected):
  lcov        lcov.info / *.info          (JS/TS, C/C++, Rust, Swift)
  cobertura   coverage.xml                (Python, .NET, PHP, Ruby, Dart)
  jacoco      jacoco.xml                  (Java/Kotlin/JVM)
  gocover     coverage.out                (Go)
  coveragepy  coverage.json               (Python, native JSON)

Output schema: testsquad.coverage/1 (printed to stdout).

Standard library only, on purpose: this runs before we are allowed to
install anything, and every number in every report traces back to it.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

if __package__ in (None, ""):  # run as a script: scripts/ is already sys.path[0]
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from atomic_io import write_text_atomic  # noqa: E402

SCHEMA = "testsquad.coverage/1"


# --------------------------------------------------------------------------
# result accumulation
# --------------------------------------------------------------------------

class FileCoverage:
    """Per-file counters. Lines and branches are tracked separately because
    conflating them is how a suite claims 90% while every branch is untested."""

    __slots__ = ("path", "lines_found", "lines_hit", "branches_found",
                 "branches_hit", "uncovered_lines")

    def __init__(self, path: str) -> None:
        self.path = path
        self.lines_found = 0
        self.lines_hit = 0
        self.branches_found = 0
        self.branches_hit = 0
        self.uncovered_lines: set[int] = set()

    def to_dict(self, max_uncovered: int) -> dict:
        uncovered = sorted(self.uncovered_lines)
        return {
            "path": self.path,
            "lines_found": self.lines_found,
            "lines_hit": self.lines_hit,
            "line_pct": pct(self.lines_hit, self.lines_found),
            "branches_found": self.branches_found,
            "branches_hit": self.branches_hit,
            "branch_pct": pct(self.branches_hit, self.branches_found),
            "uncovered_lines": uncovered[:max_uncovered],
            "uncovered_lines_truncated": max(0, len(uncovered) - max_uncovered),
        }


def pct(hit: int, found: int) -> float | None:
    """None, not 0.0, when nothing was measured. A file with no branches has
    no branch coverage; reporting that as 0% would be a lie that reads as a gap.

    None again when the report contradicts itself -- more hits than there were
    things to hit. 250% is not a measurement anybody took, and the arithmetic
    that produced it says the inputs are wrong, not that coverage is excellent.
    Clamping to 100 would be worse still: it would turn a broken report into a
    perfect score, which is the one shape a normalizer must never produce.
    """
    if found <= 0:
        return None
    if hit > found:
        return None
    return round(100.0 * hit / found, 2)


class Accumulator:
    def __init__(self) -> None:
        self._files: dict[str, FileCoverage] = {}
        # Every measurement that was dropped, and why. These travel in the
        # output JSON as well as to stderr: a consumer that only reads the
        # JSON must not see a null and assume the file simply had no branches.
        self.warnings: list[str] = []

    def warn(self, message: str) -> None:
        self.warnings.append(message)
        print(f"warning: {message}", file=sys.stderr)

    def file(self, path: str) -> FileCoverage:
        path = normalize_path(path)
        if path not in self._files:
            self._files[path] = FileCoverage(path)
        return self._files[path]

    def files(self) -> list[FileCoverage]:
        return sorted(self._files.values(), key=lambda f: f.path)


def normalize_path(path: str) -> str:
    path = path.replace("\\", "/").strip()
    while path.startswith("./"):
        path = path[2:]
    return path


# --------------------------------------------------------------------------
# format detection
# --------------------------------------------------------------------------

def read_text(path: str) -> str:
    """Decode by what the bytes say they are, not by hope.

    A UTF-16 report read as UTF-8 turns into mojibake: no DA: record matches,
    and the run ends with a confident zero that reads downstream exactly like
    "this project has no coverage data". Sniffing the BOM is the only way to
    tell those bytes apart from a genuinely empty report.
    """
    with open(path, "rb") as fh:
        raw = fh.read()
    return decode_bytes(raw)


def decode_bytes(raw: bytes) -> str:
    """The decode half of read_text(), reusable by the stdin path.

    `-` is a documented input and it used to go through sys.stdin, which
    decodes UTF-8 and nothing else: the same UTF-16 report that normalizes
    correctly from a file died on a UnicodeDecodeError traceback from a pipe.
    One decoder for both paths is the only way those two stay in agreement.
    """
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw.decode("utf-8-sig", errors="replace")
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        # The BOM itself picks LE or BE. errors="replace" because one
        # truncated unit must not cost us the rest of the report.
        return raw.decode("utf-16", errors="replace")
    # No BOM: UTF-8 is the only sane default, and errors="replace" keeps a
    # single stray byte inside a path from losing the whole file.
    return raw.decode("utf-8", errors="replace")


def detect_format(text: str, filename: str) -> str:
    # A UTF-8 BOM survives as a leading ﻿, which is not whitespace and so
    # defeats startswith("{"). coverage.py reads its own BOM'd report fine; we
    # must not refuse a file its own ecosystem considers valid.
    head = text.lstrip("﻿").lstrip()[:4000]
    base = os.path.basename(filename).lower()

    if head.startswith("mode:") or re.match(r"^mode:\s*(set|count|atomic)", head):
        return "gocover"
    if head.startswith("{"):
        return "coveragepy"
    if head.startswith("<"):
        low = head.lower()
        if "<report" in low and "jacoco" in low:
            return "jacoco"
        if "<coverage" in low:
            return "cobertura"
        if "<report" in low:
            return "jacoco"
        raise Detect("XML given, but it is neither Cobertura nor JaCoCo")
    if re.search(r"^(SF:|TN:|DA:)", head, re.M):
        return "lcov"
    if base.endswith(".info"):
        return "lcov"
    raise Detect(f"could not recognise the format of {filename!r}")


class Detect(Exception):
    """Raised when the input format cannot be identified. We stop rather than
    guess: a misparsed report produces confident wrong numbers."""


# --------------------------------------------------------------------------
# parsers
# --------------------------------------------------------------------------

def parse_lcov(text: str, acc: Accumulator) -> None:
    current: FileCoverage | None = None
    # BRDA lines repeat per branch; a '-' means the branch was never reached.
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        tag, _, value = line.partition(":")
        if tag == "SF":
            current = acc.file(value)
        elif current is None:
            continue
        elif tag == "DA":
            parts = value.split(",")
            if len(parts) < 2:
                continue
            try:
                lineno, count = int(parts[0]), int(float(parts[1]))
            except ValueError:
                continue
            current.lines_found += 1
            if count > 0:
                current.lines_hit += 1
            else:
                current.uncovered_lines.add(lineno)
        elif tag == "BRDA":
            parts = value.split(",")
            if len(parts) < 4:
                continue
            current.branches_found += 1
            taken = parts[3]
            if taken != "-" and taken.isdigit() and int(taken) > 0:
                current.branches_hit += 1
        elif tag == "end_of_record":
            current = None
    # LF/LH/BRF/BRH are deliberately ignored: DA/BRDA are authoritative and
    # some generators emit summary lines that disagree with their own detail.


def parse_cobertura(text: str, acc: Accumulator) -> None:
    root = ET.fromstring(text)
    sources = [s.text.strip() for s in root.findall("./sources/source")
               if s.text and s.text.strip() not in ("", ".", "/")]

    for cls in root.iter("class"):
        filename = cls.get("filename")
        if not filename:
            continue
        if sources and not os.path.isabs(filename):
            filename = _shortest_existing(sources, filename)
        fc = acc.file(filename)
        for line in cls.iter("line"):
            try:
                lineno = int(line.get("number", "0"))
                hits = int(float(line.get("hits", "0")))
            except ValueError:
                continue
            fc.lines_found += 1
            if hits > 0:
                fc.lines_hit += 1
            else:
                fc.uncovered_lines.add(lineno)
            if line.get("branch", "").lower() == "true":
                found, hit = _cobertura_branch(line.get("condition-coverage", ""))
                fc.branches_found += found
                fc.branches_hit += hit


_COND = re.compile(r"\((\d+)/(\d+)\)")


def _cobertura_branch(condition: str) -> tuple[int, int]:
    """condition-coverage looks like '50% (1/2)'. Without the parenthesised
    counts there is nothing trustworthy to extract, so we count nothing."""
    m = _COND.search(condition or "")
    if not m:
        return (0, 0)
    hit, found = int(m.group(1)), int(m.group(2))
    return (found, hit)


def _shortest_existing(sources: list[str], filename: str) -> str:
    for src in sources:
        candidate = os.path.join(src, filename)
        if os.path.exists(candidate):
            return candidate
    return filename


def parse_jacoco(text: str, acc: Accumulator) -> None:
    # JaCoCo nests sourcefile under package; the package name carries the path.
    root = ET.fromstring(text)
    for package in root.iter("package"):
        pkg_path = (package.get("name") or "").strip("/")
        for sf in package.iter("sourcefile"):
            name = sf.get("name")
            if not name:
                continue
            path = f"{pkg_path}/{name}" if pkg_path else name
            fc = acc.file(path)
            for counter in sf.findall("counter"):
                ctype = counter.get("type")
                # Both halves are required. Defaulting a missing `missed` to
                # zero turns a truncated counter into 100% coverage -- the one
                # shape a normalizer must never produce, because a gate waves
                # a perfect score straight through.
                raw_missed = counter.get("missed")
                raw_covered = counter.get("covered")
                label = f"{path}: {ctype or 'untyped'} counter"
                if raw_missed is None or raw_covered is None:
                    acc.warn(f"{label} is missing 'missed' or 'covered'; "
                             "skipping it — the totals exclude this counter")
                    continue
                try:
                    missed = int(raw_missed)
                    covered = int(raw_covered)
                except ValueError:
                    # A bare `continue` here dropped the counter without a
                    # word, and a silently dropped counter is a silently
                    # wrong total.
                    acc.warn(f"{label} has non-integer values "
                             f"(missed={raw_missed!r}, covered={raw_covered!r}); "
                             "skipping it — the totals exclude this counter")
                    continue
                if missed < 0 or covered < 0:
                    # missed="-5" covered="10" made found = 5 against 10 hit,
                    # and printed 200%. Negative counts are not a measurement
                    # of anything, so nothing here is salvageable.
                    acc.warn(f"{label} has a negative count "
                             f"(missed={missed}, covered={covered}); "
                             "skipping it — the totals exclude this counter")
                    continue
                if ctype == "LINE":
                    fc.lines_found += missed + covered
                    fc.lines_hit += covered
                elif ctype == "BRANCH":
                    fc.branches_found += missed + covered
                    fc.branches_hit += covered
            for line in sf.findall("line"):
                try:
                    nr = int(line.get("nr", "0"))
                    ci = int(line.get("ci", "0"))   # covered instructions
                except ValueError:
                    continue
                if ci == 0 and nr > 0:
                    fc.uncovered_lines.add(nr)


_GO_LINE = re.compile(
    r"^(?P<path>.+?):(?P<sl>\d+)\.(?P<sc>\d+),(?P<el>\d+)\.(?P<ec>\d+)\s+"
    r"(?P<stmts>\d+)\s+(?P<count>\d+)\s*$"
)


def parse_gocover(text: str, acc: Accumulator) -> None:
    """Go reports statement blocks, not lines. Blocks can repeat for the same
    span across -count runs, so hits are merged per block before counting."""
    blocks: dict[str, dict[tuple, list]] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("mode:"):
            continue
        m = _GO_LINE.match(line)
        if not m:
            continue
        path = m.group("path")
        key = (int(m.group("sl")), int(m.group("sc")),
               int(m.group("el")), int(m.group("ec")))
        stmts, count = int(m.group("stmts")), int(m.group("count"))
        per_file = blocks.setdefault(path, {})
        if key in per_file:
            per_file[key][1] += count
        else:
            per_file[key] = [stmts, count]

    for path, per_file in blocks.items():
        fc = acc.file(path)
        for (start_line, _sc, end_line, _ec), (stmts, count) in per_file.items():
            fc.lines_found += stmts
            if count > 0:
                fc.lines_hit += stmts
            else:
                # Record the span start; Go gives no per-line detail.
                fc.uncovered_lines.add(start_line)


def parse_coveragepy(text: str, acc: Accumulator) -> None:
    # A BOM read from stdin never reaches read_text(), and json.loads chokes
    # on it; strip it here so the two input paths behave identically.
    data = json.loads(text.lstrip("﻿"))
    if not isinstance(data, dict):
        # A top-level array is a JSON file, but not a coverage.py report.
        # Saying so is the job; crashing with AttributeError is not.
        raise Detect("JSON given, but its top level is "
                     f"{type(data).__name__}, not an object (not a coverage.py report)")
    files = data.get("files")
    if not isinstance(files, dict):
        raise Detect("JSON given, but it has no 'files' map (not a coverage.py report)")
    for path, entry in files.items():
        summary = entry.get("summary", {}) if isinstance(entry, dict) else {}
        fc = acc.file(path)
        fc.lines_found += int(summary.get("num_statements", 0) or 0)
        fc.lines_hit += int(summary.get("covered_lines", 0) or 0)
        fc.branches_found += int(summary.get("num_branches", 0) or 0)
        fc.branches_hit += int(summary.get("covered_branches", 0) or 0)
        for lineno in entry.get("missing_lines", []) or []:
            try:
                fc.uncovered_lines.add(int(lineno))
            except (TypeError, ValueError):
                continue


# Formats whose reports are a list of per-file records: zero records means the
# input was not what it claimed, never "this project measured nothing".
RECORD_FORMATS = {"lcov", "gocover", "coveragepy"}

PARSERS = {
    "lcov": parse_lcov,
    "cobertura": parse_cobertura,
    "jacoco": parse_jacoco,
    "gocover": parse_gocover,
    "coveragepy": parse_coveragepy,
}


# --------------------------------------------------------------------------
# entry point
# --------------------------------------------------------------------------

def normalize(text: str, fmt: str, *, source: str = "", strip_prefix: str = "",
              max_uncovered: int = 200) -> dict:
    acc = Accumulator()
    PARSERS[fmt](text, acc)

    files = acc.files()
    if strip_prefix:
        prefix = normalize_path(strip_prefix).rstrip("/") + "/"
        for fc in files:
            if fc.path.startswith(prefix):
                fc.path = fc.path[len(prefix):]

    totals = {
        "files": len(files),
        "lines_found": sum(f.lines_found for f in files),
        "lines_hit": sum(f.lines_hit for f in files),
        "branches_found": sum(f.branches_found for f in files),
        "branches_hit": sum(f.branches_hit for f in files),
    }
    totals["line_pct"] = pct(totals["lines_hit"], totals["lines_found"])
    totals["branch_pct"] = pct(totals["branches_hit"], totals["branches_found"])

    # pct() has already refused to turn a self-contradicting count into a
    # percentage. That refusal shows up as a null, and a null is also what a
    # file with no branches produces -- so without this, "nothing to measure"
    # and "the report disagrees with itself" render identically.
    for fc in files:
        _flag_contradiction(acc, fc.path, "line", fc.lines_hit, fc.lines_found)
        _flag_contradiction(acc, fc.path, "branch",
                            fc.branches_hit, fc.branches_found)

    return {
        "schema": SCHEMA,
        "source_format": fmt,
        "source_file": source,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "warnings": list(acc.warnings),
        "totals": totals,
        "files": [f.to_dict(max_uncovered) for f in files],
    }


def _flag_contradiction(acc: Accumulator, path: str, kind: str,
                        hit: int, found: int) -> None:
    """Warn when more was hit than exists. `found == 0` is deliberately NOT
    exempt.

    There used to be a `found > 0` guard here, and it carved out the one case
    where silence is most dangerous. Cobertura's `condition-coverage="(3/0)"`
    gives hit=3, found=0: pct() already refuses it, so branch_pct is null --
    and null is also what a file with no branches produces. With the guard,
    warnings stayed empty too, so "3 of 0 branches covered", which cannot
    happen, rendered indistinguishably from "this file has no branches",
    which happens constantly.

    The line between the two is hit, not found:
      * hit == 0 and found == 0 -- nothing to cover. Legitimate, common, and
        stays silent: `hit > found` is False, so this function says nothing.
      * hit  > 0 and found == 0 -- impossible. Warned, like every other
        self-contradicting count.
    """
    if hit > found:
        acc.warn(f"{path}: {hit} {_plural(kind)} recorded as covered out of "
                 f"{found} that exist — the report contradicts itself, so "
                 f"{kind} coverage for this file is reported as null, not as "
                 "a percentage")


def _plural(kind: str) -> str:
    return f"{kind}es" if kind.endswith("ch") else f"{kind}s"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="normalize_coverage.py",
        description="Normalize any supported coverage report into testsquad JSON.",
    )
    ap.add_argument("input", help="coverage file, or '-' for stdin")
    ap.add_argument("--format", default="auto",
                    choices=["auto", *PARSERS.keys()],
                    help="input format (default: auto-detect)")
    ap.add_argument("--out", help="write JSON here instead of stdout")
    ap.add_argument("--strip-prefix", default="",
                    help="remove this leading path segment from every file path")
    ap.add_argument("--max-uncovered", type=int, default=200,
                    help="cap uncovered line numbers kept per file (default 200)")
    args = ap.parse_args(argv)

    if args.input == "-":
        # Read bytes, not sys.stdin's UTF-8-only text: a UTF-16 report piped
        # in used to die on a UnicodeDecodeError traceback while the identical
        # file normalized correctly. getattr() because a test double, or any
        # caller that replaced sys.stdin with a StringIO, has no .buffer.
        buffer = getattr(sys.stdin, "buffer", None)
        text = decode_bytes(buffer.read()) if buffer is not None else sys.stdin.read()
        source = "<stdin>"
    else:
        if not os.path.exists(args.input):
            print(f"error: no such file: {args.input}", file=sys.stderr)
            return 2
        text = read_text(args.input)
        source = args.input

    if not text.strip():
        print(f"error: {source} is empty — nothing to normalize", file=sys.stderr)
        return 2

    try:
        fmt = detect_format(text, source) if args.format == "auto" else args.format
        result = normalize(text, fmt, source=source,
                           strip_prefix=args.strip_prefix,
                           max_uncovered=args.max_uncovered)
    except Detect as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except (ET.ParseError, json.JSONDecodeError) as exc:
        print(f"error: {source} is malformed: {exc}", file=sys.stderr)
        return 2

    # "parsed nothing" and "measured nothing" must not render identically. A
    # record-oriented report that yields no record at all was mis-decoded or
    # truncated, not produced by a project without tests, and exiting 0 with
    # an empty report lets a coverage gate read it as clean.
    # The XML formats are excluded: a Cobertura/JaCoCo document legitimately
    # parses to zero classes, and refusing those would be a new false alarm.
    if fmt in RECORD_FORMATS and result["totals"]["files"] == 0:
        print(f"error: {source} was read as {fmt} but contains no coverage "
              "records — check the file is complete, and that --format "
              "names the tool that wrote it", file=sys.stderr)
        return 2

    payload = json.dumps(result, indent=2)
    if args.out:
        write_text_atomic(args.out, payload + "\n")
        t = result["totals"]
        # "null" rather than a number, spelled out: a percentage that is
        # missing has to look different from one that is zero.
        def shown(value):
            return "not measured" if value is None else f"{value}%"
        dropped = len(result["warnings"])
        note = f", {dropped} measurement(s) dropped — see warnings above" if dropped else ""
        print(f"{source} [{fmt}] -> {args.out}: "
              f"{t['files']} files, lines {shown(t['line_pct'])}, "
              f"branches {shown(t['branch_pct'])}{note}",
              file=sys.stderr)
    else:
        print(payload)
    return 0


if __name__ == "__main__":
    sys.exit(main())
