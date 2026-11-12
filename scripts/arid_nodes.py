#!/usr/bin/env python3
"""Decide which lines are worth mutating, and suppress the rest.

WHY THIS IS THE MOST IMPORTANT STEP IN THE MUTATION PATH
--------------------------------------------------------
Google ran mutation testing over 16.9M mutants and 776,740 changelists
(Petrovic & Ivankovic, TSE 2021; docs/RESEARCH.md §15). The numbers that
matter here:

  * Naive generation produces a median of **820 mutants per changelist**.
    After suppression and one-mutant-per-line that median is **7**.
  * Only **12.5%** of generated mutants are ever shown to a developer at
    all; the median actually surfaced is **2**, with a hard cap of
    **7 per file**.
  * Developer-reported usefulness went from **15% to 89%** over six years.
    Nothing in the tooling changed to do that -- the rules grew, fed by
    developer feedback on 66,798 judged mutants.
  * Google needed 100+ suppression rules and 200+ function families. We
    need maybe 15-20 to capture most of the benefit, which is what this
    file is.

**This step matters more than the choice of mutation tool.** A tool that
emits 820 mutants with a perfect operator set is worse than a crude tool
emitting 7 good ones, because the 820 are never read.

WHAT AN ARID NODE IS
--------------------
A syntax node whose mutation is almost never informative. Mutating it
yields either an equivalent mutant (nothing observable changed) or a
killable-but-pointless one (a test fails for a reason nobody cares about).
Both spend the same scarce resource: a developer's attention. A logging
call is the canonical example -- `logger.info("saved %s", id)` can be
deleted, negated or re-ordered and no one learns anything from the test
that does or does not notice.

WHEN UNSURE, SUPPRESS
---------------------
The two errors are not symmetric. A pointless mutant costs a developer's
attention and, repeated, teaches them to ignore the tool -- which takes
every true positive with it. A missed mutant costs one unit of theoretical
coverage that nobody was going to act on. So every rule here errs toward
suppression, and where a rule is a guess it is a guess in that direction.

The one thing suppression may never do is swallow real business logic.
Arithmetic on money, a comparison in a boundary check, a logical connector
in an authorization decision, a loop condition: these must stay mutable
however they are named or wherever they sit. Those four shapes have
explicit "must remain mutable" tests in tests/test_arid_nodes.py, and the
rules below are deliberately narrowed (bare call statements, literal-only
right-hand sides, null-check guards) so that they cannot reach them.

ONE MUTANT PER LINE
-------------------
Where several operators apply to a line we pick exactly one. Google's
justification, which we adopt: in **>90% of lines with multiple applicable
mutants, all the mutants share the same fate** -- the same test kills all
of them, or none does. Generating the rest is nearly pure cost. The pick
is seeded and derived from a hash of (seed, path, line, snippet), so two
runs over the same input choose the same operator, and editing a line
re-rolls only that line.

THE OPERATOR SET
----------------
Exactly the five Google uses, and no more: SBR, UOI, LCR, ROR, AOR.
Exotic operator sets buy <=13% over plain random selection, which does not
pay for the mutants they add. Selection is weighted by the measured
productivity of each operator -- ROR was judged productive 84.1% of the
time and UOI only 74.5% -- so ties lean toward ROR and away from UOI.

THE FEEDBACK LOOP
-----------------
`--feedback .testing/feedback.json` is the mechanism behind 15% -> 89%.
Developers mark a node type, an operator, a function family or a line
pattern as useless; those are suppressed on the next run and the output
records which suppressions came from feedback rather than from a built-in
rule. That is how a 15-rule file grows into a 100-rule one without anyone
rewriting it.

Standard library only, so this runs before anything may be installed.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone

if __package__ in (None, ""):  # run as a script: scripts/ is already sys.path[0]
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from atomic_io import write_text_atomic  # noqa: E402

SCHEMA = "testsquad.aridnodes/1"

# Same ceiling and same reasoning as flake_smells.MAX_FILE_BYTES and
# env_safety_check.MAX_BYTES: read_lines() holds the bytes, the decoded text
# and the line list at once, and a 49MB generated file was measured at 45s and
# ~3GB RSS. A source file over 2MB is generated or vendored -- and a mutation
# planner has nothing useful to say about generated code anyway. Over the
# ceiling the file is REPORTED in skipped_files, never quietly dropped.
MAX_FILE_BYTES = 2 * 1024 * 1024

# The file ceiling above bounds nothing *within* a file, and this script had no
# per-line bound at all. Measured on this machine before the cap, with the
# whole file comfortably under MAX_FILE_BYTES:
#
#   one line of "x = a + a + a + ..."   100,000 chars   0.080s
#                                       400,000 chars   RecursionError
#                                                       (ast.parse, uncaught,
#                                                        kills the whole run)
#   one line of spaces inside assert    400,000 chars   0.113s
#
# Same number as flake_smells.MAX_LINE_CHARS, and for the same reason: no
# hand-written source line is 2,000 characters wide, so a line that is, is
# minified, generated or vendored -- and a mutation planner has nothing useful
# to say about any of those. Line numbers and the file's line count are
# unchanged, so every planned mutant still points where it did, and every
# shortened line is REPORTED in truncated_lines. A planner that quietly
# shortens its input would report "no mutants here" about code it never read,
# which is indistinguishable from a genuinely unmutable file.
MAX_LINE_CHARS = 2000

# The one ceiling in this file we do NOT set and cannot raise.
#
# `ast.parse` builds the Python AST objects from the C parse tree with a
# recursive C function (`ast2obj_expr` in Python-ast.c), so a deeply NESTED
# expression costs C stack, and past some depth `ast.parse` raises
# `RecursionError`. That depth is not `sys.getrecursionlimit()`. Measured
# here, binop chains (`a + a + a + ...`, nesting depth = term count):
#
#   CPython 3.14.6, macOS, main thread   accepts 95,065; RecursionError at
#                                        95,066; still RecursionError at
#                                        400,000 -- it never crashes
#   CPython 3.14.6, 512KB thread stack   accepts 2,834; RecursionError at 2,835
#   CPython 3.9.6 (this repo's FLOOR),   accepts 260,937; SIGSEGV (rc -11) by
#   macOS, main thread                   262,500. Bisected in child processes.
#                                        The macOS crash report for one of
#                                        those runs names the cause: "Thread
#                                        stack size exceeded due to excessive
#                                        recursion", dead at recursion level
#                                        261,942 -- inside the bracket above.
#   CPython 3.9.6, 512KB thread stack,   SIGBUS, exit 138
#   depth 20,000
#
# Three consequences, all measured rather than assumed:
#
#  1. `sys.setrecursionlimit()` is NOT the knob. On 3.14.6 at a 512KB stack and
#     depth 3,000 the result is RecursionError at limits of 1,000, 10,000,
#     100,000 and 1,000,000 alike.
#  2. On 3.9.6 there is NO depth at which this raises. The bisect found no
#     RecursionError anywhere between "accepted" and "crashed": it goes
#     straight from OK at 260,937 to SIGSEGV. So the margin that raising the
#     limit would be spending is not small -- on the interpreter this repo
#     declares as its floor it does not exist. A RecursionError is catchable
#     and can be reported; a SIGSEGV has no exception, no traceback, no output
#     and no exit code a caller can act on. Raising the limit trades the
#     former for the latter, which is strictly worse than the bug it would be
#     trying to fix. We therefore do NOT raise it, and do not call
#     threading.stack_size() either -- both are depth guesses, and a depth
#     guess is exactly what is not portable here.
#  3. The depth at which it bites depends on the C stack, which differs
#     between macOS and Linux and between the main thread and a worker. A file
#     that parses on a developer's machine can fail on a CI runner. That is
#     exactly how this escaped: one machine could not have told us.
#
# Catching RecursionError at the call site needs no depth guess at all, which
# is why it is the fix. Residual risk, stated plainly: on an interpreter whose
# AST builder has no counter (3.9.6) a deep enough file still kills the
# process, and nothing in this script can catch that. What this script
# controls is that where the interpreter DOES raise, the run survives and says
# so.
#
# So it has to be REPORTED, not merely survived. The AST pass is what gives
# exact function spans and real parameter names; without it the regex scanner
# still runs, but it misses lambdas, nested closures and any declaration shape
# not in its table. A file where that happened is in `unparsed_files` and is
# counted in summary.files_ast_unparsed -- a third state, distinct from both
# "not read at all" (skipped_files) and "read and planned in full".
AST_TOO_DEEP = "ast-nesting-too-deep"
AST_TOO_DEEP_REASON = (
    "ast.parse raised RecursionError: expression nesting is too deep for "
    "CPython's AST builder on this interpreter and stack. No flag raises this "
    "ceiling -- it is the C stack, not --max-file-bytes, --max-line-chars or "
    "sys.setrecursionlimit, and it moves between platforms. The AST pass did "
    "NOT run on this file."
)
AST_TOO_DEEP_LOST = (
    "exact function spans and real parameter names; the regex scanner ran "
    "instead, so rules needing function context (trivial-accessor, "
    "pure-delegation, repr/equality boilerplate) may not have fired on "
    "lambdas, nested closures or unrecognised declaration shapes"
)


# --------------------------------------------------------------------------
# operators
# --------------------------------------------------------------------------
# Five operators, chosen because Google measured them, not because they are
# elegant. `weight` is the selection weight when several apply to one line.
#
# `productive` is the share of mutants of that kind developers judged worth
# seeing. It is a MEASUREMENT, and it exists for exactly two of the five:
# ROR (0.841) and UOI (0.745), from Petrovic et al., TSE 2021, which reports
# productivity for those two operators and no others. docs/RESEARCH.md S20 is
# honest about this -- the other three cells read "--".
#
# SBR, AOR and LCR therefore carry `productive=None`, meaning NOT MEASURED.
# They previously carried 0.80, 0.79 and 0.78, which no study produced; they
# sat between the two real endpoints in descending order of the weights beside
# them, which is what interpolation looks like, and they read as measurements
# because they sat next to measurements. None is the honest value here: this
# project's rule is that "not measured" is a valid answer and a confident
# wrong number is not.
#
# The weights are NOT derived from this column, and the old comment claiming
# they were could not have been true -- SBR, AOR and LCR share weight=3.0
# while the three deleted values were all different. The weights are a design
# choice: ROR highest because it is the best-measured of the five, UOI lowest
# because it is the worst, and the remaining three level in the middle
# because there is no measurement to rank them by. Nothing reads this column
# (the weighted draw in choose_operator() reads `weight` only), so the three
# values never reached a user and changing them changes no behaviour.

OPERATORS: dict[str, dict] = {
    "ROR": dict(
        weight=4.0, productive=0.841,
        what="replace a relational operator (< with <=, == with !=)",
        why="Boundary errors are the most common real defect this catches, "
            "and ROR mutants were judged productive 84.1% of the time -- the "
            "best of the five, so ties go here.",
    ),
    "SBR": dict(
        weight=3.0, productive=None,  # not measured; see note above
        what="delete the statement",
        why="Statement removal is ~68% of Google's generated mutants: it asks "
            "the bluntest useful question, 'does any test notice if this line "
            "never runs?'",
    ),
    "AOR": dict(
        weight=3.0, productive=None,  # not measured; see note above
        what="replace an arithmetic operator (+ with -, * with /)",
        why="Arithmetic mutants are cheap to understand and land directly on "
            "the calculations people care about most.",
    ),
    "LCR": dict(
        weight=3.0, productive=None,  # not measured; see note above
        what="replace a logical connector (and with or)",
        why="A flipped connector is how authorization and validation rules "
            "silently widen, and a test that cannot see it is a real gap.",
    ),
    "UOI": dict(
        weight=1.0, productive=0.745,
        what="insert a unary operator (negate a condition, flip a sign)",
        why="The weakest of the five at 74.5% productive -- kept because it "
            "reaches lines no other operator touches, weighted down because "
            "it most often produces mutants nobody wants.",
    ),
}


# --------------------------------------------------------------------------
# suppression rules
# --------------------------------------------------------------------------
# `why` is written for the developer who asks "why did you not mutate this
# line?". It has to answer in plain language what the mutant would have
# looked like and why it would have been a waste; a reason that only names
# the pattern that matched reads as noise.

RULES: dict[str, dict] = {
    "test-file": dict(
        why="This is a test file. Mutating a test changes the thing doing the "
            "measuring, so the result says nothing about the code under test.",
    ),
    "generated-code": dict(
        why="This file is generated. Nobody will fix a surviving mutant here "
            "-- the fix belongs in the generator or the schema -- and the next "
            "regeneration throws the edit away.",
    ),
    "logging-or-telemetry": dict(
        why="A logging, metrics or tracing call. Deleting or altering it "
            "changes what an operator sees, not what the program computes, so "
            "a test that catches it is asserting on log text and a test that "
            "misses it has missed nothing.",
    ),
    "debug-statement": dict(
        why="A debug or defensive assertion statement. It exists to help a "
            "human inspect a running program; its mutant either changes "
            "nothing observable or fails a test for a reason unrelated to "
            "behaviour.",
    ),
    "trivial-accessor": dict(
        why="A one-line getter or setter that only moves a field in or out. "
            "Any mutant here is caught by the first test that touches the "
            "field at all, so it measures nothing the rest of the suite does "
            "not already measure.",
    ),
    "pure-delegation": dict(
        why="The body is a single call forwarding its own arguments. The "
            "logic lives in the callee and is mutated there; mutating the "
            "hand-off only tests that the hand-off exists.",
    ),
    "human-string-formatting": dict(
        why="String building for humans (repr/toString/a message). A mutant "
            "changes wording, which either no test asserts on, or one test "
            "asserts on verbatim -- and neither outcome tells you whether the "
            "program is correct.",
    ),
    "error-message-constant": dict(
        why="The raise/throw itself plus its message text. The decision to "
            "fail is on the line that guards this one and is mutated there; "
            "the wording of the failure is not behaviour anyone should be "
            "pinned to.",
    ),
    "timing-or-clock": dict(
        why="A sleep or a clock read used for instrumentation. Its mutant "
            "either changes a duration nobody asserts on, or makes a test "
            "slow or flaky -- the most expensive kind of pointless mutant.",
    ),
    "cache-lookup": dict(
        why="A cache read or write, where a miss still produces the correct "
            "answer. A mutant that defeats the cache leaves behaviour intact, "
            "so it is equivalent in every way that a test can observe.",
    ),
    "defensive-guard": dict(
        why="A null/nil guard that re-raises or returns the value unchanged. "
            "It is a belt-and-braces check on a condition the caller already "
            "excludes, so its mutant usually cannot even be reached.",
    ),
    "declaration": dict(
        why="A declaration or a literal -- import, constant, type, interface, "
            "enum member, or a row of a data table. There is no behaviour on "
            "this line to get wrong; a "
            "mutant either fails to compile or changes a value that the lines "
            "actually using it are already mutated for.",
    ),
    "equality-boilerplate": dict(
        why="Generated-shaped equality or hash boilerplate. A mutant here "
            "breaks a contract every collection operation relies on, so it "
            "dies instantly and far from the code that would have to change.",
    ),
    "unreachable-code": dict(
        why="This line sits after an unconditional return, raise or break and "
            "cannot execute. Its mutant is equivalent by construction -- no "
            "test can ever distinguish it.",
    ),
    "arid-function-family": dict(
        why="A call to a function in a known-arid family (logging, tracing, "
            "metrics, dumping, reporting). The family matcher exists so a "
            "newly named helper in a family we already judged useless is "
            "covered without waiting for someone to write a new rule.",
    ),
    "module-entry-guard": dict(
        why="The entry-point guard that decides whether this file was run or "
            "imported. Its mutant either changes nothing inside a test "
            "process, which never runs the guard, or stops the program "
            "starting -- and neither answers a question about behaviour.",
    ),
    "feedback": dict(
        why="A developer marked this kind of mutant useless. Feedback is the "
            "mechanism that took Google's reported usefulness from 15% to "
            "89%; a rule learned this way outranks anything we guessed.",
    ),
    "per-file-cap": dict(
        why="Over the per-file budget. Google caps surfaced mutants at 7 per "
            "file, because the eighth is read with less care than the first "
            "and an over-long list gets skipped entirely.",
    ),
}


# --------------------------------------------------------------------------
# languages and file selection
# --------------------------------------------------------------------------

EXT_LANG = {
    ".py": "python",
    ".js": "javascript", ".jsx": "javascript",
    ".ts": "javascript", ".tsx": "javascript", ".mjs": "javascript",
    ".java": "java", ".kt": "java", ".kts": "java",
    ".go": "go",
    ".rb": "ruby",
    ".php": "php",
    ".cs": "csharp",
    ".dart": "dart",
}

LINE_COMMENT = {
    "python": ("#",),
    "ruby": ("#",),
    "php": ("//", "#"),
    "javascript": ("//",),
    "java": ("//",),
    "go": ("//",),
    "csharp": ("//",),
    "dart": ("//",),
}

# Same shape as flake_smells.TEST_FILE_PATTERN. Kept local rather than
# imported so this script stays runnable on its own.
TEST_FILE_PATTERN = re.compile(
    r"(^|/)(tests?|spec|specs|__tests__|e2e|integration_tests?)/"
    r"|(^|/)test_[^/]*\.(py|rb)$"
    r"|(^|/)[^/]*_test\.(py|go|rb|dart|php)$"
    r"|[._-](test|spec)\.(js|jsx|ts|tsx|mjs|dart)$"
    r"|(^|/)tests?\.py$"
    r"|(^|/)conftest\.py$"
    r"|(^|/)Test[A-Z][^/]*\.(java|kt|cs)$"
    r"|(^|/)[^/]*Tests?\.(java|kt|cs)$"
    r"|(^|/)[^/]*(Test|Spec)\.(php)$",
    re.IGNORECASE,
)

# Path shapes that are generated by convention. Cheaper and more reliable
# than reading the file, and it catches the generators that forget the
# header.
GENERATED_PATH_PATTERN = re.compile(
    r"_pb2(_grpc)?\.py$|\.pb\.go$|_pb\.js$|\.g\.dart$|\.freezed\.dart$"
    r"|\.generated\.|\.gen\.go$|\.designer\.cs$|_generated\.|\.pb\.cc$"
    r"|/migrations/|\.min\.js$",
    re.IGNORECASE,
)

# The header convention: "Code generated by ... DO NOT EDIT." (Go), "@generated"
# (Bazel/protobuf/Meta), "Autogenerated", "This file was automatically
# generated". Only the first few lines are inspected -- a mention of the phrase
# halfway down a real source file is prose, not a marker.
GENERATED_HEADER_PATTERN = re.compile(
    r"@generated\b|DO NOT EDIT|do not edit this file|Code generated by"
    r"|auto[- ]?generated|automatically generated|Generated by the protocol"
    r"|This file is generated",
    re.IGNORECASE,
)
GENERATED_HEADER_LINES = 12


def detect_language(path: str) -> str | None:
    return EXT_LANG.get(os.path.splitext(path)[1].lower())


def is_test_file(path: str) -> bool:
    return bool(TEST_FILE_PATTERN.search(path.replace(os.sep, "/")))


def is_generated(path: str, raw: list[str]) -> bool:
    if GENERATED_PATH_PATTERN.search(path.replace(os.sep, "/")):
        return True
    head = "".join(raw[:GENERATED_HEADER_LINES])
    return bool(GENERATED_HEADER_PATTERN.search(head))


# --------------------------------------------------------------------------
# scrubbing
# --------------------------------------------------------------------------

def scrub(lines: list[str], lang: str) -> tuple[list[str], list[list[str]]]:
    """Split each line into (code with comments and string bodies removed,
    the string contents that were removed).

    Every operator-applicability test runs on `code`, so a comment that
    mentions `logger.info` or a SQL string containing ` > ` cannot create a
    mutation candidate out of nothing.

    Known limits, inherited from being a character scanner rather than a
    lexer: a JavaScript regex literal containing `//`, Ruby's =begin/=end
    blocks, and nested f-string braces are not understood. All three make
    the scanner scrub MORE than it should, which loses candidates rather
    than inventing them -- and losing a candidate is the safe direction for
    this file.
    """
    code_lines: list[str] = []
    string_lines: list[list[str]] = []
    block: str | None = None
    tokens = LINE_COMMENT.get(lang, ("//",))
    has_block_comment = lang not in ("python", "ruby")

    for raw in lines:
        buf: list[str] = []
        strs: list[str] = []
        i, n = 0, len(raw)
        while i < n:
            if block:
                end = "*/" if block == "*/" else block
                idx = raw.find(end, i)
                if idx < 0:
                    i = n
                else:
                    i = idx + len(end)
                    block = None
                continue
            if lang == "python" and raw[i:i + 3] in ('"""', "'''"):
                block = raw[i:i + 3]
                i += 3
                continue
            if has_block_comment and raw[i:i + 2] == "/*":
                block = "*/"
                i += 2
                continue
            if any(raw.startswith(t, i) for t in tokens):
                i = n
                continue
            ch = raw[i]
            if ch in "\"'`":
                j = i + 1
                content: list[str] = []
                while j < n:
                    if raw[j] == "\\":
                        content.append(raw[j:j + 2])
                        j += 2
                        continue
                    if raw[j] == ch:
                        break
                    content.append(raw[j])
                    j += 1
                strs.append("".join(content))
                buf.append('""')
                i = j + 1
                continue
            buf.append(ch)
            i += 1
        code_lines.append("".join(buf))
        string_lines.append(strs)
    return code_lines, string_lines


