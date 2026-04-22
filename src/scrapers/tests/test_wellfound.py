"""Tests for src/scrapers/wellfound.py — parse against fixture HTML."""
from scrapers.wellfound import WellfoundScraper


_CARD = """
<div class="card">
  <h4>VP of Engineering</h4>
  <span>Remote, US</span>
</div>
"""

_CARD_NO_REMOTE = """
<div class="card">
  <h4>Director of Product</h4>
  <span>New York, NY</span>
</div>
"""


def _payload(html=_CARD, href="/company/acme-corp/jobs/12345-vp-of-engineering"):
    return {
        "_href": href,
        "_url":  f"https://wellfound.com{href}",
        "_html": html,
    }


def test_parse_canonical_card:
    s   = WellfoundScraper
    raw = s.parse(_payload)
    assert raw is not None
    assert raw.native_id == "12345"
    assert raw.title     == "VP of Engineering"
    assert raw.company   == "Acme Corp"     # title-cased from URL slug
    assert raw.location  == "Remote, US"
    assert raw.remote is True


def test_parse_non_remote_location:
    s   = WellfoundScraper
    raw = s.parse(_payload(html=_CARD_NO_REMOTE,
                            href="/company/example-publisher/jobs/67890-director"))
    assert raw is not None
    assert raw.company  == "Example Publisher"
    assert raw.location == "New York, NY"
    # No remote signal → None (unknown).
    assert raw.remote is None


def test_parse_skips_when_url_unparseable:
    """Non-WF job URL → no numeric id → skip."""
    s = WellfoundScraper
    assert s.parse({
        "_href": "/some/random/path",
        "_url":  "https://wellfound.com/some/random/path",
        "_html": _CARD,
    }) is None


def test_parse_skips_when_no_title:
    s = WellfoundScraper
    no_title = '<div><span>Remote</span></div>'
    assert s.parse(_payload(html=no_title,
                             href="/company/acme/jobs/99-test")) is None


def test_parse_jobs_only_url_works:
    """Some hrefs are /jobs/<id>-<slug> without /company/ prefix."""
    s   = WellfoundScraper
    raw = s.parse({
        "_href": "/jobs/55555-vp-engineering-startup",
        "_url":  "https://wellfound.com/jobs/55555-vp-engineering-startup",
        "_html": '<div><h4>VP Engineering</h4><span>Acme Inc</span><span>Remote</span></div>',
    })
    assert raw is not None
    assert raw.native_id == "55555"
    # Company comes from DOM (no /company/ in URL).
    assert raw.company == "Acme Inc"
