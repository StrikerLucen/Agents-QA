# Prompt review — adversarial pass

Scope: `agents/*.md` (7), `skills/*/SKILL.md` (16), `templates/agents/*.tmpl` (4), read
against `docs/RESEARCH.md`, `templates/report/RULES.md`, `templates/report/STYLE.md`,
`scripts/*.py`, `hooks/`, `templates/ci/testsquad-pr.yml`.

Counts **as originally filed**: 5 critical, 5 high, 9 medium, 5 low. Read-only review;
nothing in the tree was edited by it.

> **RE-DERIVED FROM THE TREE — 2026-11-16.** This document was written as a record of the
> review session, not re-measured against the repository afterwards, and the branch has
> since closed several of the findings it still described as open. Every finding below has
> been re-checked against the current tree; closed ones are struck out with the command
> that shows them closed, and two were **not findings at all**:
>
> | Finding | Status after re-derivation |
> |---|---|
> | C2 — PR comment reads files nothing writes | **CLOSED** — all three now have named writers |
> | C3 — "nothing creates `.testing/tasks/current`" | **WITHDRAWN — false on this branch and on `main`.** `skills/test/SKILL.md` has always created it |
> | M15 — "What gets written" table incomplete | **CLOSED** — both named files are in the table |
> | M16 — reporter's contract is an unresolvable path | **CLOSED** — now `${CLAUDE_PLUGIN_ROOT}`-qualified |
> | Broken references: `coverage-current.json` | **CLOSED** — the file no longer exists anywhere |
> | Broken references: `arid_nodes.py` "invoked nowhere" | **WITHDRAWN** — `/testsquad:mutate` invokes it |
> | Broken references: 3 agents "referenced by no skill" | **WITHDRAWN** — all three are referenced |
> | L24 and contradiction #18, quoting `README.md:25` | **WITHDRAWN — fabricated.** No README existed when the quotation was attributed to one. A `README.md` was added 2026-11-16, after the withdrawal; it does not contain the quoted line. See L24 |
> | `hooks/examples/README.md` | **does not exist** — that directory holds three `.json` files |
>
> Line numbers into `templates/report/RULES.md` were also all wrong by exactly 7 (this
> branch added 7 lines above them) and have been converted to anchors.

**On locations in this document.** Line numbers in this repo go stale within a single
session — `skills/start/SKILL.md` was reordered twice on 2026-11-12, once between two reads
taken minutes apart, and `scripts/arid_nodes.py` moved ~76 lines during the 2026-11-16
re-derivation while another agent held the file. Where a reference has been revisited it
now quotes an anchor phrase instead of, or alongside, the line number. Un-revisited
`file:NN` references predate that rule and should be treated as approximate.

Every citation into `templates/report/RULES.md` has been converted, because that file
grew by 7 lines on this branch and **every** line number this document held into it was
wrong by exactly 7 (`:33` → the secrets rule is at `:40`; `:30`, `:31`, `:34`, `:18-20`
all likewise). A reference that is uniformly wrong by a constant is the signature of this
whole class of defect: the prose was right when written and never re-read against the
tree.

---

## Findings, ranked by damage in a real run

### C1. The gatekeeper is forbidden from doing its own decisive step

`agents/qa-gatekeeper.md:39-42`

> 2. **You never run a destructive or outward-facing command.** No `git push`, no
>    `git commit`, no `rm`, **no writes to any path**, no deploys…

`agents/qa-gatekeeper.md:91-100`

> ### 3. Prove it can fail (this is the decisive step)
> For each new test, in a **throwaway `git worktree`** … change one line of the
> production code … Remove the worktree afterwards

Same pair in `templates/agents/ts-qa-gatekeeper.md.tmpl:39` vs `:59-62`.

`git worktree add` writes a directory, editing the production line writes a file, and
`git worktree remove` is an `rm`. A literal reader obeys rule 2 — it is numbered,
labelled non-negotiable, and comes first — and silently downgrades fail-proof to a code
read. That is precisely the failure the whole plugin exists to prevent ("a passing test
is not evidence until you have seen it fail", `:26-28`), and nothing in the verdict
format forces the agent to admit it skipped it.

Rewrite rule 2 with the carve-out stated, not implied:

> 2. **Nothing you do changes the working copy, the index, or any remote.** No `git
>    push`, no `git commit`, no deploys, no network beyond a local test server, and no
>    write anywhere under the repo root. The one exception is the throwaway
>    `git worktree` of step 3: you may create it under `$TMPDIR`, edit production files
>    *inside it*, and remove it. Before you finish, `git status` and
>    `git worktree list` on the working copy must be unchanged — print both.

### C2. The PR comment — declared the primary surface — reads files nothing writes

`skills/report/SKILL.md:35-54` promises a deterministic comment built from
`.testing/verification-log.md`, `.testing/coverage-raw.json`,
`.testing/health-baseline.json` and "the run's findings", via:

```
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/pr_comment.py" --testing-dir .testing …
```

`scripts/pr_comment.py:190-193` actually loads `health-baseline.json`,
**`coverage-diff.json`**, **`gates.json`**, **`findings.json`**. Three of the four
filenames are written by no skill, no agent, no script and no CI step. `load()` swallows
the error (`:45-46`), so the run *succeeds* and emits a comment with the gates, coverage
delta and findings sections empty.

Damage: the one artifact the design rests on (Infer ~0% vs ~70%, cited in four files)
ships blank, and does so quietly. Fix in the prompt by naming the real contract: either
change `report/SKILL.md` to say which command writes `gates.json`, `coverage-diff.json`
and `findings.json`, or state that `/testsquad:verify` and `/testsquad:diagnose` must emit
them — today neither is told to.

> **CLOSED 2026-11-16 — the prescribed fix is in the tree, both halves.** All three files
> now have a named writer, and the skills that write them say so:
>
> - `gates.json` — `skills/verify/SKILL.md`, anchor *"Then write the same table to
>   **`.testing/gates.json`**, which is the file the PR comment"*
> - `findings.json` — `skills/test/SKILL.md`, anchor *"Write the round's findings to
>   **`.testing/findings.json`**"*, and `skills/debug/SKILL.md`, anchor *"Then write the
>   findings to **`.testing/findings.json`**, the only input the PR comment"*
> - `coverage-diff.json` — `skills/diagnose/SKILL.md`, anchor *"Write
>   `.testing/coverage-state.md` and **`.testing/coverage-diff.json`**"*
>
> `skills/report/SKILL.md` now names the producing command for each row of the PR-comment
> source table (`— /testsquad:verify`, `— /testsquad:diagnose`, `— /testsquad:test`), and
> both `verify` and `debug` additionally specify the silence contract this finding was
> really about: an absent `gates.json` is reported as "run `/testsquad:verify`" rather than
> as an empty section. **The quiet-blank-comment failure mode is gone.**

### ~~C3. Both protective hooks are permanently disarmed~~ — **WITHDRAWN 2026-11-16: the finding is false, and was false when it was filed**

> `skills/test/SKILL.md` creates `.testing/tasks/current` and deletes it, and has done
> since the initial commit. Measured:
>
> ```
> $ git show 8a2f1a1:skills/test/SKILL.md | grep -n "tasks/current"
> 29:**Mark the task in flight.** Write the task id to `.testing/tasks/current` — a single
> 118:Delete `.testing/tasks/current` before you finish — on success, on BLOCKED, and on a
> $ git show HEAD:skills/test/SKILL.md | grep -n "tasks/current"
> 29:**Mark the task in flight.** Write the task id to `.testing/tasks/current` — a single
> 142:Delete `.testing/tasks/current` before you finish — on success, on BLOCKED, and on a
> ```
>
> On `main` and on `HEAD`. The hooks are armed. **The §1 text below even prescribes, as a
> fix, almost exactly the sentence that was already in the file** — including the
> "whatever the verdict" clause, which `skills/test/SKILL.md` states as *"on success, on
> BLOCKED, and on a crash"*.
>
> **How this got written is the finding worth keeping.** The paragraph was edited on this
> branch to add the parenthetical "(it does carry a `.testing/tasks/current` row, marked
> **not** committed, but nothing creates the file)" — a correction to the *table* reference
> that was applied without re-checking the *assertion it was attached to*. A half-verified
> edit is more dangerous than no edit: it carries the authority of having just been
> checked. Kept here, struck through, rather than deleted.

