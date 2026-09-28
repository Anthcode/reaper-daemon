"""Technical checks for one drum candidate: findings and raw metrics, no score.

Shared by the drum workshop and later arrangement stages. Nothing here judges
musical quality, picks a winner or contacts REAPER. Every check reports a
finding with a level:

  error    blocks export and insertion (the part cannot be what was asked for)
  warning  worth a listen before trusting it; never blocks audition
  info     a measurement or a note about how the kit resolved the part

Thresholds for warnings are deliberately conservative until audition feedback
calibrates them. A sparse groove is not a defect: note count is a metric only.
"""

from collections import Counter
from fractions import Fraction

from . import groovekit, smf
from .arrangement_schema import FAMILIES
from .goldenrule import enforce, violations

REPORT_VERSION = 1
LEVELS = ("error", "warning", "info")

# A candidate this long whose bars are all identical gets a repetition warning.
# Shorter parts, and long parts with any change, are left alone: a deliberately
# repetitive passage is a musical choice, not an error.
REPETITION_MIN_BARS = 8
# More than this many hand-played pieces on one step needs a third hand.
HANDS = 2
FEET = 2
AUDIO_CAVEAT = ("Riff onsets were inferred from audio in a saved .rpp, not live "
                "project state; treat the comparison as a hypothesis.")

# Lanes played by the feet. Everything else is a hand, including hats: the
# closed hat's pedal foot is held, not struck.
FOOT_LANES = {"kick", "kick_l", "hat_pedal"}


def family(lane):
    if lane.endswith("_choke"):
        return "choke"
    return next((f for f in sorted(FAMILIES) if lane.startswith(f)), lane)


def structure(parsed):
    """Exact score onsets in bar units, independent of kit, velocity and jitter."""
    events = set()
    start = 0
    for section in parsed["sections"]:
        grid = section["grid"]
        for lane in section["lanes"]:
            for step in range(section["bars"] * grid):
                if lane["cells"][step % len(lane["cells"])] != ".":
                    events.add((Fraction(start) + Fraction(step, grid), family(lane["lane"])))
        start += section["bars"]
    return events, start


def finding(level, check, message, **data):
    assert level in LEVELS
    item = {"level": level, "check": check, "message": message}
    if data:
        item["data"] = data
    return item


def _where(position):
    """1-based bar and beat text for a position in bar units."""
    bar = int(position)
    beat = (position - bar) * 4
    return f"bar {bar + 1} beat {float(beat) + 1:g}"


def _section_ranges(parsed):
    start, ranges = 0, []
    for section in parsed["sections"]:
        ranges.append((section["name"], start, start + section["bars"]))
        start += section["bars"]
    return ranges


def check_score(parsed, brief, plan=None):
    """Checks that need only the parsed score. Returns (findings, events, bars)."""
    findings = []
    events, bars = structure(parsed)
    got = (parsed["tempo"], parsed["map"], bars)
    want = (brief["tempo"], brief["map"], brief["bars"])
    if got != want:
        findings.append(finding("error", "brief_match",
                                "Candidate tempo, map and bars must match the brief",
                                candidate=list(got), brief=list(want)))
    forbidden = {f for _, f in events} & set(brief.get("exclude_families", []))
    if forbidden:
        findings.append(finding("error", "exclusions",
                                f"Excluded families present: {sorted(forbidden)}",
                                families=sorted(forbidden)))
    if not events:
        findings.append(finding("error", "empty", "Candidate contains no hits"))
    if plan is not None:
        findings.extend(_check_plan(parsed, events, plan))
    findings.extend(_check_limbs(parsed))
    findings.extend(_check_repetition(events, bars))
    return findings, events, bars


