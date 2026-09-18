# DaVinci Resolve harness test plan

## Unit tests

- Default Windows path discovery and explicit configuration override.
- Locked config persistence.
- CLI help and JSON path configuration through the installed command.
- JSON `doctor` failure remains structured when no desktop session is present.
- Dry-run mutations do not call the real mutation callback.
- Timeline summary preserves integer frame boundaries and exact timecodes.

## Real-backend tests

Marked `real_backend`; skipped unless `CLI_ANYTHING_DAVINCI_E2E=1`.

- Installed `cli-anything-davinci-resolve` command resolves from PATH.
- `doctor --json` connects to the installed Resolve Studio application.
- `auth status`, `project list`, `project current`, `timeline list`, and
  `render capabilities` return real data.
- `preview capture` exports a real current frame and verifies every manifest
  artifact exists. The test skips only when the current project has no visible
  timeline frame.

The E2E suite is non-destructive. It never creates, imports, saves, renders, or
deletes production data.
