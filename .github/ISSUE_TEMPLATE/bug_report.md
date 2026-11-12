---
name: Bug report
about: Something in testsquad does the wrong thing
title: "<what goes wrong, in your terms>"
labels: ["bug"]
---

<!--
This form mirrors the bug report standard testsquad holds its own agents to
(templates/knowledge/bug-report.md). The headline should be the consequence,
not the taxonomy: "the fixture regen fails on a repo with no git" beats
"IndexError in regen_git_history.sh".

One thing up front, because it changes what you should write:

  **A wrong repro delays a fix more than a missing one.**

It sends someone to look in the wrong place, with confidence, and they come
back with "works for me". So "I'm not sure how to reproduce it" is a complete,
useful and welcome answer. Say that instead of guessing, mark Reproducibility
below as unknown, and tell us what you saw. We would rather chase a vague
symptom we know is vague than a precise story that is wrong.

(The reason this form asks for a starting state and a reproducibility count at
all: Bettenburg et al., TSE 2010 -- across 466 developers, steps to reproduce,
stack traces and test cases are the three most valued items in a bug report,
and the three reporters supply least. docs/RESEARCH.md has the citation.)
-->

## What goes wrong

<!-- One or two sentences, in terms of the consequence to you. -->

## Environment

| Field | Value |
|---|---|
| testsquad version / commit | <!-- `.claude-plugin/plugin.json` version, or the SHA --> |
| OS | <!-- e.g. macOS 15.2, ubuntu-24.04 --> |
| Python | <!-- `python3 --version` -- 3.9 is the supported floor --> |
| Claude Code version | <!-- if the problem involves an agent or skill --> |
| Command | <!-- the exact command you ran --> |

## Steps to reproduce

**Starting state:** <!-- Say what it is, or the steps are unrepeatable. e.g.
"clean clone at <SHA>, `cd examples/fixtures && ./regen_git_history.sh` run,
no `.testing/` present, no CLAUDE_PLUGIN_DATA set." -->

1.
2.
3.

**Expected:**
**Actual:** <!-- and `file:line` if you have it -->

```
paste the real output here, trimmed.
Never a paraphrase. Never a secret -- redact before pasting.
```

<!-- If you cannot reproduce it, delete the numbered steps and write what you
were doing when it happened. That is a legitimate report. -->

## Reproducibility

<!-- N of M runs. "5 of 5" and "1 of 10" lead to completely different
investigations, and "unknown, saw it once" is a valid answer. -->

- [ ] Every time (N of N)
- [ ] Intermittent — ___ of ___ runs
- [ ] Saw it once, could not reproduce

## Severity

<!-- Impact x scope. Pick one; we may re-grade it, and priority is set by a
human, never computed from this. -->

- [ ] **S1** — data loss, a secret exposed, or money lost; no workaround
- [ ] **S2** — a core flow is broken; a workaround exists
- [ ] **S3** — degraded or wrong in a contained way
- [ ] **S4** — cosmetic, or a documentation error

## Evidence

- [ ] ✅ reproduced — I ran it and saw it
- [ ] 👁 observed — I saw it happen but have not re-run it
- [ ] 📖 code-read — I believe it from reading the source
- [ ] 💭 suspicion — something feels wrong

<!-- 📖 and 💭 reports are welcome and will be read. They are just filed as
issues rather than counted as bugs, exactly as the agents' own reports are. -->

## Anything else

<!-- Related findings, a guess at the cause, what you tried. Guesses are
welcome as long as they are labelled as guesses. -->
