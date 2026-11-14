"""Resolve 21.2 read-only checks (export thumbnails, export frame, grade cdl) against fakes."""

import json

import pytest
from click.testing import CliRunner

from cli_anything.davinci_resolve import davinci_resolve_cli as cli_mod
from cli_anything.davinci_resolve.core import checks as chk
from cli_anything.davinci_resolve.core import fusion as fx

START = 86400  # 01:00:00:00 at 24 fps


class Graph:
    def __init__(self, item):
        self.item = item

    def GetNumNodes(self):
        return len(self.item.cdl)

    def GetNodeEnabled(self, i):
        return i not in self.item.disabled


class Item:
    def __init__(self, name, start, end, cdl=None, disabled=()):
        self.name, self.start, self.end = name, start, end
        self.cdl = cdl if cdl is not None else {1: {"Slope": "1 1 1", "Offset": "0 0 0", "Power": "1 1 1", "Saturation": "1"}}
        self.disabled = set(disabled)
        self.sets = []

    def GetUniqueId(self): return f"id-{self.name}"
    def GetName(self): return self.name
    def GetStart(self): return self.start
    def GetEnd(self): return self.end
    def GetNodeGraph(self): return Graph(self)
    def GetCDL(self, i): return self.cdl.get(i)

    def SetCDL(self, cdl):  # present so a test can prove nothing calls it
        self.sets.append(cdl)
        return True


class Timeline:
    def __init__(self, frames=288, fps="24"):
        self.frames, self.fps = frames, fps
        self.tracks = {1: [Item("camera", START, START + frames)], 2: []}
        self.calls = []

    def GetName(self): return "S1E7 final"
    def GetStartFrame(self): return START
    def GetEndFrame(self): return START + self.frames
    def GetSetting(self, key): return self.fps if key == "timelineFrameRate" else None
    def GetTrackCount(self, kind): return len(self.tracks)
    def GetItemListInTrack(self, kind, track): return self.tracks.get(track, [])

    def ExportThumbnails(self, frames, opts):
        self.calls.append(("ExportThumbnails", frames, opts))
        bad = [c["frameOffset"] for c in frames if not 0 <= c["frameOffset"] < self.frames]
        return {"success": False, "error": f"offset {bad[0]} out of range"} if bad else {"success": True}

    def ExportFrame(self, offset, opts):
        self.calls.append(("ExportFrame", offset, opts))
        return {"success": True}


class OldTimeline(Timeline):
    """Resolve 21.1: no ExportThumbnails or ExportFrame at all."""
    ExportThumbnails = None
    ExportFrame = None


def test_offsets_take_plus_n_record_frames_and_timecodes_and_refuse_the_end():
    tl = Timeline()
    assert chk.offset_of(tl, "+12") == 12
    assert chk.offset_of(tl, str(START + 30)) == 30
    assert chk.offset_of(tl, "01:00:01:00") == 24
    with pytest.raises(ValueError, match="outside the timeline"):
        chk.offset_of(tl, "+288")   # one past the last frame fails a whole sheet in 21.2
    with pytest.raises(ValueError):
        chk.offset_of(tl, "00:59:59:23")


def test_thumbnails_label_cells_and_always_pass_a_size(tmp_path):
    tl = Timeline()
    cells = chk.thumbnail_cells(tl, ["+0", "01:00:05:12"], ["open"])
    assert cells == [{"frameOffset": 0, "label": "open"}, {"frameOffset": 132, "label": "00:00:05:12"}]
    out = chk.export_thumbnails(tl, cells, tmp_path / "sheet.jpg")
    _, frames, opts = tl.calls[-1]
    assert frames == cells and opts["maxDimension"] == chk.DEFAULT_SHEET_MAX and opts["outputType"] == "file"
    assert out["path"].endswith("sheet.jpg")


