"""Tests for src/lambdas/api_jobs.py."""
import json

from common import db
from lambdas.api_jobs import handler


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


def _event(route, path_params=None, body=None, qs=None):
    return {
        "routeKey": route,
        "pathParameters": path_params or {},
        "queryStringParameters": qs,
        "body": body,
    }


# ----- list ----------------------------------------------------------------

def test_list_jobs_empty(aws):
    resp = handler(_event("GET /api/jobs"), None)
    assert resp["statusCode"] == 200
    body = json.loads(resp["body"])
    assert body["jobs"] == 
    assert body["count"] == 0
    assert body["next_cursor"] is None


def test_list_jobs_sorted_by_score_desc(aws):
    for i, score in enumerate([50, 90, 70]):
        db.put_job(_make_job(f"remoteok:{i}", score=score))
    resp = handler(_event("GET /api/jobs"), None)
    body = json.loads(resp["body"])
    scores = [j["score"] for j in body["jobs"]]
    assert scores == [90, 70, 50]


def test_list_jobs_respects_limit(aws):
    for i in range(10):
        db.put_job(_make_job(f"remoteok:{i}", score=i * 5))
    resp = handler(_event("GET /api/jobs", qs={"limit": "3"}), None)
    body = json.loads(resp["body"])
    assert body["count"] == 3
    assert body["next_cursor"] is not None


def test_list_jobs_pagination_roundtrip(aws):
    for i in range(5):
        db.put_job(_make_job(f"remoteok:{i}", score=i * 10))
    page1 = json.loads(handler(_event("GET /api/jobs", qs={"limit": "2"}), None)["body"])
    assert page1["count"] == 2
    resp2 = handler(_event("GET /api/jobs", qs={"limit": "2", "cursor": page1["next_cursor"]}), None)
    page2 = json.loads(resp2["body"])
    ids1 = {j["job_id"] for j in page1["jobs"]}
    ids2 = {j["job_id"] for j in page2["jobs"]}
    assert ids1.isdisjoint(ids2)


# ----- get one -------------------------------------------------------------

def test_get_job(aws):
    db.put_job(_make_job("remoteok:1"))
    resp = handler(
        _event("GET /api/jobs/{job_id}", {"job_id": "remoteok:1"}),
        None,
    )
    assert resp["statusCode"] == 200
    body = json.loads(resp["body"])
    assert body["job"]["job_id"] == "remoteok:1"


def test_get_missing(aws):
    resp = handler(
        _event("GET /api/jobs/{job_id}", {"job_id": "nope:1"}),
        None,
    )
    assert resp["statusCode"] == 404


# ----- action --------------------------------------------------------------

def test_action_applied_sets_status_and_notes(aws):
    db.put_job(_make_job("remoteok:1"))
    resp = handler(
        _event(
            "POST /api/jobs/{job_id}/action",
            {"job_id": "remoteok:1"},
            body=json.dumps({"action": "applied", "notes": "talked to Alice"}),
        ),
        None,
    )
    assert resp["statusCode"] == 200
    stored = db.get_job("remoteok:1")
    assert stored["status"] == "applied"
    assert stored["user_notes"] == "talked to Alice"


def test_action_skip_archives(aws):
    db.put_job(_make_job("remoteok:2"))
    handler(
        _event(
            "POST /api/jobs/{job_id}/action",
            {"job_id": "remoteok:2"},
            body=json.dumps({"action": "skip"}),
        ),
        None,
    )
    assert db.get_job("remoteok:2")["status"] == "archived"


def test_action_invalid_rejected(aws):
    db.put_job(_make_job("remoteok:3"))
    resp = handler(
        _event(
            "POST /api/jobs/{job_id}/action",
            {"job_id": "remoteok:3"},
            body=json.dumps({"action": "explode"}),
        ),
        None,
    )
    assert resp["statusCode"] == 400


