"""Tests for src/scrapers/games_jobs_direct.py — parse against fixture HTML.

No HTTP. fetch is exercised by the live smoke test.
"""
from scrapers.games_jobs_direct import GamesJobsDirectScraper


# Live-shape fixture from a real 2026-04 listing payload (Bitmap Bureau card).
# The site stores company name inside the LOGO div's title attribute as
# "Posted by <Company>" — a quirky shape we strip in parse.
_CARD_HTML = """
<a href="/job/bitmap-bureau-ltd/senior-programmer/336631">
  <h4 class="job-title">Senior Programmer</h4>
  <p class="job-location">Southampton</p>
  <div class="job-desc margin-b-1">About Bitmap Bureau

  Founded in 2016 by industry veterans Matt Cope and Mike Tuc...</div>
  <div style="width: 120px">
    <div class="outer">
      <div class="inner"
           style="background-image:url(/assets/employer-images/employer-logo-8763.png#new)"
           title="Posted by Bitmap Bureau Ltd">
      </div>
    </div>
  </div>
</a>
"""


_CARD_HTML_REMOTE_DESC = """
<a href="/job/acme/director-of-engineering/123456">
  <h4 class="job-title">Director of Engineering</h4>
  <p class="job-location">Worldwide</p>
  <div class="job-desc">Fully remote distributed team across timezones...</div>
  <div><div><div title="Posted by Acme Studios"></div></div></div>
</a>
"""


_CARD_HTML_NO_LOGO_TITLE = """
<a href="/job/some-recruiter-name/lead-artist/555555">
  <h4 class="job-title">Lead Artist</h4>
  <p class="job-location">Berlin</p>
</a>
"""


def _payload(html: str = _CARD_HTML,
             href: str = "/job/bitmap-bureau-ltd/senior-programmer/336631"):
    return {
        "_href": href,
        "_url":  f"https://www.gamesjobsdirect.com{href}",
        "_html": html,
    }


def test_parse_canonical_card:
    s   = GamesJobsDirectScraper
    raw = s.parse(_payload)
    assert raw is not None
    assert raw.title    == "Senior Programmer"
    # "Posted by " prefix must be stripped.
    assert raw.company  == "Bitmap Bureau Ltd"
    assert raw.location == "Southampton"
    assert raw.native_id == "336631"
    # No remote signal in location or description → None.
    assert raw.remote is None
    assert raw.description is not None
    assert "Bitmap Bureau" in raw.description


def test_parse_remote_inferred_from_description:
    """The site has no structured remote field; we scan location + desc."""
    s   = GamesJobsDirectScraper
    raw = s.parse(_payload(html=_CARD_HTML_REMOTE_DESC,
                            href="/job/acme/director-of-engineering/123456"))
    assert raw is not None
    assert raw.company == "Acme Studios"
    # "Worldwide" in location triggers remote=True.
    assert raw.remote is True


def test_parse_falls_back_to_url_slug_when_no_logo_title:
    """If the logo div is missing/has no title attr, un-slugify the
    recruiter segment of the URL."""
    s   = GamesJobsDirectScraper
    raw = s.parse(_payload(html=_CARD_HTML_NO_LOGO_TITLE,
                            href="/job/some-recruiter-name/lead-artist/555555"))
    assert raw is not None
    # /job/some-recruiter-name/.../... → "Some Recruiter Name"
    assert raw.company == "Some Recruiter Name"
    assert raw.title   == "Lead Artist"


def test_parse_skips_when_no_title:
    no_title = """
    <a href="/job/x/y/1"><div title="Posted by Acme"></div></a>
    """
    s = GamesJobsDirectScraper
    assert s.parse(_payload(html=no_title, href="/job/x/y/1")) is None


def test_parse_skips_when_href_blank:
    s = GamesJobsDirectScraper
    assert s.parse({"_href": "", "_url": "", "_html": _CARD_HTML}) is None