def _check_plan(parsed, events, plan):
    findings = []
    got = [(name, end - start) for name, start, end in _section_ranges(parsed)]
    want = [(s["id"], s["bars"]) for s in plan["sections"]]
    if got != want:
        findings.append(finding("error", "section_bounds",
                                "DSL sections must match the plan's sections (id and bars)",
                                candidate=got, plan=want))
        return findings
    for section in plan["sections"]:
        lo, hi = section["start_bar"] - 1, section["start_bar"] - 1 + section["bars"]
        hit = sorted({f for t, f in events if lo <= t < hi} & set(section["effective_exclusions"]))
        if hit:
            findings.append(finding("error", "exclusions",
                                    f"Excluded families present in {section['id']}: {hit}",
                                    section=section["id"], families=hit))
    return findings


def _check_limbs(parsed):
    """Conservative: counts pieces struck on one step, not stickings or reach."""
    findings, start = [], 0
    for section in parsed["sections"]:
        grid, total = section["grid"], section["bars"] * section["grid"]
        hands, feet = Counter(), Counter()
        for lane in section["lanes"]:
            for step in range(total):
                if lane["cells"][step % len(lane["cells"])] != ".":
                    (feet if lane["lane"] in FOOT_LANES else hands)[step] += 1
        for limbs, count, name in ((hands, HANDS, "hands"), (feet, FEET, "feet")):
            crowded = sorted(step for step, n in limbs.items() if n > count)
            if crowded:
                where = _where(Fraction(start) + Fraction(crowded[0], grid))
                findings.append(finding(
                    "warning", "limbs",
                    f"[{section['name']}] {len(crowded)} step(s) need more than "
                    f"{count} {name}, first at {where}",
                    section=section["name"], limb=name, steps=len(crowded)))
        start += section["bars"]
    return findings


def _bar_patterns(events, bars):
    per_bar = [set() for _ in range(bars)]
    for t, f in events:
        bar = int(t)
        per_bar[bar].add((t - bar, f))
    return per_bar


def _check_repetition(events, bars):
    per_bar = _bar_patterns(events, bars)
    longest = run = 1 if bars else 0
    for a, b in zip(per_bar, per_bar[1:]):
        run = run + 1 if a == b else 1
        longest = max(longest, run)
    findings = [finding("info", "repetition", f"Longest run of identical bars: {longest}",
                        longest_identical_run=longest)]
    if bars >= REPETITION_MIN_BARS and longest == bars:
        findings.append(finding("warning", "repetition",
                                f"All {bars} bars are identical; check the phrase develops",
                                bars=bars))
    return findings


def render_checked(source, bars, seed):
    """Render through the shared humanizer and prove the MIDI.

    Returns (findings, rendered notes, midi bytes or None, build info). Any
    error finding means the MIDI is not safe to export.
    """
    findings = []
    rendered, info = groovekit.build(source, seed=seed)
    if info["unmapped_roles"]:
        findings.append(finding("error", "kit_mapping",
                                f"Map {info['map']!r} cannot play {info['unmapped_roles']} "
                                "and has no fallback; those hits would be dropped",
                                roles=info["unmapped_roles"]))
    if info["fallback_roles"]:
        pairs = ", ".join(f"{a} -> {b}" for a, b in info["fallback_roles"].items())
        findings.append(finding("info", "kit_mapping", f"Played through fallbacks: {pairs}",
                                fallbacks=info["fallback_roles"]))
    if not rendered:
        # Nothing to write; the score checks already say why (empty or unmapped).
        return findings, rendered, None, info
    end = bars * 4 * info["ppq"]
    rendered = sorted(rendered, key=lambda n: (n["tick"], n["pitch"]))
    for note in rendered:
        note["tick"] = min(max(0, note["tick"]), end - 1)
        note["dur"] = min(note["dur"], end - note["tick"])
    # Same-pitch duplicate triggers are invalid. Shorten only overlapping releases.
    previous = {}
    for note in rendered:
        old = previous.get(note["pitch"])
        if old:
            if old["tick"] == note["tick"]:
                findings.append(finding("error", "duplicate_trigger",
                                        "Duplicate same-pitch trigger after kit mapping",
                                        pitch=note["pitch"], tick=note["tick"]))
                return findings, rendered, None, info
            old["dur"] = min(old["dur"], note["tick"] - old["tick"])
        previous[note["pitch"]] = note
    rows = [dict(index=i, ppq=n["tick"], pitch=n["pitch"]) for i, n in enumerate(rendered)]
    vel = enforce(rows, {i: n["vel"] for i, n in enumerate(rendered)},
                  min_gap=1,
                  bands={pitch: (min(n["vel"] for n in rendered if n["pitch"] == pitch),
                                 max(n["vel"] for n in rendered if n["pitch"] == pitch))
                         for pitch in {n["pitch"] for n in rendered}})
    if violations(rows, vel):
        findings.append(finding("error", "golden_rule",
                                "Cannot enforce dynamics inside rendered velocity bands"))
        return findings, rendered, None, info
    for i, note in enumerate(rendered):
        note["vel"] = vel[i]
    midi = smf.write_smf(rendered, ppq=info["ppq"], tempo=info["tempo"], end_tick=end)
    readback = smf.parse_smf(midi)
    expected = [{"tick": n["tick"], "pitch": n["pitch"], "vel": n["vel"]} for n in rendered]
    if readback["notes"] != expected:
        findings.append(finding("error", "midi_readback",
                                "MIDI serialization did not preserve the notes"))
        return findings, rendered, None, info
    return findings, rendered, midi, info


