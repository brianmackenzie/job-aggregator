"""Google Sheets CSV scraper.

Generic scaffold for ingesting a community-curated job-openings list
that's published as a Google Sheet. The owner publishes the sheet as
a CSV using File → Share → Publish to web → CSV, and we hit that URL
on each scrape. The source name is `asgc_sheet` for historical reasons
(it was the first such sheet wired in); rename if desired, but make sure
to update `config/sources.yaml` and `template.yaml` together.

Config (config/sources.yaml):
    asgc_sheet:
      enabled: true
      csv_url: "https://docs.google.com/spreadsheets/d/.../pub?output=csv"

Sheet schema (positionally — header names vary by maintainer; we accept
several common spellings):
    Company | Role | Location | Posted | URL | Notes
    Company | Title | Location | Date   | Link | Notes
    Company | Position | Region | Posted Date | Apply URL | ...

We do best-effort header mapping. Rows missing Company OR Role are skipped.

Why CSV not the Sheets API: the API requires OAuth + an API key + a
non-trivial scopes story. Public CSV publish is one URL with no auth
and the maintainer can keep editing the sheet normally.
"""
import csv
import io
from typing import Iterable, Optional

import requests

from scrapers.base import BaseScraper, RawJob
from scrapers.registry import register
from scrapers.sources_config import load_source_config
from scrapers.user_agent import USER_AGENT as _USER_AGENT


# Header-name aliases. Lower-cased + stripped before lookup. First match wins.
_COMPANY_HEADERS  = ("company", "employer", "organization", "org")
_ROLE_HEADERS     = ("role", "title", "position", "job", "job title")
_LOCATION_HEADERS = ("location", "region", "city", "geo", "where")
_DATE_HEADERS     = ("posted", "date", "posted date", "added", "date added")
_URL_HEADERS      = ("url", "link", "apply url", "apply link", "job url", "posting")
_NOTES_HEADERS    = ("notes", "description", "details", "comments")


def _pick(row: dict, candidates: tuple[str, ...]) -> str:
    """Return the first non-empty value for any header in `candidates`.

    Headers are matched case-insensitively after stripping whitespace.
    Helpful when different maintainers spell the same column differently.
    """
    # Build a normalized lookup once per row. Coerce non-string values to
    # str — _row_index (int), and any other metadata callers might attach.
    normalized = {
        (str(k) if k is not None else "").strip.lower:
        (str(v) if v is not None else "").strip
        for k, v in row.items
    }
    for candidate in candidates:
        v = normalized.get(candidate)
        if v:
            return v
    return ""


@register("asgc_sheet")
class ASGCSheetScraper(BaseScraper):
    source_name = "asgc_sheet"
    schedule = "cron(30 6 * * ? *)"      # 06:30 UTC daily, with HTML batch
    rate_limit_rps = 1.0

    # Centralised in scrapers/user_agent.py — reads contact_email from
    # config/sources.yaml:scraper_defaults. Change that field, not this line.
    USER_AGENT = _USER_AGENT

    def fetch(self) -> Iterable[dict]:
        cfg = load_source_config(self.source_name)
        csv_url = cfg.get("csv_url")
        if not csv_url:
            # Not configured — yield nothing. The base scraper records this
            # as a successful zero-job run rather than an error.
            return

        self._throttle
        resp = requests.get(
            csv_url,
            headers={
                "User-Agent": self.USER_AGENT,
                "Accept": "text/csv",
            },
            timeout=30,
        )
        resp.raise_for_status

        # Google's CSV export uses UTF-8 with no BOM. csv.DictReader needs
        # a text stream — wrap the bytes through io.StringIO.
        text = resp.content.decode("utf-8-sig", errors="replace")
        reader = csv.DictReader(io.StringIO(text))

        # Track row index so we can build a stable native_id when the
        # sheet has no URL column for a given row.
        for idx, row in enumerate(reader):
            row["_row_index"] = idx
            yield row

    def parse(self, payload: dict) -> Optional[RawJob]:
        company = _pick(payload, _COMPANY_HEADERS)
        title   = _pick(payload, _ROLE_HEADERS)
        if not company or not title:
            # Maintainer left the row blank or in a non-standard schema.
            return None

        url      = _pick(payload, _URL_HEADERS)
        location = _pick(payload, _LOCATION_HEADERS) or None
        notes    = _pick(payload, _NOTES_HEADERS) or None
        posted   = _pick(payload, _DATE_HEADERS) or None

        # Build a stable native_id: prefer the URL slug; fall back to the
        # row index. Rows can move around in the sheet so the index isn't
        # ideal, but it's deterministic per scrape run.
        if url:
            native_id = url.rstrip("/").rsplit("/", 1)[-1] or url
        else:
            native_id = f"row-{payload.get('_row_index', 0)}"

        return RawJob(
            native_id=native_id,
            title=title,
            company=company,
            url=url,
            location=location,
            description=notes,
            posted_at=posted,
            # We don't know remote-ness reliably from a free-form sheet.
            # Leave None; downstream remote-flag inference still runs.
            remote=None,
            raw=payload,
        )
