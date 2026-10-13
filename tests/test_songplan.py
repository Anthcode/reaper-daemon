import copy
import os
import subprocess
import sys

import pytest

import songplan as sp

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture(autouse=True)
def catalog_only(monkeypatch):
    """Tests see the shipped catalog, never this machine's saved sounds."""
    real = sp.inst.load_library
    monkeypatch.setattr(sp.inst, "load_library",
                        lambda root, include_user=True: real(root, include_user=False))

SHEET = {
    "title": "Test Pattern", "tempo": 140, "key": "E",
    "parts": {"drums": "monarch-drums", "guitar_l": "argent-l", "guitar_r": "argent-r",
              "bass": "nolly-bass", "layers": {"pad": "nolly-bass"}},
    "sections": [
        {"name": "intro", "bars": 2, "energy": 2, "feel": "mf",
         "drums": {"lanes": {"kick": "X.......X.......", "snare": "........X......."}},
         "layers": {"pad": [{"time": 0, "duration": 8, "pitch": 40, "velocity": 90}]}},
        {"name": "verse", "bars": 3, "energy": 4, "feel": "f",
         "drums": {"lanes": {"kick": "XxXxXxXxXxXxXxXx", "snare": "....X.......X..."}},
         "riff": ["Xxx.Xx.xXx.xXxx.", "Xx_.mx.xXx.v_k_."],
         "layers": {"pad": [{"time": 2, "duration": 4, "pitch": 43, "velocity": 80}]}},
        {"name": "outro", "bars": 1},
    ],
}


def sheet(**changes):
    s = copy.deepcopy(SHEET)
    s.update(changes)
    return s


def test_good_sheet_has_no_problems():
    assert sp.check(sheet(), REPO) == []


@pytest.mark.parametrize("mutate,expect", [
    (lambda s: s.update(tempo=12), "tempo"),
    (lambda s: s.update(meter="7/8"), "4/4"),
    (lambda s: s.update(title=""), "title"),
    (lambda s: s.update(sections=[]), "at least one section"),
    (lambda s: s["parts"].update(drums="no-such-kit"), "no instrument named"),
    (lambda s: s["sections"][0].update(bars=0), "bars must be"),
    (lambda s: s["sections"][0].update(feel="loud"), "feel must be"),
    (lambda s: s["sections"][0].update(energy=9), "energy"),
    (lambda s: s["sections"][0]["drums"]["lanes"].update(cowbell="X..."), "drums"),
    (lambda s: s["sections"][1].update(riff=["XXX"]), "riff"),
    (lambda s: s["sections"][1].update(name="intro"), "unique"),
    (lambda s: s["sections"][0]["layers"]["pad"][0].update(duration=9), "past the end"),
    (lambda s: s["sections"][0].update(layers={"lead": []}), "not named in parts.layers"),
    (lambda s: s["sections"][0]["layers"]["pad"][0].update(pitch=10), "outside"),
    (lambda s: s.update(guitar_map="banjo"), "guitar map"),
])
def test_problems_are_named(mutate, expect):
    s = sheet()
    mutate(s)
    problems = sp.check(s, REPO)
    assert any(expect in p for p in problems), problems


def test_check_reports_every_problem_at_once():
    s = sheet(tempo=12, meter="5/4")
    s["sections"][0]["feel"] = "loud"
    assert len(sp.check(s, REPO)) >= 3


def test_structure_tiles_and_offsets():
    s = sheet()
    assert sp.section_starts(s) == [1, 3, 6]
    assert sp.total_bars(s) == 6
    bars = sp.riff_bars(s)
    assert bars == [sp.REST_BAR, sp.REST_BAR, "Xxx.Xx.xXx.xXxx.", "Xx_.mx.xXx.v_k_.",
                    "Xxx.Xx.xXx.xXxx.", sp.REST_BAR]
    dsl = sp.drum_dsl(s)
    assert dsl.startswith("@tempo 140\n[intro-1] bars=2 feel=mf")
    assert "[outro-3] bars=1 feel=mf\ngrid 16\nkick | ................ |" in dsl
    ev = sp.layer_events(s, "pad")
    assert [(e["time"], e["duration"], e["pitch"]) for e in ev] == [(0, 8, 40), (10, 4, 43)]


def test_approval_breaks_on_any_edit():
    s = sp.approve(sheet())
    assert sp.is_approved(s)
    assert "Approved" in sp.render_text(s)
    s["sections"][1]["bars"] = 4
    assert not sp.is_approved(s)
    assert "needs approval again" in sp.render_text(s)


