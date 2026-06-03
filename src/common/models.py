"""Dataclasses for the domain.

Used by the scraper pipeline and tests as typed intermediaries. The
persistence layer (`db.py`) accepts and returns plain dicts at the
DynamoDB boundary — keeping Lambdas simple and JSON-serializable.
`to_dict` on each class drops None fields so DynamoDB doesn't
store meaningless empty values.
"""
from dataclasses import asdict, dataclass, field
from typing import Optional


@dataclass
class Job:
    """One row in the Jobs table."""
    job_id: str
    title: str
    company: str
    company_normalized: str
    source: str
    native_id: str
    url: str
    posted_at: str
    scraped_at: str
    status: str = "active"            # active | archived | hidden | applied
    track: str = "unscored"           # unscored | gaming | media | igaming | analyst | other
    score: int = 0
    score_posted: str = ""            # "0087#2026-04-16T..."
    location: Optional[str] = None
    remote: Optional[bool] = None
    description: Optional[str] = None
    salary_min: Optional[int] = None
    salary_max: Optional[int] = None
    ats_type: Optional[str] = None
    # renamed from hard_gates_hit. The scoring layer's
    # internal key is gates_triggered; the DDB column used to be
    # hard_gates_hit, causing the export script to read an always-empty
    # field. One-shot migration (scripts/migrate_gates_column.py) copies
    # the old column forward for existing rows.
    gates_triggered: list = field(default_factory=list)
    modifiers_applied: list = field(default_factory=list)
    score_breakdown: dict = field(default_factory=dict)
    tags: list = field(default_factory=list)
    user_notes: Optional[str] = None

    def to_dict(self) -> dict:
        # Drop Nones so DynamoDB doesn't get empty attributes.
        return {k: v for k, v in asdict(self).items if v is not None}


@dataclass
class Company:
    """One row in the Companies table. Seeded from config/companies.yaml."""
    company_name_normalized: str
    company_name: str
    tier: str                          # S | A | B | ...
    track: Optional[str] = None
    ats_type: Optional[str] = None
    ats_slug: Optional[str] = None
    careers_url: Optional[str] = None
    notes: Optional[str] = None
    last_scraped: Optional[str] = None
    active: bool = True

    def to_dict(self) -> dict:
        return {k: v for k, v in asdict(self).items if v is not None}


@dataclass
class ScrapeRun:
    """One row in the ScrapeRuns table."""
    source_name: str
    run_timestamp: str                 # ISO8601 UTC
    status: str                        # ok | partial | error
    jobs_found: int = 0
    jobs_new: int = 0
    jobs_updated: int = 0
    duration_ms: int = 0
    error_message: Optional[str] = None
    raw_s3_key: Optional[str] = None
    expires_at: int = 0                # unix epoch — TTL attribute

    def to_dict(self) -> dict:
        return {k: v for k, v in asdict(self).items if v is not None}
