---
name: "cli-anything-davinci-resolve"
description: "Control a real local DaVinci Resolve Studio session for project, media, timeline, caption, render, and preview work."
---

# DaVinci Resolve Studio CLI

Use `cli-anything-davinci-resolve` or the `davinci` alias. Prefer `--json` for
agent work and `--dry-run` before a new mutating workflow.

## Connection contract

The CLI wraps Resolve's installed local scripting bridge. It does not need a
cloud API key. Resolve must be running and past startup/sign-in. Begin with:

```bash
cli-anything-davinci-resolve --json doctor
cli-anything-davinci-resolve --json auth status
```

## Command groups

- `configure paths`: persist the Resolve executable, API package, and native
  bridge paths.
- `auth status|connect`: verify the active desktop session.
- `project list|current|open|create|save|export`: project operations.
- `media list|import`: inspect or import media.
- `timeline list|current|create|append|title|captions|export`: assemble and
  exchange timelines.
- `render capabilities|jobs|add|start|status`: use Resolve's native render queue.
- `preview recipes|capture|latest`: publish truthful preview bundles.

There are intentionally no destructive delete commands in v1.

## Agent workflow

1. Run `doctor --json`; require `ok: true`.
2. Inspect the current project and timeline before mutation.
3. Run the intended mutation with `--dry-run --json`.
4. Run it without `--dry-run` only when the task authorizes that change.
5. Verify the resulting project/timeline/render state with a read command.
6. Use `preview capture` for a native-frame checkpoint.

Preview producer/consumer example:

```bash
davinci --json preview capture --recipe current-frame
cli-hub previews inspect <bundle-dir>
```
