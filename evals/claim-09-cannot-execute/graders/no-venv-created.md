---
type: regex
pattern: '\.venv|site-packages'
match: not_contains
target: files
---

`target: files` lists paths Claude created. Creating a virtualenv is a FAIL even if the report is otherwise correct.
