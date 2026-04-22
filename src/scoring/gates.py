"""
# PERSONAL PROFILE DATA — REPLACE BEFORE USING AT SCALE
#
# This module contains constants that encode the ORIGINAL AUTHOR'S
# personal job-search profile (geography, target companies, target
# keywords, career-history heuristics). Shipping these as-is in a
# public fork is safe (the data is not secret) but the scoring
# behavior will be tuned for the original author, not you.
#
# For a proper fork:
#   1. Edit `config/candidate_profile.yaml` first — it drives the
#      Claude Haiku semantic layer, which is the dominant signal.
#   2. Come back here and rewrite the constants below to match
#      your own geography, industry keywords, and company lists.
#
# See `docs/FORKING.md` for a file-by-file guide.
"""

"""Hard gates and the geographic soft-gate multiplier.

Every gate is evaluated BEFORE category scoring. If any hard gate fires
the engine returns score=0 immediately and records which gates triggered.

Gate functions return a tuple (fired: bool, name: str) so the engine
can collect a list of gates_triggered for the breakdown.

Gates implemented here:
  seniority_gate     — "intern", "entry level", etc. in the job TITLE
  function_gate      — IC engineering / design / legal / policy titles
                       that are categorically outside the original author's background
  engagement_gate    — unpaid / commission-only → engagement_gate_value = 0
  compensation_gate  — explicit salary below the configured floor
  geographic_gate    — requires relocation AND not remote → value = 0
                       international location embedded in title → value = 0
                       ambiguous location → value = 0.5 (soft penalty)
                       passes          → value = 1.0
"""
import re
from typing import Optional

from .keywords import (
    CFG,
    GATES_CFG,
    KW_ENG_DISQ,
    KW_SENIORITY_DISQ,
    LOC_HEAVY_OFFICE_RE,
    LOC_NJ_RE,
    LOC_NYC_RE,
    LOC_OUT_OF_AREA_RE,
    LOC_RELOCATION_RE,
    LOC_REMOTE_RE,
    LOC_SCORES,
    any_match,
    regex_match,
)


# ---------------------------------------------------------------------------
# Function gate — IC engineering / design / legal / policy titles
# ---------------------------------------------------------------------------