# --------------------------------------------------------------------------
# operator applicability
# --------------------------------------------------------------------------
# Relational and arithmetic operators are matched spaced-out on purpose.
# `List<String>`, `=>`, `->`, `<-` and `x[-1]` all contain characters that a
# greedy pattern reads as operators, and the spaced form rejects every one of
# them. What it misses is unspaced real code (`if(a<b)`), which costs a
# candidate rather than inventing one -- again the safe direction.

ROR_RE = re.compile(r"===|!==|==|!=|<=|>=|\s<\s|\s>\s")
LCR_RE = re.compile(r"&&|\|\||\band\b|\bor\b|\.and\b")
AOR_RE = re.compile(r"\s[+\-*/%]\s|[+\-*/%]=[^=]|\s//\s|\*\*")
NUM_LITERAL_RE = re.compile(r"(?<![\w.])\d+(?:\.\d+)?(?![\w.])")
CONDITION_RE = re.compile(r"^\s*(?:\}?\s*else\s+)?(?:if|elif|while|unless|until)\b"
                          r"|^\s*for\b|^\s*switch\b|^\s*case\b")

# Lines that are pure structure: removing or negating them is not a mutation
# anyone can act on.
STRUCTURAL = {
    "", "{", "}", "};", "})", "});", ")", ");", "]", "];", "],", "end", "else",
    "else:", "else {", "} else {", "try", "try:", "do", "begin", "pass", "...",
    ":", ";", "break", "break;", "fi", "done", "},", "):", "default:",
}

