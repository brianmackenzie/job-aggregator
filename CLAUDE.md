# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when
working with code in this repository.

## Non-negotiable ground rules

- **Architecture is FINAL.** The stack below was chosen deliberately.
  Propose alternatives only if asked.
- **Pause for approval after each phase of a multi-phase task.** Show
  the `template.yaml` diff and the list of files to be created, get
  explicit approval, then write code. Never proceed to phase N+1
  before acceptance criteria for phase N pass.
- **Comment Python code liberally.** The primary reader of this code
  may not write much code themselves; favor clarity over cleverness.
- **Never hard-fail a scrape run.** Wrap each source + each item in
  try/except; log failures to the `ScrapeRuns` DynamoDB table and
  continue. One flaky HTML source must not poison the daily run.
- **Save every non-trivial command to `RUNBOOK.md`.** That file is
  the operator's manual.

## Stack (non-negotiable)

- Python 3.12, Lambda on arm64 (Graviton2)
- AWS SAM (one `template.yaml`); deploy with `sam build && sam deploy`
- API Gateway HTTP API -> Lambdas -> DynamoDB on-demand
- Vanilla HTML + CSS + JS for the frontend. No React, no Vue, no build
  step. Target iOS Safari (use CSS custom properties, CSS Grid,
  `safe-area-inset-*`).
- Private S3 + CloudFront + Origin Access Control
- HTTP basic auth enforced by a CloudFront Function at the edge
- Secrets in SSM Parameter Store under `/jobs-aggregator/*`
- EventBridge Scheduler for scraper cadence
- LinkedIn scraping via Apify REST API

## Repo layout (authoritative)

```
jobs-aggregator/
|-- template.yaml              SAM infra
|-- requirements.txt           shared Python deps
|-- RUNBOOK.md                 every non-trivial command
|-- README.md
|-- config/                    tunable values (SINGLE SOURCE OF TRUTH)
|   |                          Shipped to Lambda as /opt/*.yaml via
|   |                          AWS::Serverless::LayerVersion.
|   |-- candidate_profile.yaml Haiku system prompt + calibration text
|   |-- companies.yaml         tier-1 target companies + ATS slugs
|   |-- scoring.yaml           all scoring weights/keywords/modifiers
|   |-- sources.yaml           which scrapers run, how often
|   |-- taxonomy.yaml          industry / role_type / company_group map
|-- src/
|   |-- common/                db, models, normalize, logging, secrets
|   |-- scoring/               engine, keywords, gates, modifiers (+ tests)
|   |-- scrapers/              base ABC + registry + per-source plugins
|   \-- lambdas/               one file per Lambda handler
|-- frontend/                  static site
|-- infra/
|   \-- cloudfront_basic_auth.js  reference copy of the edge auth function
\-- scripts/                   seeders, rescorers, probes
```

## DynamoDB tables (all on-demand)

- **Jobs** - PK `job_id` = `"{source}:{native_id}"`. GSIs include
  `ScoreIndex`, `CompanyIndex`, `SourceDateIndex`, `TrackIndex`.
- **Companies** - PK `company_name_normalized`. GSI `TierIndex`.
- **ScrapeRuns** - PK `source_name`, SK `run_timestamp`. TTL on
  `expires_at`.
- **UserPrefs** - PK `user_id`, SK `config_key`.

Table names are exposed to every Lambda via env vars
(`JOBS_TABLE`, `COMPANIES_TABLE`, `SCRAPE_RUNS_TABLE`,
`USER_PREFS_TABLE`). Never hardcode.

## Scraper plugin contract

Every source is a subclass of `BaseScraper` in
`src/scrapers/base.py`, registered via `@register("source_name")` in
`src/scrapers/registry.py`. Subclass implements `fetch()` (raw
payloads) and `parse(payload) -> Optional[RawJob]`. The base class's
`scrape_run()` handles retry, rate limiting, per-item try/except,
dedup via `job_id`, writing a `ScrapeRuns` row, and archiving raw
payloads to S3.

## Scoring contract

`src/scoring/engine.py::score(job, prefs)` returns:

```python
{
  "score": int 0-100,
  "tier": str,
  "track": str,
  "breakdown": dict,
  "gates_triggered": list,
  "modifiers_applied": list,
}
```

A binary algo prefilter runs first; anything that survives is scored
by Claude Haiku as the sole ranker. If a hard gate triggers, final
score = 0. All weights/keywords/thresholds live in
`config/scoring.yaml`.

## API surface (all behind basic auth at CloudFront)

```
GET  /api/health                   scrape run status
GET  /api/stats                    counts by tier/track
GET  /api/jobs?<filters>           paginated list
GET  /api/jobs/{id}                full job incl. score_breakdown
POST /api/jobs/{id}/action         {action: save|skip|applied, notes?}
POST /api/jobs/bulk_action         {action: save|skip, job_ids: [...]}
GET  /api/prefs                    user prefs
PUT  /api/prefs                    update prefs
GET  /api/taxonomy                 filter UI facet labels
POST /api/scrape/{source}          manual scrape trigger
```

## Common commands

```bash
sam build && sam deploy
aws s3 sync frontend/ s3://$SITE_BUCKET/ --delete
sam logs -n ApiHealthFn --stack-name jobs-aggregator --tail
```

Everything else is in `RUNBOOK.md`.
