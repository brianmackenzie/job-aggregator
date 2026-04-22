"""scrape_worker — runs one scraper end-to-end.

Invoked async by scrape_dispatcher. Event shape: {"source": "remoteok"}.

The list of imports below is what populates the scraper registry — each
import triggers the @register decorator on its module. New scrapers added
in later phases must be added to this import list.
"""
# Import every implemented scraper module so @register fires.
# Each import triggers the @register decorator on its class.
# daily JSON sources
from scrapers import himalayas as _himalayas      # noqa: F401
from scrapers import hnhiring as _hnhiring        # noqa: F401
from scrapers import remoteok as _remoteok        # noqa: F401
# ATS sources (weekly)
from scrapers import ashby as _ashby              # noqa: F401
from scrapers import greenhouse as _greenhouse    # noqa: F401
from scrapers import lever as _lever              # noqa: F401
# Workday cxs API (weekly, alongside the other ATS scrapers)
from scrapers import workday as _workday          # noqa: F401
# SmartRecruiters public API (weekly, alongside the other ATS scrapers)
from scrapers import smartrecruiters as _smartrecruiters  # noqa: F401
# Apify LinkedIn (daily)
from scrapers import apify_linkedin as _apify_linkedin   # noqa: F401
# HTML/RSS/CSV sources (daily, 06:30 UTC after the JSON batch)
from scrapers import asgc_sheet as _asgc_sheet           # noqa: F401
from scrapers import bettingjobs as _bettingjobs         # noqa: F401
from scrapers import builtinnyc as _builtinnyc           # noqa: F401
from scrapers import fractional_jobs as _fractional_jobs # noqa: F401
from scrapers import gamesindustry as _gamesindustry     # noqa: F401
from scrapers import hitmarker as _hitmarker             # noqa: F401
from scrapers import welcometothejungle as _wttj         # noqa: F401
from scrapers import wellfound as _wellfound             # noqa: F401
from scrapers import weworkremotely as _wwr              # noqa: F401
from scrapers import working_nomads as _working_nomads   # noqa: F401
# additional gaming-industry sources (daily, 06:30 UTC).
# `community_sheets` registers four thin subclasses of asgc_sheet
# (sheet_rehm / sheet_mayne / sheet_tucker / sheet_ploger) — one
# import is enough for all of them.
from scrapers import community_sheets as _community_sheets  # noqa: F401
from scrapers import games_jobs_direct as _games_jobs_direct  # noqa: F401
from scrapers import outscal as _outscal                      # noqa: F401
from scrapers import remote_game_jobs as _remote_game_jobs    # noqa: F401
from scrapers import work_with_indies as _work_with_indies    # noqa: F401
# Tier-A gaming-industry sources (daily, 06:30 UTC).
from scrapers import games_career as _games_career    # noqa: F401
from scrapers import ingamejob as _ingamejob          # noqa: F401
from scrapers import game_jobs_uk as _game_jobs_uk    # noqa: F401

from common.logging import log
from scrapers.registry import get_scraper, list_scrapers


def handler(event, context):
    source = (event or {}).get("source")
    if not source:
        log.error("scrape_worker_missing_source", event=event)
        return {"ok": False, "error": "missing event.source"}

    try:
        scraper_cls = get_scraper(source)
    except KeyError:
        log.error(
            "scrape_worker_unknown_source",
            source=source,
            known=list_scrapers,
        )
        return {"ok": False, "error": f"unknown source {source!r}"}

    # One-shot-override plumbing (introduced for the Phase-9 historical seed).
    # Callers can pass `{"source": "apify_linkedin", "overrides": {...}}` to
    # tweak one run without touching config/sources.yaml. The scraper opts
    # into overrides by reading `self.overrides` in its fetch method.
    # Scrapers that don't care for overrides simply ignore the attribute.
    overrides = (event or {}).get("overrides") or {}
    scraper = scraper_cls
    if overrides:
        scraper.overrides = overrides
        log.info("scrape_worker_overrides_applied", source=source, overrides=overrides)

    # scrape_run is self-contained — handles its own errors, writes
    # the ScrapeRuns row, archives raw payloads. We just call it.
    summary = scraper.scrape_run
    return {"ok": True, "summary": summary}
