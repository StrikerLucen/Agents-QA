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

**Measured 2026-11-12 on a quiet tree, after a full day of work. These numbers were
produced by running the commands above, not by recollection.**

| | |
|---|---|
| Tests | **902, all passing** (1 skip, 2 expected failures) |
| Plugin validation | **passes**, 26 components, 0 warnings, 0 could not be validated |
| Own flake gate on own code | **exit 0 now.** A clone of `e9bdf9b` gives 9 `no-assertion` block findings and exit 1 — all 9 were false positives (assertions delegated to a `timed()` helper the rule cannot see), fixed on this branch |
| Agents / skills / stacks | 7 / 16 / 10 |
| Eval cases | 27, with 81 graders, over 11 claims |
| Evals ever run | **No.** `evals/SCORECARD.md` is still a template |

**Built and measured**: the deterministic core — stdlib Python across nine scripts,
each mutation-tested under the protocol in §5.

**Written but not measured**: the seven agents and sixteen commands are prompts. The
eleven claims in `evals/` are falsifiable but have never been scored. Until
`SCORECARD.md` has numbers, every behavioural claim is **design intent, not evidence**.

### What changed, and why the previous handoff misdirected a day of work

The file you are reading replaced one that was wrong in eight confirmed places. Each
error cost real time, so they are recorded rather than quietly fixed:

| It said | Measured reality |
|---|---|
| 3 failing tests | 5 broken items; one was an *error*, not a failure |
| AMBER health fixture never reached the child process | It always did. One call site omitted `env=onboard(...)`, so no consent record existed |
| `EMAIL_RE` ReDoS, ~20 min on a 2MB line | Already fixed. The live quadratic was `BARE_HOST_RE`, and the cost was per *line*, not per file |
| 5 HIGH prompt findings open | 4 were already closed |
| 9 unsourced claims needing owner decisions | 0 of them. All 7 enumerated were in `docs/RESEARCH.md` §15/§20 the whole time |
| 0 block findings on own code | 9, on a pristine clone, all false positives from our own gate |
| detector `unreadable-sensitive-file` | No such detector. It is `unreadable_config` |
| Python 3.9 unverifiable here | 3.9.6 is installed; everything was re-verified on it |

**The lesson is the one §5 already states: re-measure before trusting this file.** It
applies to this version too.

## 2. Failing tests

**None.** 814 pass. The three named in the previous handoff are fixed, and the two it
did not know about are too.

## 3. What was closed today

- **The missing fixture.** `examples/fixtures/node-ts/.env` is MANIFEST ground truth
  (`NODE-CONFIG-01`) that had never been committed. Every clone was missing it, so
  `env_safety_check.py` returned **SAFE** for the fixture that exists to be UNSAFE.
  Restored, now tracked, and `tests/test_fixture_manifest.py` asserts all 71 MANIFEST
  items resolve on disk.
- **Fixture absence now fails, loudly.** `require()` used to *skip*, which is how the
  above rotted unnoticed. It now fails and names the path; set
  `TESTSQUAD_ALLOW_MISSING_FIXTURES=1` for local concurrent work, never in CI.
- **Four silent-success defects**, the class this project exists to catch, found in its
  own code: `session_context.py` exiting 0 with both streams empty; `pr_comment.py`
  printing "No new findings on this change." when nothing ran; `flake_smells.py`
  truncating a 1.9MB line and reporting a clean scan of 1,898,000 characters it never
  read; `validate_plugin.py` losing a whole check's verdict for all 26 components when
  one was unreadable.
- **Two real DoS paths**, both measured: `BARE_HOST_RE` (17.6s → 2.2s on an 800KB
  `.env`) and a quadratic in `arid_nodes._single_statement_body` (12.2s → 0.15s at
  8,005 lines, inside a ceiling that was supposed to bound it).
- **Coverage honesty.** UTF-16 via stdin, negative JaCoCo counters, and `hit > found`
  now yield `null` with a named warning instead of 0%, 100%, 200% or 450%.
- **All 7 `--out` writers are atomic**, so `skills/start/SKILL.md`'s promise is kept.
  `atomic_io` itself had been narrowing every destination to 0600.
- **All 7 agents are reachable.** `project-analyst`, `tech-scout` and `reporter` were
  dispatched by nothing.
- **S2-3, the consent gate** (was §4, now done). `/start` step 5 shows the verbatim
  commands and says plainly that agreeing runs the repo author's code as you, on this
  machine. Step 4 profiles statically first, so nothing from the repo executes before
  the gate.

## 4. Next, in priority order

1. **Run the evals.** `cd evals && claude plugin eval`, then fill in `SCORECARD.md`.
   This is still the only thing that converts the README's claims from intent into
   evidence. **Blocked:** the `claude` CLI is not installed on this machine.
2. **Run `claude plugin validate --strict`.** Never done. `scripts/validate_plugin.py`
   is a stand-in, not a substitute. Same blocker.