def test_action_missing_job(aws):
    resp = handler(
        _event(
            "POST /api/jobs/{job_id}/action",
            {"job_id": "nope:99"},
            body=json.dumps({"action": "save"}),
        ),
        None,
    )
    assert resp["statusCode"] == 404


# ----- routing -------------------------------------------------------------

def test_unknown_route(aws):
    resp = handler(_event("DELETE /api/jobs"), None)
    assert resp["statusCode"] == 404


# ===========================================================================
# R2: /api/jobs/browse + /api/taxonomy
# ===========================================================================

def _make_browse_job(
    job_id: str,
    score: int = 50,
    qol: int = 50,
    salary_min: int = 0,
    industries: list = None,
    role_types: list = None,
    company_group: str = None,
    work_mode: str = None,
    posted_at: str = "2026-04-15T12:00:00Z",
    status: str = "active",
) -> dict:
    """Build a Jobs row with the R2 fields populated."""
    base = _make_job(job_id, score=score, status=status)
    base["qol_score"] = qol
    base["salary_min"] = salary_min
    base["industries"] = industries or 
    base["role_types"] = role_types or 
    base["work_mode"] = work_mode or "unclear"
    base["posted_at"] = posted_at
    if company_group:
        base["company_group"] = company_group
    return base


def _seed_browse_corpus(aws):
    """Three diverse jobs: gaming VP, AI IC, igaming director."""
    db.put_job(_make_browse_job(
        "test:vp-roblox",
        score=88, qol=80, salary_min=300_000,
        industries=["gaming", "tech"],
        role_types=["engineering_leadership"],
        company_group="tier_s",
        work_mode="remote",
        posted_at="2026-04-17T00:00:00Z",
    ))
    db.put_job(_make_browse_job(
        "test:ic-anthropic",
        score=72, qol=70, salary_min=200_000,
        industries=["ai"],
        role_types=["software_engineering"],
        company_group="ai_labs",
        work_mode="hybrid",
        posted_at="2026-04-15T00:00:00Z",
    ))
    db.put_job(_make_browse_job(
        "test:dir-draftkings",
        score=60, qol=40, salary_min=180_000,
        industries=["igaming"],
        role_types=["operations"],
        company_group="sportsbooks",
        work_mode="onsite",
        posted_at="2026-04-10T00:00:00Z",
    ))


def test_browse_default_returns_all_active_sorted_by_score(aws):
    _seed_browse_corpus(aws)
    resp = handler(_event("GET /api/jobs/browse"), None)
    assert resp["statusCode"] == 200
    body = json.loads(resp["body"])
    assert body["total"] == 3
    scores = [j["score"] for j in body["jobs"]]
    assert scores == sorted(scores, reverse=True)


def test_browse_filter_by_industry(aws):
    _seed_browse_corpus(aws)
    resp = handler(
        _event("GET /api/jobs/browse", qs={"industries": "ai"}),
        None,
    )
    body = json.loads(resp["body"])
    assert body["total"] == 1
    assert body["jobs"][0]["job_id"] == "test:ic-anthropic"


def test_browse_filter_by_multiple_industries_or_within(aws):
    _seed_browse_corpus(aws)
    resp = handler(
        _event("GET /api/jobs/browse", qs={"industries": "ai,igaming"}),
        None,
    )
    body = json.loads(resp["body"])
    assert body["total"] == 2  # ai OR igaming


def test_browse_filter_by_role_type(aws):
    _seed_browse_corpus(aws)
    resp = handler(
        _event("GET /api/jobs/browse",
               qs={"role_types": "engineering_leadership"}),
        None,
    )
    body = json.loads(resp["body"])
    assert body["total"] == 1
    assert body["jobs"][0]["job_id"] == "test:vp-roblox"


def test_browse_filter_by_company_group(aws):
    _seed_browse_corpus(aws)
    resp = handler(
        _event("GET /api/jobs/browse", qs={"company_groups": "tier_s"}),
        None,
    )
    body = json.loads(resp["body"])
    assert body["total"] == 1


