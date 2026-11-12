---
name: start
description: >
  Onboard testsquad onto one or more projects. Profiles each repo, checks it is safe to
  run anything, takes a suite-health baseline, builds a risk map from git history,
  generates testing agents tailored to that stack, seeds a knowledge folder, diagnoses
  coverage, and proposes a quality contract and a prioritized roadmap. Use it the first
  time testsquad meets a codebase, or to re-profile after the stack changes.
---

# /testsquad:start

You are onboarding one or more repositories. By the end the user should have agents that
fit *their* project, an honest picture of where they stand, and a ranked list of what to
do first.

**Everything here is a chain of the single-job commands.** Nothing is a black box: each
stage can be re-run on its own afterwards, and you should say so.

## Before anything: the two rules that govern this whole run

**Ask once, at the start, batched.** Questions are gathered into one decision point, then
the run proceeds unattended. Re-asking something already answered is a defect — check
`.testing/preferences.json` before asking anything.

**Never run anything until it is safe to.** No test command, no coverage run, no
dependency install, until step 3 has passed or the user has said yes. A suite you have
never seen may point at production.

---

## Step 1 — Choose the targets

Find candidate project roots under the working directory: directories containing a git
repo, or a `package.json` / `pyproject.toml` / `go.mod` / `pom.xml` / `build.gradle` /
`*.csproj` / `Gemfile` / `composer.json` / `Cargo.toml` / `pubspec.yaml`.

Offer them with AskUserQuestion (multi-select). Accept typed paths too. If exactly one
obvious root exists, say which you picked and move on rather than asking.

## Step 2 — Ask the opening questions, once

In **one** AskUserQuestion call:

1. **Depth** — quick / standard / deep / exhaustive. Explain it in terms of effort spent
   reproducing a finding, not in abstract terms. Standard is the default.
2. **May I install dependencies?** If no, the run still works; some stages report
   "cannot execute" instead of a number.

Write both to `.testing/preferences.json` immediately, with the date and a 30-day expiry
on the install permission. Never ask either again.

## Step 3 — Environment safety check

```
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/env_safety_check.py" "<repo>" --out .testing/env-safety.json
```

Read the verdict.

- **UNSAFE** — stop. Show the findings (file, line, key name, redacted preview — **never
  the secret itself**) and ask whether to continue with execution disabled, or abort. Do
  not run a single test command until the user answers.
- **CAUTION** — report it, proceed, and say which findings you are proceeding past.
- **SAFE** — continue.

This is not ceremony. A test suite that points at a live database deletes real data, and
a report that leaks a key into a PR comment publishes it.

## Step 4 — Profile the project

For each target, in parallel where they are independent. Read the matching
`${CLAUDE_PLUGIN_ROOT}/stacks/<stack>.md` playbook and use its detection signals.

Record: languages and frameworks; monorepo packages and their boundaries; the test
runner and its **exact** invocation; how to run tests for changed files only; test files
by type (unit / integration / contract / E2E); CI configuration and which jobs run
tests; the coverage tool, its command and output format; the mocking library; where
fixtures and factories live; services the tests require; static analysis and type
checking available; and **which directories have no tests at all**.

Write it to `.testing/profile.json`. Every command in there must be one you have seen
in the repo's own config, not one you assumed.

## Step 4b — Show the test command before you run it, once

**Print the exact commands you are about to run and get agreement**, before the first
execution in a repo: whole-suite, changed-files, and coverage, each with the file you
found it in. Name what the runner invokes implicitly — npm runs `pretest` before `test`,
and nobody reads that one.

Running a repo's tests is executing a stranger's code, and the environment check covers
*where* tests point, not *what* they do. Showing the string is what closes the class.
Record the answer in `.testing/preferences.json`: asked once, re-asked when the command
changes.

## Step 5 — Suite health. This is a gate

```
/testsquad:health
```

Coverage from a red or flaky suite is a lie, so no coverage number is produced until the
health verdict exists. Health distinguishes things that look alike and need different
fixes:

- **cannot execute** (missing deps, bad config, absent env var) vs **executes and fails**
- **timing flake** vs **order dependence**
- green-because-passing vs green-because-40%-is-skipped

Verdict is GREEN / AMBER / RED. **RED still produces a useful report** — it just withholds
coverage and makes fixing the suite roadmap item #0. There is no point writing new tests
on a foundation that does not hold.

If dependencies are missing and the user said no to installing, record "cannot execute"
with the exact command that would be needed. Do not install anything.

## Step 6 — Risk map

```
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/risk_map.py" "<repo>" --out .testing/risk-map.json
```

This computes **likelihood** only — relative churn and time-weighted bug-fix recency from
git history. It deliberately does not compute risk, because:

> Risk = Impact × Likelihood, and **Impact is a business judgment you cannot read out of
> a repository.** Multiplying two invented ordinals produces a number that looks precise
> and that nobody can argue with. Keep them separate and visible.

Impact is collected in step 10, from the user.

Check `warnings` in the output. An empty risk map means *no data*, not *no risk*, and
must be reported that way.

## Step 7 — CLAUDE.md

If absent, create it. If present, insert or replace **only** the block between
`<!-- testsquad:start -->` and `<!-- testsquad:end -->`, leaving everything else byte-for-
byte untouched.

