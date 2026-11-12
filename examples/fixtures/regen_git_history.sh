#!/usr/bin/env bash
# Rebuild the deterministic git history of all three fixture repositories.
#
# The history is part of the fixture: scripts/risk_map.py is evaluated against
# it, so the ranking it produces must be reproducible on any machine. Every
# commit therefore carries an explicit GIT_AUTHOR_DATE and GIT_COMMITTER_DATE
# and a fixed author.
#
#   ./regen_git_history.sh          # rebuild all three
#   ./regen_git_history.sh node-ts  # rebuild one
#
# This DESTROYS each repo's .git directory and recreates it. It never changes
# the working files: the hot file is backed up and restored byte for byte.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"

DANA_NAME="Dana Okafor"
DANA_EMAIL="dana@fixture.example"
SAM_NAME="Sam Reyes"
SAM_EMAIL="sam@fixture.example"

commit() {           # commit <repo> <iso-date> <author:dana|sam> <message>
  local repo="$1" when="$2" who="$3" msg="$4"
  local name email
  if [ "$who" = "dana" ]; then name="$DANA_NAME"; email="$DANA_EMAIL";
  else name="$SAM_NAME"; email="$SAM_EMAIL"; fi
  git -C "$repo" add -A
  GIT_AUTHOR_DATE="$when" GIT_COMMITTER_DATE="$when" \
  GIT_AUTHOR_NAME="$name" GIT_AUTHOR_EMAIL="$email" \
  GIT_COMMITTER_NAME="$name" GIT_COMMITTER_EMAIL="$email" \
  git -C "$repo" -c commit.gpgsign=false -c user.name="$name" -c user.email="$email" \
      commit -q --allow-empty -m "$msg"
}

# Replace (or insert) a single revision-marker comment on line 2 of a file, so
# each churn commit shows up as one added and one deleted line.
mark() {             # mark <file> <comment-prefix> <revision>
  python3 - "$1" "$2" "$3" <<'PY'
import sys
path, prefix, rev = sys.argv[1], sys.argv[2], sys.argv[3]
lines = open(path, encoding="utf-8").read().split("\n")
marker = f"{prefix} revision r{rev} -- see CHANGELOG"
if len(lines) > 1 and lines[1].startswith(f"{prefix} revision r"):
    lines[1] = marker
else:
    lines.insert(1, marker)
open(path, "w", encoding="utf-8").write("\n".join(lines))
PY
}

build() {            # build <repo> <hot-file> <warm-file> <comment-prefix> <legacy-file> <legacy-body>
  local repo="$ROOT/$1" hot="$2" warm="$3" prefix="$4" legacy="$5" legacy_body="$6"
  echo "=== $1"
  rm -rf "$repo/.git"
  git -C "$repo" init -q -b main
  git -C "$repo" config commit.gpgsign false

  local backup_hot backup_warm
  backup_hot="$(mktemp)"; cp "$repo/$hot" "$backup_hot"
  backup_warm="$(mktemp)"; cp "$repo/$warm" "$backup_warm"

  # A file that exists only in history. Created here, deleted at the end.
  printf '%s\n' "$legacy_body" > "$repo/$legacy"

  commit "$repo" "2025-01-13T09:12:00+00:00" dana "feat: initial shop skeleton"

  # --- commits are in chronological order; the hot file is churned by a
  # --- run of bug fixes, the warm file is touched twice, everything else
  # --- is never modified after it was created.
  mark "$repo/$hot" "$prefix" 2
  commit "$repo" "2025-02-04T11:40:00+00:00" sam   "fix: tax was computed on the wrong base for discounted lines"
  mark "$repo/$hot" "$prefix" 3
  commit "$repo" "2025-03-18T16:05:00+00:00" dana  "fix: rounding drifted by a cent on large quantities"
  mark "$repo/$warm" "$prefix" 2
  commit "$repo" "2025-04-02T09:00:00+00:00" sam   "feat: expose a windowing helper"
  mark "$repo/$hot" "$prefix" 4
  commit "$repo" "2025-05-22T10:31:00+00:00" sam   "refactor: extract the base-amount helper"
  mark "$repo/$hot" "$prefix" 5
  commit "$repo" "2025-07-09T14:18:00+00:00" dana  "fix: regression in the split remainder"
  cp "$backup_warm" "$repo/$warm"
  commit "$repo" "2025-08-14T15:45:00+00:00" sam   "fix: wrong page count on a partial last page"
  mark "$repo/$hot" "$prefix" 6
  commit "$repo" "2025-09-30T08:55:00+00:00" sam   "fix: incorrect total when a line is fully discounted"
  cp "$backup_hot" "$repo/$hot"
  commit "$repo" "2025-10-28T13:07:00+00:00" dana  "fix: broken currency total reported by support"

  # --- a deletion, so the history contains a file that no longer exists --
  rm -f "$repo/$legacy"
  commit "$repo" "2025-11-03T12:00:00+00:00" dana  "chore: drop the superseded legacy tax table"

  rm -f "$backup_hot" "$backup_warm"
  git -C "$repo" --no-pager log --date=short --pretty='%ad  %an  %s'
  echo
}

do_node() {
  build node-ts "src/cart.ts" "src/pagination.ts" "//" "src/legacy-tax.ts" \
"// Superseded flat tax table. Deleted 2025-11-03.
export const LEGACY_RATE = 0.07;"
}

do_python() {
  build python-pkg "shop/invoice.py" "shop/ranges.py" "#" "shop/legacy_tax.py" \
"\"\"\"Superseded flat tax table. Deleted 2025-11-03.\"\"\"

LEGACY_RATE = 0.07"
}

do_java() {
  build java-min \
    "src/main/java/com/example/shop/Cart.java" \
    "src/main/java/com/example/shop/Ranges.java" "//" \
    "src/main/java/com/example/shop/LegacyTax.java" \
"package com.example.shop;

/** Superseded flat tax table. Deleted 2025-11-03. */
public final class LegacyTax {
    public static final double LEGACY_RATE = 0.07;
    private LegacyTax() {
    }
}"
}

target="${1:-all}"
case "$target" in
  node-ts)    do_node ;;
  python-pkg) do_python ;;
  java-min)   do_java ;;
  all)        do_node; do_python; do_java ;;
  *) echo "unknown fixture: $target" >&2; exit 2 ;;
esac
