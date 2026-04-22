"""/ Claude Haiku semantic-fit scorer.

Public entry point:
    semantic_score(job: dict, prefilter_output: dict | None = None) -> Optional[dict]
        Returns a dict with the structured fields Haiku emits:
          {
            "score": int 0-100,
            "rationale": str,
            "work_mode": "remote"|"hybrid"|"onsite"|"unclear",
            "role_family_match": "strong"|"partial"|"none"|"unclear",
            "industry_match":    "strong"|"partial"|"none"|"unclear",
            "geography_match":   "reachable"|"unreachable"|"unclear",
            "level_match":       "match"|"under"|"over"|"unclear",
            "watchlist_dream":   bool,
            "life_fit_concerns": list[str],
            "model":             str,
            "scored_at":         str (ISO UTC timestamp),
          }
        Returns None if scoring was skipped/failed (caller should then
        fall back to a "NEEDS_REVIEW" tier per tiering).

architecture change:
    - Algo layer is now a BINARY PREFILTER (see scoring/algo_prefilter.py).
    - Haiku is the SOLE RANKER — the score it returns IS the final score.
    - The optional `prefilter_output` dict is injected into the user
      message so Haiku can see which flags fired (positive signals,
      soft warnings, dream-company tag, detected industry, etc.).

Why a separate module:
    - Keeps anthropic SDK + SSM dependency out of the prefilter, so
      the prefilter stays unit-testable without mocking AWS or the
      Anthropic API.
    - Lets `combined.score_combined` decide *whether* to call us
      based on prefilter outcome, cache freshness, or a kill switch
      in scoring.yaml.

Failure modes (all return None — never raise):
    - SDK not installed                 → import-time fallback
    - SSM lookup of API key fails        → log + None
    - Anthropic API request fails        → log + None
    - Response not valid JSON            → log + None
    - Response missing score/rationale   → log + None

Cost / rate limit:
    - One call per `semantic_score` invocation.
    - Module-level rate-limiter ensures min spacing per
      scoring.yaml::semantic.rate_limit_sleep_ms within a single
      Lambda container's lifetime.
"""
from __future__ import annotations

import json
import os
import re
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import yaml

from common.logging import log
from .keywords import CFG  # the parsed scoring.yaml dict


# ------------------------------------------------------------------
# Config — read once at module import, mirrors the keywords.py pattern.
# ------------------------------------------------------------------

_SEMANTIC_CFG: dict = CFG.get("semantic", {}) or {}


def _semantic_enabled -> bool:
    """Kill switch — set semantic.enabled: false in scoring.yaml to bypass."""
    return bool(_SEMANTIC_CFG.get("enabled", False))


def _model -> str:
    return _SEMANTIC_CFG.get("model", "claude-haiku-4-5-20251001")


def _max_tokens -> int:
    return int(_SEMANTIC_CFG.get("max_tokens", 200))


def _temperature -> float:
    return float(_SEMANTIC_CFG.get("temperature", 0.0))


def _rate_limit_sleep_seconds -> float:
    """Min spacing between API calls inside ONE Lambda container."""
    return float(_SEMANTIC_CFG.get("rate_limit_sleep_ms", 500)) / 1000.0


def _ssm_key_name -> str:
    return _SEMANTIC_CFG.get("ssm_key_name", "/jobs-aggregator/anthropic_api_key")


# ------------------------------------------------------------------
# Candidate profile — the system prompt fed to Haiku.
# Loaded lazily so unit tests can stub the path or skip loading entirely.
# ------------------------------------------------------------------

_PROFILE_CACHE: Optional[dict] = None


