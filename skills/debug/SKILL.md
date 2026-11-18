---
name: debug
description: >
  Drive a symptom — a log line, a stack trace, a failing test, a red CI job — to a root
  cause that is reproduced rather than argued, and hand back a failing test that proves
  it. Thin wrapper around the `ts-debugger` agent: it sets the budget, supplies the
  context and enforces the hand-over. Use it when something is broken and nobody knows
  why, or when a test fails intermittently and someone is about to call it flaky. It
  never fixes product code and never reports a hypothesis as a finding.
---

# /testsquad:debug \<symptom|log|test\> [--deep]

You are orchestration, not investigation. The investigation belongs to `ts-debugger`,
which is built for it. Your job is to hand that agent a well-formed problem, a budget it
cannot quietly exceed, and a definition of done it cannot talk its way around.

`ts-debugger` exists only after `/testsquad:start` has run here. **If it is absent,
dispatch the plugin's `debugger` agent** and say in your report that you did — it carries
the same method but none of this repo's commands.

## Before dispatch

Check `.testing/env-safety.json` for this commit — `UNSAFE` means nothing runs, and a
debugging session is exactly when someone is tempted to point a suite at a real database
to "see what happens". Check install permission in `.testing/preferences.json`; missing
dependencies without permission is a `cannot execute` report with the exact command, not
an install.

Read `.testing/known-issues.md` first. If this symptom is already recorded, say so and
show what was found last time before spending the budget again.

## The budget

Depth comes from `.testing/preferences.json` and governs **reproduction effort per
finding**, nothing else:

| Depth | Repro effort | Strategies tried |
|---|---|---|
| quick | ~2 min | 1 |
| standard | ~10 min | 3 |
| deep | ~45 min | all, including `git bisect` |
| exhaustive | until reproduced or genuinely exhausted | all |

`--deep` lifts the budget to deep **for this one item**, and does not change the stored
preference. Use it when the user already knows this one is worth the time.

When a budget expires, the result is *"could not reproduce in 10 minutes; here is what I
tried and what I would do next"*. That is a legitimate and useful outcome. Dressing it
up as a conclusion is not, and a wrong repro delays a fix **more than a missing one**
(Bettenburg et al., TSE 2010, 466 developers) because the developer spends their time
disproving you.

## The sequence

1. **Capture.** The exact symptom, verbatim. The command, the environment, the commit,
   the failing output as text — never paraphrased.
2. **Reproduce**, under the budget. Reproduction is the entry fee; everything after it
   is only worth doing once this succeeds.
3. **Shrink.** Smallest input, fewest steps, narrowest scope that still fails. A repro
   that needs the whole app is a repro nobody will run.
4. **Bisect** where history helps — `git bisect run` with the shrunk repro as the test.
   Budget permitting.
5. **Prove the root cause.** The proof is **making red and green switch places on
   demand**: change the suspected cause, the repro passes; revert it, the repro fails.
   An explanation that was never flipped is a hypothesis wearing a conclusion's clothes.
6. **Classify** (see the matrix below).
7. **Hand over a failing test.** Not a patch. `ts-debugger` has `Write` for a test, a
   repro script or a note under `.testing/`, and does not edit the source it is
   diagnosing — a diagnosis and a fix from the same agent get reviewed as one act of
   faith, while separated each can be checked.

## Flake versus real bug — the discrimination matrix

This is the step people skip, and skipping it is how bugs get lost. Run the test in
isolation, in the suite, repeatedly, and under load, then read the pattern:

| Observation | Classification | What it means |
|---|---|---|
| Fails in the suite, **passes in isolation** | **Order dependence** | Shared state leaked from an earlier test. Find the leak, not the timeout |
| Fails in isolation **non-deterministically**, worse under CPU contention | **Timing** | Async wait, race, or a fixed `sleep` standing in for a condition |
| **Fails deterministically in isolation** | **A real bug.** Not a flake | Reproduces every time on its own — there is nothing intermittent here |
| Passes locally, **fails only on CI** | **Environment** | Timezone, locale, hash seed, file ordering, clock, container resources, a missing env var |