DEF_HEADER_RE = re.compile(
    r"^\s*(?:@|#\[)"                                   # decorator / attribute
    r"|^\s*(?:async\s+)?def\s+(?:self\.)?[\w?!=]+\s*[(:]?\s*$"  # ruby, no parens
    r"|^\s*(?:async\s+)?def\s+\w+\s*\("                # python
    r"|^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s*\w*\s*\("
    r"|^\s*(?:(?:export|public|private|protected|internal|abstract|sealed|"
    r"static|final|open|data)\s+)*class\b|^\s*module\b"
    r"|^\s*func\s+"                                    # go
    r"|^\s*(?:public|private|protected|internal|static|final|abstract|"
    r"override|open|suspend|virtual|sealed|\s)*(?:fun|def)\s+\w+\s*\("
    r"|^\s*(?:public|private|protected|internal)\s[\w<>,\[\]\s.]+\s+\w+\s*\([^;]*\)\s*\{?\s*$"
)


# Ruby's `@field` is an instance variable, not a decorator. Without this the
# decorator branch of DEF_HEADER_RE/DECLARATION_RE eats every ivar line in
# every Ruby file and they silently stop being mutable.
RUBY_IVAR_RE = re.compile(r"^\s*@@?[A-Za-z_]")


def _is_annotation(code: str, lang: str) -> bool:
    if lang == "ruby" and RUBY_IVAR_RE.match(code):
        return False
    return bool(re.match(r"^\s*(?:@|#\[)", code))


def applicable_operators(code: str, lang: str = "") -> set[str]:
    """Which of the five operators could touch this line at all.

    Deliberately syntactic: this answers "is there something to mutate", not
    "is it worth mutating". The arid rules answer the second question, and
    keeping the two apart is what lets the output say how many candidates
    were considered and how many were thrown away.
    """
    stripped = code.strip()
    if stripped in STRUCTURAL or not stripped:
        return set()

    ops: set[str] = set()
    if ROR_RE.search(code):
        ops.add("ROR")
    if LCR_RE.search(code):
        ops.add("LCR")
    if AOR_RE.search(code):
        ops.add("AOR")

    is_header = bool(DEF_HEADER_RE.match(code)) and (
        lang != "ruby" or not RUBY_IVAR_RE.match(code))
    if not is_header:
        ops.add("SBR")
    # UOI needs somewhere to insert: a number to negate, an operator-bearing
    # expression, or a condition to invert.
    if ops - {"SBR"} or NUM_LITERAL_RE.search(code) or CONDITION_RE.match(code):
        ops.add("UOI")
    return ops


def choose_operator(ops: set[str], seed: int, path: str, line: int,
                    snippet: str) -> str:
    """One operator per line, chosen deterministically from a weighted draw.

    Hashing rather than `random`: the choice has to be identical in a later
    run, in another process and on another machine, and `random.seed` state
    would depend on how many lines came before. The snippet is part of the
    key so that editing a line re-rolls that line and only that line.
    """
    ordered = sorted(ops)
    weights = [OPERATORS[o]["weight"] for o in ordered]
    total = sum(weights)
    key = f"{seed}\x00{path}\x00{line}\x00{snippet}".encode("utf-8")
    digest = hashlib.sha256(key).digest()
    draw = int.from_bytes(digest[:8], "big") / float(1 << 64) * total
    upto = 0.0
    for name, weight in zip(ordered, weights):
        upto += weight
        if draw < upto:
            return name
    return ordered[-1]


def _rank_key(entry: dict, seed: int, path: str) -> tuple:
    """Order for the per-file cap: a deterministic shuffle, nothing more.

    It is tempting to keep the most productive operators when the cap bites,
    and the first version of this did. It was wrong. The productivity
    weighting is already spent when the line's operator is drawn, and
    applying it a second time here compounds it: a 500-line file with eight
    ROR candidates would surface seven ROR mutants and never an SBR one, so
    whole operator classes disappear from exactly the files big enough to
    need them. Google samples; so do we. Keeping it a hash of the line
    rather than a counter also means the survivors are spread through the
    file instead of clustered at the top.
    """
    digest = hashlib.sha256(
        f"rank\x00{seed}\x00{path}\x00{entry['line']}".encode("utf-8")).digest()
    return (int.from_bytes(digest[:8], "big"), entry["line"])


# --------------------------------------------------------------------------
# node classification (used by the feedback loop)
# --------------------------------------------------------------------------

def classify_node(code: str) -> str:
    s = code.strip()
    if CONDITION_RE.match(code):
        return "loop" if re.match(r"^\s*(?:for|while|until)\b", code) else "condition"
    if re.match(r"^\s*(?:return|yield)\b", s):
        return "return"
    if re.match(r"^\s*(?:raise|throw|panic)\b", s):
        return "raise"
    if re.match(r"^\s*(?:import|from|using|package|require|include)\b", s):
        return "import"
    if re.search(r"(?<![=!<>+\-*/%])=(?!=)", s):
        return "assignment"
    if re.match(r"^[\w$.\[\]:>\-]+\s*\(", s):
        return "call"
    return "statement"


# --------------------------------------------------------------------------
# function spans
# --------------------------------------------------------------------------

class Func:
    """One function: name, parameter names, and the lines of its body.

    Several rules (trivial accessor, pure delegation, repr/equality
    boilerplate) are only decidable with this context -- `return self._x` is
    arid inside a getter and ordinary code inside a calculation.
    """

    __slots__ = ("name", "params", "start", "end", "body", "head")

    def __init__(self, name: str, params: list[str], start: int, end: int,
                 body: list[int], head: int = 0):
        self.name = name
        self.params = params
        self.start = start      # 1-based declaration line
        self.end = end          # 1-based last body line, inclusive
        self.body = body        # 1-based body lines that carry code
        # Column on `start` where the body begins, non-zero only when the
        # whole function is written on one line -- `int getX() { return x; }`.
        # That shape IS the trivial accessor, so getting it wrong would miss
        # the most common arid node in Java and JavaScript.
        self.head = head

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"Func({self.name!r}, {self.start}-{self.end})"


PY_DEF_RE = re.compile(r"^(\s*)(?:async\s+)?def\s+(\w+)\s*\((.*)")
BRACE_FUNC_RE = re.compile(
    r"^\s*(?:(?:export|public|private|protected|internal|static|final|abstract|"
    r"override|open|suspend|virtual|sealed|async|const|def)\s+)*"
    r"(?:function\s+|func\s+(?:\([^)]*\)\s*)?|fun\s+|[\w<>,\[\]\.]+\s+)?"
    r"(?P<name>[A-Za-z_$][\w$]*)\s*\((?P<params>[^;{]*)\)"
    r"(?:\s*:\s*[\w<>,\[\]\. ?]+)?(?:\s*throws\s[\w.,\s]+)?"
    r"\s*(?:\{[^{}]*\})?\s*\{?\s*$"
)
RUBY_DEF_RE = re.compile(r"^(\s*)def\s+(?:self\.)?([\w?!=]+)\s*(?:\((.*)\))?")