def _find_profile_path -> Path:
    """Locate candidate_profile.yaml across dev + Lambda layouts.

    Candidate order (first match wins):
      1. /opt/candidate_profile.yaml
         — Lambda runtime: ConfigLayer mounts the repo-root config/
           directory's contents here. This is the production path
.
      2. <repo-root>/config/candidate_profile.yaml
         — Local dev + pytest: the canonical source of truth.
      3. <src-root>/config/candidate_profile.yaml
         — Legacy src/config/ fallback, retained for safety while the
           migration settles. Can be removed once we're confident the
           Lambda layer is the only runtime path (slated for deletion
           in the same PR as this change).
      4. /var/task/config/candidate_profile.yaml
         — Legacy flat-bundle path when CodeUri: src/ bundled
           src/config/ under /var/task/. Kept as a belt-and-braces
           fallback during the migration.
    """
    here = Path(__file__).resolve.parent
    candidates = [
        Path("/opt/candidate_profile.yaml"),                       # Lambda layer (prod)
        here.parent.parent / "config" / "candidate_profile.yaml",  # repo-root (dev)
        here.parent / "config" / "candidate_profile.yaml",         # legacy src/config/
        Path("/var/task/config/candidate_profile.yaml"),           # legacy runtime
    ]
    for p in candidates:
        if p.exists:
            return p
    raise FileNotFoundError(
        "candidate_profile.yaml not found — checked: "
        + ", ".join(str(c) for c in candidates)
    )


def _load_profile -> dict:
    """Parse + cache candidate_profile.yaml."""
    global _PROFILE_CACHE
    if _PROFILE_CACHE is None:
        with open(_find_profile_path, "r", encoding="utf-8") as fh:
            _PROFILE_CACHE = yaml.safe_load(fh)
    return _PROFILE_CACHE


def _system_prompt -> str:
    return _load_profile["system_prompt"]


# ------------------------------------------------------------------
# SSM key fetch — cached per Lambda container. boto3 clients are reused
# automatically; we cache the *value* so we don't re-hit SSM per call.
# ------------------------------------------------------------------

_API_KEY_CACHE: Optional[str] = None


def _get_api_key -> Optional[str]:
    """Return the Anthropic API key from SSM, or None if unavailable.

    Tries env var ANTHROPIC_API_KEY first (useful for local dev / CLI
    testing) before falling back to SSM. Returns None on any error so
    the caller can degrade to algo-only.
    """
    global _API_KEY_CACHE
    if _API_KEY_CACHE is not None:
        return _API_KEY_CACHE

    # 1. Env var override — for `python scripts/score_job.py` locally.
    env = os.environ.get("ANTHROPIC_API_KEY")
    if env:
        _API_KEY_CACHE = env.strip
        return _API_KEY_CACHE

    # 2. SSM Parameter Store — production path.
    try:
        import boto3
        ssm = boto3.client("ssm", region_name=os.environ.get("AWS_REGION", "us-east-1"))
        resp = ssm.get_parameter(Name=_ssm_key_name, WithDecryption=True)
        _API_KEY_CACHE = resp["Parameter"]["Value"]
        return _API_KEY_CACHE
    except Exception as exc:
        log.warn(
            "semantic_ssm_key_fetch_failed",
            ssm_key=_ssm_key_name,
            error=str(exc),
        )
        return None


# ------------------------------------------------------------------
# Anthropic client — lazy import so the SDK is only required when
# semantic scoring is actually invoked.
# ------------------------------------------------------------------

_CLIENT_CACHE = None


def _get_client:
    """Build an anthropic.Anthropic client, cached per Lambda container.

    Returns None if the SDK isn't installed or no API key is available
    — the caller treats either as 'semantic disabled'.
    """
    global _CLIENT_CACHE
    if _CLIENT_CACHE is not None:
        return _CLIENT_CACHE

    api_key = _get_api_key
    if not api_key:
        return None

    try:
        # Lazy import: keeps tests + local-dev environments happy when
        # the anthropic SDK isn't installed in the venv.
        from anthropic import Anthropic
        # max_retries=0 disables the SDK's built-in 2-retry
        # exponential backoff on 429s. Without this, every 429 triggers
        # 3 actual HTTP requests (original + 2 retries with 0/0.5/2s
        # backoff), tripling token-budget burn during a rate-limit storm
        # and pushing us further past the 450K tpm Anthropic cap.
        # We already pace at rate_limit_sleep_ms (scoring.yaml::semantic)
        # in the outer loop and treat any thrown error as
        # semantic_api_failed=true, which rescore.py correctly handles
        # by skipping the DDB write so the row gets re-tried on the
        # next rescore pass. Failing fast is safer than retry-storming.
        _CLIENT_CACHE = Anthropic(api_key=api_key, max_retries=0)
        return _CLIENT_CACHE
    except ImportError as exc:
        log.warn(
            "semantic_sdk_missing",
            error=str(exc),
            hint="add `anthropic` to src/requirements.txt and redeploy",
        )
        return None


