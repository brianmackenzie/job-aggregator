"""Tests for src/scrapers/games_career.py — Schema.org microdata parser.

Fixtures match the live DOM from games-career.com as of 2026-04-18.
If the site moves away from itemprop microdata, these tests fail
loudly so we know to refresh selectors.
"""
from scrapers.games_career import GamesCareerScraper


_CARD_HTML = """
<div class="joblist_element_title">
  <h3 class="box_hl" itemprop="title">
    <a href="https://www.games-career.com/Joboffer/33669_AI-Video-Artist---Marketing_InnoGames-GmbH">AI Video Artist - Marketing</a>
  </h3>
  <time class="date" datetime="2026-04-02" itemprop="datePosted">04/02/2026</time>
  <div class="description">
    <table>
      <tr>
        <td>Company:</td>
        <td itemprop="hiringOrganization">
          <a><span itemprop="name">InnoGames GmbH</span></a>
        </td>
      </tr>
      <tr>
        <td>Place of work:</td>
        <td itemprop="jobLocation">
          <span itemprop="address">
            <span itemprop="addressLocality">Hamburg</span> /
            <span itemprop="addressCountry">Germany</span>
          </span>
        </td>
      </tr>
    </table>
  </div>
</div>
"""

_CARD_HTML_REMOTE = """
<div class="joblist_element_title">
  <h3 class="box_hl" itemprop="title">
    <a href="https://www.games-career.com/Joboffer/33810_VP-Engineering_Acme">VP, Engineering</a>
  </h3>
  <time class="date" datetime="2026-04-15" itemprop="datePosted">04/15/2026</time>
  <div class="description">
    <table>
      <tr><td itemprop="hiringOrganization"><span itemprop="name">Acme Studios</span></td></tr>
      <tr><td itemprop="jobLocation">
        <span itemprop="address">
          <span itemprop="addressLocality">Worldwide</span> /
          <span itemprop="addressCountry">Remote</span>
        </span>
      </td></tr>
    </table>
  </div>
</div>
"""


def _payload(html: str = _CARD_HTML, native_id: str = "33669"):
    return {
        "_native_id": native_id,
        "_href":      f"https://www.games-career.com/Joboffer/{native_id}_X_Y",
        "_url":       f"https://www.games-career.com/Joboffer/{native_id}_X_Y",
        "_html":      html,
    }


def test_parse_canonical_card:
    s   = GamesCareerScraper
    raw = s.parse(_payload)
    assert raw is not None
    assert raw.native_id == "33669"
    assert raw.title     == "AI Video Artist - Marketing"
    assert raw.company   == "InnoGames GmbH"
    assert raw.location  == "Hamburg, Germany"
    assert raw.posted_at == "2026-04-02T00:00:00Z"
    # Hamburg is onsite — remote stays None (we don't infer onsite negative).
    assert raw.remote is None


def test_parse_remote_inferred_from_country:
    s   = GamesCareerScraper
    raw = s.parse(_payload(html=_CARD_HTML_REMOTE, native_id="33810"))
    assert raw is not None
    assert raw.title    == "VP, Engineering"
    assert raw.company  == "Acme Studios"
    assert raw.remote is True


def test_parse_skips_when_no_company:
    s = GamesCareerScraper
    no_co = """
    <div class="joblist_element_title">
      <h3 itemprop="title"><a href="/Joboffer/1_x">Director</a></h3>
    </div>
    """
    assert s.parse(_payload(html=no_co, native_id="1")) is None


def test_parse_skips_when_no_title:
    s = GamesCareerScraper
    no_title = """
    <div class="joblist_element_title">
      <td itemprop="hiringOrganization"><span itemprop="name">Acme</span></td>
    </div>
    """
    assert s.parse(_payload(html=no_title, native_id="2")) is None


def test_parse_falls_back_to_country_only_when_no_city:
    s = GamesCareerScraper
    country_only = """
    <div class="joblist_element_title">
      <h3 itemprop="title"><a>Producer</a></h3>
      <td itemprop="hiringOrganization"><span itemprop="name">Acme</span></td>
      <td itemprop="jobLocation">
        <span itemprop="addressCountry">Germany</span>
      </td>
    </div>
    """
    raw = s.parse(_payload(html=country_only, native_id="3"))
    assert raw is not None
    assert raw.location == "Germany"


def test_parse_handles_missing_date:
    s = GamesCareerScraper
    no_date = """
    <div class="joblist_element_title">
      <h3 itemprop="title"><a>Designer</a></h3>
      <td itemprop="hiringOrganization"><span itemprop="name">Acme</span></td>
      <td itemprop="jobLocation">
        <span itemprop="addressLocality">Berlin</span>
      </td>
    </div>
    """
    raw = s.parse(_payload(html=no_date, native_id="4"))
    assert raw is not None
    assert raw.posted_at is None
