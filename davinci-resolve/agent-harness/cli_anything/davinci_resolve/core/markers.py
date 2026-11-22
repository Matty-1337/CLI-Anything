"""Timeline size and frame rate, timeline markers, and cue sheet import (Resolve 21.2).

API used (Resolve 21.2 stubs and README "Looking up Project and Clip properties"):

* ``Timeline.SetSettings({...})`` / ``GetSettings()``. ``useCustomSettings`` is applied first and must be
  ``'1'`` before any other timeline setting can change; width and height are strings; ``timelineFrameRate``
  is set as a string (``'30'``, ``'29.97 DF'``) and read back as a number. When a setting fails, the ones
  before it are kept and the call returns False.
* ``Timeline.AddMarker(frameId, color, name, note, duration, customData)``, ``GetMarkers()`` (frameId ->
  info), ``DeleteMarkerAtFrame(frameId)``. ``frameId`` counts from the timeline's first frame
  (``GetStartFrame()``, 86400 at 01:00:00:00 and 24 fps, 108000 at 30 fps), not from record frame 0.

Nothing is trusted from the request: every setting and every marker is read back, and a marker that does
not read back as asked is removed again before the command fails.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

from .fusion import FusionError

MARKER_COLORS = ("Blue", "Cyan", "Green", "Yellow", "Red", "Pink", "Purple", "Fuchsia", "Rose",
                 "Lavender", "Sky", "Mint", "Lemon", "Sand", "Cocoa", "Cream")
CUE_PREFIX = "cue:"


# ---------------------------------------------------------------- timeline size and frame rate

def parse_fps(text) -> tuple[str, float]:
    """``'30'``, ``'29.97'``, ``'23.976'`` or ``'29.97 DF'`` -> (the string Resolve takes, the number it returns)."""
    raw = str(text).strip()
    match = re.fullmatch(r"(\d+(?:\.\d+)?)(\s*DF)?", raw, flags=re.IGNORECASE)
    if not match:
        raise ValueError(f"--fps must be a frame rate such as 30, 29.97 or '29.97 DF', got {text!r}")
    number = float(match.group(1))
    if not 1 <= number <= 120:
        raise ValueError(f"--fps {number} is outside 1 to 120")
    value = match.group(1) + (" DF" if match.group(2) else "")
    return value, number


def plan_settings(width: int | None, height: int | None, fps: str | None) -> dict[str, str]:
    if (width is None) != (height is None):
        raise ValueError("--width and --height go together")
    settings: dict[str, str] = {}
    if width is not None:
        if not (16 <= int(width) <= 16384 and 16 <= int(height) <= 16384):
            raise ValueError(f"timeline size {width}x{height} is outside 16 to 16384")
        settings["timelineResolutionWidth"] = str(int(width))
        settings["timelineResolutionHeight"] = str(int(height))
    if fps is not None:
        settings["timelineFrameRate"] = parse_fps(fps)[0]
    if settings:
        settings = {"useCustomSettings": "1", **settings}
    return settings


def settings_state(timeline) -> dict[str, Any]:
    s = timeline.GetSettings() or {}
    return {key: s.get(key) for key in ("useCustomSettings", "timelineResolutionWidth",
                                         "timelineResolutionHeight", "timelineFrameRate")}


def _same(key: str, want: str, got) -> bool:
    if got is None:
        return False
    if key == "timelineFrameRate":
        try:
            return abs(float(str(got).split()[0]) - parse_fps(want)[1]) < 1e-3
        except ValueError:
            return False
    return str(got).strip() == str(want).strip()


def apply_settings(timeline, settings: dict[str, str]) -> dict[str, Any]:
    """Set and read back. Raises with what Resolve holds when any value does not read back."""
    if not settings:
        return {"set": {}, "after": settings_state(timeline), "verified": True}
    setter = getattr(timeline, "SetSettings", None)
    if not callable(setter):
        raise FusionError("Timeline.SetSettings is not available (needs Resolve 21.2 or later)")
    returned = setter(dict(settings))
    after = settings_state(timeline)
    wrong = [f"{k} read {after.get(k)!r}, set {v!r}" for k, v in settings.items() if not _same(k, v, after.get(k))]
    if wrong:
        raise RuntimeError("timeline settings readback failed (SetSettings returned "
                           f"{returned!r}): " + "; ".join(wrong))
    return {"set": settings, "returned": bool(returned), "after": after, "verified": True}


# ---------------------------------------------------------------- markers

def _markers(timeline) -> dict[int, dict[str, Any]]:
    return {int(k): dict(v or {}) for k, v in (timeline.GetMarkers() or {}).items()}


def list_markers(timeline) -> list[dict[str, Any]]:
    start = int(timeline.GetStartFrame())
    return [{"frame": f, "record_frame": start + f, **info} for f, info in sorted(_markers(timeline).items())]


def plan_marker(frame: int, name: str, color: str = "Blue", note: str = "", duration: int = 1,
                custom_data: str | None = None) -> dict[str, Any]:
    if int(frame) < 0:
        raise ValueError("--frame counts from the timeline start and cannot be negative")
    if color not in MARKER_COLORS:
        raise ValueError(f"--color must be one of {', '.join(MARKER_COLORS)}")
    if int(duration) < 1:
        raise ValueError("--duration must be at least 1 frame")
    if not str(name or "").strip():
        raise ValueError("a marker needs a name")
    return {"frame": int(frame), "color": color, "name": str(name).strip(), "note": str(note or ""),
            "duration": int(duration), "customData": custom_data or ""}


def _matches(want: dict[str, Any], got: dict[str, Any] | None) -> list[str]:
    if got is None:
        return ["no marker at that frame after AddMarker"]
    out = []
    for key in ("color", "name", "note", "duration"):
        if str(got.get(key, "")) != str(want[key]):
            out.append(f"{key} read {got.get(key)!r}, set {want[key]!r}")
    if want["customData"] and got.get("customData") not in (None, want["customData"]):
        out.append(f"customData read {got.get('customData')!r}, set {want['customData']!r}")
    return out


def add_marker(timeline, frame: int, name: str, color: str = "Blue", note: str = "", duration: int = 1,
               custom_data: str | None = None) -> dict[str, Any]:
    want = plan_marker(frame, name, color, note, duration, custom_data)
    existing = _markers(timeline)
    if want["frame"] in existing:
        raise RuntimeError(f"frame {want['frame']} already has a marker ({existing[want['frame']].get('name')!r}); "
                           "Resolve keeps one marker per frame")
    ok = timeline.AddMarker(want["frame"], want["color"], want["name"], want["note"], want["duration"],
                            want["customData"] or None)
    got = _markers(timeline).get(want["frame"])
    problems = [] if ok else ["AddMarker returned False"]
    problems += _matches(want, got) if ok else []
    if problems:
        undone = bool(got is not None and timeline.DeleteMarkerAtFrame(want["frame"]))
        raise RuntimeError("marker readback failed: " + "; ".join(problems) + f"; removed again: {undone}")
    start = int(timeline.GetStartFrame())
    return {"frame": want["frame"], "record_frame": start + want["frame"], **got, "verified": True}


# ---------------------------------------------------------------- cue sheets

def _fps_of(timeline) -> float:
    value = (timeline.GetSettings() or {}).get("timelineFrameRate")
    try:
        return float(str(value).split()[0])
    except (TypeError, ValueError):
        raise FusionError(f"cannot read the timeline frame rate (got {value!r})") from None


def plan_cues(sheet: dict[str, Any], color: str = "Blue", beats: bool = False,
              beat_color: str = "Green") -> list[dict[str, Any]]:
    """Markers for a Motion Studio cue sheet (scripts/di-offer-cues.mjs): one per cue, and per beat with ``beats``."""
    cut = str(sheet.get("cut") or "cut")
    rows = []
    for cue in sheet.get("cues") or []:
        rows.append(plan_marker(cue["frame"], cue["name"], color, cue.get("phrase", ""), 1,
                                f"{CUE_PREFIX}{cut}:{cue['name']}"))
    if beats:
        for b in sheet.get("beats") or []:
            rows.append(plan_marker(b["startFrame"], f"beat {b['kind']}", beat_color, "",
                                    max(1, int(b["endFrame"]) - int(b["startFrame"])), f"{CUE_PREFIX}{cut}:beat:{b['kind']}"))
    seen: dict[int, str] = {}
    for r in rows:
        if r["frame"] in seen:
            raise ValueError(f"two markers on frame {r['frame']}: {seen[r['frame']]!r} and {r['name']!r}")
        seen[r["frame"]] = r["name"]
    return sorted(rows, key=lambda r: r["frame"])


def import_cues(timeline, sheet: dict[str, Any], color: str = "Blue", beats: bool = False,
                replace: bool = False) -> dict[str, Any]:
    rows = plan_cues(sheet, color, beats)
    fps = _fps_of(timeline)
    if abs(fps - float(sheet.get("fps", 0))) > 1e-3:
        raise RuntimeError(f"cue sheet is {sheet.get('fps')} fps, timeline is {fps}; set the timeline first")
    existing = _markers(timeline)
    removed = []
    if replace:
        for frame, info in existing.items():
            if str(info.get("customData", "")).startswith(CUE_PREFIX) and timeline.DeleteMarkerAtFrame(frame):
                removed.append(frame)
        existing = _markers(timeline)
    clash = [r["frame"] for r in rows if r["frame"] in existing]
    if clash:
        raise RuntimeError(f"frames already carry markers: {clash[:10]}; pass --replace to swap earlier cue markers")
    added: list[int] = []
    try:
        for r in rows:
            add_marker(timeline, r["frame"], r["name"], r["color"], r["note"], r["duration"], r["customData"])
            added.append(r["frame"])
    except Exception as exc:
        undone = [f for f in added if timeline.DeleteMarkerAtFrame(f)]
        raise RuntimeError(f"{exc}; {len(undone)} of {len(added)} markers from this import removed again") from exc
    after = _markers(timeline)
    missing = [r["frame"] for r in rows if r["frame"] not in after]
    if missing:
        raise RuntimeError(f"markers missing after import: {missing}")
    return {"cut": sheet.get("cut"), "fps": fps, "added": len(added), "removed": len(removed),
            "frames": [r["frame"] for r in rows], "verified": True}


def compare(timeline, sheet: dict[str, Any]) -> list[str]:
    """Cue frames the timeline does not carry (empty when every cue has its marker)."""
    have = _markers(timeline)
    return [f"{c['name']} at {c['frame']}" for c in sheet.get("cues") or [] if int(c["frame"]) not in have]


def summarize_rows(rows: Iterable[dict[str, Any]]) -> list[str]:
    return [f"{r['frame']:>6}  {r.get('color', ''):<8} {r.get('name', '')}" for r in rows]
