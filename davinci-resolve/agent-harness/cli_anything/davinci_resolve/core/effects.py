"""Edit page transitions and clip effects on one timeline item (Resolve 21.1.2 and later).

API used (Resolve 21.2 stubs):

* ``resolve.GetEffectsList(mediaType)`` returns ``[{"category", "id", "name"}]`` for every clip effect.
* ``TimelineItem.AddTransition({"type", "category", "position", "alignment", "duration"})`` returns the
  transition as a TimelineItem, or None. Its ``TransitionType``, ``TransitionCategory``,
  ``TransitionDuration`` and ``TransitionAlignment`` are item properties; duration and alignment can be set.
* ``TimelineItem.AddEffect(category, id)``, ``GetEffects(category)`` and ``DeleteEffects([...])``;
  ``Effect.GetInfo()`` and ``Effect.GetFusionComp()``.

Nothing is trusted from the request. A transition is read back from the item Resolve returns and,
when the track lists it, from a fresh query of the track; every clip on every video track must keep
its start and end. A wrong duration is corrected once through ``TransitionDuration`` (a 1 frame
transition has been reported for scripted adds); if it is still wrong, or a clip moved, the
transition is removed again and the command fails with what it saw.
"""

from __future__ import annotations

import re
from typing import Any, Iterable

from .fusion import FusionError, parse_frame, parse_value, set_inputs

TRANSITION_CATEGORIES = ("simple", "fusion", "ofx", "audio")
EFFECT_CATEGORIES = ("fusion", "ofx", "audio")
POSITIONS = ("start", "end")
ALIGNMENTS = ("left", "center", "right")
MEDIA_TYPES = ("video", "photo", "audio")
_PLUMBING = ("MediaIn", "MediaOut")


# ---------------------------------------------------------------- lookups

def _is_transition(item) -> bool:
    getter = getattr(item, "GetType", None)
    return callable(getter) and getter() == "transition"


def find_clip(timeline, ref: str):
    """A clip (never a transition) by unique id, or ``V<track>@<frame|timecode>`` for the clip
    covering that record frame."""
    match = re.match(r"^[Vv](\d+)@(.+)$", str(ref).strip())
    count = int(timeline.GetTrackCount("video"))
    if match:
        track, where = int(match.group(1)), parse_frame(match.group(2), timeline)
        if not 1 <= track <= count:
            raise FusionError(f"No video track V{track}")
        for item in timeline.GetItemListInTrack("video", track) or []:
            if not _is_transition(item) and item.GetStart() <= where < item.GetEnd():
                return item, track
        raise FusionError(f"No clip on V{track} at frame {where}")
    for track in range(1, count + 1):
        for item in timeline.GetItemListInTrack("video", track) or []:
            if not _is_transition(item) and str(item.GetUniqueId()) == str(ref):
                return item, track
    raise FusionError(f"No video clip with id {ref!r}")


def clip_positions(timeline) -> dict[str, tuple[int, int, int]]:
    """Every clip on every video track: unique id -> (track, start, end)."""
    out = {}
    for track in range(1, int(timeline.GetTrackCount("video")) + 1):
        for item in timeline.GetItemListInTrack("video", track) or []:
            if not _is_transition(item):
                out[str(item.GetUniqueId())] = (track, int(item.GetStart()), int(item.GetEnd()))
    return out


def moved(before: dict, after: dict) -> list[str]:
    out = []
    for uid, pos in before.items():
        now = after.get(uid)
        if now is None:
            out.append(f"{uid} is gone (was V{pos[0]} {pos[1]}-{pos[2]})")
        elif now != pos:
            out.append(f"{uid} V{pos[0]} {pos[1]}-{pos[2]} -> V{now[0]} {now[1]}-{now[2]}")
    out += [f"{uid} is new at V{p[0]} {p[1]}-{p[2]}" for uid, p in after.items() if uid not in before]
    return out


# ---------------------------------------------------------------- effects list

