"""drumgen.evaluate: technical findings for one candidate (PR 2)."""
import json
from pathlib import Path

import pytest

from drumgen import evaluate, groovekit
from drumgen.arrangement_schema import validate_plan

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "arrangement"
SPARSE = json.loads((FIXTURES / "maps" / "sparse_kit.json").read_text())


def dsl(*sections, tempo=120, kit="GM Standard"):
    body = "".join(f"[{name}] bars={bars} feel=mf\ngrid 16\n" +
                   "".join(f"{lane} | {cells} |\n" for lane, cells in lanes)
                   for name, bars, lanes in sections)
    return f"@tempo {tempo}\n@map {kit}\n{body}"


GROOVE = [("kick", "x.......x......."), ("snare", "....x.......x..."),
          ("hat_c", "x.x.x.x.x.x.x.x.")]


def run(source, bars=None, plan=None, riff_kick=None, seed=3, exclude=()):
    parsed = groovekit.parse_dsl(source)
    total = sum(s["bars"] for s in parsed["sections"])
    brief = {"tempo": parsed["tempo"], "map": parsed["map"], "bars": bars or total,
             "exclude_families": list(exclude)}
    return evaluate.evaluate_candidate(source, parsed, brief, seed, plan=plan,
                                       riff_kick=riff_kick)


def checks(result, level):
    return {f["check"] for f in result["findings"] if f["level"] == level}


@pytest.fixture
def sparse_kit(monkeypatch):
    real = groovekit.load_maps
    monkeypatch.setattr(groovekit, "load_maps", lambda: {**real(), **SPARSE})


def test_plain_groove_is_valid_with_metrics_only():
    result = run(dsl(("a", 4, GROOVE)))
    assert result["valid"] and result["midi"]
    assert checks(result, "error") == set() and checks(result, "warning") == set()
    assert result["metrics"]["family_hits"] == {"kick": 8, "snare": 8, "hat": 32}
    assert result["metrics"]["hits_per_bar"]["kick"] == 2


def test_sparse_groove_is_not_penalized():
    result = run(dsl(("space", 8, [("kick", "x..............."),
                                    ("snare", "........x.......")])))
    # Eight identical bars trip only the development prompt, never an error.
    assert result["valid"]
    assert checks(result, "error") == set()
    assert checks(result, "warning") == {"repetition"}
    varied = run(dsl(("space", 4, [("kick", "x...............")]),
                     ("answer", 4, [("kick", "x.......x.......")])))
    assert varied["valid"] and checks(varied, "warning") == set()


def test_unmapped_role_is_an_error_and_blocks_midi(sparse_kit):
    result = run((FIXTURES / "dsl" / "unmapped_china.dsl").read_text())
    assert not result["valid"] and result["midi"] is None
    error = next(f for f in result["findings"] if f["check"] == "kit_mapping")
    assert error["level"] == "error" and error["data"]["roles"] == ["CHINA_R"]


def test_fallback_resolution_is_reported_as_info(sparse_kit):
    result = run(dsl(("a", 1, [("kick", "x.......x......."), ("kick_l", "....x.......x..."),
                               ("snare", "....o.......x...")]), kit="Sparse Fixture Kit"))
    assert result["valid"]
    info = next(f for f in result["findings"] if f["check"] == "kit_mapping")
    assert info["level"] == "info"
    assert info["data"]["fallbacks"] == {"KICK_L": "KICK_R", "SNARE_GHOST": "SNARE"}


def test_exclusions_and_brief_mismatch_are_errors():
    result = run(dsl(("a", 4, GROOVE + [("ride", "x...............")])),
                 bars=8, exclude=["ride"])
    assert checks(result, "error") == {"brief_match", "exclusions"}
    assert result["midi"] is None


def test_empty_candidate_is_an_error():
    result = run(dsl(("a", 1, [("kick", "................")])))
    assert "empty" in checks(result, "error")


