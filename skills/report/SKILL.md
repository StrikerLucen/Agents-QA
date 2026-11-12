---
name: report
description: >
  Render the dashboard from the data already in `.testing/`, or with `--pr` the compact
  PR comment. Measures nothing itself — it assembles what other commands measured. Use
  it after diagnosing, verifying or a work loop, and in CI on every push. It never
  states a number nobody measured, never prints a secret, and never posts a second
  comment where it can edit the first.
---

# /testsquad:report [--pr]

Two surfaces, one source of truth. **The PR comment is primary; the dashboard is
secondary.** That ordering is not a preference — Facebook's Infer delivered as a batch
bug list achieved a **~0% fix rate**, and the same analyzer finding the same bugs,
delivered as comments on the diff under review, achieved **~70%**. Nothing about the
findings changed. Timing was the whole difference.

Google's bug-prediction study is the other half of the lesson: the ranking was correct
and developer behaviour changed **not at all**, because it lived in a dashboard. A
finding must be actionable, its reasoning obvious, and it must arrive in the workflow.

## Binding

`${CLAUDE_PLUGIN_ROOT}/templates/report/STYLE.md` is binding, not advisory. Its 13-point
review checklist runs against the draft **before publishing**, and a `no` on any line
blocks publication. The `reporter` agent reads it and complies; you check the result
before it ships. The lines most often broken: one verdict sentence of ≤25 words at the
very top, symptom headlines with no file paths or exception names, ≤10 items in the top
section, emphasis on no more than `max(2, floor(0.30 × rows))` of the rows, a literal
command under every number, and bugs (product) separated from issues (testing) in two
lists with two counts.

## `--pr` — the PR comment, which is deterministic

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/pr_comment.py" \
  --testing-dir .testing --out .testing/reports/pr-comment.md
```

**No LLM runs in this path, and the `reporter` agent is not dispatched on it.** The
comment is rendered by that script, standard library only, from files on disk — so the same inputs always produce the same comment. A comment
whose contents depend on a model call is not reproducible, cannot be diffed between
pushes, and cannot be trusted as a gate. It is also the only version that is safe on
forks: a pull request from a fork must never be able to make a model read
attacker-controlled text with repository credentials in scope.

It contains exactly four things:

| Section | File it reads, and what writes it | Shape |
|---|---|---|
| Contract gates | `.testing/gates.json` — `/testsquad:verify` | One row per gate: name, target, actual, met |
| Coverage delta on **changed lines only** | `.testing/coverage-diff.json` — `/testsquad:diagnose` | `Lines 83.0% / base 71.0% / +12.0 pts` |
| New **REPRODUCED** findings | `.testing/findings.json` — `/testsquad:test` or `:debug` | Count plus severity breakdown. ✅ and 👁 only |
| Health verdict | `.testing/health-baseline.json` — `/testsquad:health` | GREEN / AMBER / RED, with its commit |

Those four filenames are the script's, not a convention you may restyle: it opens exactly
them under `--testing-dir`. A file it cannot find is named in the comment as missing,
with the command that produces it — so an incomplete run says so instead of looking
clean. If you find yourself about to explain a blank section, write the missing file.

And nothing else. Specifically **never**:

- a suspicion, a code-read-only item, or anything not reproduced — those live in the
  collapsed appendix of the dashboard, with the reason they did not qualify, and are
  never counted in the bug total;
- a secret, an environment value, a connection string or a redacted-but-recoverable
  preview of one — a report that leaks a key into a PR comment publishes it;
- whole-project coverage as a headline;
- a wall of text. If it does not fit on one screen it will not be read, and the
  dashboard link is right there.

**Pre-existing findings never block.** They are counted, labelled pre-existing, and
non-blocking. Nobody is made responsible for a defect they did not introduce; doing that
once is how the gate gets disabled for everyone.

### One comment, edited in place

Find the existing testsquad comment by its marker and **update it**. Do not post a new
one per push.

```bash
gh pr comment <n> --edit-last --body-file .testing/reports/pr-comment.md
```

A thread of eleven bot comments is read zero times. One comment that is always current
is read every time, and its edit history is the trend.

If no PR context exists, write the comment to `.testing/reports/pr-comment.md`, say
where it is, and stop. Never push, never open a PR, never comment on something the user
did not ask you to comment on.

## Default — the dashboard

**Dispatch the `reporter` agent.** Assembling the dashboard is its whole job: it is bound
to `STYLE.md`, it separates reproduced bugs from unreproduced issues, it clusters by root
cause, and it tracks which checks produce findings nobody acts on. Hand it the run's
findings and the paths under `.testing/` they came from. Keep for yourself anything that
needs the user — it cannot prompt, and it has no `Bash`, so it measures nothing and
publishes nothing.

It renders `.testing/reports/dashboard-<date>.html` from
`${CLAUDE_PLUGIN_ROOT}/templates/report/dashboard.html`, in the fixed section order:
verdict, what changed since the last report, top actions, the capped findings queue,
coverage and health, exploratory charters, collapsed appendix.

It must read correctly in light mode, dark mode, grayscale and at 360px with no
horizontal scroll — colour is never the only signal, so every status carries an icon and
a word.

`.testing/reports/` is gitignored. The knowledge in `.testing/` is worth committing; the
generated HTML is not.

## The rule underneath all of it

**You measure nothing.** Every figure comes from a file another command wrote, carrying
the commit and the health verdict it was measured under. If a number is missing, the
report says it is missing and which command would produce it. An estimate inserted to
complete a table is the single fastest way to make every other number in the report
worthless.

And track your own miss rate: every item carries a one-click "not useful / couldn't
reproduce" control writing to `.testing/feedback.json`, any check above **10% effective
false positives** over a rolling window of at least 10 observations is demoted out of
the main list until it recovers, and the current rate per check is printed in the
appendix. A report that will not report its own miss rate is asking for trust it has not
earned.
