"""Tests for src/scrapers/bettingjobs.py — Applyflow JSON API parser.

The previous version of this scraper tried to parse the public HTML
listing (which is JS-rendered and produced 0 jobs). The current
implementation hits Applyflow's seeker API and gets a clean JSON
record per job. Tests below cover parse against representative
fixture payloads matching what the live API returns.
"""
from scrapers.bettingjobs import BettingJobsScraper, _coerce_int, _strip_html


# ---------- _coerce_int -----------------------------------------------------

def test_coerce_int_handles_strings:
    assert _coerce_int("120000") == 120_000

def test_coerce_int_handles_floats:
    assert _coerce_int(120000.0) == 120_000

def test_coerce_int_rejects_zero_and_neg:
    assert _coerce_int(0) is None
    assert _coerce_int(-5) is None

def test_coerce_int_rejects_blank_and_null_string:
    assert _coerce_int("")     is None
    assert _coerce_int("null") is None
    assert _coerce_int(None)   is None

def test_coerce_int_rejects_garbage:
    assert _coerce_int("abc")  is None
    assert _coerce_int({})     is None


# ---------- _strip_html -----------------------------------------------------

def test_strip_html_collapses_whitespace:
    assert _strip_html("<p>Hello   world</p>") == "Hello world"

def test_strip_html_handles_plain_text:
    assert _strip_html("Just text") == "Just text"

def test_strip_html_empty:
    assert _strip_html("") == ""
    assert _strip_html(None) == ""


# ---------- parse — canonical JSON record --------------------------------

def _api_record(**overrides):
    """Build a representative search-job record. Overrides patch fields."""
    base = {
        "id":             "1762775",
        "uuid":           "7030b8e7-22aa-492b-b5ae-aebc9af9c834",
        "job_title":      "Director of Trading",
        "company_name":   "Acme Sportsbook",
        "URL":            "director-of-trading/7030b8e7-22aa-492b-b5ae-aebc9af9c834",
        "job_description":"Lead trading risk and pricing for our sportsbook.",
        "job_body":       "<p>Lead <strong>trading risk</strong> and pricing.</p>",
        "short_description": "Lead trading risk.",
        "location_label": "London, UK",
        "location_state_code": "london",
        "pay_min_norm":   "120000",
        "pay_max_norm":   "160000",
        "pay_min":        "",
        "pay_max":        "",
        "pay_currency":   "GBP",
        "created_at":     "2026-04-16T15:02:03.000000Z",
        "activates_at":   "2026-04-16 14:51:13",
        "apply_url":      "https://apply.example.com/abc",
    }
    base.update(overrides)
    return base


def test_parse_canonical_record:
    s   = BettingJobsScraper
    raw = s.parse(_api_record)
    assert raw is not None
    assert raw.native_id == "7030b8e7-22aa-492b-b5ae-aebc9af9c834"
    assert raw.title     == "Director of Trading"
    assert raw.company   == "Acme Sportsbook"
    assert raw.url == (
        "https://www.bettingjobs.com/jobs/"
        "director-of-trading/7030b8e7-22aa-492b-b5ae-aebc9af9c834/"
    )
    assert raw.location  == "London, UK"
    assert raw.description == "Lead trading risk and pricing for our sportsbook."
    assert raw.salary_min == 120_000
    assert raw.salary_max == 160_000
    assert raw.posted_at == "2026-04-16T15:02:03.000000Z"
    # No remote keyword in location → remote stays None.
    assert raw.remote is None


def test_parse_remote_from_location_label:
    s   = BettingJobsScraper
    raw = s.parse(_api_record(location_label="France, Remote",
                              location_state_code="remote-france"))
    assert raw.remote is True


def test_parse_remote_from_state_code_only:
    """state_code 'remote-*' is enough even when label doesn't say 'remote'."""
    s   = BettingJobsScraper
    raw = s.parse(_api_record(location_label="Paris",
                              location_state_code="remote-france"))
    assert raw.remote is True


def test_parse_onsite_label:
    s   = BettingJobsScraper
    raw = s.parse(_api_record(location_label="London (onsite)"))
    assert raw.remote is False


def test_parse_skips_when_no_title:
    s = BettingJobsScraper
    assert s.parse(_api_record(job_title="")) is None


def test_parse_skips_when_no_uuid_or_id:
    s = BettingJobsScraper
    rec = _api_record(uuid=None)
    rec["id"] = None
    assert s.parse(rec) is None


def test_parse_falls_back_to_id_when_uuid_missing:
    s = BettingJobsScraper
    rec = _api_record(uuid=None)   # but `id` still present
    raw = s.parse(rec)
    assert raw.native_id == "1762775"


def test_parse_handles_blank_pay_fields:
    s = BettingJobsScraper
    raw = s.parse(_api_record(pay_min_norm="", pay_max_norm="",
                              pay_min="", pay_max=""))
    assert raw.salary_min is None
    assert raw.salary_max is None


def test_parse_drops_below_threshold_pay:
    """Anything under 20k is hourly/daily noise — drop it."""
    s = BettingJobsScraper
    raw = s.parse(_api_record(pay_min_norm="40", pay_max_norm="60"))
    assert raw.salary_min is None
    assert raw.salary_max is None


def test_parse_falls_back_to_raw_pay_when_norm_missing:
    s = BettingJobsScraper
    raw = s.parse(_api_record(pay_min_norm="", pay_max_norm="",
                              pay_min="80000", pay_max="120000"))
    assert raw.salary_min == 80_000
    assert raw.salary_max == 120_000


def test_parse_strips_html_from_job_body_when_description_missing:
    s = BettingJobsScraper
    raw = s.parse(_api_record(job_description="",
                              job_body="<p>Hello <em>world</em></p>"))
    assert raw.description == "Hello world"


def test_parse_default_company_when_missing:
    s = BettingJobsScraper
    raw = s.parse(_api_record(company_name=""))
    assert raw.company == "BettingJobs"


def test_parse_url_falls_back_to_uuid_when_slug_missing:
    s = BettingJobsScraper
    raw = s.parse(_api_record(URL=""))
    assert raw.url == (
        "https://www.bettingjobs.com/jobs/"
        "7030b8e7-22aa-492b-b5ae-aebc9af9c834/"
    )
