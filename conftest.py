"""Shared pytest fixtures for the jobs-aggregator test suite.

The `aws` fixture is the workhorse: it starts moto's mock_aws context,
creates all four DynamoDB tables + the raw-scrape S3 bucket, sets the
env vars that Lambda code reads at runtime, and resets our module-level
boto3 resource cache so the mock is picked up cleanly.

Tests that touch AWS should just add `aws` to their signature.
"""
import pytest

import boto3
from moto import mock_aws


@pytest.fixture(autouse=True)
def _dummy_aws_env(monkeypatch):
    """Inject obviously-fake credentials so no test can ever reach
    real AWS by accident (belt-and-braces alongside moto)."""
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")


@pytest.fixture
def aws(monkeypatch):
    """Moto context + four DynamoDB tables + one S3 bucket."""
    with mock_aws:
        ddb = boto3.resource("dynamodb", region_name="us-east-1")

        # -- Jobs (mirrors the real schema in template.yaml)
        ddb.create_table(
            TableName="test-jobs",
            BillingMode="PAY_PER_REQUEST",
            KeySchema=[{"AttributeName": "job_id", "KeyType": "HASH"}],
            AttributeDefinitions=[
                {"AttributeName": "job_id", "AttributeType": "S"},
                {"AttributeName": "status", "AttributeType": "S"},
                {"AttributeName": "score_posted", "AttributeType": "S"},
                {"AttributeName": "company_normalized", "AttributeType": "S"},
                {"AttributeName": "posted_at", "AttributeType": "S"},
                {"AttributeName": "source", "AttributeType": "S"},
                {"AttributeName": "track", "AttributeType": "S"},
            ],
            GlobalSecondaryIndexes=[
                {
                    "IndexName": "ScoreIndex",
                    "KeySchema": [
                        {"AttributeName": "status", "KeyType": "HASH"},
                        {"AttributeName": "score_posted", "KeyType": "RANGE"},
                    ],
                    "Projection": {"ProjectionType": "ALL"},
                },
                {
                    "IndexName": "CompanyIndex",
                    "KeySchema": [
                        {"AttributeName": "company_normalized", "KeyType": "HASH"},
                        {"AttributeName": "posted_at", "KeyType": "RANGE"},
                    ],
                    "Projection": {"ProjectionType": "ALL"},
                },
                {
                    "IndexName": "SourceDateIndex",
                    "KeySchema": [
                        {"AttributeName": "source", "KeyType": "HASH"},
                        {"AttributeName": "posted_at", "KeyType": "RANGE"},
                    ],
                    "Projection": {"ProjectionType": "ALL"},
                },
                {
                    "IndexName": "TrackIndex",
                    "KeySchema": [
                        {"AttributeName": "track", "KeyType": "HASH"},
                        {"AttributeName": "score_posted", "KeyType": "RANGE"},
                    ],
                    "Projection": {"ProjectionType": "ALL"},
                },
            ],
        )

        # -- Companies
        ddb.create_table(
            TableName="test-companies",
            BillingMode="PAY_PER_REQUEST",
            KeySchema=[{"AttributeName": "company_name_normalized", "KeyType": "HASH"}],
            AttributeDefinitions=[
                {"AttributeName": "company_name_normalized", "AttributeType": "S"},
                {"AttributeName": "tier", "AttributeType": "S"},
            ],
            GlobalSecondaryIndexes=[{
                "IndexName": "TierIndex",
                "KeySchema": [
                    {"AttributeName": "tier", "KeyType": "HASH"},
                    {"AttributeName": "company_name_normalized", "KeyType": "RANGE"},
                ],
                "Projection": {"ProjectionType": "ALL"},
            }],
        )

        # -- ScrapeRuns
        ddb.create_table(
            TableName="test-scrape-runs",
            BillingMode="PAY_PER_REQUEST",
            KeySchema=[
                {"AttributeName": "source_name", "KeyType": "HASH"},
                {"AttributeName": "run_timestamp", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "source_name", "AttributeType": "S"},
                {"AttributeName": "run_timestamp", "AttributeType": "S"},
            ],
        )

        # -- UserPrefs
        ddb.create_table(
            TableName="test-user-prefs",
            BillingMode="PAY_PER_REQUEST",
            KeySchema=[
                {"AttributeName": "user_id", "KeyType": "HASH"},
                {"AttributeName": "config_key", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "user_id", "AttributeType": "S"},
                {"AttributeName": "config_key", "AttributeType": "S"},
            ],
        )

        # -- S3 bucket for raw-scrape archives
        boto3.client("s3", region_name="us-east-1").create_bucket(
            Bucket="test-raw-scrape"
        )

        # Point Lambda code at the ephemeral resources.
        monkeypatch.setenv("JOBS_TABLE", "test-jobs")
        monkeypatch.setenv("COMPANIES_TABLE", "test-companies")
        monkeypatch.setenv("SCRAPE_RUNS_TABLE", "test-scrape-runs")
        monkeypatch.setenv("USER_PREFS_TABLE", "test-user-prefs")
        monkeypatch.setenv("RAW_SCRAPE_BUCKET", "test-raw-scrape")

        # Reset module-level boto3 caches so they rebind to the mock.
        from common import db as _db
        _db._resource = None
        # clear the browse-scan cache too, otherwise tests in the
        # same session that hit /api/jobs/browse will see stale items from
        # earlier mock_aws contexts (the cache is keyed by status, not by
        # table identity).
        _db.invalidate_browse_cache

        yield
