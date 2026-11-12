---
name: health
description: >
  Take a suite-health baseline before trusting any coverage number. Reports whether the
  suite can even run, whether it passes, how flaky it is, how slow it is, and how much of
  it is skipped or assertion-free. Returns GREEN, AMBER or RED. Use it before measuring
  coverage, when adding a repo to CI, or when a passing suite feels too good to be true.
---

# /testsquad:health

**Coverage from a red or flaky suite is a lie.** `/testsquad:diagnose` will not emit a
coverage number without a health result from the current commit, and this is the command
that produces it.

Your job is to find out what the suite actually is, not what it claims to be.

## Before you run anything

Check `.testing/env-safety.json` is present and not `UNSAFE` for this commit. If it is
missing, run the check first. If it is `UNSAFE`, stop and report — do not execute a
suite that may point at production.

Check `.testing/preferences.json` for install permission. **If dependencies are missing
and permission was not granted, do not install them.** Report `cannot execute` with the
exact command that would be needed, who it would affect (lockfile, globals, network),
and stop. Guessing here is how a tool gets uninstalled.

## What to report, per package

### 1. Can it even run?

Separate two things that look identical in a terminal and need completely different fixes:

| Observation | Classification | Roadmap item |
|---|---|---|
| Missing deps, bad config, absent env var, import error | **cannot execute** | Fix the harness |
| The runner starts and tests fail | **executes and fails** | Fix the tests or the code |

Conflating them produces a roadmap item nobody can act on.

### 2. Pass / fail

Counts plus the names of failing tests. Record them as a **baseline** in
`.testing/health-baseline.json` against the commit SHA, so a later run can tell *your*
breakage from breakage that was already here. Blaming someone for a pre-existing failure
costs credibility you will need later.

### 3. Flakiness

Re-run the suite N times (default 3, `--flake N` to raise it), in randomized order where
the runner supports it.

Report **order-dependent** separately from **timing** flake. They are different bugs with
different fixes, and the distribution differs sharply by language:

| Stack | Dominant causes |
|---|---|
| Java | async wait **45%**, concurrency 20%, order dependency 12% |
| Python | **order dependency 59%**, test-infrastructure 28%, async wait almost absent |
| JavaScript | concurrency 21%, async 20%, OS 18%, network 13%; order dependency nearly nonexistent |

Use the stack playbook's tooling: `pytest-randomly`, `go test -shuffle=on -race`, NonDex
for Java, Jest `--runInBand` as a diagnostic.

**State the limit honestly.** Detection power is `1 − (1−p)^N`. Three runs catch a 50%
flake most of the time, a 5% flake about 14% of the time, and a 0.1% flake essentially
never. You are measuring coarse flakiness, so say that rather than implying the suite is
stable.

### 4. Speed

Total duration and the slowest tests. A suite too slow to run is a suite nobody runs,
which makes it decorative regardless of what it covers.

### 5. Honesty

Count and list: skipped tests, `.only` / `fit` / `describe.only` (these silently disable
the rest of the suite — report them as serious), `xit`, empty bodies, and tests with no
assertion at all.

```
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/flake_smells.py" <test paths> --format text
```

**A suite that is green because 40% of it is skipped must be reported as such.** This is
the number that most often surprises people.

## The verdict

| Verdict | Meaning |
|---|---|
| **GREEN** | Coverage measured now is trustworthy |
| **AMBER** | Usable with stated caveats — name each one |
| **RED** | Coverage would be meaningless. **Fixing the suite is roadmap item #0** |

RED does not mean you produce nothing. Produce the full health report — it is the most
useful thing you can give someone whose suite is broken. You just withhold the coverage
number and say why.

## Output

Write `.testing/health-baseline.json`, stamped with the commit SHA, so the dashboard can
show whether the suite is getting slower or flakier over time.

Report in the house style: the verdict in one line, then what is wrong in order of
consequence, each with the command that demonstrates it. State consequences, not
taxonomy — *"the suite passes, but 40% of it never runs"*, not *"skip count: 112"*.
