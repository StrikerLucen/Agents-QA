---
type: regex
pattern: '<!--\s*testsquad:start\s*-->[\s\S]*<!--\s*testsquad:start\s*-->'
match: not_contains
target: {source: file, path: CLAUDE.md}
---

The CLAUDE.md marker block must appear at most once after a re-run.
