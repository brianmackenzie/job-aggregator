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

"""
src/scoring/candidate_profile.py — the original author-specific candidate profile.

Source of truth for WHO the original author is as a candidate. This file is deliberately
plain-Python (no YAML) because the scoring refactor stopped
treating the keyword lists as a bag-of-weights. Instead they fall into
three clearly named categories that match how the new algo_prefilter +
Haiku semantic layer actually use them:

    1. HARD_DISQUALIFIERS  — any match kills the job before semantic.
                             No weighting. No blend. Binary auto-reject.
    2. SOFT_WARNINGS       — flag for Haiku to weigh. Never auto-kill.
                             Haiku decides whether the role is still worth it.
    3. POSITIVE_SIGNALS    — diagnostic flagging only. NOT for score math.
                             Used to surface "why did this rank high?" in
                             UI / markdown export and to help Haiku's prompt.

Also preserved (unchanged from config/scoring.yaml):
    - COMPANY_INDUSTRY_MAP (company → industry bucket)
    - INDUSTRY_SCORES      (bucket → relative score, still useful for flags)
    - INDUSTRY_KEYWORDS    (fallback classifier keywords)
    - CRUNCH_COMPANIES     (studios with documented crunch culture)
    - HRC100_COMPANIES     (HRC Corporate Equality Index — LGBTQ flag)
    - COMMUTE_CITIES       (NYC + NJ ZIP of zones commutable from Mountain Lakes)
    - COMP_THRESHOLDS      (base+bonus floor/target/ceiling, USD)

Why it's Python and not YAML:
    the original author's direction on the refactor was explicit — "Python
    constants, not YAML, so that assertion-style calibration tests can
    import and diff the lists". The YAML file `config/scoring.yaml` is
    being kept for the weighted score math that algo_prefilter still
    performs as a diagnostic in the prefilter's internal `algo_score`
    output, but the CATEGORIES a job falls into (hard-disq vs soft-warn
    vs positive-signal) live here.

Size note:
    HARD_DISQUALIFIERS.titles.function is ~500 entries. Don't panic —
    almost every entry was observed in live Phase-6 / rescore audits
    (Rounds 4 through 9 of title-gate tuning). Each block has a comment
    explaining which audit round added it and why.
"""
from __future__ import annotations

# =============================================================================
# 1) HARD_DISQUALIFIERS — auto-kill before semantic (no weight, no blend).
# =============================================================================
#
# If any of these fire, the job is hard-rejected and Haiku is NOT called.
# The prefilter writes the reason into `prefilter_reason` so the UI /
# markdown export can explain WHY the row was skipped.
#
# Categories:
#   - WRONG_FUNCTION_TITLES : IC / sub-VP / wrong-function titles
#   - SUB_VP_SENIORITY      : intern, junior, entry-level, new-grad
#   - UNPAID_ENGAGEMENT     : commission-only, equity-only, unpaid
#
# Also:
#   - LEADERSHIP_EXCEPTIONS       : exec titles that EXEMPT from wrong-function
#                                    (e.g., "VP of Engineering" contains
#                                     "engineering" but isn't IC)
#   - LEADERSHIP_ACRONYM_EXCEPTIONS: CTO, COO, CPO (word-boundary match)
#   - PRIORITY_DISQUALIFIERS      : always-kill even when a leadership
#                                    exception would normally protect
#                                    (e.g., "Director of Product - Ads
#                                     Performance" must still kill because
#                                     of "ads performance")
#   - DILUTING_PREFIXES           : prefixes that INVALIDATE a leadership
#                                    exception (e.g., "Associate 3D Design
#                                     Director" — "associate" + "3d"
#                                     downgrade the exception to senior-IC).
# -----------------------------------------------------------------------------