def test_browse_filter_by_work_mode_remote(aws):
    _seed_browse_corpus(aws)
    resp = handler(
        _event("GET /api/jobs/browse", qs={"work_modes": "remote"}),
        None,
    )
    body = json.loads(resp["body"])
    assert body["total"] == 1
    assert body["jobs"][0]["work_mode"] == "remote"


def test_browse_filter_combo_and_across_categories(aws):
    """industry=ai AND role_type=software_engineering → only the
    Anthropic IC matches (Roblox is gaming+tech, DraftKings is igaming)."""
    _seed_browse_corpus(aws)
    resp = handler(
        _event("GET /api/jobs/browse",
               qs={"industries": "gaming,ai",
                   "role_types": "software_engineering"}),
        None,
    )
    body = json.loads(resp["body"])
    assert body["total"] == 1
    assert body["jobs"][0]["job_id"] == "test:ic-anthropic"


def test_browse_min_score_filter(aws):
    _seed_browse_corpus(aws)
    resp = handler(
        _event("GET /api/jobs/browse", qs={"min_score": "70"}),
        None,
    )
    body = json.loads(resp["body"])
    assert body["total"] == 2  # 88 and 72


def test_browse_min_qol_filter(aws):
    _seed_browse_corpus(aws)
    resp = handler(
        _event("GET /api/jobs/browse", qs={"min_qol": "75"}),
        None,
    )
    body = json.loads(resp["body"])
    assert body["total"] == 1
    assert body["jobs"][0]["qol_score"] == 80


def test_browse_min_salary_filter(aws):
    _seed_browse_corpus(aws)
    resp = handler(
        _event("GET /api/jobs/browse", qs={"min_salary": "250000"}),
        None,
    )
    body = json.loads(resp["body"])
    assert body["total"] == 1
    assert body["jobs"][0]["salary_min"] == 300_000


def test_browse_sort_by_qol(aws):
    _seed_browse_corpus(aws)
    resp = handler(
        _event("GET /api/jobs/browse", qs={"sort_by": "qol"}),
        None,
    )
    body = json.loads(resp["body"])
    qols = [j["qol_score"] for j in body["jobs"]]
    assert qols == sorted(qols, reverse=True)


def test_browse_sort_by_comp(aws):
    _seed_browse_corpus(aws)
    resp = handler(
        _event("GET /api/jobs/browse", qs={"sort_by": "comp"}),
        None,
    )
    body = json.loads(resp["body"])
    sals = [j["salary_min"] for j in body["jobs"]]
    assert sals == sorted(sals, reverse=True)


def test_browse_sort_by_newest(aws):
    _seed_browse_corpus(aws)
    resp = handler(
        _event("GET /api/jobs/browse", qs={"sort_by": "newest"}),
        None,
    )
    body = json.loads(resp["body"])
    dates = [j["posted_at"] for j in body["jobs"]]
    assert dates == sorted(dates, reverse=True)


def test_browse_sort_by_oldest(aws):
    _seed_browse_corpus(aws)
    resp = handler(
        _event("GET /api/jobs/browse", qs={"sort_by": "oldest"}),
        None,
    )
    body = json.loads(resp["body"])
    dates = [j["posted_at"] for j in body["jobs"]]
    assert dates == sorted(dates)  # ascending


def test_browse_pagination_offset(aws):
    """Seed 5 rows, page through with limit=2."""
    for i in range(5):
        db.put_job(_make_browse_job(
            f"test:row-{i}", score=50 + i, qol=10,
            industries=["tech"],
        ))
    page1 = json.loads(handler(
        _event("GET /api/jobs/browse", qs={"limit": "2", "offset": "0"}), None
    )["body"])
    assert page1["count"] == 2
    assert page1["total"] == 5
    assert page1["has_more"] is True
    assert page1["next_offset"] == 2

    page2 = json.loads(handler(
        _event("GET /api/jobs/browse", qs={"limit": "2", "offset": "2"}), None
    )["body"])
    assert page2["count"] == 2
    assert page2["next_offset"] == 4

    page3 = json.loads(handler(
        _event("GET /api/jobs/browse", qs={"limit": "2", "offset": "4"}), None
    )["body"])
    assert page3["count"] == 1
    assert page3["has_more"] is False
    assert page3["next_offset"] is None


