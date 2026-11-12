# FAQ

The questions a skeptic asks. Numbers here come from the repo or from
[RESEARCH.md](RESEARCH.md); where there is no number, that is said instead of
filled in.

## Is this just prompts?

Partly, and it is worth knowing which part.

**Prompts:** the seven agents (`agents/*.md`) and the sixteen skills
(`skills/*/SKILL.md`). These are instructions to a model. They are as reliable
as the model following them, which is why none of the project's claims rest on
them alone.

**Executable Python:** `scripts/` is ~5,600 lines of standard-library Python —
environment safety (`env_safety_check.py`), the churn-based risk map
(`risk_map.py`), flake and weak-test smells (`flake_smells.py`), coverage
normalization (`normalize_coverage.py`), the session preamble, the PR comment
and the plugin validator. It has 484 unit tests of its own
(`python3 -m unittest discover -s tests`), checked by mutation — a rule that
still passes when its own condition is negated proves nothing.

The division of labour is the design: anything a verdict depends on — can the
suite run, can this test fail, is this environment safe — is executed, not
reasoned about. An LLM judging test quality is near chance (40–46% accuracy
classifying assertions), so the gatekeeper's verdict rests on re-running
commands, never on its reading.

## Will it edit my code?

No. It writes tests, and it writes files under `.testing/`, `.claude/agents/`
and one marked block in `CLAUDE.md`. Bugs in your product are reported with a
reproducing command and never silently fixed. Work lands on a
`testsquad/<task>` branch; it does not push, merge or deploy.

There is an opt-in hook (`hooks/examples/pre-tool-use-protect-product-code.json`)
that blocks edits to non-test files while a testing task is in flight, for the
specific failure mode of an agent that cannot make a test pass quietly editing
the product until it does.

## Will it run my tests against production?

It checks first, and it stops. `scripts/env_safety_check.py` runs before any
test command, coverage run or install — step 3 of `/testsquad:start`, re-checked
by `/testsquad:health`. It looks for live-looking credentials, production
hostnames and credential-shaped keys with high-entropy values. **UNSAFE** stops
the run and asks whether to continue with execution disabled or abort, showing
file, line, key name and a redacted preview, never the value. **CAUTION**
proceeds but names what it is proceeding past. **SAFE** continues.

You can check the check: `examples/fixtures/node-ts/.env` is a fabricated
production file that must return UNSAFE, and `python-pkg/.env.test` must return
SAFE. Both directions are measured, because a detector that fires on everything
is as useless as one that fires on nothing.

## How much will it cost me in tokens?

The plugin's own footprint, measured from the files:

| | Tokens |
|---|---|
| Session preamble, every request in a repo with `.testing/` | **~150** (578 chars measured; a test caps it at 1,500 chars) |
| A `/testsquad:test` run | **~6,000** — skill ~1,250, report rules ~790, plus 2–4 agents at ~2,100–3,200 each |
| Any single agent / skill | ≤3,500 / ≤3,000, enforced by `scripts/validate_plugin.py` |

No `.testing/` directory, no preamble: the hook exits silently on a repo that
has never run `/testsquad:start`.

The orchestrator picks **2–4 agents**, not all seven, and that is a cost
decision with numbers behind it: cost bottoms out around 4–6 subagents and rises
after; a four-agent system can burn ten times a single agent; past roughly six
hand-offs you pay about four times the cost for half the success rate.

The honest caveat: the dominant cost of a real run is usually not the plugin. It
is your own suite, which `/testsquad:health` runs three times by default, plus
the model time the specialists spend. Neither is something this repo can put a
dollar figure on for your codebase, so it does not.

## Why doesn't it just report coverage?

Because coverage answers a narrower question than people read it as.
Inozemtseva & Holmes (ICSE 2014, 31,000 suites across 5 Java systems) found that
once suite *size* is controlled for, the correlation between coverage and
effectiveness drops to low-to-moderate; their conclusion was that coverage
"should not be used as a quality target."

The asymmetry this tool relies on: **uncovered code is definitely untested — a
strong signal. Covered code may still be unverified — a weak one.** So coverage
is a floor detector, and the headline is *which high-risk code is untested*, not
a percentage. No coverage number is emitted at all under a RED health verdict.

## What will it get wrong?

The documented limitations, specifically.

