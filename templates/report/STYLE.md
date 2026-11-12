# testsquad report style contract

Every rule here is **checkable**. An agent that produces a report runs the
checklist at the end of this file against its own draft before publishing, and a
reviewing agent runs it again. A rule you cannot answer yes/no on does not belong
in this document.

The goal is stated once, plainly: **reports must be easy and fast to understand,
so that people never get bored by them and start treating errors as routine.**
Routine is the failure. A number that is merely missing can be added later; a
reader who has stopped believing the report cannot be won back cheaply.

Evidence for each rule is cited to `docs/RESEARCH.md`.

---

## 1. One glance, one answer

The first line of any report answers two questions in **one sentence**:
*is anything on fire, and what do I do first?*

```
VERDICT: Not safe to merge — checkout drops EU discounts; fix PR #482 first.
```

```
VERDICT: Safe to merge — nothing reproduced; 2 items parked in the backlog.
```

Rules:
- Exactly one verdict line, at the very top, before any table, chart or count.
- It names a **state** (safe / not safe / blocked / needs a decision) and **one
  next action**. Never both a hedge and a list.
- Maximum 25 words. If it does not fit, the report has more than one story and
  should be split.
- No counts in the verdict line. "47 failures" is not an answer to "what do I do".

*Why:* Google's bug-prediction study (RESEARCH §6, Lewis et al. ICSE 2013)
ranked files correctly and changed developer behaviour **not at all**. A finding
must be actionable, its reasoning obvious, and it must arrive in the workflow.
A correct ranking nobody acts on is a failed feature.

---

## 2. The report is a queue to empty, not a dashboard to scan

The top section contains **only** items that are:

- **new** (not seen in the previous report), **or**
- **reproduced** (status flipped from unconfirmed to confirmed), **and**
- **untriaged** (no human has yet decided what to do with it).

Anything already triaged, already acknowledged, or carried over unchanged belongs
below the fold or in the backlog. The reading list is the untriaged list.

**Hard cap: 10 items.** If 11 qualify, the 11th goes to the backlog. The cap is
not a rendering limit, it is the contract.

**Show the cap honestly.** Never silently truncate:

```
10 of 37 shown, ranked by business risk. Full list: ./backlog.md
```

Checkable:
- [ ] Top section item count ≤ 10.
- [ ] If any item was dropped, the "N of M shown, ranked by …" line is present
      and M is the true total.
- [ ] No item in the top section has a triage decision recorded against it.

*Why:* Google SRE's alerting budget is **≤2 incidents requiring intervention per
12-hour shift**, plus the instruction "you'll be tempted to add a lot of stuff;
resist" (RESEARCH §7). ReportPortal's **"To Investigate"** bucket is the same
idea: the report distinguishes triaged from untriaged and carries human decisions
forward, so the reading list only ever contains what nobody has looked at yet
(RESEARCH §10). A queue that is never emptied stops being read.

---

### First install: when everything is new

Rules 2 and 16 collide the first time testsquad runs on a repository. "New and
untriaged" and "scoped to the change under review" select the same thing — everything —
and the result is the wall of legacy findings that gets a tool ignored permanently.

**The tiebreak: on a first run, timing wins over novelty.**

| Situation | Main list holds | Everything else goes to |
|---|---|---|
| First run on a repo (no prior report exists) | Findings on code the **risk map ranks critical**, capped at 10 | The **backlog**, counted, non-blocking, explicitly labelled "pre-existing" |
| Any later run | Findings that are **new since the last report** | Known issues, collapsed |
| Run scoped to a diff | Findings **attributable to this change** | Pre-existing backlog |

Two consequences worth stating in the report itself so nobody is misled:

- On a first run, say plainly: *"This is a first scan. 7 findings shown from the
  highest-risk code; 143 pre-existing findings are in the backlog, not shown."* A reader
  who thinks they are seeing everything will draw the wrong conclusion from a short list.
- A pre-existing finding **never blocks** a PR. Nobody should be made responsible for a
  defect they did not introduce, and doing it once is how the gate gets disabled.

The backlog is worked down through `/testsquad:plan`, deliberately and in risk order —
not by ambushing whoever happens to touch the file next.

## 3. Symptom, not cause

Headlines describe **what a user or a system experiences**. The cause — stack
frames, module names, the failing assertion — lives *inside* the item, never in
its title.

This is the single most teachable rule in this document. Rewrite pairs:

