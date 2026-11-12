---
name: False positive
about: A check fired on code that is actually correct. This is the most valuable report this project can receive.
title: "[FP] <check name> fired on <what>"
labels: ["false-positive"]
---

<!--
Read this part. It changes what you should write below.

testsquad's whole promise is that it never reports something it could not
prove. A check that fires on correct code breaks that promise directly, and it
does more damage than a check that stays quiet.

The project tracks an **effective false positive** rate, and the threshold is
10% -- the number at which Google auto-disables a static analysis check
(Sadowski et al., CACM 2018; see docs/RESEARCH.md). "Effective" is the strict
reading: a finding the developer took no action on counts as a false positive
*even if it was technically correct*. Being right and being ignored are the
same outcome.

So: **this is a defect in the check, not a mistake by you.** You will not be
asked to configure anything, suppress anything, or restructure your code to
make the tool happy. If the check is wrong, the check changes or the check is
demoted. If the check turns out to be right, the finding's wording failed to
make its case, and that is also our bug.

You do not need to fill in every field. The one that matters most is "Why the
code is actually correct".
-->

## Which check fired

<!-- e.g. flake_smells `no-assertion`, arid_nodes, env_safety_check
`unreadable-sensitive-file`, risk_map ranking, or an agent/skill by name. -->

## The exact finding text

<!-- Paste it verbatim, including the rule name, the file:line and the message.
Do not paraphrase -- the wording is the thing under review. -->

```
```

## The code it fired on

<!-- The smallest excerpt that still shows why the finding is wrong. Strip
anything you cannot share; a redacted excerpt is fine, a secret is not. -->

```
```

## Why the code is actually correct

<!-- THIS IS THE FIELD THAT MATTERS.

Explain what the check missed. Usually it is one of:

  * the assertion is real but indirect -- a helper, a context manager, a
    `with self.assertRaises(...)`, a custom `assert_*` method;
  * the behaviour the check calls nondeterministic is pinned somewhere the
    check did not look -- a fixed seed, a frozen clock, a local fake;
  * the pattern is deliberate and the alternative would be worse;
  * the finding is true but not worth acting on, which is still a false
    positive under the effective-FP definition. Say so plainly if that is the
    case -- "correct, but I would never change this" is exactly the signal the
    10% threshold is measuring.

Write it in your own terms. You do not need to know how the check works. -->

## How to reproduce it

<!-- The exact command, run from the repository root, that produces the
finding. If it fires on a public repository, a link plus the command is ideal,
because it lets us add it as a fixture and keep it from regressing. -->

```
python3 scripts/flake_smells.py <path> --format text
```

## Version

| Field | Value |
|---|---|
| testsquad version / commit | <!-- `.claude-plugin/plugin.json` version, or the commit SHA --> |
| Python | <!-- `python3 --version` --> |
| OS | |

## What happens to this report

A confirmed false positive is fixed in one of three ways, in order of
preference:

1. **The check learns the pattern.** Preferred, and the reason the repro
   command above matters -- it becomes a regression test.
2. **The check's severity drops.** A rule that is right less often than it is
   wrong does not get to `block`.
3. **The check is removed.** A check above roughly 10% effective false
   positives is ignored wholesale by the developers it fires on, which makes
   it worse than nothing: it trains people to skim past every finding,
   including the true ones.

When in doubt, the project's rule is to stay silent. A missed bug costs one
bug. A false positive costs the credibility of every finding after it.