# ------------------------------------------------------------------
# Rate limiter — ensures we don't burst Anthropic if the same Lambda
# container scores many jobs in a row (e.g. rescore_all batch run).
# ------------------------------------------------------------------

_LAST_CALL_AT: float = 0.0


def _throttle -> None:
    """Sleep so we maintain at least rate_limit_sleep_seconds between calls."""
    global _LAST_CALL_AT
    min_interval = _rate_limit_sleep_seconds
    if min_interval <= 0:
        return
    elapsed = time.monotonic - _LAST_CALL_AT
    if elapsed < min_interval:
        time.sleep(min_interval - elapsed)
    _LAST_CALL_AT = time.monotonic


# ------------------------------------------------------------------
# Job-text builder — what we actually send to Haiku.
# Keep concise: title + company + location + first ~3000 chars of JD.
# Haiku at $0.25/1M input tokens means full JDs add up; truncating keeps
# us in the $0.0005/job budget the spec calls for.
# ------------------------------------------------------------------

_JD_MAX_CHARS = 3000


def _format_prefilter_summary(prefilter_output: dict) -> str:
    """Render the prefilter verdict as a human-readable block for Haiku.

    Haiku sees which signals fired so it can anchor its score.
    We keep the block short (one line per section, comma-separated lists)
    because every token here costs input-token money on the Haiku call
    and the original author's budget target is ~$0.0005/job.

    Only includes sections that have content — an empty block is cleaner
    than "positive_signals: " repeated noise.
    """
    lines: list[str] = ["--- Pre-filter context ---"]

    track = prefilter_output.get("track")
    if track:
        lines.append(f"Track: {track}")

    industry = prefilter_output.get("industry")
    industry_score = prefilter_output.get("industry_score")
    if industry:
        if industry_score is not None:
            lines.append(f"Detected industry: {industry} (preference score {industry_score}/10)")
        else:
            lines.append(f"Detected industry: {industry}")

    loc_flag = prefilter_output.get("location_flag")
    if loc_flag:
        lines.append(f"Location flag: {loc_flag}")

    company_flags: list[str] = 
    if prefilter_output.get("is_dream_company"):
        company_flags.append("dream-tier")
    if prefilter_output.get("is_hrc100"):
        company_flags.append("HRC 100")
    if prefilter_output.get("is_crunch_co"):
        company_flags.append("crunch-company risk")
    if company_flags:
        lines.append(f"Company flags: {', '.join(company_flags)}")

    pos = prefilter_output.get("positive_signals") or 
    if pos:
        lines.append(f"Positive signals fired: {', '.join(pos)}")

    soft = prefilter_output.get("soft_warnings") or 
    if soft:
        lines.append(f"Soft warnings fired: {', '.join(soft)}")

    lines.append("--- End pre-filter ---")
    return "\n".join(lines)