def test_browse_status_filter(aws):
    """status=saved should NOT include active rows."""
    db.put_job(_make_browse_job("test:active", score=80, status="active"))
    db.put_job(_make_browse_job("test:saved",  score=70, status="saved"))
    resp = handler(
        _event("GET /api/jobs/browse", qs={"status": "saved"}), None
    )
    body = json.loads(resp["body"])
    assert body["total"] == 1
    assert body["jobs"][0]["job_id"] == "test:saved"


def test_browse_query_echoed_in_response(aws):
    """The resolved query is echoed so the UI can render active filters."""
    _seed_browse_corpus(aws)
    resp = handler(
        _event("GET /api/jobs/browse",
               qs={"industries": "ai", "min_qol": "60", "sort_by": "qol"}),
        None,
    )
    body = json.loads(resp["body"])
    assert body["query"]["industries"] == ["ai"]
    assert body["query"]["min_qol"] == 60
    assert body["query"]["sort_by"] == "qol"


# ----- /api/taxonomy --------------------------------------------------------

def test_taxonomy_endpoint_returns_facet_lists(aws):
    resp = handler(_event("GET /api/taxonomy"), None)
    assert resp["statusCode"] == 200
    body = json.loads(resp["body"])
    for k in ("industries", "role_types", "company_groups",
              "work_modes", "sort_options", "statuses"):
        assert k in body, f"missing {k}"
        assert isinstance(body[k], list)
        assert len(body[k]) > 0
    # Each facet entry should be {value, label}.
    for entry in body["industries"]:
        assert "value" in entry and "label" in entry


def test_taxonomy_includes_known_industries(aws):
    body = json.loads(handler(_event("GET /api/taxonomy"), None)["body"])
    values = {e["value"] for e in body["industries"]}
    for expected in ("gaming", "ai", "igaming"):
        assert expected in values


# ===========================================================================
# server-side q= search, dedup, projection trim, warm cache.
# Each of these is a regression-pin against bugs that bit the original author:
#   - "Search 'FanDuel' doesn't find FanDuel" (was client-side, post-page-1)
#   - "Same role from greenhouse + apify shows up 4 times" (no dedup)
#   - "10K-row scan is slow on every refresh" (no cache, full payload)
# ===========================================================================

def _seed_dupe_corpus(aws):
    """Three rows for the same FanDuel role across different sources +
    one unrelated row for negative control. All three FanDuel rows MUST
    share company_normalized AND title for dedup grouping to fire."""
    # Same (company_normalized, title) — should collapse to one. Highest
    # score wins; the survivor reports dupe_count=3.
    common = {
        "company": "FanDuel",
        "company_normalized": "fanduel",
        "title": "Senior Director, AI Platforms & Ops",
    }
    db.put_job({
        **_make_browse_job(
            "greenhouse:fanduel:1",
            score=72, qol=20,
            industries=["igaming"],
            role_types=["engineering_leadership"],
            company_group="tier_1",
            work_mode="onsite",
            posted_at="2026-04-15T00:00:00Z",
        ),
        **common,
    })
    db.put_job({
        **_make_browse_job(
            "apify_linkedin:fd-1",
            score=67, qol=20,
            industries=["igaming"],
            role_types=["engineering_leadership"],
            company_group="tier_1",
            work_mode="onsite",
            posted_at="2026-04-12T00:00:00Z",
        ),
        **common,
    })
    db.put_job({
        **_make_browse_job(
            "apify_linkedin:fd-2",
            score=51, qol=20,
            industries=["igaming"],
            role_types=["engineering_leadership"],
            company_group="tier_1",
            work_mode="onsite",
            posted_at="2026-04-10T00:00:00Z",
        ),
        **common,
    })
    # Unrelated control row — shouldn't be touched by dedup.
    db.put_job(_make_browse_job(
        "test:control-row",
        score=40, qol=20,
        industries=["tech"],
        role_types=["software_engineering"],
        company_group="tier_2",
        work_mode="remote",
        posted_at="2026-04-14T00:00:00Z",
    ))