def python_funcs_detailed(source: str) -> tuple[list[Func] | None, str | None]:
    """(funcs, why_not) -- funcs is None when Python's own parser refused it.

    `ast` genuinely helps here and is not decoration: it gives real parameter
    names (so pure-delegation can compare arguments to parameters rather than
    guess), and real body boundaries (so a one-line getter is recognised as
    one-line even when its decorators and type hints span four). Returns None
    when the file does not parse, so the caller can fall back to the regex
    scanner -- a half-written file still deserves an answer.

    `why_not` exists because those refusals are not one thing. A SyntaxError is
    an ordinary, expected state: the file is half-written, the regex scanner
    is the right answer and saying so every time would be noise. A
    RecursionError is not: nothing about the file is wrong, the limit is the
    interpreter's C stack (see AST_TOO_DEEP above), it differs per platform,
    and the caller MUST be able to tell the reader that the AST pass did not
    run. Falling back silently in that case is the whole defect class this
    file exists to avoid.

    This function never raises. The docstring used to promise that while the
    code only caught SyntaxError and ValueError, so a deeply nested expression
    killed the entire run from inside plan().
    """
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return None, "syntax-error"
    except RecursionError:
        # Caught here and NOT re-raised, but the caller is handed the reason so
        # it can put the file in unparsed_files. Catch-and-continue on its own
        # would be silence wearing a fix's clothes.
        return None, AST_TOO_DEEP
    funcs: list[Func] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        args = node.args
        params = [a.arg for a in list(args.posonlyargs) + list(args.args) +
                  list(args.kwonlyargs)]
        if args.vararg:
            params.append(args.vararg.arg)
        if args.kwarg:
            params.append(args.kwarg.arg)
        params = [p for p in params if p not in ("self", "cls")]
        body = node.body
        start = body[0].lineno
        end = max(getattr(n, "end_lineno", n.lineno) or n.lineno for n in body)
        body_lines = sorted({
            n.lineno for stmt in body
            for n in [stmt]
        } | {
            ln for stmt in body
            for ln in range(stmt.lineno,
                            (getattr(stmt, "end_lineno", stmt.lineno) or
                             stmt.lineno) + 1)
        })
        head = body[0].col_offset if body[0].lineno == node.lineno else 0
        funcs.append(Func(node.name, params, node.lineno, end, body_lines, head))
    funcs.sort(key=lambda f: f.start)
    return funcs, None


def python_funcs(source: str) -> list[Func] | None:
    """Back-compatible wrapper: spans only. Prefer python_funcs_detailed(),
    which also hands back WHY the parser refused -- a caller that cannot tell
    "half-written" from "too deeply nested" cannot report the second one."""
    return python_funcs_detailed(source)[0]


def regex_funcs(code: list[str], lang: str) -> list[Func]:
    """Header + indentation/brace scan, for everything Python's ast cannot
    read. It misses lambdas, nested closures assigned to variables, and any
    declaration shape not in the table -- every miss meaning a rule that
    needs function context simply does not fire, which keeps the line
    mutable."""
    if lang in ("python", "ruby"):
        pattern = PY_DEF_RE if lang == "python" else RUBY_DEF_RE
        return _indent_funcs(code, pattern)
    return _brace_funcs(code, lang)


def _indent_funcs(code: list[str], pattern: re.Pattern) -> list[Func]:
    funcs: list[Func] = []
    for i, line in enumerate(code):
        m = pattern.match(line)
        if not m:
            continue
        indent = len(m.group(1))
        params = _split_params(m.group(3) or "")
        end = len(code)
        for j in range(i + 1, len(code)):
            nxt = code[j]
            if not nxt.strip():
                continue
            if len(nxt) - len(nxt.lstrip()) <= indent:
                end = j
                break
        body = [n for n in range(i + 2, end + 1)
                if n <= len(code) and code[n - 1].strip()
                and code[n - 1].strip() != "end"]
        funcs.append(Func(m.group(2), params, i + 1, end, body))
    return funcs


def _brace_funcs(code: list[str], lang: str = "") -> list[Func]:
    funcs: list[Func] = []
    for i, line in enumerate(code):
        m = BRACE_FUNC_RE.match(line)
        if not m:
            continue
        name = m.group("name")
        if name in ("if", "while", "for", "switch", "catch", "return", "new"):
            continue
        end = _brace_end(code, i)
        if end <= i + 1:
            brace = line.find("{")
            funcs.append(Func(name, _split_params(m.group("params"), lang), i + 1,
                              i + 1, [i + 1], brace + 1 if brace >= 0 else 0))
            continue
        body = [n for n in range(i + 2, end)
                if n <= len(code) and code[n - 1].strip()
                and code[n - 1].strip() not in STRUCTURAL]
        funcs.append(Func(name, _split_params(m.group("params"), lang), i + 1,
                          end, body))
    return funcs


def _brace_end(code: list[str], start: int) -> int:
    depth = 0
    opened = False
    for i in range(start, len(code)):
        for ch in code[i]:
            if ch == "{":
                depth += 1
                opened = True
            elif ch == "}":
                depth -= 1
                if opened and depth <= 0:
                    return i + 1
    return len(code)


def _split_params(text: str, lang: str = "") -> list[str]:
    """Parameter NAMES out of a parameter list, across languages. Types,
    defaults and annotations are stripped; what is left is what a delegation
    check compares against.

    Go and Kotlin put the name first (`price int`, `price: Int`) while Java
    and C# put it last (`int price`), so the side we keep depends on the
    language. Guessing one side for both would make pure-delegation compare
    argument names against type names and never fire.
    """
    name_first = lang in ("go", "ruby", "python", "javascript", "php")
    out: list[str] = []
    depth = 0
    current: list[str] = []
    for ch in text:
        if ch in "([{<":
            depth += 1
        elif ch in ")]}>":
            if depth == 0:
                break
            depth -= 1
        if ch == "," and depth == 0:
            out.append("".join(current))
            current = []
            continue
        current.append(ch)
    out.append("".join(current))
    names = []
    for part in out:
        part = part.split("=")[0].split(":")[0].strip()
        part = part.lstrip("*&").strip()
        tokens = re.findall(r"[A-Za-z_$][\w$]*", part)
        if tokens:
            names.append(tokens[0] if name_first else tokens[-1])
    return [n for n in names if n not in ("self", "this", "cls")]


# --------------------------------------------------------------------------
# arid patterns
# --------------------------------------------------------------------------

LOG_CALLEE_RE = re.compile(
    r"^(?:[\w$]+\.)*_?(?:logger|logging|log|LOG|LOGGER|console|slog|zap|logrus"
    r"|winston|pino|Rails\.logger)\b"
    r"|^(?:print|println|printf|puts|pp|var_dump|print_r|echo|dump|dd)$"
    r"|^System\.(?:out|err)\b|^fmt\.(?:Print|Fprint)"
    r"|^(?:statsd|metrics|meter|telemetry|analytics|tracer|trace|Sentry|"
    r"Datadog|newrelic|Bugsnag)\b"
    r"|\.(?:info|debug|warn|warning|error|trace|verbose|fatal|exception|"
    r"log|notice|critical)$"
    r"|\.Log[A-Z]\w*$|\.Write(?:Line)?$"
    r"|\.(?:increment|incr|decrement|gauge|histogram|timing|observe|"
    r"record_metric|count|startSpan|start_span|addEvent|add_event|"
    r"set_attribute|setAttribute|capture_message|captureException)$",
)

# The fuzzy family matcher. Google needed 200+ function families; naming one
# at a time does not scale, and the names in a family rhyme. A new helper
# called `emit_render_metrics` or `trace_request` is covered here the day it
# is written, with no new rule.
#
# Matched against the final segment of the callee only, and only when the
# whole statement is that call -- `total = record_sale(x)` keeps its mutant,
# because a call whose result is used is not instrumentation.
ARID_FAMILY_RE = re.compile(
    r"^(?:.*_)?(?:log|logs|debug|trace|print|dump|emit|report|record|span|"
    r"instrument|telemetry|metric|metrics|audit|notify|track|monitor|profile|"
    r"breadcrumb|diagnostic|diagnostics)(?:_.*)?$"
    r"|^(?:emit|record|report|track|trace|log|debug|dump|instrument|audit|"
    r"notify|monitor|profile)[A-Z]\w*$",
)

DEBUG_STATEMENT_RE = re.compile(
    r"^\s*assert\b"                              # python/java production assert
    r"|^\s*debugger\s*;?\s*$"
    r"|^\s*breakpoint\s*\(\s*\)"
    r"|^\s*(?:pdb|ipdb|pudb)\s*\.\s*set_trace\s*\("
    r"|^\s*console\s*\.\s*(?:trace|debug|dir|table)\s*\("
    r"|^\s*Debug\.(?:Assert|WriteLine|Log)\s*\("
    r"|^\s*binding\.pry\b"
)

RAISE_STATEMENT_RE = re.compile(
    r"^\s*(?:raise|throw)\b"
    r"|^\s*return\s+(?:errors\.New|fmt\.Errorf)\s*\("
    r"|^\s*panic\s*\("
)

CLOCK_CALL_RE = re.compile(
    r"\btime\s*\.\s*(?:time|monotonic|perf_counter|process_time|Now)\s*\("
    r"|\bDate\s*\.\s*now\s*\(|\bnew\s+Date\s*\(\s*\)"
    r"|\bSystem\s*\.\s*(?:currentTimeMillis|nanoTime)\s*\("
    r"|\bInstant\s*\.\s*now\s*\(|\bDateTime\s*\.\s*(?:Now|UtcNow)\b"
    r"|\bprocess\s*\.\s*hrtime\b|\bperformance\s*\.\s*now\s*\("
    r"|\bmicrotime\s*\(|\bTime\s*\.\s*now\b"
)
SLEEP_CALL_RE = re.compile(
    r"^\s*(?:await\s+)?(?:time\s*\.\s*)?[Ss]leep\s*\("
    r"|^\s*Thread\s*\.\s*sleep\s*\(|^\s*(?:await\s+)?Task\s*\.\s*Delay\s*\("
    r"|^\s*usleep\s*\(|^\s*setTimeout\s*\("
)
# Variable names that say "this clock read is instrumentation, not a
# business time". A line like `if now > expires_at:` is deliberately NOT
# matched: that comparison is a business boundary and must stay mutable.
TIMER_NAME_RE = re.compile(
    r"^(?:_?(?:t0|t1|start|started|begin|began|end|ended|finish|finished|"
    r"elapsed|duration|took|latency|timer|stopwatch|ts|timestamp)\w*"
    r"|\w*(?:_start|_end|_elapsed|_duration|_ts|_time|Start|End|Elapsed|"
    r"Duration|Ms|Millis))$"
)

# Underscore counts as a boundary so `user_cache` matches, but `precached`
# and `Namespace` do not: a name has to END a word with the cache token.
CACHE_NAME_RE = re.compile(r"(?<![A-Za-z0-9])(?:cache|caches|memo|memoize|lru)",
                           re.IGNORECASE)
CACHE_OP_RE = re.compile(
    r"\.(?:get|set|put|has|contains|fetch|setdefault|add|store|invalidate|"
    r"delete|evict|peek)\s*\(|\[[^\]]*\]\s*=|\bin\b"
)

