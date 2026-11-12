---
name: preferences
description: >
  Show and change everything testsquad remembers about this repo in one place: depth,
  install permission, service access, thresholds and promoted lessons — each with when
  it was given and when it expires. Use it to audit what the tool thinks it is allowed
  to do, or to withdraw something. It never widens a permission silently and never
  treats an old yes as a current one.
---

# /testsquad:preferences

Memory that cannot be inspected is not a feature, it is a liability. This is the one
screen where everything remembered is visible and revocable.

## What is shown

Read `.testing/preferences.json`, `.testing/decisions.md` and `.testing/conventions.md`
and render every remembered item with four columns — **what, value, when it was given,
when it expires**:

| Item | Value | Given | Expires |
|---|---|---|---|
| Depth | standard | 2026-10-04 | never |
| Install dependencies | yes — npm, lockfile may change | 2026-10-04 | **2026-11-03 (expired)** |
| Run tests against `staging-db` | **no** | 2026-10-11 | never (standing decline) |
| Coverage threshold, billing | 90% branch | 2026-10-18 | per contract |
| Lesson: fixtures live in `test/factories/` | promoted | 2026-10-22 | re-check 2027-01-22 |

An expired item is shown as expired, in place, not quietly dropped and not quietly
honoured.

## Approvals are scoped and they expire

Every permission records **what it covers**, not just that it was given. "Yes, install
dependencies" granted for an `npm ci` in one package is not permission to add a global
binary, touch a different package manager, or reach the network from a test.

**Expiry is 30 days.** The reasoning is simple: a yes is a statement about a situation,
and situations move. The person who agreed may not be the person running it six weeks
later, and the repository they agreed about may not be this one anymore.

**Revalidate early — before the 30 days — when any of these change:**

- the lockfile (`package-lock.json`, `poetry.lock`, `go.sum`, `Gemfile.lock`, …);
- the CI configuration;
- the hosts or service names in `.env` / `.env.example` / the compose file.

Those are exactly the changes that alter what the old permission would now authorize. A
30-day window with no early revalidation is a permission that follows the calendar
instead of the risk.

## A no is remembered as firmly as a yes

A standing decline is a first-class record, with the same durability as an approval.
It is not re-asked every run, it is not worked around with a narrower phrasing of the
same request, and it is honoured by every command — `/testsquad:advise` will not
recommend a tool that was declined, `/testsquad:scout` will not re-propose it without
new evidence, and if a decline is costing something measurable, that is stated once, as
information, not as a renegotiation.

Withdrawing a yes takes effect immediately and needs no justification.

## What is never remembered

Some things re-ask every single time regardless of history, and no preference can
switch that off:

- anything destructive — deleting data, dropping a schema, force-pushing, rewriting
  history;
- anything prod-shaped — a connection string, hostname or credential that resolves to a
  production or production-like system;
- any side effect outside the worktree: push, merge, publish, deploy, posting to an
  external service.

A remembered yes for one of these would be a standing authorization to do damage on a
future day nobody is watching. The cost of asking again is a few seconds; the cost of
not asking is unbounded and lands on the user.

Secrets are never stored here in any form — not the value, not a truncated preview, not
a hash that could be checked against a guess. `.testing/env-safety.json` records that a
key was found at a location, and nothing more.

## Changing something

Name the item and the new value. Write `.testing/preferences.json` atomically, with the
date and a fresh expiry. Where the change affects a standard rather than a convenience —
a threshold, a tier, a gate — it is also appended to `.testing/decisions.md` with the
old value, the new value and the reason, so standards cannot erode quietly through this
screen either.

## Output

The table, the expired items called out first because those are the ones needing a
decision today, and one line on what to do about each. Nothing executes here; this
command reads and writes preferences and does nothing else.
