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
    no branch coverage; reporting that as 0% would be a lie that reads as a gap."""
    if found <= 0:
        return None
    return round(100.0 * hit / found, 2)


class Accumulator:
    def __init__(self) -> None:
        self._files: dict[str, FileCoverage] = {}

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
                if raw_missed is None or raw_covered is None:
                    print(f"warning: {path}: {ctype or 'untyped'} counter is missing "
                          "'missed' or 'covered'; skipping it — the totals below "
                          "exclude this counter", file=sys.stderr)
                    continue
                try:
                    missed = int(raw_missed)
                    covered = int(raw_covered)
                except ValueError:
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

    return {
        "schema": SCHEMA,
        "source_format": fmt,
        "source_file": source,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "totals": totals,
        "files": [f.to_dict(max_uncovered) for f in files],
    }


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
        text, source = sys.stdin.read(), "<stdin>"
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
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(payload + "\n")
        t = result["totals"]
        print(f"{source} [{fmt}] -> {args.out}: "
              f"{t['files']} files, lines {t['line_pct']}%, branches {t['branch_pct']}%",
              file=sys.stderr)
    else:
        print(payload)
    return 0


if __name__ == "__main__":
    sys.exit(main())
