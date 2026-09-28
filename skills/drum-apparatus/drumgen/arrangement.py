"""Build an ArrangementPlan from a brief and optional audio observations.

Offline and deterministic: no MIDI, no REAPER, no randomness. The output is a
plan in its input form, so a user can edit the JSON and validate it again.

Priority, highest first:
  1. what the user wrote in the brief (`user_explicit`);
  2. what riff/profile analysis of a saved .rpp suggests (`audio_inferred`);
  3. a neutral default (`default`).

Audio never overrides the user. When the two disagree, the plan keeps the
user's value and the result lists the disagreement so it can be looked at.
"""

import copy

from .arrangement_schema import (KICK_GRID_STEPS, MAX_BARS, PlanError,
                                 SCHEMA_VERSION, validate_plan)

BRIEF_FIELDS = {"tempo", "meter", "kit_map", "seed", "exclude_families",
                "total_bars", "sections", "riff"}
RIFF_FIELDS = {"rpp", "track", "start_bar", "keep_pct", "offset_steps"}
# Section fields that describe the user's intent; `evidence` and `start_bar`
# are the planner's to set.
USER_SECTION_FIELDS = {"id", "bars", "role", "energy", "kick_strategy", "snare_strategy",
                       "cymbal_strategy", "exclude_families", "require_families",
                       "transition_out", "kick_grid"}
PLANNER_VERSION = 1


def _check_brief(brief):
    if not isinstance(brief, dict):
        raise PlanError("brief must be an object")
    extra = set(brief) - BRIEF_FIELDS
    if extra:
        raise PlanError(f"Unknown brief fields: {sorted(extra)}")
    riff = brief.get("riff")
    if riff is not None:
        if not isinstance(riff, dict) or set(riff) - RIFF_FIELDS or not riff.get("rpp") \
                or not riff.get("track"):
            raise PlanError("riff needs rpp and track; optional start_bar, keep_pct, offset_steps")
    sections = brief.get("sections")
    if sections is not None:
        if not isinstance(sections, list) or not sections:
            raise PlanError("sections must be a nonempty list when given")
        for i, section in enumerate(sections):
            if not isinstance(section, dict):
                raise PlanError(f"sections[{i}] must be an object")
            extra = set(section) - USER_SECTION_FIELDS
            if extra:
                raise PlanError(f"Unknown fields in sections[{i}]: {sorted(extra)}; "
                                "evidence and start_bar are set by the planner")
    elif brief.get("total_bars") is None:
        raise PlanError("Give either sections or total_bars")


def _user_sections(brief):
    out = []
    for section in copy.deepcopy(brief["sections"]):
        evidence = {"section_boundary": "user_explicit"}
        for key in section:
            if key not in ("id", "bars"):
                evidence[key] = "user_explicit"
        section["evidence"] = evidence
        out.append(section)
    return out


def _starts(sections):
    starts, cursor = [], 1
    for section in sections:
        starts.append(cursor)
        cursor += section["bars"] if type(section.get("bars")) is int else 0
    return starts


def _inferred_sections(total, boundaries):
    """Split `total` bars at audio boundaries, or keep one default section."""
    starts = sorted({b for b in boundaries if 1 < b <= total})
    source = "audio_inferred" if starts else "default"
    edges = [1] + starts + [total + 1]
    return [{"id": f"section_{i + 1}", "bars": b - a, "role": "section",
             "evidence": {"section_boundary": source, "role": "default"}}
            for i, (a, b) in enumerate(zip(edges, edges[1:]))]


def build_plan(brief, observations=None):
    """Return {"plan": plan (input form), "normalized": validated plan,
    "disagreements": [...], "planner_version": N}.

    observations (all optional, plan-relative, bar 1 = first plan bar):
      boundaries  bar numbers that start a new section, from profile analysis
      kick_grid   one 'x'/'.' cell per 16th over the whole plan, from riff analysis
      source      where they came from, recorded in the disagreements
    """
    _check_brief(brief)
    obs = observations or {}
    disagreements = []
    if brief.get("sections"):
        sections = _user_sections(brief)
        total = sum(s["bars"] for s in sections if type(s.get("bars")) is int)
        if brief.get("total_bars") is not None and brief["total_bars"] != total:
            raise PlanError(f"total_bars is {brief['total_bars']} but the sections add up to {total}")
        audio = sorted(set(obs.get("boundaries", [])))
        user = _starts(sections)[1:]
        if audio and audio != user:
            disagreements.append({"field": "section_boundary", "kept": "user_explicit",
                                  "user": user, "audio": audio,
                                  "source": obs.get("source")})
    else:
        total = brief["total_bars"]
        if type(total) is not int or not 1 <= total <= MAX_BARS:
            raise PlanError(f"total_bars must be an integer from 1 to {MAX_BARS}")
        sections = _inferred_sections(total, obs.get("boundaries", []))

    grid = obs.get("kick_grid")
    if grid is not None:
        if not isinstance(grid, str) or len(grid) != total * KICK_GRID_STEPS:
            raise PlanError("observed kick_grid must cover the whole plan, one cell per 16th")
        for section, start in zip(sections, _starts(sections)):
            lo = (start - 1) * KICK_GRID_STEPS
            heard = grid[lo:lo + section["bars"] * KICK_GRID_STEPS]
            if "kick_grid" in section:
                if section["kick_grid"] != heard:
                    disagreements.append({"field": "kick_grid", "section": section["id"],
                                          "kept": "user_explicit", "source": obs.get("source")})
            elif "x" in heard:
                section["kick_grid"] = heard
                section["evidence"]["kick_grid"] = "audio_inferred"

    plan = {"schema_version": SCHEMA_VERSION, "tempo": brief.get("tempo"),
            "meter": brief.get("meter", "4/4"), "kit_map": brief.get("kit_map"),
            "seed": brief.get("seed", 1),
            "exclude_families": list(brief.get("exclude_families", [])),
            "sections": sections}
    normalized = validate_plan(plan)
    for section, derived in zip(plan["sections"], normalized["sections"]):
        section["start_bar"] = derived["start_bar"]
    return {"plan": plan, "normalized": normalized, "disagreements": disagreements,
            "planner_version": PLANNER_VERSION}


def observe(riff, total_bars):
    """Read boundaries and a kick grid from a saved project (never live state).

    riff: {rpp, track, start_bar=0, keep_pct=100, offset_steps=0}; start_bar is
    the item bar (0-based) that plan bar 1 sits on. Returns observations for
    build_plan with plan-relative bar numbers.
    """
    from .profile import profile_track
    from .riff import riff_to_kicks

    start = riff.get("start_bar", 0)
    kicks = riff_to_kicks(riff["rpp"], riff["track"], bars=total_bars, start_bar=start,
                          keep_pct=riff.get("keep_pct", 100),
                          offset_steps=riff.get("offset_steps", 0))
    profile = profile_track(riff["rpp"], riff["track"], bars=total_bars, start_bar=start)
    return {"boundaries": [b - start for b in profile["boundaries"]],
            "kick_grid": kicks["kick"],
            "source": {"rpp": riff["rpp"], "track": riff["track"], "start_bar": start,
                       "kind": "saved_project"}}


def plan_total_bars(brief):
    """Bars the plan will cover, known before any audio is read."""
    if brief.get("sections"):
        return sum(s.get("bars", 0) for s in brief["sections"] if type(s.get("bars")) is int)
    return brief.get("total_bars")
