"""Tests for src/lambdas/api_prefs.py — GET + PUT round-trip."""
import json

from common import db
from lambdas.api_prefs import handler


def _event(method, body=None):
    """HTTP API V2 event shape — method lives under requestContext.http."""
    return {
        "requestContext": {"http": {"method": method}},
        "body": json.dumps(body) if body is not None else None,
    }


# ----- GET -----------------------------------------------------------------

def test_get_returns_empty_dict_when_no_prefs(aws):
    resp = handler(_event("GET"), None)
    assert resp["statusCode"] == 200
    body = json.loads(resp["body"])
    assert body["ok"] is True
    assert body["prefs"] == {}


def test_get_returns_existing_prefs(aws):
    db.put_pref("owner", "hidden_companies", ["acme", "spammer inc"])
    db.put_pref("owner", "display_options", {"hide_below_score": 50})
    resp = handler(_event("GET"), None)
    body = json.loads(resp["body"])
    assert body["prefs"]["hidden_companies"] == ["acme", "spammer inc"]
    assert body["prefs"]["display_options"] == {"hide_below_score": 50}


# ----- PUT -----------------------------------------------------------------

def test_put_writes_and_can_be_read_back(aws):
    body = {"config_key": "hidden_companies", "value": ["acme", "spammer inc"]}
    resp = handler(_event("PUT", body), None)
    assert resp["statusCode"] == 200
    # And the value is now retrievable via GET.
    got = json.loads(handler(_event("GET"), None)["body"])
    assert got["prefs"]["hidden_companies"] == ["acme", "spammer inc"]


def test_put_overwrites_existing_value(aws):
    db.put_pref("owner", "hidden_companies", ["old"])
    handler(_event("PUT", {"config_key": "hidden_companies", "value": ["new"]}), None)
    got = json.loads(handler(_event("GET"), None)["body"])
    assert got["prefs"]["hidden_companies"] == ["new"]


def test_put_accepts_empty_list_to_clear(aws):
    db.put_pref("owner", "hidden_companies", ["x"])
    handler(_event("PUT", {"config_key": "hidden_companies", "value": }), None)
    got = json.loads(handler(_event("GET"), None)["body"])
    assert got["prefs"]["hidden_companies"] == 


def test_put_rejects_missing_config_key(aws):
    resp = handler(_event("PUT", {"value": }), None)
    assert resp["statusCode"] == 400


def test_put_rejects_missing_value(aws):
    resp = handler(_event("PUT", {"config_key": "hidden_companies"}), None)
    assert resp["statusCode"] == 400


def test_put_rejects_invalid_json(aws):
    # Body is not valid JSON.
    resp = handler({"requestContext": {"http": {"method": "PUT"}}, "body": "{bad"}, None)
    assert resp["statusCode"] == 400


def test_put_accepts_nested_dict_value(aws):
    """Display options is a dict, not a list — both shapes must round-trip."""
    body = {"config_key": "display_options",
            "value": {"hide_below_score": 65, "default_track": "gaming"}}
    handler(_event("PUT", body), None)
    got = json.loads(handler(_event("GET"), None)["body"])
    assert got["prefs"]["display_options"] == {
        "hide_below_score": 65, "default_track": "gaming",
    }


def test_unknown_method_405(aws):
    resp = handler(_event("DELETE"), None)
    assert resp["statusCode"] == 405


def test_put_with_unknown_config_key_still_succeeds(aws):
    """We log the unknown key but accept it — opt-in schema, not a closed set."""
    body = {"config_key": "experimental_thing", "value": 42}
    resp = handler(_event("PUT", body), None)
    assert resp["statusCode"] == 200