def kick_riff(events, riff_kick, grid=16, start_bar=0):
    """Compare kick onsets with a riff kick grid (riff.onsets_to_kick_grid).

    Returns an info finding: the share of riff onsets a kick lands on (within
    half a riff step) and the kicks with no riff onset. Always carries the
    audio caveat.
    """
    tolerance = Fraction(1, 2 * grid)
    riff = [Fraction(i, grid) + start_bar for i, c in enumerate(riff_kick) if c != "."]
    span = Fraction(len(riff_kick), grid)
    kicks = sorted(t for t, f in events if f == "kick" and start_bar <= t < start_bar + span)
    covered = sum(1 for r in riff if any(abs(k - r) <= tolerance for k in kicks))
    extra = sum(1 for k in kicks if not any(abs(k - r) <= tolerance for r in riff))
    share = round(covered / len(riff), 4) if riff else None
    return finding("info", "kick_riff",
                   f"Kick lands on {covered} of {len(riff)} riff onsets; {extra} kicks off the riff",
                   riff_onsets=len(riff), covered=covered, coverage=share,
                   kicks_off_riff=extra, caveat=AUDIO_CAVEAT)


def metrics(events, bars, tempo, notes):
    hits = Counter(f for _, f in events)
    return {"notes": notes, "length_bars": bars,
            "length_seconds": bars * 240 / tempo,
            "family_hits": dict(hits),
            "hits_per_bar": {f: round(n / bars, 3) for f, n in sorted(hits.items())} if bars else {}}


def evaluate_candidate(source, parsed, brief, seed, plan=None, riff_kick=None):
    """Run every check on one parsed candidate.

    brief: {tempo, map, bars, exclude_families}. plan: a normalized plan from
    arrangement_schema.validate_plan, or None. riff_kick: optional kick grid
    string from riff analysis. Returns {valid, findings, metrics, midi, rendered}.
    """
    findings, events, bars = check_score(parsed, brief, plan)
    midi, rendered = None, []
    if not any(f["level"] == "error" for f in findings):
        more, rendered, midi, info = render_checked(source, bars, seed)
        findings.extend(more)
        if midi is None and not any(f["level"] == "error" for f in findings):
            findings.append(finding("error", "empty", "Nothing rendered to MIDI"))
    if riff_kick:
        findings.append(kick_riff(events, riff_kick))
    valid = midi is not None and not any(f["level"] == "error" for f in findings)
    return {"valid": valid, "findings": findings,
            "metrics": metrics(events, bars, brief["tempo"], len(rendered)),
            "midi": midi if valid else None, "rendered": rendered}


def summary(findings):
    """Short human-readable version of a finding list, most severe first."""
    order = {level: i for i, level in enumerate(LEVELS)}
    return [f"{f['level'].upper()}: {f['message']}"
            for f in sorted(findings, key=lambda f: order[f["level"]])]