NULLISH_RE = re.compile(
    r"\bis\s+(?:not\s+)?None\b|==\s*(?:None|null|nil|undefined)\b"
    r"|!=\s*(?:None|null|nil|undefined)\b|\bnot\s+\w[\w.]*\s*:"
    r"|^\s*if\s*\(\s*!\s*[\w.$]+\s*\)|\?\?|===\s*(?:null|undefined)\b"
    r"|!==\s*(?:null|undefined)\b|\berr\s*!=\s*nil\b"
)
RETURN_UNCHANGED_RE = re.compile(
    r"^\s*(?:return|raise|throw)\s*(?:[\w.$\[\]]*\s*)?[;)]?\s*$"
    r"|^\s*(?:raise|throw)\b|^\s*return\s+err\b|^\s*return\s*$"
)

DECLARATION_RE = re.compile(
    r"^\s*(?:import|from\s+[\w.]+\s+import|package|using|include|include_once|"
    r"require|require_once|require_relative|export\s+type|export\s+interface|"
    r"declare|namespace|use)\b"
    r"|^\s*<\?php\b|^\s*\?>\s*$|^\s*#!"
    r"|^\s*(?:@|#\[)"                                        # decorator
    r"|^\s*(?:public\s+|private\s+|protected\s+|internal\s+|export\s+)?"
    r"(?:abstract\s+|sealed\s+|static\s+)?"
    r"(?:interface|enum|type|typealias|struct|protocol|trait)\b"
    r"|^\s*(?:(?:export|public|private|protected|internal|abstract|sealed|"
    r"static|final|open|data)\s+)*(?:class|module)\s+\w+[\s:(<{\w,]*$"
    r"|^\s*\w+\s*:\s*[\w\[\]<>,. |]+$"                       # bare type field
    # Bare enum members (`RED,` / `RED("r"),` / `RED;`). The `NAME = value`
    # form is deliberately NOT here: it goes through _is_constant, which
    # demands a literal, so `RATE = BASE * 1.05` keeps its mutant however
    # loudly it is named.
    r"|^\s*[A-Z][A-Z0-9_]*(?:\s*\([^()]*\))?\s*[,;]\s*$"
)
# A constant is a name in shouting case, or a const/final/readonly binding --
# but ONLY when the right-hand side is a literal. `RATE = BASE * 1.05` keeps
# its mutant: the arithmetic is behaviour even though the name shouts.
CONST_ASSIGN_RE = re.compile(
    r"^\s*(?:(?:export|public|private|protected|internal|static|final|const|"
    r"readonly|val|var|let)\s+)*"
    r"(?:(?P<type>[A-Za-z_$][\w$<>\[\],.]*)\s+)?"
    r"(?P<name>[A-Za-z_$][\w$]*)\s*(?::\s*[\w\[\]<>,. |?]+)?\s*=\s*(?P<rhs>.+?)\s*;?\s*$"
)
# The modifier order differs per language (`private static final int X`,
# `export const X`, `static readonly int X`), so this looks for the keyword
# anywhere left of the `=` rather than trying to spell every ordering.
CONST_KEYWORD_RE = re.compile(r"^[^=]*\b(?:const|final|readonly|val)\b[^=]*=")
LITERAL_RHS_RE = re.compile(
    r"^(?:-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?[LlFfDdUu]?|\"\"|''|`|True|False|"
    r"true|false|None|null|nil|undefined|\[\s*\]|\{\s*\}|\(\s*\))$"
)
# A collection literal spread over one line: ("a", "b") / [1, 2, 3] / {"a": 1}.
LITERAL_COLLECTION_RE = re.compile(r'^[\[\({][^()\[\]{}]*[\]\)}],?$')
# `X = [` / `X = {` opening a table that continues on the next lines.
LITERAL_OPENER_RE = re.compile(r'^[\[({]$')
# `re.compile("")`, `frozenset(("",""))`, `Decimal("")` -- a constructor call
# whose arguments are all literals is still a constant. The argument text must
# contain no operator, so `timedelta(days=n * 2)` is not one.
LITERAL_CALL_RE = re.compile(r"""^[\w.]+\(\s*[\s,"'\d.\[\]{}()=\w-]*\)\s*,?$""")
OPERATOR_CHARS_RE = re.compile(r"[+*/%<>]|\s-\s|\band\b|\bor\b|\bif\b")
# A line whose scrubbed code holds no identifier at all: a row of a literal
# table, a continuation of a message, a lone number. There is nothing on it
# that a mutant could make wrong in an interesting way.
LITERAL_DATA_RE = re.compile(r"""^[\s"'\d.,:;\[\]{}()+|-]*$""")

STR_FORMAT_FUNCS = {
    "__repr__", "__str__", "__format__", "__unicode__",
    "tostring", "to_s", "to_str", "inspect", "describe", "String",
    "toDebugString", "debugDescription", "GoString", "Error",
}
EQ_HASH_FUNCS = {
    "__eq__", "__ne__", "__hash__", "equals", "hashcode", "gethashcode",
    "eql?", "hash", "Equals", "GetHashCode",
}

TERMINATOR_RE = re.compile(
    r"^\s*(?:return\b|raise\b|throw\b|break\b|continue\b|panic\s*\(|"
    r"os\.Exit\s*\(|sys\.exit\s*\(|exit\s*\(|System\.exit\s*\()"
)


def _call_statement(code: str) -> str | None:
    """The callee when the ENTIRE line is one call, otherwise None.

    This is the narrowing that keeps the logging and family rules away from
    business logic. `logger.info(total)` is a bare call statement and is
    suppressed; `total = record_sale(a, b)` and `if logger.enabled and
    amount > limit:` are not, because their value is used.

    Multi-line calls: only the opening line is recognised. The continuation
    lines are judged on their own content, and since they are usually bare
    arguments they tend to carry no operators at all and never become
    candidates. A continuation that does contain arithmetic stays mutable,
    which is a miss in the safe direction.
    """
    s = code.strip().rstrip(";").strip()
    if not s:
        return None
    m = re.match(r"^(?:await\s+|go\s+|defer\s+|yield\s+)?"
                 r"(?P<callee>[\w$]+(?:\s*[\.:]{1,2}\s*[\w$]+)*)\s*\(", s)
    if not m:
        return None
    rest = s[m.end() - 1:]
    depth = 0
    for idx, ch in enumerate(rest):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                tail = rest[idx + 1:].strip()
                # Allow a chained terminator (`.log()`, `.await`), but nothing
                # that would make this an expression feeding something else.
                if tail and not re.match(r"^(?:\.\w+\s*\(\s*\)\s*)*;?,?$", tail):
                    return None
                return m.group("callee").replace(" ", "")
    # Unbalanced: the call opens here and continues on the next line. Still a
    # call statement for our purposes.
    return m.group("callee").replace(" ", "")


def _last_segment(callee: str) -> str:
    return re.split(r"[.:]+", callee)[-1]


def _assignment(code: str) -> tuple[str, str] | None:
    """(target name, right-hand side) for a single plain assignment."""
    m = CONST_ASSIGN_RE.match(code)
    if not m:
        return None
    rhs = m.group("rhs")
    if rhs.startswith("=") or m.group("name") in ("if", "while", "return"):
        return None
    return m.group("name"), rhs.strip()


# --------------------------------------------------------------------------
# feedback
# --------------------------------------------------------------------------

class Feedback:
    """Developer verdicts on previously surfaced mutants.

    Google's usefulness number moved from 15% to 89% because 66,798 judged
    mutants turned into suppression rules. This is the small version of that
    loop: a judgement recorded once is applied on every later run, and the
    output says which suppressions came from feedback rather than from a
    rule we guessed, so the two are never confused.

    Accepted shapes (a bare list, {"entries": [...]} or {"feedback": [...]}),
    each entry keyed by one of:
        operator   -- never generate this operator again
        node_type  -- suppress lines of this kind (call, assignment, ...)
        pattern    -- a regex; suppress lines whose code matches
        function   -- a regex on a called function's name
        rule       -- re-assert a built-in rule (recorded, already active)
    An entry is a suppression unless it says `"verdict": "useful"`.
    """

    def __init__(self, entries: list[dict] | None = None):
        self.operators: set[str] = set()
        self.node_types: dict[str, dict] = {}
        self.patterns: list[tuple[re.Pattern, dict]] = []
        self.functions: list[tuple[re.Pattern, dict]] = []
        self.reasserted: list[str] = []
        self.ignored: list[dict] = []
        for entry in entries or []:
            self._add(entry)

    def _add(self, entry: dict) -> None:
        if not isinstance(entry, dict):
            self.ignored.append({"entry": entry, "reason": "not an object"})
            return
        if entry.get("verdict", "useless") not in ("useless", "noisy", "bad"):
            return
        if entry.get("operator"):
            self.operators.add(str(entry["operator"]).upper())
            return
        if entry.get("node_type"):
            self.node_types[str(entry["node_type"])] = entry
            return
        for key, bucket in (("pattern", self.patterns), ("function", self.functions)):
            if entry.get(key):
                try:
                    bucket.append((re.compile(str(entry[key])), entry))
                except re.error as exc:
                    # A broken regex in a feedback file must not take the run
                    # down; the whole point of the loop is that people edit it
                    # by hand.
                    self.ignored.append({"entry": entry, "reason": f"bad regex: {exc}"})
                return
        if entry.get("rule"):
            self.reasserted.append(str(entry["rule"]))
            return
        self.ignored.append({"entry": entry, "reason": "no recognised key"})

    @property
    def active(self) -> bool:
        return bool(self.operators or self.node_types or self.patterns
                    or self.functions)

    def match(self, code: str, node_type: str, callee: str | None) -> dict | None:
        entry = self.node_types.get(node_type)
        if entry:
            return entry
        for rx, entry in self.patterns:
            if rx.search(code):
                return entry
        if callee:
            name = _last_segment(callee)
            for rx, entry in self.functions:
                if rx.search(name) or rx.search(callee):
                    return entry
        return None

    def summary(self) -> dict:
        return {
            "operators": sorted(self.operators),
            "node_types": sorted(self.node_types),
            "patterns": [rx.pattern for rx, _ in self.patterns],
            "functions": [rx.pattern for rx, _ in self.functions],
            "reasserted_rules": sorted(set(self.reasserted)),
            "ignored": self.ignored,
        }


def load_feedback(path: str) -> Feedback:
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    if isinstance(data, dict):
        entries = data.get("entries") or data.get("feedback") or []
    else:
        entries = data
    if not isinstance(entries, list):
        raise ValueError("feedback must be a list, or an object with "
                         "'entries' / 'feedback'")
    return Feedback(entries)


