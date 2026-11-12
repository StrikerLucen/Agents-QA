#!/usr/bin/env python3
"""Tier 0 gate: find flaky and meaningless tests statically, before running them.

WHY THIS IS STATIC, AND WHY IT RUNS FIRST
-----------------------------------------
The obvious way to find a flaky test is to run it a few times and see if it
disagrees with itself. That does not work, and the arithmetic says so.

  * Flakiness compounds across a suite. With 1,000 tests each failing
    spuriously 0.1% of the time, the chance a given PR hits at least one
    flaky failure is 1 - 0.999^1000 ~= 63%. Most PRs, in other words.
  * Rerunning has poor detection power against exactly the flakes that
    survive longest. Detection power is 1 - (1 - p)^N for a flake that fires
    with probability p over N reruns. Five reruns catch a 50% flake 97% of
    the time -- but a 5% flake only 23% of the time. To catch a 5% flake at
    95% confidence you need N ~= ln(0.05)/ln(0.95) ~= 59 reruns.
  * Sixty reruns per test is not a budget anyone has, so rerun-based
    detection silently specialises in loud flakes and lets quiet ones
    through. Quiet ones are the ones that live for years.

So: static smells are the PRIMARY gate and reruns are secondary confirmation.
This script costs nothing, needs no LLM, executes no test, and runs on every
generated or changed test before it is ever executed.

WHY FALSE POSITIVES ARE THE DESIGN CONSTRAINT
---------------------------------------------
Google's own operating rule is that a check firing wrongly often enough gets
auto-disabled -- somewhere around 10% false positives a check stops being
read and starts being skipped, taking its true positives with it. A quiet
detector that misses some real smells is worth more than a loud one nobody
reads. Every rule here therefore:

  * runs on source with comments and string literals scrubbed out, so
    commented-out code and strings that merely mention `time.sleep` are
    invisible to it;
  * fires only inside recognised test files, and the meaningless-test rules
    fire only inside recognised test functions;
  * has an explicit suppressor for its common legitimate twin (a seeded RNG,
    a sorted listdir, an approximate float comparison, a localhost URL).

Where a rule is a heuristic that can miss things, the comment above it says
what it misses. We do not attempt real parsing -- except for Python, where
`ast` gives a materially better answer about which functions contain no
assertion at all, so the Python path uses it and falls back to the regex
path when the file does not parse.

PER-LANGUAGE WEIGHTING
----------------------
Flake cause distributions differ sharply by ecosystem, and a detector that
ignores that gives every finding the same urgency:

  Java        async-wait 45%, concurrency 20%, order-dependency 12%
  Python      order-dependency 59%, test-infrastructure 28%, async-wait ~0%
  JavaScript  concurrency 21%, async 20%, OS 18%, network 13%,
              order-dependency ~0 (per-file process isolation)

This is attached as a `note` on the relevant findings, never used as a hard
filter: a fixed sleep in Python is still a bad test, it is just not the
reason Python suites flake.

Standard library only, so this runs before anything may be installed.
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import re
import sys
from datetime import datetime, timezone
from fractions import Fraction

if __package__ in (None, ""):  # run as a script: scripts/ is already sys.path[0]
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from atomic_io import write_text_atomic  # noqa: E402

SCHEMA = "testsquad.flakesmells/1"

SEVERITIES = ("block", "warn", "info")


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

# A file is only scanned if it looks like a test. Firing on production code
# would be pure noise: `time.sleep` in a retry loop is correct there.
TEST_FILE_PATTERN = re.compile(
    r"(^|/)(tests?|spec|specs|__tests__|e2e|integration_tests?)/"
    r"|(^|/)test_[^/]*\.(py|rb)$"
    r"|(^|/)[^/]*_test\.(py|go|rb|dart|php)$"
    r"|[._-](test|spec)\.(js|jsx|ts|tsx|mjs|dart)$"
    r"|(^|/)tests?\.py$"
    r"|(^|/)Test[A-Z][^/]*\.(java|kt|cs)$"
    r"|(^|/)[^/]*Tests?\.(java|kt|cs)$"
    r"|(^|/)[^/]*(Test|Spec)\.(php)$",
    re.IGNORECASE,
)

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


def detect_language(path: str) -> str | None:
    return EXT_LANG.get(os.path.splitext(path)[1].lower())


def is_test_file(path: str) -> bool:
    return bool(TEST_FILE_PATTERN.search(path.replace(os.sep, "/")))


# --------------------------------------------------------------------------
# scrubbing
# --------------------------------------------------------------------------

def scrub(lines: list[str], lang: str) -> tuple[list[str], list[list[str]], list[str]]:
    """Split each line into (code, string contents, comment text).

    This is the single most important false-positive guard in the file. Every
    regex rule runs against `code`, where comments are gone and every string
    literal has been collapsed to `""`. That is what makes

        # time.sleep(5)            <- commented out, must stay silent
        msg = "call time.sleep()"  <- a string, must stay silent

    invisible, while the rules that genuinely care about literal text (URLs,
    TODO scaffolds) read `strings` and `comments` deliberately.

    Known limits: this is a character scanner, not a lexer. A JavaScript
    regex literal containing `//`, or Ruby's =begin/=end block comments, are
    not understood. Both failure modes make the scanner scrub MORE than it
    should, which loses findings rather than inventing them -- the safe
    direction.
    """
    code_lines: list[str] = []
    string_lines: list[list[str]] = []
    comment_lines: list[str] = []

    block: str | None = None          # open multi-line comment/string marker
    tokens = LINE_COMMENT.get(lang, ("//",))
    has_block_comment = lang not in ("python", "ruby")

    for raw in lines:
        buf: list[str] = []
        strs: list[str] = []
        cmt: list[str] = []
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
                cmt.append(raw[i:])
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
        comment_lines.append("".join(cmt))
    return code_lines, string_lines, comment_lines


# --------------------------------------------------------------------------
# rule catalogue
# --------------------------------------------------------------------------
# why = what will go wrong, in plain language. fix = the concrete remedy.
# Neither is allowed to restate the regex: a finding that only says what it
# matched gets read as noise and the check gets turned off.

RULES: dict[str, dict] = {
    # --- flake -----------------------------------------------------------
    "unseeded-random": dict(
        category="flake", severity="block", family="randomness",
        why="The test feeds itself a different input on every run, so it "
            "passes today and fails on the run that happens to draw the "
            "unlucky value -- and the failure is not reproducible.",
        fix="Seed the generator once at the top of the file: random.seed(0) "
            "(Faker.seed(0), new Random(42), rand.New(rand.NewSource(1))).",
    ),
    "seed-immune-random": dict(
        category="flake", severity="block", family="randomness",
        why="SystemRandom, secrets and os.urandom draw from the operating "
            "system's entropy pool, which random.seed cannot reach. The seed "
            "at the top of the file looks like it makes this reproducible and "
            "does not, so the test draws a different value on every run and "
            "the one failure you get back cannot be replayed.",
        fix="Draw from the seeded generator instead (random.randrange after "
            "random.seed(0)), or pass the value the test needs in rather than "
            "generating one.",
    ),
    "real-clock": dict(
        category="flake", severity="warn", family="clock",
        why="The assertion depends on what time it is when it runs, so it "
            "will fail at a midnight rollover, a DST change, or whenever the "
            "machine is slow enough that the clock moves mid-test.",
        fix="Inject a fixed clock and assert against it (freeze_time, "
            "jest.useFakeTimers, Clock.fixed(...), a passed-in now()).",
    ),
    "naive-local-time": dict(
        category="flake", severity="warn", family="clock",
        why="Converting a timestamp without naming a timezone uses whatever "
            "zone the machine happens to be set to, so the same assertion "
            "passes in UTC and fails in Europe/Madrid -- and there it fails on "
            "every run, so it reads as a real bug nobody can reproduce.",
        fix="Name the zone: datetime.fromtimestamp(0, tz=timezone.utc), "
            "astimezone(ZoneInfo(\"UTC\")), and set TZ=UTC for the suite.",
    ),
    "sleep": dict(
        category="flake", severity="warn", family="async-wait",
        why="A fixed wait is a bet that the work finishes in that long. On a "
            "loaded CI machine it does not, and the test fails for a reason "
            "that has nothing to do with the code.",
        fix="Wait for the condition, not the clock: poll until the state is "
            "ready with a timeout, or await the operation directly.",
    ),
    "real-network": dict(
        category="flake", severity="block", family="network",
        why="The test's result depends on DNS, a third party's uptime and the "
            "CI network. It will fail on a flight, in a sandboxed runner, and "
            "on the day the remote service is slow.",
        fix="Stub the call (responses/nock/WireMock) or point it at a local "
            "fixture server on 127.0.0.1.",
    ),
    "fs-order": dict(
        category="flake", severity="info", family="os",
        why="Directory listing order is whatever the filesystem feels like "
            "returning. It is stable on your laptop and different in the "
            "container, so this passes locally and fails in CI.",
        fix="Sort the listing before using it: sorted(os.listdir(p)).",
    ),
    "iteration-order": dict(
        category="flake", severity="info", family="order-dependency",
        why="The assertion pins an ordered literal against a collection with "
            "no guaranteed order, so it depends on hashing and insertion "
            "history rather than on the behaviour under test.",
        fix="Compare order-insensitively: sort both sides, or assert on a "
            "set/dict rather than on a list.",
    ),
    "float-equality": dict(
        category="flake", severity="warn", family="numeric",
        why="Binary floating point does not hold decimal values exactly "
            "(0.1 + 0.2 != 0.3), so this comparison is testing the rounding "
            "behaviour of the hardware, not the code.",
        fix="Compare with a tolerance: assertAlmostEqual(x, 0.3, places=7), "
            "toBeCloseTo(0.3), or use Decimal for money.",
    ),
    "locale-timezone": dict(
        category="flake", severity="info", family="os",
        why="Formatting without an explicit locale or timezone produces a "
            "different string on a machine configured differently, so the "
            "test encodes the developer's laptop settings.",
        fix="Pass the locale and timezone explicitly, and set TZ=UTC for the "
            "suite.",
    ),
    "shared-mutable-state": dict(
        category="flake", severity="info", family="order-dependency",
        why="A mutable value declared outside the tests and written inside "
            "them leaks between tests, so the suite passes in one order and "
            "fails in another -- including when a single test is run alone.",
        fix="Build the value fresh per test in setUp/beforeEach instead of "
            "sharing one instance across the module.",
    ),

    # --- meaningless ------------------------------------------------------
    "empty-test": dict(
        category="meaningless", severity="block", family="meaningless",
        why="The test body does nothing. It can never fail, so it reports "
            "green forever while covering nothing.",
        fix="Write the arrange/act/assert, or delete the test.",
    ),
    "no-assertion": dict(
        category="meaningless", severity="block", family="meaningless",
        why="The test calls the code but never checks the result, so it only "
            "fails if the code throws. It buys coverage and no confidence.",
        fix="Assert on the observable outcome -- the return value, the "
            "persisted state, or the call that should have been made.",
    ),
    "tautological-assertion": dict(
        category="meaningless", severity="block", family="meaningless",
        why="Both sides of the comparison are the same thing, so the "
            "assertion holds no matter what the code under test does.",
        fix="Assert the computed value against an independently known "
            "expected value.",
    ),
    "todo-assertion": dict(
        category="meaningless", severity="block", family="meaningless",
        why="A placeholder where the assertion should be. It counts as "
            "coverage and verifies nothing; reviewers reject these even when "
            "they raise the coverage number.",
        fix="Write the real assertion now, or delete the stub rather than "
            "merge a test that cannot fail.",
    ),
    "mock-return-tautology": dict(
        category="meaningless", severity="block", family="meaningless",
        why="The only assertion checks the value the test itself configured "
            "the mock to return, so it verifies the mocking library, not the "
            "code. This is the single most common way a generated test ends "
            "up meaningless.",
        fix="Assert on what the code did with that value -- a transformation, "
            "a branch taken, or the arguments the collaborator was called "
            "with.",
    ),
    "weak-oracle": dict(
        category="meaningless", severity="warn", family="meaningless",
        why="The only check is that something exists. Almost any wrong value "
            "is also not-null, so the test passes through nearly every bug it "
            "is supposed to catch.",
        fix="Assert the actual expected value, not merely its presence.",
    ),
    "skipped-test": dict(
        category="meaningless", severity="info", family="meaningless",
        why="The test is disabled, so it protects nothing while still looking "
            "like coverage in the suite listing.",
        fix="Fix and re-enable it, or delete it and record the gap.",
    ),
    "focused-test": dict(
        category="meaningless", severity="block", family="meaningless",
        why="A focused test silently disables every other test in the file "
            "(or the run). CI goes green having executed a handful of tests.",
        fix="Remove .only / fit / fdescribe before committing.",
    ),
}

# Per-language flake-cause weighting. Attached as a note so a reviewer can
# triage; never used to suppress a finding.
LANG_NOTES: dict[tuple[str, str], str] = {
    ("java", "async-wait"):
        "Java: asynchronous waiting is the largest single flake cause "
        "(~45% of Java flakes); a fixed sleep is its canonical form.",
    ("java", "order-dependency"):
        "Java: order-dependency accounts for ~12% of flakes, and shared "
        "mutable state is ~20% more as concurrency.",
    ("java", "network"):
        "Java: network calls usually surface as async-wait failures, the "
        "dominant (~45%) Java flake family.",
    ("python", "order-dependency"):
        "Python: order-dependency is the dominant flake cause (~59%), so "
        "shared state between tests matters more here than anywhere else.",
    ("python", "async-wait"):
        "Python: async-wait is nearly absent as a flake cause -- this is a "
        "slow test more than a likely-flaky one.",
    ("python", "os"):
        "Python: test-infrastructure and environment issues are ~28% of "
        "flakes, the second-largest family.",
    ("javascript", "async-wait"):
        "JavaScript: concurrency (~21%) and async (~20%) together are the "
        "largest flake families; fixed timeouts are the usual culprit.",
    ("javascript", "network"):
        "JavaScript: network accounts for ~13% of flakes.",
    ("javascript", "os"):
        "JavaScript: OS and filesystem issues are ~18% of flakes.",
    ("javascript", "order-dependency"):
        "JavaScript: order-dependency is nearly nonexistent (per-file "
        "process isolation), so this is lower priority here than it looks.",
}


# --------------------------------------------------------------------------
# patterns
# --------------------------------------------------------------------------

ASSERT_RE = re.compile(
    r"\bassert\b"
    r"|\bassert[A-Z_]\w*\s*\("
    r"|\bassert\s*\("
    r"|\b(?:Assert|Assertions)\s*\.\s*\w+"
    r"|\bexpect\s*\("
    r"|\bshould(?:Be|Equal|Have|Match|NotBe)?\s*[\(.]"
    r"|\.\s*should\b"
    r"|\bverify\s*\("
    r"|\b(?:assert|require)\s*\.\s*\w+\s*\("
    r"|\bt\s*\.\s*(?:Error|Fatal|Errorf|Fatalf)\b"
    r"|\bassert_(?:equal|equals|nil|not_nil|match|includes|raises|empty"
    r"|instance_of|predicate|operator|in_delta|respond_to|same|kind_of|valid)\b"
    r"|\bmust_\w+\b"
    r"|\bExpect\s*\("
    r"|\braises\s*\(|\bassert_raises\b|\.\s*toThrow\w*\s*\("
    r"|\bexpectException\w*\s*\(|\bassertThrows\w*\s*\("
)

# A seed anywhere in the file disarms the randomness rule. Coarse on purpose:
# seeding is a file-level decision and a per-call match would miss the common
# "seed once in setUp" shape.
SEED_RE = re.compile(
    r"\brandom\s*\.\s*seed\s*\("
    r"|\bnp(?:\.random)?\s*\.\s*seed\s*\("
    r"|\bnumpy\.random\.seed\s*\("
    r"|\b(?:Faker|faker|fake)\s*\.\s*seed\w*\s*\("
    r"|\bseed\s*\(\s*\d"
    r"|\bnew\s+Random\s*\(\s*[\dA-Za-z_]"
    r"|\bRandom\s*\(\s*(?:seed\s*[:=]\s*)?\d"
    r"|\brand\s*\.\s*(?:Seed|New)\s*\("
    r"|\bsrand\s*\(\s*\d"
    r"|\bmt_srand\s*\(\s*\d"
    r"|\bsetSeed\s*\("
    r"|\bseed\s*[:=]\s*\d"
)

RANDOM_RE = re.compile(
    r"\brandom\s*\.\s*(?:random|randint|randrange|choice|choices|shuffle|"
    r"sample|uniform|getrandbits)\s*\("
    r"|\bMath\s*\.\s*random\s*\("
    r"|\buuid\s*\.?\s*uuid4\s*\("
    r"|\bcrypto\s*\.\s*randomUUID\s*\("
    r"|\bUUID\s*\.\s*randomUUID\s*\("
    r"|\bGuid\s*\.\s*NewGuid\s*\("
    r"|\bSecureRandom\s*\.\s*\w+"
    r"|\bnew\s+Random\s*\(\s*\)"
    r"|\bRandom\s*\(\s*\)\s*\."
    r"|\brand\s*\.\s*(?:Int|Intn|Float64|Perm)\s*\("
    r"|\bmt_rand\s*\(|\brand\s*\(\s*\d*\s*\)"
    r"|\bFaker\s*\(\s*\)|\bfaker\s*\.\s*\w+\s*\("
)

# Sources the file-level seed cannot reach. Kept apart from RANDOM_RE because
# the seed suppressor must NOT apply to them: a file that calls random.seed(0)
# and then draws from SystemRandom is still non-deterministic, and the seed
# makes it look settled. That is the whole reason this pattern is separate.
UNSEEDABLE_RANDOM_RE = re.compile(
    r"\b(?:random\s*\.\s*)?SystemRandom\s*\("
    r"|\bsecrets\s*\.\s*(?:randbelow|randbits|choice|token_hex|token_bytes"
    r"|token_urlsafe)\s*\("
    r"|\bos\s*\.\s*urandom\s*\("
)

CLOCK_RE = re.compile(
    r"\bdatetime\s*\.\s*(?:datetime\s*\.\s*)?(?:now|utcnow|today)\s*\("
    r"|\bdate\s*\.\s*today\s*\("
    r"|\btime\s*\.\s*(?:time|monotonic|perf_counter)\s*\("
    r"|\bDate\s*\.\s*now\s*\("
    r"|\bnew\s+Date\s*\(\s*\)"
    r"|\bLocalDate(?:Time)?\s*\.\s*now\s*\("
    r"|\bInstant\s*\.\s*now\s*\("
    r"|\bSystem\s*\.\s*currentTimeMillis\s*\("
    r"|\btime\s*\.\s*Now\s*\("
    r"|\bTime\s*\.\s*now\b"
    r"|\bDateTime\s*\.\s*(?:now|Now|UtcNow)\b"
)

# Conversions that silently use the machine's timezone. The timezone-aware
# twin -- fromtimestamp(0, tz=timezone.utc) -- is deterministic and must stay
# silent, which LOCALE_SAFE_RE below handles: it matches the tz argument.
# `utcfromtimestamp` is deliberately absent, being deprecated but deterministic.
LOCAL_TIME_RE = re.compile(
    r"\b(?:datetime|date)\s*\.\s*(?:datetime\s*\.\s*)?fromtimestamp\s*\("
    r"|\btime\s*\.\s*localtime\s*\("
    r"|\.\s*astimezone\s*\(\s*\)"
)

SLEEP_RE = re.compile(
    r"\btime\s*\.\s*[Ss]leep\s*\("
    r"|\bThread\s*\.\s*[Ss]leep\s*\("
    r"|\bsetTimeout\s*\("
    r"|\bwaitForTimeout\s*\("
    r"|\bTask\s*\.\s*Delay\s*\("
    r"|\bFuture\s*\.\s*delayed\s*\("
    r"|\busleep\s*\(|\bsleep\s*\(\s*\d"
    r"|\bdelay\s*\(\s*\d"
)

NETWORK_RE = re.compile(
    r"\brequests\s*\.\s*(?:get|post|put|patch|delete|head|request|Session)\s*\("
    r"|\bhttpx\s*\.\s*(?:get|post|put|patch|delete|Client)\s*\("
    r"|\burllib\s*\.?\s*request\s*\.\s*urlopen\s*\("
    r"|(?<![\w.])fetch\s*\("
    r"|(?<![\w.])axios\s*(?:\.\s*\w+)?\s*\("
    r"|\bsocket\s*\.\s*(?:connect|create_connection)\s*\("
    r"|\bhttp\s*\.\s*(?:Get|Post)\s*\("
    r"|\bNet::HTTP\b"
    r"|\bcurl_exec\s*\("
    r"|\bnew\s+HttpClient\s*\("
    r"|\bHttpURLConnection\b"
)

URL_RE = re.compile(r"https?://([^/\s\"'`)>\\]+)", re.IGNORECASE)

BARE_URL_RE = re.compile(r"^https?://\S+$", re.IGNORECASE)

# A `[` that opens a literal rather than a subscript: not preceded by an
# identifier, a closing bracket or a quote.
LIST_LITERAL_RE = re.compile(r"(?<![\w\]\)\"'])\[")


def _has_delta_argument(line: str) -> bool:
    """JUnit's three-argument assertEquals(expected, actual, delta) IS the
    approximate comparison, and it is indistinguishable from the exact
    two-argument form by regex alone -- the arguments can contain their own
    parentheses, so the commas have to be counted at depth zero."""
    m = re.search(r"\bassert(?:Equals|AreEqual)\s*\(", line)
    if not m:
        return False
    depth, args, current = 0, [], []
    for ch in line[m.end() - 1:]:
        if ch == "(":
            depth += 1
            if depth == 1:
                continue
        elif ch == ")":
            depth -= 1
            if depth == 0:
                args.append("".join(current))
                break
        if depth == 1 and ch == ",":
            args.append("".join(current))
            current = []
            continue
        current.append(ch)
    return len(args) >= 3 and bool(re.match(r"^\s*[\d.]+(?:[eE][+-]?\d+)?\s*$",
                                            args[-1]))


def _exact_in_binary(literal: str) -> bool:
    """True when the decimal has an exact binary float representation, so an
    == comparison against it is deterministic rather than a rounding bet."""
    try:
        denom = Fraction(literal).denominator
    except (ValueError, ZeroDivisionError):
        return False
    return denom & (denom - 1) == 0


# Calls whose arguments ARE the things being compared, so a float literal
# inside one of them is an operand rather than an incidental argument.
EQ_CALL_RE = re.compile(
    r"\bassertEquals?\s*\(|\bassertSame\s*\(|\bassert_equal\s*\("
    r"|\bAssert\s*\.\s*(?:AreEqual|Equal)\s*\("
    r"|\b(?:assert|require)\s*\.\s*(?:[Ee]qual|deepEqual|strictEqual)\s*\("
    r"|(?<![\w.])Equal\s*\("
    r"|(?<![\w.])expect\s*\("
    r"|\.\s*to(?:Be|Equal|StrictEqual)\s*\("
)


def _top_level_split(text: str, seps: tuple[str, ...]) -> list[str]:
    """Split on separators that sit outside any bracket."""
    out: list[str] = []
    cur: list[str] = []
    depth = i = 0
    while i < len(text):
        ch = text[i]
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        if depth == 0:
            hit = next((s for s in seps if text.startswith(s, i)), None)
            if hit:
                out.append("".join(cur))
                cur = []
                i += len(hit)
                continue
        cur.append(ch)
        i += 1
    out.append("".join(cur))
    return out


def _call_args(line: str, open_paren: int) -> list[str]:
    """Top-level arguments of the call whose `(` is at `open_paren`."""
    chunk = line[open_paren + 1:]
    depth = 0
    for i, ch in enumerate(chunk):
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            if depth == 0:
                chunk = chunk[:i]
                break
            depth -= 1
    return _top_level_split(chunk, (",",))


def _strip_call_arguments(expr: str) -> str:
    """Blank out the arguments of every call inside an operand.

    `cents(1.15)` is a call that returns an int; the 1.15 is an input to the
    code under test, not a side of the comparison. Leaving it visible is what
    made `assert cents(1.15) == 115` fire on an exact integer comparison.
    """
    out: list[str] = []
    i = 0
    while i < len(expr):
        ch = expr[i]
        if ch == "(" and out and (out[-1].isalnum() or out[-1] in "_.]"):
            depth = 0
            while i < len(expr):
                if expr[i] == "(":
                    depth += 1
                elif expr[i] == ")":
                    depth -= 1
                    if depth == 0:
                        i += 1
                        break
                i += 1
            out.append("()")
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _compared_operands(line: str) -> list[str]:
    """The expressions actually on either side of an equality assertion."""
    ops: list[str] = []
    for m in EQ_CALL_RE.finditer(line):
        ops.extend(_call_args(line, m.end() - 1))
    m = re.search(r"\bassert\b", line)
    if m:
        # Drop the optional `, "message"` tail before splitting the comparison.
        body = _top_level_split(line[m.end():], (",",))[0]
        sides = _top_level_split(body, ("==", "!="))
        if len(sides) > 1:
            ops.extend(sides)
    return ops


def _compares_inexact_float(line: str) -> bool:
    """True when a decimal binary floating point cannot hold exactly is one of
    the things being compared -- not merely somewhere on the line."""
    return any(
        not _exact_in_binary(lit.group(0))
        for op in _compared_operands(line)
        for lit in FLOAT_LITERAL_RE.finditer(_strip_call_arguments(op))
    )


# Sentence end, but not the decimal point in "0.1 + 0.2 != 0.3".
FIRST_SENTENCE_RE = re.compile(r"(?<=[a-z)])\.\s")

# Hosts that never leave the machine (or are reserved for documentation and
# therefore cannot be a real dependency).
LOCAL_HOST_RE = re.compile(
    r"^(?:localhost|127\.\d+\.\d+\.\d+|0\.0\.0\.0|\[::1\]|::1|host\.docker\.internal"
    r"|testserver|.*\.local|.*\.localhost|.*\.test|.*\.invalid|.*\.example"
    r"|example\.(?:com|org|net))(?::\d+)?$",
    re.IGNORECASE,
)
# XML/JSON-schema namespaces are URLs that are never fetched.
NAMESPACE_RE = re.compile(r"xmlns|namespace|schemaLocation|\$schema|w3\.org|schemas\.",
                          re.IGNORECASE)

FS_ORDER_RE = re.compile(
    r"\bos\s*\.\s*(?:listdir|scandir|walk)\s*\("
    r"|\bglob\s*\.\s*(?:glob|iglob)\s*\("
    r"|\bPath\s*\(\s*[^)]*\)\s*\.\s*(?:glob|iterdir|rglob)\s*\("
    r"|\bfs\s*\.\s*readdir(?:Sync)?\s*\("
    r"|\breaddirSync\s*\("
    r"|\bDir\s*\.\s*(?:glob|entries|children)\b"
    r"|\bscandir\s*\(|\bFile\s*\.\s*listFiles\s*\("
    r"|\bfilepath\s*\.\s*Glob\s*\(|\bioutil\s*\.\s*ReadDir\s*\("
    r"|\bos\s*\.\s*ReadDir\s*\("
)
SORTED_RE = re.compile(r"\bsorted\s*\(|\.\s*sort\s*\(|\bsort\s*\.\s*\w+\s*\("
                       r"|\bOrderBy\s*\(|\.\s*toSorted\s*\(")

ITERATION_ORDER_RE = re.compile(
    r"\.\s*keys\s*\(\s*\)|\.\s*values\s*\(\s*\)|\.\s*items\s*\(\s*\)"
    r"|\bObject\s*\.\s*(?:keys|values|entries)\s*\("
    r"|\.\s*entrySet\s*\(\s*\)|\.\s*keySet\s*\(\s*\)"
    r"|\bnew\s+(?:HashMap|HashSet)\b"
    r"|\bArray\s*\.\s*from\s*\(\s*new\s+(?:Set|Map)\b"
    r"|\blist\s*\(\s*\w*\s*set\s*\("
)

EXACT_EQ_RE = re.compile(
    r"\bassertEquals?\s*\(|\bassert_equal\b|\bassertSame\s*\("
    r"|\.\s*toBe\s*\(|\.\s*toEqual\s*\(|\.\s*toStrictEqual\s*\("
    r"|\bAssert\s*\.\s*(?:AreEqual|Equal)\s*\("
    r"|\bassert\b[^=!<>]*==|\bEqual\s*\(|\btoStrictEqual\s*\("
)
FLOAT_LITERAL_RE = re.compile(r"(?<![\w.])\d+\.\d+(?![\w.])")
APPROX_RE = re.compile(
    r"\bassertAlmostEqual\b|\btoBeCloseTo\b|\bapprox\b|\bplaces\s*=|\bdelta\s*="
    r"|\bInDelta\b|\bcloseTo\b|\btolerance\b|\bepsilon\b|\bMath\s*\.\s*abs\s*\("
    r"|\bDecimal\s*\(|\bwithin\s*\(|\bassertEqualsDelta\b"
)

LOCALE_RE = re.compile(
    r"\bstrftime\s*\(|\bstrptime\s*\("
    r"|\.\s*toLocale(?:Date|Time)?String\s*\("
    r"|\bnew\s+SimpleDateFormat\s*\("
    r"|\bNumberFormat\s*\.\s*getInstance\s*\(\s*\)"
    r"|\bDateTimeFormatter\s*\.\s*ofLocalized\w*\s*\("
    r"|\bToString\s*\(\s*\"\"\s*\)"
    r"|\bsetlocale\s*\("
)
LOCALE_SAFE_RE = re.compile(
    r"\bLocale\s*\.|\blocale\s*[:=]|\btz\s*=|\btimezone\b|\bZoneId\b|\bUTC\b|\butc\b"
    r"|\bCultureInfo\b|\bInvariant\b|\bZoneInfo\b",
    re.IGNORECASE,
)

MUTABLE_DECL_RE = re.compile(
    r"^(?P<name>[A-Za-z_]\w*)\s*(?::[^=]+)?=\s*(?:\[\s*\]|\{\s*\}|set\s*\(\s*\)"
    r"|list\s*\(\s*\)|dict\s*\(\s*\))"
)
STATIC_MUTABLE_RE = re.compile(
    r"\bstatic\b(?!\s+final\b)(?![^;=]*\breadonly\b)[^;=]*?\b(?P<name>\w+)\s*=\s*"
    r"(?:new\s+(?:ArrayList|HashMap|HashSet|List|Dictionary|StringBuilder)|\[|\{)"
)
JS_MUTABLE_DECL_RE = re.compile(
    r"^(?:let|var|const)\s+(?P<name>\w+)\s*(?::[^=]+)?=\s*(?:\[\s*\]|\{\s*\}"
    r"|new\s+(?:Map|Set)\s*\(\s*\))"
)
MUTATE_TMPL = r"\b{name}\s*(?:\.\s*(?:append|push|add|extend|update|set|insert|remove|pop)\s*\(|\[[^\]]*\]\s*=)"

TAUTOLOGY_RES = [
    re.compile(r"\bassert(?:True|_true|_)?\s*\(\s*(?:True|true|1)\s*[,)]"),
    re.compile(r"\bassertFalse\s*\(\s*(?:False|false|0)\s*[,)]"),
    re.compile(r"\bassert\s+(?:True|true)\s*(?:,|$)"),
    re.compile(r"\bAssert\s*\.\s*(?:IsTrue|True)\s*\(\s*true\s*\)"),
    # same expression on both sides of an exact-equality assertion
    re.compile(r"\bassert(?:Equals?|_equal|Same|AreEqual)\s*\(\s*([^,()]+?)\s*,"
               r"\s*\1\s*[,)]"),
    re.compile(r"\bAssert\s*\.\s*(?:AreEqual|Equal)\s*\(\s*([^,()]+?)\s*,\s*\1\s*\)"),
    re.compile(r"\bexpect\s*\(\s*([^()]+?)\s*\)\s*\.\s*(?:toBe|toEqual|toStrictEqual)"
               r"\s*\(\s*\1\s*\)"),
    re.compile(r"\bassert\s+([\w.\[\]]+)\s*==\s*\1\s*(?:,|$)"),
]

# Several patterns above pair a lazy quantifier with a backreference, which
# costs roughly O(n^3) in the length of the line. On repository-controlled
# input that is a denial of service, not a slow path: a single assertion line
# padded with ~6000 spaces holds the scanner for over ninety seconds, and this
# gate runs in CI and on a reviewer's machine against code from a pull
# request. A genuine tautological assertion is short -- the longest in this
# project's own fixtures is under 120 characters -- so a line past the cap is
# minified or generated and is not what this rule is looking for.
MAX_TAUTOLOGY_LINE = 400


def _is_tautology(line: str) -> bool:
    if len(line) > MAX_TAUTOLOGY_LINE:
        return False
    return any(r.search(line) for r in TAUTOLOGY_RES)

WEAK_ORACLE_RE = re.compile(
    r"\bassertIsNotNone\s*\(|\bassertNotNull\s*\(|\bassert_not_nil\b"
    r"|\.\s*toBeDefined\s*\(\s*\)|\.\s*toBeTruthy\s*\(\s*\)"
    r"|\.\s*not\s*\.\s*toBeNull\s*\(\s*\)|\.\s*toBeNull\s*\(\s*\)"
    r"|\bAssert\s*\.\s*NotNull\s*\(|\bisNotNull\s*\(\s*\)"
    r"|\bassertIsNotNull\s*\(|\bexpect\s*\(\s*[^()]*\)\s*\.\s*toBeTruthy\b"
    # pytest's bare assert is the dominant Python idiom, and these two forms
    # are the same weak oracle as assertIsNotNone: they prove the call
    # returned, not that it returned the right thing.
    #   assert result is not None
    #   assert result            <- truthy-only
    # The truthy form deliberately requires a bare name or attribute/call
    # chain with no operator, so `assert a == b`, `assert x in y` and
    # `assert len(xs) > 0` are untouched.
    r"|\bassert\s+[\w.]+(?:\([^()]*\))?\s+is\s+not\s+None\s*(?:,|$)"
    r"|\bassert\s+[\w.]+(?:\([^()]*\))?\s*(?:,[^=]*)?$"
)

# A line carrying one of these is deliberate, and the author said so. Without
# an escape hatch the only way to silence a false positive is to disable the
# tool, which is what people actually do. Requiring a reason keeps the
# suppressions auditable rather than reflexive.
#   assert x == 0.3  # testsquad:ignore float-equality -- rounded upstream
#   url = "https://api.example.com"  # testsquad:ignore -- fixture data
SUPPRESS_RE = re.compile(
    # The rule list must START with an alphanumeric. Without that anchor the
    # lazy group happily swallows the "--" that introduces the reason, so
    # `# testsquad:ignore -- fixture data` parses as the rule name
    # "-- fixture data" and silences nothing -- the exact opposite of what
    # the author asked for, and silently.
    r"(?:#|//|/\*)\s*testsquad:ignore"
    r"(?:\s+([a-z0-9][a-z0-9,\-\s]*?))?"
    r"\s*(?:--|$|\*/)",
    re.IGNORECASE,
)


def suppressed_rules(raw_line: str) -> set[str] | None:
    """Rules silenced on this line. An empty set means "silence everything"."""
    m = SUPPRESS_RE.search(raw_line)
    if not m:
        return None
    names = (m.group(1) or "").strip()
    return {n.strip() for n in names.split(",") if n.strip()} if names else set()


SKIP_RE = re.compile(
    # `skip\b`, not `skip\w*`: @skipIf / @skipUnless are conditional and the
    # condition is usually "this platform cannot run it", which is not a
    # disabled test.
    r"@\s*(?:unittest\s*\.\s*)?skip\b"
    r"|@\s*pytest\s*\.\s*mark\s*\.\s*skip\w*"
    r"|\b(?:it|test|describe|context|specify)\s*\.\s*skip\s*\("
    r"|\b(?:xit|xdescribe|xtest|xcontext)\s*\("
    r"|@\s*(?:Ignore|Disabled)\b|\[\s*(?:Ignore|Skip)\b"
    r"|\bt\s*\.\s*Skip(?:Now)?\s*\("
    r"|\bmarkTestSkipped\s*\(|\bmarkTestIncomplete\s*\("
    r"|\bskip\s*:\s*(?:true|\"\")"
)
FOCUS_RE = re.compile(
    r"\b(?:it|test|describe|context|specify|suite)\s*\.\s*only\s*\("
    r"|\b(?:fit|fdescribe|ftest)\s*\("
)

TODO_RE = re.compile(
    r"\bTODO\b.{0,60}?\b(?:assert|assertion|verify|expectation|implement|"
    r"fill\s*in|add\s+(?:a\s+)?(?:real\s+)?test)"
    r"|\b(?:assert|assertion|verify|implement)\w*\b.{0,30}?\bTODO\b"
    r"|\bFIXME\b.{0,60}?\b(?:assert|assertion)",
    # Case-SENSITIVE on purpose. TODO and FIXME are shouted by convention, and
    # matching them case-insensitively made the rule fire on the ordinary
    # identifier `todo-assertion`.
    re.DOTALL,
)
NOT_IMPLEMENTED_RE = re.compile(
    r"\braise\s+NotImplementedError\b|\bfail\s*\(\s*\"\"\s*\)|\bthrow\s+new\s+"
    r"NotImplementedException\b|\bpytest\s*\.\s*fail\s*\("
)

MOCK_RETURN_RE = re.compile(
    r"\.\s*return_value\s*=\s*(?P<val>[^#\n]+)"
    r"|\bmockReturnValue\s*\(\s*(?P<val2>[^)]*)\)"
    r"|\bmockResolvedValue\s*\(\s*(?P<val3>[^)]*)\)"
    r"|\bthenReturn\s*\(\s*(?P<val4>[^)]*)\)"
    r"|\.\s*Returns\s*\(\s*(?P<val5>[^)]*)\)"
    r"|\bandReturn\s*\(\s*(?P<val6>[^)]*)\)"
)
LITERAL_RE = re.compile(r"^(?:-?\d+(?:\.\d+)?|True|False|true|false|\"\")$")


# --------------------------------------------------------------------------
# test block extraction
# --------------------------------------------------------------------------

class Block:
    """One test function: where it is declared and which lines are its body."""

    __slots__ = ("name", "decl", "start", "end", "head")

    def __init__(self, name: str, decl: int, start: int, end: int, head: int = 0):
        self.name = name
        self.decl = decl        # 1-based declaration line -- the EARLIEST line
                                # of the declaration, so a decorated or
                                # annotated test anchors on the decorator
                                # (@pytest.mark.slow, @Test), not the `def` or
                                # the method signature. Consistent across
                                # languages; do not "fix" it to point at the
                                # name without updating every fixture that
                                # joins findings by (file, line).
        self.start = start      # 1-based first body line
        self.end = end          # 1-based last body line, inclusive
        # Column on `start` where the body begins. Non-zero only for a test
        # written entirely on one line -- `it('x', () => { expect(...) });` --
        # where the declaration and the body share a line and the declaration
        # half must not be mistaken for test code.
        self.head = head

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"Block({self.name!r}, decl={self.decl}, {self.start}-{self.end})"


PY_TEST_DEF_RE = re.compile(r"^(\s*)(?:async\s+)?def\s+(test\w*)\s*\(")

JS_TEST_RE = re.compile(
    r"\b(?:it|test|specify|xit|fit)\s*(?:\.\s*(?:only|skip|each|failing)\s*)?"
    r"(?:\([^)]*\))?\s*\(\s*(?:\"\"|`|\w)"
)
JS_FUNC_TEST_RE = re.compile(r"\bfunction\s+(test\w*)\s*\(")
GO_TEST_RE = re.compile(r"^func\s+((?:Test|Benchmark|Fuzz)\w*)\s*\(")
DART_TEST_RE = re.compile(r"\b(?:test|testWidgets)\s*\(")
RUBY_TEST_RE = re.compile(r"^\s*(?:def\s+(test_\w+)|it\s+.*\bdo\b|specify\s+.*\bdo\b)")
JVM_ANNOTATION_RE = re.compile(r"@\s*(?:Test|ParameterizedTest|RepeatedTest|TestTemplate)\b"
                               r"|\[\s*(?:Test|Fact|Theory|TestMethod)\b")
JVM_METHOD_RE = re.compile(r"\b(\w+)\s*\([^;]*\)\s*(?:throws\s[\w.,\s]+)?\{\s*$")
JVM_METHOD_NL_RE = re.compile(r"\b(\w+)\s*\([^;]*\)\s*(?:throws\s[\w.,\s]+)?$")
PHP_TEST_RE = re.compile(r"\bfunction\s+(test\w*)\s*\(")


def find_blocks(code: list[str], lang: str) -> list[Block]:
    """Locate test functions without parsing. Deliberately conservative: a
    declaration shape we do not recognise yields no block, which means the
    meaningless-test rules stay silent rather than guess at a body."""
    if lang == "python":
        return _python_blocks_regex(code)
    if lang == "ruby":
        return _ruby_blocks(code)
    if lang == "go":
        return _brace_blocks(code, [(GO_TEST_RE, 1)])
    if lang == "javascript":
        return _brace_blocks(code, [(JS_TEST_RE, 0), (JS_FUNC_TEST_RE, 1)])
    if lang == "dart":
        return _brace_blocks(code, [(DART_TEST_RE, 0)])
    if lang == "php":
        return _brace_blocks(code, [(PHP_TEST_RE, 1)])
    if lang in ("java", "csharp"):
        return _jvm_blocks(code)
    return []


def _python_blocks_regex(code: list[str]) -> list[Block]:
    """Indentation-based fallback for Python, used when the file does not
    parse (a half-written generated test is exactly the case we must still
    report on)."""
    blocks: list[Block] = []
    for i, line in enumerate(code):
        m = PY_TEST_DEF_RE.match(line)
        if not m:
            continue
        indent = len(m.group(1))
        end = len(code) - 1
        for j in range(i + 1, len(code)):
            nxt = code[j]
            if not nxt.strip():
                continue
            if len(nxt) - len(nxt.lstrip()) <= indent:
                end = j - 1
                break
        blocks.append(Block(m.group(2), i + 1, i + 2, max(end + 1, i + 1)))
    return blocks


def python_blocks_ast(source: str) -> list[Block] | None:
    """Exact block boundaries from Python's own parser. Returns None if the
    file does not parse, so the caller can fall back to the regex path."""
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return None
    blocks: list[Block] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not node.name.startswith("test"):
            continue
        body = node.body
        start = body[0].lineno
        end = max(getattr(n, "end_lineno", n.lineno) or n.lineno for n in body)
        decl = min([node.lineno] + [d.lineno for d in node.decorator_list])
        blocks.append(Block(node.name, decl, start, end))
    blocks.sort(key=lambda b: b.decl)
    return blocks


def _brace_blocks(code: list[str], patterns: list[tuple[re.Pattern, int]]) -> list[Block]:
    blocks: list[Block] = []
    i = 0
    while i < len(code):
        name = None
        for pat, group in patterns:
            m = pat.search(code[i])
            if m:
                name = (m.group(group) if group and m.lastindex else None) or f"line {i+1}"
                break
        if name is None:
            i += 1
            continue
        end = _brace_end(code, i)
        blocks.append(_make_brace_block(code, name, i, end, decl=i + 1))
        i = max(end, i + 1)
    return blocks


def _make_brace_block(code, name, i, end, decl):
    """A one-line test keeps its body on the declaration line, so `start`
    stays there and `head` records where the braces open."""
    if end <= i + 1:
        brace = code[i].find("{")
        return Block(name, decl, i + 1, i + 1, head=brace + 1 if brace >= 0 else 0)
    return Block(name, decl, i + 2, max(end, i + 1))


def _brace_end(code: list[str], start: int) -> int:
    """1-based line of the closing brace of the block opening at/after `start`."""
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


def _jvm_blocks(code: list[str]) -> list[Block]:
    """Java/Kotlin/C#: a method is a test if it carries a test annotation or
    its name says so. Annotation may sit up to three lines above the method,
    which is where other annotations (@DisplayName, @Timeout) usually live."""
    blocks: list[Block] = []
    i = 0
    while i < len(code):
        line = code[i]
        m = JVM_METHOD_RE.search(line)
        if not m:
            # C# convention puts the opening brace on its own line.
            m = JVM_METHOD_NL_RE.search(line)
            nxt = next((code[k] for k in range(i + 1, min(i + 3, len(code)))
                        if code[k].strip()), "")
            if not m or not nxt.strip().startswith("{"):
                i += 1
                continue
        name = m.group(1)
        annotated = any(JVM_ANNOTATION_RE.search(code[k])
                        for k in range(max(0, i - 3), i + 1))
        looks_like_test = name.lower().startswith("test") or name.endswith("Test")
        if not (annotated or looks_like_test):
            i += 1
            continue
        decl = i + 1
        for k in range(max(0, i - 3), i):
            if JVM_ANNOTATION_RE.search(code[k]):
                decl = k + 1
                break
        end = _brace_end(code, i)
        blocks.append(_make_brace_block(code, name, i, end, decl=decl))
        i = max(end, i + 1)
    return blocks


def _ruby_blocks(code: list[str]) -> list[Block]:
    blocks: list[Block] = []
    i = 0
    while i < len(code):
        m = RUBY_TEST_RE.match(code[i])
        if not m:
            i += 1
            continue
        indent = len(code[i]) - len(code[i].lstrip())
        end = len(code)
        for j in range(i + 1, len(code)):
            nxt = code[j]
            if not nxt.strip():
                continue
            if (len(nxt) - len(nxt.lstrip())) == indent and nxt.strip() == "end":
                end = j + 1
                break
        blocks.append(Block(m.group(1) or f"line {i+1}", i + 1, i + 2, max(end, i + 1)))
        i = max(end, i + 1)
    return blocks


# --------------------------------------------------------------------------
# analysis
# --------------------------------------------------------------------------

class FileScan:
    def __init__(self, path: str, lang: str, raw: list[str], use_ast: bool = True):
        self.path = path
        self.lang = lang
        self.raw = raw
        self.code, self.strings, self.comments = scrub(raw, lang)
        self.has_seed = any(SEED_RE.search(c) for c in self.code)
        self.ast_used = False
        blocks = None
        if lang == "python" and use_ast:
            blocks = python_blocks_ast("".join(raw))
            self.ast_used = blocks is not None
        self.blocks = blocks if blocks is not None else find_blocks(self.code, lang)
        self.findings: list[dict] = []
        self.suppressed = 0

    # -- helpers ---------------------------------------------------------
    def add(self, rule: str, line: int, severity: str | None = None) -> None:
        spec = RULES[rule]
        raw = self.raw[line - 1] if 0 < line <= len(self.raw) else ""

        # An author who marked this line deliberate gets the last word.
        silenced = suppressed_rules(raw)
        if silenced is not None and (not silenced or rule in silenced):
            self.suppressed += 1
            return

        snippet = raw.strip()
        if len(snippet) > 100:
            snippet = snippet[:97] + "..."
        finding = {
            "file": self.path,
            "line": line,
            "category": spec["category"],
            "rule": rule,
            "severity": severity or spec["severity"],
            "snippet": snippet,
            "why": spec["why"],
            "fix": spec["fix"],
        }
        note = LANG_NOTES.get((self.lang, spec["family"]))
        if note:
            finding["note"] = note
        self.findings.append(finding)

    def block_line(self, block: Block, n: int) -> str:
        """The code on line `n` that belongs to `block` -- which for a
        one-line test excludes the `it('name', () => {` preamble."""
        s = self.code[n - 1]
        if n == block.start and block.head:
            s = s[block.head:]
        return s

    def body(self, block: Block) -> list[int]:
        """1-based body line numbers that carry code."""
        return [n for n in range(block.start, min(block.end, len(self.code)) + 1)
                if self.block_line(block, n).strip()]

    def is_assertion(self, line: int) -> bool:
        return bool(ASSERT_RE.search(self.code[line - 1]))


def analyse_file(path: str, raw: list[str], lang: str, use_ast: bool = True) -> FileScan:
    scan = FileScan(path, lang, raw, use_ast=use_ast)
    _flake_rules(scan)
    _meaningless_rules(scan)
    scan.findings.sort(key=lambda f: (f["line"], f["rule"]))
    return scan


def _flake_rules(scan: FileScan) -> None:
    code = scan.code

    # -- unseeded randomness --------------------------------------------
    # File-level suppression: one seed call disarms the whole file, because
    # seeding is done once in setUp and we would otherwise fire on every draw.
    # The seed-immune sources are checked first and outside that suppression:
    # random.seed() does not reach them, so a seeded file is not evidence.
    for i, line in enumerate(code, 1):
        if UNSEEDABLE_RANDOM_RE.search(line):
            scan.add("seed-immune-random", i)
        elif not scan.has_seed and RANDOM_RE.search(line):
            scan.add("unseeded-random", i)

    # -- real clock -------------------------------------------------------
    # Fires when the clock is read on an assertion line, or when a variable is
    # assigned from the clock and that variable is later asserted on. The
    # second pass is what catches the common `expected = datetime.now()` shape.
    # Misses: a clock read passed through two intermediate variables.
    assertion_lines = [i for i in range(1, len(code) + 1) if ASSERT_RE.search(code[i - 1])]
    assertion_text = " ".join(code[i - 1] for i in assertion_lines)
    for i, line in enumerate(code, 1):
        if not CLOCK_RE.search(line):
            continue
        if ASSERT_RE.search(line):
            scan.add("real-clock", i)
            continue
        m = re.match(r"\s*(?:(?:const|let|var|final|val|auto)\s+)?([A-Za-z_]\w*)\s*"
                     r"(?::[^=]+)?=", line)
        if m and re.search(r"\b%s\b" % re.escape(m.group(1)), assertion_text):
            scan.add("real-clock", i)

    # -- naive local-time conversion ---------------------------------------
    # Same tiering as real-clock above: an assertion that depends on the
    # machine's timezone fails on every run in the wrong zone, so it blocks;
    # a conversion merely assigned to a variable that is asserted on later is
    # the weaker signal and only warns. Misses, deliberately: a conversion
    # that is never named or asserted on, which cannot change a verdict.
    for i, line in enumerate(code, 1):
        if not LOCAL_TIME_RE.search(line) or LOCALE_SAFE_RE.search(line):
            continue
        if ASSERT_RE.search(line):
            scan.add("naive-local-time", i, severity="block")
            continue
        m = re.match(r"\s*(?:(?:const|let|var|final|val|auto)\s+)?([A-Za-z_]\w*)\s*"
                     r"(?::[^=]+)?=", line)
        if m and re.search(r"\b%s\b" % re.escape(m.group(1)), assertion_text):
            scan.add("naive-local-time", i)

    # -- sleeps / fixed timeouts -----------------------------------------
    for i, line in enumerate(code, 1):
        if SLEEP_RE.search(line):
            scan.add("sleep", i)

    # -- real network ------------------------------------------------------
    for i, line in enumerate(code, 1):
        if NETWORK_RE.search(line):
            scan.add("real-network", i)
            continue
        # URLs live in string literals, which every other rule deliberately
        # cannot see, so this one reads them directly.
        if NAMESPACE_RE.search(scan.raw[i - 1]):
            continue
        for s in scan.strings[i - 1]:
            # The whole string has to BE the URL. A URL mentioned inside a
            # larger string ("API_URL=https://...", a README fixture, a shell
            # command) is test data, not a request this test performs.
            bare = s.strip().rstrip("/").replace("\\n", "").replace("\\r", "")
            if not BARE_URL_RE.match(bare):
                continue
            host = URL_RE.match(bare).group(1)
            if not LOCAL_HOST_RE.match(host):
                # Weaker evidence than NETWORK_RE above, which saw a real
                # call. A bare URL literal may be a request this test makes,
                # or it may be test data -- a tool that scans configs for
                # production URLs has whole fixtures of them. Statically the
                # two are indistinguishable, so this is a warning, not a
                # block. Blocking a commit on an ambiguous signal is how a
                # gate gets switched off.
                scan.add("real-network", i, severity="warn")
                break

    # -- filesystem order ---------------------------------------------------
    for i, line in enumerate(code, 1):
        if FS_ORDER_RE.search(line) and not SORTED_RE.search(line):
            scan.add("fs-order", i)

    # -- iteration order ---------------------------------------------------
    for i, line in enumerate(code, 1):
        if not ASSERT_RE.search(line) or SORTED_RE.search(line):
            continue
        # `[` only counts when it opens a literal. In `s["by_rule"].values()`
        # the bracket is a subscript, and treating it as an expected list made
        # the rule fire on perfectly ordinary dictionary access.
        if ITERATION_ORDER_RE.search(line) and LIST_LITERAL_RE.search(line):
            scan.add("iteration-order", i)

    # -- float equality ----------------------------------------------------
    for i, line in enumerate(code, 1):
        if not ASSERT_RE.search(line) or APPROX_RE.search(line):
            continue
        if _has_delta_argument(line):
            continue
        if not EXACT_EQ_RE.search(line):
            continue
        # Only decimals that binary floating point cannot hold exactly are a
        # hazard. 0.0, 0.5, 75.0 and 10.50 compare exactly every time, and
        # firing on them was the single biggest false-positive source when
        # this detector was first run over a real suite.
        # ...and only when it is one of the two sides. `cents(1.15) == 115`
        # compares an int against an int; the float is an input to the code
        # under test, and firing there reports an exact comparison as a
        # rounding bet.
        if _compares_inexact_float(line):
            scan.add("float-equality", i)

    # -- locale / timezone --------------------------------------------------
    for i, line in enumerate(code, 1):
        if LOCALE_RE.search(line) and not LOCALE_SAFE_RE.search(line):
            scan.add("locale-timezone", i)

    # -- shared mutable state ----------------------------------------------
    _shared_state(scan)


def _shared_state(scan: FileScan) -> None:
    """A mutable container declared outside any test and written inside one.

    Heuristic: we match the declaration shape (empty list/dict/set, or a
    non-final static collection) and then look for a mutating call on that
    name anywhere later in the file. It misses state hidden behind a factory
    call, and it cannot tell a deliberate module-level registry from an
    accidental one -- hence `info`, not `warn`.
    """
    declared: list[tuple[str, int]] = []
    for i, line in enumerate(scan.code, 1):
        if scan.lang == "python":
            m = MUTABLE_DECL_RE.match(line)          # column 0 == module level
            if m:
                declared.append((m.group("name"), i))
        elif scan.lang == "javascript":
            # Column 0 only: an indented `let rows = []` is inside a describe
            # or a beforeEach and is therefore re-created per test.
            m = JS_MUTABLE_DECL_RE.match(line)
            if m:
                declared.append((m.group("name"), i))
        else:
            m = STATIC_MUTABLE_RE.search(line)
            if m:
                declared.append((m.group("name"), i))

    if not declared:
        return
    in_test = set()
    for b in scan.blocks:
        in_test.update(range(b.start, b.end + 1))
    for name, decl_line in declared:
        pat = re.compile(MUTATE_TMPL.format(name=re.escape(name)))
        for i in sorted(in_test):
            if i <= len(scan.code) and pat.search(scan.code[i - 1]):
                scan.add("shared-mutable-state", decl_line)
                break


def _meaningless_rules(scan: FileScan) -> None:
    code = scan.code

    # .only and skip are file-level: `.only` anywhere disables the rest of the
    # file whether or not we resolved its block.
    for i, line in enumerate(code, 1):
        if FOCUS_RE.search(line):
            scan.add("focused-test", i)
        elif SKIP_RE.search(line):
            scan.add("skipped-test", i)

    for block in scan.blocks:
        body = scan.body(block)
        meaningful = [n for n in body
                      if not _is_noop(scan.block_line(block, n), scan.lang)]

        # TODO scaffolds are read from comments and strings, which are the only
        # place they ever live.
        todo_line = None
        # Python's ast ends a function at its last statement, so a trailing
        # `# TODO: add assertion` -- the exact shape we care about most -- sits
        # one line past the body. Extend over the trailing blank/comment run.
        for n in range(block.decl, _todo_scan_end(scan, block) + 1):
            # Comments only, never string literals. A TODO marker lives in a
            # comment; a TODO inside a string is data -- a fixture, an error
            # message, or a test about TODO markers. Searching literals made
            # this rule fire on its own test suite, and a `block` severity
            # rule that cries wolf gets the whole tool switched off.
            #
            # Known limitation, accepted deliberately: a TODO written in a
            # docstring rather than a comment is missed. Distinguishing a
            # docstring from any other literal near the top of a block needs
            # real parsing; a proximity heuristic re-admits exactly the false
            # positives above. A missed marker costs one finding, a false one
            # costs the tool.
            haystack = scan.comments[n - 1]
            if TODO_RE.search(haystack) or NOT_IMPLEMENTED_RE.search(code[n - 1]):
                todo_line = n
                break
        if todo_line:
            scan.add("todo-assertion", todo_line)

        if not meaningful:
            # An empty body and a TODO stub are the same defect; report the
            # more specific one only.
            if not todo_line:
                scan.add("empty-test", block.decl)
            continue

        asserts = [n for n in body
                   if ASSERT_RE.search(scan.block_line(block, n))]
        if not asserts:
            if not todo_line:
                scan.add("no-assertion", block.decl)
            continue

        taut = [n for n in asserts
                if _is_tautology(scan.block_line(block, n))]
        for n in taut:
            scan.add("tautological-assertion", n)

        if len(asserts) == 1 and not taut:
            n = asserts[0]
            if _mock_return_tautology(scan, block, n):
                scan.add("mock-return-tautology", n)
            elif WEAK_ORACLE_RE.search(scan.block_line(block, n)):
                scan.add("weak-oracle", n)
        elif asserts and not taut and all(
                WEAK_ORACLE_RE.search(scan.block_line(block, n)) for n in asserts):
            scan.add("weak-oracle", asserts[0])




def _todo_scan_end(scan: FileScan, block: Block) -> int:
    end = min(block.end, len(scan.raw))
    while end < len(scan.raw) and not scan.code[end].strip():
        # Only blank-or-comment lines are absorbed, so we never reach into the
        # next test and attribute its TODO to this one.
        if not scan.comments[end].strip() and not scan.raw[end].strip():
            break
        if not scan.comments[end].strip():
            break
        end += 1
    return end


def _is_noop(line: str, lang: str) -> bool:
    s = line.strip()
    return s in ("", "pass", "...", "{", "}", "};", "end", ";", "return;", "return",
                 "pass;", "Unit", "()", "});", "})", ");", ")", "}));")


def _mock_return_tautology(scan: FileScan, block: Block, assert_line: int) -> bool:
    """The sole assertion compares against a literal the test itself told a
    mock to return.

    Best-effort: we only recognise literals (numbers, booleans, strings), and
    we require the configured value to appear verbatim in the assertion. A
    mock returning an object, or an assertion that reformats the value, is
    missed. That is the right trade -- the alternative fires on every test
    that happens to reuse a constant.
    """
    assert_code = scan.code[assert_line - 1]
    assert_strings = scan.strings[assert_line - 1]
    for n in range(block.start, min(block.end, len(scan.code)) + 1):
        if n == assert_line:
            continue
        for m in MOCK_RETURN_RE.finditer(scan.code[n - 1]):
            val = next((v for v in m.groups() if v), "").strip().rstrip(",);")
            if not LITERAL_RE.match(val):
                continue
            if val == '""':
                # The literal was scrubbed; compare the real string contents.
                if any(s in assert_strings for s in scan.strings[n - 1]):
                    return True
                continue
            if re.search(r"(?<![\w.])%s(?![\w.])" % re.escape(val), assert_code):
                return True
    return False


# --------------------------------------------------------------------------
# driving
# --------------------------------------------------------------------------

def iter_paths(paths: list[str]):
    for p in paths:
        if os.path.isdir(p):
            for root, dirs, names in os.walk(p):
                dirs[:] = [d for d in dirs
                           if d not in ("node_modules", ".git", "venv", ".venv",
                                        "build", "dist", "target", "__pycache__")]
                for name in sorted(names):
                    yield os.path.join(root, name)
        else:
            yield p


# Several rules here are long alternations of `\s*`-separated fragments, which
# cost more than linearly in the length of a line. The input is a repository,
# which on a pull request is attacker-controlled, so an unbounded line is a
# denial of service: one 40,000-character line already costs seconds, and this
# gate runs in CI and on a reviewer's machine. No test assertion is 2,000
# characters wide -- a line that is, is minified or generated -- so scanning is
# capped there. Line numbers and the file's line count are unchanged, so every
# finding still points where it did.
#
# The cap is NOT silent. Until this was fixed the tail past 2,000 characters
# was dropped with nothing said, so a 1.9MB single-line test file -- under the
# file ceiling, therefore "scanned" -- came back files_scanned=1, findings=0,
# skipped_files=[]: a clean verdict about 1,898,000 characters nobody looked
# at, which is exactly the shape of the worst defect in this build. Measured
# before the fix: 0.002s and zero findings on that file. Every truncated line
# now appears in `truncated_lines`, is counted in summary.lines_truncated, is
# printed by render_text() and is warned about on stderr.
MAX_LINE_CHARS = 2000

# The line cap above bounds the work per line; this bounds the work per FILE,
# which nothing did before. read_lines() holds the bytes, the decoded str and
# the list of lines at once, so a file costs several times its own size in RAM.
# Measured on this machine: a 49MB generated test file took 45 seconds and
# peaked near 3GB RSS -- on a gate that is supposed to run before every suite.
# 2MB is the same number env_safety_check.py uses, and for the same reason: a
# test file larger than that is generated, vendored or minified, not written.
# Raise it with --max-file-bytes; anything over it is REPORTED as skipped,
# never silently dropped, because a skipped file reads exactly like a clean one.
MAX_FILE_BYTES = 2 * 1024 * 1024


def read_lines_bounded(path: str, *, max_bytes: int = MAX_FILE_BYTES,
                       max_line_chars: int = MAX_LINE_CHARS
                       ) -> tuple[list[str] | None, list[dict]]:
    """(lines, truncated) -- lines is None when the file is unreadable, binary
    or over `max_bytes`; `truncated` lists every line whose tail was NOT
    scanned.

    The second element is the whole point. A gate that shortens its own input
    and says nothing reports "nothing found" about text it never read, so the
    caller is given what it would have to lie about otherwise. A
    generated-test gate that crashes on one odd file blocks the whole
    pipeline, so this never raises.
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
                "scanned_chars": max_line_chars,
                "reason": (f"line is {len(line)} characters, over the "
                           f"{max_line_chars}-character line ceiling; only "
                           f"the first {max_line_chars} were analysed and "
                           "the rest was NOT examined"),
            })
            line = line[:max_line_chars]
        lines.append(line)
    return lines, truncated


