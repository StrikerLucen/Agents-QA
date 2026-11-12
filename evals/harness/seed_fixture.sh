#!/usr/bin/env bash
# Copy a seeded fixture into the current eval run's workspace.
#
# Called from each case's fixture.sh, which `claude plugin eval --scaffold` runs
# before the agent starts, as you, outside the agent's sandbox, with $PWD set to
# the run workspace.
#
# Usage: seed_fixture.sh <fixture-name>
#   node-ts | python-pkg | java-min        -- copied verbatim
#   node-ts-onboarded                      -- node-ts plus a prior testsquad install
#
# The fixtures are READ-ONLY inputs. This script never writes to them.
set -euo pipefail

FIXTURE="${1:?usage: seed_fixture.sh <fixture-name>}"
HARNESS="$(cd "$(dirname "$0")" && pwd)"
PLUGIN_ROOT="$(cd "$HARNESS/../.." && pwd)"
SRC_ROOT="$PLUGIN_ROOT/examples/fixtures"

SRC_NAME="$FIXTURE"
[ "$FIXTURE" = "node-ts-onboarded" ] && SRC_NAME="node-ts"

SRC="$SRC_ROOT/$SRC_NAME"
if [ ! -d "$SRC" ]; then
  echo "seed_fixture: no fixture at $SRC" >&2
  echo "seed_fixture: expected the testsquad plugin root at $PLUGIN_ROOT" >&2
  exit 1
fi

# -a preserves the seeded .git history (claim 4 and the risk map depend on it).
cp -a "$SRC/." "$PWD/"

# The fixture README tells an agent not to touch the fixture; inside the eval
# workspace that instruction is noise and biases the run. Drop it.
rm -f "$PWD/README.md"

if [ "$FIXTURE" = "node-ts-onboarded" ]; then
  # Simulate a repository testsquad has already been installed into, so a second
  # /testsquad:start can be checked for idempotence (claim 11).
  mkdir -p "$PWD/.testing/lessons" "$PWD/.testing/tasks"

  cat > "$PWD/CLAUDE.md" <<'EOF'
# Project notes

Hand-written content that predates testsquad. It must survive re-onboarding.

<!-- testsquad:start -->
## Testing

This project uses testsquad. Run `/testsquad:health` for the suite baseline and
`/testsquad:verify` before accepting any testing work.
<!-- testsquad:end -->
EOF

  cat > "$PWD/.testing/profile.json" <<'EOF'
{
  "stack": "node-ts",
  "test_command": "npx vitest run",
  "package_manager": "npm",
  "ci": null,
  "profiled_at": "2026-01-01"
}
EOF

  cat > "$PWD/.testing/conventions.md" <<'EOF'
# Conventions (approved)

- Tests live in `tests/`, named `<module>.spec.ts`.
- Never sleep on the real clock in a test; inject a sleeper.
EOF

  cat > "$PWD/.testing/lessons/2026-01-01-suite-order.md" <<'EOF'
# Lesson: suite order

- Date: 2026-01-01
- Claim: this suite is order-dependent above 200 tests
- Evidence: `npx vitest run --sequence.shuffle` -> 3 failures that pass in isolation
- Confidence: high
- Re-check: 2026-04-01
EOF

  cat > "$PWD/.testing/health-baseline.json" <<'EOF'
{
  "taken_at": "2026-01-01",
  "status": "CANNOT_EXECUTE",
  "reason": "dependencies not installed",
  "coverage": null
}
EOF
fi

echo "seed_fixture: seeded $FIXTURE into $PWD"
