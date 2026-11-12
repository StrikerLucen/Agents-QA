# Citation audit — 2026-11-12

> **SUPERSEDED IN PART — re-verified 2026-11-12 (second pass).** The "9 UNSOURCED
> claims" headline below is wrong twice over, and `HANDOFF.md` §4 inherited the error.
> The body of this file enumerates **7** distinct claims, not 9, and **all 7 are present
> in `docs/RESEARCH.md` today** — six of them in **§20**, a section the first pass never
> opened. Nothing in the original list is unsourced. Three *different* numbers, never
> audited, are. See **[Dossier — second pass](#dossier--second-pass-2026-11-12)**, which
> is the live list. Sections below it are kept as the historical record.

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
| ~~**UNSOURCED** — asserted nowhere in RESEARCH.md~~ | ~~9~~ → **0** (see dossier) |
| **UNSOURCED** — found by the second pass, not in the original list | **3** |
| **Total** | 82 |

The 8 HEDGED claims were previously counted under UNSOURCED (17 → 9). They were the
tool-performance folklore in the playbooks — items H1–H8 below. None of them was given a
citation: the invented precision was removed and the actionable advice kept, with a
measurement the reader can take in its place.

**The "9" was never real.** Items 1–3 below enumerate 4 + 2 + 1 = **7** distinct claims;
the summary table said 9 and the body never matched it. (The original "17" does not
reconcile either: items 1–7 enumerated 7 + 8 = 15.) And every one of the 7 *is* in
`docs/RESEARCH.md`, and has been since the initial commit — `git diff 8a2f1a1 HEAD --
docs/RESEARCH.md` is empty, so the ledger has not moved at all. The first pass checked
**§15** and concluded the operator figures appear "nowhere"; **§20, "Mutation operators:
which five, and how to weight them"**, holds the complete table with its citation, and
**§15** holds "200+ function families" and the qodo-cover paragraph verbatim. The
proximate cause is the audit's own rule 2: not one of the downstream files carries an
inline `(RESEARCH §20)` pointer, so a `grep` for provenance finds nothing.

Plus 7 internal issues inside RESEARCH.md itself (below). No edits were made to
RESEARCH.md.

---

# Dossier — second pass, 2026-11-12

Prepared so the owner can decide in minutes. **No decision in here has been taken.** Every
source below was opened and quoted today; where nothing was found, this file says "no
source found" and leaves the claim open.

One edit was made to `docs/RESEARCH.md` in this pass, and only one: §15's qodo-cover
paragraph now carries **Qodo's own funding announcement** as the primary source, with
TechCrunch, Globes and the existing SiliconANGLE link beside it as corroboration. That is
a verified reference, located and quoted, not a replacement for the owner's call on
anything else.

### Verdict in one table

| # | Claim | Live locations | Sourceable? | Recommendation |
|---|---|---|---|---|
| D-1 | "Exotic operator sets buy **≤13%** over random selection" | 2 | **Yes — number exact, sentence misapplies it** | **A**, with corrected wording |
| D-2 | SBR is **~68%** of generated mutants | 2 | **Yes — exact** | **A** (add inline §20 pointer) |
| D-3 | ROR **84.1% productive** | 4 | **Yes — exact, verbatim quote** | **A** |
| D-4 | UOI **74.5% productive** | 4 | **Yes — exact, verbatim quote** | **A** |
| D-5 | qodo-cover unmaintained since **June 2025** | 2 | **Yes — primary (the repo itself)** | **A** |
| D-6 | its parent raised **$70M** | 2 | **Yes — primary (Qodo's announcement)** | **A** |
| D-7 | Google needed **200+ function families** | 3 | **Yes — exact, verbatim quote** | **A** |
| **N-1** | **SBR 0.80 / AOR 0.79 / LCR 0.78 "productive"** | 3 | **NO SOURCE FOUND — the paper does not report them** | **C — delete** |

D-1…D-7 are the original list. **N-1 is new**, was never audited, and is the only
genuinely unsourced material in this area.

### Risk flag — what a user actually sees

**None of D-1…D-7 or N-1 reaches `README.md`, `docs/QUICKSTART.md`, `docs/FAQ.md` or
`CHANGELOG.md`** (`grep`-confirmed, all eight claims). The exposure is one tier down and
real: `agents/tech-scout.md` and `skills/mutate/SKILL.md` are prompts the model is
instructed to reproduce, and `scripts/arid_nodes.py` prints its `why=` strings — so
**"ROR mutants were judged productive 84.1% of the time"** and **"The weakest of the five
at 74.5% productive"** are emitted into user-visible mutation output, and
`agents/tech-scout.md`'s qodo sentence is designed to be copied into the user's
`.testing/tooling-radar.md`. Those four are the ones that leave the repo. All four are
**verified true**, which is the good news; N-1 does not print, which is why it has gone
unnoticed.

---

## D-1. "Exotic operator sets buy ≤13% over random selection"

**Verbatim, and where it lives**

- `skills/mutate/SKILL.md:56` — "Exotic operator sets buy **≤13%** over random selection, so there is no reason to carry more than these:"
- `scripts/arid_nodes.py:65-66` — "Exotic operator sets buy <=13% over plain random selection, which does not pay for the mutants they add."
- Ledger: `docs/RESEARCH.md:646-649` (§20) — "the maximum achievable improvement of **any** reduction strategy over plain random sampling is about **13%**", and `:657` — "the ceiling there is 13%".

**Load-bearing for:** the decision to ship exactly five operators and reject a larger set.
It is justification prose — **no code branches on 13**, and no threshold is derived from
it. If it were deleted the engine would behave identically; what is lost is the answer to
"why not more operators?".

**Source: FOUND.** Gopinath, Alipour, Ahmed, Jensen & Groce, *On the Limits of Mutation
Reduction Strategies*, ICSE 2016, DOI [10.1145/2884781.2884787](https://dl.acm.org/doi/10.1145/2884781.2884787).
Abstract, read in full at the NAU research portal on 2026-11-12
(<https://experts.nau.edu/en/publications/on-the-limits-of-mutation-reduction-strategies/>):

> "Our results show that the limit in improvement of effectiveness over random sampling
> for real-world open source programs is a mean of only **13.078%**. Interestingly,
> **there is no limit to the improvement that can be made by addition of new mutation
> operators.** […] We conclude that more effort should be focused on enhancing mutations
> than removing operators in the name of selective mutation for questionable benefit."

**Honesty about fit — read this before choosing.** The *number* is exact: 13.078% → "≤13%"
is a fair rounding, and §20 already cites the right paper. The *sentence* is not. The
13.078% bound is on **reduction** strategies — schemes that select a subset of mutants, of
which selective/operator-based mutation is one. The paper says in the same breath that
**adding** operators has *no* such ceiling. "Exotic operator sets buy ≤13%" applies the
bound in the one direction the paper explicitly exempts. §20's own framing is faithful
("any reduction strategy"); its heading "Why not a larger operator set" and its closing
"the ceiling there is 13%" are not.

What the paper *does* support for this project is stronger and simpler: operator-based
selection is **not** superior to uniform random sampling, so a bigger operator catalogue
is not where the leverage is.

**Options**

- **(A) Cite, with the direction corrected.** No new RESEARCH entry needed — §20 already
  holds the citation. The change is downstream wording, in files this pass does not own:
  - `skills/mutate/SKILL.md:56` → "Selective operator sets cannot beat plain random
    selection by more than ~13% (Gopinath et al., ICSE 2016 — RESEARCH §20), and
    operator-based selection is not superior to uniform random sampling, so the leverage
    is in suppression, not in a bigger catalogue:"
  - `scripts/arid_nodes.py:65-66` → the same sentence in comment form, plus "(RESEARCH §20)".
  - `docs/RESEARCH.md:657` → "the ceiling on *reduction* strategies is 13%, and adding
    operators is explicitly not bounded by it — the reason we stop at five is cost, not
    that ceiling."
- **(B) Restate as a design choice.** "We ship the five operators Google measured, because
  a set we can explain and weight beats a set we cannot. (RESEARCH §20)" Loses the
  argument for *why* a bigger set is a poor investment.
- **(C) Delete.** The operator table stands on §20's distribution data alone. Loses the
  pre-emptive answer to "why not add more operators?", which is the first question a
  reviewer asks about a mutation engine.

**Recommendation: (A).** The citation is real, the number is right, and the only defect is
a direction of application that takes one sentence to fix. Leaving it as-is is the one
option that is actually wrong — a reader who opens the cited paper finds it saying the
opposite about added operators, which is worse than no citation at all.

---

## D-2. "SBR is roughly 68% of generated mutants"

**Verbatim, and where it lives**

- `skills/mutate/SKILL.md:61` — "| **SBR** statement block removal | Roughly **68%** of generated mutants |"
- `scripts/arid_nodes.py:127` — `why="Statement removal is ~68% of Google's generated mutants: …"` (this string is printed in output)
- Ledger: `docs/RESEARCH.md:636` (§20) — "| **SBR** statement block removal | 68% | 12.6% | — |"

**Load-bearing for:** the rationale given for `SBR weight=3.0` and the explanation shown to
a developer beside an SBR mutant. Not a threshold; no branch reads it.

**Source: FOUND, exact.** Petrović, Ivanković, Fraser & Just, *Practical Mutation Testing
at Scale*, TSE 2021, [arXiv:2102.11378](https://arxiv.org/abs/2102.11378). **TABLE IV**,
read at <https://ar5iv.labs.arxiv.org/html/2102.11378> on 2026-11-12:

| Operator | Generated mutants | Share | Survivability |
|---|---|---|---|
| SBR | 11,522,932 | **68.0%** | 12.7% |
| UOI | 3,137,375 | 18.5% | 9.6% |
| LCR | 1,305,499 | 7.7% | 16.3% |
| ROR | 672,009 | 4.0% | 14.7% |
| AOR | 297,333 | 1.8% | 13.5% |
| Total | 16,935,148 | 100% | 12.5% |

**Options:** (A) cite — nothing to add to RESEARCH; add `(RESEARCH §20)` inline at
`skills/mutate/SKILL.md:61` and in the `arid_nodes.py` docstring. (B) n/a. (C) delete —
loses the explanation for why the bluntest operator is also the commonest.

**Recommendation: (A).** This was never unsourced; it was unfindable.

---

## D-3. "ROR — 84.1% productive"

**Verbatim, and where it lives**

- `skills/mutate/SKILL.md:62` — "| **ROR** relational operator replacement | **84.1% productive** — weight toward it |"
- `scripts/arid_nodes.py:67-68` — "ROR was judged productive 84.1% of the time and UOI only 74.5% -- so ties lean toward ROR and away from UOI."
- `scripts/arid_nodes.py:118` — `weight=4.0, productive=0.841`
- `scripts/arid_nodes.py:121` — `"and ROR mutants were judged productive 84.1% of the time -- the best of the five, so ties go here."` **(printed to the user)**
- Ledger: `docs/RESEARCH.md:639` (§20).

**Load-bearing for:** **real behaviour.** `ROR weight=4.0` is the highest of the five and
is read at `scripts/arid_nodes.py:508` in the weighted draw, so this figure is the stated
reason ROR wins ties on a line where several operators apply. Changing the justification
without changing the weight would leave the weight unexplained.

**Source: FOUND, exact, verbatim.** Same TSE 2021 paper, §V:

> "For example, ROR mutants are productive **84.1%** of the time, whereas, UOI mutants are
> only productive **74.5%** of the time."

**Options:** (A) cite — already in §20; add the inline pointer in both files. (C) delete —
would strand `weight=4.0` with no justification.

**Recommendation: (A).**

---

## D-4. "UOI — 74.5% productive"

**Verbatim, and where it lives**

- `skills/mutate/SKILL.md:65` — "| **UOI** unary operator insertion | **74.5% productive** — weight away from it |"
- `scripts/arid_nodes.py:68`, `:144` (`weight=1.0, productive=0.745`), `:146` — "The weakest of the five at 74.5% productive -- kept because it reaches lines no other operator touches…" **(printed to the user)**
- Ledger: `docs/RESEARCH.md:637` (§20).

**Load-bearing for:** `UOI weight=1.0`, the lowest weight in the table and the one that
actually suppresses UOI selection. Same status as D-3.

**Source: FOUND, exact** — the same sentence quoted under D-3.

**Recommendation: (A).** D-3 and D-4 are one decision; they come from one sentence.

---

## D-5. "qodo-cover unmaintained since June 2025"

**Verbatim, and where it lives**

- `agents/tech-scout.md:27-28` — "…`gremlins` semi-dormant since **2026-03-30**; qodo-cover unmaintained since **June 2025** despite its parent raising **$70M**."
- Ledger: `docs/RESEARCH.md:389-390` (§15).
- **The audit's second location is stale.** It lists `skills/scout/SKILL.md:46-47`; that
  paragraph no longer exists — another agent rewrote `skills/scout/SKILL.md` in the
  working tree (uncommitted) and the qodo sentence went with it. `agents/tech-scout.md` is
  the only live prose location today. *This is the partial-fix pattern the handoff warns
  about, running the other way: the claim was removed from one file and left in the other.*

**Load-bearing for:** the "**Popularity is not maintenance**" rule in `tech-scout`, of
which it is the worked example, and for the radar's maintenance-first check order. The
rule survives without it; the persuasion does not, and this is the example a user is most
likely to see copied into `.testing/tooling-radar.md`.

**Source: FOUND — primary.** The repository itself,
<https://github.com/qodo-ai/qodo-cover>, read 2026-11-12. README banner, verbatim:

> "⚠️ This repository is no longer maintained. Please fork it if you wish to continue
> development or use it in your own projects."

Dated **2025-06-15**; 5.7k stars; AGPL-3.0 — all three matching §15. The release date
(v0.3.10, 2025-05-21) was **not** re-confirmed in this pass; the repo's releases page was
not opened. Treat that one sub-figure as carried forward, not re-verified.

**Options:** (A) cite — §15 already holds it; this pass added a "re-verified 2026-11-12"
note. Optionally add the repository URL inline at `agents/tech-scout.md:27`. (B) n/a —
it is a fact, not a design choice. (C) delete — loses the single most legible example of
the rule.

**Recommendation: (A).** This is the claim the handoff treated as most dangerous, and it
is simply true. Re-verify the dates before any external publication, since a maintainer
can un-archive a repo.

---

## D-6. "…despite its parent raising $70M"

**Verbatim, and where it lives:** same two locations as D-5 (`agents/tech-scout.md:28`;
`docs/RESEARCH.md:391-401`).

**Load-bearing for:** the second half of "Funding, stars and a good idea are all
independent of whether anyone will fix your bug" — the funding figure *is* the argument.

**Source: FOUND — primary, and it is exact.** Qodo's own announcement,
<https://www.qodo.ai/blog/qodo-70m-series-b-shift-to-artificial-wisdom/>, 30 March 2026:

> "Today, we are thrilled to announce that Qodo has raised **$70 million in Series B
> funding**, bringing total capital raised to $120 million."

Led by Qumra Capital. Corroborated independently the same day by
[TechCrunch](https://www.techcrunch.com/2026/03/30/qodo-bets-on-code-verification-as-ai-coding-scales-raises-70m/),
[Globes](https://en.globes.co.il/en/article-1001538970) and the
[SiliconANGLE](https://siliconangle.com/2026/03/30/ai-generated-code-verification-startup-qodo-raises-70m/)
piece §15 already cited. The handoff's worry about drift in secondary reporting was the
right instinct and did not materialise: amount, round and date agree across all four.

**Action taken in this pass:** §15 now leads with the primary announcement and its quote,
keeping SiliconANGLE as corroboration. That is the whole of the RESEARCH.md edit.

**Recommendation: (A).** Settled.

---

## D-7. "Google needed 100+ suppression rules and 200+ function families"

**Verbatim, and where it lives**

- `scripts/arid_nodes.py:18-20` — "Google needed 100+ suppression rules and 200+ function families. We need maybe 15-20 to capture most of the benefit, which is what this file is."
- `scripts/arid_nodes.py:776-779` — "The fuzzy family matcher. Google needed 200+ function families; naming one at a time does not scale, and the names in a family rhyme."
- Ledger: `docs/RESEARCH.md:357` (§15) — "**plus fuzzy name matching covering 200+ function families**".

**Load-bearing for:** the existence of `ARID_FAMILY_RE` (`scripts/arid_nodes.py:786+`) — the
fuzzy family matcher. It is the stated reason the engine carries a regex family matcher
beside its explicit rules, so it justifies a whole mechanism, not a constant.

**Source: FOUND, exact, verbatim.** Same TSE 2021 paper, §III-B3 (arid-node suppression),
read 2026-11-12:

> "we have fuzzy name suppression rules for **more than 200 function families**."

The first pass's "appears nowhere" is wrong on both counts: it is in the paper, and it was
already in §15.

**Options:** (A) cite — add `(RESEARCH §15)` to both `arid_nodes.py` comments. (C) delete
and fall back to "Google's rule set is large and grown from feedback; ours is smaller",
which the first pass suggested — loses the specific justification for the fuzzy matcher.

**Recommendation: (A).** The number is the paper's, word for word.

---

## N-1. NEW — three per-operator productivity constants with no source at all

**Verbatim, and where it lives** — `scripts/arid_nodes.py`:

- `:112-114` (the header that gives them their status) — "`weight` is the selection weight when several apply to one line; `productive` is the share of mutants of that kind developers judged worth seeing, and is what the weights are derived from."
- `:125` — `weight=3.0, productive=0.80,` (SBR)
- `:132` — `weight=3.0, productive=0.79,` (AOR)
- `:138` — `weight=3.0, productive=0.78,` (LCR)

**Source: NO SOURCE FOUND.** TSE 2021 **TABLE IV does not have a productivity column**;
productivity is reported in prose for exactly two operators, ROR and UOI, in the one
sentence quoted under D-3. `docs/RESEARCH.md:636,638,640` (§20) is honest about this —
those three cells read "**—**". Nothing in the paper, and nothing anywhere else located in
this pass, gives a productivity figure for SBR, AOR or LCR. The three values 0.80 / 0.79 /
0.78 appear to be interpolations between the two real endpoints, written in the descending
order of the weights they sit beside.

They are stated as **measurements** — the comment above them says they are "the share of
mutants of that kind developers judged worth seeing", which is a claim about what Google's
developers did. That is the exact failure mode `CLAUDE.md` names: *"Never state a number
you did not measure."* And it is in the implementation file, three lines below two figures
that *are* real, which is what makes them read as equally real.

**Load-bearing for: nothing.** This is the cheapest resolution in the whole dossier and
the owner should know it first. The `productive` key is **never read at runtime** —
`grep -n "productive" scripts/arid_nodes.py` returns only the five definitions and three
comment strings; the weighted draw at `:508` reads `OPERATORS[o]["weight"]` and nothing
else. No test references it (`grep -rn "productive" tests/` → no matches). Deleting all
three changes no behaviour and breaks no test. (The comment's "is what the weights are
derived from" is itself inaccurate: three operators share `weight=3.0` while carrying
three different `productive` values, so the weights cannot have been derived from them.)

**Options** — in `scripts/arid_nodes.py`, which this pass does not own:

- **(A) Cite.** Not available. No source exists to cite, and inventing one is the worst
  outcome available here.
- **(B) Restate as a design choice.** Keep the keys, relabel them honestly:
  ```python
  # `weight` is the selection weight when several apply to one line. `productive` is
  # Google's measured figure where they published one (ROR, UOI — RESEARCH §20) and
  # `None` where they did not; the weights are our design choice, informed by those two
  # endpoints and by each operator's share of generated mutants, not derived from a
  # per-operator measurement that does not exist.
  ```
  with `productive=None` for SBR, AOR and LCR. Keeps the shape for a future measurement.
- **(C) Delete the three values and the clause.** Drop `productive=` from the SBR, AOR and
  LCR entries, and cut "and is what the weights are derived from" from `:114`. Nothing is
  lost — no reader, no test and no code path consumes them.

**Recommendation: (C), with (B) as the fallback** if the owner wants the slot kept for a
real measurement later. A number nobody reads is pure liability: it cannot help, and the
first person who greps for a productivity figure finds three fabricated ones sitting
beside two genuine ones. (C) is a four-line deletion with zero behavioural risk.

---

## Also found in the second pass — cheap to fix, not owner-level

Reported, not acted on. The first two touch `docs/RESEARCH.md`, which this pass owns; both
are left for the owner because they change figures.

1. **§20's survivability column is off by 0.1pp in two cells.** `docs/RESEARCH.md:636-637`
   gives SBR **12.6%** and UOI **9.5%**; TABLE IV gives **12.7%** and **9.6%**. LCR, ROR
   and AOR match exactly. Harmless in itself, but it is the drift signature the audit's
   rule 3 exists to catch.
2. **§15's "12.5% of mutants ever shown to a developer" and TABLE IV's total survivability
   of 12.5% are the same digits** — exactly the shape flagged under "Inside RESEARCH.md"
   item 3 for the two 73%s. They may well both be true (a mutant must survive to be shown,
   and the shown rate could coincide). **Worth one look at the PDF before publication**;
   this pass did not resolve it and does not assert either is wrong.
3. **§20's "~87% of mutant runs kill the mutant"** is supported and slightly conservative:
   the paper says "Overall, **87.5%** of all generated mutants are killed by the initial
   test suite."
4. **§20's "context-based selection beats uniform random by >40% on survivability and ~50%
   on productivity"** is exact: "Context-based selection improves the probability that a
   generated mutant survives by more than 40% and the probability that a generated mutant
   is productive by almost 50%."
5. **The root cause, and the only process fix worth making:** `skills/mutate/SKILL.md` and
   `scripts/arid_nodes.py` carry **zero** `RESEARCH §` pointers between them
   (`grep -n "RESEARCH §"` → no matches in either). Every claim in both files is sourced;
   none of them says so. That is what cost this audit two passes, and it is rule 2 of
   "How to keep this clean", already written down and not applied to these two files.

---

## UNSOURCED — ranked by damage if challenged

> **Historical — first pass, superseded.** Items 1–3 below are **all three resolved**: see
> D-1…D-7 above. Their "appears nowhere in RESEARCH.md" findings were made against §15
> alone and are factually wrong; §20 holds the operator table and §15 holds the other two.
> Kept verbatim as the record of what the first pass concluded and why.

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
