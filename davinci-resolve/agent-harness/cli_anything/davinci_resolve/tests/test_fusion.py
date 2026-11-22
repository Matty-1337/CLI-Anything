"""Fusion title commands against fake Resolve objects (no Resolve needed)."""

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from cli_anything.davinci_resolve import davinci_resolve_cli as cli_mod
from cli_anything.davinci_resolve.core import fusion as fx


# ---------------------------------------------------------------- fakes

class FakeInput:
    def __init__(self, ident, name):
        self._a = {"INPS_ID": ident, "INPS_Name": name}

    def GetAttrs(self):
        return self._a


class FakeTool:
    def __init__(self, name, reg="TextPlus"):
        self.name, self.reg, self.values = name, reg, {"StyledText": "Sample"}

    def GetAttrs(self):
        return {"TOOLS_Name": self.name, "TOOLS_RegID": self.reg}

    def SetInput(self, ident, value):
        self.values[ident] = value  # Fusion returns None here too

    def GetInput(self, ident):
        return self.values.get(ident)

    def GetInputList(self):
        return {1: FakeInput("StyledText", "Styled Text")}


class FakeComp:
    def __init__(self, tools=("mainText", "secondaryText"), global_end=1799.0):
        self.tools = {n: FakeTool(n) for n in tools}
        self.attrs = {"COMPN_GlobalStart": 0.0, "COMPN_GlobalEnd": global_end}
        self.locks = 0

    def FindTool(self, name):
        return self.tools.get(name)

    def GetToolList(self, selected):
        return dict(enumerate(self.tools.values(), 1))

    def SetAttrs(self, attrs):
        self.attrs.update(attrs)

    def GetAttrs(self):
        return self.attrs

    def Lock(self):
        self.locks += 1

    def Unlock(self):
        self.locks -= 1


class FakeItem:
    _next = 0

    def __init__(self, name, start, end, track, comp=None):
        FakeItem._next += 1
        self.id, self.name, self.start, self.end, self.track = f"item{FakeItem._next}", name, start, end, track
        self.comps = [comp] if comp else []
        self.exported = []

    def GetUniqueId(self): return self.id
    def GetName(self): return self.name
    def GetStart(self): return self.start
    def GetEnd(self): return self.end
    def GetDuration(self): return self.end - self.start
    def GetTrackTypeAndIndex(self): return ["video", self.track]
    def GetFusionCompCount(self): return len(self.comps)
    def GetFusionCompByIndex(self, i): return self.comps[i - 1] if 0 < i <= len(self.comps) else None

    def ImportFusionComp(self, path):
        if "broken" in path:
            return None
        comp = FakeComp()
        self.comps.append(comp)
        return comp

    def ExportFusionComp(self, path, index):
        self.exported.append((path, index))
        Path(path).write_text("comp", encoding="utf-8")
        return True


class FakeTimeline:
    def __init__(self, name="Episode", tracks=1, fps=30):
        self.name, self.tracks, self.fps = name, {i: [] for i in range(1, tracks + 1)}, fps
        self.title_inserts = []
        self.deleted = []

    def GetName(self): return self.name
    def GetUniqueId(self): return f"tl-{self.name}"
    def GetSetting(self, key): return str(self.fps) if key == "timelineFrameRate" else None
    def GetTrackCount(self, kind): return len(self.tracks) if kind == "video" else 0
    def GetItemListInTrack(self, kind, i): return list(self.tracks.get(i, []))
    def GetStartTimecode(self): return "01:00:00:00"
    def SetCurrentTimecode(self, tc): return True

    def AddTrack(self, kind):
        self.tracks[len(self.tracks) + 1] = []
        return True

    def DeleteClips(self, items, ripple=False):
        for item in items:
            self.tracks[item.track].remove(item)
            self.deleted.append(item)
        return True

    def InsertFusionTitleIntoTimeline(self, name):
        self.title_inserts.append(name)
        if name == "Missing":
            return None
        item = FakeItem(name, 108000, 108150, 1, FakeComp(tools=("mainText",), global_end=149.0))
        self.tracks[1].append(item)
        return item


class FakeClip:
    def __init__(self, path, frames=1800):
        self.path, self.frames = path, frames

    def GetName(self): return Path(self.path).name
    def GetUniqueId(self): return "clip-" + Path(self.path).name

    def GetClipProperty(self, key=None):
        props = {"File Path": self.path, "Frames": str(self.frames)}
        return props if key is None else props.get(key)


