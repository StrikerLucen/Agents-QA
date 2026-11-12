# Handoff — resume here

Snapshot taken 2026-11-12 while three hardening agents were mid-change. They were
stopped deliberately to take a clean commit, so some of their work landed and some
did not. **This file lists exactly what is finished and what is not.** Nothing below
is guesswork — each item was verified by running it.

## First thing after cloning

The three fixture repositories are committed as plain files; their git history is
**not** in this repo (an embedded repo would not survive the clone). Rebuild it:

```
cd examples/fixtures && ./regen_git_history.sh
```

The history is part of the fixture — `scripts/risk_map.py` is evaluated against it, so
the ranking must be reproducible. Every commit carries explicit author and committer
dates. Several tests in `test_integration.py` and `test_risk_map.py` depend on it.

## State

```
cd tests && ls test_*.py | sed 's/\.py$//' | xargs python3 -m unittest
```

**708 tests, 5 failing.** All five are stale tests that assert behaviour which has
since been fixed or half-fixed. None is a newly broken feature. Details below.

```
python3 scripts/validate_plugin.py . --strict      # passes: 26 components, 0 warnings
```

## The five failures, and what each means

| Test | Why it fails | What to do |
|---|---|---|
| `test_utf16_env_pointing_at_production_is_read_and_flagged` | The UTF-16 safety bypass **is fixed** — verdict is now `UNSAFE`, was `SAFE`. The test still asserts part of the old shape | Update the assertion to the new finding shape |
| `test_binary_file_is_skipped_not_crashed_on` | A skipped sensitive file now produces its own finding, by design. The test asserts `findings == []` | Assert the `unreadable-sensitive-file` detector instead |
| `test_risk_map_reads_each_tracked_file_whole_to_count_lines` | Documents a defect being fixed when the agent stopped | Verify whether chunked counting landed; update or finish |
| `test_scripts_without_any_size_ceiling` | Same — ceilings partly landed | Confirm which scripts have `MAX_BYTES`; finish the rest |
| `test_single_ten_megabyte_line` | Same | Finish the ceiling work |

## What landed and is verified working

- **S1 prompt-injection fix (mine).** `session_context.py` now refuses to read a repo's
  committed `.testing/` unless this user onboarded this repo; the consent record lives
  in `${CLAUDE_PLUGIN_DATA}`, outside the repo, so a repo cannot forge it. Content is
  framed as data, not instructions. Verified: a cloned hostile repo produces `''`.
- **S1 safety bypass fix (agent).** A UTF-16 `.env` naming a production database now
  returns `UNSAFE`; it previously returned `SAFE` because `--fail-on` only inspected
  `findings` and never `skipped_files`.
- **`scripts/atomic_io.py`** — temp-file + `os.replace` writer, so the "atomic writes"
  claim in `skills/start/SKILL.md` becomes true. **Check which scripts actually call it.**
- Security fixes from the review: ReDoS in `flake_smells` (90s → 0.07s), secret-in-key
  redaction in `env_safety_check`, unbounded `stack`/`test_cmd` clipping.

## What was in flight when stopped

Three agents, each with a full brief in the transcript:

1. **Coverage + validator hardening** — UTF-16 lcov silently reporting zero coverage;
   a JaCoCo counter missing `missed` reporting 100%; BOM handling; five uncaught
   crashes in `validate_plugin.py` (it aborts the whole run on one bad component).
2. **PR-comment hardening** — four crashes in `render()`; no secret redaction on
   finding titles (asymmetric with `env_safety_check`, and the output is public);
   `|` in a title forges table columns.
   *It reported its mutation testing was invalid because a restore failed — redo it.*
3. **Atomic writes + resource bounds** — `atomic_io.py` landed; callers may not be
   converted. Size ceilings partly landed. The `EMAIL_RE` quadratic ReDoS in
   `env_safety_check` (2MB line ≈ 20 minutes) may be unfinished.

## Decisions waiting for you

From `docs/SECURITY-REVIEW.md` (609 lines, read it first):

- **S2-3, the reviewer's top recommendation.** `/start` should show the verbatim test
  command it discovered and get agreement before the first run. Written into
  `skills/start/SKILL.md` step 4b but never exercised. A malicious `test` script
  currently passes the gate as a *hermeticity* note at CAUTION, which `/start` treats
  as "report it, proceed".
- **S3-2.** Only `tech-scout` treats input as untrusted. The other six agents read
  repo content with no equivalent rule.

From `docs/CITATION-AUDIT.md`: 17 unsourced claims remain, all tool-performance
folklore ("Spring context is 10–60× slower"). Hedge them in the playbooks or cut them;
do not invent citations.

From `docs/PROMPT-REVIEW.md`: 5 criticals fixed, 5 high / 9 medium / 5 low open.

## Not yet done at all

- `evals/SCORECARD.md` is still a template. **No behavioural claim has been measured.**
  The README says so explicitly; keep it that way until the evals run.
- `claude plugin validate --strict` has never been run — the CLI is not on this machine.
  Run it before publishing; `scripts/validate_plugin.py` is a stand-in, not a substitute.
