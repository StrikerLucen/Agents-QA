# Changelog

All notable changes to this project are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

Everything below is on `main` as of 2026-11-18 (`harden/silent-success-sweep` merged
in #1; `git log main..harden/silent-success-sweep` is empty). The entry under 0.1.0 was written at
release and never revised; several of its claims were wrong when written and are
corrected in place, with what was corrected noted where it is not obvious.

### Added

- **A consent gate before any repo-supplied code runs.** `/testsquad:start` step 5
  prints the repository's own test, coverage and install commands verbatim and states
  that agreeing runs the repo author's code as you, on this machine. Step 4 profiles
  statically first, so nothing executes before the gate. The record lives at
  `consent.execute_repo_commands` in `.testing/preferences.json`; a missing or
  malformed record is **not** consent and fails closed to the static pass.
  `tests/test_consent_contract.py` guards the contract.
- **CI** (`.github/workflows/ci.yml`): the suite on Python 3.9 / 3.11 / 3.12,
  `validate_plugin.py --strict`, and the project's own flake gate run against the
  project's own `tests/` and `scripts/`. Nothing is `continue-on-error`.

### Fixed

- **Four silent-success defects in our own code** — the class this project exists to
  catch: `session_context.py` exiting 0 with both streams empty; `pr_comment.py`
  printing "No new findings on this change." when nothing ran; `flake_smells.py`
  reporting a clean scan of a line it had truncated and never read; `validate_plugin.py`
  losing a whole check's verdict for all 26 components when one was unreadable.
- **Coverage honesty.** Malformed or impossible inputs now yield `null` with a named
  warning instead of a confident wrong percentage.
- **All seven `--out` writers are atomic** (temp file, then rename), so
  `skills/start/SKILL.md`'s promise is kept.
- **All seven agents are reachable.** `project-analyst`, `tech-scout` and `reporter`
  were dispatched by nothing; they are now dispatched by `start`, `scout` and `report`
  respectively.
- **Missing fixture ground truth restored.** `examples/fixtures/node-ts/.env` is named
  by `MANIFEST.json` but had never been committed, so `env_safety_check.py` returned
  SAFE for the fixture that exists to be UNSAFE.

### Not done — stated so silence does not read as success

- **The evals have barely run, and the one result is bad.** A bounded run on 2026-11-16
  took **3 of 27 cases**, all under claim 1, and the plugin scored **worse than no
  plugin**: mean Δ −0.20, p = 0.35 at n=10 per arm. Ten of the eleven claims still have
  no data, so `evals/SCORECARD.md` remains a template — its rule 4 forbids publishing
  one while any claim reads NO DATA. Every behavioural claim in this file is still
  design intent rather than evidence; the one place it was tested, the evidence pointed
  the other way. See `evals/RUN-2026-11-16-partial.md`.
- **`claude plugin validate --strict` passes**, run 2026-11-16 with CLI 2.1.276. It is
  **not** a superset of `scripts/validate_plugin.py . --strict` (26 components, 0
  warnings, 0 could not be validated, same date): the CLI validates the *marketplace
  manifest*, and said nothing about the other 25 components. Neither runs in CI — the
  CLI is not available on the runners, and the workflow says so rather than skipping it
  quietly. Note the CLI lives at `~/.local/bin/claude` and is not on the default PATH,
  so `which claude` reports it missing; that is how this entry first said it had never
  run.

## [0.1.0] - 2026-11-12

### Added

**The squad.** Eight roles, of which **seven are agent files in `agents/`** —
project analyst, unit-test engineer, automation engineer, debugger, QA gatekeeper,
reporter, tech scout — and the eighth is the **orchestrator**, which is a skill
(`skills/test/`) rather than a subagent, because it has to ask the user questions a
subagent cannot. The orchestrator picks two to four specialists per run, chosen from
what the change actually touches. Multi-agent cost bottoms out around four to six
subagents and climbs after, so the roster is a menu, not a crew — see
`docs/RESEARCH.md` §21, which labels those cost figures directional rather than
measured. (`/testsquad:advise` is a command, not an eighth agent; earlier wording in
this entry listed it as one and left the orchestrator out.)

**Onboarding that fits your repo.** `/testsquad:start` checks it is safe to run
anything, profiles the project, takes a suite-health baseline, builds a risk map from
git history, and then writes agents tailored to *that* stack into `.claude/agents/`,
plus a `.testing/` knowledge folder. The safety check comes first, before the profile
— nothing the repository supplied runs until it has passed. Generated agents are fully
regenerable; everything learned lives in `.testing/`, so upgrading never discards your
work.

**Single-job commands.** `health`, `diagnose`, `test`, `verify`, `debug`,
`mutate`, `learn`, `report`, `advise`, `scout`, `plan`, `depth`, `preferences`,
`upgrade`, `uninstall`. Fifteen commands, plus `start` itself. `start` is not a black
box: most of its stages are one of these and can be re-run on their own, and the rest
are a single documented script invocation printed in `skills/start/SKILL.md`. The
environment safety check, the profiling pass and the risk map have **no standalone
command** — they are `scripts/env_safety_check.py`, the `project-analyst` agent and
`scripts/risk_map.py`, run directly.

**A deterministic core.** Risk mapping, coverage normalization, flake smells,
environment safety and PR comments are Python, **standard library only, with no
install step** — no dependencies to resolve and nothing to keep in sync. The LLM
writes tests; it never sits inside the suite deciding pass or fail.

**Safety guarantees.** Nothing executes until an environment safety check passes,
and secrets are reported as key names and redacted previews only. Only findings badged
**reproduced** (you ran it and saw it fail) or **observed** (present in a log or CI
artifact you read) reach the main list and the bug total; code-read and suspicion are
labelled, moved to a collapsed appendix with the reason they did not qualify, and never
counted. `CANNOT VERIFY` is a first-class verdict that is never rendered as a pass.
Acceptance criteria are frozen before work starts, and lowering a threshold is
recorded as a dated decision. One hook ships enabled — a read-only session
summary; the intrusive ones are examples you copy in deliberately.
`/testsquad:uninstall` removes exactly what was added.

**Research basis.** Every non-obvious decision cites a source in
`docs/RESEARCH.md`: coverage as a floor detector rather than a quality target (§1);
test generation from a spec rather than an implementation, which moves
bug-asserting tests from 3.84% to 0.46% (§2); mutation-guided acceptance at 12–15%
kill rate against 2.0–2.4% for coverage-guided (§4); a 10% effective-false-positive
ceiling before developers stop trusting a tool (§7); and per-language flake taxonomies,
because async-wait causes 45% of Java flakes (74/161, the paper's own rounding) and
is almost absent in Python — 3 instances in a study of 7,571 flaky tests (§19).

[Unreleased]: https://github.com/StrikerLucen/Agents-QA/commits/main
[0.1.0]: https://github.com/StrikerLucen/Agents-QA

No `v0.1.0` tag exists — `git ls-remote --tags` returns nothing as of 2026-11-18.
The two links above pointed at `thomaspino/testsquad` and at a tag that was never
cut, so both were 404s. Neither can be a compare range until a release is tagged:
`[Unreleased]` lists commits on `main` instead, and `[0.1.0]` goes to the repository
root. Replace both with real ranges when a tag is cut.