def plan(*sections, exclude=()):
    return validate_plan({"schema_version": 1, "tempo": 120, "meter": "4/4",
                          "kit_map": "GM Standard", "exclude_families": list(exclude),
                          "sections": [dict(id=i, bars=b, role="verse", **extra)
                                       for i, b, extra in sections]})


def test_plan_sections_must_match():
    good = plan(("verse", 2, {}), ("chorus", 2, {}))
    source = dsl(("verse", 2, GROOVE), ("chorus", 2, GROOVE))
    assert run(source, plan=good)["valid"]
    renamed = plan(("verse", 2, {}), ("bridge", 2, {}))
    assert "section_bounds" in checks(run(source, plan=renamed), "error")
    resized = plan(("verse", 3, {}), ("chorus", 1, {}))
    assert "section_bounds" in checks(run(source, plan=resized), "error")


def test_section_exclusion_applies_only_to_its_section():
    rules = plan(("verse", 2, {"exclude_families": ["ride"]}), ("chorus", 2, {}))
    ride = GROOVE + [("ride", "x...x...x...x...")]
    assert run(dsl(("verse", 2, GROOVE), ("chorus", 2, ride)), plan=rules)["valid"]
    result = run(dsl(("verse", 2, ride), ("chorus", 2, GROOVE)), plan=rules)
    error = next(f for f in result["findings"] if f["check"] == "exclusions")
    assert error["data"] == {"section": "verse", "families": ["ride"]}


def test_crowded_hands_and_feet_are_warnings_not_errors():
    hands = run(dsl(("a", 1, GROOVE + [("crash", "....x...........")])))  # snare+hat+crash
    assert hands["valid"] and "limbs" in checks(hands, "warning")
    two_hands = run(dsl(("a", 1, [("kick", "x..............."), ("snare", "x..............."),
                                  ("crash", "x...............")])))
    assert "limbs" not in checks(two_hands, "warning")
    feet = run(dsl(("a", 1, [("kick", "x..............."), ("kick_l", "x..............."),
                             ("hat_pedal", "x...............")])))
    warning = next(f for f in feet["findings"] if f["check"] == "limbs")
    assert warning["data"]["limb"] == "feet" and "bar 1 beat 1" in warning["message"]


def test_kick_riff_coverage_carries_the_audio_caveat():
    result = run(dsl(("a", 1, [("kick", "x..x....x......."), ("snare", "....x.......x...")])),
                 riff_kick="x..x....x..x....")
    info = next(f for f in result["findings"] if f["check"] == "kick_riff")
    assert info["level"] == "info"
    assert info["data"]["covered"] == 3 and info["data"]["riff_onsets"] == 4
    assert info["data"]["kicks_off_riff"] == 0
    assert "saved .rpp" in info["data"]["caveat"]


@pytest.mark.parametrize("name", ["simple_4_4", "blast_beat", "breakdown_pause",
                                  "verse_a", "verse_b"])
def test_fixtures_pass_every_error_gate_across_seeds(name):
    source = (FIXTURES / "dsl" / f"{name}.dsl").read_text()
    for seed in range(12):
        result = run(source, seed=seed)
        assert result["valid"], (seed, evaluate.summary(result["findings"]))


def test_summary_puts_errors_first():
    lines = evaluate.summary([evaluate.finding("info", "a", "i"),
                              evaluate.finding("error", "b", "e"),
                              evaluate.finding("warning", "c", "w")])
    assert lines == ["ERROR: e", "WARNING: w", "INFO: i"]


def test_groovegen_warns_about_dropped_roles(tmp_path, sparse_kit, capsys):
    import importlib.util
    path = Path(groovekit.__file__).resolve().parent.parent / "groovegen.py"
    spec = importlib.util.spec_from_file_location("groovegen_under_test", path)
    groovegen = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(groovegen)
    code = groovegen.main(["--dsl", str(FIXTURES / "dsl" / "unmapped_china.dsl"),
                           "--out", str(tmp_path / "x.mid"), "--seed", "1"])
    assert code == 0
    assert "no pitch or fallback for CHINA_R" in capsys.readouterr().out
