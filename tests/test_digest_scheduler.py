import datetime as dt
from zoneinfo import ZoneInfo

from sam_radar.config import BusinessProfile, Settings
from sam_radar.digest import build_digest
from sam_radar.scheduler import next_run, parse_daily_cron


def test_digest_uses_app_base_url_and_normalized_time():
    profile = BusinessProfile(name="Example LLC", dba="Example")
    settings = Settings(sam_gov_api_key="test", app_base_url="https://sam-radar.example.test", timezone="America/Denver")
    opp = {
        "title": "Security Support",
        "organization": "Example Agency",
        "postedDate": "2026-08-04",
        "responseDeadline": "2026-08-10T09:00:00-04:00",
        "score": 10,
        "recommendation": "Pursue",
        "reasons": ["keywords: security"],
        "uiLink": "https://sam.gov/example",
    }
    digest = build_digest([opp], profile, settings, "08/01/2026", "08/04/2026", settings.latest_report_url)
    assert "https://sam-radar.example.test/reports/latest.html" in digest
    assert "MDT" in digest
    assert "Security Support" in digest


def test_daily_cron_next_run_rolls_to_tomorrow_when_elapsed():
    assert parse_daily_cron("0 6 * * *") == (6, 0)
    now = dt.datetime(2026, 8, 4, 7, 0, tzinfo=ZoneInfo("America/Denver"))
    run_at = next_run(now, "0 6 * * *")
    assert run_at.date() == dt.date(2026, 8, 5)
    assert run_at.hour == 6
