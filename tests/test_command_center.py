import datetime as dt

from sam_radar.command_center import build_command_center, parse_command_datetime

NOW = dt.datetime(2026, 8, 6, 18, 0, tzinfo=dt.UTC)


def _opp(notice_id: str, **overrides):
    base = {
        "noticeId": notice_id,
        "title": f"Opportunity {notice_id}",
        "organization": "Example Agency",
        "score": 8,
        "recommendation": "Review",
        "workflowStatus": "new",
        "workflowPriority": "normal",
        "responseDeadline": "2026-08-20T17:00:00-04:00",
    }
    base.update(overrides)
    return base


def test_command_datetime_parsing_is_timezone_stable_and_malformed_safe():
    assert parse_command_datetime("2026-08-06T10:30:00-06:00", "America/Denver") == dt.datetime(
        2026, 8, 6, 16, 30, tzinfo=dt.UTC
    )
    assert parse_command_datetime("2026-08-06T10:30:00", "America/Denver") == dt.datetime(
        2026, 8, 6, 16, 30, tzinfo=dt.UTC
    )
    assert parse_command_datetime("2026-08-06", "America/Denver") == dt.datetime(2026, 8, 6, 6, 0, tzinfo=dt.UTC)
    assert parse_command_datetime("not a date", "America/Denver") is None
    assert parse_command_datetime("", "America/Denver") is None


def test_command_center_empty_portfolio_has_stable_empty_state():
    center = build_command_center([], now=NOW, timezone="America/Denver")

    assert center["metrics"]["totalActions"] == 0
    assert center["metrics"]["criticalCount"] == 0
    assert center["metrics"]["activeAssignments"] == 0
    assert center["doToday"] == []
    assert center["portfolioHealth"]["riskLevel"] == "low"
    assert center["portfolioHealth"]["explanation"] == "No active portfolio items are currently in the report."
    assert center["recentIntelligence"] == []


def test_command_center_aggregates_risks_actions_targets_and_tie_ordering():
    center = build_command_center(
        [
            _opp(
                "bravo",
                title="High fit <script>alert(1)</script>",
                score=13,
                recommendation="Pursue",
                workflowStatus="new",
                responseDeadline="2026-08-07T01:00:00Z",
            ),
            _opp(
                "alpha",
                score=9,
                workflowStatus="pursue",
                workflowOwner="Capture",
                workflowFollowUpAt="2026-08-06",
                workflowNextAction="",
                responseDeadline="2026-08-06T19:00:00Z",
                amendmentSummary={"materialChangeCount": 2, "unreadCount": 1, "staleEvidenceCount": 1},
                amendmentTimeline=[{"id": 5, "material": True, "readAt": "", "field": "deadline"}],
                staleEvidenceWarnings={"count": 1, "items": [{"evidenceId": 4, "reason": "Citation predates revision"}]},
                evidenceCitations=[{"id": 4, "verificationState": "needs-review"}],
                complianceRequirements=[
                    {"id": 7, "status": "open", "verificationState": "needs-review", "invalidated": True},
                    {"id": 8, "status": "satisfied", "verificationState": "verified"},
                ],
                proposal={"id": 2, "stage": "requirements", "stageLabel": "Requirements"},
            ),
            _opp(
                "charlie",
                score=9,
                workflowStatus="pursue",
                workflowOwner="Capture",
                workflowFollowUpAt="2026-08-06",
                responseDeadline="2026-08-06T19:00:00Z",
            ),
            _opp("alpha", score=99, workflowStatus="new"),
        ],
        now=NOW,
        timezone="America/Denver",
    )

    assert center["metrics"] == {
        "totalActions": 12,
        "criticalCount": 6,
        "highCount": 4,
        "mediumCount": 2,
        "lowCount": 0,
        "overdueFollowUps": 2,
        "unreadMaterialAmendments": 1,
        "staleEvidence": 1,
        "unverifiedEvidence": 1,
        "complianceGaps": 1,
        "proposalDeadlines": 3,
        "highFitUnreviewed": 1,
        "activeAssignments": 2,
    }
    assert center["portfolioHealth"]["riskLevel"] == "critical"
    assert "6 critical" in center["portfolioHealth"]["explanation"]
    assert [item["noticeId"] for item in center["doToday"][:4]] == ["alpha", "charlie", "alpha", "charlie"]
    assert [item["kind"] for item in center["doToday"][:4]] == [
        "proposal-deadline",
        "proposal-deadline",
        "follow-up-overdue",
        "follow-up-overdue",
    ]
    first = center["doToday"][0]
    assert first["target"] == {"view": "proposals", "noticeId": "alpha", "surface": "proposal-workspace", "anchor": ""}
    assert first["action"] == "Advance proposal stage"
    assert first["reason"] == "Proposal deadline is due in 1 hour."
    assert center["doToday"][4]["target"]["surface"] == "amendments"
    assert center["doToday"][5]["target"]["surface"] == "evidence"
    assert center["doToday"][6]["target"]["surface"] == "opportunity-detail"
    assert center["doToday"][7]["target"]["surface"] == "evidence"
    assert center["doToday"][8]["target"]["surface"] == "compliance"
    assert center["doToday"][9]["target"]["surface"] == "opportunity-detail"
    assert center["doToday"][10]["target"]["surface"] == "workflow"
    assert center["doToday"][11]["target"]["surface"] == "workflow"
    assert [item["noticeId"] for item in center["recentIntelligence"]] == ["alpha"]


