# Forking this project for your own job search

This tool was built by one person for their own search. The infrastructure is
generic, but every personal preference — who you are, what companies you want
to work at, what Haiku is told about you — lives in five YAML files you edit.
A new user can fork, rewrite those YAMLs, point it at their own AWS account,
and be running in an afternoon.

This doc is the end-to-end walkthrough. It links to the deeper per-file
reference [`USER_CONFIG.md`](./USER_CONFIG.md) for the full-fat detail.

> **Heads up.** The tool is opinionated. The `src/scoring/` Python code
> still encodes the original author's preferences as fallback constants
> (hardcoded company tier bumps, keyword lists, etc.). Rewriting the
> YAML configs below is enough for 90%+ of the scoring behavior, but if
> you want total parity with your own tastes you'll eventually want to
> look at `src/scoring/candidate_profile.py`, `algo_prefilter.py`,
> `gates.py`, and `modifiers.py`. The YAML-only path is the fast path;
> the Python-tweak path is the complete path.

---

## 1. Prerequisites

You need:

- **An AWS account** you're comfortable deploying to. Budget is usually
  a few dollars a month for a single-user dashboard; Apify LinkedIn
  scraping is the main variable cost (roughly $100/mo at daily cadence).
- **AWS CLI v2** configured with a profile that can create IAM roles,
  Lambda functions, API Gateway, DynamoDB tables, S3 buckets, and
  CloudFront distributions. (The deploy creates all of these.)
- **AWS SAM CLI** (`sam --version` ≥ 1.100).
- **Python 3.12** locally. (The Lambdas run 3.12 on arm64 Graviton2.
  SAM build works from 3.12 locally and transplants the runtime.)
- **An Anthropic API key** with access to Claude Haiku 4.5. This is
  the semantic ranker — without it the pipeline falls back to algo
  scoring only and results are markedly worse.
- *(Optional, recommended)* **An Apify token** if you want LinkedIn
  coverage. Everything else scrapes for free.

---

## 2. Clone and explore before touching anything

```powershell
git clone <your-fork-url> jobs
cd jobs
```

Read, in order:

1. `README.md` — 60-second project overview and stack.
2. `CLAUDE.md` — the architecture pact. Don't skip. Notes which
   decisions are frozen and which are yours.
3. `docs/USER_CONFIG.md` — the file-by-file walkthrough of every
   tunable YAML.
4. `RUNBOOK.md` — every operational command you'll run more than once.

---

## 3. Customize the five YAML configs

Every tunable value lives under `config/`. **You do not need to edit any
Python code** for a first deploy.

