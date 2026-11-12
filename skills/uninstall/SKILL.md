---
name: uninstall
description: >
  Remove exactly what testsquad added to this repository — the `CLAUDE.md` marker block,
  the generated `ts-*` agents, and optionally `.testing/` — showing the full diff before
  touching anything. Use it to undo an install, or to check what would be removed before
  committing to one. It never deletes hand-written content and never removes anything it
  did not create.
---

# /testsquad:uninstall

**A tool that writes into real repositories needs a reversible first step, or nobody
risks the first run.** This command is that step, and it is not an afterthought: it is
the reason `/testsquad:start` is allowed to write anything at all.

Everything about the install was designed so this command can be exact. The `CLAUDE.md`
block is fenced by markers. The agent files carry a version stamp and are generated,
never hand-authored. Nothing testsquad writes is interleaved with anything it did not
write. So removal is a subtraction, not a guess.

## What is removed

| Thing | Rule |
|---|---|
| `CLAUDE.md` marker block | Only the lines between `<!-- testsquad:start -->` and `<!-- testsquad:end -->`. **Everything else stays byte-for-byte**, including whitespace |
| `.claude/agents/ts-*.md` | Only files matching the `ts-` prefix **and** carrying a testsquad version stamp |
| `.gitignore` entry | Only the `.testing/reports/` line, only if testsquad added it |
| `.testing/` | **Only if asked.** Defaults to kept |

## What is never touched

- Any agent not matching `ts-*`, and any `ts-*.local.md` — the local suffix exists
  precisely so someone can hand-edit an agent and keep it through install, upgrade and
  removal.
- Any `ts-*.md` that has been hand-edited since generation. Show the diff, say it is
  modified, and ask. Deleting someone's edits because they used your filename prefix is
  the bug that ends a tool's reputation.
- Everything in `CLAUDE.md` outside the markers. If the markers are missing or damaged —
  someone edited across them — **stop**, show the file region, and ask the user to
  confirm the boundary rather than pattern-matching your way through their instructions.
- Test files, fixtures, CI configuration, branches. The tests written during the squad's
  work are the user's code now, and they stay.

## `.testing/` is kept by default

Ask explicitly, with the consequence stated:

> Keep `.testing/`? It holds the risk map, the acceptance contract, the health baseline,
> promoted conventions and the lessons — the only things here that were learned rather
> than generated. Reinstalling with it present restores everything; without it, the
> onboarding starts from zero.

Default to keeping. It is a handful of small committed text files, and the asymmetry is
stark: keeping it costs almost nothing, deleting it discards every measurement and
agreement made since the install. If the user does want it gone, say what is going and
offer to write it to a single archive file first.

## The diff comes first

Before any write, show:

- the exact `CLAUDE.md` lines to be removed, in context;
- each agent file to be deleted, with its version stamp, and whether it is unmodified;
- anything modified, flagged, with its diff;
- what is being kept and why.

Then ask once. Then act. Removal is atomic per file, and a file that cannot be removed
cleanly is reported rather than half-removed.

## Output

A short, literal list of what was removed and what was kept, followed by the one-line
check the user can run themselves:

```bash
git diff --stat
```

Nothing is committed, nothing is pushed. The user reviews the removal the same way they
would review any other change — which is the point. A tool that can be audited out of a
repository in one diff is one people will try in the first place.
