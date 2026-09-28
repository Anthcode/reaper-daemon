"""drumgen.candidates: which parts of a plan each request sees (PR 4)."""
import json
from pathlib import Path

from drumgen import candidates
from drumgen.arrangement_schema import validate_plan

PLAN = validate_plan(json.loads((Path(__file__).resolve().parent / "fixtures" / "arrangement"
                                 / "plans" / "full_song.json").read_text()))


def test_every_role_gets_the_structure_and_hard_constraints():
    for role in candidates.DIRECTIONS:
        view = candidates.request_plan(PLAN, role)
        assert [s["id"] for s in view["sections"]] == ["intro", "verse_1", "chorus_1", "breakdown"]
        assert view["sections"][1]["effective_exclusions"] == ["choke", "ride"]
        assert view["sections"][2]["require_families"] == ["ride"]
        assert view["total_bars"] == 24 and view["meter"] == "4/4"


def test_wildcard_gets_no_treatment_fields():
    fresh = candidates.request_plan(PLAN, "fresh")["sections"][1]
    wildcard = candidates.request_plan(PLAN, "wildcard")["sections"][1]
    assert fresh["kick_strategy"] == "riff_selective" and fresh["energy"] == 0.55
    assert not set(candidates.TREATMENT_FIELDS) & set(wildcard)


def test_audio_sketch_is_reference_only_and_user_grid_is_for_everyone():
    plan = json.loads(json.dumps(PLAN))
    sections = plan["sections"]
    sections[0]["kick_grid"] = "x" + "." * 63
    sections[0]["evidence"]["kick_grid"] = "user_explicit"
    sections[1]["kick_grid"] = "x..." * 32
    sections[1]["evidence"]["kick_grid"] = "audio_inferred"
    assert candidates.has_sketch(plan) and not candidates.has_sketch(PLAN)
    for role in ("fresh", "contrast", "wildcard"):
        view = candidates.request_plan(plan, role)["sections"]
        assert view[0]["kick_grid"] == "x" + "." * 63
        assert "kick_sketch" not in view[1] and "kick_grid" not in view[1]
    reference = candidates.request_plan(plan, "reference")["sections"]
    assert reference[1]["kick_sketch"]["source"] == "audio_inferred"
    targets = candidates.kick_targets(plan)
    assert [(t["section"], t["start_bar"], t["source"]) for t in targets] == [
        ("intro", 0, "user_explicit"), ("verse_1", 4, "audio_inferred")]