The distribution differs sharply by language, which tells you where to look first: Java
is async-wait **45%** / concurrency 20% / order dependence 12%; Python is **order
dependence 59%** / test-infrastructure 28% with async wait almost absent; JavaScript is
concurrency 21% / async 20% / OS 18% / network 13%, with order dependence nearly
nonexistent.

**Calling a real bug "flaky" loses the bug.** That is the whole reason this matrix is
here. The label ends the investigation, the test gets retried or quarantined, and the
defect ships. Google traced newly-flaky stable tests back to cause and found **a real
production bug about 1 in 6 times (~17%)** — so "intermittent" is never by itself a
reason to stop. In the other direction, **84% of pass→fail transitions at Google are
flakes rather than real bugs**, so a single red run is a lead, not a conclusion: a PASS
is strong evidence, a FAIL is weak evidence.

Use the three-way retry rather than N identical reruns. GitHub's 18x reduction in flaky
builds came from splitting retries by cause class — same process, simulated time shift,
different host — which caught **~90%** of flaky failures where naive same-condition
retries caught **25%**. Same budget, and it names the cause instead of just flagging it.

Reruns alone cannot settle this and the arithmetic says so: detection power is
`1 − (1−p)ⁿ`, so ten runs catch a 10% flake 65% of the time and a 0.1% flake 1% of the
time. Where coverage data exists, DeFlaker's differential-coverage check is free and
needs no reruns at all — if a test fails but its coverage does not intersect the changed
code, the test is at fault, not the code (1,874 flaky tests across 26 projects at a 1.5%
false-alarm rate).

## Output

One finding per root cause, with three symptoms listed under it rather than three
findings. Per `${CLAUDE_PLUGIN_ROOT}/templates/report/RULES.md`:

- A symptom headline — *"Visit sync sometimes reports success without saving — 3 runs in
  10"*, never *"flaky: 3/10 runs fail in VisitSyncSpec"*.
- Exactly one evidence badge. **Only ✅ reproduced and 👁 observed reach the main list**;
  code-read and suspicion go to the collapsed appendix with the reason they did not
  qualify, and are **never counted in the bug total**.
- Trigger, cause and symptom named separately. A report that stops at the trigger gets
  fixed at the trigger, and the bug comes back.
- Severity computed with its inputs shown; priority marked *proposed*.
- The failing test, committed to a `testsquad/<symptom>` branch. Never pushed.

Anything unresolved goes to `.testing/known-issues.md`, dated, with a review date and
what was already tried — so the next person starts where you stopped.

Then write the findings to **`.testing/findings.json`**, the only input the PR comment
has for its findings section. Without it the comment renders no findings at all, and
reads exactly like a run that found nothing:

```json
{"findings": [{"severity": "S2", "title": "Visit sync reports success without saving",
               "file": "src/sync/visit.ts", "line": 88, "evidence": "reproduced",
               "count": 3, "repro": "npx jest visit.spec.ts -t 'offline save'"}]}
```

One object per **root cause**, matching the main list above — `count` is how many
symptoms clustered under it, not a second finding. `evidence` is `reproduced` or
`observed`; anything weaker is written out with that badge and the renderer withholds it
by count, which is the behaviour you want — do not drop it silently and do not promote it.
`repro` is a command, so it goes through the same redaction as everything else.

If the session ran and nothing qualified — nothing reproduced, nothing observed — still
write the file **with the key and an empty list: `{"findings": []}`**. Never `{}` and
never `{"findings": null}`: an empty list means *this ran and found nothing*, while an
absent or `null` key means *something wrote this file but not what the reader needs*, and
the PR comment has to keep those apart (`null` is this repo's "not measured", which a
wrapper around a list never is). If you never got as far as investigating, write **no
file** — an absent `findings.json` is reported as "run `/testsquad:test or :debug`",
which is the true fact.
