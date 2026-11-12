# Lesson: {{ONE-SENTENCE CLAIM}}

One lesson per file. Filename: `.testing/lessons/{{YYYY-MM-DD}}-{{slug}}.md`.

| Field | Value |
|---|---|
| **Date** | {{YYYY-MM-DD}} |
| **Claim** | Integration tests need `DATABASE_URL` set or they error at import |
| **Confidence** | observed once / seen repeatedly / confirmed by QA |
| **Re-check** | {{YYYY-MM-DD}} — when this stops being assumed true |
| **Author** | {{ts-unit-test-engineer}} |

## Evidence

The command and its **real output**. Not a description of what happened.

```
$ npx vitest run test/integration/orders.test.ts
Error: connect ECONNREFUSED 127.0.0.1:5432
  at test/integration/setup.ts:12
```

## What changes if this is promoted

{{Which agent behaves differently, and how. "ts-automation-engineer will skip
integration tests and say why, instead of reporting 14 failures."}}

---

**A lesson with no command under it does not get written.** It stays an
impression until someone can produce the command.

That rule is not pedantry. A convention is read by every agent on every run and
is never questioned again — that is the point of it, and it is also why **one
wrong lesson poisons every future task in this repo**. An agent told "fixtures
live in `test/factories/`" will create them there forever, including after the
repo moved them, and nobody notices, because the output stays confident and
consistent.

Promotion to `conventions.md` happens in `/testsquad:learn`, needs the user's
approval, and never happens on the strength of a single observation.
