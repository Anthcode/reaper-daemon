"""ArrangementPlan contract: validation only, no planning, MIDI or REAPER.

A plan describes the sections of a drum part before anyone writes notes. It is
the input the workshop will hand to composers (docs/drum-arrangement-plan.md).
This module pins the shape so later stages can rely on it:

  - the engine and the workshop are 4/4 only, so `meter` must be "4/4";
  - the workshop renders at most 64 bars, so the plan does too;
  - sections are consecutive like DSL [sections]; `start_bar` is derived and
    an explicit one must match, which rules out gaps and overlaps;
  - every exclusion is a hard rule; a family both required and excluded in
    one section is a contradiction, not a preference to weigh;
  - `evidence` names where each decision came from. `audio_inferred` came from
    a saved .rpp, never live state, and the normalized plan says so.

Strategy, role and transition values are slugs here. Their vocabulary belongs
to the stages that act on them.
"""

import re

from .catalog import load_maps

SCHEMA_VERSION = 1
METERS = {"4/4"}
MAX_BARS = 64
TEMPO_RANGE = (40, 320)
SEED_MAX = 2**31 - 1

# Drum families as the workshop counts them (drum_workshop.family()).
FAMILIES = frozenset({"kick", "snare", "tom", "hat", "ride", "crash", "china",
                      "splash", "stack", "bell", "choke"})
EVIDENCE_SOURCES = {"user_explicit", "audio_inferred", "default"}
AUDIO_CAVEAT = ("Inferred from audio in a saved .rpp, not live project state; "
                "a hypothesis the user can override.")

PLAN_FIELDS = {"schema_version", "tempo", "meter", "kit_map", "seed",
               "exclude_families", "sections"}
SECTION_FIELDS = {"id", "start_bar", "bars", "role", "energy", "kick_strategy",
                  "snare_strategy", "cymbal_strategy", "exclude_families",
                  "require_families", "transition_out", "evidence"}
SLUG_FIELDS = ("role", "kick_strategy", "snare_strategy", "cymbal_strategy",
               "transition_out")
_SLUG = re.compile(r"^[a-z][a-z0-9_]{0,47}$")


class PlanError(ValueError):
    pass


def _int(value, name, low, high):
    # bool is an int subclass; reject it like drum_workshop.integer() does.
    if type(value) is not int or not low <= value <= high:
        raise PlanError(f"{name} must be an integer from {low} to {high}")
    return value


def _slug(value, name):
    if not isinstance(value, str) or not _SLUG.match(value):
        raise PlanError(f"{name} must be a lowercase slug (a-z, 0-9, _)")
    return value


def _families(value, name):
    if not isinstance(value, list) or any(f not in FAMILIES for f in value):
        raise PlanError(f"{name} must list known drum families: {sorted(FAMILIES)}")
    if len(set(value)) != len(value):
        raise PlanError(f"{name} repeats a family")
    return list(value)


def _unknown(obj, allowed, where):
    extra = set(obj) - allowed
    if extra:
        raise PlanError(f"Unknown {where} fields: {sorted(extra)}")


def validate_plan(plan):
    """Validate an ArrangementPlan and return a normalized copy.

    The copy carries every section's derived `start_bar`, its effective
    exclusions (global + section) and a `caveats` list for audio-inferred
    evidence. Raises PlanError on the first violation. The input is not
    modified.
    """
    if not isinstance(plan, dict):
        raise PlanError("plan must be an object")
    _unknown(plan, PLAN_FIELDS, "plan")
    if plan.get("schema_version") != SCHEMA_VERSION:
        raise PlanError(f"schema_version must be {SCHEMA_VERSION}")
    tempo = _int(plan.get("tempo"), "tempo", *TEMPO_RANGE)
    if plan.get("meter") not in METERS:
        raise PlanError(f"meter must be one of {sorted(METERS)}; the engine renders 4/4 only")
    kit_map = plan.get("kit_map")
    if not isinstance(kit_map, str) or kit_map not in load_maps():
        raise PlanError("kit_map must name a known drum map")
    seed = _int(plan.get("seed", 1), "seed", 0, SEED_MAX)
    global_excluded = _families(plan.get("exclude_families", []), "exclude_families")

    sections = plan.get("sections")
    if not isinstance(sections, list) or not sections:
        raise PlanError("sections must be a nonempty list")
    out_sections, caveats, seen = [], [], set()
    cursor = 1
    for index, section in enumerate(sections):
        where = f"sections[{index}]"
        if not isinstance(section, dict):
            raise PlanError(f"{where} must be an object")
        _unknown(section, SECTION_FIELDS, where)
        sid = _slug(section.get("id"), f"{where}.id")
        if sid in seen:
            raise PlanError(f"Duplicate section id: {sid}")
        seen.add(sid)
        bars = _int(section.get("bars"), f"{sid}.bars", 1, MAX_BARS)
        if "start_bar" in section and section["start_bar"] != cursor:
            raise PlanError(f"{sid}.start_bar is {section['start_bar']!r}; sections are "
                            f"consecutive, so it must be {cursor}")
        for key in SLUG_FIELDS:
            if key == "role" or key in section:
                _slug(section.get(key), f"{sid}.{key}")
        energy = section.get("energy")
        if energy is not None and (type(energy) not in (int, float)
                                   or energy != energy or not 0 <= energy <= 1):
            raise PlanError(f"{sid}.energy must be a number from 0 to 1")
        excluded = _families(section.get("exclude_families", []), f"{sid}.exclude_families")
        required = _families(section.get("require_families", []), f"{sid}.require_families")
        effective = sorted(set(global_excluded) | set(excluded))
        clash = sorted(set(required) & set(effective))
        if clash:
            raise PlanError(f"{sid} both requires and excludes {clash}")
        evidence = section.get("evidence", {})
        if not isinstance(evidence, dict):
            raise PlanError(f"{sid}.evidence must be an object")
        for key, source in evidence.items():
            _slug(key, f"{sid}.evidence key")
            if source not in EVIDENCE_SOURCES:
                raise PlanError(f"{sid}.evidence.{key} must be one of {sorted(EVIDENCE_SOURCES)}")
            if source == "audio_inferred":
                caveats.append({"section": sid, "field": key, "caveat": AUDIO_CAVEAT})
        normalized = dict(section, start_bar=cursor, exclude_families=excluded,
                          require_families=required, effective_exclusions=effective,
                          evidence=dict(evidence))
        out_sections.append(normalized)
        cursor += bars
    total = cursor - 1
    if total > MAX_BARS:
        raise PlanError(f"Plan is {total} bars; the workshop renders at most {MAX_BARS}. "
                        "Split the song into several plans by section groups.")
    return {"schema_version": SCHEMA_VERSION, "tempo": tempo, "meter": plan["meter"],
            "kit_map": kit_map, "seed": seed, "exclude_families": global_excluded,
            "total_bars": total, "sections": out_sections, "caveats": caveats}