| ✗ Cause in the headline | ✓ Symptom in the headline |
|---|---|
| `assertion failure in tax computation module` | **Checkout fails for EU customers when a discount is applied to a taxed line** |
| `NullPointerException in OrderSyncService.merge()` | **Orders edited on two devices lose the later edit instead of merging** |
| `test_retry_backoff timeout after 30s` | **A failed payment retries forever instead of giving up after 3 attempts** |
| `schema mismatch: expected string, got null in customer.email` | **Customers created without an email can't be invoiced — the invoice screen shows a blank recipient** |
| `12 snapshot tests failing in InvoicePdf.test.tsx` | **Every generated invoice PDF is missing the tax breakdown line** |
| `flaky: 3/10 runs fail in VisitSyncSpec` | **Visit sync sometimes reports success without saving — 3 runs in 10** |

Checkable:
- [ ] No headline contains a stack frame, exception class name, file path, test
      function name or the word "assertion".
- [ ] Every headline could be read aloud to a product manager and understood.
- [ ] The cause appears inside the item body, under "Why it happens".

*Why:* Google SRE: **alert on symptoms, not causes** (RESEARCH §7). A cause-named
finding forces the reader to reconstruct the impact before they can decide
whether to care — and most readers stop at that point.

---

## 4. Plain language

Write for a developer who last opened this area **six months ago** and has 90
seconds. QA jargon is banned from headlines and from the verdict line, and should
be rare everywhere else.

Banned phrasings and their replacements:

| ✗ Banned | ✓ Write instead |
|---|---|
| "the assertion failed" | "it returned X when it should return Y" |
| "regression detected in …" | "this used to work; it broke in \<commit\>" |
| "non-deterministic behaviour observed" | "it passes sometimes and fails sometimes — 3 runs in 10" |
| "coverage gap identified" | "nothing tests this code path" |
| "test suite instability" | "these 4 tests fail at random and block PRs" |
| "the SUT does not conform to the expected contract" | "the API returns a different shape than the client expects" |
| "P0 critical blocker" | "this stops customers checking out — fix before merge" |
| "validation error encountered" | "the form accepts an empty email and saves it" |
| "anomaly in the data layer" | name the table and the wrong value |
| "please investigate" | say what to run, or what decision is needed |

Also banned anywhere in the report: "comprehensive", "robust", "best practices",
"world-class", "leverage", "utilize", any score with no definition next to it.

Checkable:
- [ ] Grep the draft for the banned list above; zero hits in headlines and in the
      verdict line.
- [ ] Every acronym is expanded on first use, or is one of: PR, CI, API, SQL, UI.
- [ ] No sentence in the top section is longer than 25 words.

---

## 5. Inverted pyramid

Fixed section order. No exceptions, no reordering per run — readers learn the
shape once.

1. **Verdict** — one sentence (§1).
2. **What changed since the last report** — new / fixed / reopened / still open,
   as counts with a delta. Three lines maximum.
3. **Top actions** — ranked, each with an owner-sized effort estimate.
4. **Findings** — the capped queue (§2), with evidence level per row.
5. **Coverage & health** — floor detection, flaky/quarantined status.
6. **Exploratory charters** — *Explore (target) with (resources) to discover
   (information)* (Hendrickson, RESEARCH §10).
7. **Appendix (collapsed)** — unconfirmed items, raw logs, methodology, full
   command list.

A reader who stops after section 1 must not be misled. A reader who stops after
section 3 must have everything they need to act today.

Checkable:
- [ ] Sections appear in exactly this order.
- [ ] Nothing in sections 4–7 contradicts the verdict line.

---

## 6. Emphasis is rationed and means something

Fixed vocabulary. These are the only meanings these signals carry:

| Signal | Meaning | May be used when |
|---|---|---|
| 🔴 **red** | Reproduced **and** critical | Both conditions true. Never one of the two. |
| 🟠 **amber** | Needs attention this cycle | Reproduced, not critical; or critical but not yet reproduced *and* in the appendix |
| ⚪ **grey** | Informational | Everything else |

- **Bold** marks **the one action** in a line. Never a whole sentence, never a
  whole row, never a heading (headings are already emphasised).
- No italics for emphasis. Italics are for quoted user-facing text only.
- No ALL CAPS except the fixed labels `VERDICT:`, `S1`–`S4`.
- Red is a budget, not a palette. If everything is red, nothing is.

