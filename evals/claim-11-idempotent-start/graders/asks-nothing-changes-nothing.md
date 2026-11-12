---
type: llm
---

The repository is unchanged since it was onboarded.

PASS only if the response reports that everything is already in place and
that there was nothing to do (or that it refreshed only what had changed),
WITHOUT asking the user any questions and WITHOUT re-running the full
onboarding interview.

FAIL if the response asks the user a question, re-asks onboarding
questions already answered in `.testing/`, overwrites or resets the
existing knowledge in `.testing/`, or reports having re-created artefacts
that already existed.
