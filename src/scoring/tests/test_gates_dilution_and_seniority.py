"""Function-gate dilution + seniority "Associate" handling guards.

These tests guard the four structural fixes that prevent IC-craft titles
with executive-sounding suffixes (e.g. "Associate 3D Design Director")
from bypassing the function and seniority hard gates.

They MUST stay green; if any starts failing the "Associate 3D Design
Director" class of bug is back.

Run locally (from repo root):
    python -m pytest src/scoring/tests/test_gates_dilution_and_seniority.py -v
"""
import pytest

from scoring.gates import check_function, check_seniority
from scoring.modifiers import _mod_immersive


# ---------------------------------------------------------------------------
# Function-gate dilution — exec exceptions get downgraded by IC prefixes
# ---------------------------------------------------------------------------

class TestFunctionGateDilution:
    """Fixes the bug where 'Associate 3D Design Director' bypassed the
    function gate via substring-match on the 'design director' exception."""

    def test_associate_3d_design_director_gates(self):
        # The original bug report — must gate, not pass.
        fired, name = check_function("Associate 3D Design Director, Experience Design")
        assert fired is True, "Associate 3D Design Director must gate (lead-IC craft)"
        assert name == "function"

    def test_visual_design_director_gates(self):
        fired, _ = check_function("Visual Design Director")
        assert fired is True, "'Visual Design Director' is a craft IC role"

    def test_graphic_design_director_gates(self):
        fired, _ = check_function("Graphic Design Director")
        assert fired is True

    def test_motion_design_director_gates(self):
        fired, _ = check_function("Motion Design Director")
        assert fired is True

    def test_ux_design_director_gates(self):
        fired, _ = check_function("UX Design Director")
        assert fired is True

    def test_real_design_director_passes(self):
        # No diluting prefix — true exec scope, must pass the gate.
        fired, _ = check_function("Design Director")
        assert fired is False, "Bare 'Design Director' is exec scope, must pass"

    def test_director_of_design_passes(self):
        fired, _ = check_function("Director of Design")
        assert fired is False

    def test_vp_of_design_passes(self):
        fired, _ = check_function("VP of Design")
        assert fired is False

    def test_director_of_engineering_passes(self):
        fired, _ = check_function("Director of Engineering")
        assert fired is False

    def test_associate_director_of_engineering_gates(self):
        # "associate" prefix dilutes the engineering exception too.
        fired, _ = check_function("Associate Director of Engineering")
        assert fired is True


# ---------------------------------------------------------------------------
# Seniority gate — Associate handling
# ---------------------------------------------------------------------------

class TestSeniorityAssociate:
    """'Associate <noun>' titles gate by default; only legitimate
    mid-exec passthroughs (Associate VP, Associate Partner, Associate General
    Counsel) and unqualified 'Associate Director' pass."""

    def test_associate_3d_design_director_gates(self):
        # Belt-and-suspenders — also caught by check_seniority, not just function.
        fired, name = check_seniority("Associate 3D Design Director")
        assert fired is True
        assert name == "seniority"

    def test_associate_producer_gates(self):
        fired, _ = check_seniority("Associate Producer")
        assert fired is True, "'Associate Producer' is junior IC at studios"

    def test_associate_marketing_manager_gates(self):
        fired, _ = check_seniority("Associate Marketing Manager")
        assert fired is True

    def test_associate_brand_manager_gates(self):
        fired, _ = check_seniority("Associate Brand Manager")
        assert fired is True

    def test_associate_vp_passes(self):
        # Insurance / banking / pharma legit mid-exec title.
        fired, _ = check_seniority("Associate Vice President, Technology Strategy")
        assert fired is False

    def test_associate_partner_passes(self):
        # Consulting (McKinsey / Deloitte / BCG) — real engagement-leader role.
        fired, _ = check_seniority("Associate Partner, Digital Transformation")
        assert fired is False

    def test_associate_general_counsel_passes(self):
        fired, _ = check_seniority("Associate General Counsel")
        assert fired is False

    def test_bare_associate_director_passes(self):
        # No craft qualifier — real mid-exec title at insurance / pharma.
        fired, _ = check_seniority("Associate Director, Strategy")
        assert fired is False

    def test_associate_marketing_director_gates(self):
        # Craft qualifier "marketing" downgrades to IC senior.
        fired, _ = check_seniority("Associate Marketing Director")
        assert fired is True

    def test_non_associate_titles_unaffected(self):
        # Make sure we didn't break the bare-intern / bare-junior path.
        assert check_seniority("Marketing Intern")[0] is True
        assert check_seniority("Junior Software Engineer")[0] is True
        assert check_seniority("VP of Engineering")[0] is False


# ---------------------------------------------------------------------------
# Immersive modifier — must require industry == immersive_lbe
# ---------------------------------------------------------------------------

class TestImmersiveModifierIndustryGate:
    """'_mod_immersive' previously fired on any text that mentioned
    'immersive' / 'experiential' / 'lbe'. Now requires the industry
    classifier to have resolved the role to the immersive_lbe bucket."""

    def test_immersive_keywords_without_immersive_industry_no_fire(self):
        # Brand-experience agency JD that mentions all the keywords but
        # whose company classifier resolved to "general_enterprise_tech".
        text = "We design immersive brand experiences and themed entertainment installations."
        fired, name, delta = _mod_immersive(text, industry="general_enterprise_tech")
        assert fired is False
        assert delta == 0

    def test_immersive_keywords_at_immersive_lbe_company_fires(self):
        text = "We design immersive themed entertainment for our LBE venues."
        fired, name, delta = _mod_immersive(text, industry="immersive_lbe")
        assert fired is True
        assert name == "immersive_themed"
        assert delta > 0

    def test_no_immersive_keywords_at_immersive_lbe_company_no_fire(self):
        # Even at an immersive_lbe company, the role itself has to mention
        # the relevant content (e.g. accounting role at Meow Wolf).
        text = "Manage accounts payable and vendor relationships."
        fired, _, delta = _mod_immersive(text, industry="immersive_lbe")
        assert fired is False
        assert delta == 0

    def test_gaming_industry_with_immersive_text_no_fire(self):
        # A gaming-publisher role mentioning "immersive gameplay" must NOT
        # get the immersive_lbe bonus — that's a different industry signal.
        text = "Build immersive multiplayer experiences for our players."
        fired, _, delta = _mod_immersive(text, industry="gaming_publisher_platform")
        assert fired is False
        assert delta == 0
