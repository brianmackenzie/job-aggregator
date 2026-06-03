"""Tests for src/lambdas/api_health.py.

Covers the hardening: a failing per-source ScrapeRuns query must
surface a GENERIC error marker, never the raw exception text (which can
carry ARNs / table names / the AWS account id).
"""
import json
from unittest.mock import patch

from lambdas.api_health import handler


def test_health_ok_shape(aws):
    resp = handler({}, None)
    assert resp["statusCode"] == 200
    body = json.loads(resp["body"])
    assert body["ok"] is True
    assert body["service"] == "jobs-aggregator"
    assert isinstance(body["registered_sources"], list)
    assert len(body["registered_sources"]) > 0
    assert isinstance(body["scrape_runs"], list)


def test_health_does_not_leak_raw_exception(aws):
    """When a per-source query throws, the response carries a generic
    'health_query_failed' marker — not the raw exception string."""
    secret = "arn:aws:dynamodb:us-east-1:111122223333:table/internal-secret-detail"
    with patch("lambdas.api_health.db.get_recent_scrape_runs",
               side_effect=Exception(secret)):
        resp = handler({}, None)

    assert resp["statusCode"] == 200
    raw = resp["body"]
    # The raw exception text (and the account id embedded in it) must not leak.
    assert secret not in raw
    assert "111122223333" not in raw
    body = json.loads(raw)
    err_rows = [r for r in body["scrape_runs"] if r.get("status") == "error"]
    assert err_rows, "expected error rows when every source query throws"
    assert all(r["error_message"] == "health_query_failed" for r in err_rows)
