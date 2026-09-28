"""Performer profiles and fill checks (PR 5)."""
import pytest

from drumgen import candidates, evaluate, fills, groovekit, performer
from drumgen.arrangement_schema import validate_plan

GROOVE = ("@tempo 180\n@map RS Monarch\n[a] bars=2 feel=f\ngrid 16\n"
          "kick | x.x.x.x.x.x.x.x. |\nsnare | ....x.......x... |\nhat_c | x.x.x.x.x.x.x.x. |\n")


def test_no_profile_renders_exactly_as_before():
    base, _ = groovekit.build(GROOVE, seed=5)
    assert groovekit.build(GROOVE, seed=5, params=None)[0] == base
    assert groovekit.build(GROOVE, seed=5, params={})[0] == base
    assert groovekit.build(GROOVE, seed=5, params={"humanize": 20})[0] == base


def test_profiles_change_the_playing_repeatably_not_the_notes():
    tight = performer.get("tight_modern_metal")
    raw = performer.get("raw_black_metal")
    a, _ = groovekit.build(GROOVE, seed=5, params=tight["params"])
    b, _ = groovekit.build(GROOVE, seed=5, params=raw["params"])
    assert a == groovekit.build(GROOVE, seed=5, params=tight["params"])[0]
    assert a != b
    assert sorted(e["pitch"] for e in a) == sorted(e["pitch"] for e in b)
    kicks = [e["vel"] for e in a if e["pitch"] == 24]
    assert kicks and min(kicks) >= 100


def test_unknown_profile_and_params_are_refused():
    with pytest.raises(ValueError, match="performer profile"):
        performer.get("polka")
    with pytest.raises(ValueError, match="render params"):
        groovekit.build(GROOVE, seed=1, params={"swing": 0.6})
    assert performer.get(None) is None


def test_kick_rate_is_measured_and_warned_against_the_profile():
    blast = GROOVE.replace("@tempo 180", "@tempo 260").replace(
        "kick | x.x.x.x.x.x.x.x. |", "kick | xxxxxxxxxxxxxxxx |")
    events, _ = evaluate.structure(groovekit.parse_dsl(blast))
    assert performer.fastest_kick_rate(events, 260) == pytest.approx(17.33, abs=0.01)
    brief = {"tempo": 260, "map": "RS Monarch", "bars": 2}
    parsed = groovekit.parse_dsl(blast)
    plain = evaluate.evaluate_candidate(blast, parsed, brief, 1)
    assert not [f for f in plain["findings"] if f["level"] == "warning" and f["check"] == "kick_rate"]
    tight = evaluate.evaluate_candidate(blast, parsed, brief, 1,
                                        performer=performer.get("tight_modern_metal"))
    assert tight["valid"]
    assert [f["data"]["limit"] for f in tight["findings"]
            if f["level"] == "warning" and f["check"] == "kick_rate"] == [15.0]
    assert tight["metrics"]["performer"] == "tight_modern_metal"


def test_transition_hints():
    verse = {"id": "v", "bars": 8, "transition_out": "short_pickup", "energy": 0.4}
    assert fills.hint(verse)["suggested_beats"] == 1
    tom = dict(verse, transition_out="tom_run")
    assert fills.hint(tom)["suggested_beats"] == 4
    assert fills.hint(dict(verse, transition_out="pickup"), {"energy": 0.9})["suggested_beats"] == 4
    assert "suggested_beats" not in fills.hint(dict(verse, transition_out="stop"))
    assert fills.hint({"id": "x", "bars": 4}) is None


def two_sections(first_extra="", second_kick="x.......x.......", second_extra=""):
    return ("@tempo 120\n@map GM Standard\n"
            "[verse] bars=1 feel=mf\ngrid 16\nkick | x.......x....... |\nsnare | ....x.......x... |\n"
            + first_extra +
            f"[chorus] bars=1 feel=mf\ngrid 16\nkick | {second_kick} |\nsnare | ....x.......x... |\n"
            + second_extra)


def fill_findings(source, planned=None):
    parsed = groovekit.parse_dsl(source)
    events, _ = evaluate.structure(parsed)
    return fills.check(events, evaluate._section_ranges(parsed), planned)


def test_fill_that_lands_on_a_kick_is_fine():
    found = fill_findings(two_sections("tom1 | ............xx.. |\ntom2 | ..............xx |\n"))
    assert not [f for f in found if f["level"] == "warning"]
    assert next(f for f in found if f["check"] == "fills")["data"]["count"] == 1


def test_fill_with_an_empty_downbeat_and_one_over_the_seam_are_warnings():
    found = fill_findings(two_sections("tom1 | ............xxxx |\n", second_kick="....x..........."))
    assert {f["check"] for f in found if f["level"] == "warning"} == {"fill_landing"}
    over = fill_findings(two_sections("tom1 | ..............xx |\n",
                                      second_extra="tom2 | xx.............. |\n"))
    assert "fill_seam" in {f["check"] for f in over if f["level"] == "warning"}


def test_same_fill_three_times_and_a_missing_planned_fill():
    lanes = ("@tempo 120\n@map GM Standard\n[a] bars=3 feel=mf\ngrid 16\n"
             "kick | x.......x....... |\ntom1 | ............xxxx |\n")
    found = fill_findings(lanes)
    assert [f["data"]["times"] for f in found if f["check"] == "fill_repeat"] == [3]
    missing = fill_findings(two_sections(), planned={"verse": "tom_run"})
    assert [f["data"]["section"] for f in missing if f["check"] == "fill_plan"] == ["verse"]


def test_requests_carry_transition_hints_except_the_wildcard():
    plan = validate_plan({"schema_version": 1, "tempo": 120, "meter": "4/4",
                          "kit_map": "GM Standard", "sections": [
                              {"id": "a", "bars": 4, "role": "verse", "energy": 0.3,
                               "transition_out": "tom_run"},
                              {"id": "b", "bars": 4, "role": "chorus", "energy": 0.8}]})
    fresh = candidates.request_plan(plan, "fresh")["sections"][0]
    assert fresh["transition_hint"]["kind"] == "tom_run"
    assert fresh["transition_hint"]["energy_change"] == 0.5
    assert "transition_hint" not in candidates.request_plan(plan, "wildcard")["sections"][0]
