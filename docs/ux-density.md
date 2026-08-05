# Pipeline UX Density Pass

SAM Radar's pipeline UI is designed as an operator cockpit for capture work, not a landing page or decorative dashboard. The interface should prioritize scan density, fast triage, and wide-screen use while keeping mobile layouts usable.

## Principles

- Use available desktop width; avoid narrow centered report layouts for pipeline views.
- Group toolbar controls by intent: view switches, filters, search, and app actions.
- Treat the board as a primary work surface with all statuses reachable on wide screens and horizontal overflow when needed.
- Keep opportunity cards compact, with status, urgency, score, owner, and dates easy to scan.
- Make the detail modal a capture workspace: decision fields first, roomy text fields, document review, and timeline in clear regions.
- Typography should support repeated operational use: readable body text, compact metadata, strong labels, and no negative letter spacing.

## Responsive QA Targets

Check these widths before closing UX changes: 390px, 768px, 1440px, and 1728px. At each size, verify the toolbar wraps cleanly, board lanes remain usable, modal fields are reachable, and text does not overlap controls.