# Job title phrases that indicate an individual-contributor (IC) role in a
# function the original author cannot credibly target. Checked against title ONLY to avoid
# false positives (e.g., a job description that mentions "software engineer
# growth" when describing a team the original author would manage).
#
# The check is substring-based and case-insensitive.  To avoid collisions
# the phrases are chosen to be specific enough that they won't appear in
# executive/leadership titles naturally (e.g., "software engineer" will NOT
# appear in "VP of Engineering" or "Director of Software").
_FUNCTION_GATE_KWS: list[str] = [
    # IC software / data / ML engineering — common explicit titles
    "software engineer",
    "machine learning engineer",
    "ml engineer",
    "data engineer",
    "data scientist",
    "platform engineer",
    "security engineer",       # NOT "Chief Security Officer" or "VP of Security"
    "firmware engineer",
    "site reliability engineer",
    "sre ",                    # trailing space avoids matching "SRES" or "career"
    "devops engineer",
    "backend engineer",
    "frontend engineer",
    "full-stack engineer",
    "fullstack engineer",
    "mobile engineer",
    "ios engineer",
    "android engineer",
    "qa engineer",
    "test engineer",
    "cloud engineer",
    "infrastructure engineer",
    "computer vision engineer",
    "ai engineer",
    "applied scientist",       # Amazon's term for ML IC roles
    # IC software — gaming-specific engineer / programmer suffixes
    # These often appear in dream-company JDs but are categorically wrong-fit:
    # "Principal Engine Programmer", "Senior Rendering Engineer", "Distinguished Engineer".
    "engine programmer",
    "engine developer",
    "engine engineer",
    "core engine",
    "gameplay engineer",
    "gameplay programmer",
    "graphics engineer",
    "graphics programmer",
    "rendering engineer",
    "rendering programmer",
    "tools engineer",
    "tools programmer",
    "audio programmer",
    "network engineer",
    "network programmer",
    "applications engineer",
    "application engineer",
    "consumer apps engineer",
    "distinguished engineer",
    "principal developer",     # IC senior IC, not leadership
    "senior developer",
    "lead developer",
    "lead engineer",           # NOTE: "lead" alone is too broad; pair with engineer
    "lead programmer",
    "principal programmer",
    "research engineer",
    "research scientist",
    # More engineer suffixes surfaced in dream-company titles
    "quality engineer",
    "automation engineer",
    "framework engineer",
    "asset management engineer",
    "app store manager",       # operational, not the original author's function
    "automation framework",
    "machine learning scientist",
    "ml scientist",
    "design engineer",
    "hardware engineer",
    "detection and response",
    "salesforce engineer",
    "servicenow",              # ServiceNow admin / engineer roles
    "system administrator",
    "principal engineer",      # bare "Principal Engineer" IC
    "senior engineer",
    "staff engineer",          # (was in hands_on_code kws, now gated)
    "fellow engineer",
    "data center engineer",
    "data center",             # "Data Center Asset Management Analyst"
    "analytics engineer",
    "software development engineer",
    "sdet",
    " sdet",
    "security operations engineer",
    "compensation engineer",
    "workday compensation",
    "sem analyst",             # search engine marketing
    "media planner",
    "knowledge manager",       # IC documentation / knowledge mgmt role
    "community manager",
    "event manager",
    "events manager",
    "project manager,",        # "Project Manager, X" IC
    "senior project manager",
    "supply chain manager",
    "logistics manager",
    "procurement manager",
    "payment risk",            # "Lead, Payment Risk Operations" — D2C/risk ops IC
    "risk operations",
    "fraud operations",
    "trust and safety",        # IC trust-and-safety ops
    # IC analytics / data roles
    "data analyst",
    "risk analyst",
    "grc analyst",
    "business analyst",
    "people scientist",        # HR analytics IC
    "people analytics",
    "senior machine learning", # "Senior Machine Learning - Avatar" bare IC
    "senior ml ",
    "marketing analyst",
    "operations analyst",
    "intelligence analyst",
    # Content / copy IC
    "copywriter",
    "writer,",                 # "Writer, Something"
    "senior writer",
    "technical writer",
    "content strategist",
    # HR / L&D leadership that's still wrong-function (even at exec level)
    "total rewards",
    "head of hr",
    "head of human resources",
    "head of people",
    "vp of people",
    "vp of human resources",
    "chief people",
    "chief human resources",
    "chro",                    # word-boundary acronym not needed; rare in other words
    "head of talent",
    "vp of talent",
    "chief talent",
    "talent acquisition",
    "learning and coaching",
    "learning & coaching",
    "learning and development",
    "hr operations",
    "people operations",
    "employee relations",
    "diversity & inclusion",
    "dei ",
    # Marketing leadership — wrong function (the original author is NOT a marketer)
    "head of marketing",
    "vp of marketing",
    "vp marketing",
    "vp, marketing",
    "chief marketing",
    "cmo",
    "director of marketing",
    "marketing director",
    "head of growth",
    "vp of growth",
    "user growth",                  # "Head of User Growth" — growth-marketing leadership
    "head of user growth",
    "vp of user growth",
    "growth manager",
    "growth marketing manager",
    "growth product manager",
    "growth analyst",
    "growth lead",
    "growth strategist",            # growth function, not the original author's strategy lane
    # Data Science leadership — wrong function for the original author (he's tech/strategy, not DS)
    # Catches "Technical Director - Data Science", "Director of Data Science",
    # "Head of Data Science", "VP Data Science", etc.
    "data science",
    "head of data science",
    "vp of data science",
    "vp, data science",
    "director of data science",
    "data science director",
    # Corporate Development / Business Development at sub-VP level — the original author's
    # M&A background is captured by the ma_gaming_media modifier; what he does
    # NOT want is "normal" sub-VP corp dev / BD operator roles that aren't
    # tech/strategy framed. Gate the explicit sub-VP titles.
    "corporate development senior associate",
    "corporate development associate",
    "corporate development analyst",
    "corporate development manager",
    "senior corporate development",
    "business development manager",
    "senior business development",
    "business development associate",
    "business development representative",
    "bd associate",
    "bd manager",
    "partnerships manager",
    "partnerships associate",
    "strategic partnerships manager",
    # ---- additions -----------------------
    # SkillBridge — DoD military-transition fellowship program. Not the original author-
    # applicable regardless of role function.
    "skillbridge",
    # "General Application" — companies use these as catch-all resume drops,
    # not real postings. Never apply blind.
    "general application",
    # Engineering Manager — rung-level management, 2-4 levels below VP.
    # the original author targets VP/Head/Director of Engineering (all in the exceptions
    # list), NOT engineering managers. Gate the bare forms.
    # IMPORTANT: "engineering manager" was REMOVED from the exceptions list
    # in this round so this gate actually fires. Director/VP of Engineering
    # still pass via their more specific exceptions.
    "engineering manager",
    "senior engineering manager",
    "principal engineering manager",
    "staff engineering manager",
    "machine learning engineering manager",
    "ml engineering manager",
    # Marketing Manager — IC and rung-management marketing, wrong function.
    # the original author's target marketing shapes (CMO / VP Marketing / Head of Marketing)
    # are ALREADY gated — he's pivoting away from marketing entirely.
    "marketing manager",
    "integrated marketing manager",
    "senior marketing manager",
    "product marketing manager",
    "digital marketing manager",
    "field marketing manager",
    "lifecycle marketing manager",
    "email marketing manager",
    # Content Design / Creative Manager — design/creative IC management.
    # the original author's design shapes are "design director", "vp of design", etc.
    # (in exceptions). Bare content/creative manager titles are gated.
    "content design",              # "Manager, Content Design"
    "creative manager",
    "manager, content",
    # Compliance — IC and program-manager level compliance, wrong function.
    # the original author isn't a compliance exec.
    "compliance manager",
    "compliance program manager",
    "senior compliance",
    "compliance analyst",
    "compliance officer,",         # "Compliance Officer, X" sub-exec
    # Risk / Fraud sub-VP — "Senior Engineering Manager - Risk & Fraud" is
    # caught by "engineering manager" above, but the bare function forms
    # also need gating.
    "risk manager",
    "fraud manager",
    "fraud analyst",
    "aml analyst",
    "aml manager",
    # Finance IC / Systems — "Senior Finance Systems Associate" and peers.
    # the original author's M&A work is captured by the ma_gaming_media modifier; what's
    # NOT him is sub-VP finance ops/IT.
    "finance systems",
    "finance associate",
    "finance analyst",
    "senior finance associate",
    "senior finance analyst",
    "finance operations",
    "financial operations",
    # ---- additions (resume-tuned) -------------------------------
    # Government Affairs / Public Policy / Lobbying — wrong function for
    # the original author. He's a tech/product/M&A executive, not a regulatory/policy
    # operator. Catches "Senior Director, State Government Affairs",
    # "Director of Government Affairs - Texas", "Senior Product Policy
    # Manager", "Product Policy Specialist", etc.
    "government affairs",
    "public affairs",
    "policy specialist",
    "policy manager",
    "regional policy",
    "state government",
    "lobbying",
    "lobbyist",
    "trust & safety policy",
    "trust and safety policy",
    # D2C Promotions / Sportsbook Promo — pivot-away lane. the original author's resume
    # does include holiday promos, but he's explicitly leaving that work.
    # Catches "Senior Director, Promotions" @ PrizePicks etc.
    "director, promotions",
    "director of promotions",
    "head of promotions",
    "vp of promotions",
    "vp, promotions",
    "promotions manager",
    "senior director, promotions",
    "promotions specialist",
    # Sub-VP IC Engineers slipping through "senior engineer" (which only
    # matches consecutive words). These titles have an interstitial word.
    "web engineer",                # "Senior Web Engineer" @ Rockstar
    "senior web engineer",
    "systems engineer",            # "Senior IT Systems Engineer"
    "senior systems engineer",
    "appsec engineer",             # "Senior AppSec Engineer"
    "application security engineer",
    "it engineer",                 # "Senior IT Engineer"
    "senior it engineer",
    "it systems engineer",
    "platform reliability",        # platform reliability engineer IC
    # Compliance Senior Manager (the comma version that "senior compliance"
    # missed because the word order is "compliance" then "senior").
    "compliance senior manager",
    # Localization — wrong function. the original author's a tech executive, not L10N.
    "localization program manager",
    "localization manager",
    "localization director",
    "localization specialist",
    "localization coordinator",
    "head of localization",
    "vp of localization",
    # Creator Growth Operations / Operations and Analytics IC — sub-VP ops
    # that the function gate didn't catch.
    "creator growth",
    "creator growth operations",
    "growth operations manager",
    "growth operations",
    "operations and analytics",
    "operations & analytics",
    "analytics associate",
    "analytics operations",
    # Learning Design / L&D IC — already gated "learning and coaching" /
    # "learning and development" but missed "learning design"-style titles.
    "learning design",
    "learning design & development",
    "learning design and development",
    "instructional designer",
    "instructional design",
    "training manager",
    "training specialist",
    # Director of Business Development (no scope qualifier) — the original author's M&A
    # background is captured by the ma_gaming_media modifier; BD director
    # without explicit "Strategy" or "Corporate Development" framing is
    # sales/partnerships work, wrong function.
    "director of business development",
    "director, business development",
    "business development director",
    # ---- additions ---------------------------------------------
    # HR / People IC associates — "People Business Associate" is HR support
    # IC, wrong function. The "business associate" form covers similar
    # HR / Audit / Sales associate titles that surfaced in mid-tier.
    "people business associate",
    "people associate",
    "business associate",
    "business operations associate",
    # UX Researcher — IC user research, wrong function. the original author's design
    # leadership shapes (design director, vp of design, head of design)
    # are exceptions and don't include "researcher".
    "ux researcher",
    "ux research",
    "user research manager",   # bare ux research mgr is IC-mgmt, not exec
    # IT Support / Support Engineer — IC IT operations.
    "it support",
    "it support engineer",
    "it support specialist",
    "support engineer",        # "Senior Support Engineer", etc.
    # Workplace / Facilities — wrong function. the original author's not a facilities
    # leader. "Director: Workplace Services NA" type roles get caught.
    "workplace services",
    "workplace coordinator",
    "workplace experience",
    "workplace operations",
    "facilities manager",
    "facilities coordinator",
    "facilities specialist",
    "head of workplace",
    "vp of workplace",
    # Investigations — IC investigative analyst (Trust & Safety / Fraud
    # investigations). "trust and safety" already gated; this catches the
    # "Principal Investigations Analyst" form.
    "investigations analyst",
    "senior investigations",
    "investigations specialist",
    "investigations manager",
    # Developer Relations / DevRel — wrong function. the original author doesn't do
    # community/evangelism work even at exec level.
    "developer relations",
    "developer connections",
    "devrel",
    "head of developer relations",
    "vp of developer relations",
    "developer advocate",
    "developer evangelist",
    # Communications executive — wrong function (PR / Comms exec, not
    # the original author's tech / strategy lane).
    "chief communications",
    "vp of communications",
    "vp, communications",
    "vp communications",
    "head of communications",
    "director of communications",
    "communications director",
    "communications manager",
    "communications officer",  # catches "Chief Communications Officer (CCO)"
    "head of pr",
    "director of pr",
    "vp of pr",
    "public relations",
    # Backend / Frontend IC engineers (with space variants the existing
    # "backend engineer" / "frontend engineer" missed).
    "back end engineer",
    "back-end engineer",
    "front end engineer",
    "front-end engineer",
    # Security IC analyst — already gated "security engineer", "risk
    # analyst", "grc analyst"; this catches "Senior Security Analyst".
    "security analyst",
    "third party risk",
    "third-party risk",
    "vendor risk analyst",
    # Incident Response IC — security ops, IC.
    "incident response",
    "incident response engineer",
    "incident response analyst",
    # Client Success Manager — CSM IC (mirror of "customer success manager"
    # which was already gated; some companies say "client" instead).
    "client success manager",
    "client success specialist",
    "client success associate",
    # Event Specialist — IC events. "events manager" already gated; this
    # catches the "specialist" form including the (Short Term) variant.
    "event specialist",
    "events specialist",
    # Database Engineer / DBA — IC database administration, wrong function.
    "database engineer",
    "database administrator",
    "dba ",                    # trailing-space avoids matching "DBAs"-like words
    # ---- additions ---------------------------------------------
    # Portfolio Management at sub-VP — SeatGeekIQ-style investment/asset
    # portfolio ops. the original author's M&A-adjacent work is captured by the
    # ma_gaming_media modifier; sub-VP portfolio ops is a different function.
    # Narrow: gate specific sub-VP phrasings rather than bare "portfolio
    # management" (which could be legit in a PE context).
    "senior manager, portfolio management",
    "senior manager portfolio management",
    "portfolio management analyst",
    "portfolio management associate",
    "portfolio management specialist",
    # Workforce Management / Workforce Planning — HR-adjacent function,
    # wrong fit for the original author's tech/strategy lane.
    "workforce manager",
    "workforce management",
    "workforce planning",
    "workforce analyst",
    "workforce coordinator",
    "senior manager, workforce",
    "senior manager workforce",
    # CRM Marketing / Lifecycle Marketing — D2C marketing exec, wrong
    # function. "Director, CRM Marketing" wasn't caught by "director of
    # marketing" due to punctuation/phrasing.
    "crm marketing",
    "director, crm",
    "director of crm",
    "crm manager",
    "crm specialist",
    "lifecycle marketing",
    # BI Analyst — IC business intelligence, wrong function. Sibling of
    # "business analyst" / "data analyst" which are already gated.
    "bi analyst",
    "business intelligence analyst",
    "bi developer",
    "bi engineer",
    # Acquisition Marketing — D2C / performance-marketing pivot-away lane.
    # Catches "BI Analyst, Acquisition Marketing", "Senior Manager,
    # Acquisition Marketing Analytics", etc.
    "acquisition marketing",
    "user acquisition manager",
    "growth acquisition",
    # Recruiting Operations — HR ops IC, wrong function.
    "recruiting operations",
    "recruiting ops",
    "sourcing operations",
    "sourcing ops",
    "recruitment operations",
    # Finance leadership — wrong function (the original author is NOT a finance exec)
    "head of finance",
    "vp of finance",
    "vp finance",
    "vp, finance",
    "chief financial",
    "cfo",
    "finance director",
    "director of finance",
    "controller",
    "head of tax",
    "head of audit",
    # Sales leadership — wrong function
    "head of sales",
    "vp of sales",
    "vp sales",
    "vp, sales",
    "chief sales",
    "chief revenue",
    "director of sales",
    "sales director",
    # Customer exec — wrong function
    "chief customer",
    "head of customer success",
    "vp of customer success",
    # Creative / art leadership — wrong function
    "creative director",        # pure IC-lean creative leadership
    "chief creative",
    "creative officer",
    # IC product management (NOT product leadership) — the original author targets VP Product,
    # Head of Product, Chief Product — not Principal/Senior PM which is IC.
    "senior product manager",
    "principal product manager",
    "staff product manager",
    "lead product manager",
    "associate product manager",
    "product manager,",        # "Product Manager, X" phrasing
    "technical program manager",
    "senior tpm",
    # IC design (UX, visual, motion — not design leadership)
    "product designer",
    "ux designer",
    "ui designer",
    "visual designer",
    "graphic designer",
    "motion designer",
    "user researcher",
    # IC artist / creative roles — categorically wrong-function
    "3d artist",
    "technical artist",
    "concept artist",
    "environment artist",
    "character artist",
    "vfx artist",
    "lighting artist",
    "animator",                # IC animator role
    "art director",            # gaming art director is IC-lean; NOT in exceptions
    "producer",                # bare IC producer — "executive producer" is in exceptions
    "senior producer",         # (redundant w/ "producer" but explicit)
    "game producer",
    "brand designer",
    "content designer",
    "senior designer",
    # Finance IC / sub-VP finance roles — wrong function
    "fp&a",
    "financial planning & analysis",
    "financial planning and analysis",
    "finance business partner",
    "senior accountant",
    "staff accountant",
    "controller,",             # "Senior Controller, X" — not CFO
    "tax manager",
    "treasury analyst",
    "financial analyst",
    # HR / People / Recruiting IC — wrong function
    "talent sourcing",
    "talent sourcer",
    "technical sourcer",
    "talent acquisition partner",
    "recruiter,",              # "Recruiter, X" — IC recruiter
    "senior recruiter",
    "people partner",
    "people business partner",     # Roblox/Google term for HRBP
    "compensation business partner",
    "benefits business partner",
    "business partner",            # catches all "X Business Partner" HR/Finance roles
    "hr business partner",
    "hrbp",
    "people operations",
    "compensation analyst",
    "benefits analyst",
    "employee experience",
    # Executive assistant / admin support — wrong function
    "executive assistant",
    "executive business partner",   # Google/Roblox term for senior EA
    "administrative assistant",
    "office of the ceo",            # often EA / chief-of-staff-light role
    "office manager",
    # Marketing IC / brand IC (not marketing leadership)
    "brand manager",
    "social media manager",
    "content marketing manager",
    "community manager",
    "growth marketer",
    "performance marketing manager",
    # Customer success / support IC
    "customer success manager",
    "customer success specialist",
    "support specialist",
    "implementation specialist",
    "onboarding specialist",
    # Legal / compliance IC
    "counsel",                 # "in-house counsel", "general counsel", etc.
    "attorney",
    "paralegal",
    # Policy / government relations (not the original author's function)
    "public policy",
    "government relations",
    "regulatory affairs",
    # Law enforcement operations (Trust & Safety investigative IC)
    "law enforcement",
    # Sales IC (not sales strategy/leadership)
    "sales lead",
    "sales representative",
    "sales associate",
    "agency sales",
    "account executive",
    "account manager",
    "business development representative",
    "bdr ",
    "sdr ",
    # Low-level / contract IC
    "human evaluator",
    "content evaluator",
    "content moderator",
    "engagement representative",
    "technical support specialist",
    # Production / project IC (not production leadership)
    "production assistant",
    "associate producer",
    "production coordinator",
    # ---- additions ----
    # The Apify LinkedIn scraper surfaced ~58 jobs from broad keyword searches;
    # the patterns below catch the dominant noise types it returned. Each was
    # observed in the live Phase-6 smoke run.
    #
    # Healthcare / clinical IC — the original author is not in healthcare. Catches "Patient
    # Care Coordinator", "Patient Access Specialist", "Provider Enrollment
    # Specialist", "Medical Records Coordinator", "Medical Editor", "VA Health
    # System Specialist", "Evening HUC/Registrar LDRP".
    "patient care",
    "patient access",
    "provider enrollment",
    "medical records",
    "medical editor",
    "medical coding",
    "clinical coordinator",
    "clinical specialist",
    "health system specialist",
    "huc/registrar",
    "huc registrar",
    # Healthcare leadership at non-target companies (the original author's industry list
    # excludes pure healthcare). "Director, Operational Performance" at
    # VytlOne (a healthcare ops firm) is the canonical example.
    "operational performance",
    # Sales IC / call center — outbound sales operator roles
    "appointment setter",
    "inbound/outbound",
    "inbound sales",
    "outbound sales",
    # Aviation / transportation IC
    "aviation analyst",
    "aviation specialist",
    "airline analyst",
    # Field / police / inspector — civic operations IC
    "field inspector",
    "police department",
    "police officer",
    # Construction / estimating — out-of-industry IC
    "estimating coordinator",
    "estimator,",
    "senior estimator",
    "construction project manager",
    "construction manager",
    # Education / teaching IC — the original author is not in K-12
    "teaching jobs",
    "elementary teacher",
    "school teacher",
    "lecturer",
    # Help desk / IT support IC — already partially covered by "it support"
    "help desk",
    "service desk",
    "tririga",            # IBM TRIRIGA = facilities mgmt; appeared as IT support role
    # Logistics / supply chain IC + leadership at non-gaming
    "supply chain analyst",
    "supply chain specialist",
    "logistics coordinator",
    "logistics analyst",
    # Procurement IC + sub-VP procurement — the original author is not procurement
    "procurement specialist",
    "procurement analyst",
    "vp procurement",
    "vp of procurement",
    "vice president procurement",
    "vice president of procurement",
    "head of procurement",
    "director of procurement",
    "director, procurement",
    # Finance leadership — full-word "vice president" forms (the existing
    # "vp finance" / "vp of finance" gates didn't catch "Vice President
    # Finance" / "Senior Vice President Finance" / "SVP Finance").
    "vice president finance",
    "vice president of finance",
    "senior vice president finance",
    "senior vice president of finance",
    "svp finance",
    "svp, finance",
    "svp of finance",
    "evp finance",
    "evp, finance",
    "evp of finance",
    # HR / People — full-word forms + bare "HR Manager" + typo guards
    "hr manager",
    "senior hr manager",
    "people operations manager",
    "talent aquisition",          # NOTE typo — sometimes posted as "Aquisition"
    "people business associate",
    "global support manager",
    # Asset Management at non-PE/non-VC firms — typically real estate or
    # commercial asset ops, NOT the original author's gaming-VC-PE lane (which uses
    # "operating partner", "managing director", etc.).
    # WARNING: don't gate bare "asset management" — Konvoy / Bitkraft etc.
    # do legitimate asset-mgmt work for gaming portfolios. Gate the specific
    # IC/sub-exec phrasings that appeared in the noise.
    "vp of asset management",
    "vp, asset management",
    "asset management analyst",
    "asset management associate",
    "asset management specialist",
    # Pure operations coordinator — IC ops, not COO-track. The function
    # gate already catches many "X coordinator" phrasings; "operations
    # coordinator" + "administrative associate" surfaced in Phase-6.
    "operations coordinator",
    "administrative associate",
    "administrative coordinator",
    # IT contractor support roles (e.g. "Help Desk Support with IBM TRIRIGA")
    "help desk support",
    "desktop support",
    # Catch-all noise titles from Phase-6 that don't fit elsewhere
    "national account manager",   # outside sales IC
    "exhibit designer",            # 3D / museum exhibit design IC
    "store designer",              # retail design IC
    "good store designer",         # observed verbatim
    "narrative designer",          # game writing IC — wrong fn even at Riot
    # Bare scrum / project IC titles (the "project manager," form was already
    # gated; these catch the bare title without comma).
    "product owner",
    # ---- FanDuel-pattern HR/comp/inclusion/analytics ----
    # The FanDuel CRM/Greenhouse scrape was producing 19/20 of the top-20
    # results from FanDuel — heavily weighted toward Marketing Sciences,
    # Compensation, DEI, Commercial Analytics, Casino Analytics. None of
    # these are the original author's lane. Adding gate kws by category.
    #
    # Compensation leadership / IC (the original author is NOT a comp exec)
    "director of compensation",
    "director, compensation",
    "compensation director",
    "head of compensation",
    "vp of compensation",
    "vp, compensation",
    "vp compensation",
    "senior director, compensation",
    "senior director of compensation",
    "compensation & benefits",
    "compensation and benefits",
    "compensation manager",
    "compensation specialist",
    # DEI leadership / IC (wrong function for the original author even at exec level)
    "head of inclusion",
    "director of inclusion",
    "director, inclusion",
    "vp of inclusion",
    "vp, inclusion",
    "diversity equity inclusion",
    "diversity, equity",
    "diversity & inclusion",       # already had "dei " w/ space; this catches phrases
    "diversity and inclusion",
    "head of diversity",
    "vp of diversity",
    "chief diversity",
    "inclusion specialist",
    "inclusion manager",
    # People leadership — comma forms missed by "vp of people"
    "vp, people",
    "vp people",
    # Commercial Analyst (FanDuel "Commercial Senior Analyst" pattern)
    "commercial analyst",
    "commercial senior analyst",
    "senior commercial analyst",
    "commercial operations analyst",
    # Analytics leadership — the original author is tech/strategy, not analytics-track exec
    # Catches FanDuel "Analytics Senior Director, Casino" pattern
    "head of analytics",
    "vp of analytics",
    "vp, analytics",
    "vp analytics",
    "director of analytics",
    "director, analytics",
    "analytics director",
    "analytics senior director",
    "senior director, analytics",
    "senior director of analytics",
    "analytics manager",
    "senior analytics manager",
    # Casino-specific operator titles (FanDuel/iGaming) — sub-VP casino ops,
    # not the original author's lane.
    "casino analyst",
    "casino manager",
    "casino operations",
    # ---- b: post-rescore audit caught more FanDuel-shape patterns ----
    # The first batch used VP-first word order (e.g. "vp marketing").
    # FanDuel + many other big-co JDs use FUNCTION-first word order:
    # "Marketing Sciences Vice President", "Algorithmic Trading Senior Manager".
    # The patterns below catch the function-suffix forms.
    #
    # Marketing Sciences / Marketing Tech (FanDuel-canonical noise)
    "marketing sciences",
    "marketing science",
    "marketing technology",
    "marketing automation",
    "marketing operations",
    "marketing analyst",           # already had — keeping explicit for clarity
    "marketing data",
    # Function-suffix VP forms (match either word-order)
    # These specifically pair the suffix "vice president" / "senior vice
    # president" with a non-the original author function. We don't gate bare "vice
    # president" because real the original author targets use that title.
    "marketing vice president",
    "marketing senior vice president",
    "human resources vice president",
    "finance vice president",
    "compensation vice president",
    "people vice president",
    # Insights / Research IC + sub-VP (FanDuel-canonical Consumer Insights)
    "consumer insights",
    "customer insights",
    "insights analyst",
    "insights manager",
    "insights senior",
    "research analyst",
    "market research analyst",
    # Bare "X Analyst" / "X Senior Analyst" forms for noise functions.
    # Avoid bare "analyst" alone (catches legit VP Analyst leadership).
    "product analyst",
    "automation analyst",
    "trading analyst",
    "trading senior",              # "Algorithmic Trading Senior Manager"
    "algorithmic trading",
    # IC events / VIP / hospitality (FanDuel-canonical noise)
    "vip host",
    "vip events",
    "vip associate",
    "vip account",                 # "VIP Account Manager" / "VIP Account Specialist"
    "events associate",
    "events specialist",           # already had — keeping
    "events coordinator",
    "events manager",              # already had — keeping
    # CRM operations IC (FanDuel-shape — different from CRM marketing)
    "crm operations",
    "crm associate",
    "crm analyst",
    # Operational Excellence — Six Sigma / IC ops mgr, not COO
    "operational excellence",
    # Commercial Strategy at sub-VP (catches "Commercial Strategy Manager,
    # Pokerstars" — IC strategy ops, not the original author's strategy lane).
    "commercial strategy manager",
    "commercial strategy associate",
    "commercial strategy analyst",
    "commercial strategy senior",
    # Responsible Gaming / iGaming compliance/operator IC — these are
    # sportsbook-specific compliance/ops roles, not the original author's lane.
    "responsible gaming",
    "responsible gambling",
    # Accounting IC (bare "accountant" + GL/AP variants — already had
    # "senior accountant", "staff accountant"; this catches the bare form).
    "accountant",
    "general ledger accountant",
    "gl accountant",
    "ap accountant",
    "ar accountant",
    "accounting manager",
    "accounting associate",
    "accounting specialist",
    # IT IC — bare "systems administrator" wasn't matched by "system
    # administrator" (existing gate); add singular-form variant.
    "systems administrator",
    # IC PM with hyphen forms (existing gate matched "X PM, Y" but not
    # "X PM - Y"). Catches "Data Product Manager - Machine Learning".
    "data product manager",
    # Performance & Insights — D2C-shape FanDuel exec
    "performance & insights",
    "performance and insights",
    # Trading IC (sportsbook trading desk — IC ops, not the original author's lane)
    "trading manager",
    "trading senior manager",
    "trading associate",
    # Pokerstars-specific operator IC (FanDuel acquired Pokerstars)
    "pokerstars",                  # If "Pokerstars" appears in title it's
                                   # FanDuel iGaming product ops. NOT a hard
                                   # gate by itself — but "Commercial Strategy
                                   # Manager, Pokerstars" is exactly the
                                   # FanDuel sub-VP shape. Risky? the original author could
                                   # legit target VP Product Pokerstars. Keep
                                   # this only if no false positives in audit.
    # Sub-VP creator/community IC (catches Roblox/etc Creator Ops sub-VP)
    "creator operations",
    "community operations",
    # ---- additions -------------------------
    # the original author explicit direction: "showing a lot of risk and compliance or
    # cybersecurity roles, etc, that I'm not interested in or qualified for."
    # These functions are categorically out of the original author's lane at EVERY level,
    # including C-suite. Previous rounds had partial coverage at IC level
    # (compliance manager, risk analyst, security engineer) but the EXEC
    # forms were either missing or in the exceptions list. Fixing both.
    #
    # --- RISK (leadership forms, not just IC) ---
    "chief risk",
    "chief risk officer",
    "cro,",                        # "Chief Risk Officer (CRO)" comma form
    "vp risk",
    "vp, risk",
    "vp of risk",
    "head of risk",
    "director of risk",
    "director, risk",
    "risk director",
    "senior director, risk",
    "senior director of risk",
    "risk management",
    "enterprise risk",
    # NOTE: bare "risk" alone is too broad (catches "risk management" in
    # a JD reference); gate only the leadership/function phrasings.
    # --- COMPLIANCE (leadership forms) ---
    "chief compliance",
    "chief compliance officer",
    "vp compliance",
    "vp, compliance",
    "vp of compliance",
    "head of compliance",
    "director of compliance",
    "director, compliance",
    "compliance director",
    "senior director, compliance",
    "senior director of compliance",
    "regulatory compliance",
    # --- GOVERNANCE / GRC ---
    "governance risk",
    "governance, risk",
    "governance and risk",
    "grc director",
    "grc manager",
    "director of governance",
    "head of governance",
    "vp of governance",
    # --- AUDIT (leadership + IC beyond what 'audit analyst' covered) ---
    "chief audit",
    "chief audit executive",
    "internal audit",
    "audit director",
    "director of audit",
    "director, audit",
    "vp of audit",
    "vp audit",
    "head of audit",            # already had as finance exec — duplicate-safe
    "audit manager",
    "senior audit",
    "audit senior",
    "staff auditor",
    "senior auditor",
    "it audit",
    # --- FRAUD (beyond the 'fraud manager' / 'fraud analyst' already gated) ---
    "head of fraud",
    "vp of fraud",
    "vp fraud",
    "director of fraud",
    "director, fraud",
    "fraud director",
    "fraud prevention",
    "fraud strategy",
    "financial crimes",
    "anti-money laundering",
    "anti money laundering",
    # --- CYBERSECURITY / INFOSEC (leadership + IC) ---
    # the original author is a tech exec; cybersecurity is a different career track. Gate
    # it at every level. NOTE: "chief security", "vp of security", "head of
    # security", "director of security" are REMOVED from _FUNCTION_GATE_EXCEPTIONS
    # below and added here instead.
    "ciso",                         # removed from _FUNCTION_GATE_ACRONYM_EXCEPTIONS
    "chief information security",
    "chief security officer",
    "chief security",               # re-added here; removed from exceptions
    "vp of security",
    "vp, security",
    "vp security",
    "head of security",
    "director of security",
    "director, security",
    "security director",
    "cybersecurity",
    "cyber security",
    "head of cybersecurity",
    "vp of cybersecurity",
    "director of cybersecurity",
    "director, cybersecurity",
    "information security",
    "infosec",
    "security operations",          # already partial coverage via 'security operations engineer'
    "soc analyst",
    "soc manager",
    "security analyst",             # already had in , keeping explicit
    "cyber threat",
    "threat intelligence",
    "appsec",
    "application security",
    "product security",
    "cloud security",
    "network security",
    "endpoint security",
    "vulnerability management",
    "penetration tester",
    "pen tester",
    "red team",
    "blue team",
    "security architect",
    "iam engineer",
    "identity and access management",
    "identity & access management",
    # --- SUPPLY CHAIN / PROCUREMENT leadership (already had IC coverage) ---
    "supply chain director",
    "director of supply chain",
    "head of supply chain",
    "vp of supply chain",
    "vp supply chain",
    # --- COMPLIANCE / POLICY VARIANTS that slipped past ---
    "policy director",
    "director of policy",
    "director, policy",
    # Algorithmic / quant IC roles
    "quant analyst",
    "quant trader",
    "quantitative analyst",
    # NOTE: deliberately NOT gating bare "Project Manager" / "Program Manager"
    # because Director/VP-of-PMO titles legit overlap. The function gate
    # already catches "senior project manager" + "project manager,X" forms.
    # Project Manager IC — already gated "project manager," with comma form;
    # but the bare title at non-target companies (construction / energy /
    # commercial renewable) is also IC. Add specific phrasings.
    "renewable energy project",
    # Digital Transformation directors at random non-tech firms — the original author's
    # background fits, but Phase-6 surfaced "Proximal Energy" type firms
    # which are out-of-industry. Without industry context this is hard to
    # gate; left unfiltered (will simply rely on the industry/company score
    # producing a low total).
    # ---- c: post-rescore audit (final tightening pass) -------------
    # FanDuel's job board has 200+ postings; many score 70+ from
    # industry/company modifiers even when title is sub-VP IC. Patterns
    # below catch FanDuel-shape noise without breaking real the original author targets.
    #
    # Change management IC + sub-VP
    "change manager",
    "change lead",
    "change management",
    "change analyst",
    # Customer Marketing / Customer Engagement (D2C-shape FanDuel noise).
    "customer marketing",
    "customer engagement",
    "customer experience manager",
    "customer experience associate",
    # Talent Management — HR-adjacent IC/sub-VP (not the original author's lane)
    "talent management",
    "talent manager",
    "talent operations",
    "talent specialist",
    "talent partner",
    "talent associate",
    # Bare "Product Manager" without exec qualifier = IC. the original author's targets
    # (Head/VP/Director of Product, Group PM) all pass via exceptions.
    "product manager",
    # Project / program management IC at sub-VP (PMO leadership exceptions
    # remain — director of operations etc).
    "project management associate",
    "project management senior associate",
    "project management specialist",
    "project management coordinator",
    "project coordinator",
    # Finance IC — "finance director" / "head of finance" already gated; this
    # adds the manager / IC forms below it.
    "finance manager",
    "senior finance manager",
    # Release / Technical Release IC (FanDuel "Technical Release Specialist")
    "release specialist",
    "release manager",
    "release engineer",
    "release coordinator",
    "technical release",
    # MarTech / QA IC
    "martech",
    "marketing tech",
    "qa associate",
    "qa specialist",
    "qa coordinator",
    "qa analyst",
    "quality associate",
    # Workplace operations Director — already gated "head of workplace" +
    # "vp of workplace"; this catches Director-of-Workplace.
    "director of workplace",
    "director, workplace",
    "north america workplace",
    "workplace director",
    # Payments Strategy IC (D2C pivot-AWAY lane — d2c_in_title modifier
    # already PENALIZES "payments" but doesn't HARD gate; add IC analyst
    # forms here so they fully gate to score 0).
    "payments strategy",
    "payments analyst",
    "payments associate",
    "payments operations",
    # Discovery & Engagement / Performance & Insights — D2C-shape sub-VP
    # at FanDuel (the "performance & insights" was added in 8b).
    "discovery & engagement",
    "discovery and engagement",
    # AI / ML Architect IC — senior-IC technical roles, not the original author's exec
    # lane. "Principal AI Architect" at FanDuel is IC, NOT Chief AI Officer.
    "ai architect",
    "ml architect",
    "principal ai",
    "principal ml",
    # Inclusion Associate / DEI IC at sub-VP (8a caught the leadership;
    # this catches the IC associate-level form).
    "inclusion associate",
    "inclusion coordinator",
    "inclusion analyst",
    # ---- d: final mid-band cleanup pass -----------------------------
    # After c the top-12 is mostly real the original author-fit. These patterns
    # catch the remaining 13-30 mid-band FanDuel/Take-Two noise.
    "operations excellence",       # "Operations Excellence Senior Analyst"
    "media associate",
    "media manager",               # IC media (FanDuel; not "head of media")
    "media specialist",
    "media coordinator",
    "media planner",               # already had — keeping
    "media buyer",
    "vip team",                    # "VIP Team Manager"
    "vip manager",
    "vip specialist",
    "vip coordinator",
    "procurement operations",      # "Associate, Procurement Operations"
    "senior associate, commercial",  # FanDuel "Senior Associate, Commercial Strategy"
    "associate, commercial",
    "acquisition strategy",        # FanDuel D2C acquisition
    "workplace manager",           # "Senior Workplace Manager"
    "senior workplace manager",
    "global compensation",         # "Senior Manager, Global Compensation"
    "qa tester",                   # IC QA testing
    "language development",        # "Language Development Supervisor" — IC L10N
    "brand strategy manager",      # IC brand strategy at sub-VP
    "brand strategy associate",
    "brand strategy specialist",
    "brand specialist",
    "brand coordinator",
    # ---- e: post-rescore final outliers --------------------------------
    # Bare 'Analyst' as a full title (FanDuel posted a role literally titled
    # "Analyst" that scored 83 from company/industry modifiers). Substring-safe:
    # "analyst" is NOT inside "analytics", and exec analytics titles already
    # gated separately in a (Director of Analytics, etc).
    "analyst",
    # FanDuel D2C "Customer Growth Associate"
    "growth associate",
    # Rockstar "Strategy Operations Associate" — IC associate at sub-VP
    "operations associate",
    # Roblox "Ops Specialist - Temporary"
    "ops specialist",
    # Take-Two "Legal Assistant / Business Affairs Specialist" — legal IC
    "business affairs",
    "legal assistant",
]