# ----- dedup --------------------------------------------------------------

def test_browse_dedup_collapses_same_role_across_sources(aws):
    """Same (company, title) across 3 sources → 1 row in the response,
    highest-scored wins, dupe_count=3, dupe_sources lists all three."""
    _seed_dupe_corpus(aws)
    body = json.loads(handler(_event("GET /api/jobs/browse"), None)["body"])
    # 3 dupes + 1 control = 4 raw rows → dedup to 2.
    assert body["raw_total"] == 4
    assert body["total"]     == 2
    # Find the FanDuel row that survived.
    fd = next(j for j in body["jobs"] if j.get("company_normalized") == "fanduel")
    assert fd["score"] == 72             # highest-score winner
    assert fd["dupe_count"] == 3
    assert set(fd["dupe_sources"]) == {"greenhouse", "apify_linkedin"}


def test_browse_dedup_off_returns_all_rows(aws):
    """With dedup=false the raw rows come through untouched."""
    _seed_dupe_corpus(aws)
    body = json.loads(handler(
        _event("GET /api/jobs/browse", qs={"dedup": "false"}), None
    )["body"])
    assert body["total"] == 4
    # No dupe_count attached on this code path.
    assert all("dupe_count" not in j for j in body["jobs"])


def test_browse_dedup_passthrough_for_rows_missing_keys(aws):
    """Rows without company_normalized OR title should pass through dedup
    untouched — we have no stable key to group them on. Simulated by
    seeding a row with title=' ' (whitespace), which our normalizer
    treats as empty."""
    db.put_job(_make_browse_job("test:keyed", score=50, industries=["tech"]))
    headless = _make_browse_job("test:no-title", score=40, industries=["tech"])
    headless["title"] = " "   # whitespace-only → normalizer returns ""
    db.put_job(headless)
    body = json.loads(handler(_event("GET /api/jobs/browse"), None)["body"])
    assert body["total"] == 2  # both survived dedup despite missing title


# ----- q= server-side search ----------------------------------------------

def test_browse_q_matches_title_substring(aws):
    """`q=ai platforms` should hit the FanDuel role even if it's past the
    visible page — the bug pre-was that q only looked at loaded
    rows in JS, not the whole DB."""
    _seed_dupe_corpus(aws)
    body = json.loads(handler(
        _event("GET /api/jobs/browse", qs={"q": "ai platforms"}), None
    )["body"])
    assert body["total"] >= 1   # dedup collapses to 1
    assert all("ai platforms" in j["title"].lower for j in body["jobs"])


def test_browse_q_matches_company_name(aws):
    _seed_dupe_corpus(aws)
    body = json.loads(handler(
        _event("GET /api/jobs/browse", qs={"q": "FanDuel"}), None
    )["body"])
    assert body["total"] >= 1
    assert any(j.get("company") == "FanDuel" for j in body["jobs"])


def test_browse_q_no_matches_returns_empty(aws):
    _seed_dupe_corpus(aws)
    body = json.loads(handler(
        _event("GET /api/jobs/browse", qs={"q": "nonexistent_zzz"}), None
    )["body"])
    assert body["total"] == 0
    assert body["jobs"]  == 


