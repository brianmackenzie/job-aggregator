"""Unit tests for src/scoring/taxonomy.py.

Strategy: feed representative job dicts and assert the expected
industries / role_types / company_group come out.  Tests use the
real config/taxonomy.yaml — the YAML is the source of truth, so
testing against a mock would just re-implement it.

Fixture jobs are intentionally minimal — only the fields the
classifiers read (title, company, company_normalized, description).
"""
from __future__ import annotations

import pytest

from scoring import taxonomy


# Reset the tier-group cache between tests so the order they run in
# doesn't matter.
@pytest.fixture(autouse=True)
def _reset_tier_cache:
    taxonomy._TIER_GROUPS_CACHE = None
    yield
    taxonomy._TIER_GROUPS_CACHE = None


def _job(**kw) -> dict:
    """Tiny job-dict builder with sensible defaults."""
    base = {
        "title":              "",
        "company":            "",
        "company_normalized": "",
        "description":        "",
    }
    base.update(kw)
    if not base["company_normalized"] and base["company"]:
        base["company_normalized"] = base["company"].lower.strip
    return base


# ---------------------------------------------------------------------
# Industry classifier — multi-value, company + keyword match.
# ---------------------------------------------------------------------

def test_industry_company_match_roblox_is_gaming_and_tech:
    """Roblox is in the gaming `companies:` list AND its description
    mentions 'platform engineering' which is a tech keyword."""
    j = _job(
        title="VP Platform Engineering",
        company="Roblox",
        description=(
            "Lead the platform engineering org at Roblox — multiplayer "
            "infrastructure and live services."
        ),
    )
    inds = taxonomy.industries_for(j)
    assert "gaming" in inds
    assert "tech" in inds


def test_industry_company_match_anthropic_is_ai:
    j = _job(
        title="Research Engineer",
        company="Anthropic",
        description="Build safer foundation models.",
    )
    assert "ai" in taxonomy.industries_for(j)


def test_industry_keyword_only_match_no_company:
    """No known company, but description mentions sportsbook → igaming."""
    j = _job(
        title="Director, Risk",
        company="ACME Holdings",
        description=(
            "Lead risk operations for our sportsbook and online betting "
            "platform across multiple regulated US states."
        ),
    )
    inds = taxonomy.industries_for(j)
    assert "igaming" in inds


def test_industry_no_match_returns_empty_list:
    """A truly generic, unmatchable role yields ."""
    j = _job(
        title="Office Manager",
        company="Some LLC",
        description="Coordinate office logistics.",
    )
    assert taxonomy.industries_for(j) == 


def test_industry_multiple_matches_preserves_yaml_order:
    """Ordering should follow YAML declaration order, not match-time order."""
    j = _job(
        title="Director of Operations",
        company="Generic Co",
        description=(
            "Manage operations spanning our streaming service and the "
            "in-game economy of our flagship MMO."
        ),
    )
    inds = taxonomy.industries_for(j)
    # gaming declared before entertainment in taxonomy.yaml.
    assert inds.index("gaming") < inds.index("entertainment")


# ---------------------------------------------------------------------
# Role type classifier — title-biased, multi-value, with excludes.
# ---------------------------------------------------------------------

def test_role_type_software_engineer_ic:
    j = _job(
        title="Senior Software Engineer",
        description="Backend services in Go.",
    )
    rts = taxonomy.role_types_for(j)
    assert "software_engineering" in rts


def test_role_type_excludes_suppress_software_engineer_for_vp:
    """`title_excludes` must keep 'VP Software Engineering' out of the
    IC software_engineering bucket — it belongs in engineering_leadership."""
    j = _job(
        title="VP, Software Engineering",
        description="Lead the engineering org.",
    )
    rts = taxonomy.role_types_for(j)
    assert "software_engineering" not in rts
    assert "engineering_leadership" in rts


def test_role_type_excludes_director_engineering:
    """Same exclude rule with director-level title."""
    j = _job(
        title="Director of Engineering",
        description="Hire and grow a team of 50 engineers.",
    )
    rts = taxonomy.role_types_for(j)
    assert "software_engineering" not in rts
    assert "engineering_leadership" in rts


def test_role_type_product_strategy_and_general_management:
    """Multi-value: a 'VP Product Strategy' is product_strategy AND
    general_management is NOT triggered (no GM-style keyword) — but
    let's check Chief Product Officer instead."""
    j = _job(
        title="VP, Product Strategy",
        description="Own the product strategy and roadmap vision.",
    )
    rts = taxonomy.role_types_for(j)
    assert "product_strategy" in rts


def test_role_type_solutions_architecture:
    j = _job(
        title="Principal Solutions Architect",
        description="Customer-facing pre-sales architect for cloud accounts.",
    )
    rts = taxonomy.role_types_for(j)
    assert "solutions_architecture" in rts


def test_role_type_strategy_top_level:
    j = _job(
        title="Director, Corporate Strategy",
        description="Corporate strategy and long-range planning.",
    )
    rts = taxonomy.role_types_for(j)
    assert "strategy" in rts


def test_role_type_transformation:
    j = _job(
        title="VP Digital Transformation",
        description="Lead enterprise-wide digital transformation programs.",
    )
    rts = taxonomy.role_types_for(j)
    assert "transformation" in rts


def test_role_type_no_match_returns_empty_list:
    j = _job(title="Receptionist", description="Greet visitors.")
    assert taxonomy.role_types_for(j) == 


# ---------------------------------------------------------------------
# Company group classifier — single-value, tier groups + YAML groups.
# ---------------------------------------------------------------------

def test_company_group_tier_s_takes_precedence_for_roblox:
    """Roblox is tier S in companies.yaml AND in gaming_aaa — tier wins."""
    j = _job(company="Roblox")
    assert taxonomy.company_group_for(j) == "tier_s"


def test_company_group_yaml_match_for_non_tiered_company:
    """A company NOT in any tier but in a YAML group falls into that group."""
    # Use a company that's in the AI labs group but unlikely to be in
    # companies.yaml as a tracked tier.
    j = _job(company="Cohere", company_normalized="cohere")
    assert taxonomy.company_group_for(j) == "ai_labs"


def test_company_group_unknown_returns_none:
    j = _job(company="Some Unknown Co")
    assert taxonomy.company_group_for(j) is None


def test_company_group_empty_company_returns_none:
    """Defensive: a job with no company shouldn't crash or match anything."""
    j = _job
    assert taxonomy.company_group_for(j) is None


# ---------------------------------------------------------------------
# Composite classify — what combined.py calls.
# ---------------------------------------------------------------------

def test_classify_returns_all_three_keys:
    j = _job(title="VP Platform Engineering", company="Roblox",
             description="Multiplayer infrastructure.")
    out = taxonomy.classify(j)
    assert set(out.keys) == {"industries", "role_types", "company_group"}
    assert isinstance(out["industries"], list)
    assert isinstance(out["role_types"], list)


def test_classify_never_raises_on_minimal_job:
    """Empty / malformed jobs must not crash the classifier."""
    out = taxonomy.classify({})
    assert out == {"industries": , "role_types": , "company_group": None}
