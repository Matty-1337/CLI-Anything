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
davinci --dry-run --json timeline title "Text+" --fusion
davinci --dry-run --json timeline captions --language english
davinci --json timeline export C:\exports\master.otio --format otio

# Native render queue
davinci --json render capabilities
davinci --dry-run --json render add --preset "YouTube 1080p" --target-dir C:\renders --custom-name obd-master
davinci --dry-run --json render start
davinci --json render status
```

The v1 harness deliberately has no destructive project, media, timeline, or
render-job deletion commands.

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
