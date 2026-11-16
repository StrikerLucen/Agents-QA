# BUG-{{ID}} — {{SYMPTOM, in the user's terms}}

Headline is the consequence, not the taxonomy: *"Checkout fails for EU customers
with a discount on a taxed line"*, not *"assertion failure in tax module"*.

| | |
|---|---|
| **Severity** | S2 — computed from impact × scope ({{why}}) |
| **Priority** | **proposed** P2 — a human decides priority, never the agent |
| **Evidence** | ✅ reproduced |
| **Reproducibility** | 5 of 5 runs |
| **Location** | `src/billing/tax.ts:148` |
| **Reported** | {{DATE}} by {{AGENT}} |

## Environment

| Field | Value |
|---|---|
| Commit | `{{SHA}}` |
| OS | {{macOS 15.2 / ubuntu-24.04}} |
| Runtime | {{node 22.11.0}} |
| Test command | `{{CMD}}` |

## Steps to reproduce

From a **known starting state** — say what it is, or the steps are unrepeatable.

Starting state: clean checkout at `{{SHA}}`, `npm ci`, no `.env` overrides.

1. {{STEP}}
2. {{STEP}}
3. {{STEP}}

**Expected:** {{what should happen}}
**Actual:** {{what happens}} — `{{file:line}}`

```
{{the real output, trimmed. Never a paraphrase, never a secret.}}
```

## Scales

- **Severity** S1 data loss, a secret exposed or money wrong, no workaround · S2 core
  flow broken, workaround exists · S3 degraded but contained · S4 cosmetic.
- **Evidence** ✅ reproduced · 👁 observed · 📖 code-read · 💭 suspicion.
  Only ✅ and 👁 reach the main report list; the rest go to the appendix and are
  never counted in the bug total.

## Why this form

Across 466 developers (Bettenburg et al., TSE 2010), the three most valued items
in a bug report are **steps to reproduce, stack traces and test cases** — and
they are exactly what reporters supply least. The same study is the reason the
repro above is pinned to a starting state and a reproducibility count: **a wrong
repro delays a fix more than a missing one**, because it sends someone to look in
the wrong place with confidence. If you could not reproduce it, say ❌ and file it
as an *issue*, not a bug.
