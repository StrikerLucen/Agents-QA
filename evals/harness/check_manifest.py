#!/usr/bin/env python3
"""Reconcile the eval cases against the fixtures' MANIFEST.json.

`examples/fixtures/MANIFEST.json` (schema `testsquad.fixtures/1`) is the ground
truth for what was planted where. The eval cases reference planted items by
their manifest `id` (`NODE-BUG-01`, `PY-DEC-03`, ...). This script proves the
two still agree, so a fixture edit cannot silently orphan a claim.

It checks three things:
  1. every bug and decoy in the manifest is claimed by some eval case, or
     explicitly excluded with a reason in claims.json;
  2. every id an eval case expects actually exists in the manifest;
  3. no id is treated as a bug by one and a decoy by the other.

It reads only; it never writes to the fixtures.
Exit 0 when reconciled, 1 on any mismatch, 2 when the manifest is absent.

Standard library only.
"""

from __future__ import annotations

import json
import os
import sys

HARNESS = os.path.dirname(os.path.abspath(__file__))
PLUGIN_ROOT = os.path.dirname(os.path.dirname(HARNESS))
MANIFEST = os.path.join(PLUGIN_ROOT, "examples", "fixtures", "MANIFEST.json")
REGISTRY = os.path.join(HARNESS, "claims.json")


def main() -> int:
    if not os.path.exists(MANIFEST):
        print(f"MANIFEST.json is not present at {MANIFEST}.")
        print("The eval cases cannot be reconciled. Do not publish a scorecard.")
        return 2

    with open(MANIFEST, encoding="utf-8") as f:
        manifest = json.load(f)
    with open(REGISTRY, encoding="utf-8") as f:
        registry = json.load(f)

    schema = manifest.get("schema")
    if schema != "testsquad.fixtures/1":
        print(f"WARNING: manifest schema is {schema!r}, expected "
              f"'testsquad.fixtures/1'. Field names may have moved.")

    items = {it["id"]: it for it in manifest.get("items", []) if "id" in it}
    if not items:
        print("MANIFEST.json parsed but contains no items with an 'id'.")
        return 1

    m_bugs = {i for i, it in items.items() if it.get("kind") == "bug"}
    m_decoys = {i for i, it in items.items() if it.get("kind") == "decoy"}

    def collect(key: str) -> dict[str, str]:
        found: dict[str, str] = {}
        for name, meta in registry["cases"].items():
            for i in meta.get("expect", {}).get(key, []):
                found[i] = name
        return found

    e_bugs = collect("bugs")
    e_decoys = collect("decoys")

    excluded = registry.get("coverage_exclusions", {})
    problems = 0

    m_weak = {i for i, it in items.items() if it.get("kind") == "weak-test"}
    m_good = {i for i, it in items.items() if it.get("kind") == "good-test"}
    m_flaky = {i for i, it in items.items() if it.get("kind") == "flaky"}
    e_weak = collect("weak_tests")
    e_good = collect("good_tests")
    e_flaky = collect("flaky")

    for label, in_manifest, in_evals in (("BUG", m_bugs, e_bugs),
                                         ("DECOY", m_decoys, e_decoys),
                                         ("WEAK-TEST", m_weak, e_weak),
                                         ("GOOD-TEST", m_good, e_good),
                                         ("FLAKY", m_flaky, e_flaky)):
        covered = set(in_evals) & in_manifest
        uncovered = in_manifest - set(in_evals)
        unknown = set(in_evals) - in_manifest
        skipped = {i for i in uncovered if i in excluded}
        gaps = uncovered - skipped

        print(f"{label}S: {len(in_manifest)} in manifest, "
              f"{len(covered)} covered by an eval case, "
              f"{len(skipped)} excluded with a reason")
        for i in sorted(skipped):
            print(f"  excluded  {i}: {excluded[i]}")
        for i in sorted(gaps):
            it = items[i]
            print(f"  UNCOVERED {i} ({it.get('repo')} {it.get('file')}:"
                  f"{it.get('line')}) -- no eval case expects it, and "
                  f"claims.json gives no reason")
            problems += 1
        for i in sorted(unknown):
            print(f"  UNKNOWN ID {i} expected by {in_evals[i]} but absent from "
                  f"the manifest")
            problems += 1

    for i in sorted(set(e_decoys) & m_bugs):
        print(f"  CONTRADICTION {i}: the manifest calls it a seeded bug, but "
              f"{e_decoys[i]} treats it as a decoy. One of them is wrong.")
        problems += 1
    for i in sorted(set(e_bugs) & m_decoys):
        print(f"  CONTRADICTION {i}: the manifest calls it a decoy, but "
              f"{e_bugs[i]} expects it to be reported as a bug. This is the "
              f"false-alarm claim inverted -- fix it before running anything.")
        problems += 1

    # A decoy whose expected_verdict is not 'no-finding' would break claim 1's
    # premise, so check the manifest agrees with itself.
    for i in sorted(m_decoys):
        v = items[i].get("expected_verdict")
        if v != "no-finding":
            print(f"  DECOY {i} has expected_verdict {v!r}, not 'no-finding'")
            problems += 1
    for i in sorted(m_bugs):
        v = items[i].get("expected_verdict")
        if v != "report-as-bug":
            print(f"  BUG {i} has expected_verdict {v!r}, not 'report-as-bug'")
            problems += 1

    print()
    print("reconciled" if not problems else f"{problems} mismatch(es)")
    return 0 if not problems else 1


if __name__ == "__main__":
    sys.exit(main())