def _build_user_message(
    job: dict,
    prefilter_output: dict | None = None,
) -> str:
    """Format a single job (plus optional prefilter verdict) for Haiku.

    when prefilter_output is provided, we prepend a short
    "--- Pre-filter context ---" block so Haiku can see which flags
    the deterministic layer already tripped — positive signals, soft
    warnings, dream-tier company, detected industry, etc. Haiku then
    rates the role holistically and returns the 9-field schema defined
    in config/candidate_profile.yaml::### OUTPUT FORMAT.
    """
    title       = (job.get("title") or "").strip
    company     = (job.get("company") or "").strip
    location    = (job.get("location") or "").strip
    remote      = "yes" if job.get("remote") else "no"
    salary_min  = job.get("salary_min")
    salary_max  = job.get("salary_max")
    description = (job.get("description") or "").strip

    if len(description) > _JD_MAX_CHARS:
        description = description[:_JD_MAX_CHARS] + "\n\n[…description truncated…]"

    salary_line = ""
    if salary_min or salary_max:
        lo = f"${int(salary_min):,}" if salary_min else "?"
        hi = f"${int(salary_max):,}" if salary_max else "?"
        salary_line = f"\nSalary: {lo} - {hi}"

    # Optional prefilter summary block — empty string if no context provided.
    prefilter_block = ""
    if prefilter_output:
        prefilter_block = _format_prefilter_summary(prefilter_output) + "\n\n"

    return (
        f"{prefilter_block}"
        f"Title: {title}\n"
        f"Company: {company}\n"
        f"Location: {location}\n"
        f"Remote: {remote}"
        f"{salary_line}\n\n"
        f"Description:\n{description}\n\n"
        "Score this posting per the rubric in the system prompt. "
        "Reply with valid JSON only — all nine fields required: "
        '{"score": <int 0-100>, '
        '"work_mode": "remote|hybrid|onsite|unclear", '
        '"rationale": "<one sentence, <=180 chars>", '
        '"role_family_match": "strong|partial|none", '
        '"industry_match": "strong|partial|none", '
        '"geography_match": "reachable|unreachable|unclear", '
        '"level_match": "match|under|over|unclear", '
        '"watchlist_dream": true|false, '
        '"life_fit_concerns": [<short phrases>]}'
    )


# ------------------------------------------------------------------
# Response parser — Haiku is reliable but not perfect at JSON-only
# output. Strip markdown fences, locate the {…} substring, parse.
# ------------------------------------------------------------------

_JSON_BLOCK_RE = re.compile(r"\{.*\}", re.DOTALL)

# Whitelist of allowed work_mode values. Anything else collapses to "unclear"
# so the frontend can render a single known set of chips.
_WORK_MODE_ALLOWED = {"remote", "hybrid", "onsite", "unclear"}


def _normalize_work_mode(raw: object) -> str:
    """Coerce Haiku's work_mode field to one of the whitelisted values.

    Haiku is instructed to reply with remote/hybrid/onsite/unclear but may
    drift (e.g. "remote-first", "in-office"). Normalize via substring match
    so obvious synonyms still land in the right bucket.
    """
    if not isinstance(raw, str):
        return "unclear"
    lo = raw.strip.lower
    if not lo:
        return "unclear"
    if lo in _WORK_MODE_ALLOWED:
        return lo
    # Substring synonyms → bucket
    if "remote" in lo or "anywhere" in lo or "distributed" in lo:
        return "remote"
    if "hybrid" in lo:
        return "hybrid"
    if "onsite" in lo or "on-site" in lo or "in office" in lo or "in-office" in lo:
        return "onsite"
    return "unclear"


# Whitelists for the structured-output fields. Anything outside
# these sets gets coerced to "unclear" so downstream tooling can rely on
# a known finite set of values.
_ROLE_FAMILY_ALLOWED = {"strong", "partial", "none", "unclear"}
_INDUSTRY_MATCH_ALLOWED = {"strong", "partial", "none", "unclear"}
_GEO_MATCH_ALLOWED = {"reachable", "unreachable", "unclear"}
_LEVEL_MATCH_ALLOWED = {"match", "under", "over", "unclear"}


def _coerce_enum(raw: object, allowed: set[str], default: str = "unclear") -> str:
    """Coerce Haiku's enum-style field to the whitelisted set.

    Haiku is reliable but can occasionally drift (e.g. "strong fit"
    instead of "strong"). We normalize via lowercase + strip and then
    try a substring match against the allowed values before defaulting
    to "unclear" — the original author would rather see "unclear" than a silently
    dropped field.
    """
    if not isinstance(raw, str):
        return default
    lo = raw.strip.lower
    if not lo:
        return default
    if lo in allowed:
        return lo
    for val in allowed:
        if val != "unclear" and val in lo:
            return val
    return default


def _coerce_bool(raw: object, default: bool = False) -> bool:
    """Coerce Haiku's bool-style field to a real Python bool.

    Haiku usually emits true/false but JSON parsers also accept "true"/
    "false" strings and 0/1 ints. Any other value returns `default`.
    """
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, (int, float)):
        return bool(raw)
    if isinstance(raw, str):
        lo = raw.strip.lower
        if lo in {"true", "yes", "1"}:
            return True
        if lo in {"false", "no", "0", ""}:
            return False
    return default


