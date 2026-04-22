"""Tests for src/scrapers/workday.py — parse + _parse_posted_on.

Payload shapes reflect the public Workday cxs API as observed across
multiple enterprise tenants. The shape is consistent
because Workday hosts the front-end for all of them.
"""
from datetime import datetime, timezone, timedelta

from scrapers.workday import WorkdayScraper, _parse_posted_on


def _payload(**over):
    """Minimal valid Workday jobPostings entry. Override fields as needed."""
    base = {
        "title":         "Vice President, Engineering",
        "externalPath":  "/job/Redwood-City/Vice-President-Engineering_R12345",
        "locationsText": "Redwood City, CA",
        "postedOn":      "Posted Yesterday",
        "bulletFields":  ["R12345"],
        "_company_meta": {
            "name":     "Acme Corp",
            "tier":     "1",
            "industry": "gaming_publisher_platform",
        },
        "_workday": {
            "base_url": "https://acme.wd1.myworkdayjobs.com",
            "tenant":   "acme",
            "site":     "AcmeCareers",
        },
    }
    base.update(over)
    return base


# ---------- _parse_posted_on ---------------------------------------------------

def test_parse_posted_on_today_and_yesterday:
    today_iso = _parse_posted_on("Posted Today")
    assert today_iso is not None
    today_dt = datetime.strptime(today_iso, "%Y-%m-%dT%H:%M:%SZ")
    # "Today" should be within the last day.
    assert (datetime.now(timezone.utc).replace(tzinfo=None) - today_dt) < timedelta(days=1)

    y_iso = _parse_posted_on("Posted Yesterday")
    assert y_iso is not None
    y_dt = datetime.strptime(y_iso, "%Y-%m-%dT%H:%M:%SZ")
    # "Yesterday" should be roughly 1 day ago (allow a 5-min slop for exec time).
    delta = datetime.now(timezone.utc).replace(tzinfo=None) - y_dt
    assert timedelta(hours=23, minutes=55) <= delta <= timedelta(hours=24, minutes=5)


def test_parse_posted_on_n_days_ago:
    iso = _parse_posted_on("Posted 5 Days Ago")
    assert iso is not None
    dt = datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ")
    assert timedelta(days=4, hours=23) <= (datetime.now(timezone.utc).replace(tzinfo=None) - dt) <= timedelta(days=5, hours=1)


def test_parse_posted_on_30_plus_days:
    """Workday emits "Posted 30+ Days Ago" — the plus sign must not break parsing."""
    iso = _parse_posted_on("Posted 30+ Days Ago")
    assert iso is not None
    dt = datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ")
    # 30+ should be parsed as ~30 days ago, not 0 or NaN.
    assert timedelta(days=29, hours=23) <= (datetime.now(timezone.utc).replace(tzinfo=None) - dt) <= timedelta(days=30, hours=1)


def test_parse_posted_on_unknown_returns_none:
    """Unrecognised string → None so canonicalize_posted_at falls back to now."""
    assert _parse_posted_on("") is None
    assert _parse_posted_on(None) is None
    assert _parse_posted_on("Some weird workday string") is None


# ---------- parse ------------------------------------------------------------

def test_parse_full_payload:
    s   = WorkdayScraper
    raw = s.parse(_payload)
    assert raw is not None
    # Tenant-prefixed native_id ensures isolation between tenants.
    assert raw.native_id == "acme:R12345"
    assert raw.title     == "Vice President, Engineering"
    assert raw.company   == "Acme Corp"
    assert raw.location  == "Redwood City, CA"
    # URL = base + site + externalPath
    assert raw.url == (
        "https://acme.wd1.myworkdayjobs.com/AcmeCareers"
        "/job/Redwood-City/Vice-President-Engineering_R12345"
    )
    # Workday list response has no description field — parse leaves it None.
    assert raw.description is None
    # No structured remote flag on Workday postings.
    assert raw.remote is None
    # Tier is propagated through .raw for the normalize injection.
    assert raw.raw["company_tier"] == "1"


def test_parse_falls_back_to_external_path_segment_when_no_bullet_id:
    """If bulletFields is empty, native_id should use the externalPath tail."""
    s   = WorkdayScraper
    raw = s.parse(_payload(bulletFields=))
    assert raw is not None
    assert raw.native_id == "acme:Vice-President-Engineering_R12345"


def test_parse_falls_back_when_bullet_is_not_a_string:
    """Defensive: some tenants emit non-string bulletFields. Must not crash."""
    s   = WorkdayScraper
    raw = s.parse(_payload(bulletFields=[12345]))
    assert raw is not None
    # Falls back to externalPath tail because the bullet wasn't usable.
    assert raw.native_id == "acme:Vice-President-Engineering_R12345"


def test_parse_skips_when_title_missing:
    s = WorkdayScraper
    assert s.parse(_payload(title="")) is None
    assert s.parse(_payload(title="   ")) is None


def test_parse_skips_when_external_path_missing:
    """No externalPath means no URL — drop the row rather than store a broken link."""
    s = WorkdayScraper
    assert s.parse(_payload(externalPath="")) is None


def test_parse_handles_missing_location:
    s   = WorkdayScraper
    raw = s.parse(_payload(locationsText=""))
    assert raw is not None
    assert raw.location is None


def test_parse_handles_missing_posted_on:
    """If postedOn is absent or unrecognised, posted_at falls through to None
    and BaseScraper.normalize will use now as the timestamp."""
    s   = WorkdayScraper
    raw = s.parse(_payload(postedOn=""))
    assert raw is not None
    assert raw.posted_at is None


# ---------- normalize --------------------------------------------------------

def test_normalize_injects_company_tier:
    s   = WorkdayScraper
    raw = s.parse(_payload)
    row = s.normalize(raw)
    assert row["company_tier"] == "1"


def test_normalize_omits_tier_when_meta_missing:
    """Defensive: a misconfigured company entry should not break the row."""
    s   = WorkdayScraper
    raw = s.parse(_payload(_company_meta={"name": "Acme"}))
    row = s.normalize(raw)
    assert "company_tier" not in row
