---
type: regex
pattern: '[Ss]everity\**\s*:\s*\**\s*S[1-4]'
match: not_contains
---

The bug report format mandates a `Severity: S1..S4` line per confirmed
        finding. Zero bugs therefore means zero severity labels. This is the
        deterministic twin of `no-bug-reported.md`: it costs nothing and cannot
        be talked around.
