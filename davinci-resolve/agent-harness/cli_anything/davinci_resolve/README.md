# CLI-Anything: DaVinci Resolve Studio

Agent-native control of a real, locally running DaVinci Resolve Studio desktop
session. The harness uses the scripting module and native library installed by
Resolve itself. It does **not** use a hosted API, API token, or paid external
service.

## Requirements

- DaVinci Resolve Studio with External scripting set to `Local` or `Network`
  in Resolve Preferences.
- Resolve must be running and past any startup/sign-in dialog for live commands.
- Python 3.10+.

The Windows defaults are discovered automatically:

- Application: `C:\Program Files\Blackmagic Design\DaVinci Resolve\Resolve.exe`
- API package: `C:\ProgramData\Blackmagic Design\DaVinci Resolve\Support\Developer\Scripting`
- Native bridge: `C:\Program Files\Blackmagic Design\DaVinci Resolve\fusionscript.dll`

Persist explicit paths when needed:

```powershell
cli-anything-davinci-resolve configure paths `
  --resolve-exe "C:\Program Files\Blackmagic Design\DaVinci Resolve\Resolve.exe" `
  --script-api "C:\ProgramData\Blackmagic Design\DaVinci Resolve\Support\Developer\Scripting" `
  --script-lib "C:\Program Files\Blackmagic Design\DaVinci Resolve\fusionscript.dll"
```

## Connection and authentication

Resolve authenticates the local scripting connection through the active
desktop application session. There is no second CLI credential or API key.

```bash
cli-anything-davinci-resolve --json doctor
cli-anything-davinci-resolve --json auth status
cli-anything-davinci-resolve --json auth connect
```

`auth connect --launch` may start Resolve, but the command intentionally asks
the caller to retry after desktop startup/sign-in is complete.

## Production commands

```bash
# Projects
davinci --json project list
davinci --json project current
davinci --dry-run --json project create "OBD Campaign 001"
davinci --json project save
davinci --json project export C:\exports\obd-campaign.drp

# Media and timelines
davinci --json media list
davinci --dry-run --json media import C:\media\hook.mp4 C:\media\voice.wav
davinci --dry-run --json timeline create "Master 16x9" --clip hook.mp4
davinci --dry-run --json timeline append --clip voice.wav
davinci --dry-run --json timeline title "Text+" --fusion   # ripple insert on V1; see Fusion titles
davinci --dry-run --json timeline captions --language english
davinci --json timeline export C:\exports\master.otio --format otio

# Native render queue
davinci --json render capabilities
davinci --dry-run --json render add --preset "YouTube 1080p" --target-dir C:\renders --custom-name obd-master
davinci --dry-run --json render start
davinci --json render status
```

The harness deliberately has no destructive project, media, timeline, or
render-job deletion commands. The one deletion it performs is internal: `fusion
export-template` removes its own throwaway timeline, and `fusion place` removes
the carrier it just added if the comp fails to load.

## Fusion titles

Proved on Resolve Studio 21.1:

- `Timeline.InsertFusionTitleIntoTimeline` always drops the title into V1 at the
  playhead as a **ripple insert**: everything after it moves later by the title
  length. `timeline title` says so in its output. It is not safe on a cut timeline.
- `fusion place` puts a title on an exact track, frame and length without moving
  anything. It appends a transparent carrier clip (QuickTime Animation, made once
  with ffmpeg and cached under `%LOCALAPPDATA%\cli-anything-davinci-resolve\carriers`)
  with `trackIndex`, `recordFrame` and `endFrame`, loads the title comp onto it with
  `ImportFusionComp`, and sets `COMPN_GlobalEnd` to the clip length so a template's
  out animation lands on the clip end instead of at the end of the carrier file.
- Text is set on named inner Text+ tools. Published macro inputs read back `0.0`
  and ignore scripts, so templates meant for automation give every text field its
  own named Text+ tool.
- `fusion export-template` gets a template's comp by inserting it on a throwaway
  timeline (`_cli_fusion_scratch`), exporting it and deleting that timeline. The
  working timeline is never inserted into.

```bash
davinci --json fusion carrier --seconds 60
davinci --json fusion export-template "Draw On 2 Lines Lower Third" C:\titles\lower.comp
davinci --dry-run --json fusion place C:\titles\lower.comp --track 3 --at 01:00:20:00 --seconds 7 --text "mainText=Matty Herrera"
davinci --json fusion place C:\titles\lower.comp --track 3 --at 01:00:20:00 --seconds 7 --text "mainText=Matty Herrera"
davinci --json fusion tools V3@01:00:21:00
davinci --json fusion inputs V3@01:00:21:00 mainText
davinci --json fusion set V3@01:00:21:00 --text "mainText=New name" --set "mainText.Size=0.06"
davinci --json fusion export V3@01:00:21:00 C:\titles\edited.comp
```

`ITEM_REF` is a timeline item's unique id or `V<track>@<frame|timecode>` for the
item covering that frame. `place` refuses V1 (`--allow-v1`) and ranges that
already hold an item (`--allow-overlap`).

## Preview bundles

`preview capture` exports the real current Resolve frame and a structured
timeline-state artifact into a `preview-bundle/v1` directory. It does not take
a GUI screenshot or synthesize a fake frame.

```bash
davinci --json preview recipes
davinci --json preview capture --recipe current-frame --label "OBD cut v3"
davinci --json preview latest
cli-hub previews inspect <bundle-dir>
```

The harness is the preview producer. `cli-hub previews` is the read-only
consumer.

## JSON, dry-run, and REPL

- Put `--json` before the command group for stable machine output.
- Put `--dry-run` before a mutating command to return the intended operation
  without changing Resolve.
- Run `davinci` or `cli-anything-davinci-resolve` with no command for the REPL.

## Installation

```bash
cd davinci-resolve/agent-harness
python -m pip install -e .
cli-anything-davinci-resolve --help
```
