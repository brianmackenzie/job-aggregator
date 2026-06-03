"""Community Google Sheet scrapers.

Generic scaffold for "a community curator publishes a Google Sheet of
job openings as a CSV". Each subclass below corresponds to ONE
curator's CSV — they all share the parser logic in `ASGCSheetScraper`
(typical publish path: File → Share → Publish to web → CSV).

To wire up a curator:
  1. Pick one of the pre-registered slots below (or copy the pattern
     to add more).
  2. In `config/sources.yaml`, set the slot's `csv_url` to the
     curator's CSV publish URL:
         <slot_name>:
           enabled: true
           csv_url: "https://docs.google.com/spreadsheets/d/.../pub?output=csv"
  3. Make sure the source name is listed in `template.yaml`'s
     `DailyHtmlRssScrapeSchedule` so the daily scrape includes it.

When a slot's `csv_url` is empty, the scraper yields zero jobs (a
successful run with `jobs_found=0`, not an error) — so you can register
a slot before the curator shares the sheet, and it'll silently no-op
until the URL is populated.

Each subclass overrides `source_name` so the ScrapeRuns row is
per-curator and you can see on health.html which sheet went stale.
The slot identifiers below are stable strings used as registry keys
and DynamoDB partition keys; rename them by editing this file *and*
`config/sources.yaml` *and* `template.yaml`'s schedule list together
(otherwise the scrape worker won't find them).
"""
from scrapers.asgc_sheet import ASGCSheetScraper
from scrapers.registry import register


@register("sheet_rehm")
class SheetRehmScraper(ASGCSheetScraper):
    """Curated community openings sheet — wire CSV URL in config/sources.yaml."""
    source_name = "sheet_rehm"


@register("sheet_mayne")
class SheetMayneScraper(ASGCSheetScraper):
    """Curated community openings sheet — wire CSV URL in config/sources.yaml."""
    source_name = "sheet_mayne"


@register("sheet_tucker")
class SheetTuckerScraper(ASGCSheetScraper):
    """Curated community openings sheet — wire CSV URL in config/sources.yaml."""
    source_name = "sheet_tucker"


@register("sheet_ploger")
class SheetPlogerScraper(ASGCSheetScraper):
    """Curated community openings sheet — wire CSV URL in config/sources.yaml."""
    source_name = "sheet_ploger"
