"""Try common slug variants for every company whose current slug 404s.

Strategy: for each failing company, generate a list of candidate slugs from
the company name (normalized, hyphenated, no-spaces, etc.) and probe each
candidate against all three public-API ATSes (Greenhouse, Lever, Ashby).
First hit wins; report results so the original author can approve updates to
config/companies.yaml.

Output: a suggested YAML diff plus a list of "give up — use ats: null" rows.

Run from repo root:
    python scripts/discover_ats_slugs.py
"""
from __future__ import annotations

import re
import time
from pathlib import Path

import requests
import yaml

REPO_ROOT     = Path(__file__).resolve.parent.parent
COMPANIES_YML = REPO_ROOT / "config" / "companies.yaml"

# Share the canonical User-Agent from src/scrapers/user_agent.py so the
# contact email lives in exactly one place (config/sources.yaml).
import sys  # noqa: E402
sys.path.insert(0, str(REPO_ROOT / "src"))
from scrapers.user_agent import USER_AGENT  # noqa: E402
TIMEOUT    = 15
RATE_LIMIT = 0.5   # Slightly faster since we only make HEAD/GET for discovery.


def _slug_variants(name: str, current: str | None) -> list[str]:
    """Produce candidate slugs to try. Ordered most-to-least likely."""
    n = name.lower.strip
    # Strip common company-suffix noise that usually isn't in slugs.
    for suf in (
        " inc.", " inc", " corp.", " corp", " ltd", " llc",
        " entertainment", " international", " company", " games",
        ", the",
    ):
        if n.endswith(suf):
            n = n[: -len(suf)].strip

    base  = re.sub(r"[^a-z0-9 ]+", "", n)          # strip punctuation
    parts = base.split
    joined_hyphen = "-".join(parts)
    joined_none   = "".join(parts)

    candidates: list[str] = 
    if current:
        candidates.append(current)     # keep current in case it flaps
    candidates.extend([
        joined_hyphen,                  # "riot-games"
        joined_none,                    # "riotgames"
        parts[0] if parts else "",      # "riot"
        (parts[0] + parts[1]) if len(parts) >= 2 else "",   # "riotgames"
        (parts[0] + "-" + parts[1]) if len(parts) >= 2 else "",
    ])
    # De-duplicate while preserving order.
    seen = set
    out: list[str] = 
    for c in candidates:
        if c and c not in seen:
            seen.add(c)
            out.append(c)
    return out


def _probe(ats: str, slug: str) -> tuple[bool, str]:
    """Return (hit, detail) for one (ats, slug) pair."""
    if ats == "greenhouse":
        url = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"
    elif ats == "lever":
        url = f"https://api.lever.co/v0/postings/{slug}?mode=json"
    elif ats == "ashby":
        url = f"https://api.ashbyhq.com/posting-api/job-board/{slug}"
    else:
        return (False, f"unknown ats {ats}")
    try:
        r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT)
    except Exception as exc:
        return (False, f"{type(exc).__name__}")
    if r.status_code != 200:
        return (False, f"{r.status_code}")
    try:
        data = r.json
    except Exception:
        return (False, "bad-json")
    if ats == "greenhouse":
        n = len(data.get("jobs") or )
        return (True, f"{n} postings")
    if ats == "lever":
        if not isinstance(data, list):
            return (False, "not-list")
        return (True, f"{len(data)} postings")
    if ats == "ashby":
        p = data.get("jobPostings")
        if p is None:
            return (False, "no-jobPostings")
        return (True, f"{len(p)} postings")
    return (False, "fallthrough")


def _throttle(last: float) -> float:
    delay = RATE_LIMIT - (time.time - last)
    if delay > 0:
        time.sleep(delay)
    return time.time


def main -> int:
    with COMPANIES_YML.open(encoding="utf-8") as fh:
        companies = yaml.safe_load(fh).get("companies", )

    targets = [c for c in companies if c.get("ats") in ("greenhouse", "lever", "ashby")]
    print(f"Probing variants for {len(targets)} companies "
          f"(current slug first, then fallbacks)\n")

    suggestions: list[dict] = 
    last_req = 0.0

    for c in targets:
        name         = c["name"]
        current_ats  = c["ats"]
        current_slug = c.get("ats_slug")
        variants     = _slug_variants(name, current_slug)

        found = None
        # Try the company's current ATS first — if that fails on every slug,
        # fall through to the other two ATSes (a handful of companies have
        # migrated between providers since the starter table was compiled).
        ats_order = [current_ats] + [a for a in ("greenhouse", "lever", "ashby")
                                     if a != current_ats]
        for ats in ats_order:
            for slug in variants:
                last_req = _throttle(last_req)
                hit, detail = _probe(ats, slug)
                if hit:
                    found = {"ats": ats, "slug": slug, "detail": detail}
                    break
            if found:
                break

        row = {
            "name":   name,
            "current_ats":  current_ats,
            "current_slug": current_slug,
            "found":  found,
        }
        suggestions.append(row)

        if found:
            same = (found["ats"] == current_ats and found["slug"] == current_slug)
            marker = "KEEP " if same else "UPDATE"
            print(f"  [{marker}] {name:40s} {found['ats']:10s} {found['slug']:25s}  ({found['detail']})")
        else:
            print(f"  [NULL ] {name:40s} no public ATS found — set ats: null")

    # ---------- summary ----------
    updates = [r for r in suggestions if r["found"] and
               (r["found"]["ats"] != r["current_ats"] or r["found"]["slug"] != r["current_slug"])]
    nulls   = [r for r in suggestions if not r["found"]]

    print("\n" + "=" * 70)
    print(f"SUMMARY:  keep={len(suggestions) - len(updates) - len(nulls)}  "
          f"update={len(updates)}  null={len(nulls)}\n")

    if updates:
        print("Slug updates to apply (YAML form):\n")
        for r in updates:
            print(f"  - name: {r['name']!r}")
            print(f"    ats: {r['found']['ats']}")
            print(f"    ats_slug: {r['found']['slug']!r}    # was {r['current_ats']}/{r['current_slug']!r}")
            print
    if nulls:
        print("\nNo public ATS match — set `ats: null, ats_slug: null`:\n")
        for r in nulls:
            print(f"  - {r['name']:40s} (was {r['current_ats']}/{r['current_slug']!r})")

    return 0


if __name__ == "__main__":
    main
