---
name: depth
description: >
  Set how hard the squad works to reproduce a finding — quick, standard, deep or
  exhaustive — once, stored in `.testing/preferences.json` and never asked again. Use it
  to change the setting or to see what it currently is. Depth governs investigation
  only: it never weakens a verification, a gate or a QA verdict.
---

# /testsquad:depth [level]

With a level, set it. Without one, show the current setting, when it was set, and what
it means in minutes.

This is asked once, during `/testsquad:start`, and then remembered. Re-asking something
already answered is a defect — every command reads `.testing/preferences.json` before it
considers asking anything.

## The levels, in effort per finding

Depth is defined in **reproduction effort**, not in adjectives. "Thorough" means nothing
to anyone; "forty-five minutes including a bisect" means something.

| Level | Budget per finding | Strategies tried | Suits |
|---|---|---|---|
| **quick** | ~2 min | 1 | A fast pass on a PR; you will miss intermittent things and the report will say so |
| **standard** | ~10 min | 3 | The default. Catches most reproducible failures |
| **deep** | ~45 min | All, including `git bisect` and order randomization | A release, a hard bug, critical-tier code |
| **exhaustive** | Until reproduced or genuinely exhausted | All, with environment sweeps over `TZ`, `LANG`, hash seed and clock | An incident, or a bug that has escaped twice |

**Every finding must be reproduced at every level.** Quick does not mean "report
hypotheses faster". It means fewer strategies are tried before the budget expires, and
whatever did not reproduce is reported as *"could not reproduce in 2 minutes; here is
what I tried"* — in the unconfirmed appendix, with its reason, never counted in the bug
total. A wrong repro delays a fix more than a missing one, so the cheap setting buys
less searching, never looser standards of proof.

`--deep` on `/testsquad:debug` lifts the budget for one item without changing the stored
preference.

## The critical rule: depth governs investigation, never verification

**The same task at quick and at exhaustive must produce the same QA verdict.**

Depth changes how long you look for something. It does not change:

- whether the acceptance criteria are checked — all of them, every time;
- whether a new test is proven able to fail;
- whether the QA gatekeeper re-runs every command itself;
- whether a contract gate is evaluated;
- whether a claim needs its literal reproducing command attached;
- whether a partial result is reported as partial.

**An agent that reads the depth setting to skip a verification step is a defect**, and
one worth reporting as a bug in this plugin rather than working around. The whole value
of the tool is that its verdict means the same thing on a Tuesday afternoon as it does
before a release. A knob that quietly lowers the bar turns every past green result into
something nobody can interpret, because they no longer know which setting produced it.

The honest way to spend less time is to search less, find less, and **say what you did
not look for**. The dishonest way is to look just as little and call what you found
confirmed. Only the first is available.

## Reporting under a shallow setting

Any report produced at `quick` states it, in the appendix, with the consequence spelled
out: *"Depth: quick — one reproduction strategy per finding, ~2 minutes each. 4 items
could not be reproduced in that budget and are in the unconfirmed list, not counted."*

A reader who does not know the search was shallow will read a short findings list as
good news. That is the single way this setting can mislead, so it is closed explicitly.

## Output

Write `.testing/preferences.json` atomically, recording the level, the date and who set
it. Confirm in one line what changed and what it now costs per finding. Nothing else
runs — this command sets a number and leaves.
