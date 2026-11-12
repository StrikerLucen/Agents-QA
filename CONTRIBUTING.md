# Contributing to testsquad

testsquad's premise is that a claim without evidence is not a claim. That applies to
the project itself, so most of what follows is about how to produce evidence rather
than about style.

Everything here is stdlib Python. There is nothing to install, and that is deliberate.
**Python 3.9 is the floor**, not a courtesy — CI runs 3.9, 3.11 and 3.12, and a
3.9-only break is a real break.

## First, after cloning

```bash
cd examples/fixtures && ./regen_git_history.sh
```

**Required.** The three fixture repositories are committed as plain files; their git
history is not, because an embedded repository does not survive a clone. That history
*is* part of the fixture — `scripts/risk_map.py` is ranked against it — and tests in
`test_integration.py` and `test_risk_map.py` read it. The script pins every author and
date, so the result is byte-identical on every machine.

## The three gates

```bash
cd tests && ls test_*.py | sed 's/\.py$//' | xargs python3 -m unittest
python3 scripts/validate_plugin.py . --strict
python3 scripts/flake_smells.py tests scripts --fail-on block
```

`python3 -m unittest discover` does **not** work from inside `tests/` in this layout.
Use the form above; it is what CI runs and what every contributor runs locally.

The second gate enforces context budgets as well as manifest validity: 3,500 tokens per
agent, 3,000 per skill, 1,600 per agent template. Prose costs tokens on every single
request, so a file that grows past its budget is a regression in the product. If your
change needs more room, pay for it by restructuring — usually some other file already
says the same thing better.

The third gate is the project's own flake detector, run against the project's own
tests. If we would not ship a suite like that to a user, we do not get to keep one.

## If a gate is red

A red gate is information, not an obstacle. Nothing in CI is marked
`continue-on-error`, because a green badge that is not earned is worse than no badge.

- **A test fails.** Report the bug. Do not edit product code to make a test pass.
- **`flake_smells` blocks your change.** Either the test really cannot fail, or the
  check is wrong. Both are worth knowing. If you believe it is a false positive, file
  it with `.github/ISSUE_TEMPLATE/false_positive.md` — that is a finding against the
  tool, and the tool is held to the same ~10% effective-false-positive threshold it
  applies to everyone else. Above that, a check gets switched off entirely and takes
  the rest of the suite's credibility with it.

## Proving a test can fail

A test that passes while being unable to fail is this project's signature defect; it
has been found here repeatedly. Before you claim a test covers something:

1. **Purge bytecode** — `rm -rf scripts/__pycache__ tests/__pycache__`, and pass `-B`.
2. **Break the production code** the test covers.
3. **Run it** and confirm it goes red *for the reason you expect*.
4. **Restore** from an absolute path.
5. **Verify the restore** by SHA-256 against a pristine copy.

Steps 1 and 5 both exist because skipping one has silently invalidated a mutation run
here. A failed `cp` once left a mutant in place with nobody checking. Later, a stale
`.pyc` served the *pre-mutation* bytecode, so a mutant reported green while the hash
check said the restore was byte-perfect — hash-verifying is necessary but not
sufficient.

Include a **control mutant**: a comment-only edit that changes the file's hash and
nothing else. It must stay green. Without one you cannot distinguish a blind test from
a mutation that did nothing, and a no-op mutant reads exactly like a coverage gap.

## Two rules that outrank convenience

**Never state a number you did not measure.** `null` and "not measured" are valid
answers; `0` is not. Most of the worst bugs found in this repo were a malformed input
rendering as a confident, wrong number — a UTF-16 coverage file reporting 0%, a counter
missing an attribute reporting 100%.

**Silence must never look like success.** "I could not read it", "nobody ran this" and
"there is nothing wrong" must be distinguishable in every output. A safety gate
returning a clean verdict about a file it never opened was the worst defect of this
build, and it has recurred in four different disguises since.

## Citations

Every non-obvious claim traces to `docs/RESEARCH.md`. If it is not there, either add
the citation or hedge the claim. **Never invent a source** — and check that a source
you cite actually supports the specific number you are attaching to it, not merely an
adjacent one. `docs/CITATION-AUDIT.md` tracks this.

## What the agents and evals are

The deterministic core in `scripts/` is tested. The seven agents and sixteen commands
in `agents/` and `skills/` are prompts, and the eleven claims in `evals/` are
falsifiable but unscored. Until `evals/SCORECARD.md` has numbers, every behavioural
claim about agent conduct is **design intent, not evidence** — and the README should
keep saying so.

`CLAUDE.md` holds the working notes for developing the plugin. `HANDOFF.md` holds the
current measured state.