The text as filed:

`hooks/examples/pre-tool-use-protect-product-code.json:9` and
`hooks/examples/stop-qa-gatekeeper.json:9` both begin:

```
test -f "${CLAUDE_PROJECT_DIR}/.testing/tasks/current" || exit 0
```

~~`skills/test/SKILL.md:18` writes `.testing/tasks/<id>.md` and never
`.testing/tasks/current`. No file in the repo creates, symlinks or removes it;
`skills/start/SKILL.md`'s "What gets written" table lists only `.testing/tasks/`
(it does carry a `.testing/tasks/current` row, marked **not** committed, but nothing
creates the file). So the product-code guard and
the QA stop-gate exit 0 on every invocation, forever, while `hooks/examples/README.md:57`
tells the user they are armed "during a testing task".~~

~~Fix: `test/SKILL.md` §1 must say *"write `.testing/tasks/<id>.md`, then point
`.testing/tasks/current` at it; remove `current` in §5 whatever the verdict"* — and §5
must say it unconditionally, including on BLOCKED, or the guard latches on forever.~~

**What is actually in the tree.** `skills/test/SKILL.md` §1, anchor *"**Mark the task in
flight.** Write the task id to `.testing/tasks/current` — a single line, nothing else — and
delete it when the task ends, however it ends."*, followed by the reason, anchor
*"they are permanent no-ops and the product-code guard silently protects nothing."* And
§5, anchor *"Delete `.testing/tasks/current` before you finish — on success, on BLOCKED,
and on a crash"*. `skills/start/SKILL.md`'s "What gets written" table carries the row.

**`hooks/examples/README.md:57` does not exist**, and no file at that path ever has.
`ls hooks/examples/` returns three `.json` files: `post-tool-use-related-tests.json`,
`pre-tool-use-protect-product-code.json`, `stop-qa-gatekeeper.json`. The sentence about
what it "tells the user" was invented. **The one real gap this finding touched** is that
the two hook JSONs ship undocumented — there is no prose anywhere telling a user what
they do or that they must be opted into. That is worth writing; it is not what C3 said.

### C4. One agent asserts a fact about the user's company that is almost never true

`agents/automation-engineer.md:85-89`

> **This repo's organisation runs a shared test company in production.** Any spec that
> depends on data it did not create … is **a defect, and you flag it as one**, whether
> you wrote it or found it.

This is a leftover from one specific organisation, shipped as a universal premise in a
marketplace plugin. In any other repo the agent will report existing, correct,
intentionally-seeded fixtures as defects — a wall of false positives in the first run,
which `agents/reporter.md:30-36` and `skills/plan/SKILL.md:87-98` both identify as the
fastest way to get the tool switched off.

Rewrite as a conditional with a stated source:

> If `.testing/known-issues.md` or `.testing/conventions.md` records that this project
> shares a test environment or test tenant, then any spec depending on data it did not
> create is a defect and you flag it as one, citing the record. If nothing records it,
> note the assumption instead of asserting it.

### C5. `project-analyst` is told to write a file it has no tool to write

`agents/project-analyst.md:9`

```
tools: Read, Grep, Glob, Bash
```

`agents/project-analyst.md:95` — "Write `.testing/profile.json`." Also `:39` "**You
write only the profile.**" and `:111` "Record a lesson".

With no `Write`, the agent either returns prose and no profile (and every other agent's
Step 0 then reads a file that does not exist — `unit-test-engineer.md:139`,
`automation-engineer.md:169`, `debugger.md:73`, `tech-scout.md:144`, `reporter.md:136`),
or it reaches for `Bash` + heredoc, which hands it unrestricted write access and makes
"you write only the profile" unenforceable.

Pick one and say it: add `Write` and keep `:39` as the constraint, **or** state "you
produce the profile JSON in your report; the orchestrator writes it" the way
`qa-gatekeeper.md:173` already handles lessons. The second is more consistent with
`docs/RESEARCH.md:246-247` ("workers are read-only where possible").

### H6. Every role exists twice and the two copies have already drifted

`agents/*.md` (7 shipped agents) and `templates/agents/*.tmpl` (4 generated agents)
restate the same roles. Divergences found:

| Rule | Shipped agent | Generated template |
|---|---|---|
| `data-testid` exception to "never edit product code" | `unit-test-engineer.md:82-84`, `automation-engineer.md:72-75` — allowed | `ts-unit-test-engineer…:64`, `ts-automation-engineer…:40` — absent, so forbidden |
| 5-run stability check | `qa-gatekeeper.md:102-113` | present `:64-69` |
| Quarantine thresholds (10 obs / 30% / 7d / 30d) | `automation-engineer.md:144-147` | `ts-automation-engineer…:54-63` — "a named owner and a deadline", no numbers |
| Missing lesson fails the round | `qa-gatekeeper.md:178` "say so: that is a failed round" | `ts-qa-gatekeeper…:112` "**Fail the round**" — stronger |

