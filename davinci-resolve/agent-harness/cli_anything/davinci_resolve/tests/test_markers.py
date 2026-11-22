"""Timeline size and frame rate, markers and cue sheet import against fakes: every value read back."""

import json

import pytest
from click.testing import CliRunner

from cli_anything.davinci_resolve import davinci_resolve_cli as cli_mod
from cli_anything.davinci_resolve.core import markers as mk

START = 108000


class Timeline:
    def __init__(self, name="T", fps=24.0, refuse=(), drop_custom=False, garble_name=False):
        self.name = name
        self.settings = {"useCustomSettings": "0", "timelineResolutionWidth": "1920",
                         "timelineResolutionHeight": "1080", "timelineFrameRate": fps}
        self.markers = {}
        self.refuse = set(refuse)
        self.drop_custom = drop_custom
        self.garble_name = garble_name

    def GetName(self): return self.name
    def GetUniqueId(self): return f"tl-{self.name}"
    def GetStartFrame(self): return START
    def GetSettings(self): return dict(self.settings)

    def SetSettings(self, settings):
        ok = True
        order = ["useCustomSettings"] + [k for k in settings if k != "useCustomSettings"]
        for key in order:
            if key not in settings:
                continue
            if key in self.refuse or (key != "useCustomSettings" and self.settings["useCustomSettings"] != "1"):
                ok = False
                continue
            value = settings[key]
            self.settings[key] = float(str(value).split()[0]) if key == "timelineFrameRate" else value
        return ok

    def AddMarker(self, frame, color, name, note, duration, custom=None):
        if frame in self.markers:
            return False
        self.markers[frame] = {"color": color, "name": name + ("!" if self.garble_name else ""), "note": note,
                               "duration": duration, "customData": "" if self.drop_custom else (custom or "")}
        return True

    def GetMarkers(self): return {k: dict(v) for k, v in self.markers.items()}

    def DeleteMarkerAtFrame(self, frame):
        return self.markers.pop(frame, None) is not None


SHEET = {"cut": "site", "fps": 30, "totalFrames": 1963,
         "beats": [{"kind": "gap", "startFrame": 12, "endFrame": 265}, {"kind": "why", "startFrame": 265, "endFrame": 509}],
         "cues": [{"name": "Your operation", "phrase": "Your operation", "frame": 13},
                  {"name": "From $1,500", "phrase": "From $1,500", "frame": 1598}]}


# ---------------------------------------------------------------- settings

def test_fps_strings_and_bad_values():
    assert mk.parse_fps("30") == ("30", 30.0)
    assert mk.parse_fps("29.97 df") == ("29.97 DF", 29.97)
    for bad in ("thirty", "0", "500"):
        with pytest.raises(ValueError):
            mk.parse_fps(bad)


def test_plan_settings_puts_custom_first_and_pairs_size():
    assert mk.plan_settings(None, None, None) == {}
    s = mk.plan_settings(1080, 1350, "30")
    assert next(iter(s)) == "useCustomSettings" and s["timelineResolutionWidth"] == "1080" and s["timelineFrameRate"] == "30"
    with pytest.raises(ValueError):
        mk.plan_settings(1080, None, None)


def test_apply_settings_reads_back_and_reports_a_refused_value():
    tl = Timeline()
    out = mk.apply_settings(tl, mk.plan_settings(1080, 1350, "30"))
    assert out["verified"] and tl.settings["timelineResolutionHeight"] == "1350" and tl.settings["timelineFrameRate"] == 30.0
    with pytest.raises(RuntimeError, match="timelineFrameRate read 24.0"):
        mk.apply_settings(Timeline(refuse={"timelineFrameRate"}), mk.plan_settings(None, None, "30"))


# ---------------------------------------------------------------- markers

def test_add_marker_reads_back_and_refuses_a_taken_frame():
    tl = Timeline()
    out = mk.add_marker(tl, 30, "cue", "Red", "note", 2, "cue:x")
    assert out["verified"] and out["record_frame"] == START + 30 and tl.markers[30]["color"] == "Red"
    with pytest.raises(RuntimeError, match="already has a marker"):
        mk.add_marker(tl, 30, "again")


def test_a_marker_that_reads_back_wrong_is_removed_again():
    tl = Timeline(garble_name=True)
    with pytest.raises(RuntimeError, match="removed again: True"):
        mk.add_marker(tl, 5, "cue")
    assert tl.markers == {}


def test_plan_marker_validates():
    for args in ((-1, "x"), (0, "x", "Orange"), (0, "x", "Blue", "", 0), (0, " ")):
        with pytest.raises(ValueError):
            mk.plan_marker(*args)


def test_list_markers_sorted_with_record_frames():
    tl = Timeline()
    mk.add_marker(tl, 90, "b")
    mk.add_marker(tl, 10, "a")
    assert [(r["frame"], r["record_frame"], r["name"]) for r in mk.list_markers(tl)] == [(10, START + 10, "a"), (90, START + 90, "b")]


# ---------------------------------------------------------------- cue sheets

