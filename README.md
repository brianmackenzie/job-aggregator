# Jobs Aggregator

Scrapes 30+ job boards on a schedule, runs each posting through a
Claude Haiku scoring pipeline tailored to your personal preferences,
and serves the ranked results through a mobile-friendly private
dashboard hosted on your own AWS account.

This is a public fork of a personal tool — the infrastructure and
algorithm are general-purpose. Every personal preference (target
companies, geography, scoring weights, search keywords, what Haiku is
told about the candidate) lives in five YAML files under `config/`.
Fork, edit those files, deploy.

<img width="642" height="1108" alt="image3" src="https://github.com/user-attachments/assets/fb71fc3b-cf0a-4bd2-9fd5-68c4bb9c90a7" />


## Quick start

1. Read [`docs/FORKING.md`](./docs/FORKING.md) — the full walkthrough
   for taking this codebase and making it yours.
2. Read [`docs/USER_CONFIG.md`](./docs/USER_CONFIG.md) — file-by-file
   guide to the 5 YAML files under `config/`.
3. Read [`RUNBOOK.md`](./RUNBOOK.md) — every non-trivial command you
   will run (deploy, upload frontend, invalidate CloudFront, trigger a
   scrape, rescore all jobs, tail Lambda logs).

## Stack

- Python 3.12 Lambdas on arm64 (Graviton2)
- AWS SAM (`template.yaml`) — single-template IaC
- API Gateway HTTP API → Lambdas → DynamoDB on-demand
- Vanilla HTML + CSS + JS (no build step) → S3 → CloudFront with
  Origin Access Control
- HTTP basic auth enforced by a CloudFront Function at the edge
- Secrets in SSM Parameter Store under `/jobs-aggregator/*`
- EventBridge Scheduler for scraper cadence
- All tunable YAMLs shipped to Lambda via an `AWS::Serverless::LayerVersion`
  (`ConfigLayer` in `template.yaml`) — edit `config/*.yaml`, redeploy,
  done. No code changes required to tune scoring.

## What's in `config/`

| File                    | What it controls                                   |
|-------------------------|----------------------------------------------------|
| `candidate_profile.yaml` | The system prompt + calibration anchors sent to Claude Haiku. Describes the candidate and what a good fit looks like. |
| `scoring.yaml`          | Algorithmic pre-filter weights, keyword vocabularies, hard-gate rules, modifier deltas. |
| `companies.yaml`        | List of target companies + ATS slugs for direct scraping. |
| `sources.yaml`          | Which scrapers run, how often, search-URL content for LinkedIn/Apify. |
| `taxonomy.yaml`         | Industry / role-type / company-group classification + QoL weights used by the filter UI. |

## Cost

Steady-state on an AWS free-tier-adjacent account: roughly **$3-10 per
month**, dominated by DynamoDB on-demand reads + Lambda invocations +
CloudFront egress. Haiku costs are minor (prompt caching keeps per-job
scoring under $0.01). The Apify LinkedIn scraper is metered
separately at ~$0.005 per result — tune `count_per_search` and the
number of `searches:` entries in `sources.yaml` to control spend.

## License

MIT. See [`LICENSE`](./LICENSE).
