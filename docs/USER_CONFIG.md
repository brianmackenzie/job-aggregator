# Adopting the Jobs Aggregator for Yourself

This tool was built by one person for their own job search. If you'd like to
run it for your own search, you'll need to replace the original author's
preferences with yours by editing five YAML files under `config/`.

This guide walks you file-by-file through what to change, what to leave
alone, and how to deploy your edits.

> **You do not need to edit any Python code** to adopt this tool. Every
> tunable value — scoring weights, target companies, search keywords,
> industries you care about, your geography — lives in these YAML files.

## One-time setup

Before editing config, make sure the infrastructure is deployed for your
own AWS account. See [`README.md`](../README.md) for the AWS account +
SSM parameter setup, then run:

```bash
sam build
sam deploy --guided    # first deploy only; creates the stack
```

After the initial deploy, the iteration loop is:

```bash
# 1. Edit any file under config/
# 2. Redeploy (the ConfigLayer ships your edits to Lambda):
sam build
sam deploy
# 3. If you changed candidate_profile.yaml or scoring.yaml:
aws lambda invoke \
  --function-name <RescoreFn name> \
  --cli-binary-format raw-in-base64-out \
  --payload '{"force_semantic": true}' \
  out.json --region us-east-1
```

---

## The five config files

Each file is annotated in-place with `[USER]`, `[SHARED]`, and `[MIXED]`
tags at the top of each section. Use the guide below alongside those
inline annotations.

### 1. candidate_profile.yaml (100% user)

This is the single most important file. The entire contents get fed
verbatim to Claude Haiku as its system prompt. Haiku then reads each
scraped job and decides how well it fits you.

**What to change:**
- The `system_prompt` block (all ~400 lines). Every paragraph
  describes the original author (background, geography, must-haves, must-avoids,
  comp expectations, career stage). Rewrite for yourself.
- The `calibration_anchors` list at the bottom. These are the original author's
  hand-graded examples used to anchor Haiku's scoring distribution.
  Replace with 5-10 of your own "this is a 95 fit for me / this
  is a 10 fit for me" examples.

**What to preserve structurally:**
- The `system_prompt: |-` key and its YAML block-scalar marker.
- The `### OUTPUT FORMAT` section at the end of the system prompt —
  Haiku's JSON response schema is parsed by the downstream code.
- The `IMPORTANT — USE THE FULL 0-100 RANGE` calibration instruction.
  Removing it causes Haiku to compress every score into the 50-70
  band, killing the scoring signal. (This was a real bug in
  ; `test_deployed_config.py` guards it now.)

**Deploy + refresh after editing:**
```bash
sam build && sam deploy
# Force re-call Haiku for every job (costs API credits):
aws lambda invoke --function-name <RescoreFn> \
  --payload '{"force_semantic": true}' out.json --region us-east-1
```

### 2. scoring.yaml (mixed — the big one)

This file controls the **algorithmic** score — the deterministic
0-100 computed before Haiku ever sees the job. Haiku's score is
then blended with this algo score (per `semantic.blend_weight_*`).

**Sections to personalize:**
- `weights:` — how much each scoring dimension matters to you
  (role_fit / industry_alignment / geographic / compensation /
  work_life_quality / etc.). Must sum to 1.0.
- `keywords:` — almost every keyword list is a personal target
  vocabulary (strategy, program_architecture, senior_titles, ...).
  A new user will want to rewrite most of these.
- `industry_buckets`, `company_industry_map`, `industry_keywords` —
  which industries you WANT, scored 0-10. Rebuild for your
  target industries.
- `location:` — preset labels (`nyc_hybrid_2d`, `nj_office`) are
  the original author-specific. Rename the presets to match YOUR home base
  and update `location.keywords.commutable`.
- `gates:` — some are shared defaults (e.g. intern-filter). Others
  (`clearance_required`, `relocation_required`, `crunch_required`)
  reflect the original author's personal deal-breakers.
- `modifiers:` — tier bumps + domain-passion bonuses. Edit the
  `passion_*` ones to reflect what YOU are excited about.
- `tracks.TRACK_3_PIVOT.industries:` — industries you'd pivot INTO
  if a great role surfaced. Set to your own pivot ambitions, or
  delete TRACK_3_PIVOT entirely if you aren't exploring pivots.
- `static_lists.crunch_companies`, `static_lists.crunch_reduced`,
  `static_lists.nonlocal_cities` — personal red-flag lists.

**Sections that are safe to leave alone:**
- `static_lists.hrc100_companies` — public HRC Corporate Equality
  Index roster. Objective, not the original author's opinion.
- `semantic:` — Haiku blend weights, cache TTLs, rate limits. Good
  defaults for any user.
- `tiers:` — 0-100 score cutoffs for T1/T2/T3/T4 tier labels.

### 3. companies.yaml (100% user)

Every entry is a specific company the original author wants to target, with his
own tier assignment (S = dream, 1 = primary, 2 = secondary).