def test_import_cues_needs_matching_fps_then_marks_every_cue():
    with pytest.raises(RuntimeError, match="30 fps, timeline is 24.0"):
        mk.import_cues(Timeline(fps=24.0), SHEET)
    tl = Timeline(fps=30.0)
    out = mk.import_cues(tl, SHEET, beats=True)
    assert out["added"] == 4 and sorted(tl.markers) == [12, 13, 265, 1598]
    assert tl.markers[12]["duration"] == 253 and tl.markers[1598]["customData"] == "cue:site:From $1,500"
    assert mk.compare(tl, SHEET) == []


def test_import_refuses_clashes_unless_replacing_earlier_cue_markers():
    tl = Timeline(fps=30.0)
    mk.import_cues(tl, SHEET)
    with pytest.raises(RuntimeError, match="already carry markers"):
        mk.import_cues(tl, SHEET)
    mk.add_marker(tl, 500, "editor's note")
    out = mk.import_cues(tl, SHEET, replace=True)
    assert out["removed"] == 2 and 500 in tl.markers and sorted(tl.markers) == [13, 500, 1598]


def test_a_failed_import_takes_its_own_markers_back_out():
    tl = Timeline(fps=30.0, garble_name=True)
    with pytest.raises(RuntimeError, match="removed again"):
        mk.import_cues(tl, SHEET)
    assert tl.markers == {}


def test_two_cues_on_one_frame_are_refused():
    sheet = {**SHEET, "cues": SHEET["cues"] + [{"name": "dup", "frame": 13}]}
    with pytest.raises(ValueError, match="two markers on frame 13"):
        mk.plan_cues(sheet)


# ---------------------------------------------------------------- CLI

@pytest.fixture
def fake(monkeypatch):
    state = {"saved": 0, "deleted": []}
    tl = Timeline(name="DI site", fps=30.0)

    class Pool:
        def CreateEmptyTimeline(self, name):
            state["created"] = Timeline(name=name, fps=24.0, refuse=state.get("refuse", ()))
            return state["created"]

        def DeleteTimelines(self, timelines):
            state["deleted"] += [t.GetName() for t in timelines]
            return True

    class Project:
        def GetName(self): return "DI Offer Film scratch"
        def GetCurrentTimeline(self): return tl
        def GetMediaPool(self): return Pool()

    project = Project()

    class PM:
        def GetCurrentProject(self): return project

        def SaveProject(self):
            state["saved"] += 1
            return True

    class Resolve:
        def GetProjectManager(self): return PM()

    monkeypatch.setattr(cli_mod.backend, "connect", lambda: Resolve())
    return tl, state


def run(*args):
    return CliRunner().invoke(cli_mod.cli, ["--json", *args])


def test_help_lists_marker_commands():
    r = CliRunner().invoke(cli_mod.cli, ["timeline", "marker", "--help"])
    assert r.exit_code == 0 and all(c in r.output for c in ("add", "list", "import", "check"))
    r = CliRunner().invoke(cli_mod.cli, ["timeline", "create", "--help"])
    assert all(o in r.output for o in ("--width", "--height", "--fps"))


def test_cli_create_sized_timeline_reads_back(fake):
    _, state = fake
    r = run("timeline", "create", "DI 4x5", "--width", "1080", "--height", "1350", "--fps", "30")
    assert r.exit_code == 0, r.output
    out = json.loads(r.output)["result"]
    assert out["settings"]["after"]["timelineResolutionWidth"] == "1080" and out["settings"]["after"]["timelineFrameRate"] == 30.0
    assert state["saved"] == 1


def test_cli_create_removes_a_timeline_whose_settings_fail(fake):
    _, state = fake
    state["refuse"] = {"timelineFrameRate"}
    r = run("timeline", "create", "DI bad", "--fps", "30")
    assert r.exit_code == 1 and "removed again: True" in r.output and state["deleted"] == ["DI bad"]


def test_cli_fps_with_clips_is_refused_before_resolve(fake):
    r = run("timeline", "create", "x", "--clip", "a.mov", "--fps", "30")
    assert r.exit_code == 1 and "empty timeline" in r.output


def test_cli_marker_import_list_check(fake, tmp_path):
    _, state = fake
    sheet = tmp_path / "cue-sheet.site.json"
    sheet.write_text(json.dumps(SHEET), encoding="utf-8")
    assert run("timeline", "marker", "check", str(sheet)).exit_code == 1
    r = run("timeline", "marker", "import", str(sheet))
    assert r.exit_code == 0, r.output
    assert state["saved"] == 1
    listed = json.loads(run("timeline", "marker", "list").output)
    assert [m["frame"] for m in listed["markers"]] == [13, 1598] and listed["start_frame"] == START
    assert run("timeline", "marker", "check", str(sheet)).exit_code == 0
    assert state["saved"] == 1  # list and check never save


def test_cli_dry_run_adds_nothing(fake, tmp_path):
    tl, state = fake
    r = CliRunner().invoke(cli_mod.cli, ["--json", "--dry-run", "timeline", "marker", "add", "--frame", "5", "--name", "x"])
    assert r.exit_code == 0 and json.loads(r.output)["dry_run"] and tl.markers == {} and state["saved"] == 0


def test_cli_rejects_a_file_that_is_not_a_cue_sheet(fake, tmp_path):
    bad = tmp_path / "x.json"
    bad.write_text("{}", encoding="utf-8")
    r = run("timeline", "marker", "import", str(bad))
    assert r.exit_code == 1 and "not a cue sheet" in r.output
