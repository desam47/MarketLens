# Phase 5.7 Accessibility and Responsive QA

Last verified: 2026-09-23

The direct Chrome/device pass could not be run in this session because
computer-use access was unavailable. The checks below are the executable
accessibility/responsive contract covered by tests and the production build;
the first device-lab run should capture screenshots against the same items.

This checklist is the release evidence for the structured Chat UI. Automated
component coverage runs in `frontend/src/components/ChatPanel.test.tsx`,
`frontend/src/utils/appNavigation.test.ts`, and the page-level tests. The
production build is the responsive CSS compilation gate.

## Keyboard and screen-reader contract

- [x] Regeneration, notebook, feedback, fixture, and route-action controls are native buttons.
- [x] Regeneration scope selectors have explicit labels and keyboard focus.
- [x] Feedback and freshness changes use `role="status"` live status text.
- [x] Typed cards expose named regions; tables use header cells and scopes where applicable.
- [x] Focus-visible outlines are present for Chat actions, notebook controls, and focused record cards.
- [x] Data-quality state is conveyed by text labels, not color alone.

## Responsive contract

- [x] Regeneration scope controls wrap on narrow layouts.
- [x] Notebook creation and notebook action controls stack on narrow layouts.
- [x] Typed tables remain inside scrollable wrappers.
- [x] Long values, missing data, and ragged visual payloads render without throwing.
- [x] Options and System Health deep links render as independent top-level pages.

## Verification commands

```text
DEBUG=false REDIS_ENABLED=false pytest -q
npm test -- --watchAll=false --runInBand
npm run build
```

The test suite intentionally records four environment-dependent skips in the
backend (Redis-backed queue and restricted loopback coverage). A later device
lab can repeat the same checklist at 320px, 768px, and desktop widths; no
release behavior depends on color, hover, or a pointer-only interaction.