def test_frame_with_a_region_passes_the_roi_keys(tmp_path):
    tl = Timeline()
    roi = chk.parse_roi([0, 0.7, 1, 0.3])
    chk.export_frame(tl, 10, tmp_path / "lower third.jpg", roi)
    _, offset, opts = tl.calls[-1]
    assert offset == 10 and opts["roiY"] == 0.7 and opts["roiHeight"] == 0.3 and opts["maxDimension"] == 1920
    for bad in ([0.5, 0, 0.6, 1], [0, 0, 0, 1], [1, 0, 0.1, 1]):
        with pytest.raises(ValueError):
            chk.parse_roi(bad)


def test_an_older_resolve_is_named_not_crashed(tmp_path):
    with pytest.raises(fx.FusionError, match="needs Resolve 21.2"):
        chk.export_frame(OldTimeline(), 0, tmp_path / "f.jpg")
    with pytest.raises(fx.FusionError, match="needs Resolve 21.2"):
        chk.export_thumbnails(OldTimeline(), [{"frameOffset": 0, "label": "a"}], tmp_path / "s.jpg")


def test_grade_reads_each_node_and_names_the_graded_items():
    tl = Timeline()
    tl.tracks[1].append(Item("graded", START + 288, START + 300,
                             cdl={1: {"Slope": "1.2 1 1", "Offset": "0 0 0", "Power": "1 1 1", "Saturation": "1"},
                                  2: {"Slope": "1 1 1", "Offset": "0 0 0", "Power": "1 1 1", "Saturation": "0.4"}},
                             disabled={2}))
    result = chk.track_grades(tl, 1)
    assert result["graded"] == ["graded"]
    graded = result["items"][1]
    assert graded["graded_nodes"] == [1]
    assert graded["nodes"][1] == {"node": 2, "enabled": False, "cdl": graded["nodes"][1]["cdl"], "neutral": False}
    assert all(not it.sets for it in tl.tracks[1])   # read only
    with pytest.raises(ValueError):
        chk.track_grades(tl, 9)


# ---------------------------------------------------------------- CLI

@pytest.fixture
def fake(monkeypatch):
    tl = Timeline()

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

    class Resolve:
        def GetProjectManager(self): return PM()

    monkeypatch.setattr(cli_mod.backend, "connect", lambda: Resolve())
    return tl, project


def run(*args):
    return CliRunner().invoke(cli_mod.cli, ["--json", *args])


def test_help_lists_the_new_groups():
    for group, commands in (("export", ("thumbnails", "frame")), ("grade", ("cdl",))):
        result = CliRunner().invoke(cli_mod.cli, [group, "--help"])
        assert result.exit_code == 0 and all(c in result.output for c in commands)


def test_cli_thumbnails_and_frame_never_save_the_project(fake, tmp_path):
    tl, project = fake
    r = run("export", "thumbnails", str(tmp_path / "s.jpg"), "--at", "+0", "--at", "+100", "--label", "a")
    assert r.exit_code == 0, r.output
    assert json.loads(r.output)["cells"][1] == {"frameOffset": 100, "label": "00:00:04:04"}
    r = run("export", "frame", str(tmp_path / "f.jpg"), "--at", "+5", "--roi", "0", "0.7", "1", "0.3",
            "--max-dimension", "1080")
    assert r.exit_code == 0, r.output
    assert tl.calls[-1][2]["maxDimension"] == 1080 and project.saved == 0


def test_cli_dry_run_exports_nothing_and_a_bad_offset_exits_1(fake, tmp_path):
    tl, _ = fake
    r = CliRunner().invoke(cli_mod.cli, ["--json", "--dry-run", "export", "frame", str(tmp_path / "f.jpg"), "--at", "+5"])
    assert r.exit_code == 0 and json.loads(r.output)["dry_run"] is True and not tl.calls
    r = run("export", "thumbnails", str(tmp_path / "s.jpg"), "--at", "+288")
    assert r.exit_code == 1 and "outside the timeline" in r.output and not tl.calls


def test_cli_grade_fail_on_grade(fake):
    tl, _ = fake
    assert run("grade", "cdl", "--fail-on-grade").exit_code == 0
    tl.tracks[1][0].cdl[1]["Power"] = "1 1 0.9"
    r = run("grade", "cdl", "--fail-on-grade")
    assert r.exit_code == 1 and json.loads(r.output)["graded"] == ["camera"]