Starter templates for all five files live under `examples/config/`.
They describe a fictional persona ("Alex Rivera, Austin-based senior
backend engineer, dev-tools/fintech targets") that looks nothing like
the original author — so you have a clean baseline to rewrite.

```powershell
# Copy the starter templates into config/
Copy-Item -Recurse examples/config/* config/
```

Then edit each of the five in place. Brief summary — read
[`USER_CONFIG.md`](./USER_CONFIG.md) for the full walkthrough:

| File | What it is | Edit scope |
|---|---|---|
| `candidate_profile.yaml` | Haiku system prompt. The heart of semantic scoring. | 100% user. Rewrite the whole `system_prompt` block and all `calibration_anchors`. |
| `companies.yaml` | Target companies + ATS slugs. | 100% user. Delete every sample entry, add your own tier S/1/2 list. |
| `scoring.yaml` | Algo prefilter weights, keywords, gates, tiers. | Mixed. Weights + keywords are yours; structural keys stay. |
| `sources.yaml` | Which scrapers run and how often. | Mostly shared. **Required:** replace `scraper_defaults.contact_email` with a real reachable email. Optional: rewrite the LinkedIn search URLs. |
| `taxonomy.yaml` | Industry + role-type classification for UI filters. | Mixed. Edit the keyword lists to match your domains. |

Look for `[USER]`, `[SHARED]`, and `[MIXED]` tags at the top of each
section in the example files — they flag which knobs to touch.

### Structural invariants

A pytest guard (`src/scoring/tests/test_deployed_config.py`) enforces
that your edits preserve the keys the runtime depends on. Run it after
every edit:

```powershell
python -m pytest src/scoring/tests/test_deployed_config.py -q
```

14 passing assertions = you haven't broken anything structural.

---

## 4. One-time infrastructure setup

### Store your secrets in SSM

```powershell
aws ssm put-parameter --name /jobs-aggregator/anthropic_api_key `
  --value "sk-ant-..." --type SecureString --region us-east-1

# Optional — only if you enabled apify_linkedin in sources.yaml
aws ssm put-parameter --name /jobs-aggregator/apify_token `
  --value "apify_api_..." --type SecureString --region us-east-1

# Basic-auth credential for the CloudFront edge (user:pass base64-encoded)
# Example for "owner:changeme":
# PowerShell:
$pair  = "owner:changeme"
$b64   = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($pair))
aws ssm put-parameter --name /jobs-aggregator/basic_auth_b64 `
  --value $b64 --type SecureString --region us-east-1
```

### Deploy the stack

```powershell
sam build
sam deploy --guided    # first deploy only — answer the prompts
```

Say yes to saving arguments to `samconfig.toml`; after that just
`sam deploy` every time.

The first deploy creates: four DynamoDB tables, ~15 Lambdas, the API
Gateway, the S3 site bucket, the CloudFront distribution, and the
EventBridge schedules for each scraper.

### Upload the frontend

```powershell
aws s3 sync frontend/ s3://<SiteBucket-from-stack-outputs>/ --delete
```

The `<SiteBucket>` name comes from the `sam deploy` output.

### Seed the companies table

```powershell
python scripts/seed_companies.py
```

This reads `config/companies.yaml` and writes one row per company to
the `Companies` DynamoDB table.

---

## 5. Trigger the first scrape

```powershell
# Manually fire one scraper to prove the pipeline works end-to-end:
aws lambda invoke --function-name <ScrapeRemoteOkFn-name-from-deploy> `
  --payload '{}' out.json --region us-east-1
Get-Content out.json
```

Then hit the dashboard:

```
https://<your-CloudFront-domain>/
```

It'll prompt for the basic-auth credential you set. Inside you should
see the jobs that scraper just pulled, each with a Haiku-assigned
score and tier.

All the other scrapers run on their EventBridge schedules (see
`sources.yaml` for cadence) — nothing else to do.

---

## 6. Iterating after the first deploy

Anytime you want to change preferences:

```powershell
# 1. Edit any config/*.yaml
# 2. Ship it
sam build
sam deploy

# 3. If you edited candidate_profile.yaml or scoring.yaml, re-rank
#    every existing job with the new criteria (costs Haiku credits):
aws lambda invoke --function-name <RescoreFn-name> `
  --cli-binary-format raw-in-base64-out `
  --payload '{\"force_semantic\": true}' out.json --region us-east-1
```

Most config changes don't require a rescore — just the ones that
change how a given job is interpreted (the profile, scoring weights,
gates, modifiers).

---

## 7. What to do when something breaks

- **A scraper failed** that's by design, one-off failures are logged
  to the `ScrapeRuns` DynamoDB table and don't kill the run. Inspect:
  ```powershell
  aws dynamodb scan --table-name ScrapeRuns --region us-east-1 `
    --filter-expression "success = :f" `
    --expression-attribute-values '{\":f\": {\"BOOL\": false}}' `
    --limit 20
  ```
- **Haiku scores look compressed** (everything 50-70) — you almost
  certainly lost the `USE THE FULL 0-100 RANGE` block in
  `candidate_profile.yaml`. Put it back, redeploy, force rescore.
- **Deploy fails** tail the specific Lambda's logs:
  ```powershell
  sam logs -n <FnLogicalId> --stack-name jobs-aggregator --tail
  ```
- **Frontend looks stale after upload** invalidate CloudFront:
  ```powershell
  aws cloudfront create-invalidation --distribution-id <Exxxxx> --paths "/*"
  ```

---

## 8. Re-exporting your own public fork (meta)

If you fork this project, improve it, and want to share *your* version
back out with your personal data scrubbed, there's a helper:

```powershell
python scripts/prepare_public_export.py C:\path\to\fresh-output-dir
```

This:
- Copies the repo to `<output-dir>` excluding secrets, caches, and
  local-only artifacts.
- Runs regex substitutions across text files to strip identifiers.
- Swaps your live `config/*.yaml` for the generic `examples/config/*`
  templates so your real preferences don't ship in the public tree.
- Writes a `FORK_NOTICE.md` at the root of the export explaining
  provenance.

Use `--dry-run` first to preview what'll be touched. The script refuses
to write into a directory inside the source repo.

---

## Questions?

This is a one-person tool that just happens to be forkable. If you run
into something the docs don't cover, the three source-of-truth files
are:

- `CLAUDE.md` — architecture + the 10-phase build plan
- `RUNBOOK.md` — every operational command
- `docs/USER_CONFIG.md` — per-file YAML reference

Good luck with your search.
