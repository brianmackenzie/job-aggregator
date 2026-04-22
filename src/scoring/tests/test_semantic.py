"""Unit tests for the Haiku semantic scorer.

We never make a real network call here — every test patches
`scoring.semantic._get_client` to return a fake Anthropic client whose
`messages.create` returns a hand-crafted Message object. The point is
to verify our wiring (request shape, response parsing, error swallowing,
rate-limit, kill-switch) — NOT to validate Haiku itself.

Run from the repo root:
    python -m pytest src/scoring/tests/test_semantic.py -v
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest


# ---------------------------------------------------------------------------
# Helpers — build a fake Anthropic Message object the way the SDK does.
# ---------------------------------------------------------------------------

def _fake_message(text: str) -> SimpleNamespace:
    """Mimic anthropic.types.Message — a .content list of TextBlocks."""
    block = SimpleNamespace(text=text)
    return SimpleNamespace(content=[block])


def _fake_client_returning(text: str) -> MagicMock:
    """Construct a MagicMock that mimics anthropic.Anthropic with one canned reply."""
    client = MagicMock
    client.messages.create.return_value = _fake_message(text)
    return client


# ---------------------------------------------------------------------------
# Sample job — small but covers all the fields the user-message builder uses.
# ---------------------------------------------------------------------------

SAMPLE_JOB = {
    "job_id":            "remoteok:abc",
    "title":             "VP, Platform Engineering",
    "company":           "Roblox",
    "company_normalized": "roblox",
    "location":          "Remote (US)",
    "remote":            True,
    "salary_min":        280000,
    "salary_max":        380000,
    "description":       "Lead platform engineering for Roblox.",
}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_semantic_returns_none_when_kill_switch_off(monkeypatch):
    """semantic.enabled=false in YAML → never calls the API."""
    from scoring import semantic
    monkeypatch.setattr(semantic, "_semantic_enabled", lambda: False)
    out = semantic.semantic_score(SAMPLE_JOB)
    assert out is None


def test_semantic_returns_none_when_no_client(monkeypatch):
    """No SDK / no API key → returns None gracefully."""
    from scoring import semantic
    monkeypatch.setattr(semantic, "_semantic_enabled", lambda: True)
    monkeypatch.setattr(semantic, "_get_client", lambda: None)
    out = semantic.semantic_score(SAMPLE_JOB)
    assert out is None


def test_semantic_happy_path_pure_json(monkeypatch):
    """Haiku returns clean JSON → we extract score + rationale."""
    from scoring import semantic
    monkeypatch.setattr(semantic, "_semantic_enabled", lambda: True)
    monkeypatch.setattr(
        semantic, "_get_client",
        lambda: _fake_client_returning(
            '{"score": 87, "rationale": "Dream-tier gaming platform VP role."}'
        ),
    )
    # Bypass the real throttle so the test doesn't sleep.
    monkeypatch.setattr(semantic, "_throttle", lambda: None)
    # Bypass the real profile loader.
    monkeypatch.setattr(semantic, "_system_prompt", lambda: "PROFILE")

    out = semantic.semantic_score(SAMPLE_JOB)
    assert out is not None
    assert out["score"] == 87
    assert "Dream-tier" in out["rationale"]
    assert out["model"]
    assert out["scored_at"]


def test_semantic_strips_markdown_fences(monkeypatch):
    """Haiku occasionally wraps JSON in ```json fences — we tolerate that."""
    from scoring import semantic
    monkeypatch.setattr(semantic, "_semantic_enabled", lambda: True)
    monkeypatch.setattr(
        semantic, "_get_client",
        lambda: _fake_client_returning(
            '```json\n{"score": 42, "rationale": "Adjacent fit."}\n```'
        ),
    )
    monkeypatch.setattr(semantic, "_throttle", lambda: None)
    monkeypatch.setattr(semantic, "_system_prompt", lambda: "PROFILE")
    out = semantic.semantic_score(SAMPLE_JOB)
    assert out is not None
    assert out["score"] == 42


def test_semantic_extracts_json_with_preamble(monkeypatch):
    """Haiku occasionally precedes JSON with a sentence — greedy {…} extraction handles it."""
    from scoring import semantic
    monkeypatch.setattr(semantic, "_semantic_enabled", lambda: True)
    monkeypatch.setattr(
        semantic, "_get_client",
        lambda: _fake_client_returning(
            'Here is my evaluation: {"score": 30, "rationale": "Wrong direction."}'
        ),
    )
    monkeypatch.setattr(semantic, "_throttle", lambda: None)
    monkeypatch.setattr(semantic, "_system_prompt", lambda: "PROFILE")
    out = semantic.semantic_score(SAMPLE_JOB)
    assert out is not None
    assert out["score"] == 30


def test_semantic_clamps_out_of_range_scores(monkeypatch):
    """If Haiku returns 120 or -5, we clamp to [0,100] rather than failing."""
    from scoring import semantic
    monkeypatch.setattr(semantic, "_semantic_enabled", lambda: True)
    monkeypatch.setattr(
        semantic, "_get_client",
        lambda: _fake_client_returning('{"score": 150, "rationale": "x"}'),
    )
    monkeypatch.setattr(semantic, "_throttle", lambda: None)
    monkeypatch.setattr(semantic, "_system_prompt", lambda: "PROFILE")
    out = semantic.semantic_score(SAMPLE_JOB)
    assert out is not None
    assert out["score"] == 100


def test_semantic_returns_none_on_unparseable_response(monkeypatch):
    """Haiku returns garbage → we return None, don't raise."""
    from scoring import semantic
    monkeypatch.setattr(semantic, "_semantic_enabled", lambda: True)
    monkeypatch.setattr(
        semantic, "_get_client",
        lambda: _fake_client_returning("I cannot respond to that request."),
    )
    monkeypatch.setattr(semantic, "_throttle", lambda: None)
    monkeypatch.setattr(semantic, "_system_prompt", lambda: "PROFILE")
    out = semantic.semantic_score(SAMPLE_JOB)
    assert out is None


# ---------------------------------------------------------------------------
# Truncation-salvage — patch
#
# Haiku occasionally hits max_tokens mid-life_fit_concerns, leaving us with
# a response that's syntactically truncated. The salvage path closes open
# containers so we recover everything up to the truncation point instead
# of discarding the whole response (and permanently stranding the row).
# Fixtures below are lightly anonymized from real CloudWatch warn events.
# ---------------------------------------------------------------------------

_TRUNCATED_ANDURIL = (
    '```json\n'
    '{\n'
    '  "score": 8,\n'
    '  "work_mode": "onsite",\n'
    '  "rationale": "Senior Electrical Engineer (EMI/EMC) at defense '
    'contractor - IC role, not VP/platform tech leadership; individual '
    'contributor hands-on engineering, not executive track.",\n'
    '  "role_family_match": "none",\n'
    '  "industry_match": "partial",\n'
    '  "geography_match": "unreachable",\n'
    '  "level_match": "under",\n'
    '  "watchlist_dream": false,\n'
    '  "life_fit_concerns": [\n'
    '    "Individual contributor engineering role (the original author is VP-level '
    'executive, not IC coder'
)

