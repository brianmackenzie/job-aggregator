"""Tests for src/lambdas/api_authorizer.py — the HTTP API request
authorizer that gates the public execute-api URL behind the CloudFront-
injected x-origin-verify secret.

No AWS needed — the authorizer is pure header logic. We set the secret
via monkeypatched env and feed synthetic payload-format-2.0 events.
"""
import importlib

import pytest

authorizer = importlib.import_module("lambdas.api_authorizer")

SECRET = "YnJpYW46c2VjcmV0"   # arbitrary base64-looking string


@pytest.fixture
def secret_env(monkeypatch):
    monkeypatch.setenv("ORIGIN_VERIFY_SECRET", SECRET)


def _event(header_value=None, identity=None):
    ev = {"headers": {}}
    if header_value is not None:
        ev["headers"]["x-origin-verify"] = header_value
    if identity is not None:
        ev["identitySource"] = identity
    return ev


def test_correct_header_authorizes(secret_env):
    assert authorizer.handler(_event(SECRET), None) == {"isAuthorized": True}


def test_wrong_header_denied(secret_env):
    assert authorizer.handler(_event("not-the-secret"), None) == {"isAuthorized": False}


def test_missing_header_denied(secret_env):
    # In production a missing identity source is 401'd at the gateway before
    # this runs; if it ever does run with no header, it must deny.
    assert authorizer.handler(_event(None), None) == {"isAuthorized": False}


def test_comma_joined_with_secret_present_authorizes(secret_env):
    """CloudFront may forward a viewer-supplied dup header comma-joined with
    the injected secret. The injected secret is always one of the values."""
    assert authorizer.handler(_event(f"spoofed,{SECRET}"), None) == {"isAuthorized": True}
    assert authorizer.handler(_event(f"{SECRET}, spoofed"), None) == {"isAuthorized": True}


def test_comma_joined_without_secret_denied(secret_env):
    assert authorizer.handler(_event("spoofed,also-wrong"), None) == {"isAuthorized": False}


def test_identity_source_fallback(secret_env):
    """When the value arrives via identitySource rather than headers."""
    assert authorizer.handler(_event(None, identity=[SECRET]), None) == {"isAuthorized": True}


def test_empty_secret_env_fails_closed(monkeypatch):
    """If the secret env var is unset/empty, deny everything (fail closed)
    rather than letting the API fall open to the public internet."""
    monkeypatch.setenv("ORIGIN_VERIFY_SECRET", "")
    assert authorizer.handler(_event(SECRET), None) == {"isAuthorized": False}
    assert authorizer.handler(_event(""), None) == {"isAuthorized": False}


def test_blank_header_denied(secret_env):
    assert authorizer.handler(_event(""), None) == {"isAuthorized": False}
