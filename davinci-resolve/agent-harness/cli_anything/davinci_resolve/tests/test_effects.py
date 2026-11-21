"""effects list, transition add and effect add against fakes: readback, the 1 frame bug, moved clips."""

import json

import pytest
from click.testing import CliRunner

from cli_anything.davinci_resolve import davinci_resolve_cli as cli_mod
from cli_anything.davinci_resolve.core import effects as ef
from cli_anything.davinci_resolve.core import fusion as fx

START = 108000

EFFECTS = [
    {"category": "fusion", "id": "Fusion.Film Burn", "name": "Film Burn"},
    {"category": "fusion", "id": "Fusion.Glass Card", "name": "Glass Card"},
    {"category": "ofx", "id": "com.borisfx.bcc_dft.ofx.filmGlow", "name": "BCC Film Glow"},
    {"category": "audio", "id": "Audio.Deesser", "name": "De-Esser"},
]


class Resolve:
    def __init__(self, effects=EFFECTS):
        self.effects = effects

    def GetEffectsList(self, media_type="video"):
        return [e for e in self.effects if (e["category"] == "audio") == (media_type == "audio")]


class Transition:
    def __init__(self, timeline, track, start, end, options, stuck=False):
        self.timeline, self.track, self.start, self.end = timeline, track, start, end
        self.props = {"TransitionType": options["type"], "TransitionCategory": options["category"],
                      "TransitionAlignment": options["alignment"], "TransitionDuration": end - start}
        self.stuck = stuck

    def GetUniqueId(self): return f"tr-{self.start}"
    def GetType(self): return "transition"
    def GetName(self): return self.props["TransitionType"]
    def GetStart(self): return self.start
    def GetEnd(self): return self.end
    def GetDuration(self): return self.end - self.start
    def GetProperty(self, key): return self.props.get(key)

    def SetProperty(self, key, value):
        if key == "TransitionDuration" and not self.stuck:
            mid = (self.start + self.end) // 2
            self.start, self.end = mid - value // 2, mid - value // 2 + value
            self.props[key] = value
            return True
        return False


class Effect:
    def __init__(self, category, effect_id, name, comp=None):
        self.info = {"category": category, "id": effect_id, "name": name}
        self.comp = comp

    def GetInfo(self): return dict(self.info)
    def GetIsEnabled(self): return True
    def GetIndex(self): return 1
    def GetFusionComp(self): return self.comp


class Tool:
    def __init__(self, name, reg, clamp=None):
        self.name, self.reg, self.inputs, self.clamp = name, reg, {}, clamp

    def GetAttrs(self): return {"TOOLS_Name": self.name, "TOOLS_RegID": self.reg}
    def SetInput(self, key, value): self.inputs[key] = self.clamp(value) if self.clamp else value
    def GetInput(self, key): return self.inputs.get(key)


class Comp:
    def __init__(self, tools):
        self.tools = {t.name: t for t in tools}
        self.locked = 0

    def GetToolList(self, selected=False): return dict(enumerate(self.tools.values(), 1))
    def FindTool(self, name): return self.tools.get(name)
    def Lock(self): self.locked += 1
    def Unlock(self): self.locked -= 1


class Clip:
    def __init__(self, timeline, track, name, start, end, transition_bug=None, refuse=False, effect_comp=None):
        self.timeline, self.track, self.name, self.start, self.end = timeline, track, name, start, end
        self.transition_bug, self.refuse, self.effect_comp = transition_bug, refuse, effect_comp
        self.effects, self.calls = [], []

    def GetUniqueId(self): return f"id-{self.name}"
    def GetType(self): return "video"
    def GetName(self): return self.name
    def GetStart(self): return self.start
    def GetEnd(self): return self.end
    def GetDuration(self): return self.end - self.start

    def AddTransition(self, options):
        self.calls.append(("AddTransition", dict(options)))
        if self.refuse:
            return None
        n = 1 if self.transition_bug in ("one-frame", "stuck") else options["duration"]
        edge = self.end if options["position"] == "end" else self.start
        t = Transition(self.timeline, self.track, edge - n // 2, edge - n // 2 + n, options,
                       stuck=self.transition_bug == "stuck")
        self.timeline.tracks[self.track].append(t)
        if self.transition_bug == "ripple":
            for item in self.timeline.tracks[self.track]:
                if item is not self and item is not t and item.start >= self.end:
                    item.start, item.end = item.start + 5, item.end + 5
        return t

    def AddEffect(self, category, effect_id):
        self.calls.append(("AddEffect", category, effect_id))
        if self.refuse:
            return None
        name = next(e["name"] for e in EFFECTS if e["id"] == effect_id)
        effect = Effect(category, effect_id, name, self.effect_comp if category == "fusion" else None)
        self.effects.append(effect)
        return effect

    def GetEffects(self, category): return [e for e in self.effects if e.info["category"] == category]

    def DeleteEffects(self, effects):
        self.effects = [e for e in self.effects if e not in effects]
        return True


