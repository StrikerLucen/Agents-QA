# testsquad

A Claude Code plugin for testing work. It profiles a repository, generates testing
agents tailored to it, and is designed so that anything a verdict depends on — can the
suite run, can this test fail, is this environment safe — is **executed rather than
reasoned about**.

Version 0.1.0 · MIT · requires Python 3.9+ and `git`. Everything is standard library;
there is nothing to install, and that is deliberate.

---

## Read this before you believe anything else here

This repository is two things, and only one of them has been measured.

**Measured.** The deterministic core in `scripts/` — ten standard-library Python
modules — has its own test suite and three gates that run on every push and pull
request. They are listed below and you can run all three yourself in about a minute.

**Not measured.** The seven agents in `agents/` and the sixteen commands in `skills/`
are *prompts*: instructions to a model, as reliable as the model following them. The
eleven behavioural claims in `evals/` are written down and falsifiable, and **ten of the
eleven have never been scored**. `evals/SCORECARD.md` is still an unfilled template —
every figure in it is an unreplaced `<...>` placeholder.

One bounded run on 2026-11-16 measured 3 of the 27 cases, all under claim 1. On that
claim the plugin scored **worse than running with no plugin at all** (mean Δ −0.20;
not statistically significant at n=10 per arm, p = 0.35). The numbers and the caveats
are in [`evals/RUN-2026-11-16-partial.md`](evals/RUN-2026-11-16-partial.md).

> **Until `evals/SCORECARD.md` has numbers in it, every behavioural claim about this
> plugin is design intent, not evidence.**

So nothing in this file tells you what an agent *does*. It tells you what is written
down for an agent to do, and which parts of that are enforced by code you can run.

---

## The three gates

```sh
cd examples/fixtures && ./regen_git_history.sh
cd tests && ls test_*.py | sed 's/\.py$//' | xargs python3 -m unittest
python3 scripts/validate_plugin.py . --strict
python3 scripts/flake_smells.py tests scripts --fail-on block
```

The first step is not optional. The three fixture repositories are committed as plain
files; their git history is not, because an embedded repository does not survive a
clone. That history is part of the fixture — `scripts/risk_map.py` is ranked against it.

Use the `ls | xargs` form above; it is what CI runs. `unittest discover` works as well
— measured 2026-11-16, identical tests and result. A wall of failures from either form
means the fixture history above was not regenerated.

No test count is printed here on purpose. It moved three times in a single working
session while the docs were being written, and three different counts were committed
that afternoon. **The command is the answer; the number is not.**

| Gate | What it enforces |
|---|---|
| the suite | 13 test modules in `tests/`, covering the ten modules in `scripts/` |
| `validate_plugin.py --strict` | manifest validity, plus context budgets — 3,500 per agent, 3,000 per skill, 1,600 per agent template, 1,200 for the report rules. The unit is `bytes // 4`, **not** a tokenizer: measured 2026-11-18 this corpus runs ~2.86 chars/token, so a "3,500" ceiling admits roughly 4,900 real tokens |
| `flake_smells.py` on `tests scripts` | the project's own flake detector (20 rules, `len(RULES)` measured 2026-11-16) run against the project's own tests, failing the build on any `block` finding |

[`.github/workflows/ci.yml`](.github/workflows/ci.yml) runs all three on every push to
`main` and every pull request. The suite runs on **Python 3.9, 3.11 and 3.12** (3.9 is
the declared floor, pinned to `ubuntu-22.04` because newer images no longer offer it);
the other two gates run once. No job is marked `continue-on-error` — a green badge that
is not earned is worse than no badge.

---

## What is in the repository

| | |
|---|---|
| `scripts/` | 10 stdlib Python modules — the deterministic core |
| `tests/` | 13 test modules for that core |
| `agents/` | 7 agent definitions — **prompts** |
| `skills/` | 16 commands, `skills/<name>/SKILL.md` → `/testsquad:<name>` — **prompts** |
| `stacks/` | 10 per-language playbooks |
| `templates/` | what gets generated into a user's repo |
| `examples/fixtures/` | 3 repos with planted defects; `MANIFEST.json` is ground truth |
| `evals/` | 27 cases, 81 graders, 11 behavioural claims + a deterministic pre-gate, 5 runs per case — **3 cases run once, 2026-11-16; 10 of 11 claims still have no data** ([partial result](evals/RUN-2026-11-16-partial.md)) |
| `docs/` | the citation ledger, three audits, and three user docs (quickstart, install, FAQ) |
| `hooks/` | one hook ships enabled (`SessionStart`); three more are opt-in in `hooks/examples/` |

`examples/fixtures/MANIFEST.json` records an expected verdict for all **71** fixture
items — 12 seeded bugs, 12 decoys (correct code that looks wrong; reporting one is a
failed eval), 16 weak tests, 13 good tests, 12 flaky, 4 skipped and 2 config files.
`tests/test_fixture_manifest.py` asserts every one of them resolves on disk.

Trying it on the fixtures costs no tokens and tells you more than this page does:
[docs/QUICKSTART.md § Try it on a fixture first](docs/QUICKSTART.md#2-try-it-on-a-fixture-first).

---

## The rules this project holds itself to

These are not style preferences. Each one exists because its absence caused a real
defect here, and most were caught by the project's own tools. The full versions, with
the incidents behind them, are in [CONTRIBUTING.md](CONTRIBUTING.md).

- **Never state a number you did not measure.** `null` and "not measured" are valid
  answers; `0` is not. Several of the worst bugs found here were a malformed input
  rendering as a confident, wrong number.
- **Silence must never look like success.** "I could not read it", "nobody ran this"
  and "there is nothing wrong" must be distinguishable in every output.
- **Prove the test can fail.** Purge bytecode → mutate → run → restore → verify by
  SHA-256, with a comment-only control mutant that must stay green. Skipping any step
  has silently invalidated a mutation run here.
- **A false positive costs more than a miss.** Past roughly 10% effective false
  positives a check gets ignored entirely, taking everything else with it.
- **Every non-obvious claim traces to [`docs/RESEARCH.md`](docs/RESEARCH.md).** Never
  invent a source.
- **Never edit product code to make a test pass.** Report the bug.

---

## Where to go next

| | |
|---|---|
| [docs/INSTALL.md](docs/INSTALL.md) | step-by-step install — from GitHub, a clone, or a local folder — and what it costs in context |
| [docs/QUICKSTART.md](docs/QUICKSTART.md) | the zero-token fixture check, a first real run, reading a report |
| [docs/FAQ.md](docs/FAQ.md) | what this gets wrong, stated specifically — start here if you are skeptical |
| [docs/RESEARCH.md](docs/RESEARCH.md) | the citation behind every non-obvious design decision |
| [evals/SCORECARD.md](evals/SCORECARD.md) | the template that is still empty |
| [CONTRIBUTING.md](CONTRIBUTING.md) | the gates, and how to produce evidence here |
| [CLAUDE.md](CLAUDE.md) · [HANDOFF.md](HANDOFF.md) | working notes and the current measured state |

Licensed under the [MIT License](LICENSE).
