"""Tests for src/scrapers/ashby.py — parse + normalize only.

Payload shapes re-verified against `api.ashbyhq.com/posting-api/job-board/openai`
on 2026-04-19. The live API top-level wrapper is `{"jobs": [...], "apiVersion": ...}`
(NOT "jobPostings" — older Ashby docs were wrong) and individual posting field
is `location` (NOT "locationName"). The scraper accepts both spellings
defensively; these tests pin the live shape and the legacy fallback both pass.
"""
from scrapers.ashby import AshbyScraper


def _payload(**over):
    """Minimal valid Ashby job (live API shape). Override fields as needed."""
    base = {
        "id":               "posting-abc-123",
        "title":            "VP, Platform Engineering",
        "location":         "Remote - United States",
        "isRemote":         True,
        "descriptionPlain": "Scale the multiplayer backend to 50M CCU.",
        "applyUrl":         "https://jobs.ashbyhq.com/somegame/posting-abc-123/apply",
        "publishedAt":      "2026-04-10T14:00:00Z",
        "_company_meta": {
            "name":     "Some Game Co",
            "ats_slug": "somegame",
            "tier":     "1",
            "industry": "gaming_b2b_infrastructure",
        },
    }
    base.update(over)
    return base


def test_parse_full_payload:
    s   = AshbyScraper
    raw = s.parse(_payload)
    assert raw is not None
    assert raw.native_id   == "somegame:posting-abc-123"
    assert raw.title       == "VP, Platform Engineering"
    assert raw.company     == "Some Game Co"
    # applyUrl (not jobUrl) is the canonical link.
    assert raw.url         == "https://jobs.ashbyhq.com/somegame/posting-abc-123/apply"
    assert raw.location    == "Remote - United States"
    assert raw.description == "Scale the multiplayer backend to 50M CCU."
    assert raw.posted_at   == "2026-04-10T14:00:00Z"
    assert raw.remote      is True
    assert raw.raw["company_tier"] == "1"


def test_parse_is_remote_false:
    s = AshbyScraper
    assert s.parse(_payload(isRemote=False)).remote is False


def test_parse_is_remote_missing:
    """Ashby sometimes omits isRemote — parse must return None, not False."""
    s = AshbyScraper
    assert s.parse(_payload(isRemote=None)).remote is None


def test_parse_missing_title_skips:
    s = AshbyScraper
    assert s.parse(_payload(title="")) is None
    assert s.parse(_payload(title="   ")) is None


def test_parse_missing_id_skips:
    s = AshbyScraper
    assert s.parse(_payload(id=None)) is None


def test_parse_missing_published_at:
    """publishedAt is sometimes None on draft postings; normalize later."""
    s   = AshbyScraper
    raw = s.parse(_payload(publishedAt=None))
    assert raw.posted_at is None


def test_parse_missing_description:
    s   = AshbyScraper
    raw = s.parse(_payload(descriptionPlain=""))
    assert raw.description is None


def test_normalize_injects_company_tier:
    s   = AshbyScraper
    raw = s.parse(_payload)
    row = s.normalize(raw)
    assert row["company_tier"] == "1"


def test_normalize_omits_company_tier_when_absent:
    s   = AshbyScraper
    raw = s.parse(_payload(_company_meta={"name": "Acme", "ats_slug": "acme"}))
    row = s.normalize(raw)
    assert "company_tier" not in row


def test_parse_legacy_locationName_field:
    """Regression: older Ashby boards may still send `locationName`. The
    scraper must accept either spelling so a single deprecated tenant
    doesn't lose its location string. (Live API uses `location`.)"""
    s = AshbyScraper
    p = _payload
    # Strip the live key, inject the legacy key.
    p.pop("location", None)
    p["locationName"] = "London, UK"
    raw = s.parse(p)
    assert raw.location == "London, UK"