# ---- WRONG_FUNCTION_TITLES --------------------------------------------------
# Titles indicating an IC or wrong-function role the original author cannot credibly target.
# Case-insensitive substring match against TITLE ONLY (not description).
# Ported verbatim from src/scoring/gates.py::_FUNCTION_GATE_KWS (rounds 1-9+).
HARD_DISQUALIFIER_TITLES_FUNCTION: list[str] = [
    # IC software / data / ML engineering
    "software engineer", "machine learning engineer", "ml engineer",
    "data engineer", "data scientist", "platform engineer",
    "security engineer", "firmware engineer", "site reliability engineer",
    "sre ", "devops engineer", "backend engineer", "frontend engineer",
    "full-stack engineer", "fullstack engineer", "mobile engineer",
    "ios engineer", "android engineer", "qa engineer", "test engineer",
    "cloud engineer", "infrastructure engineer", "computer vision engineer",
    "ai engineer", "applied scientist",
    # Gaming-specific IC engineer / programmer suffixes
    "engine programmer", "engine developer", "engine engineer", "core engine",
    "gameplay engineer", "gameplay programmer", "graphics engineer",
    "graphics programmer", "rendering engineer", "rendering programmer",
    "tools engineer", "tools programmer", "audio programmer",
    "network engineer", "network programmer", "applications engineer",
    "application engineer", "consumer apps engineer", "distinguished engineer",
    "principal developer", "senior developer", "lead developer",
    "lead engineer", "lead programmer", "principal programmer",
    "research engineer", "research scientist",
    # More engineer suffixes
    "quality engineer", "automation engineer", "framework engineer",
    "asset management engineer", "app store manager", "automation framework",
    "machine learning scientist", "ml scientist", "design engineer",
    "hardware engineer", "detection and response", "salesforce engineer",
    "servicenow", "system administrator", "principal engineer",
    "senior engineer", "staff engineer", "fellow engineer",
    "data center engineer", "data center", "analytics engineer",
    "software development engineer", "sdet", " sdet",
    "security operations engineer", "compensation engineer",
    "workday compensation", "sem analyst", "media planner",
    "knowledge manager", "community manager", "event manager",
    "events manager", "project manager,", "senior project manager",
    "supply chain manager", "logistics manager", "procurement manager",
    "payment risk", "risk operations", "fraud operations", "trust and safety",
    # IC analytics / data
    "data analyst", "risk analyst", "grc analyst", "business analyst",
    "people scientist", "people analytics", "senior machine learning",
    "senior ml ", "marketing analyst", "operations analyst",
    "intelligence analyst",
    # Content / copy IC
    "copywriter", "writer,", "senior writer", "technical writer",
    "content strategist",
    # HR leadership — wrong function even at exec level
    "total rewards", "head of hr", "head of human resources",
    "head of people", "vp of people", "vp of human resources",
    "chief people", "chief human resources", "chro",
    "head of talent", "vp of talent", "chief talent", "talent acquisition",
    "learning and coaching", "learning & coaching",
    "learning and development", "hr operations", "people operations",
    "employee relations", "diversity & inclusion", "dei ",
    # Marketing leadership — the original author is pivoting AWAY
    "head of marketing", "vp of marketing", "vp marketing", "vp, marketing",
    "chief marketing", "cmo", "director of marketing", "marketing director",
    "head of growth", "vp of growth", "user growth", "head of user growth",
    "vp of user growth", "growth manager", "growth marketing manager",
    "growth product manager", "growth analyst", "growth lead",
    "growth strategist",
    # Data Science leadership
    "data science", "head of data science", "vp of data science",
    "vp, data science", "director of data science", "data science director",
    # Corporate Development / Business Development sub-VP
    "corporate development senior associate", "corporate development associate",
    "corporate development analyst", "corporate development manager",
    "senior corporate development", "business development manager",
    "senior business development", "business development associate",
    "business development representative", "bd associate", "bd manager",
    "partnerships manager", "partnerships associate",
    "strategic partnerships manager",
    # additions
    "skillbridge", "general application",
    "engineering manager", "senior engineering manager",
    "principal engineering manager", "staff engineering manager",
    "machine learning engineering manager", "ml engineering manager",
    "marketing manager", "integrated marketing manager",
    "senior marketing manager", "product marketing manager",
    "digital marketing manager", "field marketing manager",
    "lifecycle marketing manager", "email marketing manager",
    "content design", "creative manager", "manager, content",
    "compliance manager", "compliance program manager", "senior compliance",
    "compliance analyst", "compliance officer,", "risk manager",
    "fraud manager", "fraud analyst", "aml analyst", "aml manager",
    "finance systems", "finance associate", "finance analyst",
    "senior finance associate", "senior finance analyst",
    "finance operations", "financial operations",
    # additions
    "government affairs", "public affairs", "policy specialist",
    "policy manager", "regional policy", "state government",
    "lobbying", "lobbyist", "trust & safety policy", "trust and safety policy",
    "director, promotions", "director of promotions", "head of promotions",
    "vp of promotions", "vp, promotions", "promotions manager",
    "senior director, promotions", "promotions specialist",
    "web engineer", "senior web engineer", "systems engineer",
    "senior systems engineer", "appsec engineer",
    "application security engineer", "it engineer", "senior it engineer",
    "it systems engineer", "platform reliability", "compliance senior manager",
    "localization program manager", "localization manager",
    "localization director", "localization specialist",
    "localization coordinator", "head of localization", "vp of localization",
    "creator growth", "creator growth operations", "growth operations manager",
    "growth operations", "operations and analytics", "operations & analytics",
    "analytics associate", "analytics operations",
    "learning design", "learning design & development",
    "learning design and development", "instructional designer",
    "instructional design", "training manager", "training specialist",
    "director of business development", "director, business development",
    "business development director",
    # additions
    "people business associate", "people associate", "business associate",
    "business operations associate", "ux researcher", "ux research",
    "user research manager", "it support", "it support engineer",
    "it support specialist", "support engineer", "workplace services",
    "workplace coordinator", "workplace experience", "workplace operations",
    "facilities manager", "facilities coordinator", "facilities specialist",
    "head of workplace", "vp of workplace", "investigations analyst",
    "senior investigations", "investigations specialist",
    "investigations manager", "developer relations", "developer connections",
    "devrel", "head of developer relations", "vp of developer relations",
    "developer advocate", "developer evangelist", "chief communications",
    "vp of communications", "vp, communications", "vp communications",
    "head of communications", "director of communications",
    "communications director", "communications manager",
    "communications officer", "head of pr", "director of pr", "vp of pr",
    "public relations", "back end engineer", "back-end engineer",
    "front end engineer", "front-end engineer", "security analyst",
    "third party risk", "third-party risk", "vendor risk analyst",
    "incident response", "incident response engineer",
    "incident response analyst", "client success manager",
    "client success specialist", "client success associate",
    "event specialist", "events specialist", "database engineer",
    "database administrator", "dba ",
    # additions
    "senior manager, portfolio management",
    "senior manager portfolio management", "portfolio management analyst",
    "portfolio management associate", "portfolio management specialist",
    "workforce manager", "workforce management", "workforce planning",
    "workforce analyst", "workforce coordinator",
    "senior manager, workforce", "senior manager workforce",
    "crm marketing", "director, crm", "director of crm", "crm manager",
    "crm specialist", "lifecycle marketing", "bi analyst",
    "business intelligence analyst", "bi developer", "bi engineer",
    "acquisition marketing", "user acquisition manager", "growth acquisition",
    "recruiting operations", "recruiting ops", "sourcing operations",
    "sourcing ops", "recruitment operations", "head of finance",
    "vp of finance", "vp finance", "vp, finance", "chief financial",
    "cfo", "finance director", "director of finance", "controller",
    "head of tax", "head of audit", "head of sales", "vp of sales",
    "vp sales", "vp, sales", "chief sales", "chief revenue",
    "director of sales", "sales director", "chief customer",
    "head of customer success", "vp of customer success",
    "creative director", "chief creative", "creative officer",
    "senior product manager", "principal product manager",
    "staff product manager", "lead product manager",
    "associate product manager", "product manager,",
    # NOTE "technical program manager" REMOVED from
    # HARD list and moved to SOFT_WARNING_TPM (below). the original author flagged
    # "Senior Technical Program Manager" titles at gaming dream-cos
    # being killed despite being legitimate program-leadership work.
    # "senior tpm" stays as a hard kill — that's clearly the IC TPM
    # acronym shape.
    "senior tpm", "product designer",
    "ux designer", "ui designer", "visual designer", "graphic designer",
    "motion designer", "user researcher", "3d artist", "technical artist",
    "concept artist", "environment artist", "character artist",
    "vfx artist", "lighting artist", "animator", "art director",
    "producer", "senior producer", "game producer", "brand designer",
    "content designer", "senior designer", "fp&a",
    "financial planning & analysis", "financial planning and analysis",
    "finance business partner", "senior accountant", "staff accountant",
    "controller,", "tax manager", "treasury analyst", "financial analyst",
    "talent sourcing", "talent sourcer", "technical sourcer",
    "talent acquisition partner", "recruiter,", "senior recruiter",
    "people partner", "people business partner",
    "compensation business partner", "benefits business partner",
    "business partner", "hr business partner", "hrbp",
    "compensation analyst", "benefits analyst", "employee experience",
    "executive assistant", "executive business partner",
    "administrative assistant", "office of the ceo", "office manager",
    "brand manager", "social media manager", "content marketing manager",
    "growth marketer", "performance marketing manager",
    "customer success manager", "customer success specialist",
    "support specialist", "implementation specialist", "onboarding specialist",
    "counsel", "attorney", "paralegal", "public policy",
    "government relations", "regulatory affairs", "law enforcement",
    "sales lead", "sales representative", "sales associate",
    "agency sales", "account executive", "account manager",
    "bdr ", "sdr ", "human evaluator", "content evaluator",
    "content moderator", "engagement representative",
    "technical support specialist", "production assistant",
    "associate producer", "production coordinator",
    # additions (Phase-6 LinkedIn noise audit)
    "patient care", "patient access", "provider enrollment",
    "medical records", "medical editor", "medical coding",
    "clinical coordinator", "clinical specialist",
    "health system specialist", "huc/registrar", "huc registrar",
    "operational performance", "appointment setter", "inbound/outbound",
    "inbound sales", "outbound sales", "aviation analyst",
    "aviation specialist", "airline analyst", "field inspector",
    "police department", "police officer", "estimating coordinator",
    "estimator,", "senior estimator", "construction project manager",
    "construction manager", "teaching jobs", "elementary teacher",
    "school teacher", "lecturer", "help desk", "service desk", "tririga",
    "supply chain analyst", "supply chain specialist",
    "logistics coordinator", "logistics analyst", "procurement specialist",
    "procurement analyst", "vp procurement", "vp of procurement",
    "vice president procurement", "vice president of procurement",
    "head of procurement", "director of procurement", "director, procurement",
    "vice president finance", "vice president of finance",
    "senior vice president finance", "senior vice president of finance",
    "svp finance", "svp, finance", "svp of finance", "evp finance",
    "evp, finance", "evp of finance", "hr manager", "senior hr manager",
    "people operations manager", "talent aquisition",
    "global support manager", "vp of asset management",
    "vp, asset management", "asset management analyst",
    "asset management associate", "asset management specialist",
    "operations coordinator", "administrative associate",
    "administrative coordinator", "help desk support", "desktop support",
    "national account manager", "exhibit designer", "store designer",
    "good store designer", "narrative designer", "product owner",
    # FanDuel pattern — HR/comp/DEI/analytics/casino
    "director of compensation", "director, compensation",
    "compensation director", "head of compensation", "vp of compensation",
    "vp, compensation", "vp compensation", "senior director, compensation",
    "senior director of compensation", "compensation & benefits",
    "compensation and benefits", "compensation manager",
    "compensation specialist", "head of inclusion", "director of inclusion",
    "director, inclusion", "vp of inclusion", "vp, inclusion",
    "diversity equity inclusion", "diversity, equity",
    "diversity & inclusion", "diversity and inclusion", "head of diversity",
    "vp of diversity", "chief diversity", "inclusion specialist",
    "inclusion manager", "vp, people", "vp people", "commercial analyst",
    "commercial senior analyst", "senior commercial analyst",
    "commercial operations analyst", "head of analytics",
    "vp of analytics", "vp, analytics", "vp analytics",
    "director of analytics", "director, analytics", "analytics director",
    "analytics senior director", "senior director, analytics",
    "senior director of analytics", "analytics manager",
    "senior analytics manager", "casino analyst", "casino manager",
    "casino operations",
    # b (FanDuel function-suffix forms)
    "marketing sciences", "marketing science", "marketing technology",
    "marketing automation", "marketing operations", "marketing data",
    "marketing vice president", "marketing senior vice president",
    "human resources vice president", "finance vice president",
    "compensation vice president", "people vice president",
    "consumer insights", "customer insights", "insights analyst",
    "insights manager", "insights senior", "research analyst",
    "market research analyst", "product analyst", "automation analyst",
    "trading analyst", "trading senior", "algorithmic trading",
    "vip host", "vip events", "vip associate", "vip account",
    "events associate", "events coordinator",
    "crm operations", "crm associate", "crm analyst",
    "operational excellence", "commercial strategy manager",
    "commercial strategy associate", "commercial strategy analyst",
    "commercial strategy senior", "responsible gaming",
    "responsible gambling", "accountant", "general ledger accountant",
    "gl accountant", "ap accountant", "ar accountant",
    "accounting manager", "accounting associate", "accounting specialist",
    "systems administrator", "data product manager",
    "performance & insights", "performance and insights",
    "trading manager", "trading senior manager", "trading associate",
    "pokerstars", "creator operations", "community operations",
    # additions (the original author explicit: cybersecurity/risk/compliance out)
    "chief risk", "chief risk officer", "cro,", "vp risk", "vp, risk",
    "vp of risk", "head of risk", "director of risk", "director, risk",
    "risk director", "senior director, risk", "senior director of risk",
    "risk management", "enterprise risk", "chief compliance",
    "chief compliance officer", "vp compliance", "vp, compliance",
    "vp of compliance", "head of compliance", "director of compliance",
    "director, compliance", "compliance director",
    "senior director, compliance", "senior director of compliance",
    "regulatory compliance", "governance risk", "governance, risk",
    "governance and risk", "grc director", "grc manager",
    "director of governance", "head of governance", "vp of governance",
    "chief audit", "chief audit executive", "internal audit",
    "audit director", "director of audit", "director, audit",
    "vp of audit", "vp audit", "audit manager", "senior audit",
    "audit senior", "staff auditor", "senior auditor", "it audit",
    "head of fraud", "vp of fraud", "vp fraud", "director of fraud",
    "director, fraud", "fraud director", "fraud prevention",
    "fraud strategy", "financial crimes", "anti-money laundering",
    "anti money laundering", "ciso", "chief information security",
    "chief security officer", "chief security", "vp of security",
    "vp, security", "vp security", "head of security",
    "director of security", "director, security", "security director",
    "cybersecurity", "cyber security", "head of cybersecurity",
    "vp of cybersecurity", "director of cybersecurity",
    "director, cybersecurity", "information security", "infosec",
    "security operations", "soc analyst", "soc manager",
    "cyber threat", "threat intelligence", "appsec",
    "application security", "product security", "cloud security",
    "network security", "endpoint security", "vulnerability management",
    "penetration tester", "pen tester", "red team", "blue team",
    "security architect", "iam engineer", "identity and access management",
    "identity & access management", "supply chain director",
    "director of supply chain", "head of supply chain",
    "vp of supply chain", "vp supply chain", "policy director",
    "director of policy", "director, policy", "quant analyst",
    "quant trader", "quantitative analyst", "renewable energy project",
    # c FanDuel final tightening
    "change manager", "change lead", "change management", "change analyst",
    "customer marketing", "customer engagement",
    "customer experience manager", "customer experience associate",
    "talent management", "talent manager", "talent operations",
    "talent specialist", "talent partner", "talent associate",
    "product manager", "project management associate",
    "project management senior associate", "project management specialist",
    "project management coordinator", "project coordinator",
    "finance manager", "senior finance manager", "release specialist",
    "release manager", "release engineer", "release coordinator",
    "technical release", "martech", "marketing tech", "qa associate",
    "qa specialist", "qa coordinator", "qa analyst", "quality associate",
    "director of workplace", "director, workplace",
    "north america workplace", "workplace director",
    "payments strategy", "payments analyst", "payments associate",
    "payments operations", "discovery & engagement",
    # NOTE "ai architect" REMOVED — was killing
    # "Associate Director, Platform AI Architect" type titles which
    # are legit platform leadership. "ml architect" / "principal ai"
    # / "principal ml" stay (those are IC research titles).
    "discovery and engagement", "ml architect",
    "principal ai", "principal ml", "inclusion associate",
    "inclusion coordinator", "inclusion analyst",
    # d mid-band cleanup
    "operations excellence", "media associate", "media manager",
    "media specialist", "media coordinator", "media buyer", "vip team",
    "vip manager", "vip specialist", "vip coordinator",
    "procurement operations", "senior associate, commercial",
    "associate, commercial", "acquisition strategy", "workplace manager",
    "senior workplace manager", "global compensation", "qa tester",
    "language development", "brand strategy manager",
    "brand strategy associate", "brand strategy specialist",
    "brand specialist", "brand coordinator",
    # e post-rescore outliers
    "analyst", "growth associate", "operations associate",
    "ops specialist", "business affairs", "legal assistant",
]


