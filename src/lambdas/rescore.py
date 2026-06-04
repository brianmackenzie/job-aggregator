"""rescore — batch re-score every job in the Jobs table.

Invoke manually via:
    aws lambda invoke --function-name <RescoreFnName> --payload '{}' /dev/null
    (see RUNBOOK.md for the full PowerShell-friendly command)

Or run the helper script:
    python scripts/rescore_all.py

When to use:
  - After editing config/scoring.yaml (weights, keywords, modifiers).
  - After a schema change in the scoring engine.
  - To populate scores on jobs scraped before was deployed.

Design:
  - Scans Jobs table in pages (DynamoDB Scan with pagination).
  - Applies score() to each job using empty prefs (personalisation = ).
  - Writes back only the score-related fields via update_job_score().
  - Wraps each item in try/except so one bad row never aborts the run.
  - Logs a summary at the end for CloudWatch visibility.
"""
import os
import time
import traceback
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import boto3
from boto3.dynamodb.conditions import Attr

from common.logging import log
from common.normalize import score_posted_sk
from scoring.combined import score_combined


def _to_dynamo(obj):
    """Recursively convert Python floats to Decimal for DynamoDB compatibility.

    boto3's DynamoDB resource rejects plain Python floats (raises TypeError).
    We convert via str() to preserve the rounded representation and avoid
    the binary-float precision issues that Decimal(float) would introduce.
    """
    if isinstance(obj, float):
        return Decimal(str(obj))
    if isinstance(obj, dict):
        return {k: _to_dynamo(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_to_dynamo(i) for i in obj]
    return obj


# DynamoDB resource is created once per Lambda container (warm re-use).
_dynamodb = boto3.resource("dynamodb")


def _get_table():
    table_name = os.environ["JOBS_TABLE"]
    return _dynamodb.Table(table_name)


def handler(event, context):
    """Lambda entry point.

    Accepts an optional event payload:
      {"dry_run": true}            — score every job but do NOT write
                                     back to DynamoDB. Useful for testing
                                     a new scoring.yaml safely.
      {"status_filter": "active"}  — only rescore jobs with this status
                                     (default: all statuses).
      {"skip_semantic": true}      — algo-only pass. Cheap, ignores
                                     the Haiku layer entirely. Use after
                                     editing keyword/weight/modifier
                                     config when the candidate profile
                                     hasn't changed.
      {"force_semantic": true}     — re-call Haiku for every eligible
                                     job, ignoring the per-job cache.
                                     Use after editing
                                     candidate_profile.yaml.
      {"force_write": true}        — write EVERY row back even
                                     when its score + track are unchanged.
                                     The default now skips the DynamoDB
                                     write for no-op rows (big WCU saving
                                     on a stable rescore); set this after
                                     adding/renaming a persisted field so
                                     the new column backfills onto every
                                     row regardless of score movement.
                                     (force_semantic also forces writes.)
      {"segment": N, "total_segments": M}
                                   — DynamoDB parallel-scan shard. Each
                                     shard handles ~1/M of the table,
                                     letting you fire M Lambdas in
                                     parallel to beat the 15-min timeout
                                     on full force_semantic refreshes.
                                     A single force_semantic=true pass
                                     on ~4k jobs at ~1.5s/Haiku call
                                     takes ~100 min; M=8 brings each
                                     shard to ~12 min — fits the timeout.
                                     Both fields must be present (or
                                     both omitted) for shard mode.
                                     CAUTION: parallelism > ~2 will hit
                                     Anthropic's 450K-tpm input rate
                                     limit on the + prompt
                                     (~3,500 tok per call).
      {"min_age_hours": F}         — skip jobs whose semantic_scored_at
                                     is newer than F hours. Use after
                                     a partial force_semantic run to
                                     resume on only the un-refreshed
                                     tail without re-burning API on
                                     already-refreshed rows. F can be
                                     a float (e.g., 1.5).
    """
    dry_run        = bool((event or {}).get("dry_run", False))
    status_filter  = (event or {}).get("status_filter")  # None = all statuses
    skip_semantic  = bool((event or {}).get("skip_semantic", False))
    force_semantic = bool((event or {}).get("force_semantic", False))
    # cost lever. By default a rescore now SKIPS the DynamoDB write
    # for any row whose final score + track are unchanged (see the skip block
    # in the per-item loop). force_write=true rewrites every row regardless —
    # use it after adding/renaming a persisted field, where the score may be
    # identical but a NEW column needs backfilling onto every row.
    force_write    = bool((event or {}).get("force_write", False))
    # The skip is only safe/meaningful when we're actually writing AND not
    # forcing. force_semantic implies "we just paid Anthropic for fresh
    # verdicts + rationales on every row" — so we always write those, even
    # when the blended score happens to land on the same integer.
    do_skip_unchanged = (not dry_run) and (not force_write) and (not force_semantic)
    # Parallel-scan shard params. None means "single-Lambda full scan"
    # (the original behavior). With both set we add Segment/TotalSegments
    # to the DynamoDB scan kwargs so each Lambda only sees its 1/M slice.
    segment        = (event or {}).get("segment")
    total_segments = (event or {}).get("total_segments")
    # Resume-from-partial filter: a job whose semantic_scored_at is newer
    # than this many hours is skipped entirely (no algo recompute either —
    # we treat its existing record as authoritative). Useful when a prior
    # force_semantic run timed out partway through and we want to finish
    # ONLY the unprocessed remainder without paying API costs to refresh
    # rows already done in the same logical batch.
    min_age_hours  = (event or {}).get("min_age_hours")
    # Pre-compute the cutoff timestamp once. Anything strictly newer than
    # this counts as "too fresh, skip".
    cutoff_iso = None
    if min_age_hours is not None:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=float(min_age_hours))
        cutoff_iso = cutoff.strftime("%Y-%m-%dT%H:%M:%SZ")

    # Targeted re-Haiku: restrict the pass to specific sources and/or to
    # rows that actually carry a description. Used to refresh ONLY the rows
    # a description backfill just enriched, instead of paying for a global
    # force_semantic over the whole table. Both default off (no behavior
    # change for existing callers / the scheduled rescore).
    source_filter      = (event or {}).get("source_filter")   # str | list | None
    require_description = bool((event or {}).get("require_description", False))
    if isinstance(source_filter, str):
        source_filter = [source_filter]

    table = _get_table()
    started = time.monotonic()

    total              = 0
    updated            = 0
    semantic_calls     = 0
    skipped            = 0
    unchanged_skipped  = 0  # score+track unchanged -> no write
    api_failed_skipped = 0  # Haiku 429/timeout/parse-fail: row left untouched
    errors             = 0

    log.info(
        "rescore_start",
        dry_run=dry_run,
        status_filter=status_filter,
        skip_semantic=skip_semantic,
        force_semantic=force_semantic,
        force_write=force_write,
        segment=segment,
        total_segments=total_segments,
    )

    # Scan the entire Jobs table in pages. The scan is eventually consistent
    # which is fine — we're not time-sensitive here.
    scan_kwargs: dict = {
        "ProjectionExpression": (
            # `score` + `track` are read so the skip-unchanged
            # gate can compare the freshly-computed values against what's
            # already stored. Projection doesn't change Scan RCU (billed on
            # the full item read regardless), so this is free.
            "job_id, score, title, company, company_normalized, #s, track, "
            "description, #loc, remote, salary_min, salary_max, "
            "posted_at, company_tier, "
            # Semantic cache fields — read so score_combined can decide
            # whether to re-call Haiku or reuse the cached value.
            "semantic_score, semantic_rationale, semantic_scored_at, "
            "semantic_model, work_mode, "
            # cached structured fields — so a non-force rescore
            # can replay the cached semantic verdict without losing the
            # role_family/industry/geo/level/watchlist_dream/life_fit
            # payload that the UI and export scripts render.
            "role_family_match, industry_match, geography_match, "
            "level_match, watchlist_dream, life_fit_concerns"
        ),
        # 'status' and 'location' are reserved words in DynamoDB expressions.
        "ExpressionAttributeNames": {"#s": "status", "#loc": "location"},
    }
    _filt = None
    if status_filter:
        _filt = Attr("status").eq(status_filter)
    if source_filter:
        _sf = (Attr("source").is_in(source_filter)
               if len(source_filter) > 1 else Attr("source").eq(source_filter[0]))
        _filt = _sf if _filt is None else (_filt & _sf)
    if _filt is not None:
        scan_kwargs["FilterExpression"] = _filt

    # Parallel-scan: if both segment and total_segments are provided,
    # this Lambda only walks its slice of the table. Useful for getting
    # under the 15-min timeout on a full force_semantic refresh.
    if segment is not None and total_segments is not None:
        scan_kwargs["Segment"]       = int(segment)
        scan_kwargs["TotalSegments"] = int(total_segments)

    last_key = None
    while True:
        if last_key:
            scan_kwargs["ExclusiveStartKey"] = last_key

        response = table.scan(**scan_kwargs)
        items    = response.get("Items", [])

        for item in items:
            total += 1
            # Resume-from-partial: skip rows whose semantic_scored_at is
            # newer than the cutoff. This lets us fire a second pass
            # without paying API costs to refresh rows already done in
            # the same logical batch (e.g. after a force_semantic run
            # that timed out partway through).
            if cutoff_iso and (item.get("semantic_scored_at") or "") > cutoff_iso:
                skipped += 1
                continue
            # Targeted re-Haiku: skip rows with no real description (nothing
            # new for the semantic layer to read).
            if require_description and len((item.get("description") or "")) < 50:
                skipped += 1
                continue
            try:
                result = score_combined(
                    item,
                    prefs={},
                    skip_semantic=skip_semantic,
                    force_semantic=force_semantic,
                )

                # 429-fallback guard: if score_combined
                # tried Haiku and got nothing back (rate limit, timeout,
                # bad JSON), it returned an algo-only result. Writing
                # that to DynamoDB would overwrite a previously-good
                # blended score with a degraded value, leaving rows like
                # "score=22 (==algo_score) but semantic_score=87 still
                # cached" — exactly the inconsistency the recovery pass
                # had to clean up. Skip the update_item entirely; the
                # next rescore run will retry.
                if result.get("semantic_api_failed"):
                    api_failed_skipped += 1
                    continue

                new_score    = result["score"]
                new_track    = result["track"]

                # cost: skip the write entirely when the final
                # score AND track match what's already stored. DynamoDB bills
                # WCU on UpdateItem by item size (ceil(size/1KB)) no matter
                # how few attributes change, and the job row carries a
                # multi-KB description — so re-writing an unchanged row costs
                # the same as a real update. On a rescore that isn't calling
                # Haiku (algo tweak / cached semantic), most rows are no-ops;
                # skipping them turns a full-table rewrite into "write only
                # what moved". Gated by do_skip_unchanged (off for dry_run /
                # force_write / force_semantic).
                #
                # 2026-06-03: also refresh when the deterministic QoL score
                # drifted. QoL depends on work_mode / posted_at / salary /
                # description, which a re-scrape can change WITHOUT moving the
                # final score+track — in which case the old guard skipped the
                # write and left qol_score stale (observed ~4% of rows). We
                # only break the skip when qol actually changed, so true no-op
                # rows still cost zero WCU.
                if do_skip_unchanged:
                    old_score = item.get("score")
                    old_track = item.get("track") or ""
                    old_qol   = int(item.get("qol_score") or 0)
                    new_qol   = int(result.get("qol_score") or 0)
                    if (old_score is not None
                            and int(old_score) == int(new_score)
                            and old_track == (new_track or "")
                            and old_qol == new_qol):
                        unchanged_skipped += 1
                        continue

                new_posted   = score_posted_sk(new_score, item.get("posted_at", ""))
                gates        = result["gates_triggered"]
                mods         = result["modifiers_applied"]
                breakdown    = result["breakdown"]
                algo_int     = result["algo_score"]

                # Track whether this rescore actually called the LLM
                # (vs reused the cache or skipped entirely) — useful for
                # cost forecasting from CloudWatch logs.
                if result.get("semantic_score") is not None and not result.get("semantic_skipped"):
                    semantic_calls += 1

                if not dry_run:
                    # Write only the score fields — do not touch source data.
                    # _to_dynamo converts any Python floats in the breakdown
                    # dict to Decimal, which boto3's DynamoDB resource requires.
                    # renamed hard_gates_hit → gates_triggered
                    # so the export reader and scoring engine agree on a key
                    # name. One-shot migration (scripts/migrate_gates_column.py)
                    # copies the old column forward before this runs in prod.
                    #
                    # additions: also persist the full
                    # prefilter verdict (passed_prefilter, prefilter_reason,
                    # hard_disqualifiers, soft_warnings, positive_signals,
                    # QoL boolean flags) and the Haiku 6-field structured
                    # output (role_family_match, industry_match,
                    # geography_match, level_match, watchlist_dream,
                    # life_fit_concerns). Plus `tier` so the frontend
                    # doesn't re-derive it on every render. This brings
                    # the Lambda to parity with scripts/score_job.py --write
                    # and with what scripts/export_semantic_review.py reads.
                    update_expr_parts = [
                        "score = :s", "algo_score = :a",
                        "track = :t", "score_posted = :sp",
                        "gates_triggered = :g", "modifiers_applied = :m",
                        "score_breakdown = :b",
                        # prefilter verdict fields — always
                        # written because they're deterministic and the
                        # view router / export script keys on them.
                        "passed_prefilter = :pf",
                        "prefilter_reason = :pr",
                        "hard_disqualifiers = :hd",
                        "soft_warnings = :sw",
                        "positive_signals = :ps",
                        "is_dream_company = :idc",
                        "is_hrc100 = :ihr",
                        "is_crunch_co = :icr",
                        "tier = :ti",
                    ]
                    values: dict = {
                        ":s":   new_score,
                        ":a":   algo_int,
                        ":t":   new_track,
                        ":sp":  new_posted,
                        ":g":   gates,
                        ":m":   mods,
                        ":b":   _to_dynamo(breakdown),
                        # prefilter passthroughs.
                        ":pf":  bool(result.get("passed_prefilter") or False),
                        ":pr":  str(result.get("prefilter_reason") or "unknown"),
                        ":hd":  list(result.get("hard_disqualifiers") or []),
                        ":sw":  list(result.get("soft_warnings") or []),
                        ":ps":  list(result.get("positive_signals") or []),
                        ":idc": bool(result.get("is_dream_company") or False),
                        ":ihr": bool(result.get("is_hrc100") or False),
                        ":icr": bool(result.get("is_crunch_co") or False),
                        ":ti":  str(result.get("tier") or "skip"),
                    }

                    # Conditional string / int fields from the prefilter
                    # (DDB rejects None — guard each).
                    if result.get("location_flag"):
                        update_expr_parts.append("location_flag = :lf")
                        values[":lf"] = str(result["location_flag"])
                    if result.get("industry"):
                        update_expr_parts.append("industry = :ix")
                        values[":ix"] = str(result["industry"])
                    if result.get("industry_score") is not None:
                        update_expr_parts.append("industry_score = :ixs")
                        values[":ixs"] = int(result.get("industry_score") or 0)

                    # Only persist semantic fields when present — keeps
                    # algo-only rows lean and avoids overwriting a recent
                    # cached value with None when --skip-semantic is set.
                    if result.get("semantic_score") is not None:
                        update_expr_parts += [
                            "semantic_score = :ss",
                            "semantic_rationale = :sr",
                            "semantic_scored_at = :sa",
                            "semantic_model = :sm",
                            # Haiku structured passthroughs.
                            "role_family_match = :rf",
                            "industry_match = :im",
                            "geography_match = :gm",
                            "level_match = :lm",
                            "watchlist_dream = :wd",
                            "life_fit_concerns = :lfc",
                        ]
                        values[":ss"]  = result["semantic_score"]
                        values[":sr"]  = result.get("semantic_rationale") or ""
                        values[":sa"]  = result.get("semantic_scored_at") or ""
                        values[":sm"]  = result.get("semantic_model") or ""
                        values[":rf"]  = str(result.get("role_family_match") or "unclear")
                        values[":im"]  = str(result.get("industry_match")    or "unclear")
                        values[":gm"]  = str(result.get("geography_match")   or "unclear")
                        values[":lm"]  = str(result.get("level_match")       or "unclear")
                        values[":wd"]  = bool(result.get("watchlist_dream") or False)
                        values[":lfc"] = list(result.get("life_fit_concerns") or [])

                    # work_mode: . Persist independently from the
                    # semantic cache so the UI chip stays available even
                    # when semantic data ages out. Only write when we have
                    # a non-None value from this run (caching handles the
                    # already-stored case).
                    if result.get("work_mode"):
                        update_expr_parts.append("work_mode = :wm")
                        values[":wm"] = result["work_mode"]

                    # R1: redesigned-UI taxonomy + QoL fields.
                    # Always write — they're deterministic and computed
                    # every pass, so no risk of clobbering richer data.
                    # `industries` and `role_types` are stored as
                    # DynamoDB List<String>; `company_group` is a single
                    # string (or absent — we DON'T write null because
                    # DynamoDB rejects None values silently here).
                    update_expr_parts += [
                        "industries = :ind",
                        "role_types = :rt",
                        "qol_score = :qs",
                        "qol_breakdown = :qb",
                    ]
                    values[":ind"] = result.get("industries") or []
                    values[":rt"]  = result.get("role_types") or []
                    values[":qs"]  = int(result.get("qol_score") or 0)
                    values[":qb"]  = _to_dynamo(result.get("qol_breakdown") or {})
                    if result.get("company_group"):
                        update_expr_parts.append("company_group = :cg")
                        values[":cg"] = result["company_group"]

                    # engagement_type — categorical chip,
                    # populated by detect_engagement() in combined.py. Always
                    # write (deterministic; cheap) so the filter chip rail
                    # has data to render. Falls back to "unclear" for empty
                    # titles, which still parses safely client-side.
                    if result.get("engagement_type"):
                        update_expr_parts.append("engagement_type = :et")
                        values[":et"] = result["engagement_type"]

                    table.update_item(
                        Key={"job_id": item["job_id"]},
                        UpdateExpression="SET " + ", ".join(update_expr_parts),
                        ExpressionAttributeValues=values,
                    )
                updated += 1

            except Exception as exc:
                # One bad item must never abort the batch run.
                errors += 1
                log.warn(
                    "rescore_item_failed",
                    job_id=item.get("job_id", "unknown"),
                    error=str(exc),
                    traceback=traceback.format_exc(limit=3),
                )

        last_key = response.get("LastEvaluatedKey")
        if not last_key:
            break   # No more pages.

    duration_ms = int((time.monotonic() - started) * 1000)

    # clear the in-process browse-scan cache. NOTE: this only
    # clears THIS Lambda container's cache. RescoreFn ≠ ApiJobsFn so the
    # API's cache will still be up to 60s stale after a manual rescore;
    # for stronger freshness we'd need a cross-Lambda signal (DDB version
    # row, SNS, etc.). Acceptable for a personal dashboard — the original author waits
    # ~2 min for a rescore anyway.
    if not dry_run:
        try:
            from common import db as _db
            _db.invalidate_browse_cache()
        except Exception as _exc:
            log.warn("rescore_cache_invalidate_failed", error=str(_exc))

    log.info(
        "rescore_done",
        total=total,
        updated=updated,
        semantic_calls=semantic_calls,
        skipped=skipped,
        unchanged_skipped=unchanged_skipped,
        api_failed_skipped=api_failed_skipped,
        errors=errors,
        duration_ms=duration_ms,
        dry_run=dry_run,
        skip_semantic=skip_semantic,
        force_semantic=force_semantic,
        force_write=force_write,
        segment=segment,
        total_segments=total_segments,
    )

    return {
        "ok":                  True,
        "total":               total,
        "updated":             updated,
        "semantic_calls":      semantic_calls,
        "skipped":             skipped,
        "unchanged_skipped":   unchanged_skipped,
        "api_failed_skipped":  api_failed_skipped,
        "errors":              errors,
        "duration_ms":         duration_ms,
        "dry_run":             dry_run,
        "skip_semantic":       skip_semantic,
        "force_semantic":      force_semantic,
        "force_write":         force_write,
        "segment":             segment,
        "total_segments":      total_segments,
    }
