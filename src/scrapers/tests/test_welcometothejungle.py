"""Tests for src/scrapers/welcometothejungle.py — parse against fixture HTML."""
from scrapers.welcometothejungle import WelcomeToTheJungleScraper


_CARD = """
<div class="card">
  <h4>Senior Backend Engineer</h4>
  <span>Acme Corp</span>
  <span>Paris, FR</span>
</div>
"""

_CARD_REMOTE = """
<div class="card">
  <h4>Director of Engineering</h4>
  <span>Example Studio</span>
  <span>Remote, EU</span>
</div>
"""

_CARD_NO_DOM_COMPANY = """
<div class="card">
  <h4>VP, Product</h4>
</div>
"""


def _payload(html=_CARD, href="/en/companies/acme-corp/jobs/senior-backend-engineer"):
    return {
        "_href": href,
        "_url":  f"https://www.welcometothejungle.com{href}",
        "_html": html,
    }


def test_parse_canonical_card:
    s   = WelcomeToTheJungleScraper
    raw = s.parse(_payload)
    assert raw is not None
    assert raw.title    == "Senior Backend Engineer"
    assert raw.company  == "Acme Corp"
    assert raw.location == "Paris, FR"
    # native_id is "<company>:<job>" so two roles at the same company
    # don't collide on the job slug alone.
    assert raw.native_id == "acme-corp:senior-backend-engineer"


def test_parse_remote_inferred:
    s   = WelcomeToTheJungleScraper
    raw = s.parse(_payload(html=_CARD_REMOTE,
                            href="/en/companies/example-studio/jobs/director-of-eng"))
    assert raw is not None
    assert raw.remote is True
    assert raw.location == "Remote, EU"


def test_parse_falls_back_to_url_slug_for_company:
    """Card with no company text → derive prettified company from URL slug."""
    s   = WelcomeToTheJungleScraper
    raw = s.parse(_payload(html=_CARD_NO_DOM_COMPANY,
                            href="/en/companies/example-publisher/jobs/vp-product"))
    assert raw is not None
    assert raw.company == "Example Publisher"
    assert raw.title   == "VP, Product"


def test_parse_skips_when_no_title:
    s = WelcomeToTheJungleScraper
    no_title = '<div><span>Acme</span></div>'
    assert s.parse(_payload(html=no_title,
                             href="/en/companies/acme/jobs/x")) is None


def test_parse_skips_when_url_unparseable:
    """Non-WTTJ URL shape → no slug → no company → skip."""
    s = WelcomeToTheJungleScraper
    assert s.parse(_payload(html=_CARD_NO_DOM_COMPANY,
                             href="/some/random/path")) is None
