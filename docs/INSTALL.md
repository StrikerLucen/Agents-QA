# Install

Every command on this page was run on 2026-11-17 and its output is what this page
reports. Where something was *not* run, the page says so rather than implying it.

**Prerequisites: Python 3.9+ and `git`.** That is the whole list. The ten modules in
`scripts/` are standard library only — no `pip install`, no virtualenv, no lockfile.
`git` is needed because the risk map is computed from `git log`.

---

## Which path applies to you

| Situation | Go to |
|---|---|
| You just want to use it | [C — straight from GitHub](#c-straight-from-github) |
| You want to work on it, or need a specific branch | [A — from a clone](#a-from-a-clone) |
| You already have the folder on this machine | [B — from a folder](#b-from-a-folder-you-already-have) |

Most people want C. `StrikerLucen/Agents-QA` became public on 2026-11-17 (`gh repo view
--json visibility`).

---

## A — from a clone

```sh
git clone --branch harden/silent-success-sweep https://github.com/StrikerLucen/Agents-QA.git
claude plugin marketplace add ~/Agents-QA
claude plugin install testsquad
```

Then **restart Claude Code**. Plugins are loaded at startup, so the commands and agents
do not appear in a session that was already running.

Three traps, each of which has actually caught someone:

- **If the clone 404s, check which GitHub account you are logged in as.** The
  repository is public now, so this should not happen — but while it was private, a
  machine authenticated as a different account got a "not found" that reads exactly
  like a typo. `gh auth status` is the check.
- **Pass `--branch harden/silent-success-sweep`.** The default branch is `main`, and
  `main` predates the hardening work: missing fixture ground truth, a failing flake gate,
  no CI, and tests that could not fail. Omitting the flag installs that instead, with no
  warning. Merging the open pull request is what retires this trap.
- **Leave the folder where you cloned it.** `marketplace add <path>` points at the
  directory; moving or deleting it afterwards breaks the installed plugin.

## B — from a folder you already have

```sh
claude plugin marketplace add /absolute/path/to/Agents-QA
claude plugin install testsquad
```

Restart Claude Code afterwards. The same "leave the folder where it is" rule applies,
and so does the branch one — the marketplace tracks the **working tree**, so checking
out `main` later silently swaps the plugin back to the un-hardened version.

## C — straight from GitHub

```
/plugin marketplace add StrikerLucen/Agents-QA
/plugin install testsquad
```

Then **restart Claude Code**. This is the path for anyone who is not you: no clone, no
`gh auth login`, nothing to build. The repository has been public since 2026-11-17.

This pulls the **default branch**. That is only safe once the hardening work is on
`main` — see the branch trap in path A.

---

## `/plugin` versus `claude plugin`

`/plugin ...` is a **slash command typed inside Claude Code**. Pasting it into a shell
gives you:

```
zsh: no such file or directory: /plugin
```

From a shell the equivalent is `claude plugin ...`. If `claude` is reported as not
found, it is probably installed at `~/.local/bin/claude`, which is not on the default
`PATH` — use the full path or add that directory to `PATH`.

---

## Check it worked

```sh
claude plugin list
```

Expect `testsquad@testsquad`, version 0.1.0, status enabled. Measured on 2026-11-17 on
the author's machine, scope `user`.

Then, from a fresh clone, both validators:

```sh
python3 scripts/validate_plugin.py . --strict
claude plugin validate .
```

Measured on a clean clone of `harden/silent-success-sweep` at `3eda681`:

| | Result |
|---|---|
| `validate_plugin.py . --strict` | passed — 26 components, 0 warnings, 0 could not be validated |
| `claude plugin validate .` | passed |

These two are **not** redundant and neither is a superset of the other. The CLI
validates the *marketplace manifest* and says nothing about the other 25 components;
the in-repo script checks those, plus context budgets.

---

## What it costs you in context

```sh
claude plugin details testsquad
```

Measured 2026-11-17, CLI 2.1.276:

| | |
|---|---|
| Always-on | **~3,121 tokens**, added to every session |
| Components | 16 skills, 7 agents, 1 hook, 0 MCP servers, 0 LSP servers |
| Heaviest on invoke | `automation-engineer` ~4.5k, `debugger` ~4.1k, `reporter` ~4k, `start` ~4k |

The always-on figure is the one that matters: it is the rent the plugin charges on every
request whether you use it or not. The per-component figures are only paid when that
component is invoked.

Note for contributors: the CLI's projection puts five components **above** the ceilings
`scripts/validate_plugin.py --strict` enforces (3,500 per agent, 3,000 per skill) — but
those ceilings are `bytes // 4`, not a token count, and this corpus runs ~2.86
chars/token, so the gate undercounts by roughly 30% while
the script reports all 26 as passing. The two count differently; which is authoritative
has not been established here. Recorded rather than resolved.

---

## The zero-token check

Before spending anything on a model, run the deterministic parts against fixtures whose
answers are written down in advance:

```sh
cd examples/fixtures
python3 ../../scripts/env_safety_check.py node-ts
python3 ../../scripts/env_safety_check.py python-pkg
python3 ../../scripts/flake_smells.py node-ts python-pkg java-min
```

No `#` comments in that block on purpose: zsh leaves `interactive_comments` off, so a
pasted trailing comment arrives as an argument and argparse rejects the line. Expected
results, measured 2026-11-16:

| Command | Expected |
|---|---|
| `env_safety_check.py node-ts` | `UNSAFE`, 3 critical — and **exit 1**, which is the gate working, not a crash |
| `env_safety_check.py python-pkg` | `SAFE`, 0 findings, exit 0 |
| `flake_smells.py` | findings on the weak tests, **nothing** on the three good ones |

A finding on `good.spec.ts`, `test_good.py` or `GoodTest.java` is a false positive, and
a false positive matters more here than a miss.

`risk_map.py` is left out of that list on purpose — see the warning below.

> **`risk_map.py` on a fresh clone reads the enclosing repository's history, and now
> says so.** The fixture repos carry no `.git` of their own until
> `examples/fixtures/regen_git_history.sh` has been run, and a directory with no history
> of its own falls through to the *enclosing* repository's. Measured 2026-11-17 that was
> silent: `risk_map.py node-ts` returned ten commits dated 2026-11-12 to 2026-11-16 —
> the plugin's own — ranked `.gitignore` at `relative_churn` 8.5, and said
> `warnings: []`. `rev-parse --is-inside-work-tree` is true for any directory nested in
> any repo, so on its own it cannot tell "scoped to a subdirectory of a monorepo", which
> is correct, from "borrowed a history that is not this project's", which is not.
>
> Since 2026-11-18 every run records `git_root` (the repository the commits were read
> from) and `path_in_repo`, and warns, in the JSON `warnings` array and on stderr,
> whenever `git_root` is not the directory you asked for. Which of the two cases you are
> in is still *your* call — the plugin cannot know your intent — but it is no longer
> invisible. The same change scopes the history to the subdirectory, so the aliased
> `.gitignore` is gone and a genuine monorepo package now ranks its own files instead of
> none. **Run `regen_git_history.sh` first anyway**; `src/cart.ts` then ranks first, with
> `warnings: []`.

---

## Using it

`/testsquad:start` in the repository you want tested. It profiles the project
statically, takes a suite-health baseline, builds a risk map, and writes agents tailored
to that stack into `.claude/agents/`.

**It asks before it runs anything your repository supplied.** Step 5 prints your
project's own test, coverage and install commands verbatim and states that agreeing runs
the repository author's code as you, on this machine. Nothing executes before that gate,
and the gate fails closed — a missing or malformed consent record is treated as *no*.

---

## Before you rely on it

The deterministic core in `scripts/` is tested and gated. The agents and commands are
**prompts**, and ten of the eleven behavioural claims in `evals/` have never been
scored. The one that was scored came back **negative** — the plugin did worse than no
plugin, mean Δ −0.20, not significant at n=10 per arm. See
[`../evals/RUN-2026-11-16-partial.md`](../evals/RUN-2026-11-16-partial.md) and the
opening section of [`../README.md`](../README.md).

---

## Uninstall

Two different things, both of which exist:

| | |
|---|---|
| `claude plugin uninstall testsquad` | removes the plugin from this machine |
| `/testsquad:uninstall` | removes what it wrote into a *project* — `.claude/agents/`, `.testing/` |

To also forget the marketplace entry: `claude plugin marketplace remove testsquad`.
Not run on this machine as of 2026-11-17, so the exact output is not recorded here.
