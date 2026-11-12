#!/usr/bin/env bash
# Seeds the node-ts-onboarded fixture into this run's workspace.
set -euo pipefail
exec "$(cd "$(dirname "$0")/../harness" && pwd)/seed_fixture.sh" node-ts-onboarded
