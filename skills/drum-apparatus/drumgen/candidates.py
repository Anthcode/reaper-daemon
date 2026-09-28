"""What each composition request may see of an ArrangementPlan.

The workshop's composers are agents; this module does not write notes. It
decides which parts of a plan go into which request:

  - Hard constraints (sections, bars, exclusions, required families, and a
    kick grid the user wrote) go to every request, the wildcard included.
  - The user's strategy fields (kick/snare/cymbal strategy, energy, transition)
    describe the expected treatment. fresh and contrast get them; the wildcard
    gets the structure without them, so it is free to depart from it.
  - A kick grid inferred from audio is a sketch, i.e. reference material. It
    goes only to the `reference` request, never to fresh or the wildcard.
"""

GENERATOR_VERSION = 1

HARD_FIELDS = ("id", "start_bar", "bars", "role", "effective_exclusions", "require_families")
TREATMENT_FIELDS = ("energy", "kick_strategy", "snare_strategy", "cymbal_strategy",
                    "transition_out")

# One direction per role, from docs/drum-arrangement-plan.md. They steer how a
# candidate differs; none of them overrides a hard constraint.
DIRECTIONS = {
    "fresh": "tight: let the kick support the riff and the plan's strategies closely.",
    "contrast": "breathing: leave more space, keep cymbals restrained, contrast the sections.",
    "reference": "Transform the sketch or references; say in intent.json what you changed.",
    "wildcard": "Keep the sections and hard constraints; the treatment inside them is yours.",
}


def has_sketch(plan):
    """True when some section's kick grid came from audio analysis."""
    return any(s.get("kick_grid") and s["evidence"].get("kick_grid") == "audio_inferred"
               for s in plan["sections"])


def request_plan(plan, role):
    """The part of a normalized plan a request with this role may see."""
    sections = []
    for section in plan["sections"]:
        view = {k: section[k] for k in HARD_FIELDS}
        if role != "wildcard":
            view.update({k: section[k] for k in TREATMENT_FIELDS if k in section})
        grid = section.get("kick_grid")
        source = section["evidence"].get("kick_grid")
        if grid and source == "user_explicit":
            view["kick_grid"] = grid
        elif grid and source == "audio_inferred" and role == "reference":
            view["kick_sketch"] = {"grid": grid, "source": "audio_inferred",
                                   "caveat": "Onsets from a saved .rpp; a hypothesis, not the part."}
        sections.append(view)
    return {"meter": plan["meter"], "total_bars": plan["total_bars"],
            "plan_direction": DIRECTIONS[role], "sections": sections,
            "dsl_rule": ("Write one DSL [section] per plan section, in order, named by its id "
                         "and with its bars.")}


def kick_targets(plan):
    """Section kick grids for the evaluation's kick-riff comparison."""
    return [{"section": s["id"], "start_bar": s["start_bar"] - 1, "grid": s["kick_grid"],
             "source": s["evidence"].get("kick_grid", "default")}
            for s in plan["sections"] if s.get("kick_grid")]