Worse, the shipped set is largely unreachable: no skill names `project-analyst`,
`tech-scout` or `reporter` anywhere (`skills/` mentions only `ts-*`, plus one stray bare
`qa-gatekeeper` at `skills/test/SKILL.md:64` against `ts-qa-gatekeeper` at `:46`). So
`/testsquad:test` on a repo that never ran `/start` dispatches four agents that do not
exist.

Decide which set is canonical. The cheapest fix: make `agents/*.md` the single source and
have the templates be the stack-filled *header* plus `Follow the rules in the testsquad
<role> agent`, or state in `skills/test/SKILL.md` §2 "if `ts-*` is absent, fall back to
the plugin agent of the same name".

### H7. Two incompatible stability protocols; the implementer is graded on one it was never given

`agents/automation-engineer.md:126-131`

> burn in **three-way rather than N identical**: once in the same process, once with a
> simulated time shift, once on a different host or under CPU contention.

`agents/qa-gatekeeper.md:102-110`

> Run each new test **5 times** … Make 2 of the 5 adversarial … **Any disagreement
> between runs is a rejection**

An automation engineer that does exactly what it is told (3 runs) hands over work the
gatekeeper will reject for insufficient evidence, burning a full loop round for nothing.
`unit-test-engineer.md` is given **no** stability requirement at all (`:94-96` covers
determinism, not repetition) and so is graded against an unstated rule.

Fix: state the gate once — "5 runs, of which ≥2 adversarial (shuffled order in a fresh
process; under CPU contention); any disagreement is a rejection" — in
`templates/report/RULES.md` or `.testing/conventions.md`, and have all three agents
reference it rather than each inventing a count.

### H8. The escalation ladder is unreachable, and the escalation itself is impossible

`skills/test/SKILL.md:74-81`

> - **Rounds 1–3**: resume the *original* implementer …
> - **Rounds 4–5**: dispatch a *fresh* implementer **on a more capable model** …
>
> **Stop early** on any of: **the same failure signature twice**, a time or token
> budget, or a blocker.

A loop that fails the same way in rounds 1 and 2 trips "same failure signature twice"
before round 4 ever happens — and repeated failures usually *are* the same signature,
which is exactly why the ladder was written. Rank order is unstated, so the model picks.

And "a more capable model" has no referent: all four templates and all seven agents are
pinned `model: opus`.

Rewrite: *"Stop early on: the same failure signature in **three** consecutive rounds, a
blocker, or the token budget. The signature rule never pre-empts the round-4 hand-off —
a fresh implementer is the response to repetition, not an alternative to stopping."* And
replace "more capable model" with "a fresh context, told plainly that two attempts
failed and it now owns the problem".

### H9. The reporter can demote a check on a single data point

`agents/reporter.md:91-93`

> **Any check above 10% EFP is auto-demoted out of the main report** into the backlog

`skills/report/SKILL.md:110-112`

> any check above **10% effective false positives** **over a rolling window of at least
> 10 observations** is demoted

