"""Verify every ATS slug in config/companies.yaml resolves to a live job board.

Per Section 7.5 of the phase plan: before shipping , Claude Code must
re-fetch each careers page and confirm the slug/ATS is correct. A stale slug
wastes an HTTP call per run and pollutes ScrapeRuns with warnings.

For each company whose `ats` is greenhouse / lever / ashby, this script:
  - Hits the public job-board API.
  - Reports: OK (2xx with postings), EMPTY (2xx with zero postings),
    NOT_FOUND (404), or ERROR (anything else).
  - Prints a one-line summary per company + a final table grouped by status.

Run from repo root:
    python scripts/verify_ats_slugs.py
"""
from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Optional

import requests
import yaml

REPO_ROOT     = Path(__file__).resolve.parent.parent
COMPANIES_YML = REPO_ROOT / "config" / "companies.yaml"

# Share the canonical User-Agent from src/scrapers/user_agent.py so the
# contact email lives in exactly one place (config/sources.yaml).
sys.path.insert(0, str(REPO_ROOT / "src"))
from scrapers.user_agent import USER_AGENT  # noqa: E402
TIMEOUT    = 20
RATE_LIMIT_SECS = 1.0   # same 1 rps cadence the scrapers use


# ---------- endpoint-per-ATS -------------------------------------------------

def _check_greenhouse(slug: str) -> tuple[str, Optional[int], str]:
    url = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"
    try:
        r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT)
    except Exception as exc:
        return ("ERROR", None, f"{type(exc).__name__}: {exc}")
    if r.status_code == 404:
        return ("NOT_FOUND", 404, url)
    if r.status_code != 200:
        return ("ERROR", r.status_code, f"HTTP {r.status_code}")
    try:
        n = len(r.json.get("jobs") or )
    except Exception:
        return ("ERROR", 200, "invalid JSON")
    return ("OK", 200, f"{n} postings") if n > 0 else ("EMPTY", 200, "0 postings")


def _check_lever(slug: str) -> tuple[str, Optional[int], str]:
    url = f"https://api.lever.co/v0/postings/{slug}"
    try:
        r = requests.get(
            url,
            params={"mode": "json"},
            headers={"User-Agent": USER_AGENT},
            timeout=TIMEOUT,
        )
    except Exception as exc:
        return ("ERROR", None, f"{type(exc).__name__}: {exc}")
    if r.status_code == 404:
        return ("NOT_FOUND", 404, url)
    if r.status_code != 200:
        return ("ERROR", r.status_code, f"HTTP {r.status_code}")
    try:
        data = r.json
    except Exception:
        return ("ERROR", 200, "invalid JSON")
    if not isinstance(data, list):
        return ("ERROR", 200, f"expected list, got {type(data).__name__}")
    return ("OK", 200, f"{len(data)} postings") if len(data) > 0 else ("EMPTY", 200, "0 postings")


def _check_ashby(slug: str) -> tuple[str, Optional[int], str]:
    url = f"https://api.ashbyhq.com/posting-api/job-board/{slug}"
    try:
        r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT)
    except Exception as exc:
        return ("ERROR", None, f"{type(exc).__name__}: {exc}")
    if r.status_code == 404:
        return ("NOT_FOUND", 404, url)
    if r.status_code != 200:
        return ("ERROR", r.status_code, f"HTTP {r.status_code}")
    try:
        data = r.json
    except Exception:
        return ("ERROR", 200, "invalid JSON")
    # Ashby returns {"jobPostings": [...]} on success; error responses
    # often still 200 with an error key. Treat missing jobPostings as EMPTY.
    postings = data.get("jobPostings")
    if postings is None:
        return ("ERROR", 200, f"keys={list(data.keys)}")
    return ("OK", 200, f"{len(postings)} postings") if len(postings) > 0 else ("EMPTY", 200, "0 postings")


_CHECKERS = {
    "greenhouse": _check_greenhouse,
    "lever":      _check_lever,
    "ashby":      _check_ashby,
}


def main -> int:
    with COMPANIES_YML.open(encoding="utf-8") as fh:
        companies = yaml.safe_load(fh).get("companies", )

    # Filter to only the three public-API ATSes.
    targets = [c for c in companies if c.get("ats") in _CHECKERS and c.get("ats_slug")]
    print(f"Verifying {len(targets)} ATS slugs "
          f"(Greenhouse + Lever + Ashby) at {RATE_LIMIT_SECS:.1f} rps\n")

    results: list[dict] = 
    last_req = 0.0
    for c in targets:
        ats  = c["ats"]
        slug = c["ats_slug"]
        name = c["name"]

        # Simple fixed-interval throttle — matches the scrapers' behavior.
        delay = RATE_LIMIT_SECS - (time.time - last_req)
        if delay > 0:
            time.sleep(delay)
        status, code, note = _CHECKERS[ats](slug)
        last_req = time.time

        results.append({
            "name":   name,
            "ats":    ats,
            "slug":   slug,
            "status": status,
            "code":   code,
            "note":   note,
        })
        # Colour-free status icon so it works in every terminal.
        icon = {"OK": "[OK]    ", "EMPTY": "[EMPTY] ",
                "NOT_FOUND": "[404]   ", "ERROR": "[ERR]   "}.get(status, "[?]     ")
        print(f"  {icon}{ats:10s} {slug:30s}  {note}   ({name})")

    # ---------- summary ----------
    buckets: dict[str, list[dict]] = {}
    for r in results:
        buckets.setdefault(r["status"], ).append(r)

    print("\n" + "=" * 70)
    print("Summary:")
    for status in ("OK", "EMPTY", "NOT_FOUND", "ERROR"):
        rows = buckets.get(status, )
        print(f"  {status:10s} {len(rows):3d}")

    bad = buckets.get("NOT_FOUND", ) + buckets.get("ERROR", )
    if bad:
        print("\nProblems to fix in config/companies.yaml:")
        for r in bad:
            # ASCII arrow — Windows cp1252 consoles barf on unicode arrows.
            print(f"  - {r['name']:40s} ats={r['ats']:<10s} slug={r['slug']:<25s} -> {r['status']} ({r['note']})")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main)