**What to do:**
1. Delete every existing entry — they're the original author's targets.
2. Add your own companies, one block per target, each with:
   - `name` — display name (e.g. "Stripe")
   - `name_normalized` — lowercase, suffix-stripped, must match
     what appears in scraper data (e.g. `"stripe"`)
   - `tier` — `S`, `1`, or `2`
   - `ats` — one of `greenhouse`, `lever`, `ashby`, `smartrecruiters`,
     `workday`, or `null` (for ATSes the scrapers don't support)
   - `ats_slug` — the ATS-specific identifier. Look this up by
     opening the company's careers page; the URL path reveals it
     (e.g. `boards.greenhouse.io/stripe` → `ats_slug: stripe`).

After editing + deploying, **seed the Companies DynamoDB table**:
```bash
python scripts/seed_companies.py
```

### 4. sources.yaml (mixed)

The scraper PLUMBING (which sources are enabled, how often, how many
results per call) is shared infrastructure. The SEARCH CONTENT and
the operator contact email are personal.

**What to change:**
- `scraper_defaults.contact_email:` — your email address. Gets embedded
  into the User-Agent string every scraper sends to every target site.
  Set to a reachable address so a site admin rate-limiting you can
  email rather than silently block. See the "Contact email" section
  below for details.
- `apify_linkedin.searches:` — every entry is a LinkedIn search
  URL with the original author's keywords + geoIds. Rewrite every entry with
  your own keyword Boolean queries and locations. The comments
  above the list explain LinkedIn's URL-filter cheat codes.
- Any other `searches:` or `keywords:` list inside a source block.

**What to leave alone:**
- Scraper `enabled:` flags (unless you know why a source was
  disabled — usually it's anti-bot blocking, not preference).
- `actor_id`, `ssm_token_param`, poll timings — these are
  infrastructure defaults.
- `scraper_defaults.user_agent_template:` — just the `{email}`
  placeholder substitution wrapper. Leave this alone unless you have
  a specific reason to re-brand the UA string (e.g. branding a fork).

#### Contact email — why it matters (sources.yaml)

Every scraper in `src/scrapers/*.py` and every one-off probe script
in `scripts/` sends an HTTP `User-Agent` header built from the value
of `scraper_defaults.contact_email`. The default header looks like:

```
User-Agent: jobs-aggregator/1.0 (personal use; your@email.com)
```

This is read ONCE per Lambda cold-start (and once per script run) by
`src/scrapers/user_agent.py`, which imports it from the same
`/opt/sources.yaml` the ConfigLayer ships. You do **not** need to
touch any Python code to change it — edit `config/sources.yaml`,
`sam build && sam deploy`, done.

Rationale: some target sites (LinkedIn, Workday tenants, a few ATS
providers) will 403 or 429 requests with no UA or an anonymous UA.
A real reachable email is both etiquette — "here's who you can
email if you want me to back off" — and a pragmatic unblocker.

### 5. taxonomy.yaml (mixed)

Classifies every job along three axes for the frontend filter UI:
industry, role_type, company_group.

**What to change:**
- `company_groups:` — the original author's personal groupings
  (`gaming_aaa`, `streaming_media`, `dream_culture`, etc.).
  Delete or rewrite to match YOUR affinity buckets.
- `qol:` — quality-of-life weights. `salary_floor` is the bottom of
  the salary band you consider acceptable — raise or lower to match
  your own comp range. `work_mode_remote: 25` weighs remote over
  hybrid 15:25 — flip if you actually want to be in an office.

**What to leave alone:**
- `industries:` — the LABELS and keyword matchers are reusable
  across users. You can add/remove industries later, but the
  defaults (gaming/tech/media/etc.) are general-purpose.
- `role_types:` — function-discipline classification, broadly
  agnostic. Edit if you add a new role type not in the list.

---

## Debugging a wrong score

1. Check the algorithm-side breakdown first. Open the job in the
   frontend and look at `score_breakdown`. Each category score shows
   which keywords fired — helpful for tracing "why did this score
   high/low?".
2. Check Haiku's rationale. In the same breakdown view, look for
   `semantic_rationale` — the 1-2 sentence explanation Haiku gave
   for its score.
3. If the algo disagrees with Haiku, the blend at
   `semantic.blend_weight_semantic` (default 0.6) tilts toward
   Haiku. Lower the semantic weight to trust the algo more.

---

## Safety: the ConfigLayer and the `src/config/` trap

Historically (before ), `config/` and `src/config/` were
both present, with `src/config/` being what actually shipped to
Lambda. They silently diverged — the original author's edits in `config/` sat
unused while the deployed copy in `src/config/` went stale.

The current design:
- The repo-root `config/` is the **only** copy.
- It ships to Lambda as `/opt/*.yaml` via the `ConfigLayer`
  declared in `template.yaml`.
- Lambda's loaders (`src/scoring/semantic.py`, `keywords.py`,
  `taxonomy.py`, `src/scrapers/ats_companies.py`,
  `sources_config.py`) read `/opt/<name>.yaml` first, then fall
  back to `<repo-root>/config/<name>.yaml` for local dev + pytest.
- `test_deployed_config.py` guards against re-introducing the
  `src/config/` duplicate — the test fails if that directory ever
  comes back.

**Do not re-create `src/config/`.** If you see a RUNBOOK section or
comment telling you to `cp config/foo.yaml src/config/foo.yaml`,
it's stale — ignore it.