def _coerce_str_list(raw: object, max_items: int = 8, max_len: int = 120) -> list[str]:
    """Coerce Haiku's list-of-strings field to a clean list[str].

    Defensive against: None, single string instead of list, non-string
    list items, overly long or excessively many items. Anything weird
    collapses to an empty list so the downstream consumer always sees
    list[str].
    """
    if raw is None:
        return 
    if isinstance(raw, str):
        # Tolerate a single phrase returned as a bare string.
        s = raw.strip
        return [s[:max_len]] if s else 
    if not isinstance(raw, list):
        return 
    out: list[str] = 
    for item in raw[:max_items]:
        if not isinstance(item, str):
            continue
        s = item.strip
        if s:
            out.append(s[:max_len])
    return out


def _salvage_truncated_json(raw: str) -> Optional[str]:
    """Close any open strings/arrays/objects in a truncated JSON blob.

    Haiku occasionally hits max_tokens mid-`life_fit_concerns` string,
    leaving us with a document like:

        {"score": 28, ..., "life_fit_concerns": ["Progra

    This function walks the chars tracking a simple stack of open
    containers, then emits the matching closers (`"`, `]`, `}`) at
    the end so `json.loads` can parse the result. The last partial
    string becomes a truncated-but-valid string value; any missing
    fields get filled by the caller's defaulting pass.

    Returns the rebuilt candidate string, or None if we can't find
    an opening `{` (nothing to salvage from).

    Why this exists: bumping max_tokens reduces the frequency of
    truncation but doesn't eliminate it. The parser needs to survive
    the edge cases so one unparseable Haiku response doesn't leave
    a row permanently unrescored.
    """
    start = raw.find("{")
    if start < 0:
        return None
    s = raw[start:]

    # Track what's open. Each entry is one of: '"', '{', '['.
    stack: list[str] = 
    in_string = False
    escape    = False

    for ch in s:
        if escape:
            escape = False
            continue
        if in_string:
            if ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
                if stack and stack[-1] == '"':
                    stack.pop
            continue
        if ch == '"':
            in_string = True
            stack.append('"')
        elif ch == "{" or ch == "[":
            stack.append(ch)
        elif ch == "}":
            if stack and stack[-1] == "{":
                stack.pop
        elif ch == "]":
            if stack and stack[-1] == "[":
                stack.pop

    # Build the closers in reverse stack order.
    _closers = {'"': '"', "{": "}", "[": "]"}
    tail = "".join(_closers[frame] for frame in reversed(stack))
    return s + tail


def _parse_response(raw: str) -> Optional[dict]:
    """Extract the nine-field JSON object from Haiku's reply.

    Returns a dict with all nine fields populated (defaults applied
    when a field is missing or malformed) or None if no usable JSON
    object can be parsed or if score isn't a number in [0, 100].

    Tolerates: leading/trailing whitespace, ```json fences, brief
    preamble, single-string list items, string bools, AND truncated
    responses (max_tokens cut-off mid-value). Only `score` is strictly
    required — every other field gets a safe default so scoring
    doesn't fail if Haiku drops a field during rollout.
    """
    if not raw:
        return None

    # Try a clean parse first.
    cleaned = raw.strip
    # Strip ```json … ``` fences if present.
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```[a-zA-Z]*\n?", "", cleaned)
        cleaned = re.sub(r"\n?```\s*$", "", cleaned)

    candidates = [cleaned]
    # Greedy {…} extraction as a fallback.
    m = _JSON_BLOCK_RE.search(raw)
    if m:
        candidates.append(m.group(0))
    # Salvage-truncated fallback: handles the case where the
    # response was cut off before Haiku emitted its closing `]}`. If
    # we can close the containers cleanly, we recover `score` +
    # everything before the truncation. Tried LAST so well-formed
    # responses go through the fast path first.
    salvaged = _salvage_truncated_json(cleaned)
    if salvaged and salvaged != cleaned:
        candidates.append(salvaged)

    for candidate in candidates:
        try:
            obj = json.loads(candidate)
        except (ValueError, TypeError):
            continue
        if not isinstance(obj, dict):
            continue
        score = obj.get("score")
        if not isinstance(score, (int, float)):
            continue
        score_int = max(0, min(100, int(round(float(score)))))

        rationale = obj.get("rationale")
        work_mode = _normalize_work_mode(obj.get("work_mode"))

        # structured fields — all optional/defaulted so a
        # slightly degraded Haiku response still parses cleanly.
        role_family = _coerce_enum(
            obj.get("role_family_match"), _ROLE_FAMILY_ALLOWED
        )
        industry_match = _coerce_enum(
            obj.get("industry_match"), _INDUSTRY_MATCH_ALLOWED
        )
        geo_match = _coerce_enum(
            obj.get("geography_match"), _GEO_MATCH_ALLOWED
        )
        level_match = _coerce_enum(
            obj.get("level_match"), _LEVEL_MATCH_ALLOWED
        )
        watchlist_dream = _coerce_bool(obj.get("watchlist_dream"), default=False)
        life_fit_concerns = _coerce_str_list(obj.get("life_fit_concerns"))

        return {
            "score":             score_int,
            "rationale":         (rationale or "").strip[:240],
            "work_mode":         work_mode,
            "role_family_match": role_family,
            "industry_match":    industry_match,
            "geography_match":   geo_match,
            "level_match":       level_match,
            "watchlist_dream":   watchlist_dream,
            "life_fit_concerns": life_fit_concerns,
        }

    return None


