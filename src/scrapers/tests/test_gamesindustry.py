"""Tests for src/scrapers/gamesindustry.py — parse against fixture HTML."""
from scrapers.gamesindustry import GamesIndustryScraper


_CARD = """
<div class="job-row">
  <h2><a href="/jobs/lead-producer-example-12345">Lead Producer</a></h2>
  <span class="company">Example Studio</span>
  <span class="location">Bellevue, WA</span>
</div>
"""

_CARD_REMOTE = """
<div class="job-row">
  <h2><a href="/job/director-of-product-12345">Director of Product</a></h2>
  <span class="company">Example Publisher</span>
  <span class="location">Remote, US</span>
</div>
"""


def _payload(html: str = _CARD, href: str = "/jobs/lead-producer-example-12345"):
    return {
        "_href": href,
        "_url":  f"https://jobs.gamesindustry.biz{href}",
        "_html": html,
    }


def test_parse_canonical_card:
    s   = GamesIndustryScraper
    raw = s.parse(_payload)
    assert raw is not None
    assert raw.title     == "Lead Producer"
    assert raw.company   == "Example Studio"
    assert raw.location  == "Bellevue, WA"
    assert raw.native_id == "lead-producer-example-12345"
    assert raw.remote is None


def test_parse_remote_inferred:
    s   = GamesIndustryScraper
    raw = s.parse(_payload(html=_CARD_REMOTE,
                            href="/job/director-of-product-12345"))
    assert raw is not None
    assert raw.title   == "Director of Product"
    assert raw.company == "Example Publisher"
    assert raw.remote is True


def test_parse_skips_when_no_company:
    s = GamesIndustryScraper
    no_company = """
    <div><h2>Director</h2><span class="location">London</span></div>
    """
    assert s.parse(_payload(html=no_company, href="/jobs/x")) is None


def test_parse_skips_when_no_title:
    s = GamesIndustryScraper
    no_title = """
    <div><span class="company">Acme</span><span class="location">London</span></div>
    """
    assert s.parse(_payload(html=no_title, href="/jobs/x")) is None


def test_parse_skips_when_href_blank:
    s = GamesIndustryScraper
    assert s.parse({"_href": "", "_url": "", "_html": _CARD}) is None


# Live-shape regression: 2026-04 GamesIndustry.biz uses a Drupal/Views
# template where the listing card is <article class="node--job-per-template">.
# Title lives in <h2 class="node__title"><a>...</a></h2>, company in
# <span class="recruiter-company-profile-job-organization">, location in
# <div class="location"><span>...</span></div>, and the date in
# <span class="date">. The picture/img in the logo block also carries the
# company name as a title/alt attribute (used as a fallback).
_LIVE_CARD_HTML = """
<article about="/job/talent-acquisition-partner-fixed-term-43935"
         class="node node--job-per-template node-teaser has-logo node-job"
         id="node-43935">
  <div class="job__logo">
    <a class="recruiter-job-link"
       href="https://jobs.gamesindustry.biz/job/talent-acquisition-partner-fixed-term-43935"
       title="Talent Acquisition Partner [Fixed Term]">
      <picture title="Example Studios">
        <img alt="Example Studios" title="Example Studios"
             src="/sites/default/files/styles/.../example.png"/>
      </picture>
    </a>
  </div>
  <div class="mobile_job__content">
    <div class="node__content">
      <h2 class="node__title">
        <a class="recruiter-job-link"
           href="https://jobs.gamesindustry.biz/job/talent-acquisition-partner-fixed-term-43935"
           title="Talent Acquisition Partner [Fixed Term]">
          Talent Acquisition Partner [Fixed Term]
        </a>
      </h2>
      <div class="description">
        <span class="date">15 Apr 2026,</span>
        <span class="recruiter-company-profile-job-organization">
          <a href="/company/example-studios">Example Studios</a>
        </span>
      </div>
      <div class="location"><span>Stockholm, Sweden</span></div>
    </div>
  </div>
</article>
"""


def test_parse_live_gamesindustry_card_shape:
    """Locked-in fixture from a real 2026-04 listing payload."""
    s   = GamesIndustryScraper
    raw = s.parse({
        "_href": "https://jobs.gamesindustry.biz/job/talent-acquisition-partner-fixed-term-43935",
        "_url":  "https://jobs.gamesindustry.biz/job/talent-acquisition-partner-fixed-term-43935",
        "_html": _LIVE_CARD_HTML,
    })
    assert raw is not None
    assert raw.title    == "Talent Acquisition Partner [Fixed Term]"
    assert raw.company  == "Example Studios"
    assert raw.location == "Stockholm, Sweden"
    assert raw.native_id == "talent-acquisition-partner-fixed-term-43935"
    # Stockholm is not a remote signal → remote stays None.
    assert raw.remote is None
    # Date parsed from "15 Apr 2026," → 2026-04-15T00:00:00.
    assert raw.posted_at is not None
    assert raw.posted_at.startswith("2026-04-15")
