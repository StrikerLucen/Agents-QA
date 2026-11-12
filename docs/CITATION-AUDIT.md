# Citation audit — 2026-11-12

Scope: every quantitative or attributed claim in `agents/*.md` (7), `skills/*/SKILL.md`
(16), `stacks/*.md` (9, including `dart-flutter.md`), `templates/report/{STYLE,RULES}.md`,
`templates/agents/*.tmpl`, `templates/knowledge/*.md`, `scripts/*.py` docstrings and
comments, `README.md`, `CHANGELOG.md`, `evals/README.md`, `hooks/examples/README.md`.
Checked against `docs/RESEARCH.md`, which is the ledger and was not edited.

Not counted as claims: the project's own eval targets in `evals/README.md` and
`SCORECARD.md` (they are commitments, measured by the suite), illustrative numbers in
`templates/knowledge/*` fixtures, and arithmetic derived in-file from a sourced formula
(e.g. "three runs catch a 5% flake about 14% of the time" = `1 − 0.95³`).

## Summary

| Classification | Distinct claims |
|---|---|
| **SOURCED** — present in RESEARCH.md, same value | 61 |
| **DRIFTED** — present, but value or attribution changed | 2 (both fixed) |
| **OVERSTATED** — RESEARCH hedges, the file did not | 2 (both softened) |
| **HEDGED** — unsourced tool-performance figures, rewritten qualitatively | 8 (all in `stacks/`) |
| **UNSOURCED** — asserted nowhere in RESEARCH.md | 9 |
| **Total** | 82 |

The 8 HEDGED claims were previously counted under UNSOURCED (17 → 9). They were the
tool-performance folklore in the playbooks — items H1–H8 below. None of them was given a
citation: the invented precision was removed and the actionable advice kept, with a
measurement the reader can take in its place. The 9 that remain UNSOURCED (items 1–3)
are load-bearing research figures outside `stacks/`, and sourcing them is the owner's
call, not a hedging job.

Plus 7 internal issues inside RESEARCH.md itself (below). No edits were made to
RESEARCH.md.

## UNSOURCED — ranked by damage if challenged

### 1. Mutation-operator productivity figures (4 numbers)
`skills/mutate/SKILL.md:56,61,62,65` and `scripts/arid_nodes.py:65,67-68,109,115,134`

> "Exotic operator sets buy **≤13%** over random selection"
> "**SBR** statement block removal | Roughly **68%** of generated mutants"
> "**ROR** relational operator replacement | **84.1% productive**"
> "**UOI** unary operator insertion | **74.5% productive**"

