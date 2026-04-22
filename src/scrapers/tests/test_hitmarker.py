"""Tests for src/scrapers/hitmarker.py — parse against fixture HTML."""
from scrapers.hitmarker import HitmarkerScraper


_CARD_HTML = """
<div class="card">
  <img src="/logo.png" alt="Acme Corp">
  <h2>VP, Platform Engineering</h2>
  <p>Los Angeles, CA</p>
</div>
"""

_CARD_HTML_REMOTE = """
<div class="card">
  <img src="/logo.png" alt="Example Studio">
  <h2>Director of Engineering</h2>
  <p>Remote, Worldwide</p>
</div>
"""

_CARD_HTML_NO_LOGO = """
<div class="card">
  <h2>Game Director</h2>
  <p>Example Games</p>
  <p>Bellevue, WA</p>
</div>
"""

_CARD_HTML_NO_TITLE = """
<div class="card">
  <img alt="Acme">
  <p>London, UK</p>
</div>
"""


def _payload(html: str = _CARD_HTML, href: str = "/jobs/vp-platform-eng-acme"):
    return {
        "_href": href,
        "_url":  f"https://hitmarker.net{href}",
        "_html": html,
    }


def test_parse_uses_logo_alt_for_company:
    s   = HitmarkerScraper
    raw = s.parse(_payload)
    assert raw is not None
    assert raw.company   == "Acme Corp"
    assert raw.title     == "VP, Platform Engineering"
    assert raw.location  == "Los Angeles, CA"
    assert raw.native_id == "vp-platform-eng-acme"


def test_parse_remote_inferred:
    s   = HitmarkerScraper
    raw = s.parse(_payload(html=_CARD_HTML_REMOTE,
                            href="/jobs/director-of-eng-example"))
    assert raw is not None
    assert raw.company == "Example Studio"
    assert raw.remote is True


def test_parse_falls_back_when_no_logo:
    """Without a logo alt, the first short text node = company."""
    s   = HitmarkerScraper
    raw = s.parse(_payload(html=_CARD_HTML_NO_LOGO,
                            href="/jobs/game-director-example"))
    assert raw is not None
    assert raw.company  == "Example Games"
    assert raw.location == "Bellevue, WA"


def test_parse_skips_when_no_title:
    s = HitmarkerScraper
    assert s.parse(_payload(html=_CARD_HTML_NO_TITLE,
                             href="/jobs/no-title")) is None


def test_parse_skips_when_href_missing:
    s = HitmarkerScraper
    assert s.parse({"_href": "", "_url": "", "_html": _CARD_HTML}) is None


# Live-shape regression: this is exactly what Hitmarker's listing page
# returns as of 2026-04 — the title is in <div class="font-bold ..."> not
# in a heading tag, and the alt text is "<Company> logo" not "<Company>".
# If this test breaks, Hitmarker changed their template.
_LIVE_CARD_HTML = """
<a class="block bg-alpha-2 border border-alpha-4 p-4"
   href="https://hitmarker.net/jobs/example-tech-project-manager-1688679">
  <div class="font-bold truncate mb-2">Project Manager, Experience Marketing, Example Tech G (GCN)</div>
  <div class="flex items-center gap-x-1.5 mb-2">
    <img alt="Example Tech logo" class="size-4.5 rounded-full" src="/logo.webp"/>
    <span class="text-alpha-7 truncate">Example Tech</span>
  </div>
  <div class="flex items-center gap-x-1.5">
    <span class="size-4.5"></span>
    <span class="text-alpha-7 truncate">Shanghai, China</span>
  </div>
</a>
"""


def test_parse_live_hitmarker_card_shape:
    """Locked-in fixture from a real 2026-04 listing payload."""
    s = HitmarkerScraper
    raw = s.parse({
        "_href": "https://hitmarker.net/jobs/example-tech-project-manager-1688679",
        "_url":  "https://hitmarker.net/jobs/example-tech-project-manager-1688679",
        "_html": _LIVE_CARD_HTML,
    })
    assert raw is not None
    assert raw.title    == "Project Manager, Experience Marketing, Example Tech G (GCN)"
    # The " logo" suffix must be stripped from the alt text.
    assert raw.company  == "Example Tech"
    assert raw.location == "Shanghai, China"
    # Shanghai is not a remote signal → remote stays None.
    assert raw.remote is None
    assert raw.native_id == "example-tech-project-manager-1688679"
