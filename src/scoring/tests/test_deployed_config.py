"""test_deployed_config — guardrails against config-divergence regressions.

Background:
  Before the ConfigLayer refactor, config YAMLs lived in TWO places:
    * config/            (repo root — edited by the original author)
    * src/config/        (duplicate — actually bundled into Lambda ZIPs)
  These silently diverged, which meant Haiku was driven by a stale
  candidate_profile.yaml while the original author's "canonical" edits in config/
  sat unused in the running Lambda. A lost afternoon of debugging.

Post-refactor contract (fb3):
  * ONE source of truth: config/ at repo root
  * Deployed to Lambda as /opt/<name>.yaml via AWS::Serverless::LayerVersion
  * src/config/ must NOT exist (its presence means someone re-introduced
    the duplicate and the divergence bug is back)
  * template.yaml must declare ConfigLayer with ContentUri pointing at
    the repo-root config/ directory
  * Key calibration markers must be present in the canonical files so
    edits that accidentally truncate the YAML are caught

These tests are structural / invariant checks — they run in milliseconds,
make no network calls, and don't depend on test fixtures.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml


# Repo root = three levels up from this file (src/scoring/tests/ -> src/scoring/ -> src/ -> repo)
_REPO_ROOT = Path(__file__).resolve.parents[3]
_CONFIG_DIR = _REPO_ROOT / "config"
_TEMPLATE = _REPO_ROOT / "template.yaml"


# ---------------------------------------------------------------------------
# 1. All 5 canonical YAMLs are present at repo root.
# ---------------------------------------------------------------------------

_EXPECTED_YAMLS = [
    "candidate_profile.yaml",
    "scoring.yaml",
    "companies.yaml",
    "sources.yaml",
    "taxonomy.yaml",
]


@pytest.mark.parametrize("filename", _EXPECTED_YAMLS)
def test_canonical_yaml_exists(filename: str) -> None:
    """Each of the 5 config files must exist at <repo-root>/config/."""
    path = _CONFIG_DIR / filename
    assert path.exists, (
        f"Missing canonical config: {path}\n"
        f"The Lambda layer ContentUri points at this directory — if the "
        f"file is missing, the deployed layer will be broken."
    )


# ---------------------------------------------------------------------------
# 2. src/config/ MUST NOT exist.
# ---------------------------------------------------------------------------

def test_no_src_config_duplicate -> None:
    """src/config/ was the source of the divergence bug.

    If this test fails, someone has re-introduced the duplicate. The
    Lambda loaders still have a fallback candidate at
    `<src-root>/config/<name>.yaml` (kept for safety during the
    migration), so it would silently start shadowing the layer if it
    ever came back. Nuke it.
    """
    bad_dir = _REPO_ROOT / "src" / "config"
    assert not bad_dir.exists, (
        f"Forbidden directory exists: {bad_dir}\n"
        f"This is the pre-fb3 duplicate config location. Delete it and "
        f"keep config/ at the repo root as the single source of truth."
    )


# ---------------------------------------------------------------------------
# 3. candidate_profile.yaml contains the Bug-2 calibration marker.
# ---------------------------------------------------------------------------

def test_candidate_profile_has_calibration_marker -> None:
    """The 'USE THE FULL 0-100 RANGE' instruction is the 
    Bug-2 fix — without it, Haiku compresses everything into a narrow
    band (50-70) and the scoring signal collapses.

    This test grepS the canonical file to make sure the instruction
    isn't accidentally removed by a YAML edit.
    """
    path = _CONFIG_DIR / "candidate_profile.yaml"
    text = path.read_text(encoding="utf-8")
    marker = "USE THE FULL 0-100 RANGE"
    assert marker in text, (
        f"Calibration marker '{marker}' is missing from {path}.\n"
        f"This is the Bug-2 fix. Removing it causes Haiku "
        f"to produce compressed score bands. Restore it before deploying."
    )


# ---------------------------------------------------------------------------
# 4. Structural markers for each YAML — guards against truncation.
# ---------------------------------------------------------------------------

_STRUCTURAL_MARKERS = {
    "scoring.yaml":     ["weights", "keywords"],
    "companies.yaml":   ["companies"],
    "sources.yaml":     ["sources"],
    "taxonomy.yaml":    ["industries", "role_types"],
    # candidate_profile.yaml: the system_prompt key is the non-negotiable
    # top-level key used by scoring.semantic._system_prompt.
    "candidate_profile.yaml": ["system_prompt"],
}


@pytest.mark.parametrize("filename,markers", list(_STRUCTURAL_MARKERS.items))
def test_yaml_structural_keys(filename: str, markers: list[str]) -> None:
    """Each YAML must parse and contain its expected top-level keys."""
    path = _CONFIG_DIR / filename
    with open(path, "r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    assert isinstance(data, dict), (
        f"{path} did not parse as a top-level YAML dict."
    )
    for key in markers:
        assert key in data, (
            f"{path} missing expected top-level key '{key}'.\n"
            f"If you restructured the file, update _STRUCTURAL_MARKERS "
            f"in this test file as well."
        )


# ---------------------------------------------------------------------------
# 5. template.yaml declares ConfigLayer with ContentUri: config/.
# ---------------------------------------------------------------------------

def test_template_has_configlayer -> None:
    """template.yaml must declare a ConfigLayer whose ContentUri points
    at the repo-root config/ directory. This is the whole point of the
    fb3 refactor — if the ContentUri ever gets changed to something
    else (e.g. back to src/config/), the divergence bug comes back."""
    text = _TEMPLATE.read_text(encoding="utf-8")

    # Structural check: "ConfigLayer:" as a resource
    assert "ConfigLayer:" in text, (
        f"{_TEMPLATE} does not declare a ConfigLayer resource."
    )

    # ContentUri must point at repo-root config/ (as a literal path —
    # the ContentUri line in raw template.yaml is `ContentUri: config/`).
    # Allow trailing whitespace / quotes just in case someone reformats.
    assert "ContentUri: config/" in text or 'ContentUri: "config/"' in text, (
        f"{_TEMPLATE} ConfigLayer ContentUri is not 'config/'.\n"
        f"The layer must bundle the repo-root config/ directory. "
        f"Anything else will re-introduce the fb2 divergence bug."
    )


# ---------------------------------------------------------------------------
# 6. sources.yaml carries the scraper_defaults block with contact_email.
# ---------------------------------------------------------------------------

def test_sources_yaml_has_scraper_defaults -> None:
    """sources.yaml must declare `scraper_defaults.contact_email`.

    This is the single source of truth for the operator's reachable email,
    embedded into every scraper's User-Agent string by
    `src/scrapers/user_agent.py`. If the key is missing, scrapers fall back
    to `anon@example.invalid` — which most target sites will 403/429
    on sight, silently zeroing out the daily scrape.
    """
    path = _CONFIG_DIR / "sources.yaml"
    with open(path, "r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    assert isinstance(data, dict), f"{path} did not parse as a dict"
    defaults = data.get("scraper_defaults")
    assert isinstance(defaults, dict) and defaults, (
        f"{path} is missing the top-level `scraper_defaults:` block. "
        f"Add it back — see docs/USER_CONFIG.md under 'Contact email'."
    )
    email = defaults.get("contact_email")
    assert isinstance(email, str) and "@" in email, (
        f"{path} scraper_defaults.contact_email is missing or invalid "
        f"({email!r}). Set it to a reachable address — site admins use it "
        f"to contact you instead of rate-limiting anonymous traffic."
    )