def test_generators_render_the_combined_song(tmp_path):
    """The real engines, offline: the whole-song DSL and riff file both render."""
    s = sheet()
    dsl = tmp_path / "song.dsl"
    dsl.write_text(sp.drum_dsl(s), encoding="utf-8")
    riff = tmp_path / "riff.txt"
    riff.write_text("\n".join(sp.riff_bars(s)) + "\n", encoding="utf-8")
    r = subprocess.run([sys.executable, os.path.join(REPO, "skills", "drum-apparatus",
                        "groovegen.py"), "--dsl", str(dsl), "--map", "RS Monarch",
                        "--out", str(tmp_path / "d.mid")], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    for part in ("guitar", "bass"):
        r = subprocess.run([sys.executable, os.path.join(REPO, "skills", "guitar-apparatus",
                            "shredgen.py"), "--bars-file", str(riff), "--part", part,
                            "--map", "argent_e", "--tempo", "140",
                            "--out", str(tmp_path / f"{part}.mid")],
                           capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
        assert (tmp_path / f"{part}.mid").stat().st_size > 100


class FakeRender:
    def __init__(self):
        self.calls = []

    def render_groove(self, br, **kw):
        self.calls.append(("drums", kw))
        return 0, "ok"

    def render_shred(self, br, **kw):
        sel = kw["track"]
        self.calls.append((sel.get("target_track_name", sel) if isinstance(sel, dict) else sel, kw))
        return 0, "ok"


def fake_sender(log, fail=()):
    def send(kind, payload, timeout_ms=30000):
        log.append((kind, payload))
        if kind in fail:
            return {"ok": False, "error": {"code": "BOOM"}}
        return {"ok": True, "data": {}}
    return send


def test_draft_refuses_unapproved_and_writes_nothing():
    log = []
    with pytest.raises(sp.SheetError, match="not approved"):
        sp.draft(sheet(), REPO, fake_sender(log), render=FakeRender())
    assert log == []


def test_draft_order_new_tab_first(tmp_path):
    log, render = [], FakeRender()
    rep = sp.draft(sp.approve(sheet()), REPO, fake_sender(log), render=render,
                   out_dir=str(tmp_path))
    kinds = [k for k, _ in log]
    assert kinds[:2] == ["new_project_tab", "set_tempo"]
    assert [p["name"] for k, p in log if k == "add_track"] == \
        ["drums", "guitar-l", "guitar-r", "bass", "pad"]
    assert [p["name"] for k, p in log if k == "add_region"] == ["intro", "verse", "outro"]
    assert [c[0] for c in render.calls] == ["drums", "guitar-l", "guitar-r", "bass"]
    assert render.calls[0][1]["kit_map"] == "RS Monarch"
    assert render.calls[1][1]["map_name"] == "argent_e"
    ev = [p for k, p in log if k == "insert_midi_events"][0]
    assert ev["time_unit"] == "beats" and ev["length_beats"] == 24
    # No chains are saved in the shipped catalog, so each track says what to do.
    assert len(rep["problems"]) == 5 and not rep["ok"]
    assert "instrument save" in rep["problems"][0]


def test_draft_stops_if_new_tab_fails():
    log, render = [], FakeRender()
    with pytest.raises(sp.SheetError, match="stopped before writing"):
        sp.draft(sp.approve(sheet()), REPO, fake_sender(log, fail={"new_project_tab"}),
                 render=render)
    assert [k for k, _ in log] == ["new_project_tab"] and render.calls == []


def test_mcp_song_plan_refuses_unapproved_draft_before_touching_reaper(tmp_path, monkeypatch):
    import json as _json
    import reaper_mcp as mcp
    path = tmp_path / "sheet.json"
    path.write_text(_json.dumps(sheet()), encoding="utf-8")
    monkeypatch.setattr(mcp.reaperd, "status_ok", lambda **kw: (_ for _ in ()).throw(
        AssertionError("asked REAPER before checking approval")))
    out = mcp._TOOL_BY_NAME["song_plan"]["handler"]({"action": "draft", "sheet": str(path)})
    assert out["isError"] and "not approved" in out["content"][0]["text"]
    out = mcp._TOOL_BY_NAME["song_plan"]["handler"]({"action": "approve", "sheet": str(path)})
    assert not out.get("isError")
    assert sp.is_approved(sp.load(str(path)))


def test_draft_warns_about_a_kit_that_needs_its_routing(tmp_path, monkeypatch):
    lib = sp.inst.load_library(REPO)
    lib["monarch-drums"] = dict(lib["monarch-drums"], chain="instrument-monarch-drums",
                                tags=["needs-rig"])
    monkeypatch.setattr(sp.inst, "load_library", lambda root: lib)
    rep = sp.draft(sp.approve(sheet()), REPO, fake_sender([]), render=FakeRender(),
                   out_dir=str(tmp_path))
    assert any("multi-output routing" in p for p in rep["problems"])


def test_draft_with_a_rig_sends_drum_midi_to_its_play_track(tmp_path, monkeypatch):
    lib = sp.inst.load_library(REPO)
    lib["monarch-drums"] = dict(lib["monarch-drums"], rig=str(tmp_path / "r.RTrackTemplate"),
                                play_track="Monarch", rig_tracks=["Monarch", "Kick"])
    monkeypatch.setattr(sp.inst, "load_library", lambda root, include_user=True: lib)
    monkeypatch.setattr(sp.inst, "load_rig",
                        lambda root, send, name: (lib[name], [{}, {}], "{rig-monarch}"))
    log, render = [], FakeRender()
    rep = sp.draft(sp.approve(sheet()), REPO, fake_sender(log), render=render,
                   out_dir=str(tmp_path))
    assert "drums" not in [p["name"] for k, p in log if k == "add_track"]
    assert render.calls[0][1]["track"] == {"target_track_guid": "{rig-monarch}"}
    assert not any("drums:" in p for p in rep["problems"])
