"""Tests for src/scrapers/builtinnyc.py — parse against fixture HTML."""
from scrapers.builtinnyc import BuiltInNYCScraper


_CARD = """
<div class="card">
  <h2><a href="/job/director-of-eng-12345">Director of Engineering</a></h2>
  <span class="company-name">ExampleSports</span>
  <span class="location-text">New York, NY (Hybrid)</span>
</div>
"""

_CARD_REMOTE = """
<div class="card">
  <h2><a href="/job/vp-platform-67890">VP, Platform</a></h2>
  <span class="company-name">Example Studio</span>
  <span class="location-text">Remote, US</span>
</div>
"""

_CARD_INOFFICE = """
<div class="card">
  <h2><a href="/job/lead-designer-99999">Lead Designer</a></h2>
  <span class="company-name">Acme Corp</span>
  <span class="location-text">In-Office NYC</span>
</div>
"""


def _payload(html: str = _CARD, href: str = "/job/director-of-eng-12345"):
    return {
        "_href": href,
        "_url":  f"https://www.builtinnyc.com{href}",
        "_html": html,
    }


def test_parse_canonical_card:
    s   = BuiltInNYCScraper
    raw = s.parse(_payload)
    assert raw is not None
    assert raw.title     == "Director of Engineering"
    assert raw.company   == "ExampleSports"
    assert raw.location  == "New York, NY (Hybrid)"
    assert raw.native_id == "director-of-eng-12345"
    # 'Hybrid' is intentionally None — neither remote nor onsite.
    assert raw.remote is None


def test_parse_remote_inferred:
    s   = BuiltInNYCScraper
    raw = s.parse(_payload(html=_CARD_REMOTE,
                            href="/job/vp-platform-67890"))
    assert raw is not None
    assert raw.remote is True


def test_parse_inoffice_inferred:
    s   = BuiltInNYCScraper
    raw = s.parse(_payload(html=_CARD_INOFFICE,
                            href="/job/lead-designer-99999"))
    assert raw is not None
    assert raw.remote is False


def test_parse_skips_when_no_company:
    s = BuiltInNYCScraper
    no_company = """
    <div><h2>Director</h2><span class="location-text">NYC</span></div>
    """
    assert s.parse(_payload(html=no_company, href="/job/x")) is None


def test_parse_skips_when_href_blank:
    s = BuiltInNYCScraper
    assert s.parse({"_href": "", "_url": "", "_html": _CARD}) is None


# Live-shape regression: 2026-04 BuiltInNYC uses a Bootstrap 5 + Alpine.js
# card with semantic data-id attributes (job-card-title, company-title,
# company-img). The work-mode pill ("In-Office"/"Hybrid"/"Remote") and
# the city sit in spans inside .bounded-attribute-section, each preceded
# by a Font Awesome icon. Locked in so we notice if BuiltIn changes
# the data-id contract.
_LIVE_CARD_HTML = """
<div class="job-bounded-responsive position-relative bg-white p-md rounded-3"
     data-id="job-card" id="job-card-9093232">
  <div class="row" id="main">
    <div class="col-12 col-lg-7 left-side-tile">
      <div class="left-side-tile-item-1">
        <a href="/company/example-insurance-company">
          <picture>
            <img alt="Example Insurance Company Logo"
                 data-id="company-img"
                 src="/cdn/blue-box-logo.png" height="72" width="72"/>
          </picture>
        </a>
      </div>
      <div class="left-side-tile-item-2">
        <a data-id="company-title"
           href="/company/example-insurance-company">
          <span>Example Insurance Company</span>
        </a>
      </div>
      <div class="left-side-tile-item-3">
        <h2>
          <a data-id="job-card-title"
             href="/job/corporate-vice-president-nyl-com-product-management-institutional-audiences/9093232">
            Corporate Vice President, Example.com Product Management, Institutional Audiences
          </a>
        </h2>
      </div>
    </div>
    <div class="col-12 col-lg-5 bounded-attribute-section">
      <div class="d-flex align-items-start gap-sm">
        <div class="d-flex justify-content-center align-items-center">
          <i class="fa-regular fa-house-building"></i>
        </div>
        <span class="font-barlow text-gray-04">In-Office</span>
      </div>
      <div class="d-flex align-items-start gap-sm">
        <div class="d-flex justify-content-center align-items-center">
          <i class="fa-regular fa-location-dot"></i>
        </div>
        <div><span class="font-barlow text-gray-04">New York, NY</span></div>
      </div>
    </div>
  </div>
</div>
"""


def test_parse_live_builtinnyc_card_shape:
    """Locked-in fixture from a real 2026-04 listing payload."""
    s   = BuiltInNYCScraper
    raw = s.parse({
        "_href": "/job/corporate-vice-president-nyl-com-product-management-institutional-audiences/9093232",
        "_url":  "https://www.builtinnyc.com/job/corporate-vice-president-nyl-com-product-management-institutional-audiences/9093232",
        "_html": _LIVE_CARD_HTML,
    })
    assert raw is not None
    assert raw.title    == ("Corporate Vice President, Example.com Product "
                            "Management, Institutional Audiences")
    # The " Logo" suffix must be stripped from the img alt text — but here
    # the structured <a data-id="company-title"> beats the alt-text fallback.
    assert raw.company  == "Example Insurance Company"
    # Work mode + city joined with ", " for clean scoring.
    assert raw.location == "In-Office, New York, NY"
    assert raw.remote is False  # "In-Office" → onsite
    assert raw.native_id == "9093232"


def test_parse_live_builtinnyc_company_falls_back_to_logo_alt:
    """If the data-id="company-title" anchor is missing, strip ' Logo' from
    the company img alt text."""
    html = """
    <div data-id="job-card">
      <img data-id="company-img" alt="Acme Corp Logo"/>
      <a data-id="job-card-title" href="/job/x">Engineer</a>
    </div>
    """
    s   = BuiltInNYCScraper
    raw = s.parse({
        "_href": "/job/x",
        "_url":  "https://www.builtinnyc.com/job/x",
        "_html": html,
    })
    assert raw is not None
    assert raw.company == "Acme Corp"