**Hard check — the 30% rule:** count the rows in the findings table. Count the
rows carrying any emphasis (red, amber, bold, or an alert icon). **If emphasised
rows exceed 30% of total rows, the report fails review and must be re-ranked
before publishing.** This is not a suggestion; it is a gate.

Small-table floor, so the gate stays usable on short reports: the allowance is
`max(2, floor(0.30 × rows))`. A 6-row report may emphasise 2 rows; a 4-row report
may emphasise 2; a 20-row report may emphasise 6. The floor is never 3.

Checkable:
- [ ] `emphasised_rows ≤ max(2, floor(0.30 × total_rows))`.
- [ ] Every red item is both reproduced and S1/S2.
- [ ] No bolded full sentences.

*Why:* RESEARCH §7 — industry false-positive rates of 46–80%, **~63% of alerts
unaddressed**, **71% of analysts burned out**. Uniform urgency is how a report
teaches people that urgency means nothing.

---

## 7. Every number links to its proof

Every count, rate, percentage and claim carries the **exact command that
reproduces it**, in a copyable block, runnable from the repo root.

```bash
npm test -- src/checkout/discount.test.ts --runInBand
# 3 failed, 41 passed — fails at discount.test.ts:88
```

Rules:
- The command is literal and complete. No `…`, no "run your test suite".
- If a claim cannot be re-run, it does not belong in the report. Move it to the
  appendix as a suspicion (§8) or delete it.
- Commit SHA and date are stated once in the header, so every command has a known
  starting state.

Checkable:
- [ ] Every number in sections 1–5 has a command block within its item.
- [ ] Every command block is copy-pasteable as-is.

*Why:* Bettenburg et al., TSE 2010, 466 developers (RESEARCH §10): the most
valued contents of a report are **steps to reproduce, stack traces and test
cases** — and these are exactly what reporters supply least. A **wrong repro
delays a fix more than a missing one**, so commands must be literal and verified,
never approximated.

---

## 8. Evidence levels

Every finding carries exactly one badge:

| Badge | Level | Meaning |
|---|---|---|
| ✅ | Reproduced | Ran it, saw it fail, ran it again, saw it fail again. Command included. |
| 👁 | Observed | Seen failing at least once in a real run, not yet re-run deterministically. |
| 📖 | Code-read only | Found by reading code. Never executed. |
| 💭 | Suspicion | A hypothesis. No execution, no complete reading. |

Rules:
- **Only ✅ and 👁 may appear in the main findings list.**
- 📖 and 💭 go to the collapsed **"Unconfirmed"** appendix, each **with the
  reason it did not qualify** ("could not run — needs a staging DB",
  "reproduced 0 of 10 runs", "no fixture for EU tax rates").
- **📖 and 💭 are never counted in the bug total.** The headline bug count is
  `count(✅) + count(👁)`, and the report says so.
- Never surface a test that has not been executed at least once (RESEARCH §14).

Checkable:
- [ ] Every finding has exactly one badge.
- [ ] No 📖 or 💭 in the main list.
- [ ] Every appendix item states its disqualifying reason.
- [ ] Bug total = count of ✅ plus 👁, and nothing else.

---

## 9. Bugs vs issues — two lists, never one

- A **bug** is a problem **in the product**.
- An **issue** is a problem **with the testing**: a broken fixture, a missing
  mock, an environment that won't start, a flaky test, *and anything that could
  not be reproduced*.

They are reported in **separate lists with separate counts**. A single
undifferentiated "failed: 47" is forbidden.

Classify every failure before counting it, into: **product defect / test defect /
infrastructure**.

Checkable:
- [ ] Two distinct lists exist, with distinct headings and counts.
- [ ] No failure appears without one of the three classifications.
- [ ] The word "failures" never appears as a single blended number.

*Why:* Session-based test management (Bach) separates bugs from issues, and this
maps exactly onto reproduced vs. unreproduced. Allure's Categories require the
product-defect / test-defect / infrastructure split **before** counting
(RESEARCH §10).

---

## 10. Cluster by root cause

40 test failures caused by one missing null guard are **one finding with a count
of 40**, not 40 findings.

```
🔴 ✅ Orders with no customer email crash the invoice screen   ×40 tests
     One missing null guard — src/invoice/recipient.ts:31
```

**Report length is O(distinct problems), not O(failing tests).**

Rules:
- Group by error signature / root cause before writing anything.
- The count goes on the finding, as a multiplier, never as a separate row.
- If grouping is uncertain, say so: `×40 tests (grouping unverified — 3 may be
  unrelated)`.

