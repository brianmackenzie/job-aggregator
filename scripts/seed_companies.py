"""seed_companies.py — populate the Companies DynamoDB table.

Reads config/companies.yaml and UPSERTs each company into the Companies table.
Safe to re-run (idempotent — DynamoDB put_item overwrites on matching PK).

Usage (from the repo root):
    python scripts/seed_companies.py

Prerequisites:
    - AWS credentials configured (deploy-jobs IAM user or equivalent)
    - Stack deployed (COMPANIES_TABLE env var not required — script queries
      CloudFormation for the table name automatically)
    - pyyaml installed: pip install pyyaml

What this writes to DynamoDB:
    PK:  company_name_normalized  (e.g. "riot games")
    SK:  (none — simple PK table)
    GSI: TierIndex on `tier` field (e.g. "S", "1", "2")
    Fields: name, tier, ats, ats_slug, industry, hq, notes
"""
import os
import sys
from pathlib import Path

import boto3
import yaml

# ---------------------------------------------------------------------------
# Resolve paths — script can be run from any directory.
# ---------------------------------------------------------------------------
REPO_ROOT    = Path(__file__).resolve.parent.parent
COMPANIES_YML = REPO_ROOT / "config" / "companies.yaml"
STACK_NAME   = "jobs-aggregator"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def get_table_name(cf_client) -> str:
    """Look up the Companies table name from CloudFormation Outputs.
    Falls back to COMPANIES_TABLE env var if set (useful in CI)."""
    env_name = os.environ.get("COMPANIES_TABLE")
    if env_name:
        return env_name

    resp = cf_client.describe_stacks(StackName=STACK_NAME)
    outputs = resp["Stacks"][0].get("Outputs", )
    for out in outputs:
        if out["OutputKey"] == "CompaniesTableName":
            return out["OutputValue"]
    raise RuntimeError(
        f"CompaniesTableName not found in {STACK_NAME} CloudFormation outputs. "
        "Is the stack deployed?"
    )


def load_companies -> list[dict]:
    """Parse config/companies.yaml and return the companies list."""
    if not COMPANIES_YML.exists:
        raise FileNotFoundError(f"Not found: {COMPANIES_YML}")
    with open(COMPANIES_YML, encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    companies = data.get("companies", )
    if not companies:
        raise ValueError("companies.yaml has an empty companies list")
    return companies


def build_row(company: dict) -> dict:
    """Convert a companies.yaml entry into a DynamoDB row dict.

    Required field: name_normalized (PK).
    All other fields are optional — we include only non-null values to
    keep rows clean (DynamoDB doesn't like empty strings).
    """
    row: dict = {}
    name_norm = company.get("name_normalized", "").strip.lower
    if not name_norm:
        raise ValueError(f"Missing name_normalized in entry: {company}")

    row["company_name_normalized"] = name_norm

    # Copy scalar fields if present and non-null.
    for field in ("name", "tier", "ats", "ats_slug", "industry", "hq", "notes"):
        val = company.get(field)
        if val is not None:
            row[field] = val

    return row


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main:
    print(f"Loading companies from {COMPANIES_YML} ...")
    companies = load_companies
    print(f"  Found {len(companies)} companies.")

    # Resolve the live DynamoDB table name from CloudFormation.
    cf  = boto3.client("cloudformation", region_name="us-east-1")
    ddb = boto3.resource("dynamodb",    region_name="us-east-1")
    table_name = get_table_name(cf)
    table      = ddb.Table(table_name)
    print(f"  Target table: {table_name}")

    # UPSERT each company. Count successes and failures.
    # NOTE: We use ASCII "OK" / "FAIL" markers (not ✓ / ✗) because the default
    # Windows console codepage is cp1252 and can't encode Unicode checkmarks,
    # which caused upserts to be miscounted as failures when the print line
    # crashed after a successful put_item.
    ok = 0
    failed = 
    for company in companies:
        row = None
        try:
            row = build_row(company)
            table.put_item(Item=row)
        except Exception as exc:
            name = company.get("name", str(company))
            failed.append(name)
            print(f"  FAIL {name}: {exc}", file=sys.stderr)
            continue
        ok += 1
        print(f"  OK   {row['company_name_normalized']}  ({row.get('tier', '?')})")

    print(f"\nDone. {ok} upserted, {len(failed)} failed.")
    if failed:
        print("Failed companies:", failed, file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main