**Show the diff and get agreement before writing.**

Keep the block to 15 lines or fewer. `CLAUDE.md` is loaded into context on every single
request in that repo, so every line costs forever. The block holds the test commands and
a pointer to `.testing/`; it does not hold the knowledge itself.

Re-running `/start` replaces the block and never duplicates it.

## Step 8 — Generate the agents

Write to `<repo>/.claude/agents/ts-*.md`, from the templates in
`${CLAUDE_PLUGIN_ROOT}/templates/agents/`, filled in from the profile.

**Generate only what fits.** A library with no UI gets an API/integration agent, not a
Playwright one. Three agents that match the stack, each with a sentence saying why, beat
eight generic ones — and the generic ones already exist in a dozen other marketplaces.

Every generated file is **fully regenerable**: stamp it with the plugin version and put
nothing hand-written or learned inside it. If someone needs to hand-edit an agent, they
copy it to `ts-<name>.local.md`, which `/testsquad:upgrade` never overwrites and which
takes precedence over the generated file. Regeneration that silently discards someone's
work is how a tool stops being trusted. All learning lives in `.testing/`, which is
what lets `/testsquad:upgrade` rewrite these safely later.

Derive the repo's conventions into `.testing/conventions.md` — a numbered rules file the
agents *enforce*. Separating the rules from the enforcer means `/start` can refresh the
rules without touching an agent.

## Step 9 — Diagnose

```
/testsquad:diagnose
```

Gated on step 5. Coverage, the test-type matrix, and the gaps against the risk map. Every
figure is labelled with the health verdict it was measured under — a 78% measured under
AMBER is not the same claim as 78% under GREEN.

Mutation appears as an available-but-not-run column, with the command, because mutation
runs on demand only.

Never report coverage as a headline score. Report **which high-risk code is untested**.
Coverage is a floor detector: uncovered code is definitely untested, but covered code may
still be unverified.

## Step 10 — The acceptance contract

Propose `.testing/acceptance-contract.md` and **get explicit approval before it becomes
the baseline**. It holds:

- Per-tier quality gates (critical / standard / low)
- Which test types are mandatory versus assess-only
- Maximum suite duration, allowed flake rate, allowed skip count
- The **Impact** scores — ask the user to name the handful of features where failure
  costs money, loses data, or breaks the law. Force scarcity: if everything is critical,
  nothing is.

**Every line must be checkable by a command.** Write *"`billing/tax.ts` branch coverage
≥90% and every new test fails under fail-proof"*, never *"tax is well tested"*. Lowering a
threshold later is recorded in `.testing/decisions.md`, so standards cannot quietly erode.

## Step 11 — The roadmap

Rank by leverage: risk reduced ÷ effort. Static analysis and type checking usually rank
first because they are the cheapest wins on the board.

Each item carries: what to do, which agent does it, the expected change, and a
**command-checkable done-when line**.

Item #0 is a RED health verdict, if there is one.

## Step 12 — Report

Write the dashboard to `.testing/reports/diagnostic-<date>.html`, following
`${CLAUDE_PLUGIN_ROOT}/templates/report/RULES.md`. Lead with the health verdict and the
contract scorecard. Offer to install the PR comment Action.

Then tell the user, briefly: what you found, the one thing to do first, and that every
stage is re-runnable on its own.

---

## Scale: large monorepos

Before any long stage, rank packages by churn, process the top N, and **list the rest as
not-yet-analyzed rather than silently omitting them**. Show the estimated time and token
cost before starting. `--scope <paths>` narrows the run. Later runs are incremental,
reusing cached results for packages whose files have not changed.

## What gets written

| Path | Committed? |
|---|---|
| `CLAUDE.md` (marker block only) | yes |
| `.claude/agents/ts-*.md` | yes |
| `.testing/schema-version` | yes |
| `.testing/profile.json` | yes |
| `.testing/acceptance-contract.md` | yes |
| `.testing/preferences.json` | yes |
| `.testing/risk-map.json` | yes |
| `.testing/health-baseline.json` | yes |
| `.testing/coverage-state.md` | yes |
| `.testing/known-issues.md` (each entry dated, with a review date) | yes |
| `.testing/conventions.md` | yes |
| `.testing/lessons/` | yes |
| `.testing/decisions.md` | yes |
| `.testing/tasks/` | yes |
| `.testing/tasks/current` — in-flight marker the guard hooks read; transient | no |
| `.testing/bugs/` — one file per reported bug, so it survives the report | yes |
| `.testing/roadmap.md` | yes |
| `.testing/verification-log.md` | yes |
| `.testing/env-safety.json` (redacted) | yes |
| `.testing/feedback.json` — "not useful" clicks, feeds the EFP rate | yes |
| `.testing/reports/` | **gitignored** |

Writes are atomic (temp file, then rename) and there is one file per item, so parallel
agents cannot clobber each other or leave a half-written baseline.

Add `.testing/reports/` and `.testing/tasks/current` to `.gitignore` — the knowledge is worth committing, the
generated HTML is not.

## Re-running

`/start` is idempotent. The `CLAUDE.md` block is replaced, never duplicated; agents are
regenerated; learned knowledge in `.testing/` is preserved. `/testsquad:uninstall` removes
exactly what was added and leaves hand-written content alone — people need a way out
before they will risk the way in.
