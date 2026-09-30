"""Fusion title placement and editing through Resolve's scripting API.

Proved on Resolve Studio 21.1 (see the harness README, "Fusion titles"):

* ``Timeline.InsertFusionTitleIntoTimeline`` always drops the title into V1 at the playhead as a
  ripple insert, pushing everything after it. It is only ever called here on a throwaway timeline.
* A title placed on an exact track, frame and length is a transparent *carrier* clip appended with
  ``trackIndex``/``recordFrame``/``endFrame``, with the title comp loaded by ``ImportFusionComp``.
* The comp's global range starts as the carrier file's full length, so a template's
  ``KeyframeStretcher`` would put the out animation there. ``COMPN_GlobalEnd`` is set to the clip
  length so the out animation lands on the clip end.
* Text is set on named inner Text+ tools (``StyledText``). Published macro inputs ignore scripts.

Every function takes Resolve objects so it can be unit tested with fakes and imported by callers.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Iterable

SCRATCH_TIMELINE = "_cli_fusion_scratch"
CARRIER_BIN = "Fusion Carriers"
_TC = re.compile(r"^(\d{1,2}):(\d{2}):(\d{2})[:;](\d{2})$")


class FusionError(RuntimeError):
    """A Fusion title operation could not be completed."""


# ---------------------------------------------------------------- parsing

def fps_of(timeline) -> float:
    value = timeline.GetSetting("timelineFrameRate") if hasattr(timeline, "GetSetting") else None
    try:
        return float(value)
    except (TypeError, ValueError):
        return 30.0


def parse_frame(value: str | int, timeline) -> int:
    """A record frame from an absolute frame number or an HH:MM:SS:FF timecode."""
    if isinstance(value, int):
        return value
    text = str(value).strip()
    if text.lstrip("-").isdigit():
        return int(text)
    match = _TC.match(text)
    if not match:
        raise ValueError(f"not a frame number or HH:MM:SS:FF timecode: {value!r}")
    fps = round(fps_of(timeline))
    h, m, s, f = (int(x) for x in match.groups())
    return ((h * 60 + m) * 60 + s) * fps + f


def parse_value(raw: str) -> Any:
    """A Fusion input value: JSON when it parses (numbers, lists, points), else the plain string.

    A JSON object with numeric keys becomes a Fusion point table, so ``{"1": 0.5, "2": 0.9}``
    sets a Center.
    """
    try:
        value = json.loads(raw)
    except (ValueError, TypeError):
        return raw
    if isinstance(value, dict) and all(str(k).isdigit() for k in value):
        return {int(k): v for k, v in value.items()}
    return value


def parse_assignment(text: str) -> tuple[str, str, Any]:
    """``Tool.Input=value`` into (tool, input, value)."""
    left, sep, raw = text.partition("=")
    tool, dot, inp = left.partition(".")
    if not sep or not dot or not tool or not inp:
        raise ValueError(f"expected Tool.Input=value, got {text!r}")
    return tool.strip(), inp.strip(), parse_value(raw)


def parse_text(text: str) -> tuple[str, str, Any]:
    """``Tool=text`` shorthand for ``Tool.StyledText=text``; the text is never JSON-parsed."""
    tool, sep, value = text.partition("=")
    if not sep or not tool.strip():
        raise ValueError(f"expected Tool=text, got {text!r}")
    return tool.strip(), "StyledText", value


# ---------------------------------------------------------------- lookups

def find_timeline(project, name: str | None):
    if not name:
        active = project.GetCurrentTimeline()
        if not active:
            raise FusionError("No timeline is currently open")
        return active
    for index in range(1, int(project.GetTimelineCount()) + 1):
        timeline = project.GetTimelineByIndex(index)
        if timeline.GetName() == name:
            return timeline
    raise FusionError(f"No timeline named {name!r}")


def item_summary(item) -> dict[str, Any]:
    track = item.GetTrackTypeAndIndex() if hasattr(item, "GetTrackTypeAndIndex") else None
    return {
        "id": item.GetUniqueId(),
        "name": item.GetName(),
        "start": item.GetStart(),
        "end": item.GetEnd(),
        "duration": item.GetDuration(),
        "track": list(track) if track else None,
        "fusion_comps": item.GetFusionCompCount() if hasattr(item, "GetFusionCompCount") else 0,
    }


def find_item(timeline, ref: str):
    """A video item by unique id, or ``V<track>@<frame|timecode>`` for the item covering that frame."""
    match = re.match(r"^[Vv](\d+)@(.+)$", ref.strip())
    tracks = range(1, int(timeline.GetTrackCount("video")) + 1)
    if match:
        track, where = int(match.group(1)), parse_frame(match.group(2), timeline)
        for item in timeline.GetItemListInTrack("video", track) or []:
            if item.GetStart() <= where < item.GetEnd():
                return item
        raise FusionError(f"No item on V{track} at frame {where}")
    for track in tracks:
        for item in timeline.GetItemListInTrack("video", track) or []:
            if str(item.GetUniqueId()) == ref:
                return item
    raise FusionError(f"No video item with id {ref!r}")


def comp_of(item, index: int = 1):
    if not item.GetFusionCompCount():
        raise FusionError(f"{item.GetName()} has no Fusion comp")
    comp = item.GetFusionCompByIndex(index)
    if comp is None:
        raise FusionError(f"{item.GetName()} has no Fusion comp at index {index}")
    return comp


def tool_list(comp) -> list[dict[str, Any]]:
    rows = []
    for tool in (comp.GetToolList(False) or {}).values():
        attrs = tool.GetAttrs() or {}
        rows.append({"name": attrs.get("TOOLS_Name"), "type": attrs.get("TOOLS_RegID")})
    return sorted(rows, key=lambda row: str(row["name"]))


def input_list(comp, tool_name: str) -> list[dict[str, Any]]:
    tool = comp.FindTool(tool_name)
    if tool is None:
        raise FusionError(f"No tool named {tool_name!r} in the comp")
    rows = []
    for inp in (tool.GetInputList() or {}).values():
        attrs = inp.GetAttrs() or {}
        ident = attrs.get("INPS_ID")
        try:
            value = tool.GetInput(ident)
        except Exception as exc:  # noqa: BLE001 - Fusion raises assorted errors for unreadable inputs
            value = f"<unreadable: {exc}>"
        rows.append({"id": ident, "name": attrs.get("INPS_Name"), "value": value})
    return sorted(rows, key=lambda row: str(row["id"]))


def set_inputs(comp, assignments: Iterable[tuple[str, str, Any]]) -> list[dict[str, Any]]:
    """Set each (tool, input, value) and read it back. A missing tool is an error, never a skip."""
    applied = []
    assignments = list(assignments)
    if not assignments:
        return applied
    comp.Lock()
    try:
        for tool_name, inp, value in assignments:
            tool = comp.FindTool(tool_name)
            if tool is None:
                raise FusionError(f"No tool named {tool_name!r} in the comp")
            tool.SetInput(inp, value)
            applied.append({"tool": tool_name, "input": inp, "value": value, "read": tool.GetInput(inp)})
    finally:
        comp.Unlock()
    return applied


# ---------------------------------------------------------------- carrier

def default_carrier_path(seconds: int = 60, width: int = 1920, height: int = 1080, fps: float = 30.0) -> Path:
    root = Path(os.environ.get("LOCALAPPDATA", Path.home() / ".local"))
    tag = f"{width}x{height}_{fps:g}fps_{seconds}s"
    return root / "cli-anything-davinci-resolve" / "carriers" / f"carrier_{tag}.mov"


def make_carrier(path: Path, seconds: int = 60, width: int = 1920, height: int = 1080, fps: float = 30.0) -> Path:
    """A transparent QuickTime Animation clip (about 12 MB for 60 s at 1080p). Needs ffmpeg."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise FusionError("ffmpeg is not on PATH; pass --carrier with an existing transparent clip")
    path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [ffmpeg, "-v", "error", "-y", "-f", "lavfi",
           "-i", f"color=c=black@0.0:s={width}x{height}:r={fps:g}:d={seconds},format=rgba",
           "-c:v", "qtrle", "-pix_fmt", "argb", str(path)]
    done = subprocess.run(cmd, capture_output=True, text=True)
    if done.returncode != 0 or not path.is_file():
        raise FusionError(f"ffmpeg could not make the carrier: {done.stderr.strip()[-400:]}")
    return path