_TRUNCATED_OPENAI = (
    '```json\n'
    '{\n'
    '  "score": 28,\n'
    '  "work_mode": "hybrid",\n'
    '  "rationale": "NYC hybrid role at dream-tier AI company, but '
    'Technical Deployment Lead is program/project management specialty.",\n'
    '  "role_family_match": "partial",\n'
    '  "industry_match": "none",\n'
    '  "geography_match": "reachable",\n'
    '  "level_match": "under",\n'
    '  "watchlist_dream": false,\n'
    '  "life_fit_concerns": [\n'
    '    "Progra'
)


def test_salvage_truncated_json_anduril_fixture:
    """Close-the-containers salvage recovers score + earlier fields from
    a response truncated mid-life_fit_concerns string."""
    from scoring.semantic import _parse_response
    out = _parse_response(_TRUNCATED_ANDURIL)
    assert out is not None
    assert out["score"] == 8
    # Structured fields should survive the salvage intact.
    assert out["role_family_match"] == "none"
    assert out["industry_match"] == "partial"
    assert out["geography_match"] == "unreachable"
    assert out["level_match"] == "under"
    assert out["watchlist_dream"] is False
    # life_fit_concerns may contain the truncated string or be shorter
    # than Haiku intended, but the field itself must exist and be a list.
    assert isinstance(out["life_fit_concerns"], list)


