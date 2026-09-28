"""Transitions between sections: hints for composers and checks on fills.

Hints go into composition requests; they suggest room and length, never notes.
Checks find fills in the written score with the humanizer's own detector
(humanize.detect_fills: a dense tom/snare run with at least one tom) and
report on how they sit against section seams. Every fill finding is a warning
or info: a fill that rolls over the barline into the next section is a common
musical choice, so it is flagged for a listen, not blocked.
"""

from fractions import Fraction

from .humanize import detect_fills

TICKS_PER_BAR = 1920  # score positions in 480-PPQ ticks, enough for 64ths
SIXTEENTH = TICKS_PER_BAR // 16
LANDING_FAMILIES = {"kick", "crash", "china", "splash", "stack"}

# What a planned transition asks of the last bar(s) of a section.
TRANSITIONS = {
    "pickup": "a short fill into the next section",
    "short_pickup": "a short fill into the next section",
    "tom_run": "a tom run into the next section",
    "stop": "stop together before the next section; leave the end open",
    "pause": "leave space at the end of the section",
    "silence_entry": "end in silence so the next section enters after it",
}


def hint(section, following=None):
    """A transition hint for one normalized plan section, or None."""
    kind = section.get("transition_out")
    if not kind:
        return None
    rise = None
    if following is not None and "energy" in section and "energy" in following:
        rise = round(following["energy"] - section["energy"], 3)
    if kind in ("pickup", "short_pickup"):
        beats = 1 if kind == "short_pickup" or section["bars"] < 2 else 2
    elif kind == "tom_run":
        beats = 4 if section["bars"] >= 4 else 2
    else:
        beats = None
    # A bigger jump in energy earns a longer run, within the section.
    if beats and rise is not None and rise >= 0.25:
        beats = min(beats * 2, 4 * section["bars"])
    out = {"kind": kind, "meaning": TRANSITIONS.get(kind, "as named by the plan")}
    if beats:
        out["suggested_beats"] = beats
    if rise is not None:
        out["energy_change"] = rise
    return out


def find(events):
    """Fills in a score: lists of (position, family), position in bars."""
    notes = [{"ppq": int(t * TICKS_PER_BAR), "pitch": f, "t": t, "family": f}
             for t, f in events if f in ("tom", "snare")]
    fam = {"tom": "tom", "snare": "snare_center"}
    return [[(n["t"], n["family"]) for n in run]
            for run in detect_fills(notes, fam, SIXTEENTH)]


def check(events, section_ranges, planned=None):
    """Findings about fills. section_ranges: [(name, start_bar, end_bar)], 0-based.

    planned: {section name: transition kind} from a plan, optional.
    """
    from .evaluate import finding  # evaluate imports this module

    findings, runs = [], find(events)
    starts = {start: name for name, start, _ in section_ranges if start > 0}
    shapes = []
    for run in runs:
        first, last = run[0][0], run[-1][0]
        crossed = [s for s in starts if first < s <= last]
        for seam in crossed:
            findings.append(finding("warning", "fill_seam",
                                    f"A fill runs from bar {int(first) + 1} over the start of "
                                    f"[{starts[seam]}]; check the landing",
                                    section=starts[seam]))
        ends_at = min((s for s in starts if s > last), default=None)
        if ends_at is not None and ends_at - last <= Fraction(1, 4):
            landed = {f for t, f in events if t == ends_at}
            if not landed & LANDING_FAMILIES:
                findings.append(finding("warning", "fill_landing",
                                        f"A fill ends right before [{starts[ends_at]}] and its "
                                        "downbeat has no kick or cymbal",
                                        section=starts[ends_at]))
        base = run[0][0] - int(run[0][0])
        shapes.append(tuple((t - run[0][0] + base, f) for t, f in run))
    repeats = max((shapes.count(s) for s in set(shapes)), default=0)
    if repeats >= 3:
        findings.append(finding("warning", "fill_repeat",
                                f"The same fill is played {repeats} times; check it isn't on autopilot",
                                times=repeats))
    for name, start, end in section_ranges:
        kind = (planned or {}).get(name)
        if kind in ("pickup", "short_pickup", "tom_run"):
            if not any(end - 1 <= run[-1][0] < end for run in runs):
                findings.append(finding("info", "fill_plan",
                                        f"[{name}] plans a {kind} but its last bar has no fill",
                                        section=name))
    findings.append(finding("info", "fills", f"Fills found: {len(runs)}", count=len(runs)))
    return findings
