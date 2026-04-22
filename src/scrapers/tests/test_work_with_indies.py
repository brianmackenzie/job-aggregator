"""Tests for src/scrapers/work_with_indies.py — parse against fixture HTML.

No HTTP. fetch is exercised by the live smoke test.
"""
from scrapers.work_with_indies import WorkWithIndiesScraper


# Live-shape fixture: the 2026-04 workwithindies.com Webflow card. The
# anchor's outer HTML contains an <img class="company-logo" alt="<Company>">,
# a <div class="text-block-28"> with the role, and TWO <div class="job-card-text bold">
# divs — the first is the company, the last is the location.
_CARD_HTML = """
<a class="job-card w-inline-block" data-w-id="..."
   href="/careers/yoyo-studios-executive-producer-head-of-product">
  <img alt="YoYo Studios" class="company-logo" loading="lazy"
       src="https://cdn.prod.website-files.com/.../yoyo-logo.webp"/>
  <div class="job-link-desktop">
    <div class="job-card-text bold">YoYo Studios</div>
    <div class="job-card-text"> is hiring a </div>
    <div class="text-block-28">Executive Producer / Head of Product</div>
    <div class="job-card-text">to work from</div>
    <div class="job-card-text bold">Anywhere</div>
  </div>
  <div class="job-link-mobile">
    <div class="text-block-14">Executive Producer / Head of Product</div>
    <div class="job-card-text-smol">YoYo Studios</div>
    <div class="job-card-text-smol"> | </div>
    <div class="job-card-text-smol">Anywhere</div>
  </div>
</a>
"""


_CARD_HTML_US = """
<a class="job-card w-inline-block"
   href="/careers/armor-games-studios-junior-marketing-associate">
  <img alt="Armor Games Studios" class="company-logo"/>
  <div class="job-link-desktop">
    <div class="job-card-text bold">Armor Games Studios</div>
    <div class="job-card-text"> is hiring a </div>
    <div class="text-block-28">Junior Marketing Associate</div>
    <div class="job-card-text">to work from the</div>
    <div class="job-card-text bold">United States</div>
  </div>
</a>
"""


def _payload(html: str = _CARD_HTML,
             href: str = "/careers/yoyo-studios-executive-producer-head-of-product"):
    return {
        "_href": href,
        "_url":  f"https://workwithindies.com{href}",
        "_html": html,
    }


def test_parse_canonical_card:
    s   = WorkWithIndiesScraper
    raw = s.parse(_payload)
    assert raw is not None
    assert raw.title    == "Executive Producer / Head of Product"
    assert raw.company  == "YoYo Studios"
    assert raw.location == "Anywhere"
    # "Anywhere" is a remote signal in the indie scene.
    assert raw.remote is True
    assert raw.native_id == "yoyo-studios-executive-producer-head-of-product"
    assert raw.url == ("https://workwithindies.com/careers/"
                       "yoyo-studios-executive-producer-head-of-product")


def test_parse_country_location_not_remote:
    """A bare country name without 'remote'/'anywhere' should NOT
    trigger the remote flag — it might be onsite-in-country."""
    s   = WorkWithIndiesScraper
    raw = s.parse(_payload(html=_CARD_HTML_US,
                            href="/careers/armor-games-studios-junior-marketing-associate"))
    assert raw is not None
    assert raw.title    == "Junior Marketing Associate"
    assert raw.company  == "Armor Games Studios"
    assert raw.location == "United States"
    assert raw.remote is None


def test_parse_falls_back_to_first_bold_when_no_logo:
    """If the company logo img is missing, the first .bold text wins."""
    no_logo = """
    <a class="job-card" href="/careers/x">
      <div class="job-link-desktop">
        <div class="job-card-text bold">Acme Studios</div>
        <div class="text-block-28">Lead Designer</div>
        <div class="job-card-text bold">Berlin</div>
      </div>
    </a>
    """
    s   = WorkWithIndiesScraper
    raw = s.parse(_payload(html=no_logo, href="/careers/x"))
    assert raw is not None
    assert raw.company == "Acme Studios"
    assert raw.title   == "Lead Designer"
    assert raw.location == "Berlin"


def test_parse_skips_when_no_title:
    s = WorkWithIndiesScraper
    no_title = """
    <a class="job-card" href="/careers/x">
      <img alt="Acme" class="company-logo"/>
      <div class="job-card-text bold">Acme</div>
    </a>
    """
    assert s.parse(_payload(html=no_title, href="/careers/x")) is None


def test_parse_skips_when_no_company:
    s = WorkWithIndiesScraper
    no_company = """
    <a class="job-card" href="/careers/x">
      <div class="text-block-28">Lead Designer</div>
    </a>
    """
    assert s.parse(_payload(html=no_company, href="/careers/x")) is None


def test_parse_skips_when_href_blank:
    s = WorkWithIndiesScraper
    assert s.parse({"_href": "", "_url": "", "_html": _CARD_HTML}) is None
