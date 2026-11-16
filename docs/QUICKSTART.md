# Quickstart

Ten minutes, in this order: install, check it on code whose answers are already
known, then point it at your own repo. If you only have two minutes, skip to
[the fixtures](#2-try-it-on-a-fixture-first) — that part costs no tokens and
tells you more than anything written here.

---

## 1. Install

```
/plugin marketplace add thomaspino/testsquad
/plugin install testsquad
```

**Prerequisites:** Python 3.9+ and `git`. That is the whole list.

The analysis scripts (`scripts/*.py`, ~5,600 lines) use the Python standard
library only — no `pip install`, no virtualenv, no lockfile. `git` is needed
because the risk map is computed from `git log`; without a history you get an
empty risk map, reported as "no data", not "no risk". Everything else it runs is
your project's own tooling, invoked exactly as your own config invokes it.

---

## 2. Try it on a fixture first

`examples/fixtures/` holds three small repos — `node-ts/` (TypeScript, Vitest),
`python-pkg/` (pytest), `java-min/` (JUnit 5) — where every defect was planted
on purpose and written down in advance. Dependencies are deliberately not
installed in any of them.

`examples/fixtures/MANIFEST.json` records the expected verdict for every item:
12 seeded bugs (3 each at S1–S4), **12 decoys** (correct code that looks wrong —
reporting one is a failed eval), 16 weak tests, 13 good tests, 12 flaky tests,
4 skipped, and 2 config files (one unsafe, one safe).

### The zero-token check

Clone the plugin repo and run the deterministic parts yourself — no model, no
API cost:

```sh
cd examples/fixtures
python3 ../../scripts/env_safety_check.py node-ts      # must print UNSAFE
python3 ../../scripts/env_safety_check.py python-pkg   # must print SAFE
python3 ../../scripts/risk_map.py node-ts --since ""   # src/cart.ts must rank first
python3 ../../scripts/flake_smells.py node-ts python-pkg java-min
```

`flake_smells.py` must fire on every weak test and report **nothing** on
`tests/good.spec.ts`, `tests/test_good.py` and `GoodTest.java`. A finding on a
good test is a false positive, which matters more here than a miss.

To check the scripts themselves, from the repository root:

```sh
cd examples/fixtures && ./regen_git_history.sh   # required on a fresh clone
cd ../../tests && ls test_*.py | sed 's/\.py$//' | xargs python3 -m unittest
```

Expect **green, with one expected failure**, in well under a minute. The *count* is
deliberately not printed here: it went 922 → 957 → 966 over a single afternoon on
2026-11-16 while this page was being written, so any number on this line would be wrong
before you read it. The command is the answer; the number is not.

**Do not use `python3 -m unittest discover -s tests`.** It is not equivalent: it reports
10 failures on a clean tree, because the test modules import each other by bare module
name and `discover` gives them a different import root. The `ls | xargs` form above is
the supported one.

### The agent check

Then point `/testsquad:start` at `examples/fixtures/node-ts`. Two things are
worth watching:

- `node_modules/` is absent on purpose. The correct answer is **"cannot
  execute"**, with the exact command that would be needed — not an invented
  pass rate.
- It must stay silent on all 12 decoys: a bare `except:` that ends in `raise`,
  a `+ size` bound that is right because the range is half-open, `== 0.25` on a
  value exactly representable in binary. Each carries a `proof` field in the
  manifest saying why it is correct.

Do not fix anything in the fixtures and do not install dependencies into them.
"Cannot execute" is a fixture state, not a bug.

---

## 3. Your first real run

```
/testsquad:start                 # or: /testsquad:start packages/api packages/web
```

### What it asks — once

One `AskUserQuestion`, two questions, at the start:

1. **Depth** — quick / standard / deep / exhaustive. Defined as reproduction
   budget per finding: ~2 min, ~10 min, ~45 min, or until exhausted. Standard is
   the default.
2. **May I install dependencies?** If you say no, the run still completes;
   stages that need deps report "cannot execute" with the command instead of a
   number.

Both are written to `.testing/preferences.json` and never asked again. The
install permission carries a 30-day expiry and is revalidated early if the
lockfile, the CI config or the hosts in `.env` change. `/testsquad:preferences`
shows everything remembered, with dates, and lets you withdraw any of it.

### What it refuses to do

- **Run nothing before the safety check.** Step 3 is `env_safety_check.py`. On
  UNSAFE it stops and asks before a single test command executes. A suite
  pointed at a live database deletes real data.
- **Run nothing from your repo before you have seen it and agreed.** Step 4
  profiles the project without executing any of it; step 5 then prints the
  commands it found — whole-suite, changed-files, coverage, any dependency
  install — verbatim, names what the runner invokes implicitly (`npm` runs
  `pretest` before `test`), and says plainly that agreeing runs code written by
  whoever wrote that repository, as you, on this machine. On a no, every later
  stage reports "cannot execute" and the command it would have run.
- **Install nothing without permission**, even when that leaves a stage
  unanswerable.
- **Edit no product code.** Push nothing, merge nothing.
- **Print no secret** — key name and a redacted preview (`A3f9...(40 chars)`),
  never the value.

### The stages

Targets → questions → safety → profile → **agree to execute** → health → risk
map → `CLAUDE.md` → agents → diagnose → contract → roadmap → report. Each is a
separate command you can re-run on its own; `/testsquad:start` only chains them.

Four stages stop for you: the safety check if it returns UNSAFE; the agreement
to execute the repo's own commands (step 5, before anything of its runs); the
`CLAUDE.md` block, with the diff shown first; and
`.testing/acceptance-contract.md`, which includes naming the handful of features
where failure costs money.

### How long, and what it costs

There is no honest fixed duration — it is dominated by your own suite, which
`/testsquad:health` runs **3 times by default** (`--flake N` to raise it) to
measure flakiness. Budget roughly three suite runs plus a coverage run, plus
model time. On a monorepo it prints its estimated time and token cost before any
long stage and waits.

The plugin's own context cost is measurable, so here it is measured rather than
estimated. All figures below are from 2026-11-16, using the same `size // 4` estimator
`scripts/validate_plugin.py` enforces its budgets with.

The always-on session preamble: **555 characters, ~139 tokens**, on a `.testing/` holding
a stack profile, a health baseline, three conventions and one known issue. It scales with
those files, so your repo will differ — **the number that does not differ is the ceiling**,
which `tests/test_integration.py` enforces at **2,000 characters** (anchor: *"the session
preamble is {0} characters and is charged on every request in this repo"*), fed ten
conventions and six 200-character blockers to prove the caps hold under hostile input.

A `/testsquad:test` run loads the skill (**1,890 tokens**) plus the report rules
(**957**) plus the 2–4 agents it selects, which range from **2,398** (`unit-test-engineer`)
to **3,486** (`automation-engineer`). That is **~7,900 tokens at the two-agent floor and
~15,300 at the four-agent ceiling** — not a single figure, because the whole point of
selecting 2–4 specialists rather than all 7 is that the cost varies.

Reproduce any of these yourself:

```sh
python3 -c "import sys; sys.path.insert(0,'scripts'); import validate_plugin as v; \
print(v.estimate_tokens('agents/automation-engineer.md'))"
```

### When the answer is bad news

A RED verdict does not produce a shorter report. It produces the full health
report and withholds the number you came for:

```
Suite health: RED

The suite passes, but 112 of 280 tests never run (40% skipped), and
tests/orders.e2e.spec.ts flipped pass→fail→pass across 3 identical runs
on the same commit.

  python3 .../flake_smells.py test/ --format text
  npx vitest run --reporter=verbose

Coverage: not measured.
Coverage from a red or flaky suite is a lie, so no coverage number is
produced under this verdict. "Not measured" is the answer; 0% is not.

Roadmap item #0: unskip or delete the 112 skipped tests.
```

Two things in that output are the tool's own rules turned on itself. "Not
measured" never becomes a zero. And the flake claim states its own limit: three
runs catch a 50% flake most of the time and a 5% flake about 14% of the time
(detection power is `1 − (1−p)^N`), so it reports coarse flakiness rather than
claiming your suite is stable.

---

## 4. What lands in your repo

| Path | Committed? |
|---|---|
| `CLAUDE.md` — only between `<!-- testsquad:start -->` / `<!-- testsquad:end -->`, ≤15 lines | yes |
| `.claude/agents/ts-*.md` — generated, version-stamped, regenerable | yes |
| `.testing/profile.json`, `risk-map.json`, `health-baseline.json`, `env-safety.json` (redacted) | yes |
| `.testing/acceptance-contract.md`, `conventions.md`, `decisions.md`, `known-issues.md`, `roadmap.md` | yes |
| `.testing/preferences.json`, `tasks/`, `bugs/`, `lessons/`, `verification-log.md`, `feedback.json` | yes |
| `.testing/reports/` — generated HTML | **gitignored** |

The split is deliberate: `.claude/agents/ts-*.md` is regenerated by
`/testsquad:upgrade` and holds nothing learned, so hand-edits there would be
lost — copy an agent to `ts-<name>.local.md` instead, which upgrade and
uninstall both leave alone. Everything learned lives in `.testing/`, which is
never regenerated.

### Getting out

`/testsquad:uninstall` shows the full diff first, then removes only the
`CLAUDE.md` marker block — **everything outside the markers stays byte-for-byte,
including whitespace** — plus `ts-*.md` files carrying a testsquad version
stamp, and the `.testing/reports/` line in `.gitignore` if it added it.
`.testing/` is kept by default; you are asked explicitly. Any `ts-*.md`
hand-edited since generation is flagged and not deleted without asking. If the
`CLAUDE.md` markers are missing or damaged it stops rather than guessing the
boundary. Nothing is committed or pushed; you review it with `git diff --stat`.

---

## 5. Three commands for day one

**`/testsquad:advise "<goal>"`** — the front door. Say what you want to be sure
of in your own words: *"I want to be sure checkout never double-charges."* The
answer always ends with three things: what it costs (hours, added suite seconds,
maintenance), a cheaper alternative that gets most of the value (often a
database constraint or a type, not a test), and what it will **not** catch. It
reads `.testing/` first; if the profile is missing it says so rather than
implying it looked.

**`/testsquad:health`** — the gate. Can the suite run at all; does it pass; how
much is skipped, `.only`'d or assertion-free; how slow; how flaky across N
identical runs. GREEN / AMBER / RED. Run it before believing any coverage
number, including one you measured yourself.

**`/testsquad:test "<task>" --diff`** — the work loop against the change under
review. It freezes numbered acceptance criteria before any agent starts, picks
2–4 specialists (not all seven), and loops through an independent QA gate that
reads the frozen contract and re-runs every command itself. `--diff` is the
highest-value mode on an active repo: findings delivered against the change
under review get acted on roughly 70% of the time, versus close to zero for a
batch list (Distefano et al., CACM 2019). Work lands on a `testsquad/<task>`
branch; nothing is pushed.

---

## 6. Reading a report

Every finding carries exactly one evidence badge:

| | Means | Where it appears |
|---|---|---|
| ✅ | **Reproduced** — ran it, saw it fail, ran it again, saw it fail again. Command included | main list |
| 👁 | **Observed** — seen failing in a real run, not yet re-run deterministically | main list |
| 📖 | **Code-read only** — found by reading, never executed | collapsed appendix |
| 💭 | **Suspicion** — a hypothesis | collapsed appendix |

Only ✅ and 👁 appear in the main list. 📖 and 💭 go to the appendix **with the
reason they did not qualify** ("reproduced 0 of 10 runs", "no fixture for EU tax
rates") and are **never counted in the bug total**. The headline count is
`count(✅) + count(👁)`, and the report says so. A tool that counts its hunches
has no number you can use.

**Bugs and issues are separate lists.** A bug is a problem in your product; an
issue is a problem with the testing, including anything unreproduced. They are
never added together.

**The main list caps at 10**, ranked by business risk, and prints
`N of M shown`. The remainder is counted, not dropped. On a first run everything
is new so novelty cannot rank: you get findings on critical-risk code only, the
rest in a labelled pre-existing backlog that never blocks a PR. Findings are
clustered by cause — 40 failures from one null guard is one finding with a count
of 40 — and headlined as a consequence, not a taxonomy.

Every finding carries a **"not useful / couldn't reproduce"** control, and any
check whose effective-false-positive rate crosses 10% is demoted out of the main
report automatically.

---

Next: [FAQ.md](FAQ.md) for what this gets wrong, and
[RESEARCH.md](RESEARCH.md) for the source of every number above.