# Even if a title contains a function-gate keyword, these override the gate —
# they indicate a leadership / executive role that the original author could legitimately hold.
#
# IMPORTANT: These match EARLIER than the gate keywords via simple substring
# check. Use only phrases that won't appear inside unrelated words (e.g.,
# "cto" would match "direCTOr" — so bare acronyms are NOT used here; they
# are checked separately via _FUNCTION_GATE_ACRONYM_EXCEPTIONS with word
# boundaries).
_FUNCTION_GATE_EXCEPTIONS: list[str] = [
    # Engineering executive leadership — the original author's core
    # NOTE: "engineering manager" was INTENTIONALLY REMOVED.
    # It's rung-level management 2-4 steps below VP and was causing all
    # "Senior Engineering Manager, X" titles to pass through. Director/VP
    # of Engineering, Engineering Director, Head of Engineering, Chief
    # Engineer still pass via their more specific exceptions below.
    "director of engineering",
    "vp of engineering",
    "vp engineering",
    "vp, engineering",
    "head of engineering",
    "engineering director",
    "chief engineer",
    "chief technology",        # CTO always passes (full-phrase form)
    # Technology leadership (the original author's core)
    "vp of technology",
    "vp technology",
    "vp, technology",
    "head of technology",
    "director of technology",
    "technology director",
    # Security executive leadership REMOVED in 
    # the original author explicit direction — cybersecurity roles are categorically
    # out of his lane. CSO/CISO/VP Security/Director of Security now
    # gate as function-wrong, listed in _FUNCTION_GATE_KWS above.
    # Design executive leadership (strategic cross-functional)
    "design director",
    "director of design",
    "vp of design",
    "head of design",
    "chief design",
    # Product leadership — VP/Head/Chief Product per the real-career targets
    # (the original author explicitly targets VP Product / Head of Product at immersive companies).
    "chief product",
    "vp of product",
    "vp product",
    "vp, product",
    "head of product",
    "director of product",
    "product director",
    "group product manager",   # often a mgr-of-mgrs product leader
    # Program / operations leadership — Fractional COO is an explicit target
    "chief operating",
    "vp of operations",
    "vp operations",
    "vp, operations",
    "head of operations",
    "director of operations",
    "operations director",
    "chief of staff",           # senior strategic role the original author can target
    # Strategic / advisory executive leadership
    "chief strategy",
    "vp of strategy",
    "head of strategy",
    "strategic advisor",
    "operating partner",
    "executive advisor",
    # Platform / data leadership (tech exec, not IC data)
    "chief data",
    "vp of platform",
    "head of platform",
    "platform director",
    "director of platform",
    # Production / content leadership
    "executive producer",       # senior production leadership
    "showrunner",
    # General counsel is in-house legal exec — sometimes overlaps with M&A
    # the original author did during Take-Two / Zynga. Keep narrowly.
    "general counsel",
    # NOTE: Intentionally REMOVED from exceptions (these are wrong-function for the original author,
    # even at exec level): CMO / VP Marketing / Head of Marketing, CHRO / VP People /
    # Head of People / VP Talent / Head of HR, CFO / VP Finance / Head of Finance /
    # Finance Director, CRO / VP Sales / Head of Sales, Chief Customer, Creative
    # Director / Art Director. These will either pass silently (if no gate kw
    # matches) or get caught by the expanded IC kw list below.
]

