"""user_agent — central User-Agent string for every scraper + probe script.

Why this module exists
----------------------
Before , every scraper hardcoded its own User-Agent string
baked around the maintainer's email address. That meant a new user forking the
project had to grep + replace the email in 20+ files. This module
centralises the value so adoption is a single YAML edit:

    config/sources.yaml  →  scraper_defaults.contact_email

Anything that sends HTTP from this codebase should import USER_AGENT
from here rather than defining its own string.

Usage
-----
    from src.scrapers.user_agent import USER_AGENT
    requests.get(url, headers={"User-Agent": USER_AGENT})

Or inside a class attribute:

    class MyScraper(BaseScraper):
        USER_AGENT = USER_AGENT            # noqa: shadow is intentional

Loading strategy
----------------
Reads config/sources.yaml via the same path-candidate fallback as the
other config loaders (see src/scrapers/sources_config.py):

    1. /opt/sources.yaml          — Lambda layer (prod)
    2. <repo-root>/config/...     — local dev + pytest
    3. <src-root>/config/...      — legacy src/config/ (migration safety)
    4. /var/task/config/...       — legacy Lambda runtime path

If the file or the `scraper_defaults` block is absent, we fall back to
a safe generic UA so imports never break a cold-start. That fallback
is intentionally anonymous — it's strictly a "better than nothing"
shield for misconfigured deployments, not the happy path.
"""
from pathlib import Path
from typing import Optional

import yaml

_HERE = Path(__file__).resolve.parent

# Same search order as sources_config.py / ats_companies.py.
_CANDIDATES = [
    Path("/opt/sources.yaml"),                          # Lambda layer (prod)
    _HERE.parent.parent / "config" / "sources.yaml",    # repo root (dev)
    _HERE.parent / "config" / "sources.yaml",           # legacy src/config/
    Path("/var/task/config/sources.yaml"),              # legacy Lambda runtime
]

# Fallback values used ONLY if config/sources.yaml can't be found OR
# the scraper_defaults block is missing. These keep imports working in
# broken/partial deploys; they should never surface in practice.
_FALLBACK_EMAIL = "anon@example.invalid"
_FALLBACK_TEMPLATE = "jobs-aggregator/1.0 (personal use; {email})"

_cached_ua: Optional[str] = None
_cached_email: Optional[str] = None


def _load -> tuple[str, str]:
    """Read sources.yaml and return (user_agent_string, contact_email).

    Silently degrades to fallback values if the file or keys are absent,
    so a misconfigured deploy still imports cleanly.
    """
    for path in _CANDIDATES:
        if not path.exists:
            continue
        try:
            with open(path, encoding="utf-8") as fh:
                data = yaml.safe_load(fh) or {}
        except Exception:
            # Corrupt YAML — keep looking; ultimately fall through to defaults.
            continue
        defaults = data.get("scraper_defaults", {}) or {}
        email = defaults.get("contact_email") or _FALLBACK_EMAIL
        template = defaults.get("user_agent_template") or _FALLBACK_TEMPLATE
        try:
            ua = template.format(email=email)
        except Exception:
            # Malformed template — swallow and use canonical fallback shape.
            ua = _FALLBACK_TEMPLATE.format(email=email)
        return ua, email

    # No candidate found — deep fallback.
    return _FALLBACK_TEMPLATE.format(email=_FALLBACK_EMAIL), _FALLBACK_EMAIL


def get_user_agent -> str:
    """Return the canonical User-Agent string (cached per process)."""
    global _cached_ua, _cached_email
    if _cached_ua is None:
        _cached_ua, _cached_email = _load
    return _cached_ua


def get_contact_email -> str:
    """Return the raw contact email (cached per process).

    Useful for scripts that want to log the operator's contact address
    without re-deriving it from the UA string.
    """
    global _cached_ua, _cached_email
    if _cached_email is None:
        _cached_ua, _cached_email = _load
    return _cached_email


def _reset_cache_for_tests -> None:
    """Test helper — drop the in-process cache between test cases."""
    global _cached_ua, _cached_email
    _cached_ua = None
    _cached_email = None


# Eagerly resolve once at import time so callers can use the constant
# directly: `from src.scrapers.user_agent import USER_AGENT`.
USER_AGENT: str = get_user_agent
CONTACT_EMAIL: str = get_contact_email
