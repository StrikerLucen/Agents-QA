# Report rules — the enforceable core

The short version, for any agent producing output. The reasoning, the evidence and the
before/after examples are in `STYLE.md`; read that only when you need to justify a rule
or are writing the reporter itself. This file is what you follow.

## Structure, in this order
1. **Verdict** — one sentence: is anything on fire, and what first.
2. **What changed** since the last report.
3. **Top actions** — ranked, with effort.
4. **Findings** — bugs and issues in *separate* tables, each with an evidence column.
5. **Coverage & health** — never above the findings.
6. **Exploratory charters** — 2–3, highest-risk areas.
7. **Appendix** — collapsed: known issues, unconfirmed items, raw data.

## Hard rules
- **Main list caps at 10.** Print `N of M shown, ranked by business risk`.
- **Every finding carries exactly one evidence badge.** ✅ **reproduced** — you ran it and
  saw it fail, N of M times, on a stated commit. 👁 **observed** — present in a log, trace
  or CI artifact you read, but not re-run by you. 📖 **code-read** — the path implies it;
  you never saw it happen. 💭 **suspicion** — pattern-matching, and not a finding.
- **Only ✅ reproduced and 👁 observed appear in the main list**, and the bug total is
  `count(✅) + count(👁)`. Reproduction is not required of every finding, at any depth:
  depth buys more attempts at turning 👁 into ✅, never a higher bar. 📖 code-read and
  💭 suspicion go to the collapsed appendix with the reason they did not qualify, and are
  **never counted in the bug total**.
- **Bugs and issues are different lists.** A bug is a problem in the product; an issue is
  a problem with the testing — broken fixture, missing mock, flaky test, dead environment.
  The badge is a separate axis: a 👁 observed product defect is a bug, not an issue.
- **Cluster by root cause.** 40 failures from one null guard is one finding with a count.
- **Symptom, not cause.** *"Checkout fails for EU customers with a discount on a taxed
  line"* — not *"assertion failure in tax module"*. The cause goes inside the item.
- **Emphasis is rationed.** Red only for reproduced-and-critical; amber for
  needs-attention; grey otherwise. Bold marks one action, never a sentence.
  **Check: emphasised rows ≤ max(2, 30% of rows), or the report fails review.**
- **Every number links to its proof** — the exact command, copy-pasteable.
- **Never a number you did not measure.** "not measured" is a valid answer; 0% is not.
- **Severity (S1–S4) is computed; priority is proposed and needs a human.**
  S1 data loss, a secret exposed or money wrong, no workaround · S2 core flow broken,
  workaround exists · S3 degraded but contained · S4 cosmetic.
- **Colour is never the only signal** — every status carries a word or icon.
- **No secrets, ever.** Key names and redacted previews only.
- **Coverage is never a headline score.** Report which high-risk code is untested.

## First run on a repo
Everything is new, so novelty cannot rank. Show findings on **critical-risk code only**,
capped at 10; everything else goes to a counted, non-blocking, explicitly labelled
pre-existing backlog. Say so: *"First scan. 7 shown from highest-risk code; 143
pre-existing in the backlog."* **A pre-existing finding never blocks a PR.**

## Before publishing — all must be yes
1. Does the first sentence say whether to worry and what to do first?
2. Is the main list ≤10, with the remainder counted?
3. Is every main-list item reproduced or observed?
4. Does every number have a runnable command beside it?
5. Are bugs and issues in separate lists?
6. Are findings clustered by cause rather than listed per symptom?
7. Is every headline phrased as a consequence, not a taxonomy?
8. Do emphasised rows pass the ≤ max(2, 30%) check?
9. Does every status carry a word or icon as well as colour?
10. Are there zero secrets and zero unconfirmed items outside the appendix?