def read_lines(path: str, *, max_bytes: int = MAX_FILE_BYTES) -> list[str] | None:
    """Back-compatible wrapper: lines only. Prefer read_lines_bounded(), which
    also hands back what it had to shorten."""
    return read_lines_bounded(path, max_bytes=max_bytes)[0]


def scan_paths(paths: list[str], use_ast: bool = True, *,
               max_bytes: int = MAX_FILE_BYTES,
               max_line_chars: int = MAX_LINE_CHARS) -> dict:
    files: list[dict] = []
    skipped: list[dict] = []
    truncated_lines: list[dict] = []
    by_rule: dict[str, int] = {}
    counts = {"block": 0, "warn": 0, "info": 0}
    categories = {"flake": 0, "meaningless": 0}

    for path in iter_paths(paths):
        lang = detect_language(path)
        if lang is None:
            continue
        if not is_test_file(path):
            # Not a test file. Silence here is the point: a sleep in product
            # code is not a smell.
            skipped.append({"path": path, "reason": "not a test file"})
            continue
        raw, cut = read_lines_bounded(path, max_bytes=max_bytes,
                                      max_line_chars=max_line_chars)
        truncated_lines.extend(cut)
        if raw is None:
            # Name the ceiling when that is what happened: "unreadable" and
            # "too big to look at" need different reactions from the reader.
            try:
                oversized = os.path.getsize(path) > max_bytes
            except OSError:
                oversized = False
            skipped.append({
                "path": path,
                "reason": (f"larger than {max_bytes} bytes "
                           f"({max_bytes // (1024 * 1024)}MB ceiling); "
                           "raise --max-file-bytes to scan it")
                if oversized else "unreadable or binary",
            })
            continue
        scan = analyse_file(path, raw, lang, use_ast=use_ast)
        for f in scan.findings:
            counts[f["severity"]] += 1
            categories[f["category"]] += 1
            by_rule[f["rule"]] = by_rule.get(f["rule"], 0) + 1
        files.append({
            "path": path,
            "language": lang,
            "parsed_with": "ast" if scan.ast_used else "regex",
            "findings": scan.findings,
        })

    total = sum(counts.values())
    # "not a test file" is a decision, not a failure. `skipped_files` carries
    # only the test files this gate was supposed to read and could not -- the
    # ones whose silence would otherwise be mistaken for a clean result.
    unread = [{"file": s["path"], "reason": s["reason"]} for s in skipped
              if s["reason"] != "not a test file"]
    # A truncated line is a *third* state, and it has to stay distinguishable
    # from both of the other two: the file was read (so it is not in
    # skipped_files) and it was scanned (so files_scanned counts it), but part
    # of it was not looked at. Reported per line, with the real character
    # count, so "nothing found here" can never be read as "nothing is here".
    return {
        "schema": SCHEMA,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "skipped_files": unread,
        "truncated_lines": truncated_lines,
        "files": files,
        "summary": {
            "files_scanned": len(files),
            "files_skipped": len(skipped),
            "skipped": skipped,
            "lines_truncated": len(truncated_lines),
            "files_with_truncated_lines": len({t["file"] for t in truncated_lines}),
            "findings": total,
            "by_severity": counts,
            "by_category": categories,
            "by_rule": dict(sorted(by_rule.items())),
            "clean_files": sum(1 for f in files if not f["findings"]),
        },
    }