# Acronym-only exceptions — require word boundaries (surrounding whitespace,
# punctuation, or start/end of string) so that "cto" doesn't match "Director".
# Checked via regex \b{acronym}\b. Only acronyms for TARGET functions are
# included — CMO/CFO/CHRO/CRO have been intentionally REMOVED (they're
# categorically wrong-function for the original author, even at exec level).
_FUNCTION_GATE_ACRONYM_EXCEPTIONS: list[str] = [
    "cto",   # Chief Technology Officer
    "coo",   # Chief Operating Officer
    "cpo",   # Chief Product Officer (also "Chief People Officer" — but the
             # Product meaning is always a target; if title is truly CPO-People,
             # other gate kws like "chief people" / "head of people" will still fire)
    # CISO REMOVED in cybersecurity is out-of-lane; see
    # "ciso" / "chief information security" in _FUNCTION_GATE_KWS above.
]
_ACRONYM_EXCEPTION_RE = re.compile(
    r"\b(?:" + "|".join(_FUNCTION_GATE_ACRONYM_EXCEPTIONS) + r")\b",
    re.IGNORECASE,
)

# Priority function-gate keywords — fire BEFORE any exception is checked.
# Use for titles where a target leadership prefix (e.g., "Director of Product")
# is paired with a categorically wrong-function scope (e.g., "Ads Performance").
# the original author is explicitly pivoting AWAY from D2C ads / performance-marketing work,
# so "Director of Product - Ads Performance" must gate even though "director
# of product" would otherwise exempt it.
_FUNCTION_GATE_PRIORITY_KWS: list[str] = [
    "ads performance",
    "ad performance",
    "performance ads",
    "performance advertising",
    "performance marketing",   # even at director+ level — pivot-away lane
    "ads product",             # "Director of Product - Ads" class
    # additions — all fire even when a the original author-target exception
    # (director of product, head of engineering, etc.) would otherwise
    # bypass. These are the D2C/performance-marketing lane the original author is
    # pivoting AWAY from per the real-career-pivot research.
    "user acquisition",        # "Senior Manager, Paid Social User Acquisition"
    "paid social",
    "paid media",
    "ads experience",          # "Tech Lead Manager - Ads Experience"
    "ads platform",
    "advertising experience",
    "advertising product",
]