class FakeFolder:
    def __init__(self, name):
        self.name, self.clips, self.subs = name, [], []

    def GetName(self): return self.name
    def GetClipList(self): return self.clips
    def GetSubFolderList(self): return self.subs


class FakePool:
    def __init__(self, project, carrier_frames=1800):
        self.project, self.root, self.current = project, FakeFolder("Master"), None
        self.appends, self.carrier_frames, self.deleted_timelines = [], carrier_frames, []

    def GetRootFolder(self): return self.root
    def GetCurrentFolder(self): return self.current or self.root
    def SetCurrentFolder(self, folder): self.current = folder; return True

    def AddSubFolder(self, parent, name):
        folder = FakeFolder(name)
        parent.subs.append(folder)
        return folder

    def ImportMedia(self, paths):
        clip = FakeClip(paths[0], self.carrier_frames)
        self.GetCurrentFolder().clips.append(clip)
        return [clip]

    def AppendToTimeline(self, infos):
        self.appends.append(infos)
        tl = self.project.GetCurrentTimeline() if self.project.target is None else self.project.target
        out = []
        for info in infos:
            start = info["recordFrame"]
            item = FakeItem(info["mediaPoolItem"].GetName(), start, start + info["endFrame"] - info["startFrame"], info["trackIndex"])
            tl.tracks[info["trackIndex"]].append(item)
            out.append(item)
        return out

    def CreateEmptyTimeline(self, name):
        if any(t.GetName() == name for t in self.project.timelines):
            return None
        tl = FakeTimeline(name)
        self.project.timelines.append(tl)
        return tl

    def DeleteTimelines(self, timelines):
        for tl in timelines:
            self.project.timelines.remove(tl)
            self.deleted_timelines.append(tl.GetName())
        return True


class FakeProject:
    def __init__(self, carrier_frames=1800):
        self.timelines = [FakeTimeline("Episode", tracks=1)]
        self.current = self.timelines[0]
        self.target = None
        self.pool = FakePool(self, carrier_frames)

    def GetName(self): return "STG test"
    def GetMediaPool(self): return self.pool
    def GetCurrentTimeline(self): return self.current
    def SetCurrentTimeline(self, tl): self.current = tl; return True
    def GetTimelineCount(self): return len(self.timelines)
    def GetTimelineByIndex(self, i): return self.timelines[i - 1]


@pytest.fixture
def carrier(tmp_path):
    path = tmp_path / "carrier.mov"
    path.write_bytes(b"mov")
    return path


@pytest.fixture
def comp_file(tmp_path):
    path = tmp_path / "lower.comp"
    path.write_text("comp", encoding="utf-8")
    return path


# ---------------------------------------------------------------- parsing

def test_parse_frame_accepts_frames_and_timecode():
    tl = FakeTimeline(fps=30)
    assert fx.parse_frame("108030", tl) == 108030
    assert fx.parse_frame(42, tl) == 42
    assert fx.parse_frame("01:00:01:00", tl) == 108030
    with pytest.raises(ValueError):
        fx.parse_frame("one minute", tl)


def test_parse_assignment_json_points_and_strings():
    assert fx.parse_assignment('Transform1.Center={"1": 0.5, "2": 0.9}') == ("Transform1", "Center", {1: 0.5, 2: 0.9})
    assert fx.parse_assignment("NameLine.Size=0.06") == ("NameLine", "Size", 0.06)
    assert fx.parse_assignment("NameLine.Font=Fraunces") == ("NameLine", "Font", "Fraunces")
    with pytest.raises(ValueError):
        fx.parse_assignment("NameLine=nope")


def test_parse_text_never_json_parses():
    assert fx.parse_text("QuestionText=42") == ("QuestionText", "StyledText", "42")
    assert fx.parse_text("NameLine=Matty = host") == ("NameLine", "StyledText", "Matty = host")


# ---------------------------------------------------------------- place

