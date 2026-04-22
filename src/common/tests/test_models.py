"""Unit tests for the dataclasses in src/common/models.py."""
from common.models import Company, Job, ScrapeRun


def test_job_to_dict_drops_none:
    j = Job(
        job_id="x:1", title="t", company="c", company_normalized="c",
        source="x", native_id="1", url="u",
        posted_at="2026-04-16T12:00:00Z",
        scraped_at="2026-04-16T13:00:00Z",
    )
    d = j.to_dict
    # Optional fields default to None and get dropped.
    assert "location" not in d
    assert "description" not in d
    assert "user_notes" not in d
    # Defaults are present.
    assert d["job_id"] == "x:1"
    assert d["status"] == "active"
    assert d["track"] == "unscored"
    assert d["score"] == 0


def test_company_defaults:
    c = Company(
        company_name_normalized="example",
        company_name="Example",
        tier="S",
    )
    assert c.active is True
    d = c.to_dict
    assert d["active"] is True
    # Optional ATS fields are absent until populated.
    assert "ats_slug" not in d


def test_scrape_run_construction:
    r = ScrapeRun(
        source_name="remoteok",
        run_timestamp="2026-04-16T12:00:00Z",
        status="ok",
        jobs_found=10,
    )
    d = r.to_dict
    assert d["jobs_found"] == 10
    assert "error_message" not in d
