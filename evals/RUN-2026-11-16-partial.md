# Partial eval run — 2026-11-16

**This is not a scorecard.** `SCORECARD.md` is still an unfilled template, and its
rule 4 forbids publishing one while any claim reads NO DATA. Ten of the eleven
behavioural claims have no data at all. This file records what one bounded run
measured, so the result is not lost in a terminal.

| | |
|---|---|
| Run date | 2026-11-16 |
| Plugin version | 0.1.0 |
| Claude Code | 2.1.276 |
| Model under test | **not recorded by the harness** — see "What this run cannot tell you" |
| Judge model | `haiku` (the `--judge-model` default; not recorded in the artifact either) |
| Ablation | `with-without`, 5 runs per case per arm |
| Cases run | **3 of 27**, all under claim 1 |
| Claims with data | **1 of 11** |
| Agent runs | 25 of a planned 270 |
| Cost | $25.67 |
| Wall clock | 6,883s |
| Stopped by | cost ceiling (`--max-cost-usd 25`) |

Command:

```bash
claude plugin eval . --scaffold --allow-tools Bash Write Edit --max-cost-usd 25
```

Raw output: `evals/results/2026-11-16T04-25-56-635Z/` (gitignored, local only).

## The result

Claim 1 is *does not report decoy code as a product bug*. The fixtures are code that
only **looks** defective — a `+ size` loop bound, `==` on a double, `Thread.sleep`,
shared mutable-looking static state. A correct response reports zero bugs.

| Case | with | without | Δ |
|---|---|---|---|
| `claim-01-decoys-java` | 0.40 | 0.70 | **−0.30** |
| `claim-01-decoys-node-exhaustive` | 0.70 | 0.80 | **−0.10** |
| `claim-01-decoys-node-quick` | 0.80 | *(not run — ceiling)* | — |
| | | **mean Δ** | **−0.20** |

Pooled on the grader that fails, across the two cases that completed both arms:

| | with plugin | without plugin |
|---|---|---|
| `no-bug-reported` passed | **2 / 10** (20%) | **5 / 10** (50%) |

Fisher exact, two-sided: **p = 0.35**.

**The plugin scored worse than no plugin, in both cases that had a baseline.** On
`claim-01-decoys-java` the with-arm failed `no-bug-reported` in **5 of 5** runs while
the baseline passed it twice.

The second grader is not the problem: `no-severity-label` passed in 14 of 15 with-arm
runs. The failure is specific — with testsquad loaded, the agent calls decoys bugs
more often.

## How much weight this carries

Stated plainly, because the direction is unflattering and that makes it tempting to
over- or under-sell:

- **p = 0.35 is not significant.** At n=10 per arm this is consistent with noise.
- **But the direction is consistent**: negative in both ablated cases, and one case
  went 0 for 5 on the deciding grader.
- **The baseline is not good either** — 50% on a task where the correct answer is
  "report nothing". This is a hard case for the model with or without the plugin.
- **One claim of eleven.** Nothing here says anything about claims 2–11.
- 3 cases of 27. The suite is designed as 270 runs; this was 25.

## What this run cannot tell you

- **Which model produced it.** `aggregate-result.json` records `claudeVersion` but no
  model id, and the report HTML has none either. `SCORECARD.md` rule 2 requires the
  model to be pinned — that rule cannot be satisfied from the artifact the harness
  writes. Either the harness should record it or the scorecard needs a different
  provenance field.
- **Anything about claims 2–11**, which include every claim about finding real bugs,
  classifying flakes, withholding coverage on a red suite, and refusing to execute.
- **Whether claim 1's result survives more runs.** It should be re-run before being
  treated as settled.

## What a full run costs

Measured here, not estimated: the with-arm costs 3–6× the baseline per run ($1.05–$3.00
against $0.31–$0.59), because the plugin's prompts and dispatched subagents are real
tokens. At this rate the full 27 cases × 2 arms × 5 runs is roughly **$220–320**.

## Next

1. Re-run claim 1 with more runs to see whether −0.20 holds or washes out.
2. If it holds, read the traces (`--keep-temp`) and find out why loading testsquad makes
   an agent likelier to call a decoy a bug. That is a prompt defect, not a code defect,
   and it is the opposite of what claim 1 promises.
3. Only then spend on the remaining 24 cases.

Until `SCORECARD.md` has numbers, `README.md` is correct to say every behavioural claim
is design intent rather than evidence — and this run is the first evidence that the
intent and the behaviour can diverge.
