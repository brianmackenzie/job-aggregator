"""Unit tests for src/common/db.py — moto-backed."""
from common import db


# Helper to keep tests terse.
def _make_job(job_id: str, score: int = 0, **overrides) -> dict:
    base = {
        "job_id": job_id,
        "title": f"Job {job_id}",
        "company": "Acme",
        "company_normalized": "acme",
        "source": job_id.split(":")[0],
        "native_id": job_id.split(":")[1],
        "url": f"https://example.com/{job_id}",
        "posted_at": "2026-04-16T12:00:00Z",
        "scraped_at": "2026-04-16T13:00:00Z",
        "status": "active",
        "track": "unscored",
        "score": score,
        "score_posted": f"{score:04d}#2026-04-16T12:00:00Z",
    }
    base.update(overrides)
    return base


# ----- put_job / get_job ---------------------------------------------------

def test_put_and_get_job(aws):
    job = _make_job("remoteok:1")
    existed = db.put_job(job)
    assert existed is False

    got = db.get_job("remoteok:1")
    assert got is not None
    assert got["title"] == "Job remoteok:1"
    # Decimal must come back as int (the _decode contract).
    assert isinstance(got["score"], int)
    assert got["score"] == 0


def test_get_job_missing(aws):
    assert db.get_job("nope:1") is None


def test_put_job_returns_existed_on_update(aws):
    db.put_job(_make_job("remoteok:1"))
    existed = db.put_job(_make_job("remoteok:1", score=50))
    assert existed is True


def test_put_job_preserves_user_state(aws):
    """Re-scrape must NOT clobber user-modified fields like status / notes."""
    db.put_job(_make_job("remoteok:2", title="Old Title"))

    # Simulate the user marking the job as "applied" with notes.
    current = db.get_job("remoteok:2")
    current["status"] = "applied"
    current["user_notes"] = "emailed alice@acme"
    db.jobs_table.put_item(Item=current)

    # Re-scrape brings a fresh title — but status/notes stay put.
    db.put_job(_make_job("remoteok:2", title="New Title"))

    final = db.get_job("remoteok:2")
    assert final["title"] == "New Title"
    assert final["status"] == "applied"
    assert final["user_notes"] == "emailed alice@acme"


# ----- query_jobs_by_score -------------------------------------------------

def test_query_jobs_by_score_descending(aws):
    for i, score in enumerate([50, 90, 70]):
        db.put_job(_make_job(f"remoteok:{i}", score=score))

    items, cursor = db.query_jobs_by_score(status="active", limit=10)
    assert [item["score"] for item in items] == [90, 70, 50]
    assert cursor is None


def test_query_jobs_by_score_pagination(aws):
    for i in range(5):
        db.put_job(_make_job(f"remoteok:{i}", score=i * 10))

    page1, cursor = db.query_jobs_by_score(status="active", limit=2)
    assert len(page1) == 2
    assert cursor is not None

    page2, _ = db.query_jobs_by_score(status="active", limit=2, cursor=cursor)
    assert len(page2) == 2

    # No overlap between pages.
    page1_ids = {j["job_id"] for j in page1}
    page2_ids = {j["job_id"] for j in page2}
    assert page1_ids.isdisjoint(page2_ids)


def test_query_jobs_by_score_filters_status(aws):
    db.put_job(_make_job("remoteok:active", score=80))
    archived = _make_job("remoteok:archived", score=90)
    archived["status"] = "archived"
    db.put_job(archived)

    items, _ = db.query_jobs_by_score(status="active")
    assert len(items) == 1
    assert items[0]["job_id"] == "remoteok:active"


# ----- query_jobs_by_company -----------------------------------------------

def test_query_jobs_by_company(aws):
    db.put_job(_make_job("remoteok:1"))
    db.put_job(_make_job("remoteok:2"))
    other = _make_job("remoteok:3")
    other["company_normalized"] = "other"
    db.put_job(other)

    items = db.query_jobs_by_company("acme")
    assert len(items) == 2


# ----- iter_active_jobs ----------------------------------------------------

def test_iter_active_jobs(aws):
    for i in range(3):
        db.put_job(_make_job(f"remoteok:{i}", score=i * 10))

    seen = list(db.iter_active_jobs(batch_size=2))
    assert len(seen) == 3


# ----- ScrapeRuns ----------------------------------------------------------

def test_scrape_runs_roundtrip(aws):
    db.put_scrape_run({
        "source_name": "remoteok",
        "run_timestamp": "2026-04-16T12:00:00Z",
        "status": "ok",
        "jobs_found": 42,
        "jobs_new": 10,
        "jobs_updated": 32,
        "duration_ms": 1500,
        "expires_at": 1776427200,
    })
    db.put_scrape_run({
        "source_name": "remoteok",
        "run_timestamp": "2026-04-17T12:00:00Z",
        "status": "ok",
        "jobs_found": 50,
        "jobs_new": 8,
        "jobs_updated": 42,
        "duration_ms": 1800,
        "expires_at": 1776513600,
    })

    runs = db.get_recent_scrape_runs("remoteok", limit=10)
    assert len(runs) == 2
    # Most recent first (descending sort key).
    assert runs[0]["run_timestamp"] == "2026-04-17T12:00:00Z"
    assert runs[0]["jobs_found"] == 50


# ----- UserPrefs -----------------------------------------------------------

def test_prefs_roundtrip(aws):
    db.put_pref("owner", "score_weights", {"gaming": 1.2})
    db.put_pref("owner", "hidden_companies", ["bad-co"])

    prefs = db.get_prefs("owner")
    # Float roundtrip survives Decimal coercion in both directions.
    assert prefs["score_weights"] == {"gaming": 1.2}
    assert prefs["hidden_companies"] == ["bad-co"]


def test_prefs_empty_user(aws):
    assert db.get_prefs("nobody") == {}


# ----- Companies -----------------------------------------------------------

def test_upsert_and_query_company(aws):
    db.upsert_company({
        "company_name_normalized": "example",
        "company_name": "Example",
        "tier": "S",
        "ats_type": "greenhouse",
        "ats_slug": "example",
        "active": True,
    })
    got = db.get_company("example")
    assert got["tier"] == "S"
    assert got["active"] is True

    by_tier = db.list_companies_by_tier("S")
    assert len(by_tier) == 1
    assert by_tier[0]["company_name"] == "Example"