class Timeline:
    def __init__(self):
        self.tracks = {1: [], 2: []}
        self.deleted = []

    def GetName(self): return "probe"
    def GetSetting(self, key): return "30"
    def GetTrackCount(self, kind): return len(self.tracks) if kind == "video" else 0
    def GetItemListInTrack(self, kind, track): return list(self.tracks.get(track, []))

    def DeleteClips(self, items, ripple=False):
        for item in items:
            for track in self.tracks.values():
                if item in track:
                    track.remove(item)
                    self.deleted.append(item)
        return True


def two_clips(track=2, **kw):
    tl = Timeline()
    a = Clip(tl, track, "a", START, START + 60, **kw)
    b = Clip(tl, track, "b", START + 60, START + 120)
    tl.tracks[track] += [a, b]
    return tl, a, b


# ---------------------------------------------------------------- core

def test_effects_list_filters_by_category_and_match():
    r = Resolve()
    assert [e["id"] for e in ef.list_effects(r, category="ofx")] == ["com.borisfx.bcc_dft.ofx.filmGlow"]
    assert {e["name"] for e in ef.list_effects(r, match="FILM")} == {"Film Burn", "BCC Film Glow"}
    assert [e["name"] for e in ef.list_effects(r, "audio")] == ["De-Esser"]
    with pytest.raises(ValueError):
        ef.list_effects(r, category="simple")
    with pytest.raises(fx.FusionError, match="21.1.2"):
        ef.list_effects(object())


def test_find_clip_skips_transitions_and_names_the_track():
    tl, a, b = two_clips()
    ef.add_transition(tl, a, 2, "Cross Dissolve", frames=24)
    assert ef.find_clip(tl, f"V2@{START + 59}") == (a, 2)  # the transition also covers this frame
    assert ef.find_clip(tl, "id-b") == (b, 2)
    for bad in (f"V2@{START + 500}", "V9@0", "id-none"):
        with pytest.raises(fx.FusionError):
            ef.find_clip(tl, bad)


def test_transition_is_read_back_from_the_track_and_no_clip_moves():
    tl, a, b = two_clips()
    out = ef.add_transition(tl, a, 2, "Cross Dissolve", "simple", "end", "center", 24)
    assert out["verified"] is True and out["listed_in_track"] is True
    assert out["after"]["duration"] == 24 and out["after"]["start"] == START + 48
    assert out["after"]["end"] == START + 72 and out["type_matches"] is True
    assert out["clips_moved"] == [] and out["duration_corrected"] is False
    assert a.calls[0] == ("AddTransition", {"type": "Cross Dissolve", "category": "simple", "position": "end",
                                            "alignment": "center", "duration": 24})
    assert (a.start, a.end, b.start, b.end) == (START, START + 60, START + 60, START + 120)


def test_one_frame_bug_is_corrected_once_and_reported():
    tl, a, _ = two_clips(transition_bug="one-frame")
    out = ef.add_transition(tl, a, 2, "Slice Hard", "fusion", frames=12)
    assert out["returned"]["duration"] == 1 and out["after"]["duration"] == 12
    assert out["duration_corrected"] is True and out["verified"] is True


def test_a_duration_that_cannot_be_fixed_is_undone():
    tl, a, _ = two_clips(transition_bug="stuck")
    with pytest.raises(RuntimeError, match="duration 1 != 18") as err:
        ef.add_transition(tl, a, 2, "BCC Film Glow Dissolve", "ofx", frames=18)
    assert "'undone': True" in str(err.value)
    assert all(i.GetType() != "transition" for i in tl.tracks[2])


def test_a_transition_that_moves_a_clip_is_undone():
    tl, a, b = two_clips(transition_bug="ripple")
    with pytest.raises(RuntimeError, match="clip moved: id-b"):
        ef.add_transition(tl, a, 2, "Cross Dissolve", frames=24)
    assert len(tl.deleted) == 1


def test_refused_and_bad_transitions():
    tl, a, _ = two_clips(refuse=True)
    with pytest.raises(RuntimeError, match="refused AddTransition"):
        ef.add_transition(tl, a, 2, "Cross Dissolve", frames=24)
    for kw in ({"category": "magic"}, {"position": "middle"}, {"alignment": "up"}, {"frames": 0}, {"type_name": " "}):
        args = {"type_name": "Cross Dissolve", "category": "simple", "position": "end", "alignment": "center",
                "frames": 24, **kw}
        with pytest.raises(ValueError):
            ef.plan_transition(**args)


def test_fusion_effect_is_added_and_its_one_tool_set_and_read_back():
    comp = Comp([Tool("MediaIn1", "MediaIn"), Tool("FilmBurn", "Fuse.FilmBurn"), Tool("MediaOut1", "MediaOut")])
    tl, a, _ = two_clips(effect_comp=comp)
    sets = [ef.parse_effect_assignment(s) for s in ("Blend=0.6", 'FilmBurn.Center={"1":0.5,"2":0.4}')]
    out = ef.add_effect(Resolve(), a, "fusion", "Fusion.Film Burn", sets)
    assert out["verified"] is True and out["effect"]["name"] == "Film Burn"
    assert len(out["after"]) == 1 and out["before"] == []
    assert comp.tools["FilmBurn"].inputs == {"Blend": 0.6, "Center": {1: 0.5, 2: 0.4}}


