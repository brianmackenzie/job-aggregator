"""ats_companies — loads config/companies.yaml for the ATS scrapers.

Greenhouse, Lever, and Ashby scrapers all call:
    companies = load_ats_companies("greenhouse")   # or "lever" / "ashby"

which returns a list of dicts like:
    [{"name": "Acme Corp", "name_normalized": "acme corp",
      "tier": "S", "ats_slug": "acme-corp", ...}, ...]

Config-file search order (first match wins):
  1. /opt/companies.yaml       — Lambda layer (prod, via ConfigLayer)
  2. <repo-root>/config/...    — Local dev + pytest
  3. <src-root>/config/...     — Legacy src/config/ fallback (pre-A2)
  4. /var/task/config/...      — Legacy runtime fallback (pre-A2)
"""
from pathlib import Path
from typing import Optional

import yaml

_HERE = Path(__file__).resolve.parent   # .../src/scrapers/

# Candidate paths, tried in order.
_CANDIDATES = [
    Path("/opt/companies.yaml"),                          # Lambda layer (prod)
    _HERE.parent.parent / "config" / "companies.yaml",    # repo root (dev)
    _HERE.parent / "config" / "companies.yaml",           # legacy src/config/
    Path("/var/task/config/companies.yaml"),              # legacy Lambda runtime
]

_cached: Optional[list] = None  # module-level cache — file is read once per container


def load_ats_companies(ats_type: str) -> list[dict]:
    """Return all companies whose `ats` field matches *ats_type*.

    Caches the full YAML parse so all three scrapers running in the same
    Lambda container only hit the disk once.
    """
    global _cached
    if _cached is None:
        _cached = _parse_yaml
    return [c for c in _cached if c.get("ats") == ats_type]


def _parse_yaml -> list[dict]:
    """Find and parse companies.yaml. Raises RuntimeError if not found."""
    for path in _CANDIDATES:
        if path.exists:
            with open(path, encoding="utf-8") as fh:
                data = yaml.safe_load(fh)
            return data.get("companies", )
    raise RuntimeError(
        "companies.yaml not found. Searched:\n"
        + "\n".join(f"  {p}" for p in _CANDIDATES)
        + "\nRun: Copy-Item config\\companies.yaml src\\config\\companies.yaml"
    )