def test_browse_q_is_case_insensitive(aws):
    _seed_dupe_corpus(aws)
    lower = json.loads(handler(
        _event("GET /api/jobs/browse", qs={"q": "fanduel"}), None
    )["body"])
    upper = json.loads(handler(
        _event("GET /api/jobs/browse", qs={"q": "FANDUEL"}), None
    )["body"])
    # Cache returns identical state on the second call; result must match.
    assert lower["total"] == upper["total"]


def test_browse_q_combines_with_filters(aws):
    """q=fanduel AND industries=tech should return zero (FanDuel is igaming)."""
    _seed_dupe_corpus(aws)
    body = json.loads(handler(
        _event("GET /api/jobs/browse",
               qs={"q": "fanduel", "industries": "tech"}), None
    )["body"])
    assert body["total"] == 0


# ----- warm-Lambda cache --------------------------------------------------

def test_browse_cache_invalidate_helper_clears_state(aws):
    """invalidate_browse_cache must drop the stored items so the next
    call re-scans the (now-modified) table. Critical for the rescore
    Lambda to not show stale scores after a manual rescore."""
    db.put_job(_make_browse_job("test:cached-1", score=10, industries=["tech"]))
    body1 = json.loads(handler(_event("GET /api/jobs/browse"), None)["body"])
    assert body1["total"] == 1

    # Add a row WITHOUT clearing — should be invisible (cache hit).
    db.put_job(_make_browse_job("test:cached-2", score=20, industries=["tech"]))
    body2 = json.loads(handler(_event("GET /api/jobs/browse"), None)["body"])
    assert body2["total"] == 1   # cache served the old result

    # Now invalidate; the new row should appear.
    db.invalidate_browse_cache
    body3 = json.loads(handler(_event("GET /api/jobs/browse"), None)["body"])
    assert body3["total"] == 2


def test_browse_cache_keyed_per_status(aws):
    """`status=active` and `status=saved` use different cache slots."""
    db.put_job(_make_browse_job("test:a", score=10, industries=["tech"]))
    db.put_job(_make_browse_job("test:s", score=20, industries=["tech"],
                                status="saved"))
    a = json.loads(handler(_event("GET /api/jobs/browse"), None)["body"])
    s = json.loads(handler(
        _event("GET /api/jobs/browse", qs={"status": "saved"}), None
    )["body"])
    assert a["total"] == 1 and a["jobs"][0]["job_id"] == "test:a"
    assert s["total"] == 1 and s["jobs"][0]["job_id"] == "test:s"


# ----- query echo includes new fields -------------------------------------

def test_browse_query_echo_includes_q_and_dedup(aws):
    body = json.loads(handler(
        _event("GET /api/jobs/browse", qs={"q": "hello", "dedup": "false"}),
        None,
    )["body"])
    assert body["query"]["q"] == "hello"
    assert body["query"]["dedup"] is False


def test_browse_dedup_default_is_true(aws):
    body = json.loads(handler(_event("GET /api/jobs/browse"), None)["body"])
    assert body["query"]["dedup"] is True


# ===========================================================================
# bulk_action + semantic snippet on cards.
#   - POST /api/jobs/bulk_action wraps N updates in one request and busts
#     the warm-cache so /browse sees the new state on the very next call.
#   - /browse projects semantic_score + semantic_rationale; long rationales
#     are truncated to ~220 chars on the wire (full text remains on the row
#     and in the cache so the detail page is unaffected).
# ===========================================================================

# ----- bulk_action --------------------------------------------------------

