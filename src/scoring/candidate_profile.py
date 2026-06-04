"""
src/scoring/candidate_profile.py — candidate profile for the scoring pipeline.

Source of truth for WHO the candidate is. Deliberately plain-Python (not
YAML) so that assertion-style calibration tests can import and diff the
lists directly. The keyword lists fall into three named categories that
match how algo_prefilter + the Haiku semantic layer use them:

    1. HARD_DISQUALIFIERS  — any match kills the job before semantic.
                             No weighting. Binary auto-reject.
    2. SOFT_WARNINGS       — flag for the semantic layer to weigh. Never
                             auto-kill. The model decides if it's worth it.
    3. POSITIVE_SIGNALS    — diagnostic flagging only. NOT for score math.
                             Surfaces "why did this rank high?" in the UI.

Also preserved:
    - COMPANY_INDUSTRY_MAP (company -> industry bucket)
    - INDUSTRY_SCORES      (bucket -> relative 0-10 score, for flags)
    - INDUSTRY_KEYWORDS    (fallback keyword classifier)
    - CRUNCH_COMPANIES     (companies with documented crunch culture)
    - HRC100_COMPANIES     (HRC Corporate Equality Index — inclusion flag)
    - LOCATION_PATTERNS    (geography zones for commute classification)
    - COMP_THRESHOLDS      (base+bonus floor/target/ceiling, USD)

FORK NOTE: every constant below encodes one example candidate's profile.
Rewrite the geography, target companies, industry preferences, and
keyword lists to match your own search. `config/candidate_profile.yaml`
drives the dominant semantic signal — edit that first.
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
# Titles indicating an IC or wrong-function role the candidate cannot credibly target.
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
    # domain-specific IC engineer / programmer suffixes
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
    # Marketing leadership — the candidate is moving away
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
    # HARD list and moved to SOFT_WARNING_TPM (below). the candidate flagged
    # "Senior Technical Program Manager" titles at top-tier target companies
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
    # a regulated wagering platform pattern — HR/comp/DEI/analytics/wagering
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
    "senior analytics manager", "wagering analyst", "wagering manager",
    "wagering operations",
    # b (a regulated wagering platform function-suffix forms)
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
    "commercial strategy senior", "regulated wagering compliance",
    "responsible gambling", "accountant", "general ledger accountant",
    "gl accountant", "ap accountant", "ar accountant",
    "accounting manager", "accounting associate", "accounting specialist",
    "systems administrator", "data product manager",
    "performance & insights", "performance and insights",
    "trading manager", "trading senior manager", "trading associate",
    "a wagering brand", "creator operations", "community operations",
    # additions (the candidate explicit: cybersecurity/risk/compliance out)
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
    # c a regulated wagering platform final tightening
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
# Title markers indicating sub-VP seniority that the candidate cannot credibly target.
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
# roles. the candidate can't take these. Source: YAML engagement_disqualifiers.
HARD_DISQUALIFIER_ENGAGEMENT: list[str] = [
    "commission only",
    "no base salary",
    "unpaid",
    "equity only",
    "equity-only",
]


# ---- LEADERSHIP_EXCEPTIONS --------------------------------------------------
# Exec / leadership phrases that, when present in the title, EXEMPT the job
# from HARD_DISQUALIFIER_TITLES_FUNCTION. They signal a role the candidate could
# legitimately hold even though some substring might overlap a blocked
# function kw (e.g., "VP of Engineering" contains "engineering"). These
# are matched via simple substring.
#
# Intentionally narrow — CMO / CFO / CHRO / CRO / Creative Director all
# REMOVED (wrong function at exec level per the candidate's explicit direction in
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
    # Principal Analyst are senior-IC thought-leadership roles the candidate
    # legitimately targets. Must exempt BEFORE the "analyst" disqualifier
    # kill fires. (Added .)
    "vp analyst", "vp, analyst", "principal analyst",
    # Platform / data exec
    "chief data", "vp of platform", "vp platform", "vp, platform",
    "head of platform", "platform director", "director of platform",
    # Online services / live service / infrastructure exec — 
    # add. Canonical the candidate targets like "VP, Platform Engineering" at
    # a large platform company and a VP-level title contain "platform engineer"
    # / "engineering" which otherwise trip the function disqualifier.
    # These phrases exempt the title substring-check so the prefilter
    # doesn't mis-gate core lane roles.
    "vp of online services", "a VP-level title", "vp, online services",
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
# role-noun. an audit of the active rows
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
    # Associate Director + technology-noun (the candidate's Test C — "Associate
    # Director, Platform AI Architect" must pass). Allow any chars in
    # between to handle "Associate Director, Platform" / "Associate
    # Director - Engineering" / etc.
    r"\bassociate\s+director\b[^.]*?\b(?:architect|platform|strategy|technology|engineering)\b",
    # Manager-level titles that in practice ARE engineering leadership.
    # "Engineering Manager" is the canonical one — the candidate's Test A.
    r"\b(?:engineering|technology|platform|technical\s+program|program)\s+manager\b",
    # Analyst-firm thought-leader titles (Gartner / Forrester / IDC).
    # "VP Analyst" is a Gartner exec-IC title; "Sr Director Analyst"
    # is the next tier up. the candidate's Test B — "Sr Director Analyst, AI
    # and Software Engineering" must pass.
    r"\bprincipal\s+analyst\b",
    r"\bvp\s+analyst\b",
    r"\bsr\.?\s+director\s+analyst\b",
    r"\bsenior\s+director\s+analyst\b",
]


# ---- PRIORITY_DISQUALIFIERS -------------------------------------------------
# Phrases that ALWAYS kill the job, even if a LEADERSHIP_EXCEPTION would
# otherwise protect it. Use sparingly for pivot-away lanes: D2C / performance
# marketing / ads work that the candidate is explicitly leaving behind.
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


# ---- WRONG_FUNCTION_EXEC_NEVER_RESCUE ---------------------------------------
# wrong-FUNCTION-at-exec-level phrases that the generic
# LEADERSHIP_WHITELIST_PATTERNS (bare \bvp\b / \bdirector\b / \bhead of\b)
# must NOT rescue. Audit of the rescore found 86% of Haiku calls were
# on jobs scoring <20 — a big chunk being exec titles in functions the candidate will
# never take (Finance Director, VP Sales, Director of Public Policy) that the
# bare-leadership whitelist exempted straight into a wasted Haiku call. These
# are matched with WORD BOUNDARIES (see algo_prefilter._NEVER_RESCUE_RE) so
# "art director" doesn't hit "smart director" etc. Each already lives in
# HARD_DISQUALIFIER_TITLES_FUNCTION; listing it here makes it kill BEFORE the
# whitelist short-circuit. Conservative: only unambiguous wrong-function execs.
WRONG_FUNCTION_EXEC_NEVER_RESCUE: list[str] = [
    # Finance
    "finance director", "director of finance", "vp of finance", "vp finance",
    "head of finance", "vice president of finance", "chief financial",
    # Sales / revenue
    "vp of sales", "vp sales", "head of sales", "sales director",
    "director of sales", "chief revenue", "account executive",
    "account manager", "regional sales", "territory sales", "sales manager",
    # Marketing / brand / growth (exec)
    "vp of marketing", "vp marketing", "head of marketing", "marketing director",
    "director of marketing", "chief marketing", "head of growth", "vp of growth",
    "brand director",
    # People / HR
    "head of people", "vp of people", "chief people", "head of hr", "vp of hr",
    "head of talent", "vp of talent", "chief talent", "head of human resources",
    # Policy / legal / comms
    "public policy", "government affairs", "government relations",
    "head of policy", "director of policy", "regulatory affairs",
    "head of communications", "vp of communications",
    "director of communications", "public relations",
    # Compliance / risk / security / audit
    "chief compliance", "head of compliance", "vp of compliance",
    "compliance director", "chief risk", "head of risk", "vp of risk",
    "head of security", "vp of security", "chief security", "head of audit",
    "internal audit", "director of audit",
    # Supply chain / procurement / workplace
    "head of supply chain", "vp of supply chain", "supply chain director",
    "head of procurement", "vp of procurement", "director of procurement",
    "head of workplace", "vp of workplace",
    # Customer success (wrong function per profile)
    "head of customer success", "vp of customer success",
    "director of customer success",
    # Creative (the candidate is technology, not creative — note: "design director"
    # is left in LEADERSHIP_EXCEPTIONS deliberately; only adding the clearly
    # non-tech creative execs here).
    "creative director", "chief creative", "art director",
]


# ---- TITLE_RELEVANCE_NOUNS --------------------
# the prefilter is otherwise a DENY-LIST — it kills only
# known-bad keywords and PASSES everything else to Haiku, so a flood of totally
# unrelated postings (Wedding Coordinator, Ultrasound Technologist, Maintenance)
# match no kill-keyword yet each cost a Haiku call to score ~0-15.
#
# This list powers a POSITIVE relevance gate (algo_prefilter.prefilter): a job
# is skipped pre-Haiku ONLY if it has NONE of {known industry, top-tier company,
# leadership title, a relevance noun in the TITLE}. a real target role always
# satisfies at least one — note "ai"/"platform"/"strategy"/"engineering" keep
# the AI-platform lane (OpenAI/Anthropic roles) even though those companies
# aren't in COMPANY_INDUSTRY_MAP. Matched WORD-BOUNDARY against the TITLE ONLY
# (see _RELEVANCE_NOUN_RE) — description is too noisy ("reports to the VP"
# would leak). Keep BROAD: a false-skip costs a real role; a false-keep only
# costs one cheap Haiku call. Variants (engineer/engineering, analyst/analytics)
# are listed explicitly to avoid stemming traps ("technolog" hits
# "technologist").
TITLE_RELEVANCE_NOUNS: list[str] = [
    # core tech-leadership lane
    "technology", "engineering", "engineer", "platform", "infrastructure",
    "architect", "architecture", "technical", "software", "systems", "system",
    "devops", "cloud", "data", "developer", "development", "digital",
    # online / platform-services lane
    "online services", "online service", "live service", "live services",
    "platform services", "backend", "distributed", "scalability",
    # role-shape lane
    "product", "strategy", "strategic", "operations", "operation", "analyst",
    "analytics", "advisor", "advisory", "interim", "fractional", "consultant",
    "consulting", "program", "transformation", "innovation",
    # exec acronyms (word-boundary matched, so safe)
    "cto", "cio", "cpo", "sre", "ai", "ml", "media",
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
# These are signals that would be wrong for SOME the candidate-style roles but
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
# title. the candidate is moving away from this work but occasional cases are
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


# High-travel / RTO mandates. the candidate has family commitments — heavy travel or
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
# kill but the candidate's audit caught senior-TPM roles at top-tier target companies that
# are legitimate cross-functional engineering-program leadership.
# Flag, let Haiku weigh whether the JD reads like real program leadership
# vs. the IC-coordinator flavor.
SOFT_WARNING_TPM: list[str] = [
    "technical program manager",
    "tpm,",  # "TPM, Platform Engineering" style titles
]


# Hands-on coding signals. the candidate is not a hands-on engineer. Most execs don't need to,
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
#   - Informing Haiku ("this role matched the candidate's marketplace_interest signals")
#   - Markdown export faceting ("here are the roles tagged ma_pmi")
#   - UI filter chips
#
# Critically: no weighted score math. Haiku does the ranking.
# -----------------------------------------------------------------------------

POSITIVE_SIGNALS: dict[str, list[str]] = {
    # Generic professional signals (diagnostic only; NOT score math).
    "strategy": ["strategy", "strategic", "roadmap", "vision", "go-to-market"],
    "architecture": ["architecture", "platform design", "systems design",
                     "technical strategy"],
    "ma_pmi": ["m&a", "post-merger integration", "acquisition integration",
               "due diligence"],
    "distributed_systems": ["distributed systems", "high availability",
                            "live service", "scalability", "real-time systems"],
    "domain_interest": ["consumer product", "platform product", "developer platform"],
    "experiential_interest": ["experiential", "location-based", "physical product"],
    "creator_interest": ["creator economy", "content tools", "media tooling"],
    "marketplace_interest": ["marketplace", "two-sided", "ecommerce"],
    "mission": ["mission-driven", "social impact", "public benefit", "nonprofit"],
    "interim": ["interim", "fractional", "advisory", "consulting"],
    "lgbtq": ["lgbtq", "inclusive", "diversity"],
    "family_friendly": ["family friendly", "work-life balance", "parental leave"],
    "local_metro": [],
    # senior_titles is referenced by literal in algo_prefilter — keep this key.
    "senior_titles": ["vp", "vice president", "head of", "director", "chief",
                      "principal", "senior director"],
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
    "consumer_platform":        10,
    "ai_infrastructure":         9,
    "platform_infra":            9,
    "consumer_marketplace":      8,
    "creator_tech":              8,
    "experiential_venue":        7,
    "media_streaming":           7,
    "analyst_firm":              7,
    "vc_pe_operating":           6,
    "accessibility_nonprofit":   6,
    "education_nonprofit":       6,
    "transaction_platform":      5,
    "hospitality_tech":          5,
    "simulation_tech":           4,
    "general_enterprise_tech":   3,
    "adtech_martech":            2,
    "crypto_web3":               1,
}


# Known company → industry bucket mapping (lowercased, suffix-stripped).
COMPANY_INDUSTRY_MAP: dict[str, str] = {
    # AI infrastructure (generic public AI / ML platform companies)
    "openai": "ai_infrastructure",
    "anthropic": "ai_infrastructure",
    "nvidia": "ai_infrastructure",
    "databricks": "ai_infrastructure",
    "hugging face": "ai_infrastructure",
    # Consumer platforms / developer platforms (replace with your targets)
    "example-platform-co": "consumer_platform",
    "example-devtools-co": "platform_infra",
    # Analyst / research firms
    "gartner": "analyst_firm",
    "forrester": "analyst_firm",
    # Enterprise software
    "example-enterprise-co": "general_enterprise_tech",
    # Marketplaces / media (generic placeholders)
    "example-marketplace-co": "consumer_marketplace",
    "example-media-co": "media_streaming",
}


# Fallback keyword classifier — if company not in COMPANY_INDUSTRY_MAP,
# run keyword match against lowercased title+company+description.
# First bucket whose keywords hit wins.
INDUSTRY_KEYWORDS: dict[str, list[str]] = {
    "consumer_platform": ["consumer platform", "consumer product", "platform company"],
    "ai_infrastructure": ["ai infrastructure", "inference platform", "model serving",
                          "llmops", "mlops platform", "foundation model", "ml platform team"],
    "platform_infra": ["platform infrastructure", "developer platform",
                       "backend platform", "platform services"],
    "consumer_marketplace": ["marketplace", "two-sided marketplace", "ecommerce platform"],
    "creator_tech": ["creator platform", "creator tools", "content platform"],
    "experiential_venue": ["experiential venue", "location-based experience",
                           "attraction operator"],
    "media_streaming": ["streaming media", "media company", "content streaming"],
    "analyst_firm": ["research firm", "analyst firm", "market research", "advisory firm"],
    "transaction_platform": ["payments platform", "transaction platform", "fintech platform"],
    "hospitality_tech": ["hospitality technology", "restaurant technology", "hotel technology"],
    "education_nonprofit": ["education nonprofit", "cultural institution", "museum"],
    "crypto_web3": ["web3", "blockchain", "cryptocurrency", "defi", "crypto"],
}


# =============================================================================
# Static company lists (preserved — all categorical, no score math).
# =============================================================================

# HRC Corporate Equality Index 100 (2025/2026). Roles at these companies
# get an HRC positive-signal flag surfaced to Haiku + UI.
HRC100_COMPANIES: list[str] = [
    # Example set of large public employers with strong inclusion ratings.
    # Replace with the companies you want flagged (e.g. from a public
    # corporate-equality index). Kept generic so no personal-interest or
    # prior-employer signal leaks through.
    "apple", "microsoft", "google", "salesforce", "ibm", "intel",
    "adobe", "cisco", "accenture", "dell", "hp", "mastercard",
    "american express", "target", "nike", "linkedin",
]


# Companies with documented crunch culture. Flag for Haiku — depending on
# role seniority the crunch exposure varies, so we don't auto-penalize.
CRUNCH_COMPANIES: list[str] = [
    # Companies with documented crunch culture. Replace with your own list.
    "example-crunch-co-a", "example-crunch-co-b",
]


# Crunch-reformed companies — same flag, but Haiku prompt notes that
# public reform reporting exists. Currently just Riot (2019+ reforms).
CRUNCH_REDUCED_PENALTY_COMPANIES: list[str] = [
    # Companies that reportedly reformed crunch culture (reduced penalty).
    "example-reformed-co",
]


# =============================================================================
# Location / commute (preserved from config/scoring.yaml).
# =============================================================================
#
# the candidate lives in the candidate's home town NJ with family commitments — commute distance is
# a practical constraint, not a preference. These lists feed into the
# prefilter's geography flag generation (remote / nyc_commutable /
# local_metro / out_of_area).
# -----------------------------------------------------------------------------

# City names that are commutable from the candidate's home town via car or NJT/PATH.
# Used for substring match against location fields.
COMMUTE_CITIES: list[str] = [
    # Towns/zones commutable from the candidate's home base.
    # Replace with your own geography.
    "example-home-city", "example-home-suburb", "example-regional-hub",
]


# Regex patterns for location classification. Kept as raw strings — the
# prefilter compiles them. Ported verbatim from config/scoring.yaml under
# `location.*`.
LOCATION_PATTERNS: dict[str, list[str]] = {
    # heavy_office: strong in-office / RTO signals (deal-breaker)
    "heavy_office": [
        r"5 days (?:a week )?in (?:the )?office", r"in-?office (?:5|five) days",
        r"full-time in office", r"on-?site only", r"100% in office",
    ],
    # out_of_area: locations outside the commute radius (deal-breaker).
    # Replace with the metros that are NOT commutable for you.
    "out_of_area": [
        r"\bexample-far-city-a\b", r"\bexample-far-city-b\b",
        r"\bexample-far-region\b",
    ],
    # local_metro: commutable home metro (best case). Replace with your city.
    "local_metro": [
        r"\bexample-home-city\b", r"\bexample-home-suburb\b",
    ],
    # regional_metro: hybrid-commutable regional hub (good).
    "regional_metro": [
        r"\bexample-regional-hub\b", r"\bexample-metro-area\b",
    ],
    # remote_us: remote roles open to US-based candidates (referenced by
    # literal in algo_prefilter — keep this key). Generic patterns.
    "remote_us": [
        r"\bremote\b", r"remote[, -]+us\b", r"remote[, -]+united states",
        r"work from home", r"\bwfh\b", r"fully remote", r"remote-first",
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
#   WATCHLIST_DREAM       flag-based       (top-tier companies + industry + level, any semantic)
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
# Preserved. Still used — the candidate's three-track job-search model.
#
#   TRACK_1_FULLTIME    default fulltime exec role
#   TRACK_2_INTERIM     interim / fractional / contract (POSITIVE_SIGNALS.interim)
#   TRACK_3_PIVOT       passion / career-pivot (the pivot/passion industries/
#                       nonprofit industries)
# -----------------------------------------------------------------------------

TRACK_3_PIVOT_INDUSTRIES = {
    # Industries treated as a "pivot/stretch" track for ranking purposes.
    "media_streaming", "transaction_platform", "adtech_martech",
    "general_enterprise_tech", "hospitality_tech",
}
