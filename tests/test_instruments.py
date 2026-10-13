import json
import os
import shutil

import pytest

import instruments as inst

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture
def root(tmp_path):
    os.makedirs(tmp_path / "instruments")
    shutil.copy(os.path.join(REPO, "instruments", "catalog.json"), tmp_path / "instruments")
    # known_maps imports the skills from the bridge root, so point at the repo's.
    os.symlink(os.path.join(REPO, "skills"), tmp_path / "skills", target_is_directory=True) \
        if hasattr(os, "symlink") and os.name != "nt" else shutil.copytree(
            os.path.join(REPO, "skills"), tmp_path / "skills",
            ignore=shutil.ignore_patterns("__pycache__", "tests", "examples", "docs"))
    return str(tmp_path)


class FakeBridge:
    def __init__(self, tracks=None, fail=None):
        self.calls, self.tracks, self.fail = [], tracks or [], fail or set()

    def __call__(self, kind, payload, **_):
        self.calls.append((kind, payload))
        if kind in self.fail:
            return {"ok": False, "error": {"code": "BOOM"}}
        if kind == "get_context":
            return {"ok": True, "data": {"tracks": self.tracks}}
        return {"ok": True, "data": {"fx": ["VST3i: Serum 2 (Xfer Records)"]}}


def test_shipped_catalog_is_valid_against_real_maps():
    maps = inst.known_maps(REPO)
    assert maps and maps["guitar"] and maps["drums"]
    for name, entry in inst.load_library(REPO).items():
        if entry["source"] == "catalog":
            inst.validate_entry(name, {k: v for k, v in entry.items()
                                       if k not in ("name", "source")}, maps)
            assert "chain" not in entry  # a chain is one machine's state


@pytest.mark.parametrize("name,entry", [
    ("Bad Name", {"role": "synth"}),
    ("pad", {"role": "kazoo"}),
    ("pad", {"role": "synth", "maps": {"synth": "x"}}),
    ("pad", {"role": "synth", "range": [90, 30]}),
    ("pad", {"role": "synth", "range": [0, 128]}),
    ("pad", {"role": "synth", "articulations": {"legato": 200}}),
    ("pad", {"role": "synth", "tags": "dark"}),
])
def test_bad_entries_are_refused(name, entry):
    with pytest.raises(inst.InstrumentError):
        inst.validate_entry(name, entry)


def test_unknown_map_is_refused():
    with pytest.raises(inst.InstrumentError, match="no drums map"):
        inst.validate_entry("kit", {"role": "drums", "maps": {"drums": "Nope"}},
                            {"drums": {"RS Monarch"}})


def test_save_writes_chain_and_user_entry_overriding_catalog(root):
    bridge = FakeBridge()
    entry, _ = inst.save_from_track(root, bridge, "dark-pad", "Pad", "synth",
                                    library="Serum 2: PD - Dark", range_=[36, 96],
                                    tags=["pad", "dark"])
    assert bridge.calls == [("save_fx_chain", {"target_track_name": "Pad",
                             "chain_name": "instrument-dark-pad", "overwrite": False})]
    assert entry["chain"] == "instrument-dark-pad"
    assert entry["plugin"] == "VST3i: Serum 2 (Xfer Records)"
    lib = inst.load_library(root)
    assert lib["dark-pad"]["source"] == "user"
    # Saving a catalog name keeps its maps and adds the chain.
    inst.save_from_track(root, FakeBridge(), "monarch-drums", "Drums", "drums")
    lib = inst.load_library(root)
    assert lib["monarch-drums"]["source"] == "user"
    assert lib["monarch-drums"]["maps"] == {"drums": "RS Monarch"}
    with open(os.path.join(root, "instruments", "catalog.json"), encoding="utf-8") as f:
        assert "chain" not in json.load(f)["instruments"]["monarch-drums"]


def test_resave_needs_overwrite_and_failed_save_registers_nothing(root):
    inst.save_from_track(root, FakeBridge(), "lead", "Lead", "synth")
    with pytest.raises(inst.InstrumentError, match="overwrite"):
        inst.save_from_track(root, FakeBridge(), "lead", "Lead", "synth")
    bridge = FakeBridge()
    inst.save_from_track(root, bridge, "lead", "Lead", "synth", overwrite=True)
    assert bridge.calls[0][1]["overwrite"] is True
    with pytest.raises(inst.InstrumentError, match="BOOM"):
        inst.save_from_track(root, FakeBridge(fail={"save_fx_chain"}), "sub", "Sub", "synth")
    assert "sub" not in inst.load_library(root)


def test_load_needs_a_chain_and_an_empty_track(root):
    with pytest.raises(inst.InstrumentError, match="no saved chain"):
        inst.load_onto_track(root, FakeBridge(), "argent-l", "Gtr L")
    with pytest.raises(inst.InstrumentError, match="no instrument named"):
        inst.load_onto_track(root, FakeBridge(), "nope", "Gtr L")
    inst.save_from_track(root, FakeBridge(), "lead", "Lead", "synth")
    busy = FakeBridge(tracks=[{"name": "Lead 2", "fx": [{"name": "ReaEQ"}]}])
    with pytest.raises(inst.InstrumentError, match="already has FX"):
        inst.load_onto_track(root, busy, "lead", "Lead 2")
    assert [c[0] for c in busy.calls] == ["get_context"]
    dup = FakeBridge(tracks=[{"name": "Lead 2", "fx": []}, {"name": "Lead 2", "fx": []}])
    with pytest.raises(inst.InstrumentError, match="2 tracks"):
        inst.load_onto_track(root, dup, "lead", "Lead 2")
    ok = FakeBridge(tracks=[{"name": "Lead 2", "fx": []}])
    entry, data = inst.load_onto_track(root, ok, "lead", "Lead 2")
    assert ok.calls[-1] == ("add_fx_chain", {"target_track_name": "Lead 2",
                                            "chain_name": "instrument-lead"})


def test_remove_only_touches_the_user_layer(root):
    inst.save_from_track(root, FakeBridge(), "lead", "Lead", "synth")
    inst.remove(root, "lead")
    assert "lead" not in inst.load_library(root)
    with pytest.raises(inst.InstrumentError, match="catalog"):
        inst.remove(root, "argent-l")


def test_find_filters_by_role_and_tag():
    lib = inst.load_library(REPO)
    assert {e["name"] for e in inst.find(lib, role="bass")} >= {"nolly-bass"}
    assert all("metal" in e["tags"] for e in inst.find(lib, tag="metal"))


def test_mcp_instruments_list_and_guarded_load(monkeypatch):
    import reaper_mcp as mcp
    tool = mcp._TOOL_BY_NAME["instruments"]["handler"]
    out = tool({"action": "list", "role": "bass"})
    assert "nolly-bass" in out["content"][0]["text"]
    out = tool({"action": "load", "name": "argent-l"})
    assert out.get("isError") and "track" in out["content"][0]["text"]