def _bin(pool, name: str):
    root = pool.GetRootFolder()
    for folder in root.GetSubFolderList() or []:
        if folder.GetName() == name:
            return folder
    return pool.AddSubFolder(root, name)


def carrier_item(project, path: Path):
    """The carrier's MediaPoolItem, imported into the carrier bin once and reused after that."""
    pool = project.GetMediaPool()
    folder = _bin(pool, CARRIER_BIN)
    wanted = str(path).casefold()
    for clip in folder.GetClipList() or []:
        props = clip.GetClipProperty() or {}
        if str(props.get("File Path", "")).casefold() == wanted:
            return clip
    current = pool.GetCurrentFolder()
    pool.SetCurrentFolder(folder)
    try:
        imported = pool.ImportMedia([str(path)]) or []
    finally:
        if current is not None:
            pool.SetCurrentFolder(current)
    if not imported:
        raise FusionError(f"Resolve could not import the carrier {path}")
    return imported[0]


def carrier_frames(clip) -> int:
    try:
        return int(clip.GetClipProperty("Frames"))
    except (TypeError, ValueError):
        return 0


# ---------------------------------------------------------------- operations

def authored_minimum(comp_path: Path) -> int:
    """The shortest placement a comp can render at, in frames (0 when it has no limit).

    A comp with a KeyStretcher only stretches: placed shorter than its authored range it fails to
    render (proved on Resolve 21.1, a 150 frame placement of a 180 frame comp). The authored range
    is the comp's GlobalRange.
    """
    try:
        text = Path(comp_path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return 0
    if "KeyStretcher" not in text:
        return 0
    match = re.search(r"GlobalRange\s*=\s*\{\s*(-?\d+)\s*,\s*(-?\d+)\s*\}", text)
    return int(match.group(2)) - int(match.group(1)) + 1 if match else 0


def occupied(timeline, track: int, start: int, end: int) -> list[dict[str, Any]]:
    """Items on the video track that overlap [start, end)."""
    if track > int(timeline.GetTrackCount("video")):
        return []
    return [item_summary(item) for item in timeline.GetItemListInTrack("video", track) or []
            if item.GetStart() < end and start < item.GetEnd()]


def place(project, timeline, comp_path: Path, track: int, record_frame: int, frames: int,
          assignments: Iterable[tuple[str, str, Any]] = (), carrier: Path | None = None,
          allow_v1: bool = False, allow_overlap: bool = False) -> dict[str, Any]:
    """Place a Fusion comp as a title on ``track`` at ``record_frame`` for ``frames`` frames.

    Never touches another item: it refuses V1 (the program track) unless ``allow_v1``, and refuses a
    range that already holds an item unless ``allow_overlap``.
    """
    comp_path = Path(comp_path)
    if not comp_path.is_file():
        raise FusionError(f"No comp file at {comp_path}")
    if track < 1:
        raise FusionError("track must be 1 or higher")
    if track == 1 and not allow_v1:
        raise FusionError("refusing to place a title on V1, the program track; pass --allow-v1 to override")
    if frames < 1:
        raise FusionError("duration must be at least one frame")
    minimum = authored_minimum(comp_path)
    if frames < minimum:
        raise FusionError(f"the comp is authored at {minimum} frames and only stretches; {frames} asked would fail to render")
    clash = occupied(timeline, track, record_frame, record_frame + frames)
    if clash and not allow_overlap:
        raise FusionError(f"V{track} already holds {len(clash)} item(s) in that range: {[c['name'] for c in clash]}")

    carrier_path = Path(carrier) if carrier else default_carrier_path(fps=fps_of(timeline))
    if not carrier_path.is_file():
        make_carrier(carrier_path, fps=fps_of(timeline))
    clip = carrier_item(project, carrier_path)
    available = carrier_frames(clip)
    if available and frames > available:
        raise FusionError(f"the carrier holds {available} frames; {frames} asked. Make a longer one with 'fusion carrier --seconds'")

    while int(timeline.GetTrackCount("video")) < track:
        if not timeline.AddTrack("video"):
            raise FusionError(f"could not add video track {int(timeline.GetTrackCount('video')) + 1}")
    placed = project.GetMediaPool().AppendToTimeline([{
        "mediaPoolItem": clip, "trackIndex": track, "recordFrame": record_frame,
        "startFrame": 0, "endFrame": frames, "mediaType": 1,
    }]) or []
    if not placed:
        raise FusionError("Resolve rejected the carrier append")
    item = placed[0]
    comp = item.ImportFusionComp(str(comp_path))
    if comp is None:
        timeline.DeleteClips([item], False)
        raise FusionError(f"Resolve could not import the comp {comp_path}; the carrier was removed")
    comp.SetAttrs({"COMPN_GlobalEnd": float(item.GetDuration() - 1)})
    applied = set_inputs(comp, assignments)
    attrs = comp.GetAttrs() or {}
    return {**item_summary(item), "comp": str(comp_path), "carrier": str(carrier_path),
            "global_end": attrs.get("COMPN_GlobalEnd"), "set": applied}


def export_template(project, title_name: str, path: Path) -> dict[str, Any]:
    """Export a Fusion title template's comp to ``path`` without touching any working timeline.

    The title is inserted on a throwaway timeline (the insert ripples V1), exported, and the
    throwaway timeline is deleted. The current timeline is restored.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    pool = project.GetMediaPool()
    previous = project.GetCurrentTimeline()
    scratch = pool.CreateEmptyTimeline(SCRATCH_TIMELINE)
    if scratch is None:
        raise FusionError(f"could not create the scratch timeline {SCRATCH_TIMELINE!r} (does it already exist?)")
    try:
        project.SetCurrentTimeline(scratch)
        scratch.SetCurrentTimecode(scratch.GetStartTimecode())
        item = scratch.InsertFusionTitleIntoTimeline(title_name)
        if item is None:
            raise FusionError(f"Resolve has no Fusion title named {title_name!r}")
        tools = tool_list(comp_of(item))
        if not item.ExportFusionComp(str(path), 1):
            raise FusionError(f"Resolve could not export the comp to {path}")
        return {"title": title_name, "path": str(path), "frames": item.GetDuration(), "tools": tools}
    finally:
        if previous is not None:
            project.SetCurrentTimeline(previous)
        pool.DeleteTimelines([scratch])
