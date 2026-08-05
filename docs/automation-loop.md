# Automation Loop

SAM Radar development can run as a no-human-review loop once a task has a clear GitHub issue or milestone.

## Operating Contract

- Implement each issue on a feature branch.
- Run automated verification before merge: lint, tests, generated report JavaScript syntax, private-context scan, local build/deploy health checks, and feature-specific smoke tests.
- Merge automatically when verification passes.
- Close the associated pull request and issue after the verified merge lands on `main`.
- Continue to the next open issue in the active milestone without waiting for manual review.
- Send Telegram notifications for branch start, verification failure, merge, milestone completion, and final completion.

## Failure Handling

- If a verification gate fails, fix the issue on the same branch and rerun the full relevant gate set.
- If a deployment or environment problem blocks progress, retry safe known remediations first.
- Stop only for true blockers such as missing credentials, unavailable external systems, or actions that would be destructive without explicit approval.

## Final Gate

The final milestone is an automated code and security review. It must verify:

- all planned issues are closed or explicitly resolved,
- all open pull requests are merged or closed with a reason,
- tests and linters pass,
- generated report JavaScript parses,
- the private-context scanner passes,
- local Docker deployment is healthy,
- no secrets or private business context are tracked,
- Telegram completion notification is sent.
