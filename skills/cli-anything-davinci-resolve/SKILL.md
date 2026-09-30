---
name: "cli-anything-davinci-resolve"
description: "Control a real local DaVinci Resolve Studio session for project, media, timeline, Fusion title, caption, render, and preview work."
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
  exchange timelines. `timeline title` is a ripple insert on V1 at the playhead
  (Resolve's behaviour): it moves everything after it. Use `fusion place` instead.
- `fusion carrier|export-template|place|tools|inputs|set|export|import`: put a
  Fusion title on an exact track, frame and length without moving anything, and
  set its text. `place` refuses V1 and occupied ranges unless told otherwise.
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

Fusion title workflow:

```bash
davinci --json fusion export-template "Draw On 2 Lines Lower Third" C:\titles\lower.comp
davinci --json fusion place C:\titles\lower.comp --track 3 --at 01:00:20:00 --seconds 7 \
  --text "mainText=Matty Herrera" --text "secondaryText=Host, Seeing the Gap"
davinci --json fusion tools V3@01:00:21:00
davinci --json fusion set V3@01:00:21:00 --text "mainText=New name"
```

Text goes on named inner Text+ tools (`Tool=text` sets `StyledText`). Published
macro inputs ignore scripts. `--set Tool.Input=value` takes JSON values, so
`--set 'Transform1.Center={"1":0.5,"2":0.9}'` sets a point.

Preview producer/consumer example:

```bash
davinci --json preview capture --recipe current-frame
cli-hub previews inspect <bundle-dir>
```
