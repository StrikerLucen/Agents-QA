# testsquad — working notes

A Claude Code plugin whose testing agents verify their own output by **execution**
rather than opinion. This file is for whoever is *developing* it. It is not loaded
when the plugin is installed by a user.

## If you were just told "continue"

**Read `HANDOFF.md` first.** It is the single source of truth: current state, the
named failing tests and why each fails, the prioritised next steps, and the decisions
that belong to the owner rather than to you.

Then re-measure before trusting anything written down:

```
cd examples/fixtures && ./regen_git_history.sh    # required on a fresh clone
cd tests && ls test_*.py | sed 's/\.py$//' | xargs python3 -m unittest
python3 scripts/validate_plugin.py . --strict
```

Expect **717 tests with 3 failures**, all named in `HANDOFF.md` §2. None is a broken
feature; each is a test that outlived the behaviour it asserted. Start there — it is
about half an hour of work and takes the suite green.

**Ask, do not assume**, on anything in `HANDOFF.md` §4. Those are the owner's calls.

**First thing after a fresh clone**, or several tests fail for the wrong reason:

```
cd examples/fixtures && ./regen_git_history.sh
```

The fixture git history is *part of the fixture* — `scripts/risk_map.py` is evaluated
against it — and it is not committed, because an embedded repo would not survive a clone.

## Commands

```
cd tests && ls test_*.py | sed 's/\.py$//' | xargs python3 -m unittest   # the suite
python3 scripts/validate_plugin.py . --strict                            # manifests + context budgets
python3 scripts/flake_smells.py tests scripts --fail-on block            # our own standards, on us
```

`unittest discover` does not work in `tests/`; use the form above. Python 3.9 is the
floor. Everything is stdlib — there is nothing to install, and that is deliberate.

## How work is done here

These are not style preferences. Each one exists because its absence caused a real
defect in this repo, and most were caught by the project's own tools.

- **Prove the test can fail.** After it passes, break the production code it covers,
  confirm it goes red, restore. Use **absolute paths and verify the restore** — one
  agent's entire mutation run was silently invalidated by a failed `cp`. Tests that
  passed while being unable to fail have been found here repeatedly.
- **Never state a number you did not measure.** `null` / "not measured" is a valid
  answer; `0` is not. Several of the worst bugs found here were a malformed input
  rendering as a confident, wrong number.
- **Every non-obvious claim traces to `docs/RESEARCH.md`.** If it is not there, hedge
  the claim or add the citation. Never invent one. `docs/CITATION-AUDIT.md` tracks this.
- **A false positive costs more than a miss.** A check above ~10% effective false
  positives gets ignored entirely, taking everything else with it. When unsure, stay
  silent — and say in the output that you stayed silent.
- **Silence must never look like success.** "I could not read it", "nobody ran this"
  and "there is nothing wrong" must be distinguishable in every output.
- **Context budgets are enforced** by `validate_plugin.py --strict`: 3,500 tokens per
  agent, 3,000 per skill, 1,600 per template. Prose costs tokens on every request.
- **Never edit product code to make a test pass.** Report the bug.

## Layout

| | |
|---|---|
| `agents/` | 7 agent definitions — the squad |
| `skills/` | 16 commands, `skills/<name>/SKILL.md` → `/testsquad:<name>` |
| `scripts/` | the deterministic core, stdlib only, heavily tested |
| `stacks/` | 10 per-language playbooks, read by `/start` only |
| `templates/` | what gets generated into a user's repo |
| `examples/fixtures/` | 3 repos with seeded bugs and decoys; `MANIFEST.json` is ground truth |
| `evals/` | 27 cases, 11 falsifiable claims, scored as rates over 5 runs |
| `docs/` | RESEARCH (the citation ledger) + four review documents |

## Known state

`evals/SCORECARD.md` is still a template. **No behavioural claim has been measured.**
The README says so, and it should keep saying so until the evals actually run.
`claude plugin validate --strict` has never run — `scripts/validate_plugin.py` is a
stand-in, not a substitute.