3. **Decisions that are yours, not an agent's** — each was deliberately left:
   - *(closed)* `RecursionError` escaping `ast.parse` is fixed at all three production
     sites — `arid_nodes.py`, `flake_smells.py`, `project_state.py` — each reporting an
     explicit "could not parse" signal rather than falling back silently. CI found it;
     every local macOS run passed, because the depth is the C stack, not the Python
     limit. Measured on the 3.9.6 floor: there is **no RecursionError window at all**,
     it goes from accepting 260,937 frames straight to SIGSEGV, so raising the limit
     converts a catchable exception into an uncatchable crash. Two unguarded sites
     remain in `tests/test_integration.py` and `tests/test_fixture_manifest.py`, both
     parsing the test module's own source — a clarity nit, not this defect class, since
     a raise there is a loud test error rather than a silent wrong number.
   - **`flake_smells`'s `no-assertion` rule cannot see an assertion delegated to a
     same-file helper.** 9 of 9 findings on this repo were false positives: a **100%
     rate** against the project's own ~10% switch-off threshold. The fix is intra-file
     helper resolution — a real feature.
   - **`flake_smells` emits the path as `"path"` in `summary["skipped"]` and `"file"`
     in `skipped_files`.** It crashed a consumer today. Unifying is a schema change to
     `testsquad.flakesmells/1`.
   - **A shared reader for the consent record.** The contract is now pinned
     (`consent.execute_repo_commands` in `.testing/preferences.json`, four states,
     fail-closed) and `tests/test_consent_contract.py` guards it against drift. But it
     is still prose executed by a model. A `scripts/consent.py` exposing
     `read_consent(path) -> (state, covered_commands, reason)` would be the only way to
     guarantee the writer and the reader agree — estimated ~100 lines plus tests. The
     catch, stated by the agent that declined to build it: the analyst is a prompt, not
     a process, so nothing *forces* it to call the reader, and a reader nobody must
     invoke is a second contract on top of the first.
   - **The consent contract is pinned on one side only.** `tests/test_consent_contract.py`
     now asserts the analyst's scope-and-expiry rules (granted + unexpired + command
     listed → those commands only). But `skills/start/SKILL.md` is the side that *prints*
     the command list the user agrees to, and nothing asserts that what it shows is the
     list the analyst later matches against. Two halves of one gate, pinned separately.
4. **Smaller, well-specified cleanups** — each found and reported today, none urgent:
   - `test_robustness.py:386/529/553/1279/1483` skip when `arid_nodes.py` is absent.
     Those are missing-*production-code* skips, not platform guards, and should fail
     like the fixture ones now do.
   - `pr_comment.py`'s `health-baseline.json` and `coverage-diff.json` have no wrapper
     key, so a `{}` there is still counted as an input that ran. Closing it means
     deciding which field is mandatory — a product call, not a guess.
   - A mode-000 file in `templates/agents/*.tmpl` still passes validation; a UTF-16
     agent `.md` validates clean. The second needs the real CLI to judge, not a guess.
   - M15's last piece: the "What gets written" inventory in `skills/start/SKILL.md`
     is missing `mutation-plan.json`, `tooling-radar.md`, `coverage-raw.json` and
     `tasks/current`. M13, M14, M17 and M15's naming and CI halves are closed.
   - **Four files are at their context ceiling** — re-measure before editing any of
     them, the numbers move: `templates/ci/testsquad-pr.yml` (**5 bytes**),
     `agents/automation-engineer.md`, `templates/agents/ts-qa-gatekeeper.md.tmpl`,
     `skills/start/SKILL.md`. The next prose edit to any of them breaches the budget.
     This is now a structural signal rather than four coincidences: the ceilings may be
     wrong for these roles, or these files need a deliberate dedup pass. Two
     restructuring levers were identified and not pulled, both in `docs/PROMPT-REVIEW.md`;
     a third was used successfully on `skills/start/SKILL.md`, which paid for the entire
     consent contract out of duplication it was already carrying.

## 5. How work is done here

Not style preferences. Each exists because its absence caused a real defect in this
repo, and most were caught by the project's own tools.

- **Prove the test can fail.** Break the production code, confirm red, restore.
  **Purge bytecode → mutate → run → restore → verify by SHA-256**, absolute paths
  throughout. A failed `cp` once silently invalidated an entire mutation run; a later
  agent caught it by comparing SHA-256 against a pristine copy. Then three agents in
  one session hit the next layer: a stale `__pycache__` serves the pre-mutation
  bytecode, so the mutant reports GREEN *while the hash check passes*. Clear
  `scripts/__pycache__` and `tests/__pycache__` before every invocation and pass `-B`.
  Add a **control mutant** (comment-only, hash changes, behaviour does not) that must
  stay GREEN — otherwise a no-op mutation is indistinguishable from a blind test.
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
