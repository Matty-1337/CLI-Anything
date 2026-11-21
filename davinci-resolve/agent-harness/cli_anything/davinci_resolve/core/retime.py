"""Clip speed, retime process and motion estimation for one timeline item (Resolve 21.2).

API used (Resolve 21.2 stubs):

* ``TimelineItem.SetSpeed({"Percentage", "PitchCorrection", "StretchKeyframesToFit", "RippleTimeline"})``
  and ``TimelineItem.GetSpeed()``.
* ``TimelineItem.SetProperties({...})`` and ``GetProperties()`` with the keys ``RetimeProcess`` and
  ``MotionEstimation``. Their values are the ``resolve.RETIME_*`` and ``resolve.MOTION_EST_*``
  constants; reads return the constant's number. All keys are validated before any is applied.
  Older builds expose ``SetProperty(key, value)`` / ``GetProperty(key)`` instead; both are handled.

Every change is read back. A readback that does not match raises, and the item's previous state is
reported so nothing is silently half applied. Ripple is off by default: a rippled SetSpeed has been
reported to unlink tracks.
"""

from __future__ import annotations

from typing import Any

from .fusion import FusionError

RETIME = {
    "project": "RETIME_USE_PROJECT",
    "nearest": "RETIME_NEAREST",
    "frame-blend": "RETIME_FRAME_BLEND",
    "optical-flow": "RETIME_OPTICAL_FLOW",
}
MOTION = {
    "project": "MOTION_EST_USE_PROJECT",
    "standard-faster": "MOTION_EST_STANDARD_FASTER",
    "standard-better": "MOTION_EST_STANDARD_BETTER",
    "enhanced-faster": "MOTION_EST_ENHANCED_FASTER",
    "enhanced-better": "MOTION_EST_ENHANCED_BETTER",
    "speed-warp-faster": "MOTION_EST_SPEED_WARP_FASTER",
    "speed-warp-better": "MOTION_EST_SPEED_WARP_BETTER",
}
SPEED_WARP = {"speed-warp-faster", "speed-warp-better"}


def _const(resolve, name: str) -> float:
    value = getattr(resolve, name, None)
    if value is None:
        raise FusionError(f"resolve.{name} is not available (needs Resolve 21.2 Studio)")
    return value


def _name_for(resolve, table: dict[str, str], value) -> str | None:
    if value is None:
        return None
    for key, const in table.items():
        got = getattr(resolve, const, None)
        if got is not None and float(got) == float(value):
            return key
    return f"unknown({value})"


def find_item(timeline, track: int, item: str):
    """Item on video track ``track`` by 1-based index (``2``), unique id (``id:...``), or the record
    frame it covers (``@86460``)."""
    if track < 1 or track > int(timeline.GetTrackCount("video")):
        raise ValueError(f"no video track V{track}")
    items = list(timeline.GetItemListInTrack("video", track) or [])
    text = str(item).strip()
    if text.startswith("id:"):
        hit = [it for it in items if it.GetUniqueId() == text[3:]]
    elif text.startswith("@"):
        frame = int(text[1:])
        hit = [it for it in items if int(it.GetStart()) <= frame < int(it.GetEnd())]
    else:
        index = int(text)
        hit = [items[index - 1]] if 1 <= index <= len(items) else []
    if len(hit) != 1:
        raise ValueError(f"no single item {item!r} on V{track} ({len(items)} items)")
    return hit[0]


def _get_props(item) -> dict[str, Any]:
    getter = getattr(item, "GetProperties", None)
    if callable(getter):
        props = getter() or {}
        return {"RetimeProcess": props.get("RetimeProcess"), "MotionEstimation": props.get("MotionEstimation")}
    single = getattr(item, "GetProperty", None)
    if callable(single):
        return {"RetimeProcess": single("RetimeProcess"), "MotionEstimation": single("MotionEstimation")}
    raise FusionError("TimelineItem has no GetProperties or GetProperty")


def _set_props(item, props: dict[str, Any]) -> bool:
    setter = getattr(item, "SetProperties", None)
    if callable(setter):
        return bool(setter(props))
    single = getattr(item, "SetProperty", None)
    if callable(single):
        return all(bool(single(k, v)) for k, v in props.items())
    raise FusionError("TimelineItem has no SetProperties or SetProperty")


def read(resolve, item) -> dict[str, Any]:
    getter = getattr(item, "GetSpeed", None)
    if not callable(getter):
        raise FusionError("TimelineItem.GetSpeed is not available (needs Resolve 21.2)")
    speed = getter() or {}
    props = _get_props(item)
    return {
        "id": item.GetUniqueId(),
        "name": item.GetName(),
        "start": item.GetStart(),
        "end": item.GetEnd(),
        "speed": speed.get("Percentage"),
        "retime_process": _name_for(resolve, RETIME, props["RetimeProcess"]),
        "motion_estimation": _name_for(resolve, MOTION, props["MotionEstimation"]),
    }


def plan(before: dict[str, Any], speed: float | None, process: str | None, motion: str | None) -> dict[str, Any]:
    """Validate a request against the item's current state. Speed Warp needs Optical Flow."""
    if speed is not None and not 0.0 <= float(speed) <= 10000.0:
        raise ValueError("--speed is a percentage from 0 (freeze) to 10000")
    if process is not None and process not in RETIME:
        raise ValueError(f"--process must be one of {', '.join(RETIME)}")
    if motion is not None and motion not in MOTION:
        raise ValueError(f"--motion must be one of {', '.join(MOTION)}")
    if speed is None and process is None and motion is None:
        raise ValueError("give at least one of --speed, --process, --motion")
    final_process = process or before.get("retime_process")
    if motion in SPEED_WARP and final_process != "optical-flow":
        raise ValueError("Speed Warp needs --process optical-flow")
    return {"speed": speed, "process": process, "motion": motion}


def apply(resolve, item, speed: float | None = None, process: str | None = None, motion: str | None = None,
          ripple: bool = False, pitch: bool | None = None) -> dict[str, Any]:
    before = read(resolve, item)
    plan(before, speed, process, motion)
    if speed is not None:
        options: dict[str, Any] = {"Percentage": float(speed), "RippleTimeline": bool(ripple)}
        if pitch is not None:
            options["PitchCorrection"] = bool(pitch)
        if not item.SetSpeed(options):
            raise RuntimeError(f"Resolve refused SetSpeed {options}; item unchanged: {before}")
    props: dict[str, Any] = {}
    if process is not None:
        props["RetimeProcess"] = _const(resolve, RETIME[process])
    if motion is not None:
        props["MotionEstimation"] = _const(resolve, MOTION[motion])
    if props and not _set_props(item, props):
        raise RuntimeError(f"Resolve refused {sorted(props)}; state before: {before}")
    after = read(resolve, item)
    wrong = []
    if speed is not None and (after["speed"] is None or abs(float(after["speed"]) - float(speed)) > 1e-6):
        wrong.append(f"speed {after['speed']} != {speed}")
    if process is not None and after["retime_process"] != process:
        wrong.append(f"retime_process {after['retime_process']} != {process}")
    if motion is not None and after["motion_estimation"] != motion:
        wrong.append(f"motion_estimation {after['motion_estimation']} != {motion}")
    if wrong:
        raise RuntimeError("readback mismatch: " + "; ".join(wrong) + f"; before: {before}; after: {after}")
    return {"before": before, "after": after, "verified": True}
