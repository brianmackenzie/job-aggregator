"""Tests for src/scrapers/game_jobs_uk.py — Tailwind card parser."""
from scrapers.game_jobs_uk import GameJobsUKScraper


_CARD = """
<a class="job-card new-job" data-job-id="2579" href="/jobs?job_id=2579">
  <div class="new-job-ribbon">NEW</div>
  <span class="industry">Programming</span>
  <span class="bg-green-900/30">Hybrid</span>
  <span class="bg-sky-950/50">Full-Time</span>
  <h3 class="text-xs">Creative Assembly</h3>
  <h4 class="text-lg">Senior Build Engineer</h4>
  <span>Horsham - United Kingdom</span>
  <span class="local-date">17.04.2026</span>
  <span>View Job →</span>
</a>
"""

_CARD_REMOTE = """
<a class="job-card" data-job-id="2580" href="/jobs?job_id=2580">
  <span class="industry">Production</span>
  <span class="bg-green-900/30">Remote</span>
  <span class="bg-sky-950/50">Full-Time</span>
  <h3>Acme Studios</h3>
  <h4>VP, Engineering</h4>
  <span>Worldwide - Remote</span>
  <span class="local-date">15.04.2026</span>
</a>
"""

_CARD_ONSITE = """
<a class="job-card" data-job-id="2581" href="/jobs?job_id=2581">
  <span class="industry">Art</span>
  <span class="bg-green-900/30">Onsite</span>
  <h3>Studio X</h3>
  <h4>3D Artist</h4>
  <span>London - United Kingdom</span>
  <span class="local-date">10.04.2026</span>
</a>
"""


def _payload(html: str = _CARD, job_id: str = "2579"):
    return {
        "_job_id": job_id,
        "_href":   f"/jobs?job_id={job_id}",
        "_html":   html,
    }


def test_parse_canonical_card:
    s   = GameJobsUKScraper
    raw = s.parse(_payload)
    assert raw is not None
    assert raw.native_id == "2579"
    assert raw.title     == "Senior Build Engineer"
    assert raw.company   == "Creative Assembly"
    assert raw.location  == "Horsham - United Kingdom"
    assert raw.posted_at == "2026-04-17T00:00:00Z"
    assert raw.url       == "https://www.gamejobsuk.com/jobs?job_id=2579"
    # Hybrid pill → remote stays None (we don't infer either way for hybrid).
    assert raw.remote is None


def test_parse_remote_pill_marks_remote_true:
    s   = GameJobsUKScraper
    raw = s.parse(_payload(html=_CARD_REMOTE, job_id="2580"))
    assert raw is not None
    assert raw.remote is True


def test_parse_onsite_pill_marks_remote_false:
    s   = GameJobsUKScraper
    raw = s.parse(_payload(html=_CARD_ONSITE, job_id="2581"))
    assert raw is not None
    assert raw.remote is False


def test_parse_skips_when_no_company:
    s = GameJobsUKScraper
    no_co = '<a class="job-card" data-job-id="9"><h4>Director</h4></a>'
    assert s.parse(_payload(html=no_co, job_id="9")) is None


def test_parse_skips_when_no_title:
    s = GameJobsUKScraper
    no_title = '<a class="job-card" data-job-id="9"><h3>Acme</h3></a>'
    assert s.parse(_payload(html=no_title, job_id="9")) is None


def test_parse_handles_missing_date:
    s = GameJobsUKScraper
    no_date = '<a class="job-card" data-job-id="9"><h3>Acme</h3><h4>Director</h4></a>'
    raw = s.parse(_payload(html=no_date, job_id="9"))
    assert raw is not None
    assert raw.posted_at is None


def test_parse_skips_when_no_job_id:
    s = GameJobsUKScraper
    p = _payload
    p["_job_id"] = ""
    assert s.parse(p) is None