def test_command_center_deep_links_skip_missing_ids_and_dedupe_notice_ids():
    center = build_command_center(
        [
            _opp("", workflowFollowUpAt="2026-08-05", workflowStatus="pursue"),
            _opp("valid", workflowFollowUpAt="2026-08-05", workflowStatus="pursue"),
            _opp("valid", workflowFollowUpAt="2026-08-04", workflowStatus="pursue"),
        ],
        now=NOW,
        timezone="America/Denver",
    )

    assert [item["noticeId"] for item in center["doToday"]] == ["valid"]
    assert center["doToday"][0]["target"]["noticeId"] == "valid"


def test_command_center_due_dates_use_configured_local_day():
    center = build_command_center(
        [
            _opp(
                "local-late",
                workflowStatus="pursue",
                workflowOwner="Capture",
                workflowFollowUpAt="2026-08-06T23:30:00-06:00",
            )
        ],
        now=NOW,
        timezone="America/Denver",
    )

    assert [item["kind"] for item in center["doToday"]] == ["follow-up-overdue", "assignment-next-action"]
    assert center["metrics"]["overdueFollowUps"] == 1


def test_command_center_flags_active_assignments_without_proposals():
    center = build_command_center(
        [
            _opp(
                "assigned-no-proposal",
                workflowStatus="pursue",
                workflowOwner="Capture",
                workflowNextAction="",
                responseDeadline="2026-08-20T17:00:00-04:00",
            )
        ],
        now=NOW,
        timezone="America/Denver",
    )

    assert [item["kind"] for item in center["doToday"]] == ["assignment-next-action"]
    assert center["doToday"][0]["action"] == "Set follow-up"
    assert center["metrics"]["mediumCount"] == 1


def test_command_center_terminal_statuses_skip_all_actions_intel_and_assignment_counts():
    for status in ("submitted", "no-bid", "archived"):
        center = build_command_center(
            [
                _opp(
                    f"terminal-{status}",
                    score=15,
                    workflowStatus=status,
                    workflowOwner="Capture",
                    workflowFollowUpAt="2026-08-06",
                    workflowNextAction="",
                    responseDeadline="2026-08-06T19:00:00Z",
                    amendmentSummary={"materialChangeCount": 1, "unreadCount": 1, "staleEvidenceCount": 1},
                    amendmentTimeline=[{"id": 5, "material": True, "readAt": "", "field": "deadline"}],
                    staleEvidenceWarnings={"count": 1, "items": [{"evidenceId": 4, "reason": "Citation predates revision"}]},
                    evidenceCitations=[{"id": 4, "verificationState": "needs-review"}],
                    complianceRequirements=[{"id": 7, "status": "open", "verificationState": "needs-review"}],
                    proposal={"id": 2, "stage": "requirements", "stageLabel": "Requirements"},
                )
            ],
            now=NOW,
            timezone="America/Denver",
        )

        assert center["doToday"] == []
        assert center["recentIntelligence"] == []
        assert center["metrics"]["totalActions"] == 0
        assert center["metrics"]["activeAssignments"] == 0
        assert center["portfolioHealth"]["riskLevel"] == "low"


def test_command_center_deadline_thresholds_use_exact_timedeltas_not_floored_hours():
    center = build_command_center(
        [
            _opp("exact-4h", responseDeadline="2026-08-06T22:00:00Z"),
            _opp("plus-4h-1s", responseDeadline="2026-08-06T22:00:01Z"),
            _opp("exact-48h", responseDeadline="2026-08-08T18:00:00Z"),
            _opp("plus-48h-1s", responseDeadline="2026-08-08T18:00:01Z"),
        ],
        now=NOW,
        timezone="America/Denver",
    )

    deadlines = {item["noticeId"]: item for item in center["doToday"] if item["kind"] == "proposal-deadline"}
    assert deadlines["exact-4h"]["risk"] == "critical"
    assert deadlines["plus-4h-1s"]["risk"] == "high"
    assert deadlines["exact-48h"]["risk"] == "high"
    assert "plus-48h-1s" not in deadlines
    assert deadlines["plus-4h-1s"]["reason"] == "Proposal deadline is due in 4 hours."
