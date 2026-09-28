"""Baseline behavior of the shared fixtures before the arrangement work.

These tests record what the engine does, so later PRs change it deliberately.
An unmapped role still renders nothing, but since PR 2 the build reports it and
drumgen.evaluate turns it into an error.
"""
import json
import os
import struct
from pathlib import Path

import pytest

from drumgen import groovekit
from drumgen.goldenrule import violations
from drumgen.riff import riff_to_kicks

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "arrangement"
DSL = FIXTURES / "dsl"


def source(name):
    return (DSL / f"{name}.dsl").read_text(encoding="utf-8")


@pytest.mark.parametrize("name, bars", [
    ("simple_4_4", 4), ("blast_beat", 4), ("breakdown_pause", 6),
    ("verse_a", 4), ("verse_b", 4)])
def test_dsl_fixtures_parse_and_render_deterministically(name, bars):
    events, info = groovekit.build(source(name), seed=7)
    again, _ = groovekit.build(source(name), seed=7)
    assert info["bars"] == bars and events == again and events
    # breakdown_pause also guards the golden rule across section seams.
    rows = [dict(index=i, ppq=e["tick"], pitch=e["pitch"]) for i, e in enumerate(events)]
    assert not violations(rows, {i: e["vel"] for i, e in enumerate(events)}, min_gap=1)


def test_breakdown_pause_bar_stays_silent():
    events, info = groovekit.build(source("breakdown_pause"), seed=7)
    bar = 4 * info["ppq"]
    # Humanized timing may pull the re-entry slightly early; leave half a 16th.
    assert not [e for e in events if 3 * bar <= e["tick"] < 4 * bar - bar // 32]


def test_similar_verses_share_the_backbone():
    parsed = [groovekit.parse_dsl(source(n)) for n in ("verse_a", "verse_b")]
    lanes = [{l["lane"]: l["cells"] for l in p["sections"][0]["lanes"]} for p in parsed]
    assert lanes[0]["kick"] == lanes[1]["kick"]
    assert lanes[0]["snare"] == lanes[1]["snare"]
    assert "hat_c" in lanes[0] and "ride" in lanes[1]


def test_unmapped_role_is_dropped_but_reported(monkeypatch):
    sparse = json.loads((FIXTURES / "maps" / "sparse_kit.json").read_text())
    real = groovekit.load_maps
    monkeypatch.setattr(groovekit, "load_maps", lambda: {**real(), **sparse})
    events, info = groovekit.build(source("unmapped_china"), seed=7)
    assert {e["pitch"] for e in events} == {36, 38}  # the china hit is gone...
    assert info["unmapped_roles"] == ["CHINA_R"]     # ...and the build says so


def _click_wav(path, sr, times, seconds):
    frames = [0.0] * int(sr * seconds)
    for t in times:
        start = int(t * sr)
        for j in range(20):
            frames[start + j] = 0.9
    data = struct.pack("<%df" % len(frames), *frames)
    with open(path, "wb") as f:
        f.write(b"RIFF" + struct.pack("<I", 36 + len(data)) + b"WAVE")
        f.write(b"fmt " + struct.pack("<IHHIIHH", 16, 3, 1, sr, sr * 4, 4, 32))
        f.write(b"data" + struct.pack("<I", len(data)) + data)


def test_simple_riff_yields_an_audio_inferred_kick_grid(tmp_path):
    # 120 BPM: a 16th is 0.125 s. Riff attacks on steps 0, 3, 8 and 11 of bar 1.
    steps = [0, 3, 8, 11]
    _click_wav(tmp_path / "riff.wav", 48000, [s * 0.125 for s in steps], 2.5)
    rpp = tmp_path / "song.rpp"
    rpp.write_text('<REAPER_PROJECT\n  TEMPO 120 4 4\n  <TRACK {0}\n    NAME "Riff"\n'
                   '    <ITEM\n      POSITION 0\n      LENGTH 2.5\n'
                   '      <SOURCE WAVE\n        FILE "riff.wav"\n      >\n    >\n  >\n>\n')
    result = riff_to_kicks(str(rpp), "Riff", bars=1)
    assert result["kick"] == "".join("x" if i in steps else "." for i in range(16))
    assert result["item_count"] == 1
    assert os.path.isabs(result["source"])
