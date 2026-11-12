---
name: diagnose
description: >
  Measure coverage, map the test types that exist, and name the high-risk code nobody
  tests. Gated on a suite-health result for the current commit — without one it refuses
  to emit a coverage number and says why. Use it after `/testsquad:health`, before
  planning work, or when someone asks "how well tested are we?". It never reports
  coverage as a headline score and never runs mutation testing on its own.
---

# /testsquad:diagnose

The question you are answering is **not** "what is our coverage?" It is **"which code
that matters is not tested?"** Those produce different reports, and only the second one
changes what anyone does on Monday.

## The gate — read this before running anything

**No health result for the current commit means no coverage number.** Not a cached one,
not an estimate, not "roughly".

```bash
git rev-parse HEAD
# compare against the commit_sha in .testing/health-baseline.json
```

| State of `.testing/health-baseline.json` | What you do |
|---|---|
| Missing | Stop. Say: *"I cannot give you a coverage number yet — the suite has never been measured. Run `/testsquad:health` first; it takes one suite run."* Then offer to run it. |
| Present, different commit | Stop. Name the commit it was taken at and how many commits behind that is. Offer to re-run health. |
| Present, current commit, verdict RED | Run coverage **only** if the user asks explicitly, and label every figure `measured under RED — the suite does not pass, so these lines were not all verified`. Lead the report with the health problem, not the percentage. |
| Present, current commit, AMBER or GREEN | Proceed, labelling every figure with the verdict. |

Guessing is the failure mode this gate exists to prevent. A coverage number from a suite
with 112 skipped tests is a measurement of what the runner executed, not of what is
verified, and once it is written down nobody remembers the caveat.

Also check `.testing/env-safety.json` for this commit. `UNSAFE` means you execute
nothing. If dependencies are missing and `.testing/preferences.json` does not grant
install permission, report `cannot execute` with the exact command that would be needed,
and stop. Do not install.

## Step 1 — Run the stack's own coverage command

Take the command from `.testing/profile.json`. It must be one you observed in the repo's
config, never one you composed from memory. Then normalize it:

```bash
<coverage command from profile.json>
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/normalize_coverage.py" "<report file>" \
  --strip-prefix "$(pwd)/" --out .testing/coverage-raw.json
```

The script is standard-library-only and reads lcov, cobertura, jacoco, `coverage.out`
and `coverage.py` JSON. It keeps lines and branches as separate counters on purpose:
conflating them is how a suite claims 90% while every branch is untested. Report both,
and when they diverge, say which one you are talking about.

If the command fails, that is a finding, not a gap in your report. A coverage tool that
no longer runs is usually the reason coverage stopped being watched.

## Step 2 — The test-type matrix

Count what exists, by type, from the profile's classification:

| Type | Files | Tests | What it currently protects |
|---|---|---|---|
| Unit | | | |
| Integration | | | |
| Contract | | | |
| E2E | | | |

Do **not** compare this against the pyramid, the trophy or the honeycomb. The right
distribution is derived per repo, not imposed: *Software Engineering at Google* offers
roughly 80% small / 15% medium / 5% large explicitly as a starting heuristic to adjust
from, and Google's own writing on the subject is titled "Pyramid or Crab?". The useful
observation is a structural one — *"every test in this repo goes through HTTP, so a
logic bug takes four minutes to localize"* — not a shape score.

## Step 3 — Cross with the risk map. This is the actual report

Join `.testing/coverage-raw.json` against `.testing/risk-map.json` and
`.testing/acceptance-contract.md`. Rank the result by **impact first** (the contract's
tier), then by likelihood (churn and time-weighted fix recency). Never blend the two
into one score — Bach's objection holds: multiplying two invented ordinals yields fake
precision people stop arguing with.

The output is a list of **named, untested, high-risk things**:

```
⚪ Code-read only  `src/billing/proration.ts` — 0% branch coverage
   Tier: critical (contract §2). 9 bug-fix commits in the last 90 days.
   Nothing tests the mid-cycle downgrade path.
   npx jest --coverage --collectCoverageFrom='src/billing/proration.ts'
   # Branches: 0/14
```

If `risk-map.json` carries `warnings` or is empty, say so plainly. **An empty risk map
means no data, not no risk** — a shallow clone or a squashed history produces one, and
reporting it as "nothing risky found" is a lie by omission.

## Step 4 — Mutation: available, not run

Mutation testing is on demand only. It appears in this report as one column, with the
command, and nothing more:

| Area | Line cov | Branch cov | Mutation |
|---|---|---|---|
| `src/billing/` | 71% | 44% | not run — `/testsquad:mutate src/billing` |

Running a mutation sweep inside a diagnostic would turn a two-minute command into a
forty-minute one, and the result would be a project-wide score — the exact number §15 of
the research says never to gate on.

## What you may and may not claim

Coverage is a **floor detector**. Inozemtseva & Holmes (ICSE 2014, 31,000 suites across
5 Java systems) found that once suite *size* is controlled for, the correlation between
coverage and effectiveness drops to low-to-moderate; their own conclusion is that
coverage "should not be used as a quality target".

So the asymmetry you are allowed to rely on, and the only one:

- **Uncovered code is definitely untested.** Strong signal. Say it flatly.
- **Covered code may still be unverified.** Weak signal. Never say "this is tested"
  because a line was executed — executed is not asserted.

Where the distinction matters, `/testsquad:mutate` is how you turn the weak signal into
a strong one, on the path that matters, on demand.

## Output

Write `.testing/coverage-state.md` and **`.testing/coverage-diff.json`**, then update the
dashboard via `/testsquad:report`.

`coverage-diff.json` is the only source for the PR comment's coverage section, and it is
scoped to the **changed lines**, never the whole repo — a project-wide figure under a
heading that says "the lines this PR changed" is a wrong number, not a spare one:

```json
{"line_pct": 83.0, "base_line_pct": 71.0, "branch_pct": 60.0,
 "base_branch_pct": null, "uncovered_changed_lines": 4}
```

Derive it from `coverage-raw.json`: take its `files[]` entries whose `path` the diff
touched, and intersect each one's `uncovered_lines` with the lines the diff added or
changed. **Any figure you did not measure is `null`, never `0`** — the renderer prints
`null` as "not measured" and omits the delta, which is the honest output. The `base_*`
fields need coverage measured at the base commit; a single `/testsquad:diagnose` run has
only one measurement, so leave them `null` unless you were given a base to compare
against. With no diff scope at all, do not write the file: a missing input is reported
by name, and that is better than a mislabelled one.

`coverage-state.md` and the dashboard are bound by
`${CLAUDE_PLUGIN_ROOT}/templates/report/RULES.md`: no
coverage percentage appears above the Findings section, every number carries the literal
command that reproduces it, and every figure is stamped with the health verdict and
commit it was measured under.

The verdict line names code, not a percentage:

```
VERDICT: Billing's proration path is untested — cover it before the next pricing change.
```
