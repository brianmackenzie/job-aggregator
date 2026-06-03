"""Tests for src/lambdas/scrape_dispatcher.py.

Mocks the Lambda client so no real invoke happens; we just verify
the dispatcher calls invoke with the expected payload.
"""
import json
from unittest.mock import MagicMock

import pytest

from lambdas import scrape_dispatcher


@pytest.fixture
def mock_lambda(monkeypatch):
    """Replace the module-level boto3 Lambda client with a MagicMock."""
    monkeypatch.setenv("SCRAPE_WORKER_FN_NAME", "test-worker")
    # Force re-read of the env var the module captured at import time.
    monkeypatch.setattr(scrape_dispatcher, "_WORKER_FN", "test-worker")
    mock = MagicMock
    monkeypatch.setattr(scrape_dispatcher, "_lambda", mock)
    return mock


# ----- HTTP path -----------------------------------------------------------

def test_http_path_invokes_worker(mock_lambda):
    event = {
        "routeKey": "POST /api/scrape/{source}",
        "pathParameters": {"source": "remoteok"},
    }
    resp = scrape_dispatcher.handler(event, None)
    assert resp["statusCode"] == 202
    body = json.loads(resp["body"])
    assert body["invoked"] == "remoteok"

    mock_lambda.invoke.assert_called_once
    call = mock_lambda.invoke.call_args.kwargs
    assert call["FunctionName"] == "test-worker"
    assert call["InvocationType"] == "Event"
    assert json.loads(call["Payload"].decode("utf-8")) == {"source": "remoteok"}


def test_http_path_missing_source_returns_400(mock_lambda):
    event = {"routeKey": "POST /api/scrape/{source}", "pathParameters": {}}
    resp = scrape_dispatcher.handler(event, None)
    assert resp["statusCode"] == 400
    mock_lambda.invoke.assert_not_called


# ----- Scheduled path ------------------------------------------------------

def test_scheduled_fanout_invokes_all(mock_lambda):
    event = {"sources": ["remoteok", "himalayas", "hnhiring"]}
    resp = scrape_dispatcher.handler(event, None)
    assert resp["ok"] is True
    assert resp["invoked"] == ["remoteok", "himalayas", "hnhiring"]
    assert resp["failed"] == 
    assert mock_lambda.invoke.call_count == 3


def test_scheduled_one_failure_does_not_stop_others(mock_lambda):
    """If one invoke fails, the rest must still fire — project rule."""
    call_count = {"n": 0}

    def side_effect(**kwargs):
        call_count["n"] += 1
        payload = json.loads(kwargs["Payload"].decode("utf-8"))
        if payload["source"] == "himalayas":
            raise RuntimeError("simulated AWS throttle")
        return {}

    mock_lambda.invoke.side_effect = side_effect

    resp = scrape_dispatcher.handler(
        {"sources": ["remoteok", "himalayas", "hnhiring"]},
        None,
    )
    assert resp["invoked"] == ["remoteok", "hnhiring"]
    assert len(resp["failed"]) == 1
    assert resp["failed"][0]["source"] == "himalayas"
    assert call_count["n"] == 3


def test_scheduled_empty_sources_is_ok(mock_lambda):
    resp = scrape_dispatcher.handler({"sources": }, None)
    assert resp["invoked"] == 
    mock_lambda.invoke.assert_not_called


def test_scheduled_bad_event_shape(mock_lambda):
    resp = scrape_dispatcher.handler({"sources": "not-a-list"}, None)
    assert resp["ok"] is False