def list_effects(resolve, media_type: str = "video", category: str | None = None,
                 match: str | None = None) -> list[dict[str, Any]]:
    if media_type not in MEDIA_TYPES:
        raise ValueError(f"--media-type must be one of {', '.join(MEDIA_TYPES)}")
    if category is not None and category not in EFFECT_CATEGORIES:
        raise ValueError(f"--category must be one of {', '.join(EFFECT_CATEGORIES)}")
    getter = getattr(resolve, "GetEffectsList", None)
    if not callable(getter):
        raise FusionError("resolve.GetEffectsList is not available (needs Resolve 21.1.2 or later)")
    rows = [{"category": e.get("category"), "id": e.get("id"), "name": e.get("name")}
            for e in (getter(media_type) or [])]
    if category:
        rows = [r for r in rows if r["category"] == category]
    if match:
        needle = match.lower()
        rows = [r for r in rows if needle in str(r["name"]).lower() or needle in str(r["id"]).lower()]
    return sorted(rows, key=lambda r: (str(r["category"]), str(r["name"]).lower()))


# ---------------------------------------------------------------- transitions

def _prop(item, key: str):
    getter = getattr(item, "GetProperty", None)
    return getter(key) if callable(getter) else None


def transition_state(item) -> dict[str, Any]:
    return {
        "id": item.GetUniqueId(),
        "type": _prop(item, "TransitionType"),
        "category": _prop(item, "TransitionCategory"),
        "alignment": _prop(item, "TransitionAlignment"),
        "duration_property": _prop(item, "TransitionDuration"),
        "start": int(item.GetStart()),
        "end": int(item.GetEnd()),
        "duration": int(item.GetDuration()),
    }


def plan_transition(type_name: str, category: str, position: str, alignment: str, frames: int) -> dict[str, Any]:
    if not str(type_name or "").strip():
        raise ValueError("--type names the transition, e.g. 'Cross Dissolve'")
    if category not in TRANSITION_CATEGORIES:
        raise ValueError(f"--category must be one of {', '.join(TRANSITION_CATEGORIES)}")
    if position not in POSITIONS:
        raise ValueError(f"--position must be one of {', '.join(POSITIONS)}")
    if alignment not in ALIGNMENTS:
        raise ValueError(f"--alignment must be one of {', '.join(ALIGNMENTS)}")
    if int(frames) < 1:
        raise ValueError("--frames must be at least 1")
    return {"type": type_name.strip(), "category": category, "position": position,
            "alignment": alignment, "duration": int(frames)}


def _listed(timeline, track: int, uid) -> Any:
    for item in timeline.GetItemListInTrack("video", track) or []:
        if str(item.GetUniqueId()) == str(uid):
            return item
    return None


def _undo(timeline, transition) -> bool:
    deleter = getattr(timeline, "DeleteClips", None)
    try:
        return bool(callable(deleter) and deleter([transition], False))
    except Exception:  # noqa: BLE001 - report the failed undo, never mask the original problem
        return False


def add_transition(timeline, clip, track: int, type_name: str, category: str = "simple",
                   position: str = "end", alignment: str = "center", frames: int = 24) -> dict[str, Any]:
    options = plan_transition(type_name, category, position, alignment, frames)
    adder = getattr(clip, "AddTransition", None)
    if not callable(adder):
        raise FusionError("TimelineItem.AddTransition is not available (needs Resolve 21.1.2 or later)")
    before = clip_positions(timeline)
    transition = adder(options)
    if transition is None:
        raise RuntimeError(f"Resolve refused AddTransition {options} on {clip.GetName()} (no handles, "
                           "an unknown type or category, or the edge already has one)")
    first = transition_state(transition)
    listed = _listed(timeline, track, first["id"])
    state = transition_state(listed) if listed is not None else first
    corrected = False
    if state["duration"] != options["duration"]:
        setter = getattr(listed or transition, "SetProperty", None)
        if callable(setter) and setter("TransitionDuration", options["duration"]):
            corrected = True
            listed = _listed(timeline, track, first["id"])
            state = transition_state(listed if listed is not None else transition)
    problems = []
    if state["duration"] != options["duration"]:
        problems.append(f"duration {state['duration']} != {options['duration']}")
    shifts = moved(before, clip_positions(timeline))
    problems += [f"clip moved: {s}" for s in shifts]
    # Reported, not enforced: Resolve may name a Fusion or OFX transition differently from the request.
    type_matches = None if state["type"] is None else state["type"] == options["type"]
    result = {"request": options, "clip": str(clip.GetUniqueId()), "track": track,
              "returned": first, "after": state, "listed_in_track": listed is not None,
              "duration_corrected": corrected, "type_matches": type_matches, "clips_moved": shifts}
    if problems:
        result["undone"] = _undo(timeline, listed or transition)
        raise RuntimeError("transition readback failed: " + "; ".join(problems) + f"; {result}")
    result["verified"] = True
    return result


