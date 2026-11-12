# Changelog

All notable changes to this project are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] - 2026-11-12

### Added

**The squad.** Eight testing roles — analyst, unit-test engineer, automation
engineer, debugger, QA gatekeeper, reporter, advisor, tech scout — of which an
orchestrator picks two to four per run, chosen from what the change actually
touches. Multi-agent cost bottoms out around four to six subagents and climbs
after, so the roster is a menu, not a crew.

**Onboarding that fits your repo.** `/testsquad:start` profiles the project,
checks it is safe to run anything, takes a suite-health baseline, builds a risk
map from git history, and then writes agents tailored to *that* stack into
`.claude/agents/`, plus a `.testing/` knowledge folder. Generated agents are
fully regenerable; everything learned lives in `.testing/`, so upgrading never
discards your work.

**Single-job commands.** `health`, `diagnose`, `test`, `verify`, `debug`,
`mutate`, `learn`, `report`, `advise`, `scout`, `plan`, `depth`, `preferences`,
`upgrade`, `uninstall`. Nothing is a black box: every stage of `start` is one of
these and can be re-run on its own.

**A deterministic core.** Risk mapping, coverage normalization, flake smells,
environment safety and PR comments are Python, **standard library only, with no
install step** — no dependencies to resolve and nothing to keep in sync. The LLM
writes tests; it never sits inside the suite deciding pass or fail.

**Safety guarantees.** Nothing executes until an environment safety check passes,
and secrets are reported as key names and redacted previews only. No bug is
reported that could not be reproduced; unverified items are labelled and never
counted. `CANNOT VERIFY` is a first-class verdict that is never rendered as a
pass. Acceptance criteria are frozen before work starts, and lowering a threshold
is recorded as a dated decision. One hook ships enabled — a read-only session
summary; the intrusive ones are examples you copy in deliberately.
`/testsquad:uninstall` removes exactly what was added.

**Research basis.** Every non-obvious decision cites a source in
`docs/RESEARCH.md`: coverage as a floor detector rather than a quality target;
test generation from a spec rather than an implementation, which moves
bug-asserting tests from 3.84% to 0.46%; mutation-guided acceptance at 12–15%
kill rate against 2.0–2.4% for coverage-guided; a 10% effective-false-positive
ceiling before developers stop trusting a tool; and per-language flake taxonomies,
because async-wait causes 45% of Java flakes and almost none in Python.

[Unreleased]: https://github.com/thomaspino/testsquad/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/thomaspino/testsquad/releases/tag/v0.1.0
