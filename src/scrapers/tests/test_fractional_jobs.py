"""Tests for src/scrapers/fractional_jobs.py — parse against fixture HTML.

No HTTP. fetch is exercised by the live smoke test.
"""
from scrapers.fractional_jobs import FractionalJobsScraper, _clean


# ---------- _clean ---------------------------------------------------------

def test_clean_collapses_whitespace:
    assert _clean("Hello\n\n  world\t!") == "Hello world !"


def test_clean_handles_empty:
    assert _clean("") == ""
    assert _clean(None) == ""


# ---------- parse --------------------------------------------------------

# Minimal fixture HTML modeled on the actual fractionaljobs.io DOM. We only
# care about the structural shape: h2 = title, then short text nodes for
# company + location, no long copy.

_CARD_HTML = """
<a href="/jobs/fractional-cto-acme">
  <div>
    <h2>Fractional CTO</h2>
    <p>Acme Corp</p>
    <span>Remote</span>
  </div>
</a>
"""

_CARD_HTML_WITH_CITY = """
<a href="/jobs/fractional-vp-eng-example">
  <div>
    <h3>Fractional VP Engineering</h3>
    <p>Example Studio</p>
    <span>Bellevue, WA</span>
  </div>
</a>
"""

_CARD_HTML_NO_TITLE = """
<a href="/jobs/no-title">
  <div>
    <p>Acme Corp</p>
    <span>Remote</span>
  </div>
</a>
"""


def _payload(html: str = _CARD_HTML, href: str = "/jobs/fractional-cto-acme"):
    return {
        "_href": href,
        "_url":  f"https://www.fractionaljobs.io{href}",
        "_html": html,
    }


def test_parse_canonical_card:
    s   = FractionalJobsScraper
    raw = s.parse(_payload)
    assert raw is not None
    assert raw.native_id == "fractional-cto-acme"
    assert raw.title     == "Fractional CTO"
    assert raw.company   == "Acme Corp"
    assert raw.location  == "Remote"
    assert raw.remote is True
    assert raw.url       == "https://www.fractionaljobs.io/jobs/fractional-cto-acme"


def test_parse_city_state_location:
    s   = FractionalJobsScraper
    raw = s.parse(_payload(html=_CARD_HTML_WITH_CITY,
                            href="/jobs/fractional-vp-eng-example"))
    assert raw is not None
    assert raw.title    == "Fractional VP Engineering"
    assert raw.company  == "Example Studio"
    assert raw.location == "Bellevue, WA"
    # City/state with no remote signal → remote stays None (unknown).
    assert raw.remote is None


def test_parse_skips_when_no_title:
    """Card with no h1/h2/h3 → skip rather than fabricate a title."""
    s = FractionalJobsScraper
    assert s.parse(_payload(html=_CARD_HTML_NO_TITLE,
                             href="/jobs/no-title")) is None


def test_parse_skips_when_href_blank:
    s = FractionalJobsScraper
    assert s.parse({"_href": "", "_url": "", "_html": _CARD_HTML}) is None


def test_parse_skips_long_description_as_company:
    """A very long text node should be treated as description, not company."""
    long_card = """
    <a href="/jobs/x">
      <h2>Fractional CTO</h2>
      <p>This is an extremely long description that should clearly be filtered
         out from the company-name slot because it exceeds the threshold of
         eighty characters and is obviously not a company name.</p>
      <span>Remote</span>
    </a>
    """
    s = FractionalJobsScraper
    raw = s.parse(_payload(html=long_card, href="/jobs/x"))
    # No short text node qualifies as company → skip.
    assert raw is None


# Live-shape regression: the 2026-04 fractionaljobs.io DOM uses a Webflow
# template where the card is .job-item, the title block is split across
# multiple <h3> sibling tags joined by " - ", and location/hours/rate live
# in a separate .job-item_more-info div delimited by " | ".
_LIVE_CARD_HTML = """
<div class="job-item w-dyn-item" role="listitem">
  <div class="job-item_content">
    <div class="job-item_job-info">
      <div class="job-item_name_url">
        <div class="text-size-regular text-inline">
          <h3 class="text-size-regular text-inline">A Small Business Acquisition Marketplace</h3>
          <h3 class="text-size-regular text-inline"> - </h3>
          <h3 class="text-size-regular text-inline">Senior Full-stack Engineer</h3>
        </div>
      </div>
      <div class="job-item_more-info">
        <div class="text-inline">20 - 40 hrs</div>
        <div class="text-inline"> | </div>
        <div class="text-inline">$125 - $150 / hr</div>
        <div class="text-inline"> | </div>
        <div class="text-inline">Hybrid (NYC only)</div>
      </div>
    </div>
  </div>
</div>
"""


def test_parse_live_fractional_jobs_card_shape:
    """Locked-in fixture from a real 2026-04 listing payload."""
    s   = FractionalJobsScraper
    raw = s.parse({
        "_href": "/jobs/senior-full-stack-engineer-at-a-small-business-acquisition-marketplace",
        "_url":  "https://www.fractionaljobs.io/jobs/senior-full-stack-engineer-at-a-small-business-acquisition-marketplace",
        "_html": _LIVE_CARD_HTML,
    })
    assert raw is not None
    assert raw.company == "A Small Business Acquisition Marketplace"
    assert raw.title   == "Senior Full-stack Engineer"
    # Last pipe-segment of more-info is the location/work-type signal.
    assert raw.location == "Hybrid (NYC only)"
    # "Hybrid" without "remote" → remote stays None.
    assert raw.remote is None