# Diluting prefixes — when an _FUNCTION_GATE_EXCEPTIONS keyword (e.g.
# "design director", "director of product") is preceded in the title by one
# of these tokens, the exception is INVALIDATED and the title falls through
# to the regular gate check. Rationale: these prefixes downgrade what looks
# like an exec title into a senior-IC / lead-IC role.
#
# Examples this catches:
#   "Associate 3D Design Director"  → "associate" + "3d" both invalidate
#                                       "design director" exception → gates.
#   "Visual Design Director"        → "visual " invalidates → gates.
#   "Junior Director of Design"     → "junior " invalidates → gates.
#   "UX/UI Design Director"         → "ux/ui " invalidates → gates.
#
# Each entry is checked as a substring search inside the slice of the title
# that appears BEFORE the matched exception keyword. Prefix matches anchored
# with leading/trailing space minimize false positives ("designer" should
# not invalidate "directing strategy").
_FUNCTION_GATE_DILUTING_PREFIXES: list[str] = [
    "associate ",                # "Associate Director of Design" — IC senior
    "asst. ", "asst ",           # "Asst. Design Director"
    "assistant ",                # "Assistant Design Director"
    "junior ",                   # "Junior Director of Design"
    "jr ", "jr. ",
    "3d ",                       # "3D Design Director" — lead-IC scope
    "2d ",                       # "2D Design Director" — lead-IC scope
    "visual ",                   # "Visual Design Director" — IC senior craft
    "graphic ",                  # "Graphic Design Director"
    "interaction ",              # "Interaction Design Director"
    "ui ", "ui/ux ", "ux ", "ux/ui ",
    "motion ",                   # "Motion Design Director" — craft IC
    "set ",                      # "Set Design Director" — film/theater craft
    "costume ",                  # "Costume Design Director"
    "sound ",                    # "Sound Design Director" — audio craft
    "lighting ",                 # "Lighting Design Director"
]


