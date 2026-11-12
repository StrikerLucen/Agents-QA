#!/usr/bin/env bash
# Seeds the python-pkg fixture into this run's workspace.
set -euo pipefail
exec "$(cd "$(dirname "$0")/../harness" && pwd)/seed_fixture.sh" python-pkg
