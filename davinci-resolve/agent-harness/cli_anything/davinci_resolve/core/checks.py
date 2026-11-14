"""Read-only build checks from Resolve 21.2's scripting API.

Proved on Resolve Studio 21.2 (21.2.0.6), 2026-11-14:

* ``Timeline.ExportThumbnails(frames, opts)`` writes one labelled contact sheet. ``frames`` holds
  offsets from the timeline's start frame, or ``{"frameOffset", "label"}`` cells. One offset past
  the timeline's last frame fails the whole sheet, so every offset is range-checked here against
  the timeline's own start and end, never computed from seconds at an assumed rate.
* ``Timeline.ExportFrame(offset, opts)`` writes one frame, optionally a normalized region of
  interest (``roiX``, ``roiY``, ``roiWidth``, ``roiHeight``). Without ``maxDimension`` the frame
  comes out at the project's resolution, so a size is always passed.
* ``TimelineItem.GetCDL(node)`` reads a Color page node's Slope, Offset and Power (strings of three
  numbers) and Saturation; ``Graph.GetNodeEnabled(node)`` says whether that node is on.

Nothing here changes the timeline, a clip or a grade. Every function takes Resolve objects so it
can be tested with fakes.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

from .fusion import FusionError, fps_of, parse_frame

DEFAULT_SHEET_MAX = 2400
DEFAULT_FRAME_MAX = 1920
NEEDS = "needs Resolve 21.2"


def _method(obj, name: str):
    fn = getattr(obj, name, None)
    if not callable(fn):
        raise FusionError(f"{name} is not available ({NEEDS})")
    return fn


def span(timeline) -> tuple[int, int]:
    """(start frame, frame count) of the timeline."""
    start, end = int(timeline.GetStartFrame()), int(timeline.GetEndFrame())
    return start, max(0, end - start)


def offset_of(timeline, ref: str | int) -> int:
    """An offset from the timeline start. ``+N`` is already an offset; anything else is a record
    frame or HH:MM:SS:FF timecode. Refused when it falls outside the timeline."""
    start, count = span(timeline)
    text = str(ref).strip()
    off = int(text[1:]) if text.startswith("+") and text[1:].isdigit() else parse_frame(text, timeline) - start
    if not 0 <= off < count:
        raise ValueError(f"{ref!r} is offset {off}, outside the timeline (0 to {count - 1})")
    return off


def timecode(offset: int, timeline) -> str:
    fps = max(1, round(fps_of(timeline)))
    s, f = divmod(int(offset), fps)
    m, s = divmod(s, 60)
    h, m = divmod(m, 60)
    return f"{h:02d}:{m:02d}:{s:02d}:{f:02d}"


def _opts(path: Path, max_dimension: int, quality: int) -> dict[str, Any]:
    if max_dimension < 16:
        raise ValueError("--max-dimension must be at least 16")
    if not 1 <= quality <= 100:
        raise ValueError("--quality must be 1 to 100")
    return {"outputType": "file", "filePath": str(path), "maxDimension": int(max_dimension), "quality": int(quality)}


def _result(res, what: str) -> dict[str, Any]:
    res = res or {}
    if not res.get("success"):
        raise FusionError(f"{what} failed: {res.get('error') or 'no result from Resolve (' + NEEDS + ')'}")
    return res


def thumbnail_cells(timeline, refs: Iterable[str], labels: Iterable[str] = ()) -> list[dict[str, Any]]:
    labels = list(labels)
    cells = []
    for i, ref in enumerate(refs):
        off = offset_of(timeline, ref)
        cells.append({"frameOffset": off, "label": labels[i] if i < len(labels) else timecode(off, timeline)})
    if not cells:
        raise ValueError("give at least one --at")
    return cells


def export_thumbnails(timeline, cells: list[dict[str, Any]], path: Path,
                      max_dimension: int = DEFAULT_SHEET_MAX, quality: int = 90) -> dict[str, Any]:
    fn = _method(timeline, "ExportThumbnails")
    path.parent.mkdir(parents=True, exist_ok=True)
    _result(fn(cells, _opts(path, max_dimension, quality)), "ExportThumbnails")
    return {"path": str(path), "cells": cells}


def parse_roi(values: Iterable[float] | None) -> dict[str, float]:
    if not values:
        return {}
    x, y, w, h = (float(v) for v in values)
    if not (0 <= x < 1 and 0 <= y < 1 and 0 < w <= 1 - x + 1e-9 and 0 < h <= 1 - y + 1e-9):
        raise ValueError("--roi X Y W H is normalized: 0 <= X, Y < 1, W and H > 0, X+W and Y+H <= 1")
    return {"roiX": x, "roiY": y, "roiWidth": w, "roiHeight": h}


def export_frame(timeline, offset: int, path: Path, roi: dict[str, float] | None = None,
                 max_dimension: int = DEFAULT_FRAME_MAX, quality: int = 92) -> dict[str, Any]:
    fn = _method(timeline, "ExportFrame")
    path.parent.mkdir(parents=True, exist_ok=True)
    _result(fn(int(offset), {**_opts(path, max_dimension, quality), **(roi or {})}), "ExportFrame")
    return {"path": str(path), "frameOffset": int(offset), "roi": roi or None}


def _triplet(value) -> list[float] | None:
    try:
        return [float(v) for v in str(value).split()]
    except (TypeError, ValueError):
        return None


NEUTRAL = {"Slope": [1.0, 1.0, 1.0], "Offset": [0.0, 0.0, 0.0], "Power": [1.0, 1.0, 1.0], "Saturation": 1.0}


def is_neutral(cdl: dict[str, Any], tolerance: float = 1e-4) -> bool:
    for key in ("Slope", "Offset", "Power"):
        got = _triplet(cdl.get(key))
        if got is None or len(got) != 3 or any(abs(a - b) > tolerance for a, b in zip(got, NEUTRAL[key])):
            return False
    try:
        return abs(float(cdl.get("Saturation", 1.0)) - 1.0) <= tolerance
    except (TypeError, ValueError):
        return False


def item_grade(item) -> dict[str, Any]:
    graph = item.GetNodeGraph()
    count = int(graph.GetNumNodes() or 0) if graph else 0
    nodes = []
    if count:
        enabled, read = _method(graph, "GetNodeEnabled"), _method(item, "GetCDL")
        for i in range(1, count + 1):
            cdl = read(i) or None
            nodes.append({"node": i, "enabled": bool(enabled(i)), "cdl": cdl,
                          "neutral": is_neutral(cdl) if cdl else None})
    graded = [n["node"] for n in nodes if n["enabled"] and n["neutral"] is False]
    return {"id": item.GetUniqueId(), "name": item.GetName(), "start": item.GetStart(), "end": item.GetEnd(),
            "nodes": nodes, "graded_nodes": graded}


def track_grades(timeline, track: int) -> dict[str, Any]:
    if track < 1 or track > int(timeline.GetTrackCount("video")):
        raise ValueError(f"no video track V{track}")
    items = [item_grade(it) for it in timeline.GetItemListInTrack("video", track) or []]
    return {"timeline": timeline.GetName(), "track": track, "items": items,
            "graded": [i["name"] for i in items if i["graded_nodes"]]}
