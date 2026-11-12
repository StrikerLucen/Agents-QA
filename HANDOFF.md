# HANDOFF — say "continue" and start here

**State measured 2026-11-12.** Everything below was produced by running it, not by
recollection. Where this file and the code disagree, **the code wins** — re-measure with
the commands in [Verify the state yourself](#verify-the-state-yourself).

---

## 0. First, after cloning

```bash
cd examples/fixtures && ./regen_git_history.sh
```

**Required.** The three fixture repositories are committed as plain files; their git
history is not (an embedded repo does not survive a clone). That history *is* part of
the fixture — `scripts/risk_map.py` is evaluated against it — and several tests in
`test_integration.py` and `test_risk_map.py` depend on it.

## Verify the state yourself

```bash
cd tests && ls test_*.py | sed 's/\.py$//' | xargs python3 -m unittest
python3 scripts/validate_plugin.py . --strict
python3 scripts/flake_smells.py tests scripts --fail-on block
```

`unittest discover` does not work in `tests/`; use the form above. Python 3.9 is the
floor. Everything is stdlib — nothing to install.

---

## 1. Where this stands

| | |
|---|---|
| Tests | **717**, of which **3 fail** (named below) |
| Plugin validation | **passes**, 26 components, 0 warnings |
| Own flake gate on own code | **0 block findings** |
| Agents / skills / stacks | 7 / 16 / 10 |
| Eval cases | 27, with 81 graders, over 11 claims |
| Evals ever run | **No.** `evals/SCORECARD.md` is still a template |

**Built and measured**: the deterministic core — ~5,900 lines of stdlib Python across
nine scripts, each mutation-tested (production code broken deliberately, test confirmed
red, restored).

**Written but not measured**: the seven agents and sixteen commands are prompts. The
eleven claims in `evals/` are falsifiable but have never been scored. Until
`SCORECARD.md` has numbers, every behavioural claim is **design intent, not evidence**.

## 2. The three failing tests

None is a broken feature. Each is a test that outlived the behaviour it asserted,
because an agent was stopped mid-change.

| Test | Why | Fix |
|---|---|---|
| `test_binary_file_is_skipped_not_crashed_on` *(env_safety_check)* | A skipped sensitive file now raises its own finding, by design. The test still asserts `findings == []` | Assert the `unreadable-sensitive-file` detector instead |
| `test_single_ten_megabyte_line` *(robustness)* | Size ceilings partly landed; this test documents the unbounded behaviour | Finish the ceiling work in `flake_smells.py` / `arid_nodes.py`, then update |
| `test_the_chain_runs_in_order_and_each_link_feeds_the_next` *(integration)* | The end-to-end chain reaches `session_context.py` and gets empty output. The consent gate is working; the test's AMBER health fixture is not reaching the child process | Trace the `onboard()` helper in `test_integration.py` — the marker is written but the health file may not be seeded for that case |

## 3. Next, in priority order

1. **Fix the three tests above.** Half an hour, and the suite goes green.
2. **Finish the three interrupted hardening tasks** — each was verified partially landed:
   - *Coverage + validator*: UTF-16 lcov silently reporting zero coverage; a JaCoCo
     counter missing `missed` reporting 100%; BOM handling; five crashes in
     `validate_plugin.py` that abort the whole run on one bad component.
   - *Atomic writes*: `scripts/atomic_io.py` landed. **Check which callers were
     converted** — `skills/start/SKILL.md` promises atomic writes, so the claim is only
     true once every `--out` writer uses it. Then the size ceilings, then the quadratic
     `EMAIL_RE` ReDoS in `env_safety_check.py` (a 2MB line extrapolates to ~20 minutes,
     and this gate runs *before* the test command, so hanging it does not fail safe).
   - *Prompts*: 5 HIGH findings in `docs/PROMPT-REVIEW.md`. The sharpest: **nothing
     dispatches `project-analyst`, `tech-scout` or `reporter`** — three of seven agents
     are unreachable.
3. **Run the evals.** `cd evals && claude plugin eval`, then fill in `SCORECARD.md`.
   This is the only thing that converts the README's claims from intent into evidence.
4. **Run `claude plugin validate --strict`.** Never done — the CLI was not on the
   machine this was built on. `scripts/validate_plugin.py` is a stand-in, not a substitute.
5. **Enable CI — one command, and it needs you, not an agent.** The workflow is
   written but parked at `templates/ci/github-ci.yml`, because pushing to
   `.github/workflows/` requires a token with `workflow` scope and the session that
   built this did not have one. On your machine:

   ```bash
   mkdir -p .github/workflows
   git mv templates/ci/github-ci.yml .github/workflows/ci.yml
   git commit -m "Enable CI" && git push
   ```

   It runs the suite on Python 3.9/3.11/3.12, the plugin validator, and the project's
   own flake gate against its own code. **Expect it red on the first run** — the three
   failing tests in §2 are real. A red CI that tells the truth is the point; do not
   add `continue-on-error`.

   Issue templates and the PR template are already in `.github/` and pushed.

## 4. Decisions that are yours, not an agent's

From `docs/SECURITY-REVIEW.md` (609 lines — **read this before publishing**):

- **S2-3, the reviewer's single recommendation before going public.** `/start` should
  show the verbatim test command it discovered and get agreement before the first run.
  Written into `skills/start/SKILL.md` step 4b, never exercised. A malicious `test`
  script currently passes the gate as a *hermeticity* note at CAUTION, which `/start`
  treats as "report it, proceed". Running a repo's tests is executing a stranger's code;
  no `curl | sh` detector list will ever be complete.
- **S3-2.** Only `tech-scout` treats input as untrusted. The other six agents read repo
  content with no equivalent rule.

From `docs/CITATION-AUDIT.md`: **9 unsourced claims remain** (was 17; 8 were hedged).
The 9 are load-bearing research figures — mutation-operator productivity, the
qodo-cover/$70M claim, "200+ function families". They need **sourcing decisions, not
hedging**: either add the citation to `docs/RESEARCH.md` or restate them as design
choices. Do not invent a source.

## 5. How work is done here

Not style preferences. Each exists because its absence caused a real defect in this
repo, and most were caught by the project's own tools.

- **Prove the test can fail.** Break the production code, confirm red, restore. Use
  **absolute paths and verify the restore** — one agent's entire mutation run was
  silently invalidated by a failed `cp`, and a later agent caught it by comparing
  SHA-256 against a pristine copy. Do that.
- **Never state a number you did not measure.** `null` / "not measured" is valid; `0`
  is not. Most of the worst bugs found here were a malformed input rendering as a
  confident wrong number.
- **Silence must never look like success.** "I could not read it", "nobody ran this"
  and "there is nothing wrong" must be distinguishable in every output. A safety gate
  returning SAFE about a file it never read was the worst defect of the build.
- **Every non-obvious claim traces to `docs/RESEARCH.md`.** Hedge it or cite it; never
  invent a source.
- **A false positive costs more than a miss.** Above ~10% effective false positives a
  check gets ignored entirely, taking everything else with it.
- **Context budgets are enforced** by `validate_plugin.py --strict`.
- **Never edit product code to make a test pass.** Report the bug.

## 6. Reading order for a fresh session

1. `CLAUDE.md` — loads automatically; the short version of this file
2. this file
3. `docs/RESEARCH.md` — the citation ledger behind every design decision
4. `docs/SECURITY-REVIEW.md`, `docs/PROMPT-REVIEW.md`, `docs/CITATION-AUDIT.md` — the
   three audits, each with open findings
5. `docs/QUICKSTART.md`, `docs/FAQ.md` — what a user sees
