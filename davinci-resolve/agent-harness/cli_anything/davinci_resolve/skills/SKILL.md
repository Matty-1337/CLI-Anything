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
- `timeline retime get|set --track N --item I` (Resolve 21.2): read or set one
  item's clip speed (`--speed` percent, 25 is quarter speed, 0 freezes), retime
  process (`--process project|nearest|frame-blend|optical-flow`) and motion
  estimation (`--motion ...|speed-warp-faster|speed-warp-better`). `--item` is a
  1-based index, `id:<unique id>` or `@<record frame>`. All three are read back;
  a mismatch exits 1 and reports the state before. Speed Warp is refused unless
  the process is `optical-flow`. Ripple is off unless `--ripple` (a rippled speed
  change has been reported to unlink tracks). Example, VOI slow motion:
  `timeline retime set --track 1 --item @108450 --speed 25 --process optical-flow --motion speed-warp-better`.
- `timeline create NAME [--width W --height H] [--fps 30]`: an empty timeline with its
  own size and frame rate (custom timeline settings), each read back; a value that
  does not read back removes the new timeline again. `--fps` is refused with
  `--clip` (Resolve fixes the frame rate once a timeline has clips).
- `timeline marker list|add|import|check`: timeline markers, framed from the
  timeline start (0 is its first frame; `list` also gives the record frame).
  `add --frame N --name X [--color Blue] [--duration N] [--note] [--custom-data]`
  reads the marker back and removes it again if it does not match; one marker per
  frame. `import CUE_SHEET [--beats] [--replace]` places one marker per cue of a
  Motion Studio cue sheet (`motion cues`), refuses a sheet whose fps differs from
  the timeline, and takes its own markers back out if any fails. `check CUE_SHEET`
  (read only) exits 1 unless every cue has its marker.
- `effects list [--category fusion|ofx|audio] [--match text]` (Resolve 21.1.2+):
  every clip effect Resolve can apply, with the `id` that `effect add` takes.
- `transition add ITEM_REF --type "Cross Dissolve" --category simple|fusion|ofx
  --position start|end --alignment left|center|right --frames N` (21.1.2+): a real
  Edit page transition on one edge of a clip. It re-queries the track and reads
  start, end and duration back from Resolve, never from the request; a wrong
  duration (a 1 frame transition has been reported) is corrected once through
  `TransitionDuration`; every clip on every video track must keep its start and
  end. If a check still fails the transition is removed again and the command
  exits 1 with what it saw. The clip needs handles to cover the duration.
- `effect add ITEM_REF --category fusion|ofx --id <id> [--set Input=value]`
  (21.1.2+): apply a clip effect, read it back from `GetEffects`, and set inputs
  through the effect's Fusion comp (`Input=value` for its one tool, or
  `Tool.Input=value`), each read back. `--set` is refused on OFX effects: Resolve
  has no parameter API for them (use `fusion add-tool` inside a comp instead).
  Any failed check removes the effect again. `transition add` and `effect add`
  refuse V1 without `--allow-v1`. `ITEM_REF` is a clip's unique id or
  `V<track>@<frame|timecode>`; transitions are never picked as the clip.
- `fusion carrier|export-template|place|tools|inputs|set|export|import`: put a
  Fusion title on an exact track, frame and length without moving anything, and
  set its text. `place` refuses V1 and occupied ranges unless told otherwise.
- `export thumbnails|frame` (Resolve 21.2, read only): a labelled contact sheet
  of timeline frames, or one frame or a normalized region of it (`--roi X Y W H`),
  at a capped size. `--at` takes a record frame, a timecode or `+N` (an offset
  from the timeline start); offsets outside the timeline are refused before
  Resolve is called, because one bad offset fails a whole 21.2 sheet.
- `grade cdl` (Resolve 21.2, read only): every Color page node's CDL and enabled
  state per item on a track, and which items carry a grade. `--fail-on-grade`
  exits 1 when any enabled node is not neutral. Nothing here sets a grade.
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

Add an effect inside a comp (any Fusion tool, Fuse or OFX plug-in, Continuum included):

```bash
davinci --json fusion add-tool V2@01:00:00:00 ofx.com.borisfx.bcc_dft.ofx.filmGlow --name Glow --before MediaOut1 --set Blend=0.6
```

It refuses V1 without `--allow-v1`, reads back the wiring, and undoes itself on any failure.
Wire before the MediaOut the item renders (Map Creator Pro: `MediaOut1_1`).

Build check workflow (Resolve 21.2):

```bash
davinci --json export thumbnails C:\checks\sheet.jpg --at +0 --at 01:00:20:00 --label open --label title
davinci --json export frame C:\checks\lower.jpg --at 01:00:20:12 --roi 0 0.7 1 0.3 --max-dimension 1920
davinci --json grade cdl --track 1 --fail-on-grade
```

These check a build. They never replace review frames taken from the final
rendered file.

Preview producer/consumer example:

```bash
davinci --json preview capture --recipe current-frame
cli-hub previews inspect <bundle-dir>
```
