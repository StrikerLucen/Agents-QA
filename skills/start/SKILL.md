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

**Everything here is a chain of the single-job commands**, each re-runnable on its own
afterwards. Nothing is a black box, and you should say so.

## Before anything: the two rules that govern this whole run

**Ask the configuration questions once**, batched, at step 2. The run is then unattended
*except at four approval gates*: safety (3), executing this repo's code (5), `CLAUDE.md`
(8), the contract (11). Nothing else interrupts, and re-asking something already
answered is a defect — check `.testing/preferences.json` first.

**Nothing runs until it is safe and agreed.** No test command, no coverage run, no
dependency install, until step 3 has passed and step 5 is agreed. A suite you have
never seen may point at production, and running it is executing its author's code.

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
2. **May I install dependencies?** On a no, nothing is installed and every stage that
   needed it reports "cannot execute" with the exact command it would have needed.

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

## Step 4 — Profile the project, running none of it

**Dispatch the `project-analyst` agent** — one per target, in parallel where the targets
are independent. It holds the method and writes `.testing/profile.json`, and it is the
only agent permitted to read `${CLAUDE_PLUGIN_ROOT}/stacks/<stack>.md` for its detection
signals, because everything after onboarding reads the small profile instead.

**This pass is static**: nothing the repo supplied runs. Its glob-resolving probes are
deferred to step 6.

Check what comes back: every command traced to its config file, every unmeasurable
field `null` with a reason, and which consent state it read.

## Step 5 — Agree to execute this repo's code. This is a gate

Nothing the repository supplied has run yet. **Print those commands verbatim and get
agreement** before the first one does: whole-suite, changed-files, coverage, and any
dependency install. Give the file you found each in, and name what the runner invokes
implicitly: npm runs `pretest` before `test`, and `install` runs `postinstall`.

Say what is being agreed to: **these run code written by whoever wrote this repository,
as you, on this machine.** It is not a configuration value being approved. Step 3 covers
*where* tests point, not *what* they do, so repeat its findings here beside the command.
Showing the string is what closes the class; a detector list never will.

Record it under `consent.execute_repo_commands` in `.testing/preferences.json`, in the
shape `agents/project-analyst.md` defines under "The consent record" — the only
definition; never restate it here. A no is `"refused"`, a different state from never
having been asked. **A missing or malformed record is not consent**: it fails closed to
the static pass and is reported as unreadable, never as agreement.

## Step 6 — Suite health. This is a gate

With that recorded, re-dispatch `project-analyst` for the probes deferred at step 4.
Then:

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

## Step 7 — Risk map

```
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/risk_map.py" "<repo>" --out .testing/risk-map.json
```

This computes **likelihood** only — relative churn and time-weighted bug-fix recency from
git history. It deliberately does not compute risk, because:

> Risk = Impact × Likelihood, and **Impact is a business judgment you cannot read out of
> a repository.** Multiplying two invented ordinals produces a number that looks precise
> and that nobody can argue with. Keep them separate and visible.

Impact is collected in step 11, from the user.

Check `warnings` in the output. An empty risk map means *no data*, not *no risk*, and
must be reported that way.

## Step 8 — CLAUDE.md

If absent, create it. If present, insert or replace **only** the block between
`<!-- testsquad:start -->` and `<!-- testsquad:end -->`, leaving everything else byte-for-
byte untouched.

**Show the diff and get agreement before writing.**

Keep the block to 15 lines or fewer. `CLAUDE.md` is loaded into context on every single
request in that repo, so every line costs forever. The block holds the test commands and
a pointer to `.testing/`; it does not hold the knowledge itself.

## Step 9 — Generate the agents

Write to `<repo>/.claude/agents/ts-*.md`, from the templates in
`${CLAUDE_PLUGIN_ROOT}/templates/agents/`, filled in from the profile.

**Generate only what fits.** A library with no UI gets an API/integration agent, not a
Playwright one. Three agents that match the stack, each with a sentence saying why, beat
eight generic ones.

Every generated file is **fully regenerable**: stamp it with the plugin version and put
nothing hand-written or learned inside it. All learning lives in `.testing/`.
`/testsquad:upgrade` holds the rest of that rule, including `ts-<name>.local.md`.

Derive the repo's conventions into `.testing/conventions.md` — a numbered rules file the
agents *enforce*. Separating the rules from the enforcer means `/start` can refresh the
rules without touching an agent.

## Step 10 — Diagnose

```
/testsquad:diagnose
```

Gated on step 6. Coverage, the test-type matrix, and the gaps against the risk map. Every
figure is labelled with the health verdict it was measured under — a 78% measured under
AMBER is not the same claim as 78% under GREEN.

Mutation appears as an available-but-not-run column, with the command, because mutation
runs on demand only.

Never report coverage as a headline score. Report **which high-risk code is untested**.
Coverage is a floor detector: uncovered code is definitely untested, but covered code may
still be unverified.

## Step 11 — The acceptance contract

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

## Step 12 — The roadmap

Rank by leverage: risk reduced ÷ effort. Static analysis and type checking usually rank
first because they are the cheapest wins on the board.

Each item carries: what to do, which agent does it, the expected change, and a
**command-checkable done-when line**.

Item #0 is a RED health verdict, if there is one.

## Step 13 — Report

Write the dashboard to `.testing/reports/dashboard-<date>.html`, following
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
| `.testing/coverage-raw.json`, `coverage-diff.json` — `/diagnose` | yes |
| `.testing/findings.json` — `/test`, `/debug` | yes |
| `.testing/gates.json` — `/verify` | yes |
| `.testing/mutation-plan.json` — `/mutate`; `tooling-radar.md` — `/scout` | yes |
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

Add the **gitignored** rows above to `.gitignore` — the knowledge is worth committing,
the generated HTML is not.

## Re-running

`/start` is idempotent. The `CLAUDE.md` block is replaced, never duplicated; agents are
regenerated; learned knowledge in `.testing/` is preserved. `/testsquad:uninstall` removes
exactly what was added and leaves hand-written content alone.
