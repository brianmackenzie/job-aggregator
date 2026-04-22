"""Tests for src/scrapers/outscal.py — parse against fixture HTML.

No HTTP. fetch is exercised by the live smoke test.
"""
from scrapers.outscal import OutscalScraper


# Live-shape fixture from a real 2026-04 listing payload (Mapbox card).
# Outscal uses Next.js + Tailwind/shadcn-ui. Note: the <img alt> contains
# the JOB TITLE, not the company — a trap for naive parsers.
_CARD_HTML = """
<a class="block" href="/job/engineering-manager-navigation-sdk-at-mapbox-in-minsk-belarus">
  <div class="border text-card-foreground">
    <div class="p-2">
      <div class="flex items-start gap-4">
        <img alt="Engineering Manager, Navigation SDK"
             class="object-contain rounded-md"
             src="/_next/image?url=mapbox.png" width="40" height="40"/>
        <div class="flex-1 min-w-0">
          <div class="flex items-start justify-between gap-2">
            <h3 class="font-semibold text-card-foreground text-lg leading-tight line-clamp-2">
              Engineering Manager, Navigation SDK
            </h3>
          </div>
          <p class="text-muted-foreground text-sm font-medium">Mapbox</p>
          <p class="text-muted-foreground text-sm mb-2 line-clamp-1">
            Minsk, Belarus (Hybrid)
          </p>
        </div>
      </div>
    </div>
  </div>
</a>
"""


_CARD_HTML_REMOTE = """
<a class="block" href="/job/staff-engineer-at-acme-remote">
  <div>
    <h3 class="font-semibold">Staff Engineer</h3>
    <p class="text-muted-foreground text-sm font-medium">Acme Studios</p>
    <p class="text-muted-foreground text-sm line-clamp-1">Remote, Worldwide</p>
  </div>
</a>
"""


def _payload(html: str = _CARD_HTML,
             href: str = "/job/engineering-manager-navigation-sdk-at-mapbox-in-minsk-belarus"):
    return {
        "_href": href,
        "_url":  f"https://outscal.com{href}",
        "_html": html,
    }


def test_parse_canonical_card:
    s   = OutscalScraper
    raw = s.parse(_payload)
    assert raw is not None
    assert raw.title    == "Engineering Manager, Navigation SDK"
    assert raw.company  == "Mapbox"
    assert raw.location == "Minsk, Belarus (Hybrid)"
    # "(Hybrid)" stays None per scraper convention.
    assert raw.remote is None
    assert raw.native_id == ("engineering-manager-navigation-sdk-at-mapbox-"
                             "in-minsk-belarus")
    assert raw.url == ("https://outscal.com/job/"
                       "engineering-manager-navigation-sdk-at-mapbox-"
                       "in-minsk-belarus")


def test_parse_remote_inferred_from_location_suffix:
    s   = OutscalScraper
    raw = s.parse(_payload(html=_CARD_HTML_REMOTE,
                            href="/job/staff-engineer-at-acme-remote"))
    assert raw is not None
    assert raw.title    == "Staff Engineer"
    assert raw.company  == "Acme Studios"
    assert raw.location == "Remote, Worldwide"
    assert raw.remote is True


def test_parse_does_not_use_img_alt_as_company:
    """REGRESSION: Outscal's <img alt> is the job title, NOT the company.
    If we ever swap to alt-text fallback for company, this test catches it."""
    no_company_p = """
    <a class="block" href="/job/x">
      <img alt="Lead Designer"/>
      <h3 class="font-semibold">Lead Designer</h3>
      <p class="text-sm">Some location</p>
    </a>
    """
    s = OutscalScraper
    # No <p class="font-medium"> for company → must skip rather than
    # accidentally use the img alt (which is "Lead Designer").
    assert s.parse(_payload(html=no_company_p, href="/job/x")) is None


def test_parse_skips_when_no_title:
    no_title = """
    <a class="block" href="/job/x">
      <p class="font-medium">Some Co</p>
    </a>
    """
    s = OutscalScraper
    assert s.parse(_payload(html=no_title, href="/job/x")) is None


def test_parse_skips_when_href_blank:
    s = OutscalScraper
    assert s.parse({"_href": "", "_url": "", "_html": _CARD_HTML}) is None