One "not useful" click on a check's first finding is 100% EFP by the agent's rule. The
agent prompt is the one the subagent actually reads. Add the ≥10-observation floor to
`reporter.md:91` verbatim, and say what happens below the floor ("report the rate,
demote nothing").

### H10. An agent that cannot ask the user is told to write the file that requires approval

`agents/automation-engineer.md:31-34`

> Read the per-framework threshold from `.testing/conventions.md`; **if none is
> recorded, record one.**

`skills/learn/SKILL.md:52` — "**Promotion requires the user's approval. Always.**"
`conventions.md` is the rules file "the agents *enforce*" (`skills/start/SKILL.md`,
step 9: *"a numbered rules file the agents *enforce*"*)
and is read unquestioned by every agent on every run (`learn/SKILL.md:19-23`). A subagent
that cannot prompt (`automation-engineer.md:159-164`) writing a flake threshold into it
is a self-approved standing rule — the exact erosion `skills/verify/SKILL.md:86-91`
forbids elsewhere.

Fix: *"…if none is recorded, **propose one in your report** with the evidence; the
orchestrator takes it through `/testsquad:learn`. Until then use 1% for unit-level and
10% for browser-level frameworks and say which you used."*

### M11. Gatekeeper's rejection list does not match what the unit engineer is told to avoid

`agents/unit-test-engineer.md:107,113` — under "**What will get your work rejected**":

> - `assertIsNotNone` / `toBeDefined` / `toBeTruthy` as the **only** assertion

`agents/qa-gatekeeper.md:79-81`:

> **Treat as weak and call it out**: the only assertion being `assertIsNotNone` /
> `toBeDefined` / `toBeTruthy`.

The engineer deletes work the gatekeeper would have accepted with a note. Harmless in
isolation, but it is evidence the two lists were maintained separately; pick one word
("reject" or "flag") and use it in both, plus both templates.

### M12. Scout: two budgets, two opposite file-handling rules, two definitions of `adopt`

| Claim | A | B |
|---|---|---|
| Web-op budget | `agents/tech-scout.md:71-73`: **12 + up to 3 reserved** for contradictions | `skills/scout/SKILL.md:22`: **12**, no reserve |
| Radar file | `tech-scout.md:150-151`: "you **update** it, you do not replace it … entries never vanish silently" | `scout/SKILL.md:93`: "**Rewrite** `.testing/tooling-radar.md`, atomically" |
| `adopt` means | `tech-scout.md:113`: "already trialled **here** successfully" | `scout/SKILL.md:70`: "Proven here, in this repo" (compatible, but the trial precondition is dropped) |

"Rewrite atomically" is about the write mechanism and will be read as "replace the
contents". Say "rewrite the whole file from the merged old+new entry set; no entry is
dropped without a dated reason line".

Also `scout/SKILL.md:22` has a stray `**` mid-sentence: `…per run** (searches and
fetches combined).**`.

### M13. "Every finding must be reproduced" vs 👁 observed in the main list

`skills/depth/SKILL.md:31` — "**Every finding must be reproduced at every level.**"
`templates/report/RULES.md:18`, `skills/debug/SKILL.md:114`, `agents/debugger.md:158-163`
— "Only ✅ reproduced **and 👁 observed** reach the main list."

Under `exhaustive`, `agents/debugger.md:96` adds a third reading: "Every finding in the
report must be reproduced, with no exceptions". A literal agent at exhaustive must drop
observed items that every other file admits. State the rank: *"the main list admits ✅
and 👁; depth changes how many strategies you spend trying to turn 👁 into ✅, never what
qualifies."*

### M14. "Ask once, batched, then unattended" is contradicted three times in the same file

`skills/start/SKILL.md` — "**Ask once, at the start, batched.** … then the run proceeds
unattended." Then, in order: the step 3 UNSAFE branch (ask whether to continue), **step 5's
consent gate** (*"Print those commands verbatim and get agreement"*), step 8 (*"**Show the
diff and get agreement before writing.**"* — `CLAUDE.md`), and step 11 (*"**get explicit
approval** before it becomes"* — the acceptance contract). **Four** further interruptions,
all of them correct.

*Updated 2026-11-12: this finding said "three", written before the step 5 consent gate
existed (see `docs/SECURITY-REVIEW.md` S2-3). The step numbers in the original recommended
wording — "`CLAUDE.md` (step 7)", "the contract (step 10)" — were also left behind by the
renumbering; they are now 8 and 11.*

Reword the "Ask once" rule to what is actually meant: *"**Ask the configuration questions
once**, at the start, batched. Later stops are approval gates, not questions: safety
(step 3), agreeing to execute this repo's code (step 5), writing to `CLAUDE.md` (step 8),
and the contract (step 11). Nothing else interrupts."*

### M15. Report artifact names and paths differ between files

**All three naming conflicts are now fixed. Re-verified 2026-11-12:**

| Thing | As found (A vs B) | Now |
|---|---|---|
| Dashboard filename | `diagnostic-<date>.html` vs `dashboard-<date>.html` | **agreed** — `skills/start/SKILL.md` and `skills/report/SKILL.md` both say `.testing/reports/dashboard-<date>.html` |
| PR comment path | `.testing/reports/pr-comment.md` vs `.testing/pr-comment.md` | **agreed** — `skills/report/SKILL.md` and `templates/ci/testsquad-pr.yml` both say `.testing/reports/pr-comment.md` |
| Normalized coverage | `coverage-raw.json` vs `coverage-current.json` | **agreed** — `skills/diagnose/SKILL.md` and `templates/ci/testsquad-pr.yml` both say `.testing/coverage-raw.json` |

The CI half was the one that mattered, and it is closed: `.gitignore` line 1 is
`.testing/reports/`, and the CI template now writes its comment *inside* that directory, so
the artifact is no longer commit-eligible. ~~**`HANDOFF.md` §4 still describes this as
open** ("the workflow writes `.testing/pr-comment.md`, outside the gitignored
`.testing/reports/`")~~ — **that quotation is not in `HANDOFF.md`.** `grep -n "pr-comment"
HANDOFF.md` returns nothing; the string does not appear in the file at all, in §4 or
anywhere else. Withdrawn 2026-11-16. Nothing is owed to `HANDOFF.md` on this point.

**~~What remains of M15.~~ CLOSED 2026-11-16 — nothing remains.** This said the "What gets
written" inventory in `skills/start/SKILL.md` was "still incomplete" because
`.testing/tooling-radar.md` and `.testing/coverage-raw.json` "do not appear in it". Both
appear in it, in the same table:

```
| `.testing/coverage-raw.json`, `coverage-diff.json` — `/diagnose` | yes |
| `.testing/mutation-plan.json` — `/mutate`; `tooling-radar.md` — `/scout` | yes |
| `.testing/tasks/current` — in-flight marker the guard hooks read; transient | no |
```

(`grep -nF "tooling-radar.md" skills/start/SKILL.md` and
`grep -nF "coverage-raw.json" skills/start/SKILL.md` — both 1 hit in the table.) The
`tasks/current` row this paragraph already credited is the third line above. The
observation that `/testsquad:uninstall` enumerates nothing beyond `.testing/` wholesale,
so this table is the only inventory that exists, still stands and is still worth acting
on.

### M16. The reporter's binding contract is an unresolvable path, and the two contracts disagree

> **The first half is CLOSED, 2026-11-16.** Both binding references in
> `agents/reporter.md` are now `${CLAUDE_PLUGIN_ROOT}`-qualified — anchors
> *"`${CLAUDE_PLUGIN_ROOT}/templates/report/STYLE.md` is the contract, not a style"* and
> *"`${CLAUDE_PLUGIN_ROOT}/templates/report/STYLE.md` — the binding contract. First,"* —
> as is `skills/report/SKILL.md`, anchor *"`${CLAUDE_PLUGIN_ROOT}/templates/report/STYLE.md`
> is binding, not advisory."* The contract resolves from a user repo. **The second half,
> the two disagreeing checklists, is still open** and is the part that matters.

~~`agents/reporter.md:43` and `:133` reference `templates/report/STYLE.md` **without**
`${CLAUDE_PLUGIN_ROOT}`. Every other reference in the repo uses the variable
(17 occurrences). Run inside a user's repo, the relative path does not exist, so the
"binding, not advisory" contract silently fails to load — and `reporter.md:50` explicitly
forbids restating the rules locally, so the agent has no fallback.~~

**Still open.** The two contracts disagree on one hard number: `templates/report/RULES.md`
— anchor *"**Check: emphasised rows ≤ max(2, 30% of rows), or the report fails review.**"*;
`templates/report/STYLE.md` checklist item 5 — anchor *"Are emphasised rows ≤30% of all
rows, with red used only for"*. On a 3-row report those differ (2 vs 0). RULES is the
better rule; make STYLE match it.

`agents/reporter.md` has since been edited to *arbitrate* rather than to pick a side —
anchor *"at most `max(2, floor(0.30 × rows))` emphasised rows, which is the form
`${CLAUDE_PLUGIN_ROOT}/templates/report/RULES.md` states and the one that wins where
STYLE.md quotes a bare 30%"*. That removes the agent's ambiguity, which was the operational
risk, but it leaves two contract files contradicting each other with a third file holding
the tie-breaker. Fix STYLE.

And `skills/report/SKILL.md` still calls STYLE's checklist "13-point" while every other
skill binds to RULES' **10**-point list (`templates/report/RULES.md`, items 1–10, anchor
*"10. Are there zero secrets and zero unconfirmed items outside the appendix?"*). Two
checklists, both labelled "all must be yes". Say which one the reporter runs and which one
everyone else runs — or collapse to one. **Unchanged; still the live half of M16.**

### M17. `/testsquad:mutate` hand-rolls what `scripts/arid_nodes.py` already does

`skills/mutate/SKILL.md:30-52` specifies one-mutant-per-line, an arid-node suppression
table, "**Hard cap: 7 mutants per file**", and a feedback loop into
`.testing/feedback.json`. `scripts/arid_nodes.py` implements exactly that —
`--max-per-file` default **7** (`:1688`), `--seed` (`:1691`), `--feedback` (`:1693`),
`--diff` scoping (`:1687`) — and is referenced by **no** skill, agent, hook or CI file.

A model asked to apply a 100+-rule suppression policy by reading prose will apply it
differently every run; the script is deterministic and seeded. Add the invocation to
`mutate/SKILL.md` §2 and cut the table down to a one-line description of what the script
suppresses.

### M18. "A tool-call budget" with no number

`skills/test/SKILL.md:48` — "Give each specialist **one dimension and a tool-call
budget**." No number is given here or in any agent except `tech-scout.md:71` (12 web
ops) and `debugger.md:91-96` (minutes). So the rule the research names as *the* fix for
the zero-output failure mode (`docs/RESEARCH.md:243-245`) is unenforceable for the three
agents that do the actual work.

Give defaults: *"unit: ≤40 tool calls; automation: ≤60; debugger: the depth budget;
scout: 12 web ops. State calls used in your report."* Tool calls are countable by the
agent; wall-clock minutes are not — the debugger's "2 minutes" / "~10 minutes" budgets
(`debugger.md:91-96`, `depth/SKILL.md:24-29`, `debug/SKILL.md:34-39`) are the one place
in the design where the enforcement mechanism is a feeling. Add a tool-call equivalent
beside each row.

### M19. Quarantine thresholds have no data source

`agents/automation-engineer.md:144-147` — "at least **10 observations** before
classifying anything, a **30% failure rate over a rolling 7-day window** to quarantine,
and **auto-unquarantine after 30 days clean**."

Nothing stores per-test run history. `start/SKILL.md:219-242` lists no such file;
`known-issues.md` holds the quarantine register (owner + re-check date), not observation
counts. A single-run subagent cannot compute a 7-day rolling rate, so it will either
invent one (violating "never state a number you did not measure", stated in four files)
or ignore the rule.

Either name the file (`.testing/flake-history.json`, written by `/testsquad:health`) or
say plainly: *"these are the thresholds a human applies to the register; you propose
quarantine with the observations you have and state how few they are."*

### M20. "Minor findings are ledgered and deferred" has no definition

`skills/test/SKILL.md:85` — minor is undefined and the deferral is attractive to a
budget-pressed orchestrator, so "NOT MET on criterion 3" can be reclassified as minor
and the run closed as done. That is the exact failure `:108-110` says the whole design
exists to prevent.

Fix: *"Minor means: not attached to any acceptance criterion, and severity S3 or S4. A
gap against a numbered criterion is never minor."*

### L21–L25 (low)

- **L21.** `skills/upgrade/SKILL.md:70` names `ts-browser-engineer`; no such template
  exists (`templates/agents/` has 4). Illustrative, but `/start` step 9 (*"**Generate only
  what fits.** A library with no UI gets an API/integration agent, not a Playwright one."*)
  promises per-stack
  agent selection from a set of four fixed roles — say "the roles that fit the stack,
  from the four templates" or add the template.
- **L22.** `agents/qa-gatekeeper.md:173` — "**Append** one file to `.testing/lessons/`
  only via your report — you do not write files." Self-contradicting in one sentence;
  make it "Do not write the lesson file. Put the lesson in your report; the orchestrator
  files it."
- **L23.** `templates/agents/ts-qa-gatekeeper.md.tmpl:112` — "**Fail the round if the
  implementer recorded no lesson.**" The verdict is defined as a per-criterion table
  (`:92-101`); a missing lesson is not a criterion, so this makes the verdict
  un-derivable from the contract. Report it as a separate `PROCESS: lesson missing`
  line, not as a criterion failure.
- **~~L24.~~ WITHDRAWN 2026-11-16 — fabricated.** This quoted `README.md:25` verbatim
  ("A test that does not fail when the code under test is broken is **deleted**") and set
  it against `unit-test-engineer.md` and `qa-gatekeeper.md` as a three-way verb conflict.
  **There is no `README.md` in this repository and there never has been**:
  `find . -name 'README*' -not -path './.git/*'` returns nothing, and
  `git log --all --diff-filter=A -- README.md` returns nothing. A verbatim quotation was
  attributed to a file that has never existed. The remaining two files do differ in verb
  ("repair it rather than deleting it" vs "Reject it"), but they are reconcilable — the
  gatekeeper rejects the *round*, the engineer repairs the *test* — so without the invented
  third party there is no finding here.

  **Note, 2026-11-16 (same day, later).** A `README.md` was added to this repository after
  the withdrawal above was written, so the two commands quoted in it no longer return
  nothing — `find . -name 'README*' -not -path './.git/*'` now returns `./README.md`. The
  withdrawal is unchanged and still correct on its merits: re-checked against the new file,
  `grep -ni delet README.md` returns nothing, and `README.md:25` is a sentence about
  unreplaced scorecard placeholders. The quotation was invented then and is still not
  there now. The paragraph is kept as written, with its commands, because it is the
  evidence that a verbatim quotation was once attributed to a file nobody had written —
  and a record that is edited into agreement with the present is no longer a record.
- **L25.** Unfalsifiable instructions, each costing context and checking nothing. With
  checkable replacements:

  | File:line | Text | Checkable version |
  |---|---|---|
  | `qa-gatekeeper.md:169`, `reporter.md:173`, `debugger.md:187` | "Keep it short. A reviewer who writes forty findings gets read as zero." | "Main list ≤10 items (`RULES.md:17`); print `N of M shown`." |
  | `unit-test-engineer.md:125` | "Write fewer, better tests and be glad when one is rejected" | "Ship no test you have not seen go red under mutation; report the count you discarded." |
  | `unit-test-engineer.md:97`, `automation-engineer.md:104` | "Follow the repo, not your taste." | "Name the existing test file you matched for layout, naming and assertion style." |
  | `reporter.md:170`, `qa-gatekeeper.md:165`, `project-analyst.md:105` | "Write for a developer who has not opened this area in six months." | Already covered by `RULES.md:24-25` (symptom-not-cause); delete the restatement. |
  | `debugger.md:57` | "State the budget you spent." | "State tool calls used of N and elapsed strategies tried of the depth table." |

---

## Contradiction table

| # | Claim A | Claim B | Which should win |
|---|---|---|---|
| 1 | `qa-gatekeeper.md:40` "no writes to any path" | `qa-gatekeeper.md:92-100` mutate production code in a worktree | **B**, with A rewritten to carve out `$TMPDIR` worktrees and require a clean `git status` + `git worktree list` |
| 2 | ~~`report/SKILL.md:51-54` PR comment sources~~ | ~~`scripts/pr_comment.py` reads `coverage-diff/gates/findings.json`~~ | ✅ **CLOSED** — the skills now name a writer for each file; see C2 |
| 3 | ~~`hooks/examples/*:9` require `.testing/tasks/current`~~ | ~~`test/SKILL.md:18` writes only `tasks/<id>.md`~~ | ✅ **WITHDRAWN — not a contradiction.** `skills/test/SKILL.md` writes *and* deletes `current`, on `main` and `HEAD`; see C3 |
| 4 | `automation-engineer.md:126-131` three-way burn-in | `qa-gatekeeper.md:102-110` 5 runs, 2 adversarial | **B** (it is the gate); state it once, centrally |
| 5 | `unit-test-engineer.md:113` lone-truthy assertion = rejected | `qa-gatekeeper.md:79-81` = "weak, call it out" | **B** — the grader defines the bar; the engineer should avoid it, not police it |
| 6 | `reporter.md:91` demote above 10% EFP | `report/SKILL.md:111` only after ≥10 observations | **B** |
| 7 | `automation-engineer.md:34` "record one" into `conventions.md` | `learn/SKILL.md:52` promotion needs user approval, always | **B** — agent proposes, `/learn` promotes |
| 8 | `test/SKILL.md:81` stop on same signature twice | `test/SKILL.md:77` rounds 4–5 fresh implementer | **Escalation first**: raise the stop rule to three consecutive identical signatures |
| 9 | `depth/SKILL.md` every finding reproduced | `RULES.md` (anchor *"**Only ✅ reproduced and 👁 observed appear in the main list**"*) / `debug/SKILL.md` 👁 observed allowed in main list | **B** — depth buys strategies, not admission criteria |
| 10 | `tech-scout.md:150` "update it, never replace" | `scout/SKILL.md:93` "Rewrite … atomically" | **A** on semantics, **B** on mechanism |
| 11 | `tech-scout.md:71-73` 12 + 3 reserved ops | `scout/SKILL.md:22` 12 ops | **A** (the reserve exists for a named purpose) |
| 12 | `RULES.md` emphasis ≤ max(2, 30%) (anchor *"emphasised rows ≤ max(2, 30% of rows)"*) | `STYLE.md` emphasis ≤30% (anchor *"Are emphasised rows ≤30% of all rows"*) | **A** — still open in the contract files; `agents/reporter.md` now states the tie-break |
| 13 | `report/SKILL.md` 13-point checklist (STYLE) | `RULES.md` 10-point checklist (anchor *"10. Are there zero secrets and zero unconfirmed items outside the appendix?"*) | **One list** — fold the 3 STYLE-only items into RULES and retire the second. Still open |
| 14 | `start/SKILL.md:22` "ask once, then unattended" | `start/SKILL.md:61,128,177` three approval gates | **The gates**; reword the promise |
| 15 | `start/SKILL.md:203` `diagnostic-<date>.html` | `report/SKILL.md:89` `dashboard-<date>.html` | **B** |
| 16 | ~~`diagnose/SKILL.md` `coverage-raw.json`~~ | ~~`templates/ci/…yml` `coverage-current.json`~~ | ✅ **CLOSED, as A** — both now say `coverage-raw.json`; `coverage-current.json` exists nowhere |
| 17 | `unit-test-engineer.md:82-84` testid exception | `ts-unit-test-engineer…:64` no exception | **A**, and port it to both templates |
| 18 | ~~`README.md:25` non-discriminating test is deleted~~ | ~~`unit-test-engineer.md:128` repair, don't delete~~ | ❌ **WITHDRAWN — fabricated.** No `README.md` existed in this repo when this was written, so there was no Claim A. One was added 2026-11-16 and does not make the claim either; re-checked, still withdrawn. See L24 |

---

## Broken references

Checked every `${CLAUDE_PLUGIN_ROOT}/…` path (17), every `scripts/*.py` invocation
against its `argparse` signature, every `.testing/…` path (27 distinct), and every
agent name used in a skill.

> **Re-derived 2026-11-16. Six of the nine rows below were wrong** — four closed by this
> branch, two never true. This table was the worst instance in the document of recording a
> session rather than the tree. Struck rows are kept with the evidence that closed them.

| Reference | In | Status |
|---|---|---|
| ~~`templates/report/STYLE.md` (no `${CLAUDE_PLUGIN_ROOT}`)~~ | `agents/reporter.md` | ✅ **CLOSED** — both references now carry `${CLAUDE_PLUGIN_ROOT}` |
| ~~`.testing/tasks/current`~~ | the two hook JSONs | ✅ **WITHDRAWN — never true.** `skills/test/SKILL.md` creates and deletes it, on `main` and `HEAD`. `hooks/examples/README.md` **does not exist** |
| ~~`.testing/coverage-diff.json`, `.testing/gates.json`, `.testing/findings.json`~~ | `scripts/pr_comment.py` | ✅ **CLOSED** — written by `/diagnose`, `/verify`, `/test` and `/debug` respectively, each named in its skill |
| ~~`.testing/coverage-current.json`, `.testing/pr-comment.md`~~ | `templates/ci/testsquad-pr.yml` | ✅ **CLOSED** — `coverage-current.json` no longer exists anywhere in the repo (`grep -rn` → only this document); the workflow writes `.testing/coverage-raw.json` and `.testing/reports/pr-comment.md`, matching the skills |
| `ts-browser-engineer` | `skills/upgrade/SKILL.md` | **Not a broken reference.** It appears once, inside an illustrative sample of `/upgrade` output, on the line `ts-browser-engineer       removed — no UI in the profile since 2026-10-30` — i.e. as an example of an agent being *removed*. Downgraded from the original finding |
| `ts-unit-test-engineer`, `ts-automation-engineer`, `ts-debugger`, `ts-qa-gatekeeper` | `skills/test/SKILL.md`, `plan/SKILL.md`, `debug/SKILL.md` | **Still open** — exist only after `/testsquad:start`; no fallback stated |
| ~~`project-analyst`, `tech-scout`, `reporter`~~ | — | ✅ **WITHDRAWN** — all three are referenced: `skills/start/SKILL.md`, `skills/scout/SKILL.md`, `skills/report/SKILL.md` (`grep -rln`) |
| ~~`scripts/arid_nodes.py`~~ | — | ✅ **WITHDRAWN** — `skills/mutate/SKILL.md` invokes it, anchors *"`${CLAUDE_PLUGIN_ROOT}/scripts/arid_nodes.py`. **Run it.**"* and the `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/arid_nodes.py" <path>` block |
| `${CLAUDE_PLUGIN_ROOT}/scripts/{env_safety_check,risk_map,flake_smells,normalize_coverage,pr_comment,session_context}.py` | `start` (the `env_safety_check.py` call in step 3 and the `risk_map.py` call in step 7), `health:83`, `verify:58`, `diagnose:50`, `report:36`, `hooks.json:8` | **Sound** — all exist, all flags match `argparse` |
| `${CLAUDE_PLUGIN_ROOT}/stacks/<stack>.md`, `/templates/agents/`, `/templates/report/{RULES.md,dashboard.html}` | `project-analyst:62`, `start:73,141,204`, `upgrade:48`, `report:90`, + 4 more | **Sound** |

---

## Context-waste candidates

`scripts/validate_plugin.py` sets the ceilings in its `BUDGETS` dict — agents 3,500,
skills 3,000, `templates/agents` 1,600, `report-rules` 1,200 tokens. The validator still
passes with **0 warnings**, so nothing is over budget.

**Re-measured 2026-11-16, and the headroom has got worse, not better.** Using the
validator's own `estimate_tokens()`:

> **The table below is in a retired unit. Read it as history, not as current.**
> `estimate_tokens()` computed `os.path.getsize(path) // 4` over the whole file,
> frontmatter included, against a 4-chars-per-token constant nobody had measured. It
> was retired on 2026-11-18 and replaced by `body_tokens()`, which measures the
> frontmatter-stripped body against a divisor calibrated at **2.86 chars/token** for
> this corpus. The old numbers are not wrong about what they measured; they are not
> token counts. In the current unit `automation-engineer.md` is **4,546**, not 3,486 —
> the finding below was understated by about 30%, not overstated. The figures are left
> unedited so the dated claim stays checkable.

| File | Then | Now | Ceiling |
|---|---|---|---|
| `agents/automation-engineer.md` | ~3,183 | **3,486** | 3,500 |
| `agents/debugger.md` | ~3,031 | **3,136** | 3,500 |
| `agents/reporter.md` | — | **3,022** | 3,500 |

`automation-engineer.md` is now **14 tokens** — under half a percent — below a ceiling the
validator fails the build on. The original finding said "within ~10%"; it is within 0.4%,
and the next paragraph added to that file breaks `--strict`. The de-duplication below is
no longer a tidiness exercise. Reproduce with:

```sh
python3 -c "import sys; sys.path.insert(0,'scripts'); import validate_plugin as v; \
print(v.body_tokens('agents/automation-engineer.md'))"
```

| Candidate | Where | Est. saving |
|---|---|---|
| The flake/real-bug matrix, verbatim 5 times | `qa-gatekeeper.md:119-124`, `debugger.md:137-142`, `debug/SKILL.md:74-79`, `ts-debugger…:42-48`, `ts-qa-gatekeeper…:73-78` | ~**550 tok** — keep it in `RULES.md` and `debugger.md`; the others cite it |
| `debug/SKILL.md:87-105` restates the four statistics `debugger.md:18-36` already carries (84%, 1-in-6, 18x/90%/25%, `1−(1−p)ⁿ`) | both files load together on `/testsquad:debug` | ~**345 tok** |
| `agents/automation-engineer.md` "Why you are built this way" — four paragraphs of arithmetic that change no action | | ~**300 tok** (compress to 3 bullets; the derivation is already in `docs/RESEARCH.md` — §17, "Flakiness: reruns alone cannot work, and the math says so"). **This is the file that is 14 tokens from the ceiling; do this one first** |
| "You cannot ask questions" section, 7 near-identical copies | `qa-gatekeeper:55-58`, `unit-test-engineer:132-135`, `automation-engineer:159-164`, `debugger:61-66`, `tech-scout:133-138`, `reporter:123-129`, `project-analyst:50-53` | ~**350 tok** (one sentence each: "You cannot prompt. Take the conservative reading and state the assumption.") |
| Lesson-recording boilerplate + "a lesson without evidence is a rumour", 7 copies | all agents, "Last step" | ~**300 tok** (the five fields are specified authoritatively in `learn/SKILL.md:30-40`) |
| Severity scale `S1 data loss · S2 … · S4 cosmetic` spelled out 4+ times | `qa-gatekeeper:156`, `unit-test-engineer:153-155`, `automation-engineer:189-190`, `debugger:179-180` | ~**100 tok** (defined in `RULES.md`, anchor *"**Severity (S1–S4) is computed"*) |
| `mutate/SKILL.md:34-46` suppression table | superseded by `scripts/arid_nodes.py` | ~**180 tok** |

Total realistic: **~2,100 tokens** across the files that load at task time — and the
larger win is that each deleted copy is one fewer place to drift (see C4, H6, H7, M12).

Examples longer than the rule they illustrate: `advise/SKILL.md:64-98` (five worked
examples, ~420 tok, for five one-line rules) and `upgrade/SKILL.md:63-80` (an 18-line
sample output for "say what changed"). Both are defensible as format specs; neither is
free.

---

## Strongest and weakest

**Strongest: `skills/depth/SKILL.md`.** 990 tokens, and every rule in it is falsifiable.
It defines the levels in minutes and strategy counts rather than adjectives (`:24-29`),
then spends its remaining length closing its own loophole: `:41-62` states that depth
governs investigation and never verification, enumerates the six things it may not change,
and declares an agent that reads the setting to skip a verification step a **defect worth
reporting as a bug in the plugin** (`:54`). `:64-71` then closes the second-order
loophole — a short findings list read as good news under a shallow setting. It is the only
file here that models how its own instruction will be abused and pre-empts it. Its one
flaw is M13.

**Weakest: `agents/automation-engineer.md`.** 3,183 tokens — the largest file in the
loaded set — and the least trustworthy per token:

- `:85` asserts a fact about the reader's organisation that is false in nearly every repo
  it will run in, and converts that falsehood into a defect-reporting instruction (C4).
- `:144-147` gives four numeric thresholds it has no data to evaluate (M19).
- `:34` instructs it to write the repo's law file without approval, from a context that
  cannot ask for approval (H10).
- `:126-131` specifies a stability protocol the grader will reject (H7).
- `:14-40` spends ~480 tokens on compounding-probability arithmetic that changes no
  decision the agent makes.
- Of its ~2,075 words, the genuinely operational content — the level-selection table
  (`:54-60`), the seven non-negotiables, the static flake checklist (`:110-124`) — is
  maybe half. The static checklist at `:110-124` is the best thing in the file and is
  the part most likely to be skimmed, because it sits behind 100 lines of preamble.

Runner-up weakest: `agents/project-analyst.md` — shortest agent (1,552 tok), good rules,
and it cannot perform its single named output (C5).

---

## What I checked and found sound

- **Script references.** All 6 invoked scripts exist and every flag used in a skill
  matches the script's `argparse` (`--out`, `--format text`, `--strip-prefix`,
  `--testing-dir`). No fabricated flags anywhere.
- **Context budgets.** `python3 scripts/validate_plugin.py . --strict` prints, verbatim:

  ```
  Validation passed: 26 component(s) checked, 0 warning(s), 0 could not be validated.
  ```

  Every agent, skill, template and RULES.md is under its ceiling, and the "no agent reads a
  stack playbook at task time" check passes (`scripts/validate_plugin.py`, the branch
  guarded by `if "stacks/" in text:`) — only `project-analyst` references `stacks/`,
  correctly exempted.

  *The third clause of that string is load-bearing and was added after a defect in which one
  unreadable component silently dropped a whole check's verdict for all 26. "0 could not be
  validated" is the plugin's own rule — silence must never look like success — applied to
  itself, so quote the string in full or not at all.*

  **Headroom is nearly gone in three places.** Measured 2026-11-12 with the validator's own
  estimator (`os.path.getsize(path) // 4`) against `BUDGETS` in `scripts/validate_plugin.py`:

  | File | Tokens | Ceiling | Headroom |
  |---|---|---|---|
  | `agents/automation-engineer.md` | 3,486 | 3,500 | **14** |
  | `templates/agents/ts-qa-gatekeeper.md.tmpl` | 1,572 | 1,600 | **28** |
  | `skills/start/SKILL.md` | 2,951 | 3,000 | **49** |

  The next edit to any of them breaches the budget and turns `--strict` red. Two
  restructuring levers were identified while closing S3-2 and **deliberately not pulled**,
  because they are content judgements for the owner rather than side effects of a cleanup:

  1. `templates/agents/ts-qa-gatekeeper.md.tmpl` duplicates a flake-classification table
     that also appears in `templates/agents/ts-debugger.md.tmpl` and
     `agents/qa-gatekeeper.md` — roughly **90 tokens** recoverable from the template.
  2. Several templates already use a *"the plugin's &lt;role&gt; agent holds the full rules"*
     pointer; the rest could adopt it instead of carrying their own copy.

  Both figures above are measured; the ~90 is an estimate made by whoever found it and is
  labelled as such.
- **Environment-safety gating.** Stated identically and in the same direction in
  `start` (the run-wide rule *"No test command, no coverage run, no dependency install,
  until step 3 has passed and step 5 is agreed"*, plus step 3), `health:18-27`,
  `diagnose:38-41`, `debug:19-24`, `mutate:116-118`,
  `advise:115-116`, `verify:28`, `project-analyst:41-43`. No file grants an exception.
- **Install permission.** Never granted implicitly; "cannot execute + the exact command"
  is the uniform fallback in `start` (step 2: *"reports \"cannot execute\" with the exact
  command it would have needed"*; step 5: *"On a no, every later stage reports \"cannot
  execute\" plus the command it would have run"*), `health:23-27`, `diagnose:38-41`,
  `debug:22-24`.
  `preferences:33-51` adds scoped, 30-day, early-revalidated approvals and a correct list
  of never-remembered actions. This is the most coherent cross-file mechanism in the
  plugin.
- **Secrets.** `start` (step 3 UNSAFE: *"key name, redacted preview — **never the secret
  itself**"*), `preferences:80-82`, `report:61-62`, and `RULES.md` (anchor *"**No secrets, ever.**"*) all say key
  names and redacted previews only, and `preferences:80` goes further (no hash). No file
  contradicts it.
- **"Never a number you did not measure."** Consistent and identically scoped in
  `reporter:57-60`, `project-analyst:31-33`, `report:102-107`, `advise:111-113`,
  `RULES.md` (anchor *"**Never a number you did not measure.**"*). `report:103` ("You
  measure nothing") is correctly the strongest form.
- **Coverage discipline.** Floor-detector framing and the uncovered/covered asymmetry are
  stated the same way in `diagnose:117-129`, `reporter:61-67`, `start:171-173`,
  `RULES.md` (anchor *"**Coverage is never a headline score.**"*). No file offers
  coverage as a headline.
- **Severity computed / priority proposed.** Consistent in all four writing agents, both
  relevant templates, and `RULES.md` (anchor *"**Severity (S1–S4) is computed; priority
  is proposed and needs a human.**"*). The evals check it (`claim-04`).
- **Evidence badges.** The four-level ladder and the "never counted in the bug total"
  clause agree in `debugger:156-166`, `debug:114-116`, `RULES.md` (anchor *"**Every
  finding carries exactly one evidence badge.**"*),
  `ts-debugger…:63-66`. Only the depth interaction (M13) is loose.
- **First-install backlog rule.** `plan:86-98`, `report:67-69`, `RULES.md:36-40` agree:
  counted, labelled pre-existing, non-blocking. Rationale identical, no drift.
- **Mutation policy.** "On demand only, never gate on a score, gate per test" holds across
  `mutate:19-21,80-89` and `diagnose:102-113`. The diff-scoping flag table
  (`mutate:69-78`) is the most falsifiable table in the repo — each row names a flag that
  can be checked against the tool's docs, including the StrykerJS `--incremental` vs
  `--since` correction.
- **Uninstall/upgrade safety.** `.local.md` precedence, version stamps, marker-block
  boundaries, "stop if markers are damaged", hand-edit detection, schema migration with
  `legacy` preservation and a refusal to downgrade — `uninstall:24-41` and `upgrade:36-57`
  agree with each other and with `start:147-152`. ~~`templates/agents/README.md:25-34`~~ —
  **that file does not exist**; `templates/agents/` holds four `.tmpl` files and nothing
  else. The reversibility story was checked across three files, not four; no contradiction
  was found among those three, and this item should be read as the weaker claim it is.
- **The gatekeeper's independence design.** `test:66-69` (fresh context, reads the frozen
  contract, re-runs commands) matches `qa-gatekeeper:43-49` exactly, including the
  "read the summary last, if at all" ordering. `CANNOT VERIFY` is routed to the
  orchestrator in both (`test:104-106`, `qa-gatekeeper:161-163`) with the same reason.
- **The verify skill's anti-erosion rule** (`verify:76-96`) is the best-argued rule in the
  plugin and has no counterpart that weakens it: no other file permits lowering a
  threshold, and `preferences:86-90` routes threshold changes to `decisions.md` the same
  way.
