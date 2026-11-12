---
name: learn
description: >
  Review what the squad has learned about this repo: promote approved lessons into
  `conventions.md`, re-check expired ones, and retire disproved ones with a reason. Use
  it after a run that discovered something durable, or on a schedule to keep the
  knowledge honest. It never promotes a lesson without the user's approval and never
  records a claim without the evidence that produced it.
---

# /testsquad:learn

The agents are regenerable; the knowledge is not. Everything the squad has learned about
this repository lives in `.testing/`, which is why `/testsquad:upgrade` can safely
rewrite every agent file. This command is how that knowledge stays worth keeping.

## Why the ceremony

**One wrong lesson poisons every future task in this repository.** A convention is read
by every agent on every run and is never questioned again — that is the point of it, and
it is also why a mistaken one is so expensive. An agent told "fixtures live in
`test/factories/`" will create them there forever, including after the repo moved them,
and nobody will notice because the output looks confident and consistent.

So: evidence is mandatory, promotion needs human approval, and retirement is explicit.
Everything else about this command follows from those three.

## The shape of a lesson

A pending lesson in `.testing/lessons/` carries five fields, and is rejected without
them:

| Field | Rule |
|---|---|
| **Date** | When it was observed |
| **Claim** | One sentence, specific and falsifiable. "Integration tests need `DATABASE_URL` set or they error at import" — not "the setup is fragile" |
| **Evidence** | **The command and its real output.** Not a description of what happened |
| **Confidence** | observed once / seen repeatedly / confirmed by QA |
| **Re-check date** | When this stops being assumed true |

A claim with no command under it is not a lesson, it is an impression, and it stays
pending until someone can produce the command.

## The promotion path

```
observed once        → stays pending, re-checked on the next run
seen 2+ times AND QA-confirmed → proposed for promotion
proposed             → the user approves → written to conventions.md
```

**Promotion requires the user's approval. Always.** Present proposals in one batch, each
with its claim, its evidence and what will change as a result:

> **Propose:** every new test file imports the shared `renderWithProviders` helper.
> Seen 4 times; QA-confirmed on tasks 11, 14, 17.
> `rg -l 'renderWithProviders' src/**/*.test.tsx | wc -l` → 38 of 41 test files.
> **Effect:** `ts-unit-test-engineer` will use it by default and flag files that don't.

An approved lesson is appended to `.testing/conventions.md` as a numbered rule — the
rules file the agents *enforce*, kept separate from the agents themselves precisely so
it can change without regenerating anything.

## Re-checking and retiring

Every promoted convention has a re-check date. On this command, re-run the evidence
command for anything due:

| Outcome | Action |
|---|---|
| Still holds | New re-check date, same rule, no fuss |
| Still holds but narrower | Amend the claim to what the evidence now supports, and say what changed |
| **Disproved** | Retire it |

Retirement means moving the file to `.testing/lessons/retired/` with **the reason, the
date, and the command that disproved it**, and removing the rule from
`conventions.md`. It is never a silent delete. The retired file is the thing that stops
the same wrong lesson being re-learned next quarter from the same misleading evidence,
and it is also how someone reading a strange old test file can find out what the squad
used to believe when it was written.

A convention that cannot be re-checked by a command is a convention that cannot be
retired, which is why the evidence field is not optional.

## Output

Report four counts and nothing inflated: promoted, re-checked and still holding,
amended, retired. For each promotion, the claim and the evidence. For each retirement,
the claim and what disproved it.

Lead with the verdict line, in the house style — *"3 lessons promoted; the fixtures rule
was wrong since the October refactor and is retired"* — because the retirement is almost
always the more important half.

All writes are atomic, one file per lesson, so parallel agents cannot clobber each
other. Nothing here executes anything beyond the recorded evidence commands, and those
run under the same environment safety check as everything else.