def test_a_set_that_reads_back_differently_removes_the_effect():
    comp = Comp([Tool("FilmBurn", "Fuse.FilmBurn", clamp=lambda v: min(v, 1.0))])
    tl, a, _ = two_clips(effect_comp=comp)
    with pytest.raises(RuntimeError, match="read 1.0, set 3"):
        ef.add_effect(Resolve(), a, "fusion", "Fusion.Film Burn", [ef.parse_effect_assignment("Blend=3")])
    assert a.effects == []


def test_effect_requests_that_are_refused_before_any_call():
    tl, a, _ = two_clips()
    with pytest.raises(ValueError, match="no fusion effect"):
        ef.add_effect(Resolve(), a, "fusion", "Fusion.Nope")
    with pytest.raises(ValueError, match="fusion effects only"):
        ef.add_effect(Resolve(), a, "ofx", "com.borisfx.bcc_dft.ofx.filmGlow", [(None, "Blend", 1)])
    with pytest.raises(ValueError):
        ef.add_effect(Resolve(), a, "simple", "x")
    assert a.calls == []


def test_an_ambiguous_bare_input_names_the_tools():
    comp = Comp([Tool("Glass", "Fuse.Glass"), Tool("Shadow", "Fuse.Shadow")])
    tl, a, _ = two_clips(effect_comp=comp)
    with pytest.raises(RuntimeError, match="2 tools"):
        ef.add_effect(Resolve(), a, "fusion", "Fusion.Glass Card", [(None, "Blend", 1)])
    assert a.effects == []


# ---------------------------------------------------------------- CLI

@pytest.fixture
def fake(monkeypatch):
    comp = Comp([Tool("FilmBurn", "Fuse.FilmBurn")])
    tl, a, b = two_clips(effect_comp=comp)
    v1 = Clip(tl, 1, "v1", START, START + 120)
    tl.tracks[1].append(v1)

    class Project:
        saved = 0
        def GetCurrentTimeline(self): return tl
        def GetTimelineCount(self): return 1
        def GetTimelineByIndex(self, i): return tl

    project = Project()

    class PM:
        def GetCurrentProject(self): return project
        def SaveProject(self):
            project.saved += 1
            return True

    class R(Resolve):
        def GetProjectManager(self): return PM()

    monkeypatch.setattr(cli_mod.backend, "connect", lambda: R())
    return tl, a, v1, project


def run(*args):
    return CliRunner().invoke(cli_mod.cli, ["--json", *args])


def test_cli_help_lists_the_new_groups():
    for group in ("effects", "transition", "effect"):
        r = CliRunner().invoke(cli_mod.cli, [group, "--help"])
        assert r.exit_code == 0, r.output


def test_cli_effects_list(fake):
    r = run("effects", "list", "--category", "fusion", "--match", "glass")
    assert r.exit_code == 0, r.output
    assert json.loads(r.output)["effects"] == [{"category": "fusion", "id": "Fusion.Glass Card", "name": "Glass Card"}]


def test_cli_transition_add_reads_back_and_saves(fake):
    tl, a, _, project = fake
    r = run("transition", "add", f"V2@{START + 10}", "--type", "Cross Dissolve", "--frames", "24")
    assert r.exit_code == 0, r.output
    body = json.loads(r.output)
    assert body["result"]["verified"] is True and body["result"]["after"]["duration"] == 24
    assert project.saved == 1


def test_cli_dry_run_and_v1_guard(fake):
    tl, a, v1, project = fake
    r = CliRunner().invoke(cli_mod.cli, ["--json", "--dry-run", "transition", "add", f"V2@{START}",
                                         "--type", "Cross Dissolve", "--frames", "12"])
    assert r.exit_code == 0 and json.loads(r.output)["dry_run"] is True
    assert a.calls == [] and project.saved == 0
    r = run("transition", "add", f"V1@{START}", "--type", "Cross Dissolve", "--frames", "12")
    assert r.exit_code == 1 and "--allow-v1" in r.output and v1.calls == []
    r = run("effect", "add", f"V1@{START}", "--category", "fusion", "--id", "Fusion.Film Burn")
    assert r.exit_code == 1 and v1.calls == []


def test_cli_effect_add_with_set(fake):
    tl, a, _, project = fake
    r = run("effect", "add", "id-a", "--category", "fusion", "--id", "Fusion.Film Burn", "--set", "Blend=0.5")
    assert r.exit_code == 0, r.output
    assert json.loads(r.output)["result"]["set"][0]["read"] == 0.5 and project.saved == 1
