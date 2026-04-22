"""Tests for src/scrapers/ingamejob.py — Bootstrap+icon DOM parser."""
from scrapers.ingamejob import InGameJobScraper, _icon_text
from bs4 import BeautifulSoup


_REGULAR_CARD = """
<div class="employer-job-listing-single shadow-sm bg-white mb-3 p-3">
  <div class="listing-job-info container">
    <div class="row text-muted">
      <div class="col-12 p-0">
        <h5>
          <a href="https://ingamejob.com/en/job/2d-artist-mobileigaming">
              2D Artist (Mobile/iGaming)
          </a>
        </h5>
      </div>
      <div class="col-sm-6 p-0">
        <p class="m-0"><strong><i class="la la-building-o"></i> App Masters</strong></p>
        <p class="m-0"><i class="text-muted la la-map-marker"></i> Remote</p>
        <p class="m-0"><i class="la la-clock-o"></i> Posted 9 hours ago</p>
      </div>
      <div class="col-sm-6 p-0">
        <p class="m-0"><i class="la la-area-chart"></i> Middle</p>
        <p class="m-0"><i class="la la-money"></i> Negotiable</p>
        <p class="m-0"><i class="la la-briefcase"></i> <span class="pr-2">Part time</span></p>
      </div>
    </div>
  </div>
</div>
"""

_PREMIUM_CARD = """
<div class="employer-job-listing-single premium-job shadow-sm bg-white mb-3 p-3">
  <div class="listing-job-info container">
    <h5><a href="https://ingamejob.com/en/job/lead-creative-producer-3">Lead Creative Producer</a></h5>
    <p class="m-0"><strong><i class="la la-building-o"></i> Values Value</strong></p>
    <p class="m-0"><i class="la la-briefcase"></i> Full time</p>
  </div>
</div>
"""

_KYIV_CARD = """
<div class="employer-job-listing-single">
  <h5><a href="https://ingamejob.com/en/job/unity-engineer-x">Unity Engineer</a></h5>
  <p><strong><i class="la la-building-o"></i> Acme Studios</strong></p>
  <p><i class="la la-map-marker"></i> Kyiv, Ukraine (office)</p>
  <p><i class="la la-briefcase"></i> Full time</p>
</div>
"""


def _payload(html: str = _REGULAR_CARD, slug: str = "2d-artist-mobileigaming"):
    return {
        "_slug": slug,
        "_href": f"https://ingamejob.com/en/job/{slug}",
        "_url":  f"https://ingamejob.com/en/job/{slug}",
        "_html": html,
    }


# ---------- _icon_text ----------------------------------------------------

def test_icon_text_finds_row:
    soup = BeautifulSoup(_REGULAR_CARD, "html.parser")
    assert _icon_text(soup, "la-map-marker") == "Remote"
    assert _icon_text(soup, "la-area-chart") == "Middle"


def test_icon_text_returns_empty_when_missing:
    soup = BeautifulSoup(_PREMIUM_CARD, "html.parser")
    assert _icon_text(soup, "la-map-marker") == ""


# ---------- parse ---------------------------------------------------------

def test_parse_regular_card:
    s   = InGameJobScraper
    raw = s.parse(_payload)
    assert raw is not None
    assert raw.native_id == "2d-artist-mobileigaming"
    assert raw.title     == "2D Artist (Mobile/iGaming)"
    assert raw.company   == "App Masters"
    assert raw.location  == "Remote"
    assert raw.remote    is True
    # contract gets stuffed into description for keyword scoring
    assert raw.description and "Part time" in raw.description


def test_parse_premium_card_no_location:
    s   = InGameJobScraper
    raw = s.parse(_payload(html=_PREMIUM_CARD, slug="lead-creative-producer-3"))
    assert raw is not None
    assert raw.title    == "Lead Creative Producer"
    assert raw.company  == "Values Value"
    assert raw.location is None
    assert raw.remote   is None


def test_parse_office_location_marks_remote_false:
    s   = InGameJobScraper
    raw = s.parse(_payload(html=_KYIV_CARD, slug="unity-engineer-x"))
    assert raw is not None
    assert raw.remote is False
    assert "Kyiv" in (raw.location or "")


def test_parse_skips_when_no_company:
    s = InGameJobScraper
    no_co = """
    <div class="employer-job-listing-single">
      <h5><a href="https://ingamejob.com/en/job/x">Director</a></h5>
    </div>
    """
    assert s.parse(_payload(html=no_co, slug="x")) is None


def test_parse_skips_when_no_title:
    s = InGameJobScraper
    no_title = """
    <div class="employer-job-listing-single">
      <p><strong><i class="la la-building-o"></i> Acme</strong></p>
    </div>
    """
    assert s.parse(_payload(html=no_title, slug="y")) is None
