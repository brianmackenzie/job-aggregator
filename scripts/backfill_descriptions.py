"""backfill_descriptions.py — one-time enrichment of empty job descriptions.

Several sources' list endpoints don't carry the job description, which
silently zeroes the QoL equity/benefits/flexibility signals AND starves the
semantic (Haiku) pass. The scrapers now fetch descriptions going forward
(Workday cxs detail endpoint; boards via JSON-LD-first detail-page extract),
but already-stored rows stay empty until re-scraped. This script backfills
them directly (UpdateItem description-only) — no full re-scrape needed.

Runs OUTSIDE Lambda (no 15-min ceiling): resumable (only touches rows whose
description is still empty, so re-running continues where it left off),
rate-limited, and bounded by --limit.

After a backfill, run a rescore so QoL (cheap, deterministic) and — with
--force-semantic — the Haiku score pick up the new descriptions.

Supported sources (must be description-wired):
    workday  + these boards:
    game_jobs_uk work_with_indies remote_game_jobs builtinnyc ingamejob
    hitmarker fractional_jobs gamesindustry
Use --source all-boards to do every board (skips workday).

Usage (PowerShell):
    $env:JOBS_TABLE = "REDACTED_DDB_TABLE"
    python scripts/backfill_descriptions.py --source workday --limit 20 --dry-run
    python scripts/backfill_descriptions.py --source workday
    python scripts/backfill_descriptions.py --source all-boards
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from urllib.parse import urlparse

import boto3
from boto3.dynamodb.conditions import Attr

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from scrapers.workday import fetch_workday_description  # noqa: E402
from scrapers.registry import get_scraper  # noqa: E402
# Import the wired board scrapers so the registry resolves them.
from scrapers import (  # noqa: E402,F401
    game_jobs_uk, work_with_indies, remote_game_jobs, builtinnyc,
    ingamejob, hitmarker, fractional_jobs, gamesindustry,
)

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/120 Safari/537.36")
_EMPTY_THRESHOLD = 50   # a description shorter than this counts as missing

BOARDS = ["game_jobs_uk", "work_with_indies", "remote_game_jobs", "builtinnyc",
          "ingamejob", "hitmarker", "fractional_jobs", "gamesindustry"]


def _derive_workday(row: dict):
    """Recover (base_url, tenant, site, external_path) from a stored row.
    url = {base_url}/{site}{external_path}; tenant from native_id/job_id."""
    url = row.get("url") or ""
    if "myworkdayjobs.com" not in url:
        return None
    p = urlparse(url)
    base_url = f"{p.scheme}://{p.netloc}"
    parts = p.path.strip("/").split("/", 1)
    if len(parts) < 2:
        return None
    site, external_path = parts[0], "/" + parts[1]
    native_id = row.get("native_id") or ""
    tenant = native_id.split(":")[0] if ":" in native_id else ""
    if not tenant:
        segs = (row.get("job_id") or "").split(":")   # workday:tenant:reqid
        tenant = segs[1] if len(segs) >= 3 else ""
    return (base_url, tenant, site, external_path) if tenant else None


def _make_describe(source):
    """Return a fn(row) -> description|None for the given source."""
    if source == "workday":
        hdr = lambda b, s: {"User-Agent": _UA, "Accept": "application/json",  # noqa: E731
                            "Origin": b, "Referer": f"{b}/{s}"}

        def _wd(row):
            d = _derive_workday(row)
            if not d:
                return None
            b, t, s, e = d
            return fetch_workday_description(b, t, s, e, hdr(b, s))
        return _wd

    # Board sources: the stored url IS the detail page.
    sc = get_scraper(source)()
    if hasattr(sc, "_fetch_description"):          # game_jobs_uk (own headers)
        return lambda row: sc._fetch_description(row.get("url")) if row.get("url") else None
    sel = getattr(sc, "_DESC_SELECTOR", None)      # generic auto-fetch boards
    return lambda row: sc._fetch_detail_description(row.get("url"), sel) if row.get("url") else None


def backfill_source(t, source, limit, rps, dry):
    describe = _make_describe(source)
    interval = 1.0 / rps if rps > 0 else 0.0
    scanned = updated = failed = 0
    ek = None
    print(f"\n[{source}] backfilling{' (DRY RUN)' if dry else ''} ...")
    while True:
        kw = {
            "ProjectionExpression": "job_id, native_id, #u, description, #s",
            "ExpressionAttributeNames": {"#u": "url", "#s": "source"},
            "FilterExpression": Attr("source").eq(source),
        }
        if ek:
            kw["ExclusiveStartKey"] = ek
        resp = t.scan(**kw)
        for row in resp.get("Items", []):
            scanned += 1
            if len(row.get("description") or "") >= _EMPTY_THRESHOLD:
                continue
            if interval:
                time.sleep(interval)
            try:
                desc = describe(row)
            except Exception:
                desc = None
            if not desc:
                failed += 1
                continue
            if dry:
                updated += 1
                print(f"  [dry] {row['job_id']}  +{len(desc)} chars")
            else:
                t.update_item(Key={"job_id": row["job_id"]},
                              UpdateExpression="SET description = :d",
                              ExpressionAttributeValues={":d": desc})
                updated += 1
                if updated % 25 == 0:
                    print(f"  [{source}] updated {updated} (scanned {scanned}) ...")
            if limit and updated >= limit:
                print(f"  [{source}] hit --limit {limit}")
                return scanned, updated, failed
        ek = resp.get("LastEvaluatedKey")
        if not ek:
            break
    return scanned, updated, failed


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", required=True,
                    help="workday | a board name | all-boards")
    ap.add_argument("--limit", type=int, default=0, help="max UPDATES per source (0=all)")
    ap.add_argument("--rps", type=float, default=1.0)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--table", default=os.environ.get("JOBS_TABLE"))
    args = ap.parse_args()
    if not args.table:
        print("Set JOBS_TABLE env or pass --table.", file=sys.stderr)
        return 2

    sources = BOARDS if args.source == "all-boards" else [args.source]
    valid = set(BOARDS) | {"workday"}
    for s in sources:
        if s not in valid:
            print(f"Source {s!r} not description-wired. Valid: {sorted(valid)} | all-boards",
                  file=sys.stderr)
            return 2

    t = boto3.resource("dynamodb", region_name="us-east-1").Table(args.table)
    tot_s = tot_u = tot_f = 0
    for s in sources:
        sc, up, fa = backfill_source(t, s, args.limit, args.rps, args.dry_run)
        tot_s += sc; tot_u += up; tot_f += fa
    print(f"\nTOTAL scanned={tot_s}  {'would-update' if args.dry_run else 'updated'}={tot_u}  "
          f"fetch-failed={tot_f}")
    if not args.dry_run and tot_u:
        print("Next: run a rescore (--force-semantic) so QoL + semantic pick up the new text.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