# ---- SUB_VP_SENIORITY -------------------------------------------------------
# Title markers indicating sub-VP seniority that the original author cannot credibly target.
# Case-insensitive substring match against TITLE ONLY.
# NOTE: "associate" NOT included here — it needs context-sensitive handling
# ("Associate Vice President" at insurance/finance is legit exec). The
# title-function gate above catches the IC "associate" variants by name.
HARD_DISQUALIFIER_TITLES_SENIORITY: list[str] = [
    "intern", "internship",
    "entry level", "entry-level", "entry_level",
    "early career",            # "[2026] Associate Art Director, Early Career"
    "new grad", "new graduate", "new-grad",
    "graduate programme", "graduate program",
    "apprentice", "traineeship", "trainee",
    "junior",
    " jr ", " jr,", " jr.",
]


# ---- UNPAID_ENGAGEMENT ------------------------------------------------------
# Full-text (title OR description) phrases indicating unpaid / commission-only
# roles. the original author can't take these. Source: YAML engagement_disqualifiers.
HARD_DISQUALIFIER_ENGAGEMENT: list[str] = [
    "commission only",
    "no base salary",
    "unpaid",
    "equity only",
    "equity-only",
]


# ---- LEADERSHIP_EXCEPTIONS --------------------------------------------------
# Exec / leadership phrases that, when present in the title, EXEMPT the job
# from HARD_DISQUALIFIER_TITLES_FUNCTION. They signal a role the original author could
# legitimately hold even though some substring might overlap a blocked
# function kw (e.g., "VP of Engineering" contains "engineering"). These
# are matched via simple substring.
#
# Intentionally narrow — CMO / CFO / CHRO / CRO / Creative Director all
# REMOVED (wrong function at exec level per the original author's explicit direction in
# ).
LEADERSHIP_EXCEPTIONS: list[str] = [
    # Engineering executive
    "director of engineering", "vp of engineering", "vp engineering",
    "vp, engineering", "head of engineering", "engineering director",
    "chief engineer", "chief technology",
    # Technology exec
    "vp of technology", "vp technology", "vp, technology",
    "head of technology", "director of technology", "technology director",
    # Design exec
    "design director", "director of design", "vp of design",
    "head of design", "chief design",
    # Product exec
    "chief product", "vp of product", "vp product", "vp, product",
    "head of product", "director of product", "product director",
    "group product manager",
    # Operations exec
    "chief operating", "vp of operations", "vp operations", "vp, operations",
    "head of operations", "director of operations", "operations director",
    "chief of staff",
    # Strategy / advisory
    "chief strategy", "vp of strategy", "head of strategy",
    "strategic advisor", "operating partner", "executive advisor",
    # Analyst-firm exec titles — Gartner / Forrester / IDC VP Analyst and
    # Principal Analyst are senior-IC thought-leadership roles the original author
    # legitimately targets. Must exempt BEFORE the "analyst" disqualifier
    # kill fires. (Added .)
    "vp analyst", "vp, analyst", "principal analyst",
    # Platform / data exec
    "chief data", "vp of platform", "vp platform", "vp, platform",
    "head of platform", "platform director", "director of platform",
    # Online services / live service / infrastructure exec — 
    # add. Canonical the original author targets like "VP, Platform Engineering" at
    # Roblox and "VP Online Services" at 2K contain "platform engineer"
    # / "engineering" which otherwise trip the function disqualifier.
    # These phrases exempt the title substring-check so the prefilter
    # doesn't mis-gate core lane roles.
    "vp of online services", "vp online services", "vp, online services",
    "head of online services", "director of online services",
    "vp of infrastructure", "vp infrastructure", "vp, infrastructure",
    "head of infrastructure", "director of infrastructure",
    "vp of game services", "vp game services", "vp, game services",
    "head of game services",
    "platform engineering director", "director of platform engineering",
    "head of platform engineering", "vp of platform engineering",
    "vp platform engineering", "vp, platform engineering",
    "online services director", "director of online services engineering",
    "head of online services engineering",
    # Live service / multiplayer exec
    "vp of live service", "vp live service", "vp, live service",
    "head of live service", "director of live service",
    "live service director",
    # Production / content exec
    "executive producer", "showrunner",
    # In-house legal exec (M&A overlap)
    "general counsel",
]


