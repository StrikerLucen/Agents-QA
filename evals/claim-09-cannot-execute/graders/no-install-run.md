---
type: regex
pattern: 'pip\s+install|python\s+-m\s+venv|virtualenv\s'
match: not_contains
target: trace
---