def render_text(result: dict) -> str:
    """`path:line  RULE  why` and nothing else. The JSON carries the fix; the
    terminal output has to be skimmable or it will not be read."""
    out: list[str] = []
    for f in result["files"]:
        for finding in f["findings"]:
            why = FIRST_SENTENCE_RE.split(finding["why"])[0].strip()
            out.append(f"{finding['file']}:{finding['line']}  "
                       f"{finding['rule']}  {why}")
    s = result["summary"]
    out.append("")
    out.append(f"{s['findings']} findings in {s['files_scanned']} files "
               f"({s['by_severity']['block']} block, "
               f"{s['by_severity']['warn']} warn, "
               f"{s['by_severity']['info']} info)")
    # A test file nobody read is not a test file with no smells. The count
    # goes in the terminal output too, not only the JSON.
    unread = result.get("skipped_files") or []
    if unread:
        out.append(f"{len(unread)} test file(s) NOT scanned (see skipped_files):")
        for s in unread[:5]:
            out.append(f"  skipped {s['file']}: {s['reason']}")
        if len(unread) > 5:
            out.append(f"  ... and {len(unread) - 5} more")
    # Same reasoning one level down: a line that was shortened was partly not
    # read, and a count of zero findings on it says nothing about the tail.
    cut = result.get("truncated_lines") or []
    if cut:
        out.append(f"{len(cut)} line(s) only PARTLY scanned "
                   "(see truncated_lines):")
        for t in cut[:5]:
            out.append(f"  truncated {t['file']}:{t['line']}: "
                       f"{t['chars']} chars, first {t['scanned_chars']} "
                       "analysed, the rest NOT examined")
        if len(cut) > 5:
            out.append(f"  ... and {len(cut) - 5} more")
    return "\n".join(out)