# Word-boundary-matched exec acronyms (CTO/COO/CPO). Checked via \b{kw}\b
# so "cto" doesn't match "direCTOr".
LEADERSHIP_ACRONYM_EXCEPTIONS: list[str] = [
    "cto",   # Chief Technology Officer
    "coo",   # Chief Operating Officer
    "cpo",   # Chief Product Officer
]


# ---- LEADERSHIP_WHITELIST_PATTERNS ------------------------------------------
# New regex-based whitelist that short-circuits the wrong-
# function gate when the title clearly carries an executive / leadership
# role-noun. the original author's audit of the 10,635 active rows surfaced these
# false-killed titles:
#
#   "Senior Engineering Manager"
#   "Sr Director Analyst, AI and Software Engineering"
#   "Associate Director, Platform AI Architect"
#   "Senior Technical Program Manager"
#
# Each was killed by naive substring overlap with an IC-engineer keyword
# ("senior engineer", "software engineer", "ai architect", "technical
# program manager"). The fix is twofold:
#
#   (1) HARD_DISQUALIFIER_TITLES_FUNCTION is now matched with WORD
#       BOUNDARIES (\b...\b) so "senior engineer" no longer matches inside
#       "senior engineering manager".
#   (2) When a disqualifier IS found, this whitelist is consulted. Any
#       match here EXEMPTS the title — Haiku gets called and decides.
#
# Patterns are case-insensitive, applied to the lowercased title only
# (not description). Each pattern is a real regex string — escape literal
# regex metachars (none here).
#
# IMPORTANT: this whitelist is intentionally generous. If a title like
# "VP of Marketing" sneaks past the prefilter because of the bare "vp"
# pattern, Haiku will downgrade it on role_family_match and it will land
# at watchlist or skip. The cost of an extra Haiku call is small; the
# cost of false-killing a real exec role is large.
LEADERSHIP_WHITELIST_PATTERNS: list[str] = [
    # Generic exec-title openers
    r"\bhead\s+of\b",
    r"\bvp\b",
    r"\bvice\s+president\b",
    r"\bsvp\b",
    r"\bsenior\s+vice\s+president\b",
    r"\bevp\b",
    r"\bexecutive\s+vice\s+president\b",
    # "Chief X" exec line (matches "chief technology officer", "chief
    # of staff", etc.) — trailing space ensures we need a word after.
    r"\bchief\s+\w",
    # Specific exec acronyms that don't trip the LEADERSHIP_ACRONYM_RE
    # path because that one only covers cto/coo/cpo.
    r"\bcto\b", r"\bcio\b", r"\bceo\b",
    # Director cluster — bare + decorated forms
    r"\bdirector\b",
    r"\bsenior\s+director\b",
    r"\bsr\.?\s+director\b",
    r"\bexecutive\s+director\b",
    r"\bmanaging\s+director\b",
    # Associate Director + technology-noun (the original author's Test C — "Associate
    # Director, Platform AI Architect" must pass). Allow any chars in
    # between to handle "Associate Director, Platform" / "Associate
    # Director - Engineering" / etc.
    r"\bassociate\s+director\b[^.]*?\b(?:architect|platform|strategy|technology|engineering)\b",
    # Manager-level titles that in practice ARE engineering leadership.
    # "Engineering Manager" is the canonical one — the original author's Test A.
    r"\b(?:engineering|technology|platform|technical\s+program|program)\s+manager\b",
    # Analyst-firm thought-leader titles (Gartner / Forrester / IDC).
    # "VP Analyst" is a Gartner exec-IC title; "Sr Director Analyst"
    # is the next tier up. the original author's Test B — "Sr Director Analyst, AI
    # and Software Engineering" must pass.
    r"\bprincipal\s+analyst\b",
    r"\bvp\s+analyst\b",
    r"\bsr\.?\s+director\s+analyst\b",
    r"\bsenior\s+director\s+analyst\b",
]


