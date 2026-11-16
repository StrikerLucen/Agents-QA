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
cd examples/fixtures && ./regen_git_history.sh
cd tests && ls test_*.py | sed 's/\.py$//' | xargs python3 -m unittest
python3 scripts/validate_plugin.py . --strict
```

Expect the suite **all passing**, with no skips and one expected failure. Run it rather
than trusting a count here — it moved from 717 to over 900 in a single session, and three
different counts were measured in one afternoon, so any number written down goes stale
before it is read. The three failures the previous handoff named are fixed, as are two it
did not know about. The suite's only skip was removed when the test holding it turned out
to be unable to fail on a fast machine.

**Ask, do not assume**, on anything in `HANDOFF.md` §4. Those are the owner's calls.

**First thing after a fresh clone**, or several tests fail for the wrong reason:

```
cd examples/fixtures && ./regen_git_history.sh
```

The fixture git history is *part of the fixture* — `scripts/risk_map.py` is evaluated
against it — and it is not committed, because an embedded repo would not survive a clone.

## Commands

```
cd tests && ls test_*.py | sed 's/\.py$//' | xargs python3 -m unittest
python3 scripts/validate_plugin.py . --strict
python3 scripts/flake_smells.py tests scripts --fail-on block
```

In order: the suite; manifests plus context budgets; our own flake standards applied to
us. No `#` comments in these blocks — zsh leaves `interactive_comments` off, so a pasted
trailing comment becomes an argument and argparse rejects the line.

Use the form above — it is what CI runs. (`unittest discover` works too: measured
2026-11-16, both forms collect the same tests and both pass. This file claimed
otherwise since the initial commit and the claim was never checked. What really
produces a wall of failures is skipping the fixture regeneration above, from *either*
form.) Python 3.9 is the
floor. Everything is stdlib — there is nothing to install, and that is deliberate.

## How work is done here

These are not style preferences. Each one exists because its absence caused a real
defect in this repo, and most were caught by the project's own tools.

- **Prove the test can fail.** After it passes, break the production code it covers,
  confirm it goes red, restore. The order is **purge bytecode → mutate → run →
  restore → verify by SHA-256**, using absolute paths throughout. All three steps
  exist because skipping one has silently invalidated a mutation run here:
  - a failed `cp` left the mutant in place, and nobody checked;
  - a stale `__pycache__` served the *pre-mutation* bytecode, so the mutant reported
    GREEN while SHA-256 said the restore was byte-perfect. Three agents hit this
    independently in one session. One mutant had an identical file size and was
    restored inside the same clock second, so the `.pyc` mtime+size validity check
    passed and the test went on running the mutant. **Hash-verifying the restore is
    necessary but not sufficient** — `rm -rf scripts/__pycache__ tests/__pycache__`
    before *every* invocation, and pass `-B`.
  - **Include a control mutant**: a comment-only edit that changes the file's hash
    and nothing else. It must stay GREEN. Without it you cannot distinguish a blind
    test from a mutation that did nothing, and a no-op mutant reads exactly like a
    coverage gap. One was nearly misdiagnosed as a blind test for this reason.
  Tests that passed while being unable to fail have been found here repeatedly.
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
`README.md` says so, under *Read this before you believe anything else here* — its
opening section, before anything about what the plugin does — and it should keep saying
so until the evals actually run. (That README was written 2026-11-16. Before then there
was none, and the three files claiming one carried the disclaimer were telling users
nothing.)
`claude plugin validate --strict` passes (run 2026-11-16, CLI 2.1.276), but it is not a
substitute for `scripts/validate_plugin.py`: it validates the marketplace manifest only
and says nothing about the other 25 components. The CLI is at `~/.local/bin/claude` and
is NOT on the default PATH — `which claude` will tell you it is missing.