# ---------------------------------------------------------------- clip effects

def parse_effect_assignment(text: str) -> tuple[str | None, str, Any]:
    """``Tool.Input=value`` or ``Input=value`` (the effect's one main tool); JSON values allowed."""
    left, sep, raw = text.partition("=")
    if not sep or not left.strip():
        raise ValueError(f"expected Input=value or Tool.Input=value, got {text!r}")
    tool, dot, inp = left.partition(".")
    if dot:
        if not tool.strip() or not inp.strip():
            raise ValueError(f"expected Tool.Input=value, got {text!r}")
        return tool.strip(), inp.strip(), parse_value(raw)
    return None, left.strip(), parse_value(raw)


def _main_tool(comp) -> str:
    names = []
    for tool in (comp.GetToolList(False) or {}).values():
        attrs = tool.GetAttrs() or {}
        if not str(attrs.get("TOOLS_RegID", "")).startswith(_PLUMBING):
            names.append(attrs.get("TOOLS_Name"))
    if len(names) != 1:
        raise FusionError(f"the effect's comp has {len(names)} tools ({', '.join(map(str, names))}); "
                          "name one as Tool.Input=value")
    return str(names[0])


def effect_state(effect) -> dict[str, Any]:
    info = effect.GetInfo() or {}
    enabled = getattr(effect, "GetIsEnabled", None)
    index = getattr(effect, "GetIndex", None)
    return {"category": info.get("category"), "id": info.get("id"), "name": info.get("name"),
            "enabled": enabled() if callable(enabled) else None, "index": index() if callable(index) else None}


def add_effect(resolve, clip, category: str, effect_id: str,
               assignments: Iterable[tuple[str | None, str, Any]] = ()) -> dict[str, Any]:
    if category not in EFFECT_CATEGORIES:
        raise ValueError(f"--category must be one of {', '.join(EFFECT_CATEGORIES)}")
    assignments = list(assignments)
    if assignments and category != "fusion":
        raise ValueError("--set works on fusion effects only: Resolve has no parameter API for OFX clip "
                         "effects (use `fusion add-tool` to place an OFX tool in a comp and set it there)")
    if not callable(getattr(clip, "AddEffect", None)):
        raise FusionError("TimelineItem.AddEffect is not available (needs Resolve 21.1.2 or later)")
    media = "audio" if category == "audio" else "video"
    known = list_effects(resolve, media, category)
    if known and not any(r["id"] == effect_id for r in known):
        close = [r["id"] for r in known if effect_id.lower() in str(r["id"]).lower()][:5]
        raise ValueError(f"no {category} effect with id {effect_id!r}" + (f"; close: {close}" if close else ""))
    before = [effect_state(e) for e in clip.GetEffects(category) or []]
    effect = clip.AddEffect(category, effect_id)
    if effect is None:
        raise RuntimeError(f"Resolve refused AddEffect({category!r}, {effect_id!r}) on {clip.GetName()}")
    try:
        state = effect_state(effect)
        after = [effect_state(e) for e in clip.GetEffects(category) or []]
        problems = []
        if state["id"] != effect_id:
            problems.append(f"effect id {state['id']!r} != {effect_id!r}")
        if len(after) != len(before) + 1:
            problems.append(f"{len(after)} {category} effects after, expected {len(before) + 1}")
        applied = []
        if assignments and not problems:
            comp = effect.GetFusionComp()
            if comp is None:
                raise FusionError("the fusion effect has no Fusion comp to set")
            resolved = [(tool or _main_tool(comp), inp, value) for tool, inp, value in assignments]
            applied = set_inputs(comp, resolved)
            for row in applied:
                if row["read"] != row["value"] and not _close(row["read"], row["value"]):
                    problems.append(f"{row['tool']}.{row['input']} read {row['read']!r}, set {row['value']!r}")
        if problems:
            raise RuntimeError("effect readback failed: " + "; ".join(problems))
    except Exception as exc:
        undone = bool(clip.DeleteEffects([effect])) if callable(getattr(clip, "DeleteEffects", None)) else False
        raise RuntimeError(f"{exc}; effect removed again: {undone}") from exc
    return {"clip": str(clip.GetUniqueId()), "effect": state, "before": before, "after": after,
            "set": applied, "verified": True}


def _close(a, b) -> bool:
    try:
        return abs(float(a) - float(b)) < 1e-6
    except (TypeError, ValueError):
        return False