# ---- PRIORITY_DISQUALIFIERS -------------------------------------------------
# Phrases that ALWAYS kill the job, even if a LEADERSHIP_EXCEPTION would
# otherwise protect it. Use sparingly for pivot-away lanes: D2C / performance
# marketing / ads work that the original author is explicitly leaving behind.
#
# Example: "Director of Product - Ads Performance" contains the exec phrase
# "director of product" which is in LEADERSHIP_EXCEPTIONS, but "ads
# performance" is a priority disqualifier and kills anyway.
PRIORITY_DISQUALIFIERS: list[str] = [
    "ads performance",
    "ad performance",
    "performance ads",
    "performance advertising",
    "performance marketing",
    "ads product",
    # additions — D2C / performance-marketing lane
    "user acquisition",
    "paid social",
    "paid media",
    "ads experience",
    "ads platform",
    "advertising experience",
    "advertising product",
]


# ---- DILUTING_PREFIXES ------------------------------------------------------
# Prefixes that, when they appear before a LEADERSHIP_EXCEPTION in the title,
# INVALIDATE the exception. The job falls back through the disqualifier gate.
#
# Example: "Associate 3D Design Director" — "design director" would match the
# exception but "associate" + "3d" are diluting prefixes, so the exception is
# ignored and the role is treated as senior-IC craft (= function disqualifier).
DILUTING_PREFIXES: list[str] = [
    "associate ",
    "asst. ", "asst ",
    "assistant ",
    "junior ",
    "jr ", "jr. ",
    "3d ", "2d ",
    "visual ",
    "graphic ",
    "interaction ",
    "ui ", "ui/ux ", "ux ", "ux/ui ",
    "motion ",
    "set ",
    "costume ",
    "sound ",
    "lighting ",
]


# =============================================================================
# 2) SOFT_WARNINGS — flag for Haiku, never auto-kill.
# =============================================================================
#
# These are signals that would be wrong for SOME the original author-style roles but
# right for others. Example: "temporary" is wrong for a full-time VP search,
# but RIGHT for TRACK_2 interim/fractional CTO. Rather than hard-coding a
# decision, we flag them as WARNINGS and let Haiku weigh them against the
# full role context.
#
# When any of these fire, the prefilter's output includes a
# `soft_warnings: list[str]` which is injected into the Haiku prompt.
# -----------------------------------------------------------------------------


# Titles that look like staff-aug / temp / part-time contractor work. NOT
# the same as TRACK_2 interim/fractional engagements — real Track-2 titles
# (Interim CTO, Fractional CIO) are recognized via the POSITIVE_SIGNALS
# `interim` list. These are the wrong-shape contractor titles.
SOFT_WARNING_TEMP_CONTRACT: list[str] = [
    "(temporary)", "(temp)", "(short term)", "(short-term)",
    "temporary)", "seasonal", "part-time", "part time",
    "staff augmentation", "temp-to-perm",
    "short-term assignment",
]


# Titles that mention D2C / commerce / payments / ads explicitly in the
# title. the original author is pivoting AWAY from this work but occasional cases are
# strategy-framed (e.g., "VP of D2C Strategy" at a legit company). Flag
# and let Haiku decide.
SOFT_WARNING_D2C_IN_TITLE: list[str] = [
    "d2c", " commerce", "payments", "payment", "e-commerce", "ecommerce",
    " ads", "advertising", "performance marketing", "growth marketing",
    "demand generation",
]


# Titles at "Manager" or "Sr Manager" level. At BigCo these are below VP,
# but at small (<50-person) startups the VP title doesn't exist and a
# Senior Manager might actually own the function. Flag, don't kill.
SOFT_WARNING_BELOW_VP: list[str] = [
    "senior manager", "sr manager", "sr. manager",
    "manager,",                 # "Manager, X"
    "associate",                # sometimes exec (Associate VP); usually not
]


# Crunch-culture JD markers. These are warning flags for Haiku — the
# severity depends on the company (e.g., "fast-paced" at Anduril vs at
# a care-focused nonprofit is very different).
SOFT_WARNING_CRUNCH_PHRASES: list[str] = [
    "fast-paced", "move fast", "high-velocity", "ship fast",
    "bias for action", "bias to action", "hustle", "grind",
    "relentless", "wear many hats", "roll up your sleeves", "scrappy",
    "startup pace", "thrives under pressure", "deadline-driven",
    "aggressive timelines", "crunch", "always-on",
]


# Toxic-culture red flags. Same treatment as crunch — flag for Haiku.
SOFT_WARNING_CULTURE_REDFLAGS: list[str] = [
    "work hard, play hard", "we're a family", "like a family",
    "rockstar", "ninja", "10x", "eat sleep breathe", "whatever it takes",
    "not a 9-to-5", "not for the faint of heart", "no clock-watchers",
]


# High-travel / RTO mandates. the original author has split custody — heavy travel or
# 5-day-in-office is a practical constraint. Flag for Haiku (some VP roles
# legitimately require it and have offsetting comp; some are deal-breakers).
SOFT_WARNING_HIGH_TRAVEL: list[str] = [
    "50% travel", "heavy travel", "travel required 50", "travel 50%",
    "extensive travel", "frequent travel",
]
SOFT_WARNING_RTO_MANDATE: list[str] = [
    "5 days in office", "five days in office", "in-office 5 days",
    "fully on-site", "fully onsite", "100% on-site", "100% in office",
    "no remote", "rto mandate", "5-day rto",
]


# TPM (Technical Program Manager) titles. Used to be a hard
# kill but the original author's audit caught senior-TPM roles at gaming dream-cos that
# are legitimate cross-functional engineering-program leadership.
# Flag, let Haiku weigh whether the JD reads like real program leadership
# vs. the IC-coordinator flavor.
SOFT_WARNING_TPM: list[str] = [
    "technical program manager",
    "tpm,",  # "TPM, Platform Engineering" style titles
]


# Hands-on coding signals. the original author is NOT a coder. Most execs don't need to,
# but some startup CTO roles blur the line. Flag, don't kill.
SOFT_WARNING_HANDS_ON_CODING: list[str] = [
    "hands-on coder", "write production code",
    "individual contributor engineer", "ic engineer",
    "full-stack developer", "actively coding", "coding required",
    "pr reviews daily", "push code", "commit code", "ship code",
    "pair programming", "on-call rotation", "production on-call",
    "kubernetes hands-on", "deploy pipelines", "leetcode",
    "coding interview", "live coding", "take-home coding", "50% coding",
]


# =============================================================================
# 3) POSITIVE_SIGNALS — diagnostic flagging, NOT score math.
# =============================================================================
#
# When any of these fire, the job is tagged with the category in its
# `positive_signals` list. Used for:
#   - Informing Haiku ("this role matched the original author's tcg_boardgame signals")
#   - Markdown export faceting ("here are the roles tagged ma_pmi")
#   - UI filter chips
#
# Critically: no weighted score math. Haiku does the ranking.
# -----------------------------------------------------------------------------

