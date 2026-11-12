#!/usr/bin/env bash
# Seeds the node-ts fixture into this run's workspace.
set -euo pipefail
exec "$(cd "$(dirname "$0")/../harness" && pwd)/seed_fixture.sh" node-ts
