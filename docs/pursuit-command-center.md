# Pursuit Command Center

SAM Radar v0.12 adds a primary Pursuit Command Center view to the generated report. It is a deterministic queue over the existing report payload; it does not call an AI model, create new opportunities, or read private configuration.

## Deterministic Semantics

`commandCenter` is built from the enriched report matches after workflow, proposal, evidence, amendment, and compliance context has been attached. Duplicate `noticeId` records are ignored after the first occurrence, and actions sort by risk, action type, due timestamp, notice ID, and action kind.

Risk order is stable:

- `critical`: proposal deadlines exactly within 4 hours or overdue, overdue follow-ups, unread material amendments, and stale evidence warnings.
- `high`: proposal deadlines exactly within 48 hours, unverified evidence, compliance gaps, and high-fit unassigned opportunities.
- `medium`: active assigned pursuits missing a recorded next action.
- `low`: reserved for future non-blocking suggestions.

The current action families are:

- `proposal-deadline`: non-terminal opportunities due in less than or equal to 48 hours. Deadlines at 4 hours are critical; deadlines at 4 hours and 1 second are high. Deadlines at 48 hours are included; deadlines at 48 hours and 1 second are not.
- `follow-up-overdue`: workflow follow-up dates due on or before the configured local day.
- `amendment-review`: unread material amendment changes.
- `stale-evidence`: evidence warnings created after material amendments.
- `evidence-unverified`: evidence citations not marked `verified`.
- `compliance-gap`: compliance rows that are open, blocked, invalidated, or not verified.
- `high-fit-unreviewed`: score `>= 12`, status `new` or `reviewing`, and no owner.
- `assignment-next-action`: active assigned `reviewing`, `pursue`, or `teaming` work with no next action.

`submitted`, `no-bid`, and `archived` are terminal Command Center statuses. Terminal notices are excluded from every action family, Recent Intelligence, and `activeAssignments`.

## Report Payload

`build_report_payload` includes:

```text
commandCenter.metrics
commandCenter.doToday
commandCenter.portfolioHealth
commandCenter.recentIntelligence
```

`Do Today` renders the actionable queue. `Portfolio Health` summarizes critical, high, medium, follow-up, and compliance counts. `Recent Intelligence` lists unread material amendment items.

## Deep Links

Each action includes a target:

```text
{ view, noticeId, surface, anchor }
```

The report uses that target to switch views and open the right notice-scoped surface:

- `proposal-workspace`: opens the Proposals view and scrolls to the proposal workspace.
- `opportunity-detail`: opens the opportunity detail modal.
- `workflow`: opens the detail modal on capture fields.
- `amendments`: opens the detail modal with the amendment panel.
- `evidence`: opens the detail modal with evidence and document intake.
- `compliance`: opens the detail modal with the compliance matrix.

## Quick Actions And Endpoints

All writes require `APP_WRITE_TOKEN` in `X-SAM-RADAR-TOKEN`. The browser Unlock control stores the token in local storage; leaving `APP_WRITE_TOKEN` blank disables writes.

Quick action mapping:

| Action | Endpoint | Payload |
| --- | --- | --- |
| Assign Owner | `POST /api/status/{noticeId}` | Full workflow payload with `owner` filled when empty |
| Review Amendment | `POST /api/amendments/mark-reviewed` | `{ noticeId, changeIds }` for unread material changes |
| Verify Evidence | `POST /api/evidence/verify` | `{ noticeId, evidenceId, state: "verified", verifier }` |
| Advance Stage | `POST /api/proposals/stage` | `{ noticeId, stage }` where `stage` is the next ordered proposal stage after the current stage |
| Open Matrix / Compliance | none | Navigation only; opens the compliance matrix and never verifies, mutates, or removes rows |
| No-Bid | `POST /api/status/{noticeId}` after confirmation | Full workflow payload with `status: "no-bid"`; the no-bid reason is preserved and may remain blank |
| Set Follow-Up | `POST /api/status/{noticeId}` | Full workflow payload with `followUpAt` filled when empty |

After a successful quick action, the report updates the notice-scoped in-memory state and deterministically recomputes `commandCenter` from the current report matches. Stale warnings, open compliance rows, and unverified sibling citations remain until the current response proves they are resolved. Recent Intelligence is cleared only when amendment review returns a reviewed state. Detailed metrics and Portfolio Health are recomputed from the same in-memory matches before repaint.

## Timezone And Date Behavior

Deadline timestamps are parsed as ISO datetimes and normalized to UTC for hour calculations. Datetime strings without a timezone are interpreted in the configured `TIMEZONE`. Date-only strings are interpreted as local midnight in the configured timezone.

Follow-up due checks compare local dates in the configured timezone. This prevents same-day local work from being missed when the timestamp crosses UTC midnight.

Generated report summaries include `generatedLocalDate` and `timezone` so in-browser command-center recomputes compare date-only follow-ups against the same configured local day the server used.

Malformed dates are ignored rather than raising during report generation.

## Privacy And XSS Boundaries

The Command Center uses only opportunity/report fields already present in the local generated report. It does not render `.env`, API keys, `APP_WRITE_TOKEN`, prompts, source text outside the report, or private business config.

Report HTML escapes user and source content before insertion into markup. CSS class tokens are sanitized through safe token helpers before being used in class names. Deep-link targets are serialized as escaped JSON in `data-*` attributes and parsed only by report JavaScript.

Writes are notice-scoped on the server side. Evidence, compliance, amendment, and workflow APIs validate IDs against the notice where applicable and reject missing or invalid write tokens.

## Accessibility And UI Behavior

The Command Center is the first report view. Buttons are keyboard-focusable native controls. Success, loading, and error messages are announced with `role="status"` and `aria-live="polite"`. The No-Bid quick action asks for confirmation before writing.

The layout collapses to a single column below 860px, and quick-action buttons become full width so long labels do not overlap.

## Limitations

The Command Center is a report-local queue, not a background scheduler. It reflects the latest generated report plus in-browser quick-action updates. A fresh report regeneration is still the source of truth for newly discovered amendments, SAM.gov deadlines, stale evidence warnings, and compliance generation.

Quick actions are intentionally conservative defaults. For example, Assign Owner fills `Lead` only when no owner exists, Advance Stage moves one valid ordered stage forward and navigates/no-ops at the terminal `review` stage, No-Bid does not fabricate `deadline-too-short`, and Set Follow-Up chooses tomorrow when no follow-up date exists. More detailed capture planning still belongs in the opportunity detail and proposal workspace surfaces.

The queue does not infer attachment content changes unless the amendment and evidence subsystems already detected them.
