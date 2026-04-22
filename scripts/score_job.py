"""score a single job from the CLI.

Four input modes (one is required):
  --job-id <id>                       look up by Jobs-table primary key
  --source <s> --native-id <n>        compute job_id and look up
  --json <file_or_string>             score a hypothetical job (no DB)
  --calibrate                         run the 5 spec calibration anchors

Examples (PowerShell):
  $env:ANTHROPIC_API_KEY = "sk-..."
  python scripts/score_job.py --job-id "remoteok:12345"
  python scripts/score_job.py --source remoteok --native-id 12345
  python scripts/score_job.py --json job.json --skip-semantic
  python scripts/score_job.py --json job.json --prefilter-only
  python scripts/score_job.py --calibrate

Flags:
  --skip-semantic   : never call Haiku; job gets tier=needs_review
                      if it passes the prefilter, tier=skip if it fails.
  --prefilter-only  : run ONLY the binary prefilter and print its verdict
                      (passed/failed, reason, flags). Does not invoke the
                      combined scorer — useful for debugging why a role
                      is/isn't being shown to Haiku.
  --force-semantic  : re-call Haiku even if a cached value exists.
  --calibrate       : run the 5 hand-graded fixtures from
                      config/candidate_profile.yaml and print PASS/FAIL.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

# Make src/ importable when running from the repo root.
_REPO_ROOT = Path(__file__).resolve.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "src"))


# ------------------------------------------------------------------
# Pretty printers
# ------------------------------------------------------------------

def _print_prefilter_verdict(label: str, job: dict, verdict: dict) -> None:
    """Print just the prefilter output — used by --prefilter-only.

    the prefilter is the binary gate, so this view answers
    the question "would Haiku ever see this job?" without actually
    making an API call. Helps the original author debug over-gating / under-gating.
    """
    print(f"\n=== PREFILTER ONLY: {label} ===")
    print(f"  Title:      {job.get('title','?')}")
    print(f"  Company:    {job.get('company','?')}")
    print(f"  Location:   {job.get('location','?')}")
    print
    passed = verdict.get("passed")
    print(f"  Passed:            {passed}  ({'Haiku would be called' if passed else 'no Haiku call - final score = 0'})")
    print(f"  Reason:            {verdict.get('prefilter_reason')}")
    print(f"  Track:             {verdict.get('track')}")
    print(f"  Industry:          {verdict.get('industry')} "
          f"(preference score {verdict.get('industry_score')}/10)")
    print(f"  Location flag:     {verdict.get('location_flag')}")
    print(f"  Company_normalized:{verdict.get('company_normalized')}")

    flags = 
    if verdict.get("is_dream_company"): flags.append("dream-tier")
    if verdict.get("is_hrc100"):        flags.append("HRC 100")
    if verdict.get("is_crunch_co"):     flags.append("crunch-risk")
    print(f"  Company flags:     {', '.join(flags) if flags else '(none)'}")

    hds = verdict.get("hard_disqualifiers") or 
    sws = verdict.get("soft_warnings") or 
    pos = verdict.get("positive_signals") or 
    print(f"  Hard disqualifiers: {', '.join(hds) if hds else '(none)'}")
    print(f"  Soft warnings:      {', '.join(sws) if sws else '(none)'}")
    print(f"  Positive signals:   {', '.join(pos) if pos else '(none)'}")


def _print_result(label: str, job: dict, result: dict) -> None:
    """Pretty-print a score_combined result for human eyes.

    drops the "ALGO X / SEMANTIC Y / BLENDED Z" formatting
    since there's no blend anymore. Now the story is "prefilter pass/fail,
    Haiku score (if called), final tier".
    """
    print(f"\n=== {label} ===")
    print(f"  Title:      {job.get('title','?')}")
    print(f"  Company:    {job.get('company','?')}")
    print(f"  Location:   {job.get('location','?')}")
    print

    # Prefilter verdict line.
    passed = result.get("passed_prefilter")
    reason = result.get("prefilter_reason") or "unknown"
    print(f"  PREFILTER:        {'PASS' if passed else 'FAIL'}   (reason: {reason})")

    pos = result.get("positive_signals") or 
    sws = result.get("soft_warnings") or 
    hds = result.get("hard_disqualifiers") or 
    if pos: print(f"  positive signals: {', '.join(pos)}")
    if sws: print(f"  soft warnings:    {', '.join(sws)}")
    if hds: print(f"  hard gates:       {', '.join(hds)}")

    # Semantic verdict line.
    sem = result.get("semantic_score")
    if sem is None:
        if result.get("semantic_api_failed"):
            print(f"  SEMANTIC:         FAILED — API error (see CloudWatch / stderr)")
        elif result.get("semantic_skipped"):
            if not passed:
                print(f"  SEMANTIC:         skipped (prefilter rejected — not worth the call)")
            else:
                print(f"  SEMANTIC:         skipped (--skip-semantic or kill switch off)")
        else:
            print(f"  SEMANTIC:         (unexpected None)")
    else:
        print(f"  SEMANTIC:         {sem:>3}   ({result.get('semantic_model','')})")
        print(f"  rationale:        {result.get('semantic_rationale','')}")
        # structured fields — most useful debug output.
        print(f"  role_family_match: {result.get('role_family_match')}")
        print(f"  industry_match:    {result.get('industry_match')}")
        print(f"  geography_match:   {result.get('geography_match')}")
        print(f"  level_match:       {result.get('level_match')}")
        print(f"  watchlist_dream:   {result.get('watchlist_dream')}")
        lfc = result.get("life_fit_concerns") or 
        if lfc:
            print(f"  life_fit_concerns: {', '.join(lfc)}")

    # Final.
    print
    print(f"  FINAL:            {result.get('score'):>3}   "
          f"tier={result.get('tier')}   track={result.get('track')}")


def _score(job: dict, *, skip_semantic: bool, force_semantic: bool) -> dict:
    """Run the combined scorer with consistent flags."""
    from scoring.combined import score_combined
    return score_combined(
        job, prefs={},
        skip_semantic=skip_semantic,
        force_semantic=force_semantic,
    )


def _prefilter_only(job: dict) -> dict:
    """Run only the binary prefilter. No Haiku call."""
    from scoring.algo_prefilter import prefilter
    return prefilter(job, prefs=None)


# ------------------------------------------------------------------
# Mode handlers
# ------------------------------------------------------------------

def _mode_dynamodb(args) -> int:
    """Look up a job in DynamoDB and score/prefilter it."""
    # Dev shell needs table-name env vars set; mirrors RUNBOOK §13.
    if not os.environ.get("JOBS_TABLE"):
        print("ERROR: JOBS_TABLE env var not set. Set it to the deployed "
              "Jobs table name (see RUNBOOK §13).", file=sys.stderr)
        return 2

    from common import db
    if args.job_id:
        job_id = args.job_id
    else:
        job_id = f"{args.source}:{args.native_id}"

    job = db.get_job(job_id)
    if not job:
        print(f"ERROR: no job found with job_id={job_id!r}", file=sys.stderr)
        return 3

    if args.prefilter_only:
        verdict = _prefilter_only(job)
        _print_prefilter_verdict(job_id, job, verdict)
        return 0

    result = _score(
        job,
        skip_semantic=args.skip_semantic,
        force_semantic=args.force_semantic,
    )
    _print_result(job_id, job, result)

    if args.write:
        # Persist the new score back. Useful for ad-hoc rescore of one
        # job without invoking the rescore Lambda.
        from common.normalize import score_posted_sk
        table = db.jobs_table
        update_parts = [
            "score = :s", "algo_score = :a", "track = :t",
            "score_posted = :sp", "modifiers_applied = :m",
            "gates_triggered = :g", "passed_prefilter = :pf",
            "prefilter_reason = :pr",
        ]
        values = {
            ":s":  result["score"],
            ":a":  result["algo_score"],
            ":t":  result["track"],
            ":sp": score_posted_sk(result["score"], job.get("posted_at", "")),
            ":m":  result["modifiers_applied"],
            ":g":  result["gates_triggered"],
            ":pf": result["passed_prefilter"],
            ":pr": result["prefilter_reason"],
        }
        if result.get("semantic_score") is not None:
            update_parts += [
                "semantic_score = :ss", "semantic_rationale = :sr",
                "semantic_scored_at = :sa", "semantic_model = :sm",
                "role_family_match = :rf", "industry_match = :im",
                "geography_match = :gm", "level_match = :lm",
                "watchlist_dream = :wd", "life_fit_concerns = :lfc",
            ]
            values[":ss"]  = result["semantic_score"]
            values[":sr"]  = result.get("semantic_rationale") or ""
            values[":sa"]  = result.get("semantic_scored_at") or ""
            values[":sm"]  = result.get("semantic_model") or ""
            values[":rf"]  = result.get("role_family_match") or "unclear"
            values[":im"]  = result.get("industry_match") or "unclear"
            values[":gm"]  = result.get("geography_match") or "unclear"
            values[":lm"]  = result.get("level_match") or "unclear"
            values[":wd"]  = bool(result.get("watchlist_dream") or False)
            values[":lfc"] = list(result.get("life_fit_concerns") or )
        table.update_item(
            Key={"job_id": job_id},
            UpdateExpression="SET " + ", ".join(update_parts),
            ExpressionAttributeValues=values,
        )
        print(f"\n  WRITTEN to DynamoDB.")
    return 0


def _mode_json(args) -> int:
    """Score a hypothetical job loaded from a JSON file or inline string."""
    raw = args.json
    if Path(raw).exists:
        with open(raw, "r", encoding="utf-8") as fh:
            job = json.load(fh)
    else:
        try:
            job = json.loads(raw)
        except (ValueError, TypeError) as exc:
            print(f"ERROR: --json arg is neither a path nor valid JSON: {exc}",
                  file=sys.stderr)
            return 2

    # Synthesise required fields if missing — the scoring pipeline needs them.
    job.setdefault("company_normalized", (job.get("company") or "").lower.strip)
    job.setdefault("posted_at", "")

    if args.prefilter_only:
        verdict = _prefilter_only(job)
        _print_prefilter_verdict("ad-hoc JSON", job, verdict)
        return 0

    result = _score(
        job,
        skip_semantic=args.skip_semantic,
        force_semantic=args.force_semantic,
    )
    _print_result("ad-hoc JSON", job, result)
    return 0


def _mode_calibrate(args) -> int:
    """Run the 5 hand-graded calibration anchors from candidate_profile.yaml."""
    import yaml
    profile_path = _REPO_ROOT / "config" / "candidate_profile.yaml"
    with open(profile_path, "r", encoding="utf-8") as fh:
        profile = yaml.safe_load(fh)
    anchors = profile.get("calibration_anchors") or 
    if not anchors:
        print("ERROR: no calibration_anchors in candidate_profile.yaml",
              file=sys.stderr)
        return 2

    passed = 0
    failed = 0
    for a in anchors:
        job = a["fixture"]
        job.setdefault("company_normalized",
                       (job.get("company") or "").lower.strip)
        job.setdefault("posted_at", "")
        result = _score(job, skip_semantic=False, force_semantic=True)
        score = result.get("score") or 0
        ok = a["expected_min"] <= score <= a["expected_max"]
        verdict = "PASS" if ok else "FAIL"
        if ok:
            passed += 1
        else:
            failed += 1
        _print_result(
            f"{verdict}: {a['name']} (expected {a['expected_min']}-{a['expected_max']})",
            job, result
        )

    print(f"\n=== Calibration: {passed} pass / {failed} fail ===")
    return 0 if failed == 0 else 1


# ------------------------------------------------------------------
# Arg parser
# ------------------------------------------------------------------

def main -> int:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--job-id", help="Jobs-table primary key, e.g. remoteok:12345")
    g.add_argument("--source", help="Source name (with --native-id)")
    g.add_argument("--json", help="Path to a JSON file OR an inline JSON string")
    g.add_argument("--calibrate", action="store_true",
                   help="Run the 5 spec calibration anchors")
    p.add_argument("--native-id", help="Required when --source is set")

    p.add_argument("--skip-semantic", action="store_true",
                   help="Never call Haiku; prefilter-pass jobs land in needs_review")
    p.add_argument("--prefilter-only", action="store_true",
                   help="Run ONLY the binary prefilter; print its verdict and exit "
                        "(no Haiku call, no score_combined flow)")
    p.add_argument("--force-semantic", action="store_true",
                   help="Re-call Haiku even if a cached value exists")
    p.add_argument("--write", action="store_true",
                   help="Persist the new score back to DynamoDB "
                        "(only with --job-id / --source)")

    args = p.parse_args

    if args.source and not args.native_id:
        p.error("--source requires --native-id")
    if args.write and args.json:
        p.error("--write only makes sense with --job-id / --source")
    if args.prefilter_only and args.write:
        p.error("--prefilter-only does not score; --write has nothing to persist")
    if args.prefilter_only and args.skip_semantic:
        p.error("--prefilter-only implies no semantic call; --skip-semantic is redundant")

    if args.calibrate:
        return _mode_calibrate(args)
    if args.json:
        return _mode_json(args)
    return _mode_dynamodb(args)


if __name__ == "__main__":
    sys.exit(main)
