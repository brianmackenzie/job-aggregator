"""Shared field normalization.

All the "strip unicode weirdness, canonicalize messy real-world strings"
work lives here so every scraper produces compatible data. The most
important function is `normalize_company` — it's what makes it possible
to dedupe a single company's postings across sources (Greenhouse says
"Example Corporation", LinkedIn says "Example", we want one company).
"""
import re
import unicodedata
from datetime import datetime, timezone
from typing import Optional

# Corporate-form suffixes stripped from company names to stabilize the
# company_normalized key across sources. Longer patterns first so
# "Pty Ltd" matches before "Ltd".
_CORP_SUFFIXES = [
    "incorporated", "corporation", "limited", "holdings",
    "pty ltd", "gmbh", "s.a.", "sa", "inc", "corp", "llc", "ltd",
    "co", "plc", "ag", "bv", "nv",
]
_CORP_SUFFIX_RE = re.compile(
    r"[,.\s]+(?:" + "|".join(re.escape(s) for s in _CORP_SUFFIXES) + r")\.?$",
    re.IGNORECASE,
)
# Trailing parenthetical alias / descriptor — e.g. "Example Studio (ES)",
# "Paramount Pictures", "Dolby Laboratories (Dolby)". LinkedIn / Apify
# frequently appends the ticker or short-name in parens, which would
# otherwise produce a different `company_normalized` than the same
# company surfaced via Greenhouse / the YAML target list.
_TRAILING_PARENS_RE = re.compile(r"\s*\([^)]*\)\s*$")
_WHITESPACE_RE = re.compile(r"\s+")


def normalize_company(name: Optional[str]) -> str:
    """Lowercase, NFKD-fold, strip corporate suffixes, collapse whitespace.

    Used as the PK of the CompanyIndex GSI. Examples:
        "Example"                  -> "example"
        "Example Corporation"      -> "example"
        "Example, Inc."            -> "example"
        "L'Oréal SA"              -> "l'oreal"
        "  Epic   Games  "        -> "epic games"
        "Example Studio (ES)" -> "example studio"
        "Foo (Bar) Inc."          -> "foo"
    """
    if not name:
        return ""
    # NFKD: "é" -> "e" + combining accent; "\u00A0" (NBSP) -> regular space.
    folded = unicodedata.normalize("NFKD", name)
    folded = "".join(c for c in folded if not unicodedata.combining(c))
    cleaned = _WHITESPACE_RE.sub(" ", folded.lower).strip
    # Iterate so chained suffixes drop together. Example walk:
    #   "foo (bar) inc"   -> "foo (bar)"   (corp-suffix strip)
    #   "foo (bar)"       -> "foo"         (paren strip)
    # And vice-versa for "foo inc (bar)" style strings:
    #   "foo inc (bar)"   -> "foo inc"     (paren strip)
    #   "foo inc"         -> "foo"         (corp-suffix strip)
    prev = None
    while prev != cleaned:
        prev = cleaned
        cleaned = _CORP_SUFFIX_RE.sub("", cleaned).strip(" ,.")
        cleaned = _TRAILING_PARENS_RE.sub("", cleaned).strip(" ,.")
    return cleaned


# Match two numbers separated by a dash/en-dash/em-dash/'to', each
# optionally prefixed by $ and optionally suffixed by 'k'. The regex
# is deliberately strict so phrases like "founded 1999-2024" don't
# get mistaken for a salary band.
_SALARY_RE = re.compile(
    r"\$?\s*([0-9]{2,3}(?:,[0-9]{3})*|[0-9]{3,7})\s*(k)?\s*"
    r"(?:-|–|—|to)\s*"
    r"\$?\s*([0-9]{2,3}(?:,[0-9]{3})*|[0-9]{3,7})\s*(k)?",
    re.IGNORECASE,
)


def parse_salary_range(text: Optional[str]) -> tuple[Optional[int], Optional[int]]:
    """Extract (min, max) annual salary (USD) from a free-text string.

    Returns (None, None) if no range is found. Only values in the
    plausible annual-salary range $10k-$10M are accepted, which rules
    out matching things like years ("1999-2024") or headcount.

    Examples:
        "$180k - $220k"        -> (180000, 220000)
        "180k-220k"            -> (180000, 220000)
        "$180,000 to $220,000" -> (180000, 220000)
        "competitive"          -> (None, None)
    """
    if not text:
        return None, None

    def _to_int(num: str, k: Optional[str]) -> Optional[int]:
        try:
            v = int(num.replace(",", ""))
        except ValueError:
            return None
        if k:
            v *= 1000
        # Sanity filter: plausible annual salary only.
        return v if 10_000 <= v <= 10_000_000 else None

    m = _SALARY_RE.search(text)
    if not m:
        return None, None
    lo = _to_int(m.group(1), m.group(2))
    hi = _to_int(m.group(3), m.group(4))
    if lo is not None and hi is not None and hi < lo:
        lo, hi = hi, lo
    return lo, hi


def canonicalize_posted_at(value: Optional[object]) -> Optional[str]:
    """Coerce various date strings/epochs to ISO8601 UTC with 'Z' suffix.

    Accepts:
      - ISO8601 strings with or without timezone
      - unix epoch seconds (as int or a string of digits)
      - date-only strings like "2026-04-16" (assumed UTC midnight)

    Returns None if parsing fails — callers should fall back to now
    when the source doesn't publish a posted_at.
    """
    if value is None:
        return None
    s = str(value).strip
    if not s:
        return None
    # Epoch seconds (pure digits).
    if s.isdigit:
        try:
            dt = datetime.fromtimestamp(int(s), tz=timezone.utc)
            return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        except (ValueError, OverflowError, OSError):
            return None
    # ISO8601. fromisoformat doesn't accept trailing 'Z' until Python 3.11;
    # rewriting it keeps us compatible with 3.10 too.
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return None


def build_job_id(source: str, native_id: str) -> str:
    """PK for the Jobs table: '<source>:<native_id>'. Collisions across
    sources are impossible by construction."""
    return f"{source}:{native_id}"


def score_posted_sk(score: int, posted_at: str) -> str:
    """Sort key for ScoreIndex: '0087#2026-04-16T12:00:00Z'.

    Zero-padding the score to 4 digits keeps DynamoDB's lexicographic
    RANGE sort aligned with numeric sort. The score is clamped to 0..100
    because scores are always in that range and a 4-digit pad would
    break otherwise.
    """
    s = max(0, min(100, int(score)))
    return f"{s:04d}#{posted_at}"
