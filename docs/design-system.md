# SAM Radar Design System

SAM Radar uses a small built-in design system inside the generated HTML report. It is intentionally framework-free so `reports/latest.html` remains portable and self-contained.

## Tokens

Global CSS custom properties live in `app/sam_radar/reports.py` under `build_html_report`.

- Typography: `--font-sans`, `--text-xs`, `--text-sm`, `--text-md`, `--text-lg`, `--text-xl`, `--text-title`
- Spacing: `--space-1` through `--space-5`
- Radius: `--radius-control`, `--radius-card`, `--radius-pill`
- Shadows: `--shadow-card`, `--shadow-dialog`
- Surfaces: `--surface-page`, `--surface-card`, `--surface-subtle`, `--surface-raised`
- Status colors: `--status-success`, `--status-warning`, `--status-danger`, `--status-info`
- Density: `--density-card-padding`, `--density-control-padding`, `--density-gap`

Theme-specific values are set with `[data-theme=dark]`. Component CSS should reference semantic tokens before raw colors.

## Components

Reusable primitives are CSS classes applied across existing generated report markup:

- Buttons: `.btn-primary`, `.btn-secondary`, `.btn-danger`, `.btn-ghost`, plus existing `.refresh`, `.sam`, `.save-detail`, `.save-status`
- Forms: `.form-control`, shared `input`, `select`, and `textarea` rules
- Cards: `.card`, `.panel`, `.opp`, `.manual-card`, `.resource-card`, `.modal-card`
- Tables: `.table-wrap`
- Dialogs and confirmations: `.modal-card`, `.dialog-confirm`, `.confirmable`
- Badges: `.badge`, `.pill`, and status/urgency variants
- Feedback: `.state-message`, `.state-loading`, `.state-success`, `.state-error`, `.state-empty`

The Resources view includes a "Design System Showcase" section demonstrating tokens, buttons, forms, cards, tables, dialogs, badges, and feedback states.

## Accessibility

- Interactive controls use `:focus-visible` outlines and a tokenized focus ring.
- Live feedback should use `setStatusState(element, state, message)`, which assigns `role="status"` and `aria-live="polite"`.
- Status colors are semantic and paired with labels or icons; do not rely on color alone.
- Destructive actions should use `.confirmable` when a confirmation step is practical in the static report.
- Motion-sensitive users are respected with `@media(prefers-reduced-motion:reduce)`.

## Responsive Density

Desktop layouts can use multi-column grids for scanning and comparison. At narrower widths, components collapse to one column and reduce card/control padding through density tokens.

Prefer these conventions:

- Keep cards at `--radius-card` and controls at `--radius-control`.
- Use `minmax(0,1fr)` or explicit min widths for dense grids.
- Keep tables inside `.table-wrap`.
- Let buttons wrap text on mobile instead of overflowing.
- Use semantic density tokens instead of one-off padding changes.
