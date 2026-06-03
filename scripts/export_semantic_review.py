"""
export_semantic_review.py

One-shot export: scans the Jobs table and writes a single document the original author
can read end-to-end to audit scoring end-to-end.

architecture reminder: the algo layer is now a BINARY PREFILTER
(pass/fail), not a weighted scorer. If the prefilter passes, Haiku's
score IS the final score. If the prefilter fails, final=0. If Haiku
errored, the row is flagged "needs_review".

Given that, the old "algo vs semantic delta" framing no longer applies.
Instead, this script emits THREE VIEWS, each targeted at a different
maintenance workflow:

  View 1 - Apply queue
      Prefilter passed AND Haiku returned a score. The actionable feed,
      sub-grouped by semantic score band so the original author can skim top-to-bottom
      (A: 80-100, B: 65-79, C: 50-64, D: <50). Use this for: daily
      review, deciding what to apply to.

  View 2 - Needs review / dream-co
      Either (a) Haiku errored / was unavailable (tier == needs_review),
      or (b) watchlist_dream flag fired (prefilter passed, Haiku gave a
      low score, but the company is on the dream list - the original author should
      eyeball it rather than trust the auto verdict). Use this for:
      weekly manual triage.

  View 3 - Prefilter rejects
      Rows where passed_prefilter == False. Sub-grouped by
      prefilter_reason (e.g. wrong_function, wrong_level, geo_blocked,
      industry_blocked) so the original author can see AT A GLANCE whether the
      prefilter is over-gating. Use this for: tuning the binary gate.

How to run
----------
    # PowerShell / any shell - pass the table env var:
    $env:JOBS_TABLE="jobs-aggregator-JobsTable-1EV6UZWFB7MVY"
    python scripts/export_semantic_review.py

    # Optional flags:
    #   --status active|saved|applied|archived   (default: active)
    #   --min-semantic 0                         (drop rows below this cutoff)
    #   --max-rows 0                             (0 = no cap)
    #   --out docs/semantic_review.md            (markdown destination)
    #   --csv docs/semantic_review.csv           (csv companion - full data)
    #   --views 1,2,3                            (subset of views to emit)

What it writes
--------------
A single markdown document with three top-level `## View N - ...`
sections. Within each view the rows are sub-grouped (by band or by
prefilter reason) and sorted most-interesting first. Each entry shows:

    - Title, Company, Location
    - Salary (if present), Work mode, Engagement type
    - Source, Posted
    - Prefilter verdict (pass/fail + reason)
    - Semantic final score + tier + track
    - Structured semantic fields (role_family_match, industry_match,
      geography_match, level_match, watchlist_dream, life_fit_concerns)
    - Positive signals, soft warnings, hard gates from the prefilter
    - FULL rationale (not the 220-char card snippet)
    - URL

A companion CSV is also emitted - same rows, flat columns,
parseable for any downstream diff / spreadsheet work.
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable

import boto3
from boto3.dynamodb.conditions import Attr


# ---------------------------------------------------------------------------
# View identifiers - used for grouping and for the CSV "view" column.
# ASCII hyphens (not em dashes) so Windows cp1252 stderr doesn't mangle
# the labels during argparse --help and progress prints.
# ---------------------------------------------------------------------------
VIEW_APPLY     = "View 1 - Apply queue"
VIEW_REVIEW    = "View 2 - Needs review / dream-co"
VIEW_REJECTS   = "View 3 - Prefilter rejects"

# Stable ordering for the markdown output (and for the TOC).
VIEW_ORDER: list[str] = [VIEW_APPLY, VIEW_REVIEW, VIEW_REJECTS]


# Sub-groups for View 1 (apply queue): semantic-score bands.
# Cut points mirror the tier thresholds:
#   T1_APPLY_NOW  ≥ 78   → A band
#   T2_APPLY_2WK  ≥ 65   → B band
#   T3_MONITOR    ≥ 50   → C band
#   watchlist     ≥ 35   → D band (still in apply queue so the original author can
#                                   eyeball but clearly lower priority)
APPLY_BANDS: list[tuple[str, int, int]] = [
    # (label, lo_inclusive, hi_inclusive)
    ("A. Strong match (Semantic 80-100)",   80, 100),
    ("B. Solid match  (Semantic 65-79)",    65, 79),
    ("C. Monitor      (Semantic 50-64)",    50, 64),
    ("D. Low-score passes (Semantic <50)",   0, 49),
]


# ---------------------------------------------------------------------------
# Small pure helpers
# ---------------------------------------------------------------------------
def _to_int(v: Any) -> int | None:
    """Coerce DDB Decimals / strs / ints to plain int; None on failure."""
    if v is None or v == "":
        return None
    try:
        return int(Decimal(str(v)))
    except Exception:
        return None


def _to_bool(v: Any) -> bool:
    """Coerce DDB BOOL / numeric / string-ish values to a Python bool."""
    if v is None:
        return False
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float, Decimal)):
        return int(v) != 0
    s = str(v).strip.lower
    return s in ("true", "t", "yes", "y", "1")


def _as_list(v: Any) -> list[str]:
    """Normalise DDB L/S values (or None) to a list of strings."""
    if v is None:
        return 
    if isinstance(v, list):
        return [str(x) for x in v if x is not None and str(x).strip]
    if isinstance(v, (set, tuple)):
        return [str(x) for x in v if x is not None and str(x).strip]
    # Single value — wrap.
    s = str(v).strip
    return [s] if s else 


def _fmt_salary(lo: Any, hi: Any, cur: Any) -> str:
    """
    Render the salary band. Accepts a pair of numeric values or a single
    point value. Returns '' when both are missing so the downstream
    render can skip the line cleanly.
    """
    lo_i = _to_int(lo)
    hi_i = _to_int(hi)
    cur_s = (str(cur).strip or "USD") if cur else "USD"
    if not lo_i and not hi_i:
        return ""
    def pretty(n: int) -> str:
        if n >= 1000:
            return f"${n / 1000:.0f}k"
        return f"${n}"
    if lo_i and hi_i:
        return f"{pretty(lo_i)} – {pretty(hi_i)} {cur_s}"
    return f"{pretty(lo_i or hi_i)} {cur_s}"


def _apply_band_for(semantic: int | None) -> str:
    """Band label for a row in View 1 (apply queue)."""
    if semantic is None:
        # View 1 should never contain rows without semantic_score — those
        # are routed to View 2 — but keep this safe as a fallback.
        return "D. Low-score passes (Semantic <50)"
    for label, lo, hi in APPLY_BANDS:
        if lo <= semantic <= hi:
            return label
    return "D. Low-score passes (Semantic <50)"


def _short_posted(iso_str: Any) -> str:
    """Take a posted_at ISO string and return just the YYYY-MM-DD portion."""
    if not iso_str:
        return "?"
    s = str(iso_str)
    return s[:10] if len(s) >= 10 else s


def _md_escape_inline(s: str) -> str:
    """
    Minimal Markdown-safe inlining for titles/companies — avoid breaking
    the table of contents or the heading syntax. We only strip characters
    that cause ambiguity, not the ones (e.g. parens) that render fine.
    """
    if not s:
        return ""
    return (
        str(s)
        .replace("\r", " ")
        .replace("\n", " ")
        .replace("|", "/")           # would split a table column
        .strip
    )


def _md_block_rationale(text: str) -> str:
    """
    Return the full rationale rendered as a Markdown blockquote. We keep
    newlines as real line breaks so multi-sentence rationales don't run
    together in the output.
    """
    if not text:
        return "_(no rationale)_"
    t = str(text).strip
    # Collapse tabs + extra spaces but preserve paragraph breaks.
    lines = [ln.strip for ln in t.splitlines if ln.strip]
    return "\n".join(f"> {ln}" for ln in lines)


def _anchor(label: str) -> str:
    """GitHub-style slug for an in-doc anchor link."""
    anchor = label.lower
    for bad in "./·—":
        anchor = anchor.replace(bad, "")
    return "-".join(anchor.split)


# ---------------------------------------------------------------------------
# View assignment — the routing rule.
# ---------------------------------------------------------------------------
def assign_view(row: dict) -> str:
    """
    Decide which of the 3 views a row belongs to. Precedence matters:

      1. `tier == "needs_review"` — Haiku errored. → View 2.
      2. `watchlist_dream` flag   — dream-co rescue. → View 2.
      3. `passed_prefilter == False` — prefilter killed it. → View 3.
      4. Otherwise — the prefilter passed AND Haiku returned a score. → View 1.

    The precedence is deliberate: a dream-co rescue SHOULD show up in
    the review bucket even if technically it "passed" the prefilter,
    because the whole point of the flag is that the auto verdict isn't
    trustworthy and the original author wants to eyeball it.
    """
    tier = str(row.get("tier") or "").strip.lower
    if tier == "needs_review":
        return VIEW_REVIEW
    # tier "DISQUALIFIED" is the new prefilter-fail label
    # (was "skip"). Catch both spellings during the rollout window.
    if tier in ("disqualified",):
        return VIEW_REJECTS
    if _to_bool(row.get("watchlist_dream")):
        return VIEW_REVIEW
    # passed_prefilter may be missing on OLD rows written before ;
    # in that case fall back on algo_score == 100 as an approximation.
    if "passed_prefilter" in row:
        passed = _to_bool(row.get("passed_prefilter"))
    else:
        passed = (_to_int(row.get("algo_score")) or 0) >= 100
    if not passed:
        return VIEW_REJECTS
    return VIEW_APPLY


# ---------------------------------------------------------------------------
# DynamoDB scan
# ---------------------------------------------------------------------------
def scan_jobs(
    table_name: str,
    status: str,
    min_semantic: int,
    max_rows: int,
) -> list[dict]:
    """
    Paginated scan of the Jobs table filtered to the requested status.
    We use a ProjectionExpression to keep the response payload small —
    the description field can be multi-KB and we don't need it here.

    additions to the projection: passed_prefilter,
    prefilter_reason, hard_disqualifiers, soft_warnings, positive_signals,
    role_family_match, industry_match, geography_match, level_match,
    watchlist_dream, life_fit_concerns, tier, track, is_dream_company,
    is_hrc100, is_crunch_co, industry.
    """
    dyn = boto3.resource("dynamodb")
    tbl = dyn.Table(table_name)

    # Reserved-word aliases: `status`, `url`, `source`, `location`, `name`
    # are DDB reserved words. Everything else can go through directly.
    projection = (
        # Core identity + display
        "job_id, title, company, #loc, #url, #src, posted_at, "
        "salary_min, salary_max, salary_currency, "
        # Scoring
        "score, algo_score, semantic_score, semantic_rationale, "
        "tier, track, "
        # prefilter outputs
        "passed_prefilter, prefilter_reason, "
        "hard_disqualifiers, soft_warnings, positive_signals, "
        # semantic structured fields
        "role_family_match, industry_match, geography_match, level_match, "
        "watchlist_dream, life_fit_concerns, "
        # Taxonomy + QoL flags (prefilter inputs)
        "work_mode, engagement_type, role_types, industries, "
        "industry, company_group, "
        "is_dream_company, is_hrc100, is_crunch_co, location_flag, "
        # Legacy / misc
        "gates_triggered, qol_score, #st"
    )
    ex_names = {
        "#loc": "location",
        "#url": "url",
        "#src": "source",
        "#st":  "status",
    }

    filter_expr = Attr("status").eq(status)

    rows: list[dict] = 
    last_key: dict | None = None
    scanned = 0
    while True:
        kwargs: dict[str, Any] = dict(
            ProjectionExpression=projection,
            ExpressionAttributeNames=ex_names,
            FilterExpression=filter_expr,
        )
        if last_key is not None:
            kwargs["ExclusiveStartKey"] = last_key
        resp = tbl.scan(**kwargs)
        page = resp.get("Items") or 
        scanned += resp.get("ScannedCount", 0)
        for it in page:
            ss = _to_int(it.get("semantic_score"))
            if min_semantic > 0 and (ss is None or ss < min_semantic):
                continue
            rows.append(it)
            if max_rows and len(rows) >= max_rows:
                return rows
        last_key = resp.get("LastEvaluatedKey")
        # Progress ping every page so a big scan doesn't look frozen.
        sys.stderr.write(
            f"  scanned={scanned:>6}  kept={len(rows):>5}"
            f"{' ... more' if last_key else ' (done)'}\n"
        )
        if not last_key:
            break
    return rows


# ---------------------------------------------------------------------------
# Grouping + sort (3-view)
# ---------------------------------------------------------------------------
def group_into_views(rows: list[dict]) -> dict[str, dict[str, list[dict]]]:
    """
    Two-level bucketing: view → sub-group → rows. Returns a dict keyed by
    VIEW_* label; each value is a dict keyed by sub-group label; each
    leaf is a list of rows already sorted most-interesting-first.

    Sub-group rules:
      View 1 (apply) — by APPLY_BANDS (semantic score).
      View 2 (review) — split "needs_review (Haiku errored)" vs
                        "watchlist_dream (dream-co rescue)" so the original author can
                        see at-a-glance which rows are errors-to-retry
                        vs judgement-calls.
      View 3 (rejects) — by prefilter_reason (falls back to
                         "unspecified" when the field is absent).

    Sort inside a sub-group:
      View 1 — semantic desc, then company asc.
      View 2 — semantic desc (None sinks), then company asc.
      View 3 — company asc (score is meaningless for rejects).
    """
    views: dict[str, dict[str, list[dict]]] = {
        VIEW_APPLY:   defaultdict(list),
        VIEW_REVIEW:  defaultdict(list),
        VIEW_REJECTS: defaultdict(list),
    }

    for r in rows:
        v = assign_view(r)

        if v == VIEW_APPLY:
            sub = _apply_band_for(_to_int(r.get("semantic_score")))
            views[VIEW_APPLY][sub].append(r)

        elif v == VIEW_REVIEW:
            tier = str(r.get("tier") or "").strip.lower
            if tier == "needs_review":
                sub = "Needs review (Haiku unavailable / errored)"
            elif _to_bool(r.get("watchlist_dream")):
                sub = "Watchlist - dream co rescue (low Haiku score)"
            else:
                # Shouldn't happen given assign_view, but safe default.
                sub = "Other"
            views[VIEW_REVIEW][sub].append(r)

        else:  # VIEW_REJECTS
            # BUG 3 — group rejects by the TOP-LEVEL fail
            # reason, not the full "wrong_function:software engineer"
            # string. The full keyword is interesting per-row but
            # exploding 500+ unique sub-groups in the export markdown
            # made the file unscannable. Strip the ":<kw>" tail.
            #
            # Top-level reasons we expect:
            #   wrong_function, sub_vp_seniority, unpaid_engagement,
            #   priority_disqualifier, diluted_exception,
            #   unknown_prefilter_fail
            #
            # If the row has a tier=="DISQUALIFIED" but no
            # prefilter_reason recorded (legacy rows from before the
            # fallback), bucket them under
            # "unknown_prefilter_fail" rather than the prior
            # "unspecified" so the original author can search the export for that
            # exact tag.
            raw_reason = str(r.get("prefilter_reason") or "").strip
            if not raw_reason:
                top_level = "unknown_prefilter_fail"
            elif ":" in raw_reason:
                top_level = raw_reason.split(":", 1)[0].strip or "unknown_prefilter_fail"
            else:
                top_level = raw_reason
            views[VIEW_REJECTS][top_level].append(r)

    # Sort each sub-group.
    for sub_map in views[VIEW_APPLY].values:
        sub_map.sort(
            key=lambda r: (
                -(_to_int(r.get("semantic_score")) or -1),
                str(r.get("company") or "").lower,
            )
        )
    for sub_map in views[VIEW_REVIEW].values:
        sub_map.sort(
            key=lambda r: (
                -(_to_int(r.get("semantic_score")) or -1),
                str(r.get("company") or "").lower,
            )
        )
    for sub_map in views[VIEW_REJECTS].values:
        sub_map.sort(
            key=lambda r: (
                str(r.get("company") or "").lower,
                str(r.get("title") or "").lower,
            )
        )

    # Materialise defaultdicts into plain dicts for predictable iteration.
    return {k: dict(v) for k, v in views.items}


# ---------------------------------------------------------------------------
# Markdown renderer
# ---------------------------------------------------------------------------
def render_markdown(
    grouped: dict[str, dict[str, list[dict]]],
    total: int,
    status: str,
    generated_at: datetime,
    views_to_emit: list[str],
) -> str:
    """Build the full markdown string for the export document."""
    lines: list[str] = 

    # ---- Header + preamble ------------------------------------------------
    lines.append("# Semantic-score audit export")
    lines.append("")
    lines.append(
        f"_Generated {generated_at.isoformat} · status=`{status}` · "
        f"{total} rows_"
    )
    lines.append("")
    lines.append(
        "scoring is a **binary prefilter → Haiku semantic** "
        "pipeline. If the prefilter passes, Haiku's score IS the final "
        "score (no blend). If it fails, final=0. If Haiku errored, the "
        "row is flagged `needs_review`."
    )
    lines.append("")
    lines.append("This document is organised into 3 views:")
    lines.append("")
    lines.append(
        "1. **Apply queue** prefilter passed + Haiku scored. Sub-grouped "
        "by semantic band (A: 80-100, B: 65-79, C: 50-64, D: <50)."
    )
    lines.append(
        "2. **Needs review / dream-co** Haiku errored OR watchlist_dream "
        "flag fired. Manual judgement required."
    )
    lines.append(
        "3. **Prefilter rejects** the binary gate killed the row. "
        "Sub-grouped by prefilter_reason so you can tell at a glance "
        "whether the gate is over-rejecting."
    )
    lines.append("")

    # ---- Distribution summary ---------------------------------------------
    lines.append("## Distribution")
    lines.append("")
    lines.append("| View | Sub-group | Count |")
    lines.append("|------|-----------|------:|")
    for view in VIEW_ORDER:
        sub_map = grouped.get(view, {})
        if not sub_map:
            lines.append(f"| {view} | _(empty)_ | 0 |")
            continue
        # Sort sub-groups by count desc for readability.
        sorted_subs = sorted(
            sub_map.items, key=lambda kv: (-len(kv[1]), kv[0])
        )
        for sub, bucket in sorted_subs:
            lines.append(f"| {view} | {sub} | {len(bucket)} |")
    lines.append("")

    # Per-view totals (quick eyeball).
    per_view_totals = {
        view: sum(len(b) for b in grouped.get(view, {}).values)
        for view in VIEW_ORDER
    }
    totals_line = "  ·  ".join(
        f"{view}: **{per_view_totals[view]}**" for view in VIEW_ORDER
    )
    lines.append(totals_line)
    lines.append("")

    # ---- Table of contents ------------------------------------------------
    lines.append("## Contents")
    lines.append("")
    for view in VIEW_ORDER:
        if view not in views_to_emit:
            continue
        n = per_view_totals.get(view, 0)
        if n == 0:
            lines.append(f"- {view} — 0 _(skipped)_")
            continue
        lines.append(f"- [{view}](#{_anchor(view)}) — {n}")
        sub_map = grouped.get(view, {})
        sorted_subs = sorted(
            sub_map.items, key=lambda kv: (-len(kv[1]), kv[0])
        )
        for sub, bucket in sorted_subs:
            sub_anchor = _anchor(f"{view} {sub}")
            lines.append(f"  - [{sub}](#{sub_anchor}) — {len(bucket)}")
    lines.append("")

    # ---- The three views --------------------------------------------------
    for view in VIEW_ORDER:
        if view not in views_to_emit:
            continue
        sub_map = grouped.get(view, {})
        total_in_view = sum(len(b) for b in sub_map.values)
        lines.append(f"## {view}")
        lines.append("")
        lines.append(_view_preamble(view, total_in_view))
        lines.append("")

        if total_in_view == 0:
            lines.append("_No rows in this view._")
            lines.append("")
            continue

        # Sub-groups in count-desc order (so the most populous band /
        # reject reason shows up first).
        sorted_subs = sorted(
            sub_map.items, key=lambda kv: (-len(kv[1]), kv[0])
        )
        for sub, bucket in sorted_subs:
            # Heading uses view + sub so the anchor is globally unique
            # across views (A. Strong match appears only under View 1
            # but belt-and-braces).
            lines.append(f"### {view} — {sub}")
            lines.append("")
            lines.append(f"_{len(bucket)} roles._")
            lines.append("")
            for r in bucket:
                lines.extend(_render_one_entry(r, view))
                lines.append("")
                lines.append("---")
                lines.append("")

    return "\n".join(lines)


def _view_preamble(view: str, n: int) -> str:
    """Short paragraph at the top of each view explaining the intent."""
    if view == VIEW_APPLY:
        return (
            f"_{n} rows in the actionable feed._ Prefilter passed AND "
            "Haiku returned a score. Skim A → D; the A / B bands are "
            "your daily review queue."
        )
    if view == VIEW_REVIEW:
        return (
            f"_{n} rows where the auto verdict is not trusted._ Two "
            "sub-buckets: **needs_review** (Haiku errored — retry or "
            "rate manually) and **watchlist_dream** (prefilter passed "
            "but Haiku scored low at a dream-list company — eyeball it)."
        )
    if view == VIEW_REJECTS:
        return (
            f"_{n} rows killed by the binary prefilter._ Sub-grouped by "
            "`prefilter_reason` so you can tell at a glance whether a "
            "reason is over-rejecting (e.g. if `wrong_function` is "
            "catching VP-level Platform titles, extend "
            "`LEADERSHIP_EXCEPTIONS` in `candidate_profile.py`)."
        )
    return ""


def _render_one_entry(r: dict, view: str) -> list[str]:
    """Render a single job row. Returns a list of markdown lines."""
    title    = _md_escape_inline(r.get("title") or "(untitled)")
    company  = _md_escape_inline(r.get("company") or "")
    location = _md_escape_inline(r.get("location") or "")
    url      = str(r.get("url") or "").strip
    source   = _md_escape_inline(r.get("source") or "")
    posted   = _short_posted(r.get("posted_at"))
    salary   = _fmt_salary(r.get("salary_min"), r.get("salary_max"),
                           r.get("salary_currency"))

    sem      = _to_int(r.get("semantic_score"))
    final    = _to_int(r.get("score"))
    tier     = str(r.get("tier") or "").strip
    track    = str(r.get("track") or "").strip

    passed = _to_bool(r.get("passed_prefilter")) if "passed_prefilter" in r \
        else ((_to_int(r.get("algo_score")) or 0) >= 100)
    reason = str(r.get("prefilter_reason") or "").strip

    # Build heading with title + company
    head_bits = [f"### {title}"]
    if company:
        head_bits.append(f"— _{company}_")
    lines = [" ".join(head_bits)]

    # Meta row (compact)
    meta_bits: list[str] = 
    if location:
        meta_bits.append(f"location: {location}")
    if salary:
        meta_bits.append(f"salary: {salary}")
    wm = r.get("work_mode")
    if wm and str(wm).strip.lower != "unclear":
        meta_bits.append(f"work_mode: {wm}")
    et = r.get("engagement_type")
    if et and str(et).strip.lower != "unclear":
        meta_bits.append(f"engagement: {et}")
    if source:
        meta_bits.append(f"src: `{source}`")
    if posted and posted != "?":
        meta_bits.append(f"posted: {posted}")
    if meta_bits:
        lines.append(" · ".join(meta_bits))
        lines.append("")

    # --- Prefilter verdict row ---
    if passed:
        pre_line = "**Prefilter:** ✅ passed"
    else:
        pre_line = f"**Prefilter:** ❌ failed — reason=`{reason or 'unspecified'}`"
    lines.append(pre_line)

    # Positive signals / soft warnings / hard gates from the prefilter.
    pos = _as_list(r.get("positive_signals"))
    soft = _as_list(r.get("soft_warnings"))
    hard = _as_list(r.get("hard_disqualifiers"))
    if pos:
        lines.append(f"**Positive signals:** {', '.join(pos)}")
    if soft:
        lines.append(f"**Soft warnings:** {', '.join(soft)}")
    if hard:
        lines.append(f"**Hard disqualifiers:** `{', '.join(hard)}`")

    # --- Semantic / final score row ---
    def _s(v: int | None) -> str:
        return "—" if v is None else str(v)
    score_bits = [
        f"semantic={_s(sem)}",
        f"final={_s(final)}",
    ]
    if tier:
        score_bits.append(f"tier=`{tier}`")
    if track:
        score_bits.append(f"track=`{track}`")
    lines.append("**Scores:** " + " · ".join(score_bits))

    # Structured semantic fields.
    struct_bits: list[str] = 
    for k, pretty in (
        ("role_family_match", "role_family"),
        ("industry_match",    "industry"),
        ("geography_match",   "geo"),
        ("level_match",       "level"),
    ):
        val = r.get(k)
        if val is not None and str(val).strip and str(val).strip.lower != "unclear":
            struct_bits.append(f"{pretty}=`{val}`")
    wd = _to_bool(r.get("watchlist_dream"))
    if wd:
        struct_bits.append("watchlist_dream=`true`")
    if struct_bits:
        lines.append("**Semantic fields:** " + " · ".join(struct_bits))

    concerns = _as_list(r.get("life_fit_concerns"))
    if concerns:
        lines.append(f"**Life-fit concerns:** {', '.join(concerns)}")

    # Taxonomy hints (less critical in but still useful).
    role_types = _as_list(r.get("role_types"))
    if role_types:
        lines.append(f"**Role types:** {', '.join(role_types)}")
    industries = _as_list(r.get("industries"))
    if industries:
        lines.append(f"**Industries:** {', '.join(industries)}")

    # Legacy gates_triggered — may exist on old rows written pre-.
    gates = _as_list(r.get("gates_triggered"))
    if gates:
        lines.append(f"**gates_triggered (legacy):** `{', '.join(gates)}`")

    # The rationale is the MAIN attraction of the document — full text,
    # as a blockquote for easy eye-scanning. For View 3 (rejects) we
    # typically have no rationale, so skip.
    rationale = str(r.get("semantic_rationale") or "").strip
    if rationale:
        lines.append("")
        lines.append("**Haiku rationale:**")
        lines.append("")
        lines.append(_md_block_rationale(rationale))

    # URL at the very bottom so the entry header stays clean.
    if url:
        lines.append("")
        lines.append(f"[Open posting →]({url})")
    return lines


# ---------------------------------------------------------------------------
# CSV renderer (companion file; same row set, machine-parseable)
# ---------------------------------------------------------------------------
CSV_COLUMNS = [
    "view", "sub_group",
    "semantic_score", "final_score", "algo_score",
    "tier", "track",
    "passed_prefilter", "prefilter_reason",
    "title", "company", "location",
    "salary_min", "salary_max", "salary_currency", "salary_pretty",
    "work_mode", "engagement_type",
    "source", "posted_at",
    "role_family_match", "industry_match",
    "geography_match",   "level_match",
    "watchlist_dream", "life_fit_concerns",
    "hard_disqualifiers", "soft_warnings", "positive_signals",
    "role_types", "industries", "industry", "company_group",
    "is_dream_company", "is_hrc100", "is_crunch_co", "location_flag",
    "gates_triggered",
    "url", "job_id",
    "semantic_rationale",
]


def _csv_sub_group(row: dict, view: str) -> str:
    """Recompute the sub-group label for a row (for CSV output)."""
    if view == VIEW_APPLY:
        return _apply_band_for(_to_int(row.get("semantic_score")))
    if view == VIEW_REVIEW:
        tier = str(row.get("tier") or "").strip.lower
        if tier == "needs_review":
            return "Needs review (Haiku unavailable / errored)"
        if _to_bool(row.get("watchlist_dream")):
            return "Watchlist - dream co rescue (low Haiku score)"
        return "Other"
    # VIEW_REJECTS
    return str(row.get("prefilter_reason") or "unspecified").strip or "unspecified"


def write_csv(rows: Iterable[dict], path: Path) -> int:
    """
    Emit a flat CSV of every row. The `view` and `sub_group` columns
    reflect the same grouping used in the markdown so the two outputs
    are trivially cross-referenceable.
    """
    n = 0
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh, quoting=csv.QUOTE_MINIMAL)
        w.writerow(CSV_COLUMNS)
        for r in rows:
            view = assign_view(r)
            sub = _csv_sub_group(r, view)
            sem = _to_int(r.get("semantic_score"))
            final = _to_int(r.get("score"))
            algo = _to_int(r.get("algo_score"))
            passed = _to_bool(r.get("passed_prefilter")) if "passed_prefilter" in r \
                else ((algo or 0) >= 100)

            w.writerow([
                view,
                sub,
                sem if sem is not None else "",
                final if final is not None else "",
                algo if algo is not None else "",
                str(r.get("tier") or ""),
                str(r.get("track") or ""),
                "true" if passed else "false",
                str(r.get("prefilter_reason") or ""),
                str(r.get("title") or ""),
                str(r.get("company") or ""),
                str(r.get("location") or ""),
                _to_int(r.get("salary_min")) or "",
                _to_int(r.get("salary_max")) or "",
                str(r.get("salary_currency") or ""),
                _fmt_salary(r.get("salary_min"), r.get("salary_max"),
                            r.get("salary_currency")),
                str(r.get("work_mode") or ""),
                str(r.get("engagement_type") or ""),
                str(r.get("source") or ""),
                str(r.get("posted_at") or ""),
                str(r.get("role_family_match") or ""),
                str(r.get("industry_match") or ""),
                str(r.get("geography_match") or ""),
                str(r.get("level_match") or ""),
                "true" if _to_bool(r.get("watchlist_dream")) else "false",
                "; ".join(_as_list(r.get("life_fit_concerns"))),
                "; ".join(_as_list(r.get("hard_disqualifiers"))),
                "; ".join(_as_list(r.get("soft_warnings"))),
                "; ".join(_as_list(r.get("positive_signals"))),
                "; ".join(_as_list(r.get("role_types"))),
                "; ".join(_as_list(r.get("industries"))),
                str(r.get("industry") or ""),
                str(r.get("company_group") or ""),
                "true" if _to_bool(r.get("is_dream_company")) else "false",
                "true" if _to_bool(r.get("is_hrc100")) else "false",
                "true" if _to_bool(r.get("is_crunch_co")) else "false",
                str(r.get("location_flag") or ""),
                "; ".join(_as_list(r.get("gates_triggered"))),
                str(r.get("url") or ""),
                str(r.get("job_id") or ""),
                # Replace newlines in the rationale so Excel keeps one
                # row per record; blockquote newlines only matter for MD.
                (str(r.get("semantic_rationale") or "")
                     .replace("\r", " ").replace("\n", " ")),
            ])
            n += 1
    return n


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _parse_views_arg(raw: str) -> list[str]:
    """
    Parse a --views flag like '1,2' or '1,3' into the canonical VIEW_*
    labels. Unknown tokens raise ValueError (argparse will surface it).
    """
    lookup = {"1": VIEW_APPLY, "2": VIEW_REVIEW, "3": VIEW_REJECTS}
    tokens = [t.strip for t in (raw or "").split(",") if t.strip]
    if not tokens:
        return list(VIEW_ORDER)
    out: list[str] = 
    for tok in tokens:
        if tok not in lookup:
            raise ValueError(f"unknown view token {tok!r} (expected 1/2/3)")
        out.append(lookup[tok])
    # Preserve canonical ordering even if user passed 3,1,2.
    return [v for v in VIEW_ORDER if v in out]


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--status", default="active",
                   help="status to export (default: active)")
    p.add_argument("--min-semantic", type=int, default=0,
                   help="drop rows with semantic_score below this "
                        "(default 0 = keep all)")
    p.add_argument("--max-rows", type=int, default=0,
                   help="cap rows for a quick dry-run (0 = no cap)")
    p.add_argument("--out", default="docs/semantic_review.md",
                   help="markdown output path")
    p.add_argument("--csv", default="docs/semantic_review.csv",
                   help="csv output path")
    p.add_argument("--table", default=os.environ.get("JOBS_TABLE", ""),
                   help="Jobs table name (defaults to $JOBS_TABLE)")
    p.add_argument("--views", default="1,2,3",
                   help="comma-separated subset of views to emit "
                        "(default: 1,2,3 = all)")
    args = p.parse_args(argv)

    if not args.table:
        sys.stderr.write(
            "error: --table not set and $JOBS_TABLE not in environment\n"
        )
        return 2

    try:
        views_to_emit = _parse_views_arg(args.views)
    except ValueError as exc:
        sys.stderr.write(f"error: {exc}\n")
        return 2

    sys.stderr.write(
        f"Scanning `{args.table}` (status={args.status}, "
        f"min_semantic={args.min_semantic}, "
        f"max_rows={args.max_rows or 'unlimited'}) ...\n"
    )
    rows = scan_jobs(
        table_name=args.table,
        status=args.status,
        min_semantic=args.min_semantic,
        max_rows=args.max_rows,
    )
    sys.stderr.write(f"Loaded {len(rows)} rows; rendering outputs...\n")

    grouped = group_into_views(rows)

    # Quick per-view / per-sub summary to stderr so the original author sees the shape
    # of the export at a glance.
    for view in VIEW_ORDER:
        sub_map = grouped.get(view, {})
        n_view = sum(len(b) for b in sub_map.values)
        sys.stderr.write(f"  {view}: {n_view} rows\n")
        for sub, bucket in sorted(sub_map.items,
                                   key=lambda kv: (-len(kv[1]), kv[0])):
            sys.stderr.write(f"    - {sub}: {len(bucket)}\n")

    md_path = Path(args.out)
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md = render_markdown(
        grouped=grouped,
        total=len(rows),
        status=args.status,
        generated_at=datetime.now(timezone.utc),
        views_to_emit=views_to_emit,
    )
    md_path.write_text(md, encoding="utf-8")
    sys.stderr.write(
        f"Wrote markdown: {md_path} ({md_path.stat.st_size:,} bytes)\n"
    )

    csv_path = Path(args.csv)
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    n = write_csv(rows, csv_path)
    sys.stderr.write(
        f"Wrote CSV: {csv_path} ({n} rows, "
        f"{csv_path.stat.st_size:,} bytes)\n"
    )

    return 0


if __name__ == "__main__":
    sys.exit(main)