Checkable:
- [ ] No two findings in the report share the same root cause.
- [ ] Every clustered finding shows its count.
- [ ] Report length did not grow proportionally with the failure count.

*Why:* Currents' error-signature grouping (RESEARCH §10). Also RESEARCH §15:
Google went from a naive median of **820 mutants per changelist to 7** through
suppression and one-per-line, and surfaced a median of **2** — usefulness rose
from **15% to 89%**. Volume reduction *is* the quality intervention.

---

## 11. Severity is computed; priority is proposed

- **Severity (S1–S4)** is computed by the tool from observable facts (blast
  radius, data loss, reproducibility, whether a workaround exists). It is stated
  as a fact with its inputs shown.
- **Priority is a proposal and is labelled as such.** When to fix is a business
  call, not a tool's call.

```
S1 (computed: data loss, no workaround, reproduced 10/10)
Priority: proposed P1 — needs confirmation from @owner
```

Rules:
- Never print a priority without the word "proposed" (or "confirmed by \<name\>"
  once a human has signed off).
- Never collapse severity and likelihood into one magic number.

Checkable:
- [ ] Every severity shows the inputs that produced it.
- [ ] Every priority is marked proposed or attributed to a named human.
- [ ] No single blended "risk score" appears anywhere.

*Why:* RESEARCH §8 — Bach's objection: multiplying two invented ordinals yields
fake precision that people stop arguing with. Likelihood is computed from
`git log`; impact is supplied by humans; the two are never collapsed.

---

## 12. Rank by cost and business risk, never by count

Ordering within the top section is by **what it costs to leave it broken** —
customers affected, revenue path, data integrity, developer minutes burned.

Never rank by: number of failing tests, number of files touched, coverage
percentage, alphabetical order, or chronological order.

Flaky tests are ranked by **cost** too — PR disruption and CI minutes wasted —
not by flake count (RESEARCH §9).

Checkable:
- [ ] The ranking criterion is stated in the report, in words.
- [ ] A finding with a high test count does not outrank one with higher business
      cost.

---

## 13. No decoration

Banned outright:
- Progress bars that encode nothing a number doesn't say better.
- Confetti, celebration states, emoji as decoration (status icons are not
  decoration — they are the accessibility signal, §14).
- Any score without its definition next to it.
- **Coverage as a headline number.** Report *which high-risk code is untested*.
  Uncovered code is definitely untested (strong signal); covered code may still
  be unverified (weak signal).
- Gauges, speedometers, sparklines with no axis.

Checkable:
- [ ] No coverage percentage appears above the Findings section.
- [ ] Every score shown has a one-line definition adjacent to it.
- [ ] Every chart present would be missed if removed.

*Why:* RESEARCH §1 — Inozemtseva & Holmes, 31,000 suites: once suite size is
controlled for, coverage correlates only low-to-moderately with effectiveness.
Their conclusion: coverage "should not be used as a quality target."

---

## 14. Accessibility

**Colour is never the only signal.** Every status carries a **word or an icon**
in addition to its colour.

The report must read correctly:
- in **light** mode,
- in **dark** mode,
- in **grayscale** (print it, or screenshot it desaturated),
- on a **phone**, with 16px side gutters and **no horizontal page scroll**.

Rules:
- Status badge format: `icon + word` (`🔴 Critical`, `🟠 Attention`, `⚪ Info`).
- Tables degrade to stacked rows at narrow widths; they never scroll sideways.
- Minimum body text 14px; minimum contrast 4.5:1 against its own background.
- Nothing conveyed by hover alone.

Checkable:
- [ ] Desaturate the report — every status is still distinguishable.
- [ ] At 360px width there is no horizontal scrollbar.
- [ ] Every coloured element has a text or icon twin.

---

## 15. Effective false positives — the trust budget

**Definition (Google, RESEARCH §7): an issue is an effective false positive if
the developer took no positive action after seeing it.** A technically-correct
finding that nobody acts on **counts against you**. A technically-incorrect one
that someone fixes anyway does not.

Rules:
- **Every item carries a one-click "not useful / couldn't reproduce" control.**
  Without a feedback path there is no measurement, and without measurement the
  rule below cannot be enforced.
- EFP is tracked **per check** (per rule, per generator, per heuristic), over a
  rolling window, with a minimum of 10 observations before any judgement.
