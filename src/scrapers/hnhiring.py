"""Hacker News 'Ask HN: Who is hiring?' scraper.

Strategy:
  1. Use HN Algolia to find the most recent "Ask HN: Who is hiring?"
     story by user `whoishiring` (it's a monthly thread).
  2. Fetch the story's full comment tree from Algolia's items endpoint.
  3. Each top-level comment is one job posting. Heuristically extract a
     title + company from the first line of the comment text.

The format is gloriously free-form, so parse is best-effort. The base
class's per-item try/except ensures bad comments don't tank the run; we
return None to skip rather than fabricate garbage.

two bugs surfaced by the semantic-review
audit and fixed here:

  1. URL substrings fooled _looks_like_title. A segment like
     "$182k - $272k USD - https://fetlife.com/jobs/head_of_engineering_and_.."
     was being classified as a title because the URL path contained
     "engineer" as a substring. Fix: any inline URL disqualifies a
     segment from being a title candidate (not just ones that START
     with `https://`).

  2. Title-first posts were mis-mapped. When the poster led with the
     title and followed with company/salary — e.g.
     "Head of Engineering & Infrastructure | $182k - $272k - <url>" —
     the parser blindly took parts[0]="Head of Engineering" as the
     company. Fix: if parts[0] looks strongly like a title AND a
     plausible company can be recovered from parts[1..n] or from the
     first URL's domain, swap them.

Also added: salary extraction via `common.normalize.parse_salary_range`
so the salary columns are no longer empty for HN rows.
"""
import html
import re
from typing import Iterable, Optional

import requests

from common.normalize import parse_salary_range
from scrapers.base import BaseScraper, RawJob
from scrapers.registry import register


_TAG_RE = re.compile(r"<[^>]+>")
_WHITESPACE_RE = re.compile(r"\s+")
_URL_RE = re.compile(r"^https?://", re.IGNORECASE)
# NEW: URL *anywhere* in a segment, not just at its start. Used
# to disqualify segments like "$182k - $272k USD - https://..." from
# being title candidates — previously the substring "engineer" inside
# the URL path fooled the role-keyword sniff.
_URL_ANY_RE = re.compile(r"https?://", re.IGNORECASE)
# Used by _company_from_url — captures the host after the scheme.
_URL_HOST_RE = re.compile(r"https?://([a-z0-9][a-z0-9.\-]*)", re.IGNORECASE)
# US state code suffix on a city ("Atlanta, GA", "Mountain View, CA"). When
# the string IS just a location, we don't want it as the job title.
_LOC_TAIL_RE = re.compile(r",\s*[A-Z]{2}\b")

# Words that strongly imply "this segment is a role/title". Substring match
# against the lowercased segment. Order doesn't matter — first match wins.
_ROLE_KEYWORDS = (
    "engineer", "developer", "programmer", "swe", "sre", "devops",
    "architect", "designer", "ux", "ui ",
    "manager", "director", "head of", " vp ", "vice president",
    "lead", "principal", "staff", "senior", "junior", "intern",
    "founder", "co-founder", "cofounder",
    "officer", "chief", "ceo", "cto", "cfo", "coo", "cmo",
    "scientist", "researcher",
    "analyst", "consultant", "specialist",
    "product", "marketing", "sales", "operations", "ops ",
    "machine learning",
    "writer", "editor",
    "support", "success", "advocate",
    "founding", "first ",
)

# A STRONG role signal — used only when deciding "is parts[0] a title
# instead of a company?". We intentionally exclude ambiguous words like
# "product", "ops", "sales", "support" that can appear in a company
# name ("ProductHunt", "SupportLogic").
#
# Matched with word boundaries (\b), not substring. This distinction
# matters for short tokens: naive substring match flagged "CVector" as
# title-like because "cvector" contains "cto" at positions 3-5. With
# \bcto\b, "CVector" no longer triggers while "CTO of Platform" still
# does.
_STRONG_ROLE_PATTERN = re.compile(
    r"\b(?:"
    r"engineer|engineers|developer|developers|architect|architects"
    r"|manager|managers|director|directors"
    r"|head\s+of|vp\s+of|vp|vice\s+president"
    r"|lead|leads|principal|staff|senior|junior"
    r"|founder|founders|co[\-\s]?founder|cofounder|founding"
    r"|officer|chief|ceo|cto|cfo|coo|cmo"
    r"|scientist|scientists|researcher|researchers"
    r")\b",
    re.IGNORECASE,
)

# Segments that look like locations or remote tags (NOT titles), even when
# they don't trigger a URL/state-code rule. Matched as lowercased substrings.
_LOCATION_TOKENS = (
    "remote", "onsite", "on-site", "hybrid", "anywhere",
    "us only", "us-only", "europe", "emea", "apac", "americas",
    "north america", "worldwide", "global", "us/eu", "us / eu",
)