def test_bulk_action_archives_all_listed(aws):
    """Multi-id archive flips status on every row + busts the browse cache
    so the very next /browse?status=active no longer shows them."""
    for i in range(3):
        db.put_job(_make_browse_job(f"test:bulk-{i}", score=70,
                                    industries=["tech"]))
    # Warm the active cache so we can prove invalidation works.
    pre = json.loads(handler(_event("GET /api/jobs/browse"), None)["body"])
    assert pre["total"] == 3

    resp = handler(_event(
        "POST /api/jobs/bulk_action",
        body=json.dumps({
            "action": "skip",
            "job_ids": ["test:bulk-0", "test:bulk-1", "test:bulk-2"],
        }),
    ), None)
    assert resp["statusCode"] == 200
    body = json.loads(resp["body"])
    assert body["ok"] is True
    assert body["updated"] == 3
    assert body["new_status"] == "archived"

    # All three must now be archived in the table.
    for i in range(3):
        assert db.get_job(f"test:bulk-{i}")["status"] == "archived"

    # Browse cache was busted → the active feed is empty on the next call.
    post = json.loads(handler(_event("GET /api/jobs/browse"), None)["body"])
    assert post["total"] == 0


def test_bulk_action_save_uses_saved_status(aws):
    """`save` action maps to status=saved (parity with the single-row
    /api/jobs/{id}/action endpoint)."""
    db.put_job(_make_browse_job("test:bulk-save", score=80,
                                industries=["tech"]))
    resp = handler(_event(
        "POST /api/jobs/bulk_action",
        body=json.dumps({"action": "save", "job_ids": ["test:bulk-save"]}),
    ), None)
    assert resp["statusCode"] == 200
    assert json.loads(resp["body"])["new_status"] == "saved"
    assert db.get_job("test:bulk-save")["status"] == "saved"


def test_bulk_action_partial_missing_reports_per_id(aws):
    """Mix of real + missing ids: real ones flip, missing ones land in
    the `missing` list, response is 200 (the request itself was valid)
    but ok=False so the UI can warn the user."""
    db.put_job(_make_browse_job("test:bulk-real", score=50,
                                industries=["tech"]))
    resp = handler(_event(
        "POST /api/jobs/bulk_action",
        body=json.dumps({
            "action": "skip",
            "job_ids": ["test:bulk-real", "test:bulk-missing"],
        }),
    ), None)
    body = json.loads(resp["body"])
    assert resp["statusCode"] == 200
    assert body["ok"] is False                # one was missing
    assert body["updated"] == 1
    assert body["missing"] == ["test:bulk-missing"]
    # The real row still got updated.
    assert db.get_job("test:bulk-real")["status"] == "archived"


def test_bulk_action_invalid_action_rejected(aws):
    resp = handler(_event(
        "POST /api/jobs/bulk_action",
        body=json.dumps({"action": "explode", "job_ids": ["a:1"]}),
    ), None)
    assert resp["statusCode"] == 400


def test_bulk_action_empty_job_ids_rejected(aws):
    resp = handler(_event(
        "POST /api/jobs/bulk_action",
        body=json.dumps({"action": "skip", "job_ids": }),
    ), None)
    assert resp["statusCode"] == 400


def test_bulk_action_dedupes_input_ids(aws):
    """Repeated ids in the input shouldn't double-count or fail."""
    db.put_job(_make_browse_job("test:bulk-dup", score=55,
                                industries=["tech"]))
    resp = handler(_event(
        "POST /api/jobs/bulk_action",
        body=json.dumps({
            "action": "skip",
            "job_ids": ["test:bulk-dup", "test:bulk-dup", "test:bulk-dup"],
        }),
    ), None)
    body = json.loads(resp["body"])
    # Only one id after dedup → only one update, no missing.
    assert body["updated"] == 1
    assert body["missing"] == 
    assert body["ok"] is True


def test_bulk_action_caps_oversized_input(aws):
    """A request with >200 ids is rejected at the Lambda boundary so a
    pathological client can't tie up the function for minutes."""
    big = [f"x:{i}" for i in range(201)]
    resp = handler(_event(
        "POST /api/jobs/bulk_action",
        body=json.dumps({"action": "skip", "job_ids": big}),
    ), None)
    assert resp["statusCode"] == 400
    body = json.loads(resp["body"])
    assert body.get("max") == 200


