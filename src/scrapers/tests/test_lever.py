"""Tests for src/scrapers/lever.py — parse + normalize only.

Payload shapes verified against `api.lever.co/v0/postings/acme-corp?mode=json`
on 2026-04-17. Lever's v0 endpoint is frozen, so these shapes are stable.
"""
from scrapers.lever import LeverScraper


def _payload(**over):
    """Minimal valid Lever posting. Override fields as needed."""
    base = {
        "id": "5ac21346-8e0c-4494-8e7a-3eb92ff77902",
        "text": "Staff Software Engineer, Games Platform",
        "descriptionPlain": "Build the Acme Corp games backend.",
        "description": "<p>Build the Acme Corp games backend.</p>",  # HTML fallback
        "categories": {
            "team":       "Engineering",
            "location":   "Los Gatos, CA",
            "commitment": "Full-time",
        },
        "workplaceType": "hybrid",
        "hostedUrl":     "https://jobs.lever.co/acme-corp/5ac21346",
        "createdAt":     1712300000000,        # epoch ms — 2024-04-05
        "_company_meta": {
            "name":     "Acme Corp",
            "ats_slug": "acme-corp",
            "tier":     "S",
            "industry": "streaming_media",
        },
    }
    base.update(over)
    return base


def test_parse_full_payload:
    s   = LeverScraper
    raw = s.parse(_payload)
    assert raw is not None
    # Slug-prefixed native_id isolates Lever IDs from Greenhouse/Ashby IDs.
    assert raw.native_id == "acme-corp:5ac21346-8e0c-4494-8e7a-3eb92ff77902"
    assert raw.title     == "Staff Software Engineer, Games Platform"
    assert raw.company   == "Acme Corp"
    assert raw.url       == "https://jobs.lever.co/acme-corp/5ac21346"
    assert raw.location  == "Los Gatos, CA"
    # parse prefers descriptionPlain — no HTML noise in keyword matching.
    assert raw.description == "Build the Acme Corp games backend."
    # createdAt is epoch ms; BaseScraper.normalize handles the conversion.
    assert raw.posted_at   == "1712300000000"
    # workplaceType=hybrid is neither explicitly onsite nor remote — None.
    assert raw.remote is None
    assert raw.raw["company_tier"] == "S"


def test_parse_remote_and_onsite_workplace_types:
    """workplaceType values Lever actually emits: 'remote', 'onsite', 'hybrid'."""
    s = LeverScraper
    assert s.parse(_payload(workplaceType="remote")).remote is True
    assert s.parse(_payload(workplaceType="onsite")).remote is False
    assert s.parse(_payload(workplaceType="hybrid")).remote is None
    # Older postings have no workplaceType at all:
    assert s.parse(_payload(workplaceType="")).remote is None


def test_parse_falls_back_to_html_description:
    """When descriptionPlain is missing (older postings), use HTML."""
    s   = LeverScraper
    raw = s.parse(_payload(descriptionPlain=""))
    # Still picks up the HTML fallback — we don't strip it here because
    # Lever postings with HTML are rare; the scoring engine tolerates markup.
    assert raw.description == "<p>Build the Acme Corp games backend.</p>"


def test_parse_missing_title_skips:
    s = LeverScraper
    assert s.parse(_payload(text="")) is None
    assert s.parse(_payload(text="   ")) is None


def test_parse_missing_id_skips:
    s = LeverScraper
    assert s.parse(_payload(id=None)) is None


def test_parse_missing_categories_still_works:
    """If `categories` is absent, location should be None, not raise."""
    s   = LeverScraper
    raw = s.parse(_payload(categories=None))
    assert raw is not None
    assert raw.location is None


def test_parse_missing_createdAt_posted_at_none:
    s   = LeverScraper
    raw = s.parse(_payload(createdAt=None))
    assert raw.posted_at is None


def test_normalize_injects_company_tier:
    s   = LeverScraper
    raw = s.parse(_payload)
    row = s.normalize(raw)
    assert row["company_tier"] == "S"


def test_normalize_omits_company_tier_when_absent:
    s   = LeverScraper
    raw = s.parse(_payload(_company_meta={"name": "Acme", "ats_slug": "acme"}))
    row = s.normalize(raw)
    assert "company_tier" not in row
