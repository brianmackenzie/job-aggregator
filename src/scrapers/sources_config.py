"""sources_config — loads config/sources.yaml for scrapers that need
runtime knobs beyond the schedule (which lives in template.yaml).

Used by:
    apify_linkedin.py  → search URLs, count_per_search, actor_id, etc.

Mirrors the search/cache pattern in ats_companies.py so the file is read
once per Lambda container.
"""
from pathlib import Path
from typing import Optional

import yaml

_HERE = Path(__file__).resolve.parent

# Candidate paths, tried in order (first match wins):
#   1. /opt/sources.yaml       — Lambda layer (prod, via ConfigLayer)
#   2. <repo-root>/config/...  — Local dev + pytest
#   3. <src-root>/config/...   — Legacy src/config/ fallback (pre-A2)
#   4. /var/task/config/...    — Legacy runtime fallback (pre-A2)
_CANDIDATES = [
    Path("/opt/sources.yaml"),                          # Lambda layer (prod)
    _HERE.parent.parent / "config" / "sources.yaml",    # repo root (dev)
    _HERE.parent / "config" / "sources.yaml",           # legacy src/config/
    Path("/var/task/config/sources.yaml"),              # legacy Lambda runtime
]

_cached: Optional[dict] = None


def load_source_config(source_name: str) -> dict:
    """Return the YAML dict for one source, or {} if not configured.

    Caller is responsible for handling missing keys with .get(...).
    """
    global _cached
    if _cached is None:
        _cached = _parse_yaml
    return _cached.get(source_name, {}) or {}


def _parse_yaml -> dict:
    """Find and parse sources.yaml. Returns {} (not raises) if not found,
    so that scrapers without sources.yaml entries still work."""
    for path in _CANDIDATES:
        if path.exists:
            with open(path, encoding="utf-8") as fh:
                data = yaml.safe_load(fh) or {}
            return data.get("sources", {}) or {}
    # Not found — empty config. Scrapers will use their hardcoded defaults.
    return {}


def _reset_cache_for_tests -> None:
    """Test helper — drop the in-process cache between test cases."""
    global _cached
    _cached = None
