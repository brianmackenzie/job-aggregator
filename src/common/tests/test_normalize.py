"""Unit tests for src/common/normalize.py."""
from datetime import datetime, timezone

from common.normalize import (
    build_job_id,
    canonicalize_posted_at,
    normalize_company,
    parse_salary_range,
    score_posted_sk,
)


# ----- normalize_company ---------------------------------------------------

def test_normalize_company_basic:
    assert normalize_company("Example") == "example"
    assert normalize_company("Example Corporation") == "example"
    assert normalize_company("Example, Inc.") == "example"
    assert normalize_company("Example Corp.") == "example"


def test_normalize_company_unicode_fold:
    # NFKD strips combining accents.
    assert normalize_company("L'Oréal SA") == "l'oreal"


def test_normalize_company_whitespace:
    assert normalize_company("  Epic   Games  ") == "epic games"


def test_normalize_company_double_suffix:
    # Iterates: "X Holdings Inc" -> "X Holdings" -> "X".
    assert normalize_company("Acme Holdings Inc") == "acme"


def test_normalize_company_empty:
    assert normalize_company("") == ""
    assert normalize_company(None) == ""


def test_normalize_company_trailing_parens_alias:
    # LinkedIn / Apify often append the short-name/ticker in parens.
    # These should normalize to match the canonical YAML target name.
    assert normalize_company("Example Studio (ES)") == "example studio"
    assert normalize_company("Paramount Pictures") == "paramount pictures"
    assert normalize_company("Paramount (Paramount Global)") == "paramount"
    assert normalize_company("Dolby Laboratories") == "dolby laboratories"
    assert normalize_company("Dolby Laboratories (Dolby)") == "dolby laboratories"


def test_normalize_company_parens_with_corp_suffix_combo:
    # Both kinds of trailing junk in either order should both come off.
    assert normalize_company("Foo (Bar) Inc.") == "foo"
    assert normalize_company("Foo Inc. (Bar)") == "foo"
    assert normalize_company("Foo (Bar) (Baz)") == "foo"


def test_normalize_company_parens_in_middle_preserved:
    # Only TRAILING parens are aliases. Middle-of-name parens (rare but
    # real, e.g. "GE (digital) Healthcare") are preserved verbatim so we
    # don't accidentally truncate the company.
    assert normalize_company("GE (digital) Healthcare") == "ge (digital) healthcare"


# ----- parse_salary_range --------------------------------------------------

def test_parse_salary_range_dollars_k:
    assert parse_salary_range("$180k - $220k") == (180_000, 220_000)
    assert parse_salary_range("180k-220k") == (180_000, 220_000)


def test_parse_salary_range_full_numbers:
    assert parse_salary_range("$180,000 to $220,000") == (180_000, 220_000)


def test_parse_salary_range_em_dash:
    assert parse_salary_range("$180k—$220k") == (180_000, 220_000)


def test_parse_salary_range_no_match:
    assert parse_salary_range("") == (None, None)
    assert parse_salary_range(None) == (None, None)
    assert parse_salary_range("competitive salary") == (None, None)


def test_parse_salary_range_implausible_rejected:
    # Year ranges and headcounts must not be mistaken for salaries.
    assert parse_salary_range("founded 1999-2024") == (None, None)


def test_parse_salary_range_swapped:
    # If somehow max < min in the source, swap them.
    assert parse_salary_range("$220k-$180k") == (180_000, 220_000)


# ----- canonicalize_posted_at ---------------------------------------------

def test_canonicalize_posted_at_iso_with_z:
    assert canonicalize_posted_at("2026-04-16T12:00:00Z") == "2026-04-16T12:00:00Z"


def test_canonicalize_posted_at_iso_with_offset:
    assert canonicalize_posted_at("2026-04-16T12:00:00+00:00") == "2026-04-16T12:00:00Z"


def test_canonicalize_posted_at_iso_naive_assumes_utc:
    assert canonicalize_posted_at("2026-04-16T12:00:00") == "2026-04-16T12:00:00Z"


def test_canonicalize_posted_at_epoch:
    # Roundtrip: build an epoch from a known datetime, then canonicalize it.
    target = datetime(2026, 4, 16, 12, 0, 0, tzinfo=timezone.utc)
    epoch_str = str(int(target.timestamp))
    assert canonicalize_posted_at(epoch_str) == "2026-04-16T12:00:00Z"


def test_canonicalize_posted_at_date_only:
    assert canonicalize_posted_at("2026-04-16") == "2026-04-16T00:00:00Z"


def test_canonicalize_posted_at_garbage:
    assert canonicalize_posted_at("") is None
    assert canonicalize_posted_at(None) is None
    assert canonicalize_posted_at("not a date") is None


# ----- build_job_id / score_posted_sk -------------------------------------

def test_build_job_id:
    assert build_job_id("remoteok", "123") == "remoteok:123"


def test_score_posted_sk_padding:
    assert score_posted_sk(87, "2026-04-16T12:00:00Z") == "0087#2026-04-16T12:00:00Z"
    assert score_posted_sk(0, "2026-04-16T12:00:00Z") == "0000#2026-04-16T12:00:00Z"
    assert score_posted_sk(100, "2026-04-16T12:00:00Z") == "0100#2026-04-16T12:00:00Z"


def test_score_posted_sk_clamps:
    assert score_posted_sk(-5, "2026-04-16T12:00:00Z") == "0000#2026-04-16T12:00:00Z"
    assert score_posted_sk(500, "2026-04-16T12:00:00Z") == "0100#2026-04-16T12:00:00Z"