def breaches(result: dict, fail_on: str) -> bool:
    counts = result["summary"]["by_severity"]
    threshold = SEVERITIES.index(fail_on)
    return any(counts[s] for s in SEVERITIES[:threshold + 1])


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="flake_smells.py",
        description="Static flake and meaningless-test detection (Tier 0 gate).",
    )
    ap.add_argument("paths", nargs="+", help="test files or directories")
    ap.add_argument("--out", help="write the report here instead of stdout")
    ap.add_argument("--fail-on", choices=SEVERITIES, default="block",
                    help="exit 1 when a finding at this severity or worse exists")
    ap.add_argument("--format", choices=("json", "text"), default="json")
    ap.add_argument("--max-file-bytes", type=int, default=MAX_FILE_BYTES,
                    help="skip test files larger than this "
                         f"(default {MAX_FILE_BYTES}, i.e. 2MB); skipped files "
                         "are reported in skipped_files, never ignored")
    ap.add_argument("--max-line-chars", type=int, default=MAX_LINE_CHARS,
                    help="analyse only the first N characters of a line "
                         f"(default {MAX_LINE_CHARS}); every line past it is "
                         "reported in truncated_lines, never silently cut")
    args = ap.parse_args(argv)

    missing = [p for p in args.paths if not os.path.exists(p)]
    if missing:
        print(f"error: no such path: {', '.join(missing)}", file=sys.stderr)
        return 2

    try:
        result = scan_paths(args.paths, max_bytes=args.max_file_bytes,
                            max_line_chars=args.max_line_chars)
        payload = (json.dumps(result, indent=2) if args.format == "json"
                   else render_text(result))
        if args.out:
            # Atomic: two agents writing .testing/flake-smells.json at once
            # must leave one whole report, not an interleaved one that still
            # parses.
            write_text_atomic(args.out, payload + "\n")
        else:
            print(payload)
        for s in result["skipped_files"]:
            print(f"warning: not scanned: {s['file']}: {s['reason']}",
                  file=sys.stderr)
        for t in result["truncated_lines"]:
            print(f"warning: only partly scanned: {t['file']}:{t['line']}: "
                  f"{t['reason']}", file=sys.stderr)
    except OSError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    return 1 if breaches(result, args.fail_on) else 0


if __name__ == "__main__":
    sys.exit(main())