# --------------------------------------------------------------------------
# diff scoping
# --------------------------------------------------------------------------

HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


def parse_diff(text: str) -> dict[str, set[int]]:
    """Path -> set of line numbers added or changed, in the NEW file.

    Only `+` lines count. A mutation gate is a ratchet on new code: an
    unchanged line below an edit is not this author's to answer for, and
    scoring it is how a diff-scoped gate turns back into a full-project run
    nobody can afford.
    """
    changed: dict[str, set[int]] = {}
    path: str | None = None
    lineno = 0
    for raw in text.splitlines():
        if raw.startswith("+++ "):
            target = raw[4:].strip().split("\t")[0]
            if target == "/dev/null":
                path = None
                continue
            path = re.sub(r"^[ab]/", "", target)
            changed.setdefault(path, set())
            continue
        if raw.startswith("@@"):
            m = HUNK_RE.match(raw)
            lineno = int(m.group(1)) if m else 0
            continue
        if path is None or not lineno:
            continue
        if raw.startswith("+"):
            changed[path].add(lineno)
            lineno += 1
        elif raw.startswith("-") or raw.startswith("\\"):
            continue
        else:
            lineno += 1
    return changed


def changed_lines_for(path: str, diff: dict[str, set[int]]) -> set[int] | None:
    """Longest-suffix match between the diff's paths and ours, because a diff
    is usually relative to the repository root while the paths on the command
    line are relative to wherever the user is standing. None means the file
    is not in the diff at all."""
    norm = path.replace(os.sep, "/")
    best: set[int] | None = None
    best_len = -1
    for key, lines in diff.items():
        k = key.replace(os.sep, "/")
        if norm == k or norm.endswith("/" + k) or k.endswith("/" + norm):
            if len(k) > best_len:
                best, best_len = lines, len(k)
    return best


# --------------------------------------------------------------------------
# the scan
# --------------------------------------------------------------------------

class FileScan:
    def __init__(self, path: str, lang: str, raw: list[str], use_ast: bool = True):
        self.path = path
        self.lang = lang
        self.raw = raw
        self.code, self.strings = scrub(raw, lang)
        self.ast_used = False
        # None unless the AST pass was attempted and the interpreter refused
        # the file for depth. "Not attempted" (Java, --no-ast) and "attempted
        # and failed" must not look alike downstream.
        self.ast_unavailable: str | None = None
        funcs = None
        if lang == "python" and use_ast:
            funcs, why = python_funcs_detailed("".join(raw))
            self.ast_used = funcs is not None
            if why == AST_TOO_DEEP:
                self.ast_unavailable = why
        self.funcs = funcs if funcs is not None else regex_funcs(self.code, lang)
        self.unreachable = _unreachable_lines(self.code)
        self._by_line: dict[int, Func] = {}
        for f in self.funcs:
            for n in f.body:
                # Innermost wins: a nested helper's body is not its parent's.
                prev = self._by_line.get(n)
                if prev is None or f.start > prev.start:
                    self._by_line[n] = f

    def func_at(self, line: int) -> Func | None:
        return self._by_line.get(line)

    def snippet(self, line: int) -> str:
        s = self.raw[line - 1].strip() if 0 < line <= len(self.raw) else ""
        return s[:97] + "..." if len(s) > 100 else s

    def prev_code_line(self, line: int) -> int | None:
        for n in range(line - 1, 0, -1):
            if self.code[n - 1].strip():
                return n
        return None

    def next_code_line(self, line: int) -> int | None:
        for n in range(line + 1, len(self.code) + 1):
            if self.code[n - 1].strip():
                return n
        return None


def _unreachable_lines(code: list[str]) -> set[int]:
    """Lines that follow an unconditional terminator inside the same block.

    Indentation-based for every language, because brace languages indent too
    in practice. It misses unreachable code written on one line after a
    `return` (`return x; log(y);`) and anything after a terminator inside a
    one-line block. Both misses leave a line mutable, which is the safe
    direction.
    """
    dead: set[int] = set()
    terminator_indent: int | None = None
    for i, line in enumerate(code, 1):
        stripped = line.strip()
        if not stripped:
            continue
        indent = len(line) - len(line.lstrip())
        if terminator_indent is not None:
            if indent < terminator_indent or stripped.startswith(("}", "else",
                                                                  "case ",
                                                                  "default:",
                                                                  "end", "catch",
                                                                  "finally")):
                terminator_indent = None
            elif indent == terminator_indent:
                dead.add(i)
                continue
            else:
                # Deeper than the terminator: part of a construct that already
                # ended. Stop rather than guess.
                terminator_indent = None
        # `return t if t > 10` and `return unless x` are conditional, so the
        # lines after them are perfectly reachable. Treating a modifier form
        # as unconditional is how a Ruby file loses most of its mutants.
        conditional = re.search(r"\b(?:if|unless|when|else)\b", stripped)
        if (TERMINATOR_RE.match(line) and not conditional
                and ";" not in stripped[:-1]):
            terminator_indent = indent
    return dead


def arid_rule(scan: FileScan, line: int) -> str | None:
    """The first rule that says this line is not worth mutating, or None.

    Ordered cheapest-and-most-certain first. The order is also the reporting
    order, so a line that is both unreachable and a log call is reported as
    unreachable -- the stronger statement.
    """
    code = scan.code[line - 1]
    stripped = code.strip()
    func = scan.func_at(line)
    callee = _call_statement(code)

    if line in scan.unreachable:
        return "unreachable-code"

    if _is_entry_point_guard(code):
        return "module-entry-guard"

    if LITERAL_DATA_RE.match(code) and code.strip():
        return "declaration"

    if ((DECLARATION_RE.match(code) and
         (scan.lang != "ruby" or not RUBY_IVAR_RE.match(code)))
            or _is_constant(code)):
        return "declaration"

    if DEBUG_STATEMENT_RE.match(code):
        return "debug-statement"

    if callee and LOG_CALLEE_RE.search(callee):
        return "logging-or-telemetry"

    if RAISE_STATEMENT_RE.match(code):
        return "error-message-constant"

    if func is not None:
        name = func.name
        if name in STR_FORMAT_FUNCS or name.lower() in STR_FORMAT_FUNCS:
            return "human-string-formatting"
        if name in EQ_HASH_FUNCS or name.lower() in EQ_HASH_FUNCS:
            return "equality-boilerplate"
        if _is_trivial_accessor(scan, func, line):
            return "trivial-accessor"
        if _is_pure_delegation(scan, func, line, callee):
            return "pure-delegation"

    if _is_timing(code, callee, stripped):
        return "timing-or-clock"

    if _is_cache_lookup(code):
        return "cache-lookup"

    if _is_defensive_guard(scan, line):
        return "defensive-guard"

    # The guard's own `return err` body. Mutating it is the same equivalent
    # mutant as mutating the guard, one line lower down.
    prev = scan.prev_code_line(line)
    if (prev is not None and RETURN_UNCHANGED_RE.match(code)
            and _is_defensive_guard(scan, prev)):
        return "defensive-guard"

    if callee and ARID_FAMILY_RE.match(_last_segment(callee)):
        return "arid-function-family"

    return None


ENTRY_GUARD_RE = re.compile(
    r"^\s*if\s+__name__\s*==|^\s*if\s*\(?\s*require\.main\s*===?\s*module"
    r"|^\s*def\s+main\s*\(|^\s*func\s+main\s*\(\s*\)"
    r"|^\s*public\s+static\s+void\s+main\s*\("
    r"|^\s*sys\.exit\s*\(\s*main\s*\(|^\s*(?:await\s+)?main\s*\(\s*\)\s*;?\s*$"
)


def _is_entry_point_guard(code: str) -> bool:
    return bool(ENTRY_GUARD_RE.match(code))


def _is_constant(code: str) -> bool:
    pair = _assignment(code)
    if not pair:
        return False
    name, rhs = pair
    shouts = bool(re.match(r"^_?[A-Z][A-Z0-9_]*$", name))
    if not (shouts or CONST_KEYWORD_RE.match(code)):
        return False
    # Literal right-hand sides only. `MAX = BASE * 2` is arithmetic that can
    # be wrong, and a shouting name does not make it arid.
    if LITERAL_RHS_RE.match(rhs) or LITERAL_COLLECTION_RE.match(rhs):
        return True
    if LITERAL_OPENER_RE.match(rhs):
        return True
    return bool(LITERAL_CALL_RE.match(rhs) and not OPERATOR_CHARS_RE.search(rhs[rhs.find("("):]))


def _body_text(scan: FileScan, func: Func, line: int) -> str:
    """The body half of a line, which for a one-line function means dropping
    the declaration and the closing brace. Without this, every one-line
    getter in Java and JavaScript reads as "a line with a method signature
    on it" and stays mutable -- which is exactly the mutant nobody wants."""
    text = scan.code[line - 1]
    if func.head and line == func.start:
        text = text[func.head:].rstrip()
        if text.endswith("}"):
            text = text[:-1]
    return text


def _single_statement_body(scan: FileScan, func: Func) -> int | None:
    """The line number when the function's body is exactly one statement.

    Stops at the second statement rather than materialising the whole body.
    It was a list comprehension over every line of the enclosing function,
    called twice for every candidate line in that function, so one function
    cost O(body^2): measured on a file well under MAX_FILE_BYTES, 8,005 lines
    inside one function took 12.2s and 4,005 lines took 3.5s -- 2x the lines
    for 3.5x the time. Extrapolated to a 2MB file of that shape, the file
    ceiling permits roughly an hour and a half of work, so the ceiling was not
    actually bounding anything. Same answer, O(body).
    """
    found: int | None = None
    for n in func.body:
        if n > len(scan.code):
            continue
        stripped = scan.code[n - 1].strip()
        if not stripped or stripped in STRUCTURAL:
            continue
        if found is not None:
            return None          # a second statement: not a one-liner
        found = n
    return found


