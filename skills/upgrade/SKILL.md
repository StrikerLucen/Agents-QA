---
name: upgrade
description: >
  Regenerate this repo's `ts-*` agents at the current plugin version and migrate the
  `.testing/` folder if its schema moved. Use it after updating the testsquad plugin, or
  when an agent file is stale, corrupted or hand-edited. It never discards learned
  knowledge and never upgrades without showing what will change.
---

# /testsquad:upgrade

Regenerating agents is safe here, and that safety was designed in rather than hoped for.

## Why the agents are disposable

**Every generated agent file is fully regenerable, because nothing hand-written or
learned ever goes inside one.** All knowledge lives in `.testing/`:

| Lives in a generated agent | Lives in `.testing/` |
|---|---|
| The role, its rules, its reasoning | The profile: runner, commands, fixtures, services |
| The stack-specific playbook, filled from the profile | `conventions.md` — the rules the agent enforces |
| The plugin version stamp | `lessons/`, `decisions.md`, `known-issues.md` |
| | `preferences.json`, `acceptance-contract.md`, baselines |

That separation is what makes this command a one-liner rather than a migration project.
An agent that accumulated learned content would have to be merged, and merging generated
prose is how tools start losing people's work.

## What you do

**1. Check the versions.** The plugin's current version against the stamp in each
`.claude/agents/ts-*.md`, and `.testing/schema-version` against the schema this version
expects. If both match and nothing was hand-edited, say so and stop — an upgrade that
changes nothing should cost one line of output, not a ceremony.

**2. Detect hand edits.** If a `ts-*.md` differs from what this plugin version would
generate from the current profile, that is a modification someone made deliberately.
**Show it as a diff and ask before overwriting.** Offer to preserve it as
`.claude/agents/ts-<name>.local.md`, which this plugin does not generate and will never
touch.

**3. Re-read the profile.** Agents are generated *from* `.testing/profile.json`, so a
stale profile produces a correct-looking agent pointed at a runner the repo stopped
using. If the profile predates the current lockfile or CI config, say so and offer
`/testsquad:start` to re-profile first. Do not silently regenerate against stale facts.

**4. Regenerate**, from `${CLAUDE_PLUGIN_ROOT}/templates/agents/`, still **generating
only what fits the stack**. A repo that dropped its UI should lose the browser agent on
upgrade, not keep it out of politeness.

**5. Migrate `.testing/` if `schema-version` moved.** Back up the folder first, migrate
forward one version at a time, write the new `schema-version` last so an interrupted
migration is detectable rather than ambiguous. **No migration ever deletes knowledge** —
a field that no longer has a home is preserved under `legacy` and reported, never
dropped. If a downgrade is attempted (schema newer than this plugin), stop and say so;
guessing backwards is how a lessons file gets truncated.

## Show what changed

The whole output is a change report, and silence is not an acceptable upgrade:

```
testsquad 0.1.0 → 0.2.0

Agents
  ts-unit-test-engineer     regenerated — mutation-kill check added to its QA hand-off
  ts-automation-engineer    regenerated — no behavioural change
  ts-debugger               regenerated — flake matrix now distinguishes environment
  ts-browser-engineer       removed — no UI in the profile since 2026-10-30
  ts-qa-gatekeeper          unchanged

.testing/
  schema-version            3 → 4 (lessons gained a re-check date; 11 migrated,
                            all defaulted to +90 days)

Preserved untouched
  conventions.md, decisions.md, lessons/, preferences.json,
  acceptance-contract.md, risk-map.json, health-baseline.json
```

If an agent's behaviour changed in a way that affects what it will do to this
repository, say what and why in one sentence. "Regenerated" with no explanation is how
someone discovers next week that their gate got stricter.

## Rules

Nothing is installed. Nothing executes beyond reading files and writing agents — this
command does not run a suite, so the environment safety check gates only what it would
otherwise execute, and a stale or missing health baseline is reported rather than
refreshed behind the user's back.

Writes are atomic, work lands on a branch, and nothing is pushed. If the upgrade looks
wrong afterwards, `/testsquad:uninstall` removes exactly what was added and
`/testsquad:start` puts it back.
