"""Unit tests for src/scrapers/base.py.

These exercise the *contract* of BaseScraper.scrape_run — the property
that one bad item never breaks a run, that ScrapeRuns rows are always
written, and that raw payloads are archived to S3. Using a FakeScraper
keeps the tests fast and free of network IO.
"""
from typing import Iterable, Optional

import boto3
import pytest

from common import db
from scrapers.base import BaseScraper, RawJob


class FakeScraper(BaseScraper):
    """In-memory scraper for testing. Subclass behavior:
        - payloads with skip=True -> parse returns None
        - payloads with fail=True -> parse raises
        - everything else -> a normal RawJob
    """
    source_name = "fake"
    schedule = "rate(1 day)"
    rate_limit_rps = 0  # disable throttling in tests

    def __init__(self, payloads):
        super.__init__
        self._payloads = payloads

    def fetch(self) -> Iterable[dict]:
        yield from self._payloads

    def parse(self, payload: dict) -> Optional[RawJob]:
        if payload.get("skip"):
            return None
        if payload.get("fail"):
            raise RuntimeError("synthetic parse failure")
        return RawJob(
            native_id=str(payload["id"]),
            title=payload["title"],
            company=payload["company"],
            url=payload["url"],
            posted_at="2026-04-16T12:00:00Z",
        )


def _payload(i: int, **kwargs) -> dict:
    return {
        "id": i,
        "title": f"Job {i}",
        "company": "Acme",
        "url": f"https://example.com/{i}",
        **kwargs,
    }


# ----- happy path ----------------------------------------------------------

def test_scrape_run_writes_jobs(aws):
    summary = FakeScraper([_payload(1), _payload(2)]).scrape_run
    assert summary["status"] == "ok"
    assert summary["jobs_found"] == 2
    assert summary["jobs_new"] == 2
    assert summary["jobs_updated"] == 0
    assert db.get_job("fake:1") is not None
    assert db.get_job("fake:2") is not None


def test_scrape_run_counts_updates(aws):
    FakeScraper([_payload(1)]).scrape_run
    summary = FakeScraper([_payload(1)]).scrape_run
    assert summary["jobs_new"] == 0
    assert summary["jobs_updated"] == 1


# ----- error handling ------------------------------------------------------

def test_scrape_run_survives_bad_item(aws):
    payloads = [_payload(1), _payload(2, fail=True), _payload(3)]
    summary = FakeScraper(payloads).scrape_run
    # 1 and 3 made it; 2 failed.
    assert summary["status"] == "partial"
    assert summary["jobs_found"] == 3
    assert summary["jobs_new"] == 2
    assert "error_message" in summary
    assert "synthetic parse failure" in summary["error_message"]


def test_scrape_run_skips_when_parse_returns_none(aws):
    payloads = [_payload(1), _payload(2, skip=True), _payload(3)]
    summary = FakeScraper(payloads).scrape_run
    assert summary["status"] == "ok"  # skipping is not an error
    assert summary["jobs_found"] == 3
    assert summary["jobs_new"] == 2
    assert db.get_job("fake:2") is None


def test_scrape_run_records_fetch_failure(aws):
    """If fetch itself blows up, the run still writes a ScrapeRuns row."""
    class BrokenScraper(BaseScraper):
        source_name = "broken"
        schedule = "rate(1 day)"
        rate_limit_rps = 0

        def fetch(self):
            raise ConnectionError("site is down")

        def parse(self, payload):
            return None

    summary = BrokenScraper.scrape_run
    assert summary["status"] == "error"
    assert "site is down" in summary["error_message"]
    assert summary["jobs_found"] == 0


# ----- side effects --------------------------------------------------------

def test_scrape_run_writes_scrape_runs_row(aws):
    FakeScraper([_payload(1)]).scrape_run
    runs = db.get_recent_scrape_runs("fake")
    assert len(runs) == 1
    row = runs[0]
    assert row["status"] == "ok"
    assert row["jobs_found"] == 1
    # TTL set in the future.
    assert row["expires_at"] > 0


def test_scrape_run_archives_to_s3(aws):
    FakeScraper([_payload(1), _payload(2)]).scrape_run
    s3 = boto3.client("s3", region_name="us-east-1")
    keys = [
        o["Key"]
        for o in s3.list_objects_v2(Bucket="test-raw-scrape").get("Contents", )
    ]
    assert len(keys) == 1
    key = keys[0]
    assert key.startswith("raw/fake/")
    assert key.endswith(".jsonl.gz")


# ----- subclass requirements ----------------------------------------------

def test_subclass_must_set_source_name:
    class Bad(BaseScraper):
        # source_name intentionally not set
        schedule = "rate(1 day)"

        def fetch(self):
            return 

        def parse(self, payload):
            return None

    with pytest.raises(ValueError, match="source_name"):
        Bad
