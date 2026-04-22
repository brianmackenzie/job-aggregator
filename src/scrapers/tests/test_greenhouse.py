"""Tests for src/scrapers/greenhouse.py — parse + normalize only.

No HTTP is made; we synthesize payloads in the shape Greenhouse actually
returns (verified against `boards-api.greenhouse.io/v1/boards/acme-corp/jobs`
on 2026-04-17). fetch is exercised by a live scrape smoke test, not unit
tests — it would need boto3 + network.
"""
from scrapers.greenhouse import GreenhouseScraper, _strip_html


# ---------- _strip_html helper ---------------------------------------------

def test_strip_html_removes_tags_and_entities:
    # Realistic Greenhouse payload fragment — paragraph + bold + entity.
    html = "<p>Lead the <b>platform</b> org.&nbsp;Remote &amp; hybrid.</p>"
    assert _strip_html(html) == "Lead the platform org. Remote & hybrid."


def test_strip_html_collapses_whitespace:
    assert _strip_html("<p>  a\n\n\nb   </p>") == "a b"


def test_strip_html_empty_and_none:
    # parse passes an empty string when Greenhouse omits `content`.
    assert _strip_html("") == ""
    assert _strip_html(None) == ""


# ---------- parse --------------------------------------------------------

def _payload(**over):
    """Minimal valid Greenhouse job dict. Override fields as needed."""
    base = {
        "id": 127817,
        "title": "Senior Staff Engineer, Platform",
        "absolute_url": "https://boards.greenhouse.io/acmecorp/jobs/127817",
        "location": {"name": "Los Angeles, CA"},
        "content": "<p>Own the live-service infra.</p>",
        "updated_at": "2026-04-15T12:44:10Z",
        "_company_meta": {
            "name":     "Acme Corp",
            "ats_slug": "acme-corp",
            "tier":     "S",
            "industry": "gaming_publisher_platform",
        },
    }
    base.update(over)
    return base


def test_parse_full_payload:
    s = GreenhouseScraper
    raw = s.parse(_payload)
    assert raw is not None
    # native_id must be slug-prefixed so Acme's id 127817 can't collide
    # with some other Greenhouse company's id 127817.
    assert raw.native_id == "acme-corp:127817"
    assert raw.title     == "Senior Staff Engineer, Platform"
    assert raw.company   == "Acme Corp"
    assert raw.url       == "https://boards.greenhouse.io/acmecorp/jobs/127817"
    assert raw.location  == "Los Angeles, CA"
    # HTML stripped to plain text:
    assert raw.description == "Own the live-service infra."
    assert raw.posted_at   == "2026-04-15T12:44:10Z"
    # Greenhouse has no structured remote flag — parse returns None.
    assert raw.remote is None
    # Tier + industry ride along in `raw` for normalize to consume.
    assert raw.raw["company_tier"] == "S"
    assert raw.raw["industry"]     == "gaming_publisher_platform"


def test_parse_missing_title_skips:
    s = GreenhouseScraper
    # Empty title after strip should return None, not raise.
    assert s.parse(_payload(title="")) is None
    assert s.parse(_payload(title="   ")) is None


def test_parse_missing_id_skips:
    s = GreenhouseScraper
    assert s.parse(_payload(id=None)) is None


def test_parse_string_location:
    """Some Greenhouse tenants send location as a bare string, not a dict."""
    s = GreenhouseScraper
    raw = s.parse(_payload(location="Remote - US"))
    assert raw.location == "Remote - US"


def test_parse_missing_location_returns_none_loc:
    s = GreenhouseScraper
    raw = s.parse(_payload(location=None))
    assert raw.location is None


def test_parse_empty_content_yields_none_description:
    s = GreenhouseScraper
    raw = s.parse(_payload(content=""))
    assert raw.description is None


# ---------- normalize ----------------------------------------------------

def test_normalize_injects_company_tier:
    """normalize must copy company_tier from job.raw into the row so the
    scoring engine's modifier stack can read it at scoring time."""
    s   = GreenhouseScraper
    raw = s.parse(_payload)
    row = s.normalize(raw)
    assert row["company_tier"] == "S"


def test_normalize_omits_company_tier_when_absent:
    """If the companies.yaml row has no tier, normalize should not set one."""
    s   = GreenhouseScraper
    raw = s.parse(_payload(_company_meta={"name": "Acme", "ats_slug": "acme"}))
    row = s.normalize(raw)
    assert "company_tier" not in row


# ---------- fetch per-company fragility (regression test) ----------------
#
# 2026-04-19 — the per-company except handler used to re-raise, killing the
# entire weekly Greenhouse run if a single company hung past the 30s HTTP
# timeout. The Monday 04-19 run actually hit this: 0 ScrapeRuns rows landed
# until a manual retry. The fix is "log + continue" (matching the rule in
# CLAUDE.md "never hard-fail a scrape run").
#
# This test pins that behavior with a tiny mock setup — no network, no DDB,
# just patch requests.get and load_ats_companies, then iterate fetch.

def test_fetch_one_bad_company_does_not_kill_the_rest(monkeypatch):
    """If company A throws (network timeout, DNS, JSON decode), companies
    B and C must still produce yields. Pre-fix this raised RuntimeError."""
    from scrapers import greenhouse as gh
    import requests as _requests

    # 3 companies; the middle one will blow up.
    fake_companies = [
        {"name": "AlphaCo",   "ats_slug": "alphaco",   "tier": "1"},
        {"name": "BadCo",     "ats_slug": "badco",     "tier": "2"},
        {"name": "GammaCo",   "ats_slug": "gammaco",   "tier": "S"},
    ]
    monkeypatch.setattr(gh, "load_ats_companies", lambda ats: fake_companies)

    # Stub responses keyed by slug — alpha/gamma OK, badco raises.
    class _FakeResp:
        def __init__(self, slug):
            self.status_code = 200
            self._slug = slug
        def raise_for_status(self): pass
        def json(self):
            return {"jobs": [{"id": 1, "title": f"Role at {self._slug}"}]}

    def fake_get(url, **kw):
        if "/badco/" in url:
            raise _requests.exceptions.ReadTimeout("simulated hang")
        slug = url.rstrip("/").split("/")[-2]
        return _FakeResp(slug)

    monkeypatch.setattr(gh.requests, "get", fake_get)

    scraper = gh.GreenhouseScraper
    # Drain the generator — must NOT raise.
    yielded = list(scraper.fetch)

    # AlphaCo + GammaCo each yielded one job. BadCo yielded nothing.
    assert len(yielded) == 2
    titles = {j["title"] for j in yielded}
    assert "Role at alphaco" in titles
    assert "Role at gammaco" in titles
    assert "Role at badco" not in titles