POSITIVE_SIGNALS: dict[str, list[str]] = {

    # -- Role-shape positives — tech-strategy / architecture / M&A -----------

    "strategy": [
        "corporate strategy", "enterprise strategy", "strategic planning",
        "strategy & operations", "strategy and operations", "chief of staff",
        "vp analyst", "principal analyst", "industry analyst",
        "market intelligence", "competitive intelligence", "research director",
        "thought leadership", "advisory", "executive advisor",
        "strategic advisor", "business transformation", "operating model",
        "portfolio strategy", "growth strategy", "horizon planning",
        "tech strategy", "technology strategy", "product strategy",
        "go-to-market strategy", "platform strategy", "capability roadmap",
    ],

    "architecture": [
        "technology program management", "tpm", "enterprise program",
        "program director", "head of programs", "enterprise architecture",
        "enterprise architect", "solutions architecture",
        "technology architecture", "chief architect", "reference architecture",
        "domain architecture", "capability model", "target state architecture",
        "technology steering", "architecture review board", "arb",
        "technology governance", "it governance", "platform strategy",
        "technology roadmap", "program management office", "pmo",
    ],

    "ma_pmi": [
        "m&a integration", "post-merger integration",
        "post-acquisition integration", "pmi", "integration management office",
        "imo lead", "carve-out", "divestiture", "day 1 readiness",
        "synergy capture", "integration playbook", "deal integration",
        "transaction advisory", "corporate development", "inorganic growth",
        "technology due diligence", "tech dd", "acquisition integration",
    ],

    # -- Seniority positives — VP+ title markers ------------------------------

    "senior_titles": [
        "vice president", "vp", "svp", "senior vice president",
        "evp", "executive vice president", "head of", "director",
        "senior director", "principal", "distinguished", "fellow",
        "practice lead", "managing director", "general manager",
    ],

    # -- Industry / passion positives -----------------------------------------

    "gaming": [
        "for the players", "games industry", "game development",
        "game developer", "game studio", "game studios", "games studio",
        "live service", "live services", "live ops", "live operations",
        "player experience", "players and creators", "creators and players",
        "creator economy", "creator platform", "developer community",
        "game community", "gaming platform", "game platform", "game engine",
        "unreal engine", "unity engine", "game services", "game production",
        "multiplayer", "matchmaking", "mmo", "f2p", "free-to-play",
        "battle pass", "battle royale", "esports", "competitive gaming",
        "game design", "gameplay", "narrative", "intellectual property",
        "franchise", "studio", "avatars", "virtual world", "virtual worlds",
        "metaverse", "experiences on", "our platform reaches",
        "monthly active users",
    ],

    "immersive": [
        "immersive", "experiential", "location-based entertainment", "lbe",
        "escape room", "themed entertainment", "installation", "exhibit",
        "interactive experience", "virtual reality", "augmented reality",
        "extended reality", "projection mapping", "dark ride", "attraction",
        "guest experience", "visitor experience",
    ],

    "tcg_boardgame": [
        "trading card game", "tcg", "ccg", "collectible card",
        "deckbuilding", "board game", "tabletop", "strategy game",
        "magic the gathering", "pokemon", "yu-gi-oh", "mtg",
    ],

    "music": [
        "music", "musician", "artist", "daw", "audio", "sound design",
        "live events", "concerts", "ticketing", "streaming audio",
        "instrument", "producer", "songwriter", "label", "recording",
    ],

    # -- Mission positives — nonprofit / helping people -----------------------

    "mission": [
        "mission-driven", "making lives better", "helping people",
        "social impact", "community impact", "serve our users",
        "user well-being", "for the players", "accessibility",
        "underrepresented", "mental health", "suicide prevention",
        "crisis support", "nonprofit", "501(c)(3)", "social good",
    ],

    # -- Engagement positives — interim / fractional track --------------------

    "interim": [
        "interim", "fractional", "fractional cto", "fractional cio",
        "contract", "contractor", "contract-to-hire", "1099",
        "w2 contract", "consultant", "consulting engagement", "sow",
        "statement of work", "project-based", "fixed-term", "ftc",
        "temp-to-perm", "short-term assignment", "engagement", "advisor",
        "advisory role", "board advisor", "6-month contract",
        "12-month contract", "day rate", "hourly rate",
    ],

    # -- Cultural alignment positives — LGBTQ+ inclusion ----------------------

    "lgbtq": [
        "gender identity", "sexual orientation", "lgbtq+", "lgbtqia+",
        "transgender", "non-binary", "pride erg", "out@", "spectrum erg",
        "domestic partner benefits", "transgender-inclusive healthcare",
        "gender-affirming care", "family-forming benefits",
        "fertility benefits", "adoption assistance", "surrogacy benefits",
        "equal parental leave", "gender-neutral parental leave", "pronouns",
        "human rights campaign", "equality 100",
        "best place to work for lgbtq",
    ],

    # -- Work-life positives (flag only — Haiku weighs) -----------------------

    "family_friendly": [
        "flexible schedule", "flexible hours", "family-friendly",
        "parental leave", "paid parental leave", "caregiver leave",
        "backup childcare", "work-life balance", "4-day work week",
        "summer hours", "no weekend work",
    ],

    # -- Geography positives — NJ office means commutable from Mountain Lakes -

    "nj_office": [
        "jersey city", "hoboken", "mountain lakes", "parsippany",
        "newark", "morristown", "short hills",
    ],

    # -- Rare-specialty positives — the original author's Take-Two / 2K Online heritage -----

    "multiplayer_live_service": [
        "multiplayer infrastructure", "multiplayer backend",
        "live service", "live ops", "live operations", "online services",
        "online services platform", "matchmaking", "concurrent players",
        "game services", "platform engineering", "merchant of record",
        "subscription platform", "post-merger integration", "shared services",
    ],
}


# =============================================================================
# Industry classification (preserved from config/scoring.yaml).
# =============================================================================
#
# Still used as diagnostic input ("what industry did we classify this as?")
# but NOT for score math in the prefilter. The Haiku prompt gets told the
# industry so it can weight fit accordingly.
# -----------------------------------------------------------------------------

# Bucket scores (0-10). Not score-math anymore — kept for sorting / UI
# facets and the `industry_score` diagnostic field.
INDUSTRY_SCORES: dict[str, int] = {
    "gaming_publisher_platform":      10,
    "digital_tcg_ccg":                10,
    "immersive_lbe":                   9,
    "gaming_b2b_infrastructure":       9,
    "music_tech":                      8,
    "gaming_accessibility_nonprofit":  8,
    "streaming_media":                 7,
    "analyst_firm":                    7,
    "gaming_vc_pe_operating":          7,
    "sports_betting_tech":             6,
    "science_education_nonprofit":     6,
    "hospitality_tech":                5,
    "defense_simulation":              4,
    "general_enterprise_tech":         3,
    "adtech_martech":                  2,
    "crypto_web3":                     1,
}


