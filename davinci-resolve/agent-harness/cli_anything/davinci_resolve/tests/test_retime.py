"""timeline retime get/set against fakes: speed, retime process, motion estimation, readback."""

import json

import pytest
from click.testing import CliRunner

from cli_anything.davinci_resolve import davinci_resolve_cli as cli_mod
from cli_anything.davinci_resolve.core import retime as rt
from cli_anything.davinci_resolve.core import fusion as fx

START = 108000


class Resolve:
    RETIME_USE_PROJECT, RETIME_NEAREST, RETIME_FRAME_BLEND, RETIME_OPTICAL_FLOW = 0.0, 1.0, 2.0, 3.0
    MOTION_EST_USE_PROJECT, MOTION_EST_STANDARD_FASTER, MOTION_EST_STANDARD_BETTER = 0.0, 1.0, 2.0
    MOTION_EST_ENHANCED_FASTER, MOTION_EST_ENHANCED_BETTER = 3.0, 4.0
    MOTION_EST_SPEED_WARP_FASTER, MOTION_EST_SPEED_WARP_BETTER = 5.0, 6.0


class Item:
    def __init__(self, name, start, end, refuse=(), drift=False):
        self.name, self.start, self.end = name, start, end
        self.speed = {"Percentage": 100.0}
        self.props = {"RetimeProcess": 0.0, "MotionEstimation": 0.0}
        self.refuse, self.drift, self.calls = set(refuse), drift, []

    def GetUniqueId(self): return f"id-{self.name}"
    def GetName(self): return self.name
    def GetStart(self): return self.start
    def GetEnd(self): return self.end
    def GetSpeed(self): return dict(self.speed)
    def GetProperties(self): return dict(self.props)

    def SetSpeed(self, options):
        self.calls.append(("SetSpeed", dict(options)))
        if "SetSpeed" in self.refuse:
            return False
        self.speed["Percentage"] = options["Percentage"] + (1.0 if self.drift else 0.0)
        return True

    def SetProperties(self, props):
        self.calls.append(("SetProperties", dict(props)))
        if "SetProperties" in self.refuse:
            return False
        self.props.update(props)
        return True


class OldItem(Item):
    """An older build: SetProperty/GetProperty only."""
    GetProperties = None
    SetProperties = None

    def GetProperty(self, key): return self.props[key]

    def SetProperty(self, key, value):
        self.calls.append(("SetProperty", key, value))
        self.props[key] = value
        return True


class Timeline:
    def __init__(self, items):
        self.items = items

    def GetName(self): return "S06-C01 class"
    def GetTrackCount(self, kind): return 2
    def GetItemListInTrack(self, kind, track): return self.items if track == 1 else []


def test_find_item_by_index_id_and_record_frame():
    a, b = Item("a", START, START + 100), Item("b", START + 100, START + 200)
    tl = Timeline([a, b])
    assert rt.find_item(tl, 1, "2") is b
    assert rt.find_item(tl, 1, "id:id-a") is a
    assert rt.find_item(tl, 1, f"@{START + 150}") is b
    for bad in ("3", "id:none", f"@{START + 999}"):
        with pytest.raises(ValueError):
            rt.find_item(tl, 1, bad)
    with pytest.raises(ValueError):
        rt.find_item(tl, 5, "1")


def test_speed_warp_quarter_speed_is_applied_and_read_back():
    item = Item("slow", START, START + 34)
    out = rt.apply(Resolve(), item, speed=25, process="optical-flow", motion="speed-warp-better")
    assert out["verified"] is True
    assert out["after"]["speed"] == 25 and out["after"]["retime_process"] == "optical-flow"
    assert out["after"]["motion_estimation"] == "speed-warp-better"
    assert ("SetSpeed", {"Percentage": 25.0, "RippleTimeline": False}) in item.calls
    assert out["before"]["speed"] == 100


def test_speed_warp_without_optical_flow_is_refused_before_any_call():
    item = Item("x", START, START + 10)
    with pytest.raises(ValueError, match="optical-flow"):
        rt.apply(Resolve(), item, motion="speed-warp-better")
    assert item.calls == []


def test_speed_warp_allowed_when_item_already_uses_optical_flow():
    item = Item("x", START, START + 10)
    item.props["RetimeProcess"] = Resolve.RETIME_OPTICAL_FLOW
    assert rt.apply(Resolve(), item, motion="speed-warp-faster")["after"]["motion_estimation"] == "speed-warp-faster"


def test_refusal_and_readback_mismatch_raise():
    with pytest.raises(RuntimeError, match="refused SetSpeed"):
        rt.apply(Resolve(), Item("x", START, START + 10, refuse={"SetSpeed"}), speed=50)
    with pytest.raises(RuntimeError, match="readback mismatch"):
        rt.apply(Resolve(), Item("x", START, START + 10, drift=True), speed=50)


def test_bad_requests_are_refused():
    before = {"retime_process": "project"}
    for kwargs in ({}, {"speed": -1}, {"process": "magic"}, {"motion": "turbo"}):
        with pytest.raises(ValueError):
            rt.plan(before, kwargs.get("speed"), kwargs.get("process"), kwargs.get("motion"))


def test_older_single_property_api_works():
    item = OldItem("old", START, START + 10)
    out = rt.apply(Resolve(), item, process="optical-flow", motion="speed-warp-better")
    assert out["after"]["motion_estimation"] == "speed-warp-better"
    assert ("SetProperty", "RetimeProcess", 3.0) in item.calls


def test_missing_constant_names_the_version():
    class Bare:
        pass
    item = Item("x", START, START + 10)
    with pytest.raises(fx.FusionError, match="21.2"):
        rt.apply(Bare(), item, process="optical-flow")


# ---------------------------------------------------------------- CLI

@pytest.fixture
def fake(monkeypatch):
    item = Item("decisive", START, START + 34)
    tl = Timeline([item])

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
    return item, project


def run(*args):
    return CliRunner().invoke(cli_mod.cli, ["--json", *args])


def test_cli_help_lists_retime():
    r = CliRunner().invoke(cli_mod.cli, ["timeline", "retime", "--help"])
    assert r.exit_code == 0 and "get" in r.output and "set" in r.output


def test_cli_get_and_set_with_save(fake):
    item, project = fake
    r = run("timeline", "retime", "get", "--track", "1", "--item", "1")
    assert r.exit_code == 0, r.output
    assert json.loads(r.output)["speed"] == 100
    r = run("timeline", "retime", "set", "--track", "1", "--item", "1", "--speed", "25",
            "--process", "optical-flow", "--motion", "speed-warp-better")
    assert r.exit_code == 0, r.output
    body = json.loads(r.output)
    assert body["result"]["verified"] is True and body["result"]["after"]["speed"] == 25
    assert project.saved == 1


def test_cli_dry_run_changes_nothing(fake):
    item, project = fake
    r = CliRunner().invoke(cli_mod.cli, ["--json", "--dry-run", "timeline", "retime", "set", "--track", "1",
                                         "--item", "1", "--speed", "25"])
    assert r.exit_code == 0 and json.loads(r.output)["dry_run"] is True
    assert item.calls == [] and project.saved == 0


def test_cli_speed_warp_without_optical_flow_exits_1(fake):
    item, _ = fake
    r = run("timeline", "retime", "set", "--track", "1", "--item", "1", "--motion", "speed-warp-better")
    assert r.exit_code == 1 and "optical-flow" in r.output and item.calls == []