# Generic boilerplate first-segments seen in the wild that aren't job titles.
_BOILERPLATE_TITLES = (
    "posted job description", "job description",
    "we are hiring", "we're hiring", "now hiring",
    "open positions", "open roles", "multiple roles",
)

# Strings that pass the naive "not a title, not a URL, short enough" sniff
# but are clearly NOT company names. Populated empirically from the
# city names, employment types, visa tags.
# Compared case-insensitively against the WHOLE segment (strip-stripped),
# not substring — we don't want to reject "San Francisco Bank of America"
# just because "san francisco" is in the list.
_NOT_A_COMPANY_EXACT = frozenset({
    # US cities / states (most common in HN hiring threads)
    "nyc", "ny", "new york", "new york city", "new york, ny",
    "sf", "san francisco", "san francisco, ca", "bay area",
    "la", "los angeles", "seattle", "boston", "chicago",
    "austin", "denver", "philadelphia", "portland", "atlanta",
    "nashville", "dallas", "houston", "pittsburgh", "baltimore",
    "minneapolis", "washington dc", "washington, dc", "dc",
    "miami", "detroit", "cleveland", "san diego", "phoenix",
    "brooklyn", "manhattan", "oakland", "palo alto", "mountain view",
    "new jersey", "nj",
    # International
    "london", "paris", "berlin", "cologne", "munich", "hamburg",
    "amsterdam", "dublin", "stockholm", "copenhagen", "helsinki",
    "madrid", "barcelona", "zurich", "geneva", "vienna",
    "tokyo", "singapore", "hong kong", "sydney", "melbourne",
    "toronto", "vancouver", "montreal",
    "cologne, germany", "london, uk", "berlin, germany",
    # Employment / visa tags
    "full-time", "full time", "fulltime",
    "part-time", "part time", "parttime",
    "contract", "contractor", "contract-to-hire",
    "intern", "internship",
    "freelance", "freelancer",
    "w2", "1099", "c2c",
    "visa", "visa sponsorship", "no visa",
    "permanent", "perm", "temp", "temporary",
    # Misc remote-tags that slip past _LOCATION_TOKENS
    "usa", "uk", "eu", "emea",
    "us based", "us-based",
})


def _strip_html(s: str) -> str:
    """HN serves comments as HTML. Strip tags, decode entities, normalize ws."""
    if not s:
        return ""
    text = _TAG_RE.sub(" ", s)
    text = html.unescape(text)
    return _WHITESPACE_RE.sub(" ", text).strip


def _looks_like_url(seg: str) -> bool:
    """A pipe segment that's literally a URL — never a title."""
    return bool(_URL_RE.match(seg.strip))


def _contains_url(seg: str) -> bool:
    """A segment that contains a URL anywhere. Stricter than _looks_like_url
    — added to reject title candidates like
    `"$182k - $272k USD - https://fetlife.com/jobs/head_of_engineering_and_.."`.
    The URL path in that string contained "engineer" as a substring and
    was fooling the role-keyword sniff."""
    return bool(_URL_ANY_RE.search(seg or ""))


def _looks_like_location(seg: str) -> bool:
    """Heuristic: does this segment look like a location or remote-tag,
    rather than a job title?"""
    s = seg.strip.lower
    if not s:
        return True
    # "Atlanta, GA" / "Mountain View, CA" — comma + 2-letter uppercase
    if _LOC_TAIL_RE.search(seg):
        return True
    # "Remote", "Hybrid", "US Only" etc.
    if any(tok in s for tok in _LOCATION_TOKENS):
        return True
    # Bare parenthesized region like "(US Only)" or "(Remote)"
    if s.startswith("(") and s.endswith(")"):
        return True
    return False


def _looks_like_title(seg: str) -> bool:
    """First-pass title sniff: contains a role keyword AND isn't an
    obvious URL/location/boilerplate. Generous on what counts as a role —
    HN posters get creative with titles."""
    s = seg.strip.lower
    if not s or len(s) > 200:
        return False
    if _looks_like_url(seg):
        return False
    # Any inline URL disqualifies — fixes the FetLife bug where a URL
    # path containing "engineer" fooled the role-keyword sniff.
    if _contains_url(seg):
        return False
    if any(b in s for b in _BOILERPLATE_TITLES):
        return False
    # If it looks like a location AND has no role keywords, skip.
    if _looks_like_location(seg) and not any(k in s for k in _ROLE_KEYWORDS):
        return False
    return any(k in s for k in _ROLE_KEYWORDS)


def _looks_strongly_like_title(seg: str) -> bool:
    """Stricter variant used ONLY to decide whether parts[0] is a title
    (title-first format) rather than a company. We require a *strong*
    role keyword — matched with word boundaries — so that company names
    like "CVector" (which contains "cto" as letters 3-5 but is NOT the
    acronym) don't accidentally trigger a swap."""
    if not _looks_like_title(seg):
        return False
    return bool(_STRONG_ROLE_PATTERN.search(seg))