def _exception_is_diluted(title_lo: str, exception_kw: str) -> bool:
    """True when an _FUNCTION_GATE_EXCEPTIONS match is preceded by a token
    that downgrades the apparent exec scope to lead-IC scope.

    This protects the gate from titles like "Associate 3D Design Director"
    where the substring "design director" technically matches the exec
    exception but the actual role is a senior-IC creative. The gate
    must fire on those — the original author doesn't take IC craft roles.
    """
    idx = title_lo.find(exception_kw)
    if idx <= 0:
        return False  # exception is at the start of the title, no prefix to dilute
    # Look at the slice before the exception. Either the whole title-up-to-here
    # contains a diluting prefix substring, OR the title literally starts with
    # one (e.g. "associate 3d design director" — "associate " is at the start).
    head = title_lo[:idx]
    for dilute in _FUNCTION_GATE_DILUTING_PREFIXES:
        if dilute in head:
            return True
    return False


def check_function(title: str) -> tuple[bool, str]:
    """Gate: job title indicates an IC role outside the original author's executive background.

    the original author's background: VP-level technology strategy, enterprise architecture,
    M&A integration, digital commerce leadership. He cannot credibly pursue IC
    software engineering, IC design, legal, public policy, or sales IC roles.

    Checked against TITLE only (not description) to avoid false positives.
    Leadership titles that contain function words (e.g., "VP of Engineering",
    "Director of Design") are explicitly excluded via _FUNCTION_GATE_EXCEPTIONS,
    UNLESS the exception is preceded by a "diluting" prefix like "Associate "
    or "3D " that downgrades the apparent exec scope to lead-IC scope (
    fix for "Associate 3D Design Director, Experience Design" landing at 77).

    Returns (fired: bool, name: "function").
    """
    lo = title.lower

    # 1. Priority gate — fires BEFORE exceptions. Use for phrases that must
    #    always gate even when the title includes a the original author-target leadership
    #    prefix (e.g., "Director of Product - Ads Performance").
    if any(kw in lo for kw in _FUNCTION_GATE_PRIORITY_KWS):
        return True, "function"

    # 2. Exceptions — legitimate leadership titles that happen to contain
    #    function-word substrings (e.g., "vp of product" contains "product").
    #    Highly specific, so they identify the original author's target shapes without
    #    swallowing wrong-function titles.
    #
    #    an exception is honored ONLY when no diluting
    #    prefix appears before it. "Director of Engineering" passes; "Associate
    #    Director of Engineering" falls through and is gated by the regular
    #    kws below. See _exception_is_diluted for the full prefix list.
    for exc in _FUNCTION_GATE_EXCEPTIONS:
        if exc in lo and not _exception_is_diluted(lo, exc):
            return False, "function"
    if _ACRONYM_EXCEPTION_RE.search(lo):
        # Acronym exceptions (CTO/COO/CPO) are never diluted — they're
        # always exec-scope titles. "Associate CTO" is not a real title.
        return False, "function"

    # 3. Regular gate kws — wrong-function titles that don't overlap with any
    #    the original author-target shape.
    if any(kw in lo for kw in _FUNCTION_GATE_KWS):
        return True, "function"

    # 4. sweep: catch lead-IC creative-craft titles that don't
    #    contain any of the explicit IC kws above but DO contain a diluted
    #    leadership phrase. "Associate 3D Design Director" — "design
    #    director" was diluted out of the exception, but no other kw fires,
    #    so without this sweep the title would pass the gate. Iterate the
    #    exceptions one more time and gate any that we just diluted.
    for exc in _FUNCTION_GATE_EXCEPTIONS:
        if exc in lo and _exception_is_diluted(lo, exc):
            return True, "function"

    return False, "function"