def test_place_puts_carrier_on_track_fits_range_and_sets_text(carrier, comp_file):
    project = FakeProject()
    tl = project.current
    result = fx.place(project, tl, comp_file, track=3, record_frame=108030, frames=210,
                      assignments=[("mainText", "StyledText", "Matty Herrera")], carrier=carrier)
    info = project.pool.appends[0][0]
    assert info["trackIndex"] == 3 and info["recordFrame"] == 108030
    assert (info["startFrame"], info["endFrame"], info["mediaType"]) == (0, 210, 1)
    assert tl.GetTrackCount("video") == 3, "missing tracks are added"
    assert result["track"] == ["video", 3] and result["duration"] == 210
    assert result["global_end"] == 209.0, "out animation must land on the clip end"
    assert result["set"][0]["read"] == "Matty Herrera"
    assert tl.title_inserts == [], "place never uses the rippling title insert"
    assert tl.tracks[1] == [], "V1 untouched"


def test_place_reuses_the_imported_carrier(carrier, comp_file):
    project = FakeProject()
    fx.place(project, project.current, comp_file, 3, 108000, 30, carrier=carrier)
    fx.place(project, project.current, comp_file, 3, 108100, 30, carrier=carrier)
    carriers = [f for f in project.pool.root.subs if f.GetName() == fx.CARRIER_BIN]
    assert len(carriers) == 1 and len(carriers[0].clips) == 1


def test_place_refuses_v1_unless_allowed(carrier, comp_file):
    project = FakeProject()
    with pytest.raises(fx.FusionError, match="V1"):
        fx.place(project, project.current, comp_file, 1, 108000, 30, carrier=carrier)
    assert project.pool.appends == []
    fx.place(project, project.current, comp_file, 1, 108000, 30, carrier=carrier, allow_v1=True)
    assert project.pool.appends[0][0]["trackIndex"] == 1


def test_place_refuses_overlap_unless_allowed(carrier, comp_file):
    project = FakeProject()
    fx.place(project, project.current, comp_file, 3, 108000, 150, carrier=carrier)
    with pytest.raises(fx.FusionError, match="already holds"):
        fx.place(project, project.current, comp_file, 3, 108100, 150, carrier=carrier)
    fx.place(project, project.current, comp_file, 3, 108150, 30, carrier=carrier)  # abutting is fine
    fx.place(project, project.current, comp_file, 3, 108100, 30, carrier=carrier, allow_overlap=True)


def test_place_refuses_longer_than_carrier(carrier, comp_file):
    project = FakeProject(carrier_frames=300)
    with pytest.raises(fx.FusionError, match="carrier holds 300"):
        fx.place(project, project.current, comp_file, 3, 108000, 301, carrier=carrier)


def test_place_removes_carrier_when_comp_import_fails(carrier, tmp_path):
    broken = tmp_path / "broken.comp"
    broken.write_text("x", encoding="utf-8")
    project = FakeProject()
    with pytest.raises(fx.FusionError, match="carrier was removed"):
        fx.place(project, project.current, broken, 3, 108000, 30, carrier=carrier)
    assert project.current.tracks[3] == []


def test_place_missing_tool_is_an_error(carrier, comp_file):
    project = FakeProject()
    with pytest.raises(fx.FusionError, match="NameLine"):
        fx.place(project, project.current, comp_file, 3, 108000, 30,
                 assignments=[("NameLine", "StyledText", "x")], carrier=carrier)


# ---------------------------------------------------------------- export-template

def test_export_template_uses_a_throwaway_timeline(tmp_path):
    project = FakeProject()
    episode = project.current
    out = fx.export_template(project, "Draw On 2 Lines Lower Third", tmp_path / "t" / "lower.comp")
    assert Path(out["path"]).is_file() and out["frames"] == 150
    assert episode.title_inserts == [], "the working timeline is never inserted into"
    assert project.current is episode, "the working timeline is restored"
    assert project.pool.deleted_timelines == [fx.SCRATCH_TIMELINE]
    assert [t.GetName() for t in project.timelines] == ["Episode"]


def test_export_template_cleans_up_on_missing_title(tmp_path):
    project = FakeProject()
    with pytest.raises(fx.FusionError, match="no Fusion title"):
        fx.export_template(project, "Missing", tmp_path / "x.comp")
    assert [t.GetName() for t in project.timelines] == ["Episode"]


# ---------------------------------------------------------------- lookups