def test_salvage_truncated_json_openai_fixture:
    """Second real-world truncation case - short life_fit_concerns 'Progra'."""
    from scoring.semantic import _parse_response
    out = _parse_response(_TRUNCATED_OPENAI)
    assert out is not None
    assert out["score"] == 28
    assert out["role_family_match"] == "partial"
    assert out["industry_match"] == "none"
    assert out["watchlist_dream"] is False
    assert isinstance(out["life_fit_concerns"], list)


def test_salvage_no_opening_brace_returns_none:
    """Salvage has nothing to work with if response never started an object."""
    from scoring.semantic import _salvage_truncated_json
    assert _salvage_truncated_json("no braces here") is None
    assert _salvage_truncated_json("") is None


def test_salvage_unaffected_when_response_is_clean:
    """A well-formed response parses via the fast path, not the salvage path."""
    from scoring.semantic import _parse_response
    clean = (
        '{"score": 75, "work_mode": "remote", "rationale": "ok",'
        ' "role_family_match": "strong", "industry_match": "strong",'
        ' "geography_match": "reachable", "level_match": "at",'
        ' "watchlist_dream": false, "life_fit_concerns": }'
    )
    out = _parse_response(clean)
    assert out is not None
    assert out["score"] == 75
    assert out["life_fit_concerns"] == 


def test_semantic_returns_none_on_api_exception(monkeypatch):
    """Network error or 5xx → returns None instead of raising."""
    from scoring import semantic
    monkeypatch.setattr(semantic, "_semantic_enabled", lambda: True)

    def _raising_client:
        c = MagicMock
        c.messages.create.side_effect = RuntimeError("boom")
        return c

    monkeypatch.setattr(semantic, "_get_client", _raising_client)
    monkeypatch.setattr(semantic, "_throttle", lambda: None)
    monkeypatch.setattr(semantic, "_system_prompt", lambda: "PROFILE")
    out = semantic.semantic_score(SAMPLE_JOB)
    assert out is None


def test_user_message_truncates_long_descriptions:
    """Job descriptions > 3000 chars get truncated so we stay in budget."""
    from scoring.semantic import _build_user_message, _JD_MAX_CHARS
    huge = {
        "title":       "VP",
        "company":     "X",
        "description": "A" * (_JD_MAX_CHARS + 5000),
    }
    msg = _build_user_message(huge)
    # The literal "A" run should have been truncated. Original would
    # produce 8000 A's in a row; truncation keeps only the first 3000.
    assert "A" * (_JD_MAX_CHARS + 1) not in msg
    assert "[…description truncated…]" in msg


def test_user_message_includes_salary_when_present:
    from scoring.semantic import _build_user_message
    msg = _build_user_message(SAMPLE_JOB)
    assert "$280,000" in msg
    assert "$380,000" in msg
    assert "Title: VP, Platform Engineering" in msg


def test_user_message_omits_salary_when_missing:
    from scoring.semantic import _build_user_message
    job = dict(SAMPLE_JOB)
    job.pop("salary_min")
    job.pop("salary_max")
    msg = _build_user_message(job)
    assert "Salary:" not in msg


def test_request_shape_passed_to_anthropic(monkeypatch):
    """Verify we pass system + user message + model + max_tokens correctly."""
    from scoring import semantic
    monkeypatch.setattr(semantic, "_semantic_enabled", lambda: True)
    monkeypatch.setattr(semantic, "_throttle", lambda: None)
    monkeypatch.setattr(semantic, "_system_prompt", lambda: "OWNER_PROFILE")

    captured = {}

    class _SpyClient:
        class messages:  # noqa: N801 — match the real SDK shape
            @staticmethod
            def create(**kwargs):
                captured.update(kwargs)
                return _fake_message('{"score": 50, "rationale": "x"}')

    monkeypatch.setattr(semantic, "_get_client", lambda: _SpyClient)

    out = semantic.semantic_score(SAMPLE_JOB)
    assert out is not None
    assert captured["system"] == "OWNER_PROFILE"
    assert captured["model"]  # comes from CFG
    assert captured["max_tokens"]
    assert captured["messages"][0]["role"] == "user"
    assert "VP, Platform Engineering" in captured["messages"][0]["content"]