**Go mutation testing is effectively unavailable.** `go-mutesting`'s last commit
was 2021; `gremlins` has been semi-dormant since 2026-03-30. The Go playbook
says so rather than recommending something unmaintained. If you are on Go, the
"prove this test can fail" step is weaker than it is on JS/TS, Python, C#, PHP,
Rust or Java.

**A `TODO` written in a docstring is missed.** `flake_smells.py` catches
`TODO: add assertion` scaffolds in comments, not in docstrings — distinguishing
a docstring from any other string literal near the top of a block needs a
parser, and this is a line scanner. Accepted deliberately
(`scripts/flake_smells.py:1166`).

**The secret-entropy heuristic has false positives.** A value of 20+ characters
with two character classes and entropy ≥3.0 bits, assigned to a credential-shaped
key, is flagged. A long hex test fixture does this. Verified:
`TEST_SIGNATURE_SECRET=A3f9B2c1...` (40 hex chars) in a `.env.test` returns
CAUTION / HIGH. The verdict is CAUTION, not UNSAFE, so it does not block — but
you will see it.

**Diff-scoped mutation is a ratchet on new code, not an audit.** It cannot see a
change in file A destroying coverage of file B; it cannot see deleted assertions
in test files, because a test-only change runs no mutants at all; and it cannot
see a dependency, config or fixture change flipping mutant status. The
mitigation is an occasional full run off the critical path, which costs real
time.

**LLM output varies between identical runs.** This is why the eval suite
(`evals/`) runs every behavioural case **5 times and publishes a rate, not a
pass**. And the honest gap as of v0.1.0: `evals/SCORECARD.md` is still a
template. The claims are written down and falsifiable; they have not been
measured and published yet. Treat the README's claims as design intent until
that file has numbers in it.

## How do I know it isn't making things up?

Three mechanisms, none of which depends on a model being careful.

**Every finding carries the command that reproduces it.** Not a description of
how it was found — the literal, copy-pasteable command and its expected
observable. A test accepted by the gatekeeper has also been shown red under
fail-proof: negate the line under test, the test must fail.

**Anything unreproduced is quarantined and uncounted.** Code-read findings (📖)
and suspicions (💭) go to a collapsed appendix with the reason they did not
qualify, and the bug total is `count(✅) + count(👁)` and nothing else. At the
`quick` depth the report must state that it searched ~2 minutes per finding and
name what it could not reproduce in that budget.

**Non-obvious design decisions are sourced.** [RESEARCH.md](RESEARCH.md) carries
the citation for each, records both sides where the evidence is contested, and
marks vendor-published numbers as such.

The one thing *not* verified by machine: whether a reported bug matters to your
business. Impact is asked of you during `/testsquad:start` and never inferred.
Risk is kept as `Impact × Likelihood` with the two sides separate, because
multiplying two invented ordinals produces a number that looks precise and that
nobody can argue with.

## Can I turn bits off?

Yes. **One hook ships enabled**: `SessionStart`, which reads a few files in
`.testing/` and prints the ~150-token summary. The three that get in your way —
run related tests on every edit, gate every turn end on the QA gatekeeper, block
product-code edits during a task — live in `hooks/examples/` and are opt-in,
each documented with what it costs and when it will annoy you. Remove any with
`/hooks`; nothing in `.testing/` depends on them.

`/testsquad:preferences` shows everything remembered — depth, install
permission, standing declines, thresholds, promoted lessons — with the date each
was given and when it expires, and lets you withdraw any of it. Permissions
expire after 30 days. A "no" is recorded as durably as a "yes" and is not
re-asked or worked around with a narrower phrasing.

`/testsquad:depth quick` reduces how hard it looks for things. It does not
reduce what is verified: the same task at `quick` and at `exhaustive` must
produce the same QA verdict. An agent that reads the depth setting to skip a
verification step is a bug in this plugin, and worth reporting as one.

## Does it work on a monorepo?

Yes, with explicit scoping. Packages are ranked by churn, the top N are
processed, and **the rest are listed as not-yet-analyzed rather than silently
omitted** — so the report tells you what it did not look at. Estimated time and
token cost are shown before any long stage. `--scope <paths>` narrows the run,
and `/testsquad:start packages/api packages/web` targets specific roots. Later
runs are incremental and reuse cached results for packages whose files have not
changed.

`/testsquad:start` is idempotent: the `CLAUDE.md` block is replaced rather than
duplicated, agents are regenerated, and learned knowledge in `.testing/` is
preserved.
