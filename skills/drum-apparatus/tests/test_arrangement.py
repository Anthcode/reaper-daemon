"""drumgen.arrangement: offline plan building with source priority (PR 3)."""
import copy
import math
import struct

import pytest

from drumgen.arrangement import build_plan, observe
from drumgen.arrangement_schema import PlanError, validate_plan

BRIEF = {"tempo": 120, "kit_map": "GM Standard", "seed": 9,
         "sections": [{"id": "verse", "bars": 4, "role": "verse", "kick_strategy": "riff_selective"},
                      {"id": "chorus", "bars": 4, "role": "chorus",
                       "exclude_families": ["china"]}]}


def brief(**changes):
    return {**copy.deepcopy(BRIEF), **changes}


def test_user_sections_are_explicit_and_positions_derived():
    result = build_plan(brief())
    plan = result["plan"]
    assert [s["start_bar"] for s in plan["sections"]] == [1, 5]
    assert plan["meter"] == "4/4" and plan["seed"] == 9
    verse = plan["sections"][0]
    assert verse["evidence"] == {"section_boundary": "user_explicit", "role": "user_explicit",
                                 "kick_strategy": "user_explicit"}
    assert result["disagreements"] == [] and result["normalized"]["caveats"] == []


def test_plan_output_is_editable_and_revalidates():
    result = build_plan(brief())
    edited = copy.deepcopy(result["plan"])
    assert validate_plan(edited) == result["normalized"]
    edited["sections"][1]["bars"] = 8
    del edited["sections"][1]["start_bar"]
    assert validate_plan(edited)["total_bars"] == 12


def test_same_brief_gives_the_same_plan():
    obs = {"boundaries": [3], "kick_grid": "x..." * 32}
    assert build_plan(brief(), obs) == build_plan(brief(), obs)
    assert build_plan(brief(), obs) == build_plan(copy.deepcopy(brief()), copy.deepcopy(obs))


def test_no_audio_and_no_sections_gives_one_default_section():
    result = build_plan({"tempo": 120, "kit_map": "GM Standard", "total_bars": 8})
    (section,) = result["plan"]["sections"]
    assert section["bars"] == 8 and section["evidence"]["section_boundary"] == "default"
    assert result["normalized"]["caveats"] == []


def test_audio_boundaries_split_an_unsectioned_brief_with_caveats():
    result = build_plan({"tempo": 120, "kit_map": "GM Standard", "total_bars": 12},
                        {"boundaries": [5, 9, 40, 1]})
    assert [s["bars"] for s in result["plan"]["sections"]] == [4, 4, 4]
    assert all(s["evidence"]["section_boundary"] == "audio_inferred"
               for s in result["plan"]["sections"])
    assert len(result["normalized"]["caveats"]) == 3


def test_user_sections_win_over_audio_boundaries():
    result = build_plan(brief(), {"boundaries": [3, 7], "source": {"kind": "saved_project"}})
    assert [s["bars"] for s in result["plan"]["sections"]] == [4, 4]
    (disagreement,) = result["disagreements"]
    assert disagreement["user"] == [5] and disagreement["audio"] == [3, 7]
    assert disagreement["kept"] == "user_explicit"
    agreeing = build_plan(brief(), {"boundaries": [5]})
    assert agreeing["disagreements"] == []


def test_user_kick_grid_wins_and_audio_fills_the_rest():
    user_grid = "x......." * 8
    sections = copy.deepcopy(BRIEF["sections"])
    sections[0]["kick_grid"] = user_grid
    heard = "x..x" * 32
    result = build_plan(brief(sections=sections), {"kick_grid": heard})
    verse, chorus = result["plan"]["sections"]
    assert verse["kick_grid"] == user_grid
    assert verse["evidence"]["kick_grid"] == "user_explicit"
    assert chorus["kick_grid"] == heard[64:]
    assert chorus["evidence"]["kick_grid"] == "audio_inferred"
    assert [d["section"] for d in result["disagreements"]] == ["verse"]
    assert [c["field"] for c in result["normalized"]["caveats"]] == ["kick_grid"]


def test_silent_audio_leaves_no_kick_grid():
    result = build_plan(brief(), {"kick_grid": "." * 128})
    assert all("kick_grid" not in s for s in result["plan"]["sections"])


@pytest.mark.parametrize("changes, message", [
    ({"meter": "7/8"}, "4/4"),
    ({"total_bars": 9}, "add up"),
    ({"sections": None, "total_bars": 65}, "total_bars"),
    ({"sections": None}, "sections or total_bars"),
    ({"surprise": 1}, "Unknown brief"),
    ({"riff": {"rpp": "a.rpp"}}, "riff"),
    ({"sections": [{"id": "a", "bars": 40, "role": "verse"},
                   {"id": "b", "bars": 40, "role": "verse"}]}, "at most 64"),
    ({"sections": [{"id": "a", "bars": 4, "role": "verse",
                    "evidence": {"x": "user_explicit"}}]}, "set by the planner"),
    ({"sections": [{"id": "a", "bars": 4, "role": "verse", "require_families": ["ride"]}],
      "exclude_families": ["ride"]}, "both requires and excludes"),
])
def test_bad_briefs(changes, message):
    with pytest.raises(PlanError, match=message):
        build_plan(brief(**changes))


def test_observed_grid_must_cover_the_plan():
    with pytest.raises(PlanError, match="whole plan"):
        build_plan(brief(), {"kick_grid": "x..."})


# ---- observe(): real riff and profile code on a synthesized saved project ----

SR, BAR = 24000, 2.0  # 120 BPM: a bar is 2 s, a 16th is 0.125 s


def _bar(hits, freq, tau):
    out = [0.0] * int(BAR * SR)
    for step in hits:
        start = int(step * 0.125 * SR)
        for j in range(int(0.3 * SR)):
            if start + j < len(out):
                out[start + j] += 0.9 * math.exp(-j / SR / tau) * math.sin(2 * math.pi * freq * j / SR)
    return out


def test_observe_reads_a_saved_project(tmp_path):
    chug = _bar(range(0, 16, 2), 100, 0.03)     # 8ths, palm-muted
    trem = _bar(range(16), 400, 0.05)           # 16ths, higher note
    samples = chug * 4 + trem * 4
    data = struct.pack("<%df" % len(samples), *samples)
    with open(tmp_path / "gtr.wav", "wb") as f:
        f.write(b"RIFF" + struct.pack("<I", 36 + len(data)) + b"WAVE")
        f.write(b"fmt " + struct.pack("<IHHIIHH", 16, 3, 1, SR, SR * 4, 4, 32))
        f.write(b"data" + struct.pack("<I", len(data)) + data)
    (tmp_path / "song.rpp").write_text(
        '<REAPER_PROJECT 0.1\n  TEMPO 120 4 4 0\n  <TRACK {A}\n    NAME GTR\n'
        '    <ITEM\n      POSITION 0\n      LENGTH 16\n'
        '      <SOURCE WAVE\n        FILE "gtr.wav"\n      >\n    >\n  >\n>\n')
    obs = observe({"rpp": str(tmp_path / "song.rpp"), "track": "GTR"}, 8)
    assert 5 in obs["boundaries"]
    assert obs["kick_grid"][:16] == "x.x.x.x.x.x.x.x."
    assert obs["source"]["kind"] == "saved_project"
    result = build_plan({"tempo": 120, "kit_map": "GM Standard", "total_bars": 8}, obs)
    assert result["plan"]["sections"][1]["start_bar"] == 5
    assert all(s["evidence"]["kick_grid"] == "audio_inferred" for s in result["plan"]["sections"])