# Known company → industry bucket mapping (lowercased, suffix-stripped).
COMPANY_INDUSTRY_MAP: dict[str, str] = {
    # Gaming publishers / platforms
    "riot games": "gaming_publisher_platform",
    "roblox": "gaming_publisher_platform",
    "epic games": "gaming_publisher_platform",
    "blizzard entertainment": "gaming_publisher_platform",
    "avalanche studios": "gaming_publisher_platform",
    "microsoft gaming": "gaming_publisher_platform",
    "xbox": "gaming_publisher_platform",
    "nintendo of america": "gaming_publisher_platform",
    "sony interactive entertainment": "gaming_publisher_platform",
    "electronic arts": "gaming_publisher_platform",
    "ea": "gaming_publisher_platform",
    "activision blizzard": "gaming_publisher_platform",
    "take-two interactive": "gaming_publisher_platform",
    "2k games": "gaming_publisher_platform",
    "ubisoft": "gaming_publisher_platform",
    "bungie": "gaming_publisher_platform",
    "naughty dog": "gaming_publisher_platform",
    "respawn entertainment": "gaming_publisher_platform",
    "crystal dynamics": "gaming_publisher_platform",
    "rockstar games": "gaming_publisher_platform",
    # Gaming B2B infra
    "unity": "gaming_b2b_infrastructure",
    "unity technologies": "gaming_b2b_infrastructure",
    "accelbyte": "gaming_b2b_infrastructure",
    "pragma": "gaming_b2b_infrastructure",
    "heroic labs": "gaming_b2b_infrastructure",
    "beamable": "gaming_b2b_infrastructure",
    "gameanalytics": "gaming_b2b_infrastructure",
    "edgegap": "gaming_b2b_infrastructure",
    # TCG / CCG
    "wizards of the coast": "digital_tcg_ccg",
    "pokemon company": "digital_tcg_ccg",
    "upper deck": "digital_tcg_ccg",
    # Immersive / LBE
    "meow wolf": "immersive_lbe",
    "figure8": "immersive_lbe",
    "museum of ice cream": "immersive_lbe",
    "five iron golf": "immersive_lbe",
    "activate": "immersive_lbe",
    "sandbox vr": "immersive_lbe",
    "cosm": "immersive_lbe",
    "puttshack": "immersive_lbe",
    "level99": "immersive_lbe",
    "immersive gamebox": "immersive_lbe",
    "dreamscape": "immersive_lbe",
    # Music
    "spotify": "music_tech",
    "splice": "music_tech",
    "ableton": "music_tech",
    "native instruments": "music_tech",
    "fender digital": "music_tech",
    "audiokinetic": "music_tech",
    "seatgeek": "music_tech",
    "live nation": "music_tech",
    "ticketmaster": "music_tech",
    # Streaming / media
    "disney": "streaming_media",
    "nbcuniversal": "streaming_media",
    "paramount": "streaming_media",
    "paramount pictures": "streaming_media",
    "paramount global": "streaming_media",
    "dolby": "streaming_media",
    "dolby laboratories": "streaming_media",
    "warner bros discovery": "streaming_media",
    "espn": "streaming_media",
    "apple": "streaming_media",
    "netflix": "streaming_media",
    # Analyst firms
    "gartner": "analyst_firm",
    "forrester": "analyst_firm",
    "idc": "analyst_firm",
    "omdia": "analyst_firm",
    # Sports betting / iGaming
    "draftkings": "sports_betting_tech",
    "fanduel": "sports_betting_tech",
    "betmgm": "sports_betting_tech",
    "bet365": "sports_betting_tech",
    "caesars digital": "sports_betting_tech",
    "pointsbet": "sports_betting_tech",
    "underdog fantasy": "sports_betting_tech",
    "prizepicks": "sports_betting_tech",
    # Hospitality tech
    "sevenrooms": "hospitality_tech",
    "resy": "hospitality_tech",
    "opentable": "hospitality_tech",
    "bentobox": "hospitality_tech",
    "toast": "hospitality_tech",
    # Science / education nonprofit
    "liberty science center": "science_education_nonprofit",
    "american museum of natural history": "science_education_nonprofit",
    "intrepid museum": "science_education_nonprofit",
    # Gaming accessibility / mission
    "games for change": "gaming_accessibility_nonprofit",
    "ablegamers": "gaming_accessibility_nonprofit",
    "deepwell dtx": "gaming_accessibility_nonprofit",
    # Defense / sim
    "anduril": "defense_simulation",
    "shield ai": "defense_simulation",
    # Gaming VC / PE
    "bitkraft ventures": "gaming_vc_pe_operating",
    "griffin gaming partners": "gaming_vc_pe_operating",
    "konvoy": "gaming_vc_pe_operating",
}


# Fallback keyword classifier — if company not in COMPANY_INDUSTRY_MAP,
# run keyword match against lowercased title+company+description.
# First bucket whose keywords hit wins.
INDUSTRY_KEYWORDS: dict[str, list[str]] = {
    "gaming_publisher_platform": [
        "game studio", "video game", "mobile game", "gaming platform",
        "console game", "esports", "live service game", "free-to-play",
        "battle pass",
    ],
    "digital_tcg_ccg": [
        "trading card game", "tcg", "ccg", "collectible card", "deckbuilding",
    ],
    "immersive_lbe": [
        "immersive entertainment", "location-based entertainment", "lbe",
        "themed entertainment", "escape room", "experiential venue",
        "dark ride", "attraction operator",
    ],
    "gaming_b2b_infrastructure": [
        "game backend", "gaming infrastructure", "gaming sdk",
        "game platform services", "matchmaking service", "game server",
    ],
    "music_tech": [
        "music technology", "music platform", "audio technology",
        "music streaming", "live events platform", "ticketing platform",
    ],
    "sports_betting_tech": [
        "sports betting", "sportsbook", "igaming", "online gambling",
        "wagering", "fantasy sports", "daily fantasy",
    ],
    "streaming_media": [
        "streaming media", "sports media", "broadcast", "media company",
        "digital media", "content platform",
    ],
    "analyst_firm": [
        "research firm", "analyst firm", "market research",
        "industry analyst", "advisory firm",
    ],
    "gaming_accessibility_nonprofit": [
        "gaming nonprofit", "games for health", "digital therapeutics",
        "game-based learning", "games and mental health",
    ],
    "hospitality_tech": [
        "hospitality technology", "restaurant technology",
        "restaurant platform", "hotel technology", "foodservice technology",
    ],
    "science_education_nonprofit": [
        "science museum", "natural history", "education nonprofit",
        "cultural institution",
    ],
    "crypto_web3": [
        "web3", "blockchain", "cryptocurrency", "defi", "nft", "crypto",
    ],
}


# =============================================================================
# Static company lists (preserved — all categorical, no score math).
# =============================================================================

# HRC Corporate Equality Index 100 (2025/2026). Roles at these companies
# get an HRC positive-signal flag surfaced to Haiku + UI.
HRC100_COMPANIES: list[str] = [
    "apple", "microsoft", "google", "meta", "amazon", "netflix", "spotify",
    "adobe", "salesforce", "ibm", "intel", "oracle", "cisco", "nvidia",
    "qualcomm", "the walt disney company", "disney", "nbcuniversal",
    "warner bros discovery", "paramount global", "paramount",
    "sony interactive entertainment", "sony pictures", "electronic arts",
    "activision blizzard", "riot games", "take-two interactive", "ubisoft",
    "nintendo of america", "zynga", "uber", "lyft", "airbnb", "linkedin",
    "pinterest", "ebay", "paypal", "american express", "jpmorgan chase",
    "goldman sachs", "mastercard", "visa", "target", "nike", "servicenow",
    "workday", "atlassian", "zoom", "dropbox", "twilio", "snap", "dolby",
]


