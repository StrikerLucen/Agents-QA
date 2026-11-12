<!--
The checklist below is the project's house standard, not boilerplate. It is
the same standard the agents in this repository are held to, and CONTRIBUTING.md
explains the reasoning behind each line.

An unchecked box is fine. An unchecked box with a sentence saying why is
better than a checked box that is not true -- the one thing this project
cannot absorb is a claim nobody verified.
-->

## What this changes

<!-- The consequence, in one or two sentences. Not the file list -- the diff
already shows that. -->

## Why

<!-- What was wrong, or what was missing. If it fixes an issue, link it. -->

---

## Evidence

### Tests

- [ ] Tests added or updated for every behaviour change
- [ ] The full suite passes locally:
      `cd tests && ls test_*.py | sed 's/\.py$//' | xargs python3 -m unittest`
- [ ] Fixture git history rebuilt first (`cd examples/fixtures && ./regen_git_history.sh`)

<!-- Paste the final counts line, e.g. "Ran 710 tests ... FAILED (failures=7)".
If failures remain, say which, and whether they are the known pre-existing
ones listed in HANDOFF.md or something this PR introduced. -->

```
```

### Mutation check — prove each new test can fail

> A passing test is not evidence until you have seen it fail.

For **every** test added in this PR: break the production code it covers,
confirm the test goes red, restore, confirm the test goes green again.

- [ ] Each new test was mutation-checked
- [ ] Absolute paths used throughout the mutation run
- [ ] **The restore was verified** — `git diff` (or `git status`) clean afterwards,
      checked, not assumed

<!-- Use absolute paths and verify the restore for a concrete reason: one
agent's entire mutation run on this repository was invalidated by a silent
restore failure. Everything after it was measuring a tree that was not the
tree it reported on.

Record each one: which file and line was broken, what the mutation was,
which test went red, and that the restore was confirmed. -->

| Test | Production line mutated | Mutation | Went red? | Restore verified? |
|---|---|---|---|---|
| | | | | |

### Claims

- [ ] Every number in this PR, in the code, the comments and the docs, was
      measured — not estimated, not remembered
- [ ] Every factual or quantitative claim added traces to an entry in
      `docs/RESEARCH.md`; anything not there is hedged, or a citation was
      added to RESEARCH.md in this PR
- [ ] No citation was invented or inferred from memory

<!-- "not measured" is an acceptable value to ship. 0% is not, unless you ran
it and got zero. docs/CITATION-AUDIT.md is what happens when this slips. -->

### Gates

- [ ] `python3 scripts/validate_plugin.py . --strict` passes
- [ ] Context budgets respected — 3,500 tokens per agent, 3,000 per skill,
      1,600 per agent template, 1,200 for the report rules (the validator
      enforces these; do not raise a budget to fit a file without saying why)
- [ ] `python3 scripts/flake_smells.py tests scripts --fail-on block`
      introduces **no new `block` finding**

<!-- If this PR adds a block finding, the right move is almost always to fix
the test. If you believe the check is wrong, say so here and open a false
positive issue -- that is a defect in the check, and we treat it as one. -->

### False positives

- [ ] If this PR adds or widens a check, it was run against a real repository
      and the findings were read one by one
- [ ] Nothing in it fires on correct code

<!-- A check above roughly 10% effective false positives gets ignored
wholesale, which makes it worse than not shipping it. When unsure, stay
silent. -->

### Scope

- [ ] stdlib only — no new dependency, no install step
- [ ] No secret, token, absolute home path or customer data in the diff or in
      any committed fixture

## Anything a reviewer should look at first

<!-- Where you are least sure. Say it here rather than hoping it is noticed. -->