RESEARCH §15 records Google's volume numbers (16.9M mutants, 12.5% shown, 820→7, 15%→89%,
>90% same-fate lines, ≤7 per file) but **nothing about operator mix or per-operator
productivity**. These four numbers choose and weight the five operators the mutation
engine actually ships, so they are load-bearing code, not decoration. Most damaging
because `arid_nodes.py` states them as measurements ("ROR was judged productive 84.1% of
the time") in the file that implements the behaviour.
*To source:* the per-operator productivity table from Petrović & Ivanković (TSE 2021,
arXiv 2102.11378) or the companion ICSE 2021 paper, added as a §15 sub-table. If those
tables do not contain these values, the operator weighting has no evidence and the table
should state the choice as a design decision instead.

### 2. qodo-cover abandonment and its parent's funding (2 claims)
`agents/tech-scout.md:27-28`, `skills/scout/SKILL.md:46-47`

> "qodo-cover unmaintained since **June 2025** despite its parent raising **$70M**"

A dated factual assertion about a named company, offered as a *verified* example in a
list where every neighbour (`go-mutesting` 2021, `mutatest` 2022, `gremlins` 2026-03-30,
Octomind, OSS-Fuzz-Gen) is sourced in RESEARCH §15/§16. Damaging because it is the one
item a reader could falsify with a single repository visit, and because getting a funding
figure wrong about a named vendor is a different class of error from a rounding drift.
*To source:* the qodo-cover repository's last-commit date and a funding announcement,
recorded in §14 next to the Octomind wind-down with the same "verified <date>" treatment.

### 3. "Google needed 100+ suppression rules and **200+ function families**"
`scripts/arid_nodes.py:18`, repeated at `:764`

RESEARCH §15 supports "100+ hand-written rules" and "66,798 mutants" of developer
feedback. **"200+ function families" appears nowhere.** It is the stated justification
for the fuzzy family matcher, so it reads as a measurement borrowed from Google that the
ledger does not hold.
*To source:* the arid-node section of TSE 2021, or drop to "Google's rule set is large
and grown from feedback; ours is smaller."

Items 4–7 of this list have been moved to **HEDGED** below.

## HEDGED — rewritten 2026-11-12

Eight tool-performance claims in `stacks/*.md`. Each described how slow a tool feels,
not what the product promises; none had a source, and none was given one. The rewrite
keeps the advice, the flag and the command, and replaces the invented figure with a
measurement the reader can take. `grep` confirms no removed figure survives anywhere in
the repo.

### H1. Spring context cost — `stacks/jvm.md:163`

- **Before:** "A full-context test is **10–60x** the cost of a `@WebMvcTest`, and context reuse breaks whenever …"
- **After:** "A full-context test is dramatically slower to start than a `@WebMvcTest` — measure the gap in your own repo before deciding (`./gradlew test --profile`, or `logging.level.org.springframework.test.context.cache=DEBUG` to watch the context cache). Context reuse breaks whenever …"
- The "prefer slice annotations" rule and the `@MockBean`/`@DirtiesContext` grep are unchanged.

### H2. `build_stubbed` speedup — `stacks/ruby.md:377`

- **Before:** "**`build_stubbed` is 10–100x faster** — the biggest single speedup available in most Rails suites"
- **After:** "**`build_stubbed` avoids the database entirely** — usually the cheapest large win in a Rails suite. Confirm it on yours with `bundle exec rspec --profile` before and after"
- Both the 10x-wide range and the unmeasured superlative are gone; the ordering advice at `ruby.md:623` (`build_stubbed` > `build` > `create`) needed no change.

### H3. Go `-race` multipliers — `stacks/go.md:442`

- **Before:** "`-race` costs roughly **2–10x** runtime and **~5–10x** memory."
- **After:** "`-race` costs a large multiple of both runtime and memory — the Go race detector's own documentation gives the upstream figures, and `go test -count=1 ./pkg` with and without `-race` gives yours."
- The upstream numbers were deliberately *not* copied in: a figure enters RESEARCH.md first. The reader is pointed at the authority and at their own measurement.

### H4. `cargo check` speedup — `stacks/rust.md:138`

- **Before:** "`# type-check only; ~5-10x faster than a test build`"
- **After:** "`# type-check only; far cheaper than a test build (time both once)`"

### H5. PHP coverage drivers — `stacks/php.md:196-197`

- **Before:** "| **PCOV** | fast (**~1.2–2x**) |" / "| **Xdebug** | slow (**~2–10x**) |"
- **After:** "| **PCOV** | fast — consistently the cheaper driver |" / "| **Xdebug** | slow — consistently the costlier driver |", plus a new line under the table: "Put a number on "fast" and "slow" for *your* suite rather than trusting a generic multiplier: `time vendor/bin/phpunit` with no coverage, then time each of the two commands below."
- Counted as two claims, one per driver; the branch-coverage column, which is what the table is actually for, is untouched.

### H6. .NET filter rhetoric — `stacks/dotnet.md:163`

- **Before:** "`FullyQualifiedName~Foo` is the one you want **90% of the time**."
- **After:** "`FullyQualifiedName~Foo` is the one to reach for by default; use the others only when a substring match is ambiguous."
- Deleted rather than measured, as the audit recommended: there is nothing here to measure.

### H7. Testcontainers startup — `stacks/{go,jvm,js-ts,rust,ruby,php,python}.md`

- **Before:** "Docker daemon required; **2–20s** startup"
- **After:** "Docker daemon required; container startup costs seconds, not milliseconds — time it in your own suite" (the JVM row reads "real container startup, usually seconds — time it in your own suite", keeping its `testcontainers.reuse.enable=true` advice)
- **Seven** files, not the six the audit listed: `stacks/python.md:313` carried the same figure and was missed.

### H8. Playwright browser download — `stacks/{js-ts,dotnet,python}.md`

- **Before:** "Browser download **~1GB**"
- **After:** "Browser download is large — hundreds of megabytes, cache it in CI"
- **Three** files, not the two the audit listed: `stacks/python.md:317` was missed.

## DRIFTED — fixed

### D1. Bug-exposing-test direction was reversed
`agents/unit-test-engineer.md:23-24`

- **Before:** "That is an 8.4x difference, and bug-exposing tests fell from 8.51% to 2.98% at the same time."
- **After:** "That is an 8.4x difference, and bug-exposing tests moved the opposite way at the same time: 2.98% with the implementation visible, 8.51% with it hidden."
- **Why:** RESEARCH §2 records "Bug-exposing tests: **2.98% (buggy) vs 8.51% (fixed)**".
  The sentence's own ordering (visible → hidden) made 8.51% the *visible* figure, which
  inverts the finding and, read literally, argues *for* showing the model the
  implementation — the exact opposite of the rule the paragraph exists to state.

### D2. Invented attribution for the StrykerJS reuse figure
`stacks/js-ts.md:253`

- **Before:** "Stryker's own example project reports **~94% result reuse**."
- **After:** "StrykerJS reuses **~94%** of prior results (RESEARCH §15)."
- **Why:** the number matches §15; "Stryker's own example project" is a source attribution
  the ledger does not contain.

## OVERSTATED — softened

### O1. One Infer deployment restated as a general law
`README.md:47`

- **Before:** "**Batch bug lists get a ~0% fix rate; the same findings at review time get ~70%.**"
- **After:** "**Facebook's Infer got a ~0% fix rate as a batch bug list; the same findings, same analyzer, delivered at review time got ~70%.**"
- **Why:** RESEARCH §6 is a single analyzer at a single company. `agents/reporter.md` and
  `skills/report/SKILL.md` already attribute it correctly; the README did not.

### O2. Same generalization, unattributed and uncited
`skills/test/SKILL.md:57`

- **Before:** "because findings delivered against the change under review get acted on roughly 70% of the time, versus close to zero for a batch list."
- **After:** "… get acted on far more often than the same findings in a batch list — ~70% versus ~0% for Facebook's Infer, the one deployment that measured it (RESEARCH §6)."

**Left as-is, flagged:** the same ~70%/~0% generalization appears in
`stacks/{go,dotnet,php,rust,ruby,dart-flutter}.md` and `templates/report/STYLE.md:470`,
but each of those cites "RESEARCH §6" inline, so the provenance is one click away. If the
phrasing is tightened, tighten all seven together. Likewise "the 10%-flake tier" used as
a standing label for browser E2E across nine stacks: §17 says "~10% for some E2E
frameworks", so the label is within the source but drops the "some".

## Inside RESEARCH.md

Reported only — RESEARCH.md was not edited.

1. **§12 is the one section with numbers and no citations at all.** "Cost bottoms out at
   ~4–6 subagents", "a 4-agent system can burn 10x a single agent", "past ~6 hand-offs,
   roughly 4x the cost for half the success rate", "0.418–0.427 vs 0.379–0.389", the MAST
   taxonomy, and "zero salvageable output" all appear without a link. §12 is the source
   for the "choose 2–4 specialists" rule in `skills/test/SKILL.md:35` and for the squad's
   whole shape, so it is the least-sourced section carrying the most weight.
2. **§12 says "the squad has 8 roles"; `agents/` contains 7 files.** Either the
   orchestrator is being counted as a role (say so) or the number is stale.
3. **73% appears twice, for two different systems** — §4 "571 tests, **73% engineer
   acceptance**" (Meta ACH, FSE 2025) and §5 "Of what survived, **73%** was accepted by
   engineers" (TestGen-LLM, FSE 2024). Both may be genuine, but an identical figure
   crossing two papers is exactly the shape of a transcription collision. Worth
   re-checking one of them against the PDF.
4. **Uncited figures in otherwise well-cited sections:** §7's security-operations numbers
   (46–80% false positives, ~63% of alerts unaddressed, 71% analyst burnout) and §3's
   "LLM-as-judge agreement is overstated by 33–41pp once corrected for chance" carry no
   link. §7's numbers are quoted downstream in `agents/reporter.md:35` and
   `templates/report/STYLE.md:221` as though sourced.
5. **§11's headline "~7x" is derived from one of its two examples.** ULT's 91.79% vs
   12.57% is ~7.3x; Siddiq's 69.1% vs 1.3% in the same section is ~53x. Downstream
   (`agents/tech-scout.md:35`, `skills/scout/SKILL.md:61-63`) both pairs are listed and
   then summarised as "~7x", which under-reports the second.
6. **§15 reads as self-contradictory on the cap.** "Hard cap **≤7 mutants per file**" sits
   in the same table as "Median actually surfaced **2** per changelist (99th pct: **43**)".
   Different denominators — per file vs per changelist — but the table does not say so.
7. **§19 rounding:** "**45%** (74/161)" — 74/161 is 46.0%. Keep 45% if that is the
   paper's own rounding, but the parenthetical invites the reader to check the division.

**URLs:** every link resolves to the paper, post or repository its claim names; none is
obviously attached to the wrong claim. Two notes: §4's MIST-RL cites alphaxiv rather than
arXiv, and the 2026 arXiv identifiers (2601.13097, 2603.01409, 2607.22880, 2607.22883,
2607.28871) could not be verified offline — they should be re-checked before any external
publication.

## How to keep this clean

1. **A number enters RESEARCH.md before it enters any other file.** The two violations
   that preceded this audit, and all 17 originally listed as UNSOURCED, are the
   same failure: a figure written straight into an agent or playbook because it
   sounded settled.
2. **Carry the section number with the number.** `(RESEARCH §15)` inline costs eight
   characters and makes drift auditable with `grep`. Where that is present, this audit
   found no unsourced claims.
3. **Quote the ledger's value and its hedge.** If RESEARCH says "~10% for *some* E2E
   frameworks", the "some" travels with it. If it says "Facebook's Infer", the attribution
   travels with it. A hedge dropped downstream is the same defect as a changed digit.
4. **Tooling folklore is a claim.** "10–100x faster", "~1GB", "90% of the time" are
   covered by the same rule as a research percentage — `templates/report/RULES.md:30`
   already says so. Either source it upstream or write it qualitatively.
5. **Derived arithmetic is fine; state the formula.** `1 − (1−p)ⁿ` results need no
   citation beyond §17 as long as the formula is visible next to the result.
6. **When no source exists, say so in the file.** `stacks/go.md:370-378` is the model:
   "no published breakdown … practitioner consensus, not measured data." That sentence is
   worth more than a plausible number.