def test_bulk_action_invalid_json_body_rejected(aws):
    resp = handler(_event(
        "POST /api/jobs/bulk_action",
        body="{not valid json",
    ), None)
    assert resp["statusCode"] == 400


# ----- semantic snippet on /browse ----------------------------------------

def test_browse_includes_semantic_score_and_rationale_short(aws):
    """A rationale shorter than the snippet limit comes through untouched."""
    j = _make_browse_job("test:sem-short", score=60, industries=["tech"])
    j["semantic_score"]     = 33
    j["semantic_rationale"] = "Solid fit; remote-friendly NYC role."
    db.put_job(j)
    body = json.loads(handler(_event("GET /api/jobs/browse"), None)["body"])
    row = body["jobs"][0]
    assert row["semantic_score"] == 33
    assert row["semantic_rationale"] == "Solid fit; remote-friendly NYC role."


def test_browse_truncates_long_semantic_rationale(aws):
    """A long rationale is cut down to ~220 chars with a single ellipsis,
    so the wire payload stays small."""
    long = ("Lorem ipsum dolor sit amet, " * 30).strip  # ~810 chars
    assert len(long) > 600
    j = _make_browse_job("test:sem-long", score=70, industries=["tech"])
    j["semantic_rationale"] = long
    db.put_job(j)
    body = json.loads(handler(_event("GET /api/jobs/browse"), None)["body"])
    row = body["jobs"][0]
    rat = row["semantic_rationale"]
    assert len(rat) <= 230               # 220 + a couple chars of slack
    assert rat.endswith("\u2026")         # ellipsis appended
    assert long.startswith(rat[:-1].rstrip)   # prefix is preserved


def test_browse_omits_semantic_when_field_absent(aws):
    """Rows without a semantic_rationale don't get a stub field — the
    UI uses presence of the field to decide whether to render the snippet
    block, so we mustn't inject empty strings."""
    db.put_job(_make_browse_job("test:no-sem", score=50, industries=["tech"]))
    body = json.loads(handler(_event("GET /api/jobs/browse"), None)["body"])
    row = body["jobs"][0]
    # Field is either absent OR explicitly null/empty — UI handles both.
    assert not row.get("semantic_rationale")


# ----- archived status bypasses the cache ---------------------------------

def test_browse_archived_status_is_not_cached(aws):
    """the archived bucket is large + rarely revisited, so the
    cache deliberately skips it. Adding a new archived row must show up
    on the very next /browse?status=archived call without needing
    invalidate_browse_cache."""
    db.put_job(_make_browse_job("test:arc-1", score=10, industries=["tech"],
                                status="archived"))
    one = json.loads(handler(
        _event("GET /api/jobs/browse", qs={"status": "archived"}), None
    )["body"])
    assert one["total"] == 1

    # Add another archived row WITHOUT invalidating; it should still appear.
    db.put_job(_make_browse_job("test:arc-2", score=15, industries=["tech"],
                                status="archived"))
    two = json.loads(handler(
        _event("GET /api/jobs/browse", qs={"status": "archived"}), None
    )["body"])
    assert two["total"] == 2   # cache was bypassed


def test_browse_active_status_still_cached(aws):
    """Cache behavior for the active feed must be unchanged — the 
    no-cache rule applies to `archived` only. Without this regression
    pin, a future change might accidentally widen _UNCACHED_STATUSES."""
    db.put_job(_make_browse_job("test:cache-a", score=10, industries=["tech"]))
    one = json.loads(handler(_event("GET /api/jobs/browse"), None)["body"])
    assert one["total"] == 1
    db.put_job(_make_browse_job("test:cache-b", score=20, industries=["tech"]))
    # No invalidate → cache hit → still 1.
    two = json.loads(handler(_event("GET /api/jobs/browse"), None)["body"])
    assert two["total"] == 1
    db.invalidate_browse_cache
    three = json.loads(handler(_event("GET /api/jobs/browse"), None)["body"])
    assert three["total"] == 2
