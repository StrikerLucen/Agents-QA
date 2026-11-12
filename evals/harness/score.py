#!/usr/bin/env python3
"""Score a testsquad eval run as per-claim pass RATES, not a binary.

`claude plugin eval` scores each CASE. testsquad makes claims, and a claim is
carried by several cases. This regroups the runner's JSON by claim, computes the
rate of fully-passing runs per claim across every run of every case under it, and
compares that rate to the threshold the claim is published at.

A rate is the point. "Weak tests are rejected" is not a yes/no property of an LLM
system; it is a frequency, and the honest way to publish it is as one.

Standard library only.

Usage:
    python3 evals/harness/score.py results/<ts>/aggregate-result.json
    python3 evals/harness/score.py <file> --json scorecard.json
    python3 evals/harness/score.py <file> --skip-pregate
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import subprocess
import sys

HARNESS = os.path.dirname(os.path.abspath(__file__))
EVALS = os.path.dirname(HARNESS)
PLUGIN_ROOT = os.path.dirname(EVALS)
REGISTRY = os.path.join(HARNESS, "claims.json")


# --------------------------------------------------------------- claim 12
def run_pregate() -> dict:
    """Claim 12: the deterministic core must be green before anything else counts.

    If `scripts/*.py` are broken, every behavioural number below is measuring a
    broken tool and means nothing. This is cheap, so it runs first.
    """
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "unittest", "discover", "-s", "tests"],
            cwd=PLUGIN_ROOT, capture_output=True, text=True, timeout=300,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return {"passed": False, "detail": f"could not run: {exc}"}
    tail = (proc.stderr or proc.stdout or "").strip().splitlines()
    return {
        "passed": proc.returncode == 0,
        "detail": tail[-1] if tail else f"exit {proc.returncode}",
    }


# --------------------------------------------------------------- scoring

def threshold_resolution(threshold: float, runs: int) -> str | None:
    """Explain why a threshold cannot be measured at this run count, or None.

    With N runs the only observable rates are k/N. A threshold that falls
    between two of them is not a target, it is a rounding artefact: at 10 runs
    a "95%" bar is really "100%, with no margin", because 90% is the next value
    down. Publishing it as 95% overstates the tolerance the suite actually has
    and guarantees an unexplained failure the first time one run drifts.

    This is the same rule the product applies to itself -- never state a number
    you did not measure -- turned on the thing doing the measuring.
    """
    if runs <= 0:
        return "no runs recorded"
    attainable = {round(k / runs, 10) for k in range(runs + 1)}
    if round(threshold, 10) in attainable:
        return None
    below = max(r for r in attainable if r < threshold)
    above = min(r for r in attainable if r > threshold)
    needed = None
    for n in range(runs + 1, 501):
        if round(threshold, 10) in {round(k / n, 10) for k in range(n + 1)}:
            needed = n
            break
    fix = f"; {needed} runs would make it measurable" if needed else ""
    return (f"threshold {threshold:.0%} is not attainable in {runs} runs "
            f"(nearest are {below:.0%} and {above:.0%}){fix}")


def run_passed(run: dict) -> bool:
    """A run passes only when it errored out on nothing and every grader passed.

    Deliberately all-or-nothing. A partial grader score would let a case that
    cited no `file:line` average its way to a respectable number, which is the
    exact dishonesty this suite exists to prevent.
    """
    if run.get("error") or run.get("aborted"):
        return False
    graders = run.get("graders") or []
    if not graders:
        return False
    return all(g.get("passed") for g in graders)


def failed_graders(run: dict) -> list[str]:
    if run.get("error"):
        return [f"run error: {run['error']}"]
    if run.get("aborted"):
        return ["run aborted"]
    return [g.get("name", "?") for g in (run.get("graders") or [])
            if not g.get("passed")]


def score(result: dict, registry: dict) -> dict:
    claims = registry["claims"]
    case_meta = registry["cases"]

    by_claim: dict[str, dict] = {}
    unregistered: list[str] = []

    for case in result.get("cases", []):
        name = case.get("name", "")
        meta = case_meta.get(name)
        if meta is None:
            unregistered.append(name)
            continue
        cid = str(meta["claim"])
        bucket = by_claim.setdefault(cid, {"runs": 0, "passed": 0, "cases": []})

        # Score the WITH arm only: these are absolute acceptance criteria, not
        # an uplift measurement. A baseline arm answers a different question.
        runs = (case.get("arms") or {}).get("with") or []
        passed = sum(1 for r in runs if run_passed(r))
        reasons: dict[str, int] = {}
        for r in runs:
            if not run_passed(r):
                for g in failed_graders(r):
                    reasons[g] = reasons.get(g, 0) + 1

        bucket["runs"] += len(runs)
        bucket["passed"] += passed
        bucket["cases"].append({
            "name": name,
            "runs": len(runs),
            "passed": passed,
            "rate": round(passed / len(runs), 4) if runs else None,
            "failing_graders": dict(sorted(reasons.items(), key=lambda kv: -kv[1])),
        })

    out_claims = []
    for cid in sorted(claims, key=int):
        meta = claims[cid]
        bucket = by_claim.get(cid)
        if cid == "12":
            continue  # handled as the pre-gate
        if bucket is None or bucket["runs"] == 0:
            out_claims.append({
                "claim": int(cid), "title": meta["title"],
                "threshold": meta["threshold"], "rate": None,
                "runs": 0, "passed": 0, "verdict": "NO DATA",
                "why": meta["why"], "cases": [],
            })
            continue
        rate = bucket["passed"] / bucket["runs"]
        entry = {
            "claim": int(cid), "title": meta["title"],
            "threshold": meta["threshold"],
            "rate": round(rate, 4),
            "runs": bucket["runs"], "passed": bucket["passed"],
            "verdict": "PASS" if rate >= meta["threshold"] else "FAIL",
            "why": meta["why"],
            "cases": sorted(bucket["cases"], key=lambda c: c["name"]),
        }

        # A threshold between two observable rates is a rounding artefact, and
        # publishing it overstates the margin the suite actually has. Say so
        # on the result rather than letting the number travel unqualified.
        problem = threshold_resolution(meta["threshold"], bucket["runs"])
        if problem:
            entry["threshold_unmeasurable"] = problem

        # Some numbers are meaningless alone. A gatekeeper that rejects
        # everything scores 100% on "weak tests rejected"; only the paired
        # claim tells you whether it is a gate or a wall.
        pair = meta.get("must_publish_with")
        if pair:
            entry["must_publish_with"] = int(pair)
            entry["why_paired"] = meta.get("why_paired", "")

        out_claims.append(entry)

    return {"claims": out_claims, "unregistered_cases": sorted(unregistered)}


def depth_invariance(scored: dict) -> dict:
    """Claim 6 needs a cross-case comparison the per-case runner cannot make.

    Each depth case asserts the SAME expected verdict with the SAME graders, so
    if both sit at 1.00 the two depths agreed in every run. Anything less and we
    cannot tell agreement from a shared failure, so we say so rather than guess.
    """
    pair = [c for c in scored["claims"] if c["claim"] == 6]
    if not pair or not pair[0]["cases"]:
        return {"determined": False, "note": "no depth cases in this run"}
    rates = {c["name"]: c["rate"] for c in pair[0]["cases"]}
    if len(rates) < 2:
        return {"determined": False,
                "note": "both depth arms must run; only one is present"}
    if all(r == 1.0 for r in rates.values()):
        return {"determined": True, "agreed": True,
                "note": "both depths reached the expected verdict in every run"}
    return {"determined": False, "agreed": False, "rates": rates,
            "note": "at least one depth arm missed the expected verdict; "
                    "agreement cannot be established from grader booleans alone "
                    "-- read the HTML report's transcripts to see which verdict "
                    "each depth actually produced"}


# --------------------------------------------------------------- output
BAR = 24


def human(scored: dict, pregate: dict, meta: dict, depth: dict) -> str:
    L = []
    L.append("=" * 78)
    L.append("testsquad eval scorecard -- per-claim pass rates")
    L.append(f"date {meta['date']}   plugin {meta['version']}   "
             f"claude {meta['claude_version']}")
    L.append("=" * 78)
    L.append("")

    state = "PASS" if pregate["passed"] else "FAIL"
    L.append(f"PRE-GATE  claim 12  deterministic core ........... {state}")
    L.append(f"          {pregate['detail']}")
    if not pregate["passed"]:
        L.append("")
        L.append("  The deterministic core is broken. Every behavioural number")
        L.append("  below is measuring a broken tool and must not be published.")
    L.append("")
    L.append(f"{'CLAIM':<6}{'RATE':>8}{'REQ':>7}{'RUNS':>7}  {'':<{BAR}} VERDICT  TITLE")
    L.append("-" * 78)

    for c in scored["claims"]:
        if c["rate"] is None:
            L.append(f"{c['claim']:<6}{'--':>8}{c['threshold']:>7.2f}{0:>7}  "
                     f"{'':<{BAR}} NO DATA  {c['title']}")
            continue
        filled = int(round(c["rate"] * BAR))
        bar = "#" * filled + "." * (BAR - filled)
        L.append(f"{c['claim']:<6}{c['rate'] * 100:>7.1f}%{c['threshold']:>7.2f}"
                 f"{c['runs']:>7}  {bar} {c['verdict']:<8} {c['title']}")

    L.append("-" * 78)
    failing = [c for c in scored["claims"] if c["verdict"] == "FAIL"]
    nodata = [c for c in scored["claims"] if c["verdict"] == "NO DATA"]
    overall = "PASS" if (pregate["passed"] and not failing and not nodata) else "FAIL"
    L.append(f"OVERALL: {overall}   "
             f"{len(scored['claims']) - len(failing) - len(nodata)} of "
             f"{len(scored['claims'])} claims met")
    L.append("")
    L.append(f"depth invariance (claim 6): {depth['note']}")

    if failing:
        L.append("")
        L.append("WHAT FAILED")
        L.append("-" * 78)
        for c in failing:
            L.append(f"  claim {c['claim']} -- {c['title']}")
            L.append(f"    rate {c['rate'] * 100:.1f}% < required "
                     f"{c['threshold'] * 100:.0f}%")
            L.append(f"    {c['why']}")
            for case in c["cases"]:
                if case["rate"] == 1.0:
                    continue
                L.append(f"    {case['name']}: {case['passed']}/{case['runs']}")
                for g, n in case["failing_graders"].items():
                    L.append(f"        {g} failed in {n} run(s)")
            L.append("")

    if scored["unregistered_cases"]:
        L.append("UNREGISTERED CASES (not attributed to any claim, not scored):")
        for n in scored["unregistered_cases"]:
            L.append(f"  {n}")
        L.append("  Add them to evals/harness/claims.json or delete them.")

    return "\n".join(L) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("result", help="aggregate-result.json from `claude plugin eval --json`")
    ap.add_argument("--json", metavar="PATH", help="also write the scorecard as JSON")
    ap.add_argument("--skip-pregate", action="store_true",
                    help="do not run the deterministic-core unit tests first")
    args = ap.parse_args()

    try:
        with open(args.result, encoding="utf-8") as f:
            result = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"cannot read {args.result}: {exc}", file=sys.stderr)
        return 1

    with open(REGISTRY, encoding="utf-8") as f:
        registry = json.load(f)

    pregate = ({"passed": True, "detail": "skipped (--skip-pregate)"}
               if args.skip_pregate else run_pregate())

    scored = score(result, registry)
    depth = depth_invariance(scored)
    meta = {
        "date": _dt.date.today().isoformat(),
        "version": registry.get("plugin_version", "see .claude-plugin/plugin.json"),
        "claude_version": result.get("claudeVersion", "unknown"),
        "cost_usd": result.get("costUsd"),
        "duration_seconds": result.get("durationSeconds"),
        "partial": result.get("partial", False),
    }

    print(human(scored, pregate, meta, depth), end="")
    if meta["partial"]:
        print("\nWARNING: the runner reported a PARTIAL run "
              f"({result.get('partialReason')}). These rates are not publishable.")

    if args.json:
        doc = {
            "schema": "testsquad.scorecard/1",
            "generated": _dt.datetime.now().isoformat(timespec="seconds"),
            "meta": meta,
            "pregate": pregate,
            "depth_invariance": depth,
            "claims": scored["claims"],
            "unregistered_cases": scored["unregistered_cases"],
        }
        failing = [c for c in scored["claims"] if c["verdict"] != "PASS"]
        doc["overall"] = "PASS" if (pregate["passed"] and not failing
                                    and not meta["partial"]) else "FAIL"
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(doc, f, indent=2)
            f.write("\n")
        print(f"\nwrote {args.json}")

    bad = [c for c in scored["claims"] if c["verdict"] != "PASS"]
    return 0 if (pregate["passed"] and not bad and not meta["partial"]) else 1


if __name__ == "__main__":
    sys.exit(main())
