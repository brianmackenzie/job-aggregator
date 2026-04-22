"""/api/health — recent scrape runs, grouped & sorted.

Iterates every registered scraper, asks DynamoDB for its 3 most recent
ScrapeRuns rows, and returns the union sorted by run_timestamp desc
(capped at 20). Powers health.html in ; also exposes a
small "scrape_runs" array on the dashboard for debug.

The scraper modules are imported here so the registry is populated.
This is the only place outside scrape_worker that needs the imports;
the API Gateway-side cold start cost is negligible (<50ms).
"""
import json

# Import every implemented scraper so list_scrapers returns the full set.
# Mirror of scrape_worker.py — keep these in sync when adding sources.
# daily JSON sources
from scrapers import himalayas as _himalayas      # noqa: F401
from scrapers import hnhiring as _hnhiring        # noqa: F401
from scrapers import remoteok as _remoteok        # noqa: F401
# ATS sources (weekly)
from scrapers import ashby as _ashby              # noqa: F401
from scrapers import greenhouse as _greenhouse    # noqa: F401
from scrapers import lever as _lever              # noqa: F401
# Apify LinkedIn (daily)
from scrapers import apify_linkedin as _apify     # noqa: F401
# HTML/RSS/CSV (daily)
from scrapers import asgc_sheet as _asgc          # noqa: F401
from scrapers import bettingjobs as _bettingjobs  # noqa: F401
from scrapers import builtinnyc as _builtinnyc    # noqa: F401
from scrapers import fractional_jobs as _frac     # noqa: F401
from scrapers import gamesindustry as _gi         # noqa: F401
from scrapers import hitmarker as _hitmarker      # noqa: F401
from scrapers import welcometothejungle as _wttj  # noqa: F401
from scrapers import wellfound as _wellfound      # noqa: F401
from scrapers import weworkremotely as _wwr       # noqa: F401
from scrapers import working_nomads as _wn        # noqa: F401
# additional gaming-industry sources
from scrapers import community_sheets as _cs      # noqa: F401
from scrapers import games_jobs_direct as _gjd    # noqa: F401
from scrapers import outscal as _outscal          # noqa: F401
from scrapers import remote_game_jobs as _rgj     # noqa: F401
from scrapers import work_with_indies as _wwi     # noqa: F401
# Tier-A gaming-industry sources
from scrapers import game_jobs_uk as _gju         # noqa: F401
from scrapers import games_career as _gc          # noqa: F401
from scrapers import ingamejob as _igj            # noqa: F401
# Workday cxs API (weekly)
from scrapers import workday as _workday          # noqa: F401
# SmartRecruiters public API (weekly)
from scrapers import smartrecruiters as _sr       # noqa: F401

from common import db
from scrapers.registry import list_scrapers


def handler(event, context):
    runs: list[dict] = 
    for source in list_scrapers:
        try:
            runs.extend(db.get_recent_scrape_runs(source, limit=3))
        except Exception as exc:
            # Don't let one bad query knock out the whole health endpoint.
            runs.append({
                "source_name": source,
                "status": "error",
                "error_message": f"health_query_failed: {exc}",
            })

    # Most recent across all sources first.
    runs.sort(key=lambda r: r.get("run_timestamp", ""), reverse=True)

    return {
        "statusCode": 200,
        "headers": {
            "content-type": "application/json",
            "cache-control": "no-store",
        },
        "body": json.dumps({
            "ok": True,
            "service": "jobs-aggregator",
            "phase": 3,
            "registered_sources": list_scrapers,
            "scrape_runs": runs[:20],
        }, default=str),
    }