# ------------------------------------------------------------------
# Public entry point
# ------------------------------------------------------------------

def semantic_score(
    job: dict,
    prefilter_output: dict | None = None,
) -> Optional[dict]:
    """Get a Haiku semantic-fit score for one job.

    signature: accepts an optional `prefilter_output` from
    `scoring/algo_prefilter.py::prefilter`. When provided, the
    prefilter flags are rendered into the user message so Haiku can
    anchor its score against the deterministic signals.

    Args:
        job: normalized job row with title/company/location/description/
            salary_min/salary_max/remote fields. Same shape the algo
            layer consumes.
        prefilter_output: optional dict from the binary prefilter. If
            the caller only wants a raw Haiku rating (e.g., from
            `scripts/score_job.py --semantic-only`), pass None and the
            user message omits the prefilter block.

    Returns:
        Dict with all 9 Haiku fields + "model" + "scored_at" on
        success, OR None if semantic scoring was skipped (kill switch
        off, no API key, no SDK) or failed (network error, parse error).

    The caller is responsible for caching, tier-routing, and writing
    the result back to DynamoDB.
    """
    if not _semantic_enabled:
        return None

    client = _get_client
    if client is None:
        # Either no API key or no SDK — algo-only fallback.
        return None

    try:
        system_prompt = _system_prompt
    except FileNotFoundError as exc:
        log.warn("semantic_profile_missing", error=str(exc))
        return None

    user_msg = _build_user_message(job, prefilter_output=prefilter_output)

    try:
        _throttle
        resp = client.messages.create(
            model       = _model,
            max_tokens  = _max_tokens,
            temperature = _temperature,
            system      = system_prompt,
            messages    = [{"role": "user", "content": user_msg}],
        )
    except Exception as exc:
        # Network blip, rate-limit, 5xx, malformed request — never raise.
        log.warn(
            "semantic_api_call_failed",
            job_id=job.get("job_id"),
            model=_model,
            error=str(exc),
            traceback=traceback.format_exc(limit=3),
        )
        return None

    # The Anthropic Messages API returns a list of content blocks; we
    # asked for plain text so block[0].text is what we want.
    raw_text = ""
    try:
        if resp.content:
            block = resp.content[0]
            raw_text = getattr(block, "text", "") or ""
    except Exception as exc:
        log.warn(
            "semantic_response_unpack_failed",
            job_id=job.get("job_id"),
            error=str(exc),
        )
        return None

    parsed = _parse_response(raw_text)
    if parsed is None:
        log.warn(
            "semantic_response_unparseable",
            job_id=job.get("job_id"),
            raw_text=raw_text[:500],
        )
        return None

    parsed["model"]     = _model
    parsed["scored_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return parsed
