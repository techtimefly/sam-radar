# Compliance Matrix

SAM Radar v0.11 adds a local compliance matrix for each opportunity. The matrix is stored in SQLite and is scoped by `noticeId` so evidence citations, revisions, row updates, mark actions, merge/split operations, and exports cannot cross notices.

## Data Model

`compliance_requirements` stores requirement rows with:

- Opportunity scope: `notice_id`
- Optional source links: `citation_id`, `revision_id`
- Matrix fields: `category`, `requirement_text`, `mandatory_state`, `owner`, `due_date`, `response_location`, `status`, `notes`
- Review fields: `verification_state`, `verifier`, `verified_at`
- Generation fields: `provenance`, `generation_key`, `generation_metadata_json`, `human_edited`
- Amendment safety fields: `invalidated`, `invalidation_reason`, `invalidated_at`

`compliance_requirement_lineage` records merge and split lineage with `child_requirement_id`, `parent_requirement_id`, and `relation`.

The migration is additive: tables and indexes are created with `CREATE TABLE IF NOT EXISTS` and existing evidence/revision data is left intact.

## API

Read endpoints do not require `APP_WRITE_TOKEN`:

- `GET /api/compliance/{noticeId}` returns `{ ok, requirements }`.
- `GET /api/compliance-export/{noticeId}.csv` returns a CSV attachment named `{noticeId}-compliance-matrix.csv`.
- `GET /api/compliance-export/{noticeId}.md` returns a Markdown attachment named `{noticeId}-compliance-matrix.md`.

Write endpoints require `APP_WRITE_TOKEN` through `X-SAM-RADAR-TOKEN`:

- `POST /api/compliance/add` creates a requirement. Supports optional `citationId` and `revisionId`; both must belong to the same `noticeId`.
- `POST /api/compliance/update` edits a requirement by `requirementId`. `noticeId` must match the existing row.
- `POST /api/compliance/verify` marks a requirement `verified`, `needs-review`, or `rejected` with an optional `verifier`.
- `POST /api/compliance/reject` marks the row status and verification state as `rejected`.
- `POST /api/compliance/generate` creates deterministic requirements from evidence citations that are not `rejected` or `superseded`.
- `POST /api/compliance/merge` merges at least two `requirementIds` from the same notice into a new row and marks parents as `merged`.
- `POST /api/compliance/split` splits one requirement into at least two child rows and marks the parent as `split`.

Related mark actions:

- `POST /api/evidence/verify` marks evidence citation review state.
- `POST /api/amendments/mark-reviewed` marks amendment changes reviewed for one notice.
- `POST /api/amendments/task/create`, `/update`, and `/delete` maintain amendment review tasks for one notice.

## Generation And Preservation

Generation reads evidence citations for the requested notice in deterministic order: document, page or section, then citation ID. The stable same-notice `generation_key` uses normalized requirement text so duplicate normalized citations deduplicate, while the selected citation remains a deterministic source trace.

On regeneration, existing generated rows with the same key are updated only if they have not been human edited. Human-edited text, owner, notes, verification state, and verified timestamp are preserved.

## Amendment Invalidation

When compliance generation runs, SAM Radar checks stale evidence warnings for the same notice. Only requirements linked to stale citation IDs are marked invalidated. Other rows for the notice remain untouched.

## Export Safety

CSV export uses the standard CSV writer, emits a fixed header order, quotes fields as needed, and prefixes formula-like cell values beginning with `=`, `+`, `-`, `@`, tab, or carriage return.

Markdown export escapes HTML-sensitive characters, pipes, and line breaks in table cells. Exports include source trace fields but do not include `APP_WRITE_TOKEN`, API keys, prompts, or private context.

## Report UI

The generated report renders the compliance matrix in opportunity detail and proposal workspace views. It includes category, status, mandatory, verification, invalidated, and missing-citation filters; inline row editing; verify/reject mark actions; add, generate, merge, split, CSV export, and Markdown export controls.

Summary, Requirements, and Gap assist include compliance matrix rows as source facts. AI prompts/source text are not stored in the local AI audit log.