def _looks_like_company(seg: str) -> bool:
    """Short, plausible company segment. Used to find a company when we
    decide parts[0] was a title. Rejects anything with a URL, anything
    that looks like a location or a title, and any absurd length."""
    s = (seg or "").strip
    if not s:
        return False
    if len(s) > 80:
        return False
    if _contains_url(s):
        return False
    if _looks_like_location(s):
        return False
    if _looks_like_title(s):      # segments with role keywords → not a company
        return False
    # Salary / dollar-amount strings aren't companies either.
    if "$" in s and re.search(r"\d", s):
        return False
    # Final sanity check against a curated blocklist of strings that
    # pass every heuristic above but are obviously not company names:
    # bare city names ("Chicago"), employment types ("Full-Time"),
    # visa tags ("Visa Sponsorship"). See _NOT_A_COMPANY_EXACT docstring.
    if s.lower in _NOT_A_COMPANY_EXACT:
        return False
    return True


def _company_from_url(text: str) -> Optional[str]:
    """Best-effort: pull a company name out of the first URL's domain.
    `https://fetlife.com/jobs/...` → 'Fetlife'. Strips the usual
    careers/www/boards subdomain prefixes, keeps only the root label
    before the TLD, and title-cases it.

    The naive title-casing produces 'Fetlife' not 'FetLife', but the
    downstream scoring engine does substring matching and doesn't care
    about inner capitalisation. Getting the *right root string* matters
    much more than getting the camel-case right.

    Returns None when the URL resolves to a known ATS host
    (greenhouse.io, lever.co, ashbyhq.com, etc.) — those domains are
    the hosting platform, not the employer, so picking 'Greenhouse'
    as the company would be actively misleading. In that case the
    caller should fall back to other signals (parts[1:] scan)."""
    m = _URL_HOST_RE.search(text or "")
    if not m:
        return None
    host = m.group(1).lower
    # Strip common jobs-adjacent subdomains so "careers.acme.com" → "acme".
    # `job-boards.` is Greenhouse's path-hosted boards subdomain;
    # `jobs-` / `careers-` are occasional variants.
    for prefix in ("www.", "careers.", "jobs.", "apply.",
                   "boards.", "job-boards.", "about.", "join.", "hire.",
                   "work.", "team.", "talent."):
        if host.startswith(prefix):
            host = host[len(prefix):]
    parts = host.split(".")
    if len(parts) < 2:
        return None
    root = parts[0]
    # Reject single-letter or absurdly-long roots.
    if len(root) < 2 or len(root) > 40:
        return None
    # Known ATS / job-board hosts: the root label is the platform, not
    # the employer. Returning None here triggers the caller's fallback
    # (segment scan, or skip entirely). Without this, posts linking to
    # Greenhouse/Lever/etc. would all get company='Greenhouse' etc.
    if root in _ATS_HOSTS:
        return None
    # Strip hyphens / underscores before title-casing so "acme-corp" → "AcmeCorp"
    # becomes a cleaner "Acme-corp" → "Acme-corp". Simplest: leave hyphens in
    # and just capitalize the first letter. Downstream scoring is substring-
    # based, so pretty capitalization is secondary.
    return root[:1].upper + root[1:]


# Host roots that represent a hiring platform, not an employer.
# `_company_from_url` returns None for these so the caller falls back
# to other signals instead of blindly picking the platform name.
_ATS_HOSTS = frozenset({
    "greenhouse", "lever", "ashby", "ashbyhq", "workable", "bamboohr",
    "jobvite", "breezy", "recruitee", "workday", "myworkdayjobs",
    "smartrecruiters", "icims", "taleo", "ultipro", "paylocity",
    "rippling", "gusto", "justworks", "pinpoint", "rippling",
    "linkedin", "indeed", "glassdoor", "ziprecruiter",
    "wellfound", "angellist", "ycombinator",
    "github", "gitlab", "notion", "airtable", "typeform",
    "forms",  # docs.google.com/forms/... etc
})


def _extract_title(parts: list[str]) -> Optional[str]:
    """Walk pipe-segments after the company looking for the first one
    that looks like a real role title. Returns None if nothing qualifies
    — caller should skip the post rather than fabricate a fake title."""
    for seg in parts[1:]:
        if _looks_like_title(seg):
            return seg
    return None


