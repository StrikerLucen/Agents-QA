# Decisions — changes to the standard

Every change to a threshold, gate or scope in `acceptance-contract.md` is
recorded here, newest first. A threshold that can be edited silently is not a
standard; it is a preference, and it erodes.

| Date | What changed | From → To | Why | Decided by |
|---|---|---|---|---|
| 2026-11-12 | `billing/tax.ts` branch coverage gate | 90% → 80% | Release deadline; 6 branches sit behind an unavailable sandbox credential. Reverts when the credential lands | {{NAME}} |
| {{DATE}} | {{GATE or RULE}} | {{OLD}} → {{NEW}} | {{REASON}} | {{WHO}} |

## Rules

A decision is recorded **before** the new number takes effect, not after the
first run that needed it.

Only a human decides. An agent proposes a change and writes the row once the
answer is yes; it never lowers a target to make its own result pass — not by a
point, not "temporarily", not because the breach is small and the release is
Friday.

A temporary lowering names what reverses it and when. Without that, it is
permanent and should be written as permanent.

Raising a standard is recorded here too. The file is the history of what this
repo decided to care about, and a one-way ratchet is just as much a decision.
