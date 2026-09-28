"""ArrangementPlan contract (PR 1 of docs/drum-arrangement-plan.md)."""
import copy
import json
from pathlib import Path

import pytest

from drumgen.arrangement_schema import (AUDIO_CAVEAT, FAMILIES, PlanError,
                                        validate_plan)

PLANS = Path(__file__).resolve().parent / "fixtures" / "arrangement" / "plans"


def load(name):
    return json.loads((PLANS / f"{name}.json").read_text(encoding="utf-8"))


def test_full_song_derives_positions_and_exclusions():
    plan = load("full_song")
    before = copy.deepcopy(plan)
    out = validate_plan(plan)
    assert plan == before  # input untouched
    assert [s["start_bar"] for s in out["sections"]] == [1, 5, 13, 21]
    assert out["total_bars"] == 24
    verse = out["sections"][1]
    assert verse["effective_exclusions"] == ["choke", "ride"]
    assert out["sections"][2]["effective_exclusions"] == ["choke"]


def test_audio_inferred_evidence_carries_the_saved_project_caveat():
    out = validate_plan(load("full_song"))
    assert [(c["section"], c["field"]) for c in out["caveats"]] == [
        ("verse_1", "kick_grid"), ("chorus_1", "section_boundary")]
    assert all(c["caveat"] == AUDIO_CAVEAT for c in out["caveats"])
    assert "saved .rpp" in AUDIO_CAVEAT


def test_plan_without_audio_has_no_caveats_and_defaults_seed():
    out = validate_plan(load("no_audio"))
    assert out["caveats"] == []
    assert out["seed"] == 1
    assert out["sections"][0]["start_bar"] == 1


def test_validation_is_deterministic():
    assert validate_plan(load("full_song")) == validate_plan(load("full_song"))


@pytest.mark.parametrize("name, message", [
    ("odd_meter", "4/4"),
    ("too_long", "at most 64"),
    ("contradiction", "both requires and excludes"),
    ("global_contradiction", "both requires and excludes"),
    ("overlap", "consecutive"),
    ("gap", "consecutive"),
    ("unknown_field", "Unknown"),
    ("duplicate_id", "Duplicate section id"),
    ("evidence_source", "evidence"),
    ("unknown_map", "kit_map"),
])
def test_invalid_fixtures_are_rejected(name, message):
    with pytest.raises(PlanError, match=message):
        validate_plan(load(f"invalid_{name}"))


@pytest.mark.parametrize("change", [
    {"schema_version": 2}, {"tempo": 39}, {"tempo": 182.0}, {"tempo": True},
    {"seed": -1}, {"sections": []}, {"exclude_families": ["cowbell"]},
    {"exclude_families": ["ride", "ride"]},
])
def test_bad_plan_fields(change):
    with pytest.raises(PlanError):
        validate_plan({**load("no_audio"), **change})


@pytest.mark.parametrize("change", [
    {"bars": 0}, {"bars": 65}, {"id": "Verse 1"}, {"role": ""},
    {"energy": 1.5}, {"energy": float("nan")}, {"energy": True},
    {"kick_strategy": "Riff Selective"}, {"evidence": []},
])
def test_bad_section_fields(change):
    plan = load("no_audio")
    plan["sections"][0].update(change)
    with pytest.raises(PlanError):
        validate_plan(plan)


def test_matching_explicit_start_bar_is_accepted():
    plan = load("full_song")
    for start, section in zip([1, 5, 13, 21], plan["sections"]):
        section["start_bar"] = start
    assert validate_plan(plan)["total_bars"] == 24


def test_families_match_the_workshop():
    import sys
    root = Path(__file__).resolve().parents[3]
    sys.path.insert(0, str(root))
    import drum_workshop
    assert FAMILIES == drum_workshop.FAMILIES
