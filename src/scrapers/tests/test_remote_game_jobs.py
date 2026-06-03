"""Tests for src/scrapers/remote_game_jobs.py — parse against fixture HTML.

No HTTP. fetch is exercised by the live smoke test.
"""
from scrapers.remote_game_jobs import RemoteGameJobsScraper


# Live-shape fixture from a real 2026-04 listing payload (DreamForge card).
# Bulma CSS template: .job-box → <a> → <article class="media"> → strong.f-20
# (title) + small.f-15 (company) + span.tag.is-warning (skill chips).
_CARD_HTML = """
<div class="job-box box has-background-light hvr-grow-shadow">
  <a class="has-text-black"
     href="https://remotegamejobs.com/jobs/dreamforge-head-of-community-growth-remote-job-f7785ffb"
     title="DreamForge is hiring Head of Community &amp; Growth (Remote Job)">
    <article class="media">
      <div class="media-left">
        <figure class="image is-64x64">
          <img class="is-rounded" src="https://example.com/dreamforge.png"/>
        </figure>
      </div>
      <div class="media-content">
        <div class="content">
          <strong class="f-20">Head of Community &amp; Growth</strong>
          <small class="f-15">DreamForge</small>
          <div>
            <span><i class="fas fa-hourglass"></i> Full-Time</span>
          </div>
          <div>
            <span class="tag is-warning is-normal">Social Media</span>
            <span class="tag is-warning is-normal">community</span>
            <span class="tag is-warning is-normal">growth</span>
          </div>
        </div>
      </div>
    </article>
  </a>
</div>
"""


# Card with NO structured strong/small but a clean anchor title attribute —
# tests the regex-fallback path.
_CARD_HTML_TITLE_ONLY = """
<div class="job-box box">
  <a href="https://remotegamejobs.com/jobs/saguni-monetization-designer"
     title="Saguni Studio is hiring Monetization Designer (Remote Job)">
    <article><div></div></article>
  </a>
</div>
"""


def _payload(html: str = _CARD_HTML,
             href: str = "https://remotegamejobs.com/jobs/dreamforge-head-of-community-growth-remote-job-f7785ffb"):
    return {
        "_href": href,
        "_url":  href,
        "_html": html,
    }


def test_parse_canonical_card:
    s   = RemoteGameJobsScraper
    raw = s.parse(_payload)
    assert raw is not None
    assert raw.title    == "Head of Community & Growth"
    assert raw.company  == "DreamForge"
    # Site premise: every job is remote, so location is hard-coded "Remote".
    assert raw.location == "Remote"
    assert raw.remote   is True
    # Tag chips collected into description for keyword scoring.
    assert raw.description is not None
    assert "Social Media" in raw.description
    assert "community" in raw.description
    assert raw.native_id == ("dreamforge-head-of-community-growth-"
                             "remote-job-f7785ffb")


def test_parse_falls_back_to_anchor_title_attribute:
    """If structured strong.f-20 / small.f-15 are missing, parse the
    anchor's title attribute: '<Company> is hiring <Role> (Remote Job)'."""
    s   = RemoteGameJobsScraper
    raw = s.parse(_payload(html=_CARD_HTML_TITLE_ONLY,
                            href="https://remotegamejobs.com/jobs/saguni-monetization-designer"))
    assert raw is not None
    assert raw.company == "Saguni Studio"
    assert raw.title   == "Monetization Designer"
    assert raw.remote is True


def test_parse_skips_when_no_company_or_title:
    s = RemoteGameJobsScraper
    bare = """
    <div class="job-box">
      <a href="https://remotegamejobs.com/jobs/x"></a>
    </div>
    """
    assert s.parse(_payload(html=bare,
                             href="https://remotegamejobs.com/jobs/x")) is None


def test_parse_skips_when_href_blank:
    s = RemoteGameJobsScraper
    assert s.parse({"_href": "", "_url": "", "_html": _CARD_HTML}) is None
