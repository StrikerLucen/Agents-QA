---
name: verify
description: >
  Re-check the acceptance contract, or one roadmap item, against evidence measured now.
  Per gate: current value, target, met or breached, and the command that proves it. Use
  it before a release, after finishing roadmap work, or when someone claims a standard
  is met. It never re-negotiates a threshold to make a result look better — a change to
  a target is a recorded decision, not a side effect of a check.
---

# /testsquad:verify [item]

With no argument, you check the whole acceptance contract. With an item id, you check
that one roadmap item's done-when line. Either way the job is identical: **run the
commands, report what they returned.**

You are not deciding whether the standard was reasonable. You are deciding whether it
was met.

## What you need

- `.testing/acceptance-contract.md` — the gates. No contract, no verification: say so
  and offer `/testsquad:start` step 11. A verification against an unagreed target is an
  opinion with a table around it.
- `.testing/roadmap.md` — the done-when lines, if an item was named.
- `.testing/health-baseline.json` for the **current** commit. Numbers measured under a
  broken suite cannot clear a gate, so an absent or stale baseline means you run
  `/testsquad:health` first or report `CANNOT VERIFY` with the reason.
- `.testing/env-safety.json` — nothing executes before this passes.

## Running the gates

Every line in the contract was written to be checkable by a command. Run that command,
literally, from the repo root, and record its actual output. Do not substitute an
equivalent command you prefer; a gate that passes under a different invocation has not
passed.

| Result | When |
|---|---|
| **MET** | The command ran and the observable matched the target |
| **BREACHED** | The command ran and the observable did not match |
| **CANNOT VERIFY** | The command could not run — missing dependency without permission, absent service, unsafe environment, stale health baseline |
| **NO LONGER CHECKABLE** | The command refers to something that no longer exists. This is a defect in the contract and needs a decision |

`CANNOT VERIFY` is a first-class verdict and must never be rendered as a pass. It names
exactly what is needed to turn it into one.

## The report shape

One row per gate, in contract order, each with its command and real output:

```
§2.1  billing/tax.ts branch coverage ≥90%           BREACHED   71%
      npx jest src/billing/tax.ts --coverage
      # Branches: 44/62 (71.0%)

§2.2  no test disabled with .only                   MET        0
      python3 "${CLAUDE_PLUGIN_ROOT}/scripts/flake_smells.py" test/ --format text
      # only/fit/fdescribe: 0

§3.1  suite completes in under 6 minutes            CANNOT VERIFY
      needs: npm ci (dependencies missing, install not permitted)
```

Breaches are reported plainly, in the house voice: *"tax coverage is 71%, the contract
says 90% — 18 branches short"*, not *"partial compliance observed"*. Nothing is rounded
up, nothing is softened, and two gates of three met is reported as two of three. That
one behaviour is what the whole design exists to protect.

Lead with the verdict line, ≤25 words, naming a state and one next action:

```
VERDICT: Not ready — billing tax coverage is 18 branches short of the contract.
```

## The rule about thresholds

**You never lower a target to make a result pass.** Not by a point, not "temporarily",
not because the breach is small and the release is Friday.

If a threshold genuinely is wrong — the code moved, the tier changed, the target was set
before anyone understood the module — that is a conversation with the user, and its
outcome is written to `.testing/decisions.md` with the date, the old value, the new
value, who agreed to it and **why**. The contract is then amended, and the next
verification runs against the amended contract openly.

The reason for the ceremony is erosion. A threshold that can be adjusted quietly at the
moment it is inconvenient is not a threshold; it is a description of whatever happened.
Every individual adjustment is defensible and the sequence of them is how a standard
disappears without anyone deciding to abandon it. Writing each one down costs thirty
seconds and makes the drift visible.

The same holds for scope: verifying a subset because the full run is slow is only
honest if the report says which gates were skipped and why. Silently narrowing the check
is the same failure wearing different clothes.

## Output

Append the run to `.testing/verification-log.md` — date, commit, health verdict, and the
per-gate table — so the trend is visible and a gate that has been breached for six weeks
cannot read as news.

Then write the same table to **`.testing/gates.json`**, which is the file the PR comment
renders its gate section from. Nothing else produces it, so skipping this is what makes
the comment ship with an empty gate section and no sign that anything is missing:

```json
{"gates": [{"name": "§2.1 tax.ts branch coverage",
            "target": "≥90%", "actual": "71%", "met": false}]}
```

One object per gate, in contract order. `met` is a boolean and nothing else.
`CANNOT VERIFY` and `NO LONGER CHECKABLE` are `met: false` — they are certainly not met —
and carry the reason in `actual`: `"CANNOT VERIFY — needs npm ci"`. A gate nobody could
check must never be indistinguishable from one that passed.

Update the dashboard via `/testsquad:report`, bound by
`${CLAUDE_PLUGIN_ROOT}/templates/report/RULES.md`: every number carries its literal
command, no number appears that was not measured, and no secret appears in any form.