def test_find_item_by_track_frame_and_id(carrier, comp_file):
    project = FakeProject()
    placed = fx.place(project, project.current, comp_file, 3, 108030, 60, carrier=carrier)
    tl = project.current
    assert fx.find_item(tl, "V3@108050").GetUniqueId() == placed["id"]
    assert fx.find_item(tl, "v3@01:00:01:10").GetUniqueId() == placed["id"]
    assert fx.find_item(tl, placed["id"]).GetStart() == 108030
    with pytest.raises(fx.FusionError):
        fx.find_item(tl, "V3@108090")


# ---------------------------------------------------------------- CLI

@pytest.fixture
def fake_resolve(monkeypatch):
    project = FakeProject()

    class PM:
        def GetCurrentProject(self): return project
        def SaveProject(self): return True

    class Resolve:
        def GetProjectManager(self): return PM()

    monkeypatch.setattr(cli_mod.backend, "connect", lambda: Resolve())
    return project


def test_help_lists_fusion_group():
    result = CliRunner().invoke(cli_mod.cli, ["fusion", "--help"])
    assert result.exit_code == 0
    for command in ("carrier", "export-template", "place", "tools", "inputs", "set", "add-tool", "export", "import"):
        assert command in result.output


def test_cli_place_dry_run_changes_nothing(fake_resolve, comp_file):
    result = CliRunner().invoke(cli_mod.cli, ["--json", "--dry-run", "fusion", "place", str(comp_file), "--track", "3",
                                              "--at", "01:00:01:00", "--seconds", "7", "--text", "mainText=Matty Herrera"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["dry_run"] is True and payload["record_frame"] == 108030 and payload["frames"] == 210
    assert fake_resolve.pool.appends == []


def test_cli_place_and_set_roundtrip(fake_resolve, comp_file, carrier):
    runner = CliRunner()
    placed = runner.invoke(cli_mod.cli, ["--json", "fusion", "place", str(comp_file), "--track", "4", "--at", "108000",
                                         "--frames", "90", "--carrier", str(carrier), "--text", "mainText=Kass"])
    assert placed.exit_code == 0, placed.output
    item_id = json.loads(placed.output)["result"]["id"]
    changed = runner.invoke(cli_mod.cli, ["--json", "fusion", "set", item_id, "--text", "mainText=Hosted by Kass"])
    assert changed.exit_code == 0, changed.output
    assert json.loads(changed.output)["result"][0]["read"] == "Hosted by Kass"
    tools = runner.invoke(cli_mod.cli, ["--json", "fusion", "tools", "V4@108010"])
    assert {t["name"] for t in json.loads(tools.output)["tools"]} == {"mainText", "secondaryText"}


def test_cli_place_needs_exactly_one_length(fake_resolve, comp_file):
    result = CliRunner().invoke(cli_mod.cli, ["--json", "fusion", "place", str(comp_file), "--track", "3", "--at", "0"])
    assert result.exit_code == 1 and "exactly one" in json.loads(result.output)["error"]


def test_cli_place_v1_refusal_is_a_clean_error(fake_resolve, comp_file, carrier):
    result = CliRunner().invoke(cli_mod.cli, ["--json", "fusion", "place", str(comp_file), "--track", "1", "--at", "0",
                                              "--frames", "30", "--carrier", str(carrier)])
    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert payload["type"] == "FusionError" and "V1" in payload["error"]


def test_timeline_title_warns_about_ripple(fake_resolve):
    result = CliRunner().invoke(cli_mod.cli, ["--json", "--dry-run", "timeline", "title", "Text+", "--fusion"])
    assert result.exit_code == 0
    assert "ripple" in json.loads(result.output)["warning"]


def test_project_open_refused_is_a_clean_error(monkeypatch):
    class PM:
        def LoadProject(self, name): return None
        def GetCurrentProject(self): return None
        def SaveProject(self): return True

    class Resolve:
        def GetProjectManager(self): return PM()

    monkeypatch.setattr(cli_mod.backend, "connect", lambda: Resolve())
    result = CliRunner().invoke(cli_mod.cli, ["--json", "project", "open", "Nope"])
    assert result.exit_code == 1
    assert "rejected" in json.loads(result.output)["error"]


def test_place_refuses_shorter_than_a_stretcher_comp(carrier, tmp_path):
    comp = tmp_path / "stretch.comp"
    comp.write_text(
        "Composition {\n\tGlobalRange = { 0, 89 },\n\tTools = ordered() {\n\t\tStretch = KeyStretcher {},\n\t},\n}\n",
        encoding="utf-8",
    )
    assert fx.authored_minimum(comp) == 90
    project = FakeProject()
    with pytest.raises(fx.FusionError, match="authored at 90"):
        fx.place(project, project.current, comp, 3, 108000, 89, carrier=carrier)
    fx.place(project, project.current, comp, 3, 108000, 90, carrier=carrier)


def test_authored_minimum_is_zero_without_a_stretcher(comp_file):
    assert fx.authored_minimum(comp_file) == 0


# ---------------------------------------------------------------- add-tool

class Port:
    """A main image input or output; an input remembers the output it reads from."""

    def __init__(self, tool):
        self.tool, self.source = tool, None

    def GetTool(self): return self.tool
    def GetConnectedOutput(self): return self.source

    def ConnectTo(self, out):
        self.source = out
        return True


class WiredTool:
    def __init__(self, comp, name, reg):
        self.comp, self.attrs, self.values = comp, {"TOOLS_Name": name, "TOOLS_RegID": reg}, {}
        self.main_in, self.main_out = Port(self), Port(self)

    def GetAttrs(self): return self.attrs

    def SetAttrs(self, attrs):
        if attrs.get("TOOLS_Name") not in self.comp.tools:
            self.comp.tools[attrs["TOOLS_Name"]] = self.comp.tools.pop(self.attrs["TOOLS_Name"])
            self.attrs.update(attrs)

    def FindMainInput(self, i): return self.main_in
    def FindMainOutput(self, i): return self.main_out
    def SetInput(self, ident, value): self.values[ident] = value
    def GetInput(self, ident): return self.values.get(ident)
    def Delete(self): self.comp.tools.pop(self.attrs["TOOLS_Name"], None)


class WiredComp:
    REGISTERED = {"ofx.com.borisfx.bcc_dft.ofx.filmGlow": "BCCFilmGlow"}

    def __init__(self):
        self.tools, self.locks = {}, 0
        self.tools["SizePosition"] = WiredTool(self, "SizePosition", "Transform")
        self.tools["MediaOut1"] = WiredTool(self, "MediaOut1", "MediaOut")
        self.tools["MediaOut1"].main_in.source = self.tools["SizePosition"].main_out

    def Lock(self): self.locks += 1
    def Unlock(self): self.locks -= 1
    def FindTool(self, name): return self.tools.get(name)
    def GetToolList(self, selected): return dict(enumerate(self.tools.values(), 1))

    def AddTool(self, reg, x, y):
        if reg not in self.REGISTERED:
            return None
        name = f"{self.REGISTERED[reg]}{sum(t.attrs['TOOLS_RegID'] == reg for t in self.tools.values()) + 1}"
        self.tools[name] = WiredTool(self, name, reg)
        return self.tools[name]


def _feeds(comp, name):
    return comp.tools[name].main_in.source.GetTool().attrs["TOOLS_Name"]


GLOW = "ofx.com.borisfx.bcc_dft.ofx.filmGlow"


def test_add_tool_goes_between_a_tool_and_what_fed_it():
    comp = WiredComp()
    got = fx.add_tool(comp, GLOW, name="Glow", before="MediaOut1", assignments=[("Blend", 0.4)])
    assert got["tool"] == "Glow" and got["type"] == GLOW
    assert got["wired"] == {"from": "SizePosition", "to": "MediaOut1", "to_reads_from": "Glow"}
    assert got["set"] == [{"input": "Blend", "value": 0.4, "read": 0.4}]
    assert _feeds(comp, "MediaOut1") == "Glow" and _feeds(comp, "Glow") == "SizePosition"
    assert comp.locks == 0


def test_add_tool_without_before_adds_an_unwired_tool():
    comp = WiredComp()
    got = fx.add_tool(comp, GLOW)
    assert got["tool"] == "BCCFilmGlow1" and got["wired"] is None
    assert _feeds(comp, "MediaOut1") == "SizePosition"


def test_an_unregistered_tool_says_to_restart_and_changes_nothing():
    comp = WiredComp()
    with pytest.raises(fx.FusionError, match="restart"):
        fx.add_tool(comp, "ofx.com.borisfx.bcc_dft.ofx.notInstalled", before="MediaOut1")
    assert set(comp.tools) == {"SizePosition", "MediaOut1"} and _feeds(comp, "MediaOut1") == "SizePosition"
    assert comp.locks == 0


def test_a_missing_before_tool_removes_the_new_tool():
    comp = WiredComp()
    with pytest.raises(fx.FusionError, match="No tool named 'Nope'"):
        fx.add_tool(comp, GLOW, before="Nope")
    assert set(comp.tools) == {"SizePosition", "MediaOut1"}


def test_a_failed_input_puts_the_original_wiring_back():
    comp = WiredComp()

    def boom(ident, value):
        raise RuntimeError("bad input")

    original = comp.AddTool

    def add(reg, x, y):
        tool = original(reg, x, y)
        tool.SetInput = boom
        return tool

    comp.AddTool = add
    with pytest.raises(RuntimeError, match="bad input"):
        fx.add_tool(comp, GLOW, before="MediaOut1", assignments=[("Blend", 1)])
    assert set(comp.tools) == {"SizePosition", "MediaOut1"} and _feeds(comp, "MediaOut1") == "SizePosition"


def test_a_taken_name_is_refused():
    comp = WiredComp()
    with pytest.raises(fx.FusionError, match="name is taken"):
        fx.add_tool(comp, GLOW, name="MediaOut1")
    assert set(comp.tools) == {"SizePosition", "MediaOut1"}


def test_tool_assignment_takes_only_an_input_of_the_new_tool():
    assert fx.parse_tool_assignment("Blend=0.5") == ("Blend", 0.5)
    with pytest.raises(ValueError):
        fx.parse_tool_assignment("Other.Blend=0.5")


def _item_with(project, track, comp):
    item = FakeItem("Glow host", 108000, 108090, track, comp)
    project.current.tracks.setdefault(track, []).append(item)
    return item


def test_cli_add_tool_refuses_v1_and_dry_run_changes_nothing(fake_resolve):
    comp = WiredComp()
    v1 = _item_with(fake_resolve, 1, comp)
    refused = CliRunner().invoke(cli_mod.cli, ["--json", "fusion", "add-tool", v1.id, GLOW, "--before", "MediaOut1"])
    assert refused.exit_code == 1 and "V1" in json.loads(refused.output)["error"]
    v2 = _item_with(fake_resolve, 2, comp)
    dry = CliRunner().invoke(cli_mod.cli, ["--json", "--dry-run", "fusion", "add-tool", v2.id, GLOW,
                                           "--before", "MediaOut1", "--set", "Blend=0.4"])
    assert dry.exit_code == 0, dry.output
    assert json.loads(dry.output)["set"] == [{"input": "Blend", "value": 0.4}]
    assert set(comp.tools) == {"SizePosition", "MediaOut1"}


def test_cli_add_tool_wires_and_reads_back(fake_resolve):
    comp = WiredComp()
    item = _item_with(fake_resolve, 2, comp)
    result = CliRunner().invoke(cli_mod.cli, ["--json", "fusion", "add-tool", item.id, GLOW, "--name", "Glow",
                                              "--before", "MediaOut1", "--set", "Blend=0.4"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["result"]["wired"]["to_reads_from"] == "Glow"

# ---------------------------------------------------------------- place on a named, non-current timeline

def test_place_on_a_named_timeline_appends_there_and_restores_the_current_one(carrier, comp_file):
    project = FakeProject()
    episode = project.current
    other = FakeTimeline("Other", tracks=1)
    project.timelines.append(other)
    result = fx.place(project, other, comp_file, track=2, record_frame=108000, frames=30, carrier=carrier)
    assert result["track"] == ["video", 2]
    assert [i.GetUniqueId() for i in other.tracks[2]] == [result["id"]], "the item is on the named timeline"
    assert all(not items for items in episode.tracks.values()), "the current timeline is untouched"
    assert project.current is episode, "the previous current timeline is put back"


def test_place_removes_a_carrier_that_lands_on_the_wrong_track(carrier, comp_file):
    project = FakeProject()
    tl = project.current
    real_append = project.pool.AppendToTimeline

    def lands_on_v1(infos):  # what Resolve did when the target track did not exist on the current timeline
        return real_append([{**info, "trackIndex": 1} for info in infos])

    project.pool.AppendToTimeline = lands_on_v1
    with pytest.raises(fx.FusionError, match="landed on video track 1, not V3 .*removed again: True"):
        fx.place(project, tl, comp_file, track=3, record_frame=108000, frames=30, carrier=carrier)
    assert tl.tracks[1] == [] and len(tl.deleted) == 1