# ---------------------------------------------------------------------------
# Seniority gate
# ---------------------------------------------------------------------------

def check_seniority(title: str) -> tuple[bool, str]:
    """Fire if the job TITLE contains an intern/entry-level disqualifier.

    We only check the title (not the description) to avoid false positives
    where the JD says "no experience required" in a perk section, or uses
    "intern program" as a reference while advertising a real VP role.

    also fires on "Associate <noun>" where <noun> is NOT
    a real exec scope. Insurance / banking / consulting use "Associate Vice
    President" / "Associate Director" / "Associate Partner" as legitimate
    mid-exec titles, so those pass. But "Associate 3D Design Director",
    "Associate Producer", "Associate Marketing Manager" — all IC roles —
    must gate. The rule: if the title STARTS with "associate" AND none of
    the exec-passthrough markers below appear, gate it.

    Returns (fired: bool, name: "seniority").
    """
    lo_title = title.lower.strip
    # Standard YAML-driven kw check (intern / entry-level / junior / etc.)
    for kw in KW_SENIORITY_DISQ:
        if kw in lo_title:
            return True, "seniority"

    # "Associate <noun>" handling — see docstring.
    # Markers that mean "this Associate-prefixed title is a real exec role":
    #   "associate vice president", "associate vp", "associate director" at a
    #   bank/insurer (no scope qualifier), "associate partner" (consulting),
    #   "associate general counsel" (in-house legal exec).
    #
    # The fix here is conservative: pass through ONLY when one of those
    # explicit exec phrases appears. Anything else starting with "associate "
    # is treated as IC junior-level → gate.
    if lo_title.startswith("associate ") or lo_title.startswith("associate,"):
        exec_passthroughs = [
            "associate vice president",
            "associate vp",
            "associate partner",
            "associate general counsel",
            "associate principal",          # consulting/architecture senior IC-of-leaders
        ]
        # "Associate Director" alone (no scope qualifier) is a real mid-exec
        # title at insurance / pharma / banking. But "Associate 3D Design
        # Director" or "Associate Marketing Director" is an IC craft role.
        # Distinguish by checking whether ANY craft/scope qualifier precedes
        # "director".
        if "associate director" in lo_title:
            craft_qualifiers = [
                "3d", "2d", "visual", "graphic", "interaction", "ui", "ux",
                "motion", "set", "costume", "sound", "lighting", "creative",
                "art", "experience", "product design", "brand",
                "marketing", "events", "social", "content",
            ]
            after_associate = lo_title[len("associate "):]
            # If a craft qualifier sits between "associate" and "director",
            # the role is craft-IC, not exec.
            director_idx = after_associate.find("director")
            if director_idx >= 0:
                slice_before_director = after_associate[:director_idx]
                if not any(q in slice_before_director for q in craft_qualifiers):
                    return False, "seniority"  # passes — true Associate Director
                # Falls through to gate below.
            else:
                # "associate director" not actually present as a phrase
                pass

        if any(passthrough in lo_title for passthrough in exec_passthroughs):
            return False, "seniority"
        return True, "seniority"

    return False, "seniority"