# Companies with documented crunch culture. Flag for Haiku — depending on
# role seniority the crunch exposure varies, so we don't auto-penalize.
CRUNCH_COMPANIES: list[str] = [
    "rockstar games", "take-two interactive", "cd projekt red",
    "activision", "blizzard", "electronic arts", "bioware", "naughty dog",
    "rocksteady studios", "ubisoft", "treyarch", "quantic dream",
    "netherealm studios", "bungie", "crystal dynamics",
    "respawn entertainment",
]


# Crunch-reformed companies — same flag, but Haiku prompt notes that
# public reform reporting exists. Currently just Riot (2019+ reforms).
CRUNCH_REDUCED_PENALTY_COMPANIES: list[str] = [
    "riot games",
]


# =============================================================================
# Location / commute (preserved from config/scoring.yaml).
# =============================================================================
#
# the original author lives in Mountain Lakes NJ with split custody — commute distance is
# a practical constraint, not a preference. These lists feed into the
# prefilter's geography flag generation (remote / nyc_commutable /
# nj_office / out_of_area).
# -----------------------------------------------------------------------------

# City names that are commutable from Mountain Lakes via car or NJT/PATH.
# Used for substring match against location fields.
COMMUTE_CITIES: list[str] = [
    # NYC boroughs + target neighborhoods
    "new york", "nyc", "manhattan", "brooklyn", "queens",
    "tribeca", "midtown", "soho", "flatiron", "hudson yards",
    # NJ commute zone
    "jersey city", "hoboken", "newark", "parsippany",
    "morristown", "mountain lakes", "short hills",
]


# Regex patterns for location classification. Kept as raw strings — the
# prefilter compiles them. Ported verbatim from config/scoring.yaml under
# `location.*`.
LOCATION_PATTERNS: dict[str, list[str]] = {
    "remote_us": [
        r"(?i)\b(fully|100%|completely)?\s*remote\b",
        r"(?i)\bwork from anywhere\b",
        r"(?i)\bremote-first\b",
        r"(?i)\bremote[ -]friendly\b",
        r"(?i)\bdistributed team\b",
        r"(?i)\bUS remote\b",
        r"(?i)\bremote \(US\)\b",
        r"(?i)\banywhere in the (US|United States)\b",
    ],
    "nyc_metro": [
        r"(?i)\bhybrid\b.*\b(NYC|New York|Manhattan)\b",
        r"(?i)\b(NYC|New York)\b.*\bhybrid\b",
        r"(?i)\b[23]\s*days?\s*(in\s*office|in-office|onsite|on-site)\b",
        r"(?i)\bNew York,? NY\b",
        r"(?i)\bNYC[ -]based\b",
        r"(?i)\b(Tribeca|Midtown|SoHo|Flatiron|Hudson Yards|Brooklyn)\b",
        r"(?i)\b(Jersey City|Hoboken|Mountain Lakes|Morristown|Parsippany)\b",
    ],
    "nj_office": [
        r"(?i)\b(Jersey City|Hoboken|Hoboken NJ|Newark NJ|Parsippany|Morristown|Mountain Lakes|Short Hills)\b",
    ],
    "out_of_area": [
        r"(?i)\bon[- ]site.*(Seattle|Redmond|Bellevue|San Francisco|SF|Bay Area|Los Angeles|LA|Austin|Boston|Cupertino|Mountain View)\b",
        r"(?i)\b(Seattle|Redmond|San Francisco|Los Angeles|Austin|Boston|Cupertino).*\b(in[- ]office|on[- ]site|5 days)\b",
        r"(?i)\brelocation (required|assistance|package)\b",
        r"(?i)\bwillingness? to relocate\b",
        r"(?i)\bmust relocate\b",
        r"(?i)\bon[- ]site in (Seattle|Redmond|Cupertino|Mountain View|Austin|San Francisco|Los Angeles)\b",
    ],
    "heavy_office": [
        r"(?i)\bin[- ]office (5|five) days\b",
        r"(?i)\bfully on-?site\b",
        r"(?i)\b100% (on-?site|in office)\b",
        r"(?i)\bno remote\b",
        r"(?i)\bRTO mandate\b",
    ],
}


# =============================================================================
# Compensation (preserved — diagnostic flag only, not a hard gate).
# =============================================================================
#
# note: compensation below the floor is NO LONGER a hard gate.
# It's now a flag that Haiku sees. Rationale: some target-company VP roles
# post comp without bonus, and some nonprofit dream-fits pay below the
# floor but offset with mission alignment. Haiku can weigh.
# -----------------------------------------------------------------------------

COMP_THRESHOLDS: dict[str, float] = {
    # Example placeholder values. The runtime always reads these from
    # `config/scoring.yaml` -> `static_lists.comp_thresholds`; this dict
    # is only the defensive in-code fallback for environments where the
    # YAML failed to load. Set the YAML to your own bands and the
    # numbers below stay invisible to the live scorer.
    "salary_floor":       175000.0,  # below this = flag
    "neutral_if_missing": 5.0,       # diagnostic weight if salary not disclosed
    "high_min":           200000.0,  # "strong comp" flag range start
    "high_max":           260000.0,
    "overpay_max":        340000.0,  # above this = overlevel risk flag
    "medium_upper":       200000.0,
    "medium_lower":       175000.0,
    "low_threshold":      175000.0,
}


# =============================================================================
# Tier labels (for prefilter output + UI). NOT used for routing decisions.
# =============================================================================
# Rewritten in per spec — the new tiers:
#
#   T1_APPLY_NOW          semantic >= 78   ("Apply immediately")
#   T2_APPLY_2WK          semantic >= 65   ("Apply within 2 weeks")
#   T3_MONITOR            semantic >= 50   ("Monitor, apply selectively")
#   WATCHLIST             semantic >= 35   ("Re-evaluate monthly")
#   WATCHLIST_DREAM       flag-based       (dream-co + industry + level, any semantic)
#   SKIP                  semantic <  35
#   NEEDS_REVIEW          Haiku errored or quota exhausted — do NOT auto-skip
# -----------------------------------------------------------------------------

TIER_THRESHOLDS: dict[str, int] = {
    "T1_APPLY_NOW":   78,
    "T2_APPLY_2WK":   65,
    "T3_MONITOR":     50,
    "WATCHLIST":      35,
    # SKIP < 35 (no threshold)
    # WATCHLIST_DREAM, NEEDS_REVIEW — flag-based, not score-based
}


# =============================================================================
# Track labels (orthogonal to tier — which "lane" is this role in?)
# =============================================================================
# Preserved. Still used — the original author's three-track job-search model.
#
#   TRACK_1_FULLTIME    default fulltime exec role
#   TRACK_2_INTERIM     interim / fractional / contract (POSITIVE_SIGNALS.interim)
#   TRACK_3_PIVOT       passion / career-pivot (gaming/tcg/music/immersive/
#                       nonprofit industries)
# -----------------------------------------------------------------------------

TRACK_3_PIVOT_INDUSTRIES: list[str] = [
    "immersive_lbe",
    "digital_tcg_ccg",
    "music_tech",
    "gaming_accessibility_nonprofit",
]
