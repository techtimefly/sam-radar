# Amendment Intelligence

SAM Radar v0.10 stores immutable local snapshots of SAM.gov opportunity metadata during refresh and compares consecutive snapshots for material amendment signals.

## Model

The SQLite migration is additive and runs at app startup:

- `opportunity_revisions`: immutable canonical opportunity snapshots keyed by `revision_id` and unique `(notice_id, content_hash)`.
- `attachment_snapshots`: normalized attachment metadata for each revision, keyed by revision and deterministic attachment key.
- `amendment_changes`: detected changes between consecutive revisions with `field`, `machine_type`, `impact`, before/after summaries, detection time, and explanation.
- `amendment_review_tasks`: local review tasks with assignee, status, due date, and notes.

Existing databases migrate safely. Repeating identical normalized input does not create another revision.

## Detection Semantics

SAM Radar normalizes whitespace, case, codes, deadlines, contacts, descriptions, and attachment metadata before hashing and diffing. This avoids false positives from casing, whitespace, and timezone-equivalent deadlines.

Detected fields:

- deadline
- status/cancellation
- notice type
- set-aside
- NAICS
- PSC
- description
- contacts
- attachments added, removed, or changed

Impact values are stable machine-readable strings: `critical`, `high`, `medium`, and `low`. Deadline contractions and cancellations are critical. Set-aside and NAICS changes are high. Deadline extensions, PSC, notice type, description, and attachment metadata changes are medium unless classified higher. Contact-only changes are low.

## API

Read endpoint:

- `GET /api/amendments/{noticeId}` returns summary, revisions, changes, stale-evidence warnings, and tasks.

Write endpoints require `APP_WRITE_TOKEN` in `X-SAM-RADAR-TOKEN`:

- `POST /api/amendments/task/create`
- `POST /api/amendments/task/update`
- `POST /api/amendments/task/delete`

Task payloads include `noticeId`, `revisionId`, optional `changeId`, `assignee`, `status`, `dueDate`, and `notes`. Cross-opportunity revision/change/task references are rejected and SQLite foreign keys are enabled.

## UI

Opportunity cards, detail views, and proposal workspaces show:

- amendment badges for material, unread, and stale-evidence counts
- timeline entries with impact badges and before/after facts
- stale citation warnings
- local task creation controls

Report rendering escapes source/user content and sanitizes CSS class tokens. Task writes use the browser-stored `APP_WRITE_TOKEN`.

## AI Assist

Deterministic and optional AI assist include amendment context as separate source facts and recommended actions. Audit logs continue to store provider, mode, result, and timestamp only; prompts, source text, excerpts, API keys, and private configuration are not persisted in audit logs.

## Privacy

Snapshots store normalized opportunity facts and attachment metadata needed for change detection. SAM.gov API keys, app write tokens, environment values, and raw private config are not stored in revision tables.

## Limitations

SAM Radar detects changes present in refreshed SAM.gov metadata and listed attachment metadata. It does not download every attachment on refresh or perform binary document diffs. If SAM.gov omits historical amendment detail or changes an attachment without changing metadata, the local detector may only report the visible metadata change.