# ---------------------------------------------------------------------------
# Engagement gate
# ---------------------------------------------------------------------------

def check_engagement(text: str) -> tuple[bool, str]:
    """Fire if the job text indicates unpaid or commission-only work.

    When fired, the formula multiplier engagement_gate = 0 → raw score = 0.

    Returns (fired: bool, name: "engagement").
    """
    fired = any_match(text, KW_ENG_DISQ)
    return fired, "engagement"


# ---------------------------------------------------------------------------
# Compensation gate
# ---------------------------------------------------------------------------

def check_compensation(
    salary_max: Optional[int],
    salary_min: Optional[int],
    text: str,
) -> tuple[bool, str]:
    """Fire if salary is explicitly stated AND is below the hard floor.

    The floor comes from gates.compensation.salary_floor in scoring.yaml.
    The Python default below is only the fallback used when the YAML is
    absent (test fixtures, malformed config). If salary is not listed,
    this gate does NOT fire — the compensation category handles the
    unlisted case as a neutral score.

    Returns (fired: bool, name: "compensation").
    """
    floor = GATES_CFG.get("compensation", {}).get("salary_floor", 175_000)

    # Use the higher of min/max as the reference (if a range is listed the
    # max tells us the role's upside, which is what matters most).
    ref_salary = salary_max if salary_max is not None else salary_min
    if ref_salary is None:
        # No salary data — gate does not fire.
        return False, "compensation"

    fired = ref_salary < floor
    return fired, "compensation"


# ---------------------------------------------------------------------------
# Geographic gate and score
# ---------------------------------------------------------------------------

def geographic_gate_and_score(
    job: dict,
) -> tuple[float, float, str]:
    """Compute both the gate multiplier and the geographic category score.

    Returns (gate_value, geo_score, geo_label) where:
      gate_value: 0.0 (hard gate), 0.5 (ambiguous soft penalty), 1.0 (pass)
      geo_score:  0-10 category score fed into the weighted sum
      geo_label:  human-readable description of what was detected
    """
    location: str  = (job.get("location") or "").lower
    remote:   bool = job.get("remote", False)
    desc:     str  = job.get("description", "") or ""
    title:    str  = job.get("title", "") or ""
    # Combine all searchable text for pattern matching.
    full_text = f"{title} {desc} {location}"

    # --- 1. Relocation required → hard gate (0) ---
    if regex_match(full_text, LOC_RELOCATION_RE):
        return 0.0, LOC_SCORES.get("international", 2), "relocation_required"

    # --- 1.5. International location embedded in the job TITLE → hard gate ---
    # When a geographic region is appended to a job title (e.g.,
    # "Director of Public Policy, UAE/Middle East"), the role almost always
    # requires physical presence in that region. Gate it unless the job is
    # explicitly remote/US-based.
    # We check the title only (not the full description) to avoid false
    # positives from JDs that mention international markets as scope.
    _INTL_TITLE_KWS = [
        "uae", "dubai", "abu dhabi", "middle east", "saudi arabia", "ksa",
        " apac", " emea", " latam",   # space-prefixed: avoid "template", etc.
        "singapore", "tokyo", "japan", "australia", "sydney",
        "germany", "berlin", "amsterdam", "paris", " uk,", " london,",
    ]
    title_lo = title.lower
    if any(kw in title_lo for kw in _INTL_TITLE_KWS):
        if not (remote or regex_match(full_text, LOC_REMOTE_RE)):
            return 0.0, LOC_SCORES.get("international", 2), "international_title"

    # --- 2. Remote anywhere-in-US → perfect geographic fit ---
    if remote or regex_match(full_text, LOC_REMOTE_RE):
        return 1.0, LOC_SCORES.get("remote_us", 10), "remote_us"

    # --- 3. NJ-based office → very commutable from Mountain Lakes ---
    if regex_match(full_text, LOC_NJ_RE):
        return 1.0, LOC_SCORES.get("nj_office", 9), "nj_office"

    # --- 4. NYC hybrid or NYC in-office ---
    if regex_match(full_text, LOC_NYC_RE):
        # Distinguish heavy in-office (4-5 days) from hybrid.
        if regex_match(full_text, LOC_HEAVY_OFFICE_RE):
            return 1.0, LOC_SCORES.get("nyc_heavy", 6), "nyc_heavy_office"
        return 1.0, LOC_SCORES.get("nyc_hybrid_3d", 8), "nyc_hybrid"

    # --- 5. Out-of-area city with required presence → hard gate (0) ---
    if regex_match(full_text, LOC_OUT_OF_AREA_RE):
        return 0.0, LOC_SCORES.get("international", 2), "out_of_area_required"

    # --- 6. Location field mentions a US location but no remote info ---
    us_location_terms = [
        "united states", "usa", ", ca", ", ny", ", wa", ", tx",
        ", ma", ", il", ", ga", "remote",
    ]
    if any(t in location for t in us_location_terms):
        return 1.0, LOC_SCORES.get("ambiguous", 4), "us_unspecified"

    # --- 7. Truly ambiguous — could be international, could be remote ---
    return 0.5, LOC_SCORES.get("ambiguous", 4), "ambiguous"


# ---------------------------------------------------------------------------
# Convenience: run all hard gates in one call
# ---------------------------------------------------------------------------

def evaluate_all_gates(job: dict, text: str) -> tuple[list, float, float, float]:
    """Run every gate check and return a combined result.

    Returns:
      hard_gates_triggered: list of gate name strings (empty = all clear)
      geographic_gate_value: 0.0, 0.5, or 1.0 (formula multiplier)
      engagement_gate_value: 0.0 or 1.0 (formula multiplier)
      geo_score:             0-10 geographic category score
    """
    hard_gates: list[str] = 

    # Seniority (checked on title only).
    seniority_fired, seniority_name = check_seniority(job.get("title", ""))
    if seniority_fired:
        hard_gates.append(seniority_name)

    # Function (checked on title only) — IC engineering, legal, policy, etc.
    function_fired, function_name = check_function(job.get("title", ""))
    if function_fired:
        hard_gates.append(function_name)

    # Compensation (needs salary fields from job dict).
    comp_fired, comp_name = check_compensation(
        job.get("salary_max"),
        job.get("salary_min"),
        text,
    )
    if comp_fired:
        hard_gates.append(comp_name)

    # Engagement / unpaid (checked on full text).
    eng_fired, eng_name = check_engagement(text)
    engagement_gate_value = 0.0 if eng_fired else 1.0
    if eng_fired:
        hard_gates.append(eng_name)

    # Geographic — returns both a gate multiplier AND a category score.
    geo_gate_value, geo_score, _label = geographic_gate_and_score(job)
    if geo_gate_value == 0.0:
        hard_gates.append("geographic")

    return hard_gates, geo_gate_value, engagement_gate_value, geo_score