- **Any check above 10% EFP is automatically demoted out of the main report**
  into the appendix, and stays there until its rate recovers.
- The report states its own current EFP rate in the appendix. A report that will
  not report its own miss rate is asking for trust it has not earned.

Checkable:
- [ ] Every finding renders a feedback control.
- [ ] No check above 10% EFP appears in the main list.
- [ ] The current EFP rate per check is printed in the appendix.

*Why:* Google disables a code-review check that exceeds 10% EFP until it is
fixed. Security operations converge independently on <10–15% (RESEARCH §7).
Above that threshold, people stop reading — which is precisely the failure this
document exists to prevent.

---

## 16. Timing

The PR comment is the primary surface; this dashboard is secondary. Findings are
scoped to the diff. Pre-existing issues go to a **separate, non-blocking
backlog** — a wall of legacy findings on first install is the fastest route to
being ignored permanently.

*Why:* RESEARCH §6 — Infer as a batch bug list achieved a **~0% fix rate**; the
same analyzer, same bugs, delivered as comments on the diff under review
achieved **~70%**.

Checkable:
- [ ] Findings in the main list are attributable to the diff under review.
- [ ] Pre-existing findings are in a separate, clearly non-blocking section.

---

## 17. Flaky tests

Flakes have a **status lifecycle**: healthy → flaky → quarantined → broken. The
headline numbers are computed **only from trusted (healthy) tests**, and the
report says how many tests were excluded.

- Classify only after **≥10 observations**.
- Quarantine at a **30% failure rate over a rolling 7-day window**;
  auto-unquarantine after **30 days clean**.
- **Quarantine never means delete.** Quarantined tests keep running and keep
  collecting data — Google found that when a stable test turned flaky and the
  cause was traceable, it was a **real production bug about 1 time in 6 (~17%)**.
- Never score one infrastructure outage as N flaky tests. A failure *column*
  across many tests is an infra problem; scattered dots are per-test flakiness.
- **A PASS is strong evidence; a FAIL is weak evidence.** Word the report
  accordingly: a single failure is a lead, not a conclusion.

Checkable:
- [ ] Quarantined tests are listed with their flake rate, window and a date.
- [ ] Headline numbers state how many tests were excluded as untrusted.
- [ ] No flaky test is reported as a product bug without a reproduction.

*Why:* RESEARCH §17 and §9. At 1,000 generated tests with a 0.1% flake rate each,
**~63% of PRs hit at least one flaky failure**. Visibility alone took Spotify
from 6% to 4% flakiness in two months — the cheapest win in the literature.

---

## Report review checklist

Run this against the draft **before publishing**. Any `no` blocks publication.

1. Does the report open with exactly one verdict sentence of ≤25 words that
   names a state and one next action? ☐ yes ☐ no
2. Is the top section ≤10 items, containing only new / reproduced / untriaged
   findings? ☐ yes ☐ no
3. If anything was truncated, is the honest "N of M shown, ranked by business
   risk" line present with the true M? ☐ yes ☐ no
4. Is every headline a symptom, with no stack frame, file path, exception class
   or test function name in it? ☐ yes ☐ no
5. Are emphasised rows ≤30% of all rows, with red used only for
   reproduced-and-critical? ☐ yes ☐ no
6. Does every number in sections 1–5 carry a literal, copy-pasteable command
   that reproduces it? ☐ yes ☐ no
7. Does every finding carry exactly one evidence badge, with 📖/💭 confined to
   the collapsed appendix *and* excluded from the bug total, each with its
   disqualifying reason? ☐ yes ☐ no
8. Are bugs (product) and issues (testing) in two separate lists with separate
   counts, with every failure classified product / test / infrastructure?
   ☐ yes ☐ no
9. Is every cluster collapsed to one finding with a count, so that length tracks
   distinct problems rather than failing tests? ☐ yes ☐ no
10. Is every severity shown with its computed inputs, and every priority marked
    "proposed" or attributed to a named human? ☐ yes ☐ no
11. Is the ranking by business cost, stated in words, rather than by count?
    ☐ yes ☐ no
12. Does the report survive grayscale, dark mode and a 360px-wide screen with
    every status still readable and no horizontal scroll? ☐ yes ☐ no
13. Does every item carry a one-click "not useful / couldn't reproduce" control,
    and is no check above 10% EFP present in the main list? ☐ yes ☐ no

A `no` on any line is a defect in the report, not a stylistic preference.
