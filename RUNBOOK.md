# RUNBOOK

Every non-trivial command you'll paste is here.

> **Shell note:** examples are shown in **both** bash (WSL / git-bash) and
> **PowerShell** where they differ. Pick whichever shell you run from.
> Paths like `/tmp/...` and the `&&` chain operator are bash-only. In
> PowerShell, use `.\file.json` or `$env:TEMP\file.json`, and run chained
> commands on separate lines. File I/O: prefer `Set-Content -NoNewline`
> over `echo >` (PS's redirect writes UTF-16 BOM which breaks AWS CLI).

Run from the repo root.

---

## 1. Generate the HTTP basic-auth credential

Pick a password, then base64-encode `user:password`:

**bash:**
```bash
echo -n 'admin:CHOOSE_PW' | base64
```

**PowerShell:**
```powershell
[Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes('admin:CHOOSE_PW'))
```

Copy the output — you'll paste it as `BasicAuthBase64` during the first
`sam deploy --guided`.

> Note: CloudFront Functions cannot read CloudFormation parameters at
> runtime, so this base64 value is baked into the function source at
> deploy time. If you rotate the password, just redeploy.

---

## 2. First deploy

```bash
sam build
sam deploy --guided
```

### 2a. PowerShell SAM build workaround

Two known errors when running `sam build` / `sam deploy` from PowerShell:

1. `PermissionError: [WinError 5] Access is denied: '...AppData\Roaming\AWS SAM\metadata.json'`
   — SAM's telemetry init crashes because the `AWS SAM` AppData dir has
   broken NTFS ACLs (cannot `takeown` without UAC elevation).
   `SAM_CLI_TELEMETRY=0` does **not** help — the metric object is still
   instantiated. **Fix:** redirect SAM's config dir via the `__SAM_CLI_APP_DIR`
   env var.
2. `PythonPipBuilder:Validation - Binary validation failed for python ... did not satisfy constraints for runtime: python3.12`
   — happens when the active PATH has Python 3.11 / 3.13 / 3.14 but not 3.12.
   Prepend the Python 3.12 install dir to PATH for the SAM invocation only.

**One-time setup (re-run if env is wiped):**

```powershell
# Persistent fix for the AppData ACL bug. Runs without UAC.
$target = 'C:\Users\<your-user>\.aws-sam'
New-Item -ItemType Directory -Path $target -Force | Out-Null
[System.Environment]::SetEnvironmentVariable('__SAM_CLI_APP_DIR', $target, 'User')
# Verify:
[System.Environment]::GetEnvironmentVariable('__SAM_CLI_APP_DIR', 'User')
```

After running, **open a new PowerShell** so it inherits the var. Existing
shells (including the one that set it) will still need a per-session
override — see below.

**Per-session override (use only in shells started before the one-time
setup, or if the persistent var is missing):**

```powershell
$env:__SAM_CLI_APP_DIR = 'C:\Users\<your-user>\.aws-sam'
$env:PATH = 'C:\Users\<your-user>\AppData\Local\Programs\Python\Python312;' + $env:PATH
& 'C:\Program Files\Amazon\AWSSAMCLI\bin\sam.cmd' build --no-cached
& 'C:\Program Files\Amazon\AWSSAMCLI\bin\sam.cmd' deploy --no-confirm-changeset
```

The Python-3.12 PATH prepend is per-session by design — if you also have
3.11 / 3.13 / 3.14 installed, you don't want to globally repin python.exe.

During `--guided` answer:

- **Stack Name:** `jobs-aggregator` (already defaulted in samconfig.toml)
- **AWS Region:** `us-east-1`
- **Parameter BasicAuthBase64:** _paste the base64 from §1_
- **Parameter DomainName:** _leave empty_
- **Parameter AcmCertificateArn:** _leave empty_
- **Confirm changes before deploy:** `Y`
- **Allow SAM CLI IAM role creation:** `Y`
- **Disable rollback:** `N`
- **Save arguments to samconfig.toml:** `Y`

After the deploy completes, the Outputs section prints:

- `CloudFrontURL` — your site (e.g. `https://d1xxxxxxx.cloudfront.net`)
- `ApiEndpoint` — direct API endpoint (keep private)
- `SiteBucketName` — where the frontend files go

---

## 3. Upload the frontend to S3

**bash:**
```bash
SITE_BUCKET=$(aws cloudformation describe-stacks \
  --stack-name jobs-aggregator \
  --query "Stacks[0].Outputs[?OutputKey=='SiteBucketName'].OutputValue" \
  --output text)

aws s3 sync frontend/ "s3://$SITE_BUCKET/" \
  --delete \
  --cache-control "public, max-age=60"
```

**PowerShell:**
```powershell
$SITE_BUCKET = aws cloudformation describe-stacks `
  --stack-name jobs-aggregator `
  --query "Stacks[0].Outputs[?OutputKey=='SiteBucketName'].OutputValue" `
  --output text

aws s3 sync frontend/ "s3://$SITE_BUCKET/" `
  --delete `
  --cache-control "public, max-age=60"
```

---

## 4. Invalidate the CloudFront cache (after frontend changes)

```bash
DIST_ID=$(aws cloudfront list-distributions \
  --query "DistributionList.Items[?Comment=='' && Origins.Items[?Id=='site-s3']].Id | [0]" \
  --output text)

aws cloudfront create-invalidation --distribution-id "$DIST_ID" --paths "/*"
```

(If the query above returns `None`, look up the distribution ID in the
CloudFront console and export it manually: `export DIST_ID=E1XXXXXX`.)

---

## 5. Open the site on your iPhone

1. Copy the `CloudFrontURL` output into Safari on iPhone.
2. iOS prompts for basic auth — enter `admin` / your chosen password.
3. You should see the dashboard load with the Health card showing
   `{"ok": true, "service": "jobs-aggregator", ...}`.

If you see a 401 loop, the base64 you entered doesn't match what iOS is
sending — regenerate and redeploy.

---

## 6. Subsequent deploys

```bash
sam build && sam deploy
```

(No `--guided` needed after the first deploy — samconfig.toml has the
parameters cached.)

---

## 7. Tail Lambda logs

```bash
sam logs -n ApiHealthFn --stack-name jobs-aggregator --tail
```

Swap `ApiHealthFn` for any of: `ApiStatsFn`, `ApiJobsFn`, `ApiPrefsFn`,
`ScrapeDispatcherFn`, `ScrapeWorkerFn`, `RescoreFn`.

---

## 8. Manually trigger a scraper

Two ways. The **direct Lambda invoke** is the one to use day-to-day — it
bypasses CloudFront basic-auth and doesn't require URL-encoding anything.

### 8a. Direct Lambda invoke (recommended)

**bash:**
```bash
DISPATCHER=$(aws cloudformation describe-stacks --stack-name jobs-aggregator \
  --query "Stacks[0].Outputs[?OutputKey=='ScrapeDispatcherFnName'].OutputValue" \
  --output text)

# The three clean-JSON sources at once
echo '{"sources":["remoteok","himalayas","hnhiring"]}' > /tmp/payload.json
aws lambda invoke --function-name "$DISPATCHER" \
  --payload fileb:///tmp/payload.json \
  /tmp/dispatcher-out.json && cat /tmp/dispatcher-out.json
```

**PowerShell:**
```powershell
$DISPATCHER = aws cloudformation describe-stacks --stack-name jobs-aggregator `
  --query "Stacks[0].Outputs[?OutputKey=='ScrapeDispatcherFnName'].OutputValue" `
  --output text

# Write payload to the current dir; -NoNewline avoids trailing \n in the JSON
Set-Content -Path .\payload.json -Value '{"sources":["remoteok","himalayas","hnhiring"]}' -NoNewline

aws lambda invoke --function-name $DISPATCHER --payload fileb://payload.json out.json
Get-Content .\out.json
```

The dispatcher returns in <1s; the actual scrape runs async in
`ScrapeWorkerFn`. Give it ~30-60s, then check the dashboard or logs.

### 8b. Via HTTPS (works, but needs the basic-auth header)

```bash
curl -u admin:CHOOSE_PW -X POST "https://<CloudFrontURL>/api/scrape/remoteok"
```

### 8c. Watch a scrape in progress

```bash
sam logs -n ScrapeWorkerFn --stack-name jobs-aggregator --tail
```

Each run emits a `scrape_run_start` line and a `scrape_run_done` line
with counts (`jobs_found`, `jobs_new`, `jobs_updated`).

---

## 9. Rescore all jobs after changing scoring weights

`config/scoring.yaml` is the ONLY copy — edit it, then `sam build && sam deploy`.
The file is shipped to Lambda via the `ConfigLayer`
(`AWS::Serverless::LayerVersion`) declared in `template.yaml` and is
served at `/opt/scoring.yaml` on every function. There is no separate
`src/config/` copy; the loaders have a legacy fallback that would
silently shadow the layer if you re-introduce one — don't.

```powershell
# Edit config/scoring.yaml with your changes, then:
sam build
sam deploy
```

Then invoke `RescoreFn` to apply the new weights retroactively:

**PowerShell:**
```powershell
# 1. Deploy updated config
sam build
sam deploy

# 2. Find the RescoreFn name (one-time; bookmark this value)
$RESCORE_FN = aws lambda list-functions `
  --query "Functions[?starts_with(FunctionName, 'jobs-aggregator-Rescore')].FunctionName | [0]" `
  --output text

# 3. Dry-run first — scores every job but writes nothing back
Set-Content -Path .\rescore_dry.json -Value '{"dry_run":true}' -NoNewline
aws lambda invoke --function-name $RESCORE_FN --payload fileb://rescore_dry.json rescore_out.json
Get-Content .\rescore_out.json

# 4. Real run — writes updated scores to DynamoDB
Set-Content -Path .\rescore_real.json -Value '{}' -NoNewline
aws lambda invoke --function-name $RESCORE_FN --payload fileb://rescore_real.json rescore_out.json
Get-Content .\rescore_out.json
```

Tail logs while it runs:
```powershell
sam logs -n RescoreFn --stack-name jobs-aggregator --tail
```

---

## 9a. Add or edit a target company

Single source of truth is `config/companies.yaml`. Edit + redeploy:

1. Edit `config/companies.yaml` — add/change the company entry.
2. Build + deploy so the ConfigLayer picks up the change:

```powershell
sam build --no-cached
sam deploy
```

3. Seed the Companies DynamoDB table (no deploy needed — hits DynamoDB directly):

```powershell
python scripts/seed_companies.py
```

---

## 9b. Manually trigger the ATS scrapers

```powershell
$DISPATCHER = aws cloudformation describe-stacks --stack-name jobs-aggregator `
  --query "Stacks[0].Outputs[?OutputKey=='ScrapeDispatcherFnName'].OutputValue" `
  --output text

Set-Content -Path .\ats_payload.json -Value '{"sources":["greenhouse","lever","ashby"]}' -NoNewline
aws lambda invoke --function-name $DISPATCHER --payload fileb://ats_payload.json ats_out.json
Get-Content .\ats_out.json
```

The dispatcher fires one async `ScrapeWorkerFn` per source and returns in <1s.
Each worker fetches all companies for its ATS type sequentially (one per second).
Allow 2-5 minutes for all three to complete, then check the dashboard.

Watch progress:
```powershell
sam logs -n ScrapeWorkerFn --stack-name jobs-aggregator --tail
```

---

## 9c. Run the scoring tests locally

```powershell
# From the repo root — no AWS credentials or network needed
python -m pytest src/scoring/tests/ -v
```

Three tests must pass:
- `test_vp_gaming_scores_tier1` — high-fit fixture → score in T1 band
- `test_analyst_espn_scores_midtier` — mid-fit fixture → score in T3 band
- `test_intern_is_hard_gated` — intern title → score 0, seniority gate

If a test fails with "GOLDEN SCORE CHANGED", investigate the scoring change
before updating the expected value.

---

## 10. Read / write an SSM parameter (for API tokens)

```bash
# Store
aws ssm put-parameter --name /jobs-aggregator/apify_token \
  --value "apify_api_XXXX" --type SecureString --overwrite

# Read
aws ssm get-parameter --name /jobs-aggregator/apify_token \
  --with-decryption --query "Parameter.Value" --output text
```

---

## 10a. Verify ATS slugs are live

Any time a company is added or its `ats`/`ats_slug` is changed in
`config/companies.yaml`, confirm the slug resolves to a live
Greenhouse / Lever / Ashby board before deploying. A bad slug wastes
an HTTP call per daily run and pollutes `ScrapeRuns` with warnings.

**Quick check (fast, just probes current slugs):**
```powershell
python scripts/verify_ats_slugs.py
```

Every company should report `[OK]` (or `[EMPTY]` — a live board with zero
current postings, which is fine). Any `[404]` or `[ERR]` row means the
slug in `companies.yaml` is wrong.

**Discovery (when verify_ats_slugs shows 404s — tries slug variants):**
```powershell
python scripts/discover_ats_slugs.py
```

For each failing company it probes ~5 slug variants across all three ATSes
(Greenhouse, Lever, Ashby) and prints a YAML diff you can paste into
`config/companies.yaml`. Companies that don't resolve anywhere should be
marked `ats: null, ats_slug: null` and picked up by the HTML/RSS scrapers
or by the Workday scraper.

After editing `config/companies.yaml`, don't forget §9a (rebuild, redeploy, reseed).

---

## 11. Seed the Companies table

```powershell
python scripts/seed_companies.py
```

Reads `config/companies.yaml` and UPSERTs every company into the Companies
DynamoDB table. Safe to re-run. Verify with:

```powershell
aws dynamodb scan `
  --table-name (aws cloudformation describe-stacks --stack-name jobs-aggregator `
    --query "Stacks[0].Outputs[?OutputKey=='CompaniesTableName'].OutputValue" `
    --output text) `
  --select COUNT
```

---

## 12. Verify DynamoDB tables are live

```bash
aws dynamodb list-tables --query "TableNames[?starts_with(@, 'jobs-aggregator')]"
```

Expect to see four tables — Jobs, Companies, ScrapeRuns, UserPrefs (with
the stack-name prefix CloudFormation appends).

---

## 13. Run the test suite locally

```bash
# One-time
python -m pip install -r requirements-dev.txt

# Every time
pytest src/
```

All tests run against in-memory moto mocks — no AWS calls, no creds needed.

---

## 14. Tear down (only if you're abandoning the project)

```bash
# Empty both S3 buckets first (CloudFormation won't delete non-empty buckets)
aws s3 rm "s3://$SITE_BUCKET/" --recursive
RAW_BUCKET=$(aws cloudformation describe-stacks --stack-name jobs-aggregator \
  --query "Stacks[0].Outputs[?OutputKey=='RawScrapeBucketName'].OutputValue" \
  --output text)
aws s3 rm "s3://$RAW_BUCKET/" --recursive

sam delete --stack-name jobs-aggregator
```

---

## 15. End-to-end scrape acceptance check

After `sam build && sam deploy` succeeds:

**bash:**
```bash
# Deploy the frontend (see §3)
aws s3 sync frontend/ "s3://$SITE_BUCKET/" --delete --cache-control "public, max-age=60"

# Trigger a scrape (see §8a)
DISPATCHER=$(aws cloudformation describe-stacks --stack-name jobs-aggregator \
  --query "Stacks[0].Outputs[?OutputKey=='ScrapeDispatcherFnName'].OutputValue" \
  --output text)
echo '{"sources":["remoteok","himalayas","hnhiring"]}' > /tmp/payload.json
aws lambda invoke --function-name "$DISPATCHER" \
  --payload fileb:///tmp/payload.json /tmp/out.json
```

**PowerShell:**
```powershell
# Deploy the frontend (see §3)
aws s3 sync frontend/ "s3://$SITE_BUCKET/" --delete --cache-control "public, max-age=60"

# Trigger a scrape (see §8a)
$DISPATCHER = aws cloudformation describe-stacks --stack-name jobs-aggregator `
  --query "Stacks[0].Outputs[?OutputKey=='ScrapeDispatcherFnName'].OutputValue" `
  --output text
Set-Content -Path .\payload.json -Value '{"sources":["remoteok","himalayas","hnhiring"]}' -NoNewline
aws lambda invoke --function-name $DISPATCHER --payload fileb://payload.json out.json
Get-Content .\out.json
```

Wait ~60s for all three workers to complete, then reload the dashboard.

Acceptance: the iPhone dashboard shows a non-empty list of jobs with
titles, companies, and posted dates.

---

## 16. Manually trigger HTML / RSS / CSV scrapers

The HTML/RSS/CSV scrapers run on their own EventBridge schedule
(`DailyHtmlRssScrapeSchedule`, daily 06:30 UTC). To fire them on demand:

**bash / git-bash:**
```bash
echo '{"sources":["weworkremotely","working_nomads","fractional_jobs","asgc_sheet","hitmarker","gamesindustry","builtinnyc","welcometothejungle","work_with_indies","remote_game_jobs","outscal","games_jobs_direct","sheet_rehm","sheet_mayne","sheet_tucker","sheet_ploger"]}' > /tmp/p7.json

DISPATCHER=$(aws cloudformation describe-stacks --stack-name jobs-aggregator \
  --query "Stacks[0].Outputs[?OutputKey=='ScrapeDispatcherFnName'].OutputValue" \
  --output text)

MSYS2_ARG_CONV_EXCL='*' aws lambda invoke \
  --function-name "$DISPATCHER" \
  --payload fileb:///tmp/p7.json \
  --region us-east-1 \
  /tmp/out.json
cat /tmp/out.json
```

**PowerShell:**
```powershell
Set-Content -Path .\p7.json -NoNewline -Value '{"sources":["weworkremotely","working_nomads","fractional_jobs","asgc_sheet","hitmarker","gamesindustry","builtinnyc","welcometothejungle","work_with_indies","remote_game_jobs","outscal","games_jobs_direct","sheet_rehm","sheet_mayne","sheet_tucker","sheet_ploger"]}'
$DISPATCHER = aws cloudformation describe-stacks --stack-name jobs-aggregator `
  --query "Stacks[0].Outputs[?OutputKey=='ScrapeDispatcherFnName'].OutputValue" `
  --output text
aws lambda invoke --function-name $DISPATCHER --payload fileb://p7.json out.json
Get-Content .\out.json
```

Wait ~90s for the async workers to finish, then check ScrapeRuns:

```bash
python scripts/check_phase7_smoke.py
```

The script prints one row per source with `jobs_found`, `jobs_new`,
duration, and last-run timestamp — handy for spotting a source that
silently went to zero.

**Disabled sources** (config-disabled in `config/sources.yaml`):
- `bettingjobs` — historically ran on applyflow.com SaaS, JS-rendered.
  An undocumented seeker JSON API was reverse-engineered later (see
  `src/scrapers/bettingjobs.py`) — re-enable in `config/sources.yaml`
  if you want gambling/sportsbook coverage.
- `wellfound` — anonymous requests get 0 results (bot detection). Needs
  cookie-jar / session refresher to re-enable.

Run `python scripts/check_phase7_yields.py` to see the top entries
per source — confirms the parsers are extracting clean, scoreable data.

### 16a. Pull a raw payload from S3 for selector debugging

When tuning a TODO scraper, fetch one of the archived payloads to inspect
the actual HTML the worker saw:

```bash
RAW_BUCKET=$(aws cloudformation describe-stacks --stack-name jobs-aggregator \
  --query "Stacks[0].Outputs[?OutputKey=='RawScrapeBucketName'].OutputValue" \
  --output text)

# Replace <date>/<file> with the raw_s3_key from check_phase7_smoke.py
MSYS2_ARG_CONV_EXCL='*' aws s3 cp \
  "s3://$RAW_BUCKET/raw/builtinnyc/<YYYY>/<MM>/<DD>/<file>.jsonl.gz" \
  ./builtinnyc_raw.jsonl.gz
gunzip -f ./builtinnyc_raw.jsonl.gz

# Pretty-print one card's _html field
python -c "import json; d=json.loads(open('builtinnyc_raw.jsonl', encoding='utf-8').readline); print(d['_html'][:2000])"
```

---

## 17. LLM semantic scoring (Claude Haiku)

The scorer blends a rule-based algo score with a Claude Haiku
semantic-fit score. Algo is fast, deterministic, free; Haiku is slow,
probabilistic, ~$0.0005/job. The blend is the value stored as `score`
in DynamoDB; the algo score is preserved separately as `algo_score` for
diagnostics. Default blend weights: `0.4` algo / `0.6` semantic.

### 17a. One-time setup — store the API key in SSM

Get an Anthropic API key from https://console.anthropic.com/, then:

**PowerShell:**
```powershell
aws ssm put-parameter `
  --name "/jobs-aggregator/anthropic_api_key" `
  --type SecureString `
  --value "sk-ant-XXXXX" `
  --region us-east-1
```

**bash:**
```bash
aws ssm put-parameter \
  --name "/jobs-aggregator/anthropic_api_key" \
  --type SecureString \
  --value "sk-ant-XXXXX" \
  --region us-east-1
```

The Lambda IAM role already has `SSMParameterReadPolicy` for
`jobs-aggregator/*` — no template change needed.

### 17b. Editing the candidate profile

The system prompt + biographical facts that Haiku sees lives in
`config/candidate_profile.yaml` (single source of truth). Edit it,
redeploy (the ConfigLayer ships the update), then force-rescore:

```powershell
sam build
sam deploy
# Force re-call Haiku for every cached job:
$RESCORE_FN = aws lambda list-functions `
  --query "Functions[?starts_with(FunctionName, 'jobs-aggregator-Rescore')].FunctionName | [0]" `
  --output text
'{"force_semantic": true}' | Set-Content -Encoding ascii payload.json
aws lambda invoke `
  --function-name $RESCORE_FN `
  --cli-binary-format raw-in-base64-out `
  --payload file://payload.json out.json --region us-east-1
Get-Content out.json
```

### 17c. Single-job CLI tester

Quickest way to debug "why did THIS job get score X":

**PowerShell:**
```powershell
$env:ANTHROPIC_API_KEY = "sk-ant-XXXXX"
$env:JOBS_TABLE = (aws cloudformation describe-stacks --stack-name jobs-aggregator `
  --query "Stacks[0].Outputs[?OutputKey=='JobsTableName'].OutputValue" `
  --output text)

# Score by job_id:
python scripts/score_job.py --job-id "remoteok:12345"

# Score by source + native_id:
python scripts/score_job.py --source weworkremotely --native-id 67890

# Score a hypothetical job from inline JSON:
python scripts/score_job.py --json '{"title":"VP X","company":"Y","description":"..."}'

# Score from a JSON file:
python scripts/score_job.py --json fixture.json

# Algo-only (skips API call, free):
python scripts/score_job.py --job-id "remoteok:12345" --skip-semantic

# Re-call Haiku ignoring the cache:
python scripts/score_job.py --job-id "remoteok:12345" --force-semantic

# Persist the new score back to DynamoDB:
python scripts/score_job.py --job-id "remoteok:12345" --write
```

### 17d. Tuning the blend

Edit `config/scoring.yaml` → `semantic:` block. Knobs:

| key | default | what it does |
|---|---|---|
| `enabled` | `true` | kill switch — `false` = algo-only mode (no API calls) |
| `model` | `claude-haiku-4-5-20251001` | which Haiku version |
| `blend_weight_algo` | `0.4` | weight on the rule-based score |
| `blend_weight_semantic` | `0.6` | weight on Haiku's score |
| `skip_below_algo` | `20` | skip API call when algo < N (saves $) |
| `cache_days` | `7` | reuse cached semantic score for N days |
| `rate_limit_sleep_ms` | `500` | min spacing between calls in one Lambda |

After editing weights only:
```powershell
sam build
sam deploy
# Re-blend without paying for new Haiku calls:
$RESCORE_FN = aws lambda list-functions `
  --query "Functions[?starts_with(FunctionName, 'jobs-aggregator-Rescore')].FunctionName | [0]" `
  --output text
'{}' | Set-Content -Encoding ascii payload.json
aws lambda invoke --function-name $RESCORE_FN `
  --cli-binary-format raw-in-base64-out `
  --payload file://payload.json out.json --region us-east-1
```

(Caveat: if you bump `skip_below_algo` upward you'll need
`{"force_semantic": true}` to actually re-grade jobs in the new band.)

### 17e. Cost forecast

Per-job: `~$0.0005` (Haiku at $0.25/1M input + $1.25/1M output, with
~3K-token JD truncation and ~150-token reply).

At a typical scrape volume:
- ~4K active jobs in the table
- ~300 new jobs/day across all sources
- ~70% pass `algo_score >= 20` and get a semantic call
- → ~210 Haiku calls/day = ~6,300/month
- → ~$3/month. Comfortably under a $5/month cap.

### 17f. Troubleshooting

**"semantic_score" is missing from new jobs:**
1. Check SSM key is set: `aws ssm get-parameter --name "/jobs-aggregator/anthropic_api_key" --with-decryption`
2. Tail ScrapeWorker logs for `semantic_*` warnings:
   `sam logs -n ScrapeWorkerFn --stack-name jobs-aggregator --tail --filter semantic`
3. Confirm `anthropic` is bundled in the Lambda zip:
   `unzip -l .aws-sam/build/ScrapeWorkerFn/anthropic/__init__.py`

**Every job has `semantic_skipped: true`:**
- Either the kill switch is off (`semantic.enabled: false` in scoring.yaml)
- Or every job has `algo_score < skip_below_algo` (lower the threshold or fix the algo)
- Or the API key fetch is failing (see logs for `semantic_ssm_key_fetch_failed`)

**`aws lambda invoke` "Invalid base64" or "Could not parse payload":**

PowerShell 5.x (Windows default) silently strips inner double-quotes
when shelling out to native binaries. `--payload '{"k":true}'` arrives
at AWS as `{k:true}` and gets rejected. Two fixes:

```powershell
# Bulletproof — write JSON to a file first
'{"force_semantic": true}' | Set-Content -Encoding ascii payload.json
aws lambda invoke `
  --function-name $RESCORE_FN `
  --payload file://payload.json `
  --cli-binary-format raw-in-base64-out `
  --invocation-type Event `
  --region us-east-1 `
  out.json
```

Use `--invocation-type Event` for the long rescore (returns 202
immediately; rescore runs in background for 30-40 min). Watch via
`sam logs -n RescoreFn --stack-name jobs-aggregator --tail`.

Permanent fix (PS 7+ only):
```powershell
$PSNativeCommandArgumentPassing = 'Standard'
```

**Async retry storm warning:** `--invocation-type Event` defaults to
**2 retry attempts on failure**, and a 15-min Lambda timeout counts as
failure. A single `force_semantic=true` invocation that times out will
auto-retry 2 more times — each restarting the scan from the top with
`force_semantic=true` bypassing cache, **triple-billing** the API.

The template ships `MaximumRetryAttempts: 0` for `RescoreFn` already,
since RescoreFn is only invoked manually. If a stack redeploy ever
resets it, re-apply:
```powershell
aws lambda put-function-event-invoke-config `
  --function-name $RESCORE_FN `
  --maximum-retry-attempts 0
```

**Recovery pattern if a force_semantic run times out:** fire ONE
follow-up with `force_semantic=false`. Already-refreshed rows
cache-hit (semantic_scored_at within `cache_days`), so the scan races
through them and burns Haiku only on the unrefreshed tail.

**Sharding a large refresh:** `RescoreFn` accepts `segment` +
`total_segments` for DynamoDB parallel-scan, plus `min_age_hours` to
skip rows refreshed within the last N hours (clean resume after a
partial run). Cap shards conservatively — at typical token sizes the
Anthropic per-org input-token rate limit (e.g. 450K tpm for
`claude-haiku-4-5`) is the binding constraint, not Lambda capacity:

```powershell
# 2 parallel shards, force-refresh
$LAMBDA = $RESCORE_FN
0..1 | ForEach-Object {
  $seg = $_
  $payload = "{`"force_semantic`": true, `"segment`": $seg, `"total_segments`": 2}"
  $payload | Set-Content -Encoding ascii payload.json
  aws lambda invoke `
    --function-name $LAMBDA `
    --invocation-type Event `
    --cli-binary-format raw-in-base64-out `
    --payload file://payload.json `
    "rescore_seg_$seg.json"
}

# Resume the unprocessed tail without re-burning API on already-done rows
'{"force_semantic": true, "segment": 0, "total_segments": 2, "min_age_hours": 2.0}' `
  | Set-Content -Encoding ascii payload.json
aws lambda invoke --function-name $LAMBDA `
  --invocation-type Event `
  --cli-binary-format raw-in-base64-out `
  --payload file://payload.json rescore_seg_0.json
```

### 17g. Function-gate semantic rescue (ENABLED)

The algo's `function` hard gate substring-matches "platform engineer",
"software engineer", etc. so leadership titles like "VP, Platform
Engineering" can land at `algo_score = 0`. Without rescue, that's a
permanent mis-classification of real fits.

**Behavior (rescue ON, default):**

When the ONLY gate fired is `function` (no seniority / comp /
geographic / engagement gate):
1. `combined.py` calls Haiku anyway (skip-below-algo check is skipped
   in this branch — algo is exactly 0 by construction).
2. If `semantic_score >= function_gate_rescue_min_semantic` (default
   **60**), the gate is overridden:
   - `final_score = semantic_score` (NOT a 0.4/0.6 blend — the algo
     contribution would be zero anyway, and we want Haiku's number
     to surface verbatim).
   - `"function_gate_rescued"` appears in `modifiers_applied` for
     transparency in the detail view.
3. If `semantic_score < threshold`, the gate stands and `final = 0`.
   The semantic rationale is still cached so we don't re-call.

**Multi-gate cases are NOT rescued.** If function fires alongside
seniority / comp / geographic / engagement, the job stays at 0 and
no semantic call is made. Those four gates are unambiguous
configured disqualifiers.

**Toggling rescue off:**
Set `semantic.function_gate_rescue: false` in `config/scoring.yaml`
and re-deploy (or invoke `RescoreFn` with `{"force_semantic": true}`
to re-run scoring without redeploy if config-only).

**Tuning the threshold:**
`semantic.function_gate_rescue_min_semantic` (default 60). Lowering
to 50 lets Haiku rescue more aggressively; raising to 75 means only
T1-strong cases get through.

**Cost impact:** rescue calls Haiku for every function-gated job
(~10-20 / month at typical scrape volumes), adding ~$0.01/month —
negligible vs. the ~$3 baseline. The reverse signal is far more
valuable: the rescued roles are typically dream-tier matches.

---

## 18. Workday tenant onboarding

The Workday scraper (`src/scrapers/workday.py`) reads tenant configs
from `config/companies.yaml` entries that have `ats: workday` and a
populated `workday:` block. Each verified tenant adds ~250 jobs/run.
Per-tenant errors are logged and swallowed — a wrong URL on one tenant
will NOT break the rest of the run.

### 18a. URL discovery (5 minutes per tenant)

1. Open the company's careers landing page in a desktop browser.
2. Open DevTools → Network tab. Filter by "jobs" or "cxs".
3. Click any "Search" / "Apply filter" button — Workday-front-end pages
   POST to `/wday/cxs/{tenant}/{site}/jobs` on every interaction.
4. Copy the request URL — split into `base_url`, `tenant`, `site`.
5. Paste into `config/companies.yaml` under the company entry:
   ```yaml
   workday:
     base_url: "https://{subdomain}.{wdN}.myworkdayjobs.com"
     tenant:   "{tenant}"
     site:     "{site}"
     max_jobs: 250
   ```
6. Build + deploy (the ConfigLayer picks up the edit):
   ```powershell
   sam build
   sam deploy
   ```

Many large brands run on private vendor variants (`cxs2`, `wd102`, etc.)
or have moved off Workday entirely (e.g. some have switched to
Greenhouse, SmartRecruiters, Eightfold, iCIMS, or built bespoke careers
sites). If the Network panel shows POSTs to a host that isn't
`*.myworkdayjobs.com`, the company isn't a Workday tenant — find the
correct ATS and use the matching scraper instead.

### 18b. Smoke-test a new tenant before deploy

```powershell
$Env:PYTHONPATH = "src"
python -c "from scrapers.workday import WorkdayScraper; \
  s = WorkdayScraper; \
  c = 0
  for p in s.fetch:
      r = s.parse(p)
      if r and c < 3: print(r.native_id, '|', r.title, '|', r.location)
      c += 1
      if c >= 25: break
  print('total:', c)"
```

If you see real titles/locations the tenant is good. If you see
`workday_endpoint_not_found`, `workday_blocked_or_throttled`, or
`workday_misconfigured` warnings in stderr, the YAML block needs
fixing.

### 18c. Manually trigger the Workday scraper

```powershell
$WORKER = aws cloudformation describe-stacks --stack-name jobs-aggregator `
  --query "Stacks[0].Outputs[?OutputKey=='ScrapeWorkerFnName'].OutputValue" `
  --output text

$Env:MSYS2_ARG_CONV_EXCL = "*"
aws lambda invoke `
  --function-name $WORKER `
  --cli-binary-format raw-in-base64-out `
  --payload '{"source":"workday"}' `
  out.json --region us-east-1
Get-Content out.json
```

Look for `jobs_new > 0` and `status=ok`. With one tenant configured,
expect `jobs_found ≈ 250` and `duration_ms ≈ 90000` (250 jobs /
20 per page = 13 paginated requests at 1 req/sec, plus scoring time).

### 18d. Why we don't fetch detail pages

Workday's list response gives us title + location + URL — enough for
scoring. Hitting the per-job detail endpoint would double the request
volume per tenant (250 → 500 round-trips) and many tenants 403 /
rate-limit on the detail endpoint. The scoring engine works fine
without the description; the click-through URL takes the user to the
full listing in one tap.

If a tenant's roles consistently score low because they need
description-text matching, revisit later — could add a per-tenant
`fetch_descriptions: true` flag to the workday block.

### 18e. SmartRecruiters tenants

The SmartRecruiters scraper (`src/scrapers/smartrecruiters.py`)
runs on the same weekly cadence as Greenhouse / Lever / Ashby /
Workday. Each company entry needs:

```yaml
- name: "Example Co"
  ats: smartrecruiters
  ats_slug: "ExampleCo"               # the SR company id (case-sensitive)
  smartrecruiters:
    max_jobs: 250                     # optional cap; default 250
```

Discovering the SR company id: open the careers landing page in a
browser. The URL `https://jobs.smartrecruiters.com/{COMPANY_ID}/...`
or `https://careers.{tenant}.com/...` will redirect — check the
network tab for `api.smartrecruiters.com/v1/companies/{COMPANY_ID}/postings`.

Smoke-test:
```powershell
$Env:PYTHONPATH = "src"
python -c "from scrapers.smartrecruiters import SmartRecruitersScraper; \
  s = SmartRecruitersScraper; \
  c = 0
  for p in s.fetch:
      r = s.parse(p)
      if r and c < 3: print(r.native_id, '|', r.title, '|', r.location)
      c += 1
      if c >= 25: break
  print('total:', c)"
```

Why we DO fetch detail pages on SmartRecruiters (unlike Workday):
the SR list endpoint omits the description and applyUrl entirely.
Without the detail call we'd lose all keyword matching, so the
extra round-trip is worth the time. At 250 jobs/company × 2 rps
that's ~125s per company — well inside the Lambda 900s timeout.

---

## 19. Frontend polish

### 19a. File map

```
frontend/
├── index.html       Today (dashboard) — stats + top 50 jobs
├── all.html         All jobs — filter + load-more pagination
├── job.html         One job — detail, score breakdown, save/skip/applied
├── settings.html    Hidden companies, saved searches, display options
├── health.html      Per-source recent scrape runs
├── manifest.webmanifest
├── css/app.css      Single stylesheet, mobile-first, dark, ≥44px tap targets
└── js/
    ├── api.js       fetch wrapper around /api/*
    ├── common.js    bottom nav, toast, escapeHtml, scoreClass, formatPosted, renderJobRow
    ├── app.js       Today page
    ├── filters.js   All page (server pagination + client refine)
    ├── job.js       Job detail page
    ├── settings.js  Settings page
    └── health.js    Health page
```

Every HTML page loads `api.js` + `common.js` + its page-specific script
in that order. `common.js` auto-renders the bottom nav so no page has
to opt in. No build step — drop these into the S3 bucket as-is.

### 19b. Deploying frontend changes

```powershell
$Env:SITE_BUCKET = (aws cloudformation describe-stacks `
    --stack-name jobs-aggregator `
    --query "Stacks[0].Outputs[?OutputKey=='SiteBucketName'].OutputValue" `
    --output text)

aws s3 sync frontend/ "s3://$Env:SITE_BUCKET/" --delete

# CloudFront caches HTML/JS/CSS — invalidate after a frontend deploy
$DIST_ID = (aws cloudformation describe-stacks `
    --stack-name jobs-aggregator `
    --query "Stacks[0].Outputs[?OutputKey=='CloudFrontDistributionId'].OutputValue" `
    --output text)
aws cloudfront create-invalidation --distribution-id $DIST_ID --paths "/*"
```

If the CloudFront distribution ID isn't an output, fetch it once from
the console and stash in `$PROFILE` as a global. The `/*` invalidation
costs $0.005/path after the first 1000/month — well within free tier.

### 19c. iPhone smoke test

1. Load `https://<distribution>/` on iPhone Safari.
2. Confirm the bottom nav stays put when scrolling (not absolute-positioned
   inside main).
3. Tap each nav tab — Today / All / Health / Settings — confirm transition
   is instant and the active tab highlights.
4. On `/all.html`, change the Track filter — list updates without a
   server round-trip (client filters the loaded cache).
5. On `/job.html?id=…`, tap "Save" then "Save" again — first call shows
   toast "Marked as save", second click is disabled because button reflects
   new status.
6. Add a hidden company in `/settings.html`, reload — chip persists
   (round-tripped through `/api/prefs`).

If the bottom nav overlaps content on a notched iPhone, check that
`safe-area-inset-bottom` is being respected — most likely cause is a
`padding-bottom` override in a custom card.

### 19d. Lighthouse target

Mobile Lighthouse ≥90 on Performance + Accessibility + Best Practices.
The CSS is < 8KB minified-gzipped, the JS payload is < 6KB total (no
framework). The biggest variable is the API response sizes — keep
`/api/jobs?limit=50` under ~80KB by trimming the projection on
`ScoreIndex` (already INCLUDE-only) if it ever gets too fat.
Run:
```powershell
npx lighthouse "https://<distribution>/" --emulated-form-factor=mobile --only-categories=performance,accessibility,best-practices --view
```

### 19e. Backend endpoints powering the UI

- `GET  /api/stats`  — counts by band (T1/T2/T3/below) + track + source.
                        Cached 60s at CloudFront.
- `GET  /api/prefs`  — returns the full prefs dict for the configured user.
- `PUT  /api/prefs`  — body `{config_key, value}` upserts one key.
                        `value` may be list, dict, number, or string.
- `GET  /api/health` — imports every scraper module so `list_scrapers`
                        returns the full set.

---

## 20. Apify LinkedIn — wide-window backfill (gap recovery)

The default `apify_linkedin` schedule scrapes **the last 24 hours** of
LinkedIn postings (`f_TPR=r86400` in every search URL). Two situations
break that and need a one-shot wider-window run:

1. **Trial lapse / billing gap.** The bebity LinkedIn-scraper Apify
   actor is metered. When the trial expires (or a renewal lapses),
   nightly runs return zero rows for 1-3 days until you re-rent.
2. **Missed schedule.** EventBridge / Lambda outage, accidental
   `--invocation-type Event` retry storm, or the dispatcher being
   disabled for a deploy window.

The scraper supports an `f_TPR_override` knob — no code change
needed. `ApifyLinkedInScraper.fetch` rewrites both the URL-style
search params (`f_TPR=r86400` → the override) AND the bebity
structured-input field (`publishedAt`) before calling the actor.

**Valid window values** (from `_F_TPR_VALID` in `apify_linkedin.py`):

| value      | window       | typical use |
|------------|--------------|-------------|
| `r86400`   | last 24h     | (default — daily run) |
| `r604800`  | last 7 days  | most gap-recovery scenarios |
| `r2592000` | last 30 days | full re-baseline (rare) |

### 20a. Fire a 7-day backfill

```powershell
# Write the payload (PowerShell strips inner quotes from --payload args,
# so always use a file).
'{"source":"apify_linkedin","overrides":{"f_TPR_override":"r604800"}}' `
  | Set-Content -Encoding ascii apify_backfill_payload.json

$WORKER = aws cloudformation describe-stacks --stack-name jobs-aggregator `
  --query "Stacks[0].Outputs[?OutputKey=='ScrapeWorkerFnName'].OutputValue" `
  --output text

# Synchronous invoke. ScrapeWorkerFn timeout is 900s (Lambda max);
# the bebity actor typically returns in 3-8 min for 16 searches × 50,
# then per-job parsing + Haiku scoring adds a few more minutes for the
# new-job tail (cached jobs are no-ops). Plan on 8-13 min wall-clock.
aws lambda invoke `
  --function-name $WORKER `
  --region us-east-1 `
  --cli-read-timeout 900 `
  --cli-binary-format raw-in-base64-out `
  --payload file://apify_backfill_payload.json `
  apify_backfill_out.json

Get-Content .\apify_backfill_out.json
```

Expected response shape:
```json
{
  "ok": true,
  "source": "apify_linkedin",
  "summary": {
    "jobs_found": 600,
    "jobs_new": <varies>,
    "jobs_updated": 0,
    "duration_ms": 280000
  }
}
```

### 20b. Why this is dedup-safe

`base.scrape_run` keys every job on `job_id = "{source}:{native_id}"`.
For LinkedIn, `native_id` is the numeric posting ID baked into the
URL. Re-fetching jobs already in the table is a no-op write — the
existing row is touched but no duplicate is created. The cost is the
Apify result fee, not data integrity.

### 20c. Cost expectation

bebity/linkedin-jobs-scraper bills per result. With 16 active searches
capped at `count_per_search: 50` each:

- Worst case: 16 × 50 = **800 results × $0.005 = ~$4.00**
- Typical case: many pinned searches return < 25 unique rows; effective ~$2.50

A 30-day window (`r2592000`) saturates the 50/search cap on most
queries, so budget the full ~$4.00 for that case.

If you want to keep a backfill cheap, use `only_searches` to restrict
to a subset (e.g. the broad keyword queries — pinned per-company
searches mostly overlap on a wider window):
```json
{"source":"apify_linkedin",
 "overrides":{"f_TPR_override":"r604800",
              "only_searches":["broad-search-1",
                               "broad-search-2",
                               "broad-search-3"]}}
```

### 20d. Verify the run landed

The CLI invocation already prints a summary if you ran synchronously
(`Get-Content .\apify_backfill_out.json`). For a deeper check, query
the most-recent ScrapeRuns row for this source. PS5 strips inner
quotes from native-exe args, so write the AWS query payload to a
file first:

```powershell
$RUNS = (aws cloudformation describe-stacks --stack-name jobs-aggregator `
  --query "Stacks[0].Outputs[?OutputKey=='ScrapeRunsTableName'].OutputValue" `
  --output text)

'{":s":{"S":"apify_linkedin"}}' | Set-Content -Encoding ascii `
  apify_runs_query.json

aws dynamodb query --table-name $RUNS `
  --key-condition-expression "source_name = :s" `
  --expression-attribute-values file://apify_runs_query.json `
  --no-scan-index-forward --limit 1
```

Look at `Item.summary.jobs_found` and `Item.status`. `status: "ok"`
plus a non-zero `jobs_found` confirms the override took effect.

You can also tail the worker logs and watch the override-applied event
fire in real time:
```powershell
aws logs tail /aws/lambda/jobs-aggregator-ScrapeWorkerFn `
  --region us-east-1 --since 10m `
  --filter-pattern "?apify_override ?scrape_run_done"
```

### 20e. Common failure modes

- **`jobs_found: 0` and `errors: ["403 Forbidden"]`** Apify trial
  expired (or token rotated). Check
  `aws ssm get-parameter --name /jobs-aggregator/apify_token --with-decryption`.
- **`status: "failed"` with `actor_run_failed`** the bebity actor
  itself crashed (rare; usually a LinkedIn DOM change). Check the
  Apify console run log. The scraper logs the Apify run-id so you can
  open it directly in their UI.
- **Lambda 15-min timeout** real risk with `r2592000` × 16 searches
  if the actor is slow that day. If it happens, drop `count_per_search`
  to 25 in the override
  (`{"overrides":{"f_TPR_override":"r2592000","count_per_search":25}}`)
  and re-run, OR split with `only_searches` to do half at a time.

---