ACCESSOR_RETURN_RE = re.compile(
    r"^\s*return\s+(?:self|this)\s*(?:\.|->)\s*[\w$]+\s*;?\s*$"
    r"|^\s*return\s+[\w$]+\s*;?\s*$"
    r"|^\s*return\s+_[\w$]+\s*;?\s*$"
    # Ruby returns the last expression, so a body that is one instance
    # variable IS the getter.
    r"|^\s*@[\w]+\s*$"
)
ACCESSOR_ASSIGN_RE = re.compile(
    r"^\s*(?:self|this)\s*(?:\.|->)\s*[\w$]+\s*=\s*[\w$]+\s*;?\s*$"
    r"|^\s*[\w$]+\s*=\s*[\w$]+\s*;?\s*$"
)
# Case-sensitive on purpose, with the capitalised forms spelled out. Making
# it case-insensitive turns `[_A-Z]` into "any letter" and the rule starts
# firing on `getting`, `settle` and `issue`.
ACCESSOR_NAME_RE = re.compile(r"^(?:get|set|is|has|Get|Set|Is|Has)(?:[_A-Z]|$)")


def _is_trivial_accessor(scan: FileScan, func: Func, line: int) -> bool:
    """A one-line getter or setter that only moves a field.

    Both halves are required: the body must be a single field read or write,
    AND the function must be named like an accessor or be a declared property.
    Requiring the name is what keeps `return total` inside `def
    compute_total()` mutable -- a one-line calculation is not an accessor.
    """
    only = _single_statement_body(scan, func)
    if only != line:
        return False
    body = _body_text(scan, func, line)
    if not (ACCESSOR_RETURN_RE.match(body) or ACCESSOR_ASSIGN_RE.match(body)):
        return False
    if ACCESSOR_NAME_RE.match(func.name):
        return True
    decorated = any("property" in scan.code[n - 1] or "setter" in scan.code[n - 1]
                    for n in range(max(1, func.start - 3), func.start))
    if decorated:
        return True
    # `return self._x` with a leading-underscore field, and Ruby's bare
    # `@field`, are the getter shape whatever the method is called.
    return bool(re.match(r"^\s*return\s+(?:self|this)\s*(?:\.|->)\s*_", body)
                or re.match(r"^\s*@[\w]+\s*$", body))


def _is_pure_delegation(scan: FileScan, func: Func, line: int,
                        callee: str | None) -> bool:
    """A body that is one call forwarding this function's own arguments.

    The argument list has to BE the parameter list -- same names, nothing
    added, nothing computed. `return self._repo.save(order)` inside
    `save(order)` is delegation; `return self._repo.save(order, now())` is
    not, because something was decided on this line.
    """
    only = _single_statement_body(scan, func)
    if only != line:
        return False
    code = _body_text(scan, func, line).strip()
    m = re.match(r"^(?:return\s+|await\s+|yield\s+)*"
                 r"(?P<callee>[\w$]+(?:\s*[\.:]{1,2}\s*[\w$]+)*)\s*\((?P<args>.*)\)"
                 r"\s*;?\s*$", code)
    if not m:
        return False
    if not func.params:
        return False
    args = [a.strip() for a in m.group("args").split(",") if a.strip()]
    args = [re.sub(r"^\*{1,2}", "", a) for a in args]
    args = [a.split("=")[-1].strip() if "=" in a else a for a in args]
    if not args or len(args) != len(func.params):
        return False
    return all(re.match(r"^[\w$]+$", a) and a in func.params for a in args)


def _is_timing(code: str, callee: str | None, stripped: str) -> bool:
    if SLEEP_CALL_RE.match(code):
        return True
    pair = _assignment(code)
    if pair and CLOCK_CALL_RE.search(pair[1]):
        # Only when the variable is named like a timer. `expires_at =
        # time.time() + ttl` is business logic about when something stops
        # being valid, and it keeps its mutant.
        return bool(TIMER_NAME_RE.match(pair[0]))
    if pair and TIMER_NAME_RE.match(pair[0]) and CLOCK_CALL_RE.search(code):
        return True
    return False


def _is_cache_lookup(code: str) -> bool:
    """A read or write against something named like a cache.

    Both the name and the operation are required. The name alone would catch
    `cache_ttl_days * 2`, which is a real calculation; the operation alone
    would catch every `.get(` in the program.
    """
    if not CACHE_NAME_RE.search(code):
        return False
    return bool(CACHE_OP_RE.search(code))


def _is_defensive_guard(scan: FileScan, line: int) -> bool:
    """A null check whose whole consequence is to re-raise or hand the value
    back untouched.

    The null check is mandatory. A guard that returns early on a real
    condition (`if amount > limit: return DENIED`) is a business decision and
    stays mutable -- which is why this looks for None/null/nil/undefined
    rather than for the early-return shape.
    """
    code = scan.code[line - 1]
    if not re.match(r"^\s*(?:\}?\s*)?(?:if|unless|guard)\b", code.strip()):
        return False
    if not NULLISH_RE.search(code):
        return False
    tail = _consequence(code)
    if tail:
        return bool(RETURN_UNCHANGED_RE.match(tail))
    nxt = scan.next_code_line(line)
    if nxt is None:
        return False
    return bool(RETURN_UNCHANGED_RE.match(scan.code[nxt - 1]))


def _consequence(code: str) -> str:
    """The `then` half written on the same line as the condition, across the
    three shapes it takes: `if c: stmt`, `if (c) { stmt }` and the braceless
    `if (c) stmt;`. Empty when the body is on the following lines."""
    _, sep, tail = code.partition(":")
    if sep and tail.strip():
        return tail.strip()
    brace = code.find("{")
    if brace >= 0:
        return code[brace + 1:].strip().rstrip("}").strip()
    m = re.match(r"^\s*(?:\}?\s*)?(?:if|unless|guard)\s*\(", code)
    if m:
        depth = 0
        for idx in range(m.end() - 1, len(code)):
            if code[idx] == "(":
                depth += 1
            elif code[idx] == ")":
                depth -= 1
                if depth == 0:
                    return code[idx + 1:].strip()
    return ""


# --------------------------------------------------------------------------
# per-file analysis
# --------------------------------------------------------------------------

def analyse_file(path: str, raw: list[str], lang: str, *, seed: int = 0,
                 max_per_file: int = 7, feedback: Feedback | None = None,
                 changed: set[int] | None = None, use_ast: bool = True) -> dict:
    feedback = feedback or Feedback()
    entry: dict = {
        "file": path,
        "language": lang,
        "candidates": [],
        "suppressed": [],
        "selected": [],
    }

    # File-level suppression comes first and short-circuits: a test file or a
    # generated file produces one explanation, not one per line. Reporting
    # 400 suppressed lines for a protobuf stub is itself noise.
    for rule, applies in (("test-file", is_test_file(path)),
                          ("generated-code", is_generated(path, raw))):
        if applies:
            entry["suppressed"].append({
                "line": 1,
                "snippet": (raw[0].strip()[:97] if raw else ""),
                "rule": rule,
                "scope": "file",
                "why": RULES[rule]["why"],
            })
            entry["file_suppressed_by"] = rule
            return entry

    scan = FileScan(path, lang, raw, use_ast=use_ast)
    entry["parsed_with"] = "ast" if scan.ast_used else "regex"
    if scan.ast_unavailable:
        # `parsed_with: "regex"` is also what a Java file and a --no-ast run
        # say, so it cannot carry this on its own. A separate key, named for
        # what happened, so "regex because that is the right tool here" and
        # "regex because the AST pass could not run" stay distinguishable.
        entry["ast_unavailable"] = scan.ast_unavailable

    pool: list[dict] = []
    for i in range(1, len(scan.code) + 1):
        if changed is not None and i not in changed:
            continue
        ops = applicable_operators(scan.code[i - 1], lang)
        ops -= feedback.operators
        snippet = scan.snippet(i)
        if not ops:
            continue
        candidate = {"line": i, "snippet": snippet, "operators": sorted(ops)}
        entry["candidates"].append(candidate)

        rule = arid_rule(scan, i)
        if rule:
            entry["suppressed"].append({
                "line": i, "snippet": snippet, "rule": rule,
                "why": RULES[rule]["why"],
            })
            continue

        fb = feedback.match(scan.code[i - 1], classify_node(scan.code[i - 1]),
                            _call_statement(scan.code[i - 1]))
        if fb:
            why = fb.get("note") or RULES["feedback"]["why"]
            entry["suppressed"].append({
                "line": i, "snippet": snippet, "rule": "feedback",
                "from_feedback": True, "feedback_entry": fb, "why": why,
            })
            continue

        operator = choose_operator(ops, seed, path, i, snippet)
        pool.append({
            "line": i, "snippet": snippet, "operator": operator,
            "operators": sorted(ops),
            "why": OPERATORS[operator]["what"],
        })

    # One mutant per line is already done (one operator each). The cap is the
    # second knife: keep the most productive, report the rest as capped so
    # nobody believes the file was clean.
    pool.sort(key=lambda e: _rank_key(e, seed, path))
    kept = pool[:max_per_file] if max_per_file > 0 else pool
    dropped = pool[max_per_file:] if max_per_file > 0 else []
    entry["selected"] = sorted(kept, key=lambda e: e["line"])
    for e in sorted(dropped, key=lambda e: e["line"]):
        entry["suppressed"].append({
            "line": e["line"], "snippet": e["snippet"], "rule": "per-file-cap",
            "operator": e["operator"], "why": RULES["per-file-cap"]["why"],
        })
    entry["suppressed"].sort(key=lambda e: (e["line"], e["rule"]))
    return entry


# --------------------------------------------------------------------------
# driving
# --------------------------------------------------------------------------

SKIP_DIRS = ("node_modules", ".git", "venv", ".venv", "build", "dist",
             "target", "__pycache__", ".tox", "vendor")


def iter_paths(paths: list[str]):
    for p in paths:
        if os.path.isdir(p):
            for root, dirs, names in os.walk(p):
                dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
                for name in sorted(names):
                    yield os.path.join(root, name)
        else:
            yield p


def read_lines_bounded(path: str, *, max_bytes: int = MAX_FILE_BYTES,
                       max_line_chars: int = MAX_LINE_CHARS
                       ) -> tuple[list[str] | None, list[dict]]:
    """(lines, truncated) -- lines is None for an unreadable, binary or
    oversized file; `truncated` lists every line whose tail was NOT read.

    The second element exists so the caller cannot report silence about text
    it shortened. A mutation planner that crashes on one odd file blocks the
    whole run, so this never raises.
    """
    try:
        if os.path.getsize(path) > max_bytes:
            return None, []
        with open(path, "rb") as fh:
            blob = fh.read()
    except OSError:
        return None, []
    if b"\x00" in blob[:8000]:
        return None, []
    try:
        text = blob.decode("utf-8")
    except UnicodeDecodeError:
        try:
            text = blob.decode("latin-1")
        except Exception:
            return None, []

    lines: list[str] = []
    truncated: list[dict] = []
    for n, line in enumerate(text.splitlines(keepends=True), start=1):
        if len(line) > max_line_chars:
            truncated.append({
                "file": path,
                "line": n,
                "chars": len(line),
                "read_chars": max_line_chars,
                "reason": (f"line is {len(line)} characters, over the "
                           f"{max_line_chars}-character line ceiling; only "
                           f"the first {max_line_chars} were considered for "
                           "mutation and the rest was NOT read"),
            })
            line = line[:max_line_chars]
        lines.append(line)
    return lines, truncated