@register("hnhiring")
class HNHiringScraper(BaseScraper):
    source_name = "hnhiring"
    schedule = "cron(0 6 * * ? *)"
    rate_limit_rps = 1.0

    SEARCH_URL = "https://hn.algolia.com/api/v1/search_by_date"
    ITEM_URL_TEMPLATE = "https://hn.algolia.com/api/v1/items/{story_id}"

    def fetch(self) -> Iterable[dict]:
        # Find the latest "Ask HN: Who is hiring?" story by user
        # whoishiring (a dedicated bot account that posts monthly).
        self._throttle
        search = requests.get(
            self.SEARCH_URL,
            params={
                "tags": "story,author_whoishiring",
                "query": "Ask HN: Who is hiring",
                "hitsPerPage": 5,
            },
            timeout=30,
        )
        search.raise_for_status
        hits = search.json.get("hits", )
        if not hits:
            return

        # whoishiring also posts "Freelancer? Seeking freelancer?" and
        # "Who wants to be hired?" threads — filter to just hiring.
        hiring = [h for h in hits if "who is hiring" in (h.get("title") or "").lower]
        if not hiring:
            return
        story_id = hiring[0]["objectID"]

        self._throttle
        items = requests.get(
            self.ITEM_URL_TEMPLATE.format(story_id=story_id),
            timeout=60,
        )
        items.raise_for_status
        story = items.json

        # Top-level children are individual job postings. Skip nested
        # replies (those are conversation, not jobs).
        for child in story.get("children") or :
            if child.get("text"):
                # Pass the parent story id along for the URL.
                child["_story_id"] = story_id
                yield child

    def parse(self, payload: dict) -> Optional[RawJob]:
        text = _strip_html(payload.get("text") or "")
        if not text:
            return None

        # The HN convention for Who-is-hiring posts is roughly:
        #   "CompanyName | Role | Location | Remote/Onsite | Stack"
        # We split on '|' and treat the first segment as company — UNLESS
        # parts[0] clearly looks like a title (title-first format — see
        # module docstring).
        first_line, _, rest = text.partition(". ")
        head = first_line if "|" in first_line else text.split("\n", 1)[0]
        parts = [p.strip for p in head.split("|") if p.strip]

        if not parts:
            return None

        # ---------- Title / company assignment ----------
        # Three cases, in precedence order:
        #
        #   (A) parts[0] looks strongly like a title AND we can find a
        #       plausible company in parts[1:] or in a URL domain.
        #       → title=parts[0], company=<best candidate>
        #
        #   (B) Original layout: parts[0]=company, title somewhere in
        #       parts[1:].
        #
        #   (C) Nothing works → return None (let the base-class try/except
        #       silently skip this comment).
        company: Optional[str] = None
        title: Optional[str] = None

        if len(parts) >= 2 and _looks_strongly_like_title(parts[0]):
            # Title-first format. The URL domain is a *much* stronger
            # signal than a segment-scan — a URL like `fetlife.com` is
            # unambiguously the employer, whereas a raw segment might be
            # a city ("Chicago") or employment type ("Full-Time") that
            # slipped past `_looks_like_company`. So we try the URL first,
            # and only fall back to scanning parts[1:] when no URL is present.
            company_candidate = _company_from_url(text)
            if company_candidate is None:
                company_candidate = next(
                    (p for p in parts[1:] if _looks_like_company(p)), None
                )
            if company_candidate:
                title = parts[0]
                company = company_candidate
            else:
                # parts[0] is clearly a title but we can't find a company
                # anywhere — skip rather than fall through (which would
                # set company=parts[0]=the-title and produce nonsense).
                return None

        if title is None:
            # Fall back to original layout.
            company = parts[0]
            title = _extract_title(parts)
            if title is None:
                return None

        # Heuristic guard: if "company" is implausibly long (>80 chars)
        # the post probably doesn't follow the convention — skip it.
        if not company or len(company) > 80 or len(title) > 200:
            return None

        # ---------- Location sniff ----------
        # Keep the original behaviour — scan parts[2:] for a location-ish
        # segment. This is approximate (HN posters put location anywhere)
        # but better than nothing.
        location = next(
            (p for p in parts[2:] if any(
                token in p.lower
                for token in ("remote", "ny", "sf", "us", "uk", "eu", "europe", "onsite", "hybrid")
            )),
            None,
        )
        is_remote = "remote" in head.lower

        # ---------- Salary ----------
        # Salary is embedded freeform in the head. Prefer the head over
        # the full body so we don't pick up narrative numbers like
        # "raised $10m-$20m series B" from the description.
        salary_min, salary_max = parse_salary_range(head)

        story_id = payload.get("_story_id") or payload.get("parent_id")
        comment_id = payload.get("objectID") or payload.get("id")
        if not comment_id:
            return None
        url = f"https://news.ycombinator.com/item?id={comment_id}"

        return RawJob(
            native_id=str(comment_id),
            title=title,
            company=company,
            url=url,
            location=location,
            description=text,
            posted_at=payload.get("created_at"),
            remote=is_remote,
            salary_min=salary_min,
            salary_max=salary_max,
            raw=payload,
        )