def read_lines(path: str, *, max_bytes: int = MAX_FILE_BYTES) -> list[str] | None:
    """Back-compatible wrapper: lines only. Prefer read_lines_bounded(), which
    also hands back what it had to shorten."""
    return read_lines_bounded(path, max_bytes=max_bytes)[0]


def plan(paths: list[str], *, seed: int = 0, max_per_file: int = 7,
         feedback: Feedback | None = None,
         diff: dict[str, set[int]] | None = None,
         use_ast: bool = True, max_bytes: int = MAX_FILE_BYTES,
         max_line_chars: int = MAX_LINE_CHARS) -> dict:
    feedback = feedback or Feedback()
    files: list[dict] = []
    skipped_files: list[dict] = []
    truncated_lines: list[dict] = []
    unparsed_files: list[dict] = []
    by_rule: dict[str, int] = {}
    considered = 0
    capped = 0
    selected = 0
    from_feedback = 0

    for path in iter_paths(paths):
        lang = detect_language(path)
        if lang is None:
            continue
        changed = None
        if diff is not None:
            changed = changed_lines_for(path, diff)
            if changed is None:
                # Not in the diff. Silence, not a finding: a mutation gate
                # scoped to a change has nothing to say about untouched files.
                continue
        raw, cut = read_lines_bounded(path, max_bytes=max_bytes,
                                      max_line_chars=max_line_chars)
        truncated_lines.extend(cut)
        if raw is None:
            # A file with no mutants and a file nobody opened produce the same
            # empty result, so the second one has to say so.
            try:
                oversized = os.path.getsize(path) > max_bytes
            except OSError:
                oversized = False
            skipped_files.append({
                "file": path,
                "reason": (f"larger than {max_bytes} bytes "
                           f"({max_bytes // (1024 * 1024)}MB ceiling); "
                           "raise --max-file-bytes to plan it")
                if oversized else "unreadable or binary",
            })
            continue
        entry = analyse_file(path, raw, lang, seed=seed, max_per_file=max_per_file,
                             feedback=feedback, changed=changed, use_ast=use_ast)
        if entry.get("ast_unavailable") == AST_TOO_DEEP:
            # The file WAS read and WAS planned -- so it is not in
            # skipped_files and it does count in summary.files -- but it was
            # planned by the weaker of the two scanners. Saying which, and
            # what that costs, is the difference between a partial analysis
            # and a partial analysis reported as complete.
            unparsed_files.append({
                "file": path,
                "stage": "ast",
                "reason": AST_TOO_DEEP_REASON,
                "fell_back_to": "regex",
                "not_analysed": AST_TOO_DEEP_LOST,
            })
        considered += len(entry["candidates"])
        selected += len(entry["selected"])
        for s in entry["suppressed"]:
            by_rule[s["rule"]] = by_rule.get(s["rule"], 0) + 1
            if s["rule"] == "per-file-cap":
                capped += 1
            if s.get("from_feedback"):
                from_feedback += 1
        files.append(entry)

    by_operator: dict[str, int] = {}
    for f in files:
        for s in f["selected"]:
            by_operator[s["operator"]] = by_operator.get(s["operator"], 0) + 1

    return {
        "schema": SCHEMA,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "settings": {
            "seed": seed,
            "max_per_file": max_per_file,
            "diff_scoped": diff is not None,
            "feedback": feedback.summary() if feedback.active or feedback.reasserted
            else None,
        },
        "skipped_files": skipped_files,
        # A truncated line is a third state, distinct from both "file not
        # read" and "file read, no mutants": the file WAS read and WAS
        # planned, but part of a line was not looked at, so "no mutants on
        # this line" is a statement about its head only.
        "truncated_lines": truncated_lines,
        # A third state again, one level sideways: the file was read in full
        # and planned in full, but by the regex scanner, because the AST pass
        # could not run on it. Never a finding -- deeply nested generated code
        # is legitimate, and a check that fires on it would be switched off
        # inside a week, taking the rest of the gate with it.
        "unparsed_files": unparsed_files,
        "files": files,
        "summary": {
            "files": len(files),
            "files_skipped": len(skipped_files),
            "files_ast_unparsed": len(unparsed_files),
            "lines_truncated": len(truncated_lines),
            "files_with_truncated_lines": len({t["file"] for t in truncated_lines}),
            "lines_considered": considered,
            "suppressed_by_rule": dict(sorted(by_rule.items())),
            "suppressed_by_cap": capped,
            "suppressed_by_feedback": from_feedback,
            "selected": selected,
            "selected_by_operator": dict(sorted(by_operator.items())),
        },
    }


def render_text(result: dict) -> str:
    """`path:line  OPERATOR  snippet` for what will be mutated, then a count
    per suppression rule. Terse because the point of the whole file is that a
    developer reads the list -- a page of prose is a list nobody reads."""
    out: list[str] = []
    for f in result["files"]:
        for s in f["selected"]:
            out.append(f"{f['file']}:{s['line']}  {s['operator']}  {s['snippet']}")
    s = result["summary"]
    out.append("")
    out.append(f"{s['selected']} mutants from {s['lines_considered']} candidate "
               f"lines in {s['files']} files")
    for rule, count in s["suppressed_by_rule"].items():
        out.append(f"  suppressed {count:>5}  {rule}")
    if not s["suppressed_by_rule"]:
        out.append("  suppressed     0")
    # Suppressed and never-read are different states; a reader who cannot tell
    # them apart will read "no mutants here" as "nothing to test here".
    unread = result.get("skipped_files") or []
    if unread:
        out.append(f"{len(unread)} file(s) NOT planned (see skipped_files):")
        for entry in unread[:5]:
            out.append(f"  skipped {entry['file']}: {entry['reason']}")
        if len(unread) > 5:
            out.append(f"  ... and {len(unread) - 5} more")
    # Same distinction one level down: part of this line was never read, so
    # "no mutant here" is a claim about its first MAX_LINE_CHARS only.
    cut = result.get("truncated_lines") or []
    if cut:
        out.append(f"{len(cut)} line(s) only PARTLY read "
                   "(see truncated_lines):")
        for entry in cut[:5]:
            out.append(f"  truncated {entry['file']}:{entry['line']}: "
                       f"{entry['chars']} chars, first {entry['read_chars']} "
                       "considered, the rest NOT read")
        if len(cut) > 5:
            out.append(f"  ... and {len(cut) - 5} more")
    # And the state between the two: fully read, fully planned, but with the
    # AST pass missing. Out loud in the terminal as well as in the JSON,
    # because the terminal is where a developer decides the run went fine.
    deep = result.get("unparsed_files") or []
    if deep:
        out.append(f"{len(deep)} file(s) planned WITHOUT the AST pass "
                   "(see unparsed_files):")
        for entry in deep[:5]:
            out.append(f"  not parsed {entry['file']}: expression nesting too "
                       f"deep for ast.parse; fell back to {entry['fell_back_to']}, "
                       "so exact function spans and parameter names were NOT used")
        if len(deep) > 5:
            out.append(f"  ... and {len(deep) - 5} more")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="arid_nodes.py",
        description="Decide which lines are worth mutating (arid-node "
                    "suppression, one mutant per line, per-file cap).",
    )
    ap.add_argument("paths", nargs="+", help="source files or directories")
    ap.add_argument("--diff", help="unified diff; scope to its changed lines")
    ap.add_argument("--max-per-file", type=int, default=7,
                    help="hard cap on mutants surfaced per file "
                         "(default 7, Google's number; 0 disables the cap)")
    ap.add_argument("--seed", type=int, default=0,
                    help="seed for the one-mutant-per-line draw (default 0)")
    ap.add_argument("--feedback", help="feedback JSON of mutants judged useless")
    ap.add_argument("--format", choices=("json", "text"), default="json")
    ap.add_argument("--out", help="write the report here instead of stdout")
    ap.add_argument("--max-file-bytes", type=int, default=MAX_FILE_BYTES,
                    help="skip source files larger than this "
                         f"(default {MAX_FILE_BYTES}, i.e. 2MB); skipped files "
                         "are reported in skipped_files, never ignored")
    ap.add_argument("--max-line-chars", type=int, default=MAX_LINE_CHARS,
                    help="consider only the first N characters of a line "
                         f"(default {MAX_LINE_CHARS}); every line past it is "
                         "reported in truncated_lines, never silently cut")
    args = ap.parse_args(argv)

    missing = [p for p in args.paths if not os.path.exists(p)]
    if missing:
        print(f"error: no such path: {', '.join(missing)}", file=sys.stderr)
        return 2

    try:
        diff = None
        if args.diff:
            with open(args.diff, "r", encoding="utf-8") as fh:
                diff = parse_diff(fh.read())
        feedback = load_feedback(args.feedback) if args.feedback else None
        result = plan(args.paths, seed=args.seed, max_per_file=args.max_per_file,
                      feedback=feedback, diff=diff,
                      max_bytes=args.max_file_bytes,
                      max_line_chars=args.max_line_chars)
        payload = (json.dumps(result, indent=2) if args.format == "json"
                   else render_text(result))
        if args.out:
            # Atomic: the mutation plan is written under .testing/ alongside
            # whatever the other parallel agents are writing.
            write_text_atomic(args.out, payload + "\n")
        else:
            print(payload)
        for entry in result["skipped_files"]:
            print(f"warning: not planned: {entry['file']}: {entry['reason']}",
                  file=sys.stderr)
        for entry in result["truncated_lines"]:
            print(f"warning: only partly read: {entry['file']}:"
                  f"{entry['line']}: {entry['reason']}", file=sys.stderr)
        for entry in result["unparsed_files"]:
            print(f"warning: planned without the AST pass: {entry['file']}: "
                  f"{entry['reason']} Fell back to {entry['fell_back_to']}; "
                  f"NOT analysed: {entry['not_analysed']}.", file=sys.stderr)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
