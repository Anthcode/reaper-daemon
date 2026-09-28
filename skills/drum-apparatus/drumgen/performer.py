"""Performer profiles: named render settings for the existing humanizer.

A profile changes how a written part is played (timing looseness, kick
velocity band), never which notes are written. No profile renders exactly as
before. Speed limits here are configuration that produces a warning, not a
law of physics: players differ, and the numbers are starting points until
audition feedback calibrates them.
"""

from fractions import Fraction

PROFILE_VERSION = 1

PROFILES = {
    "tight_modern_metal": {
        "description": "Quantized-feeling modern metal: little timing drift, even kicks.",
        # Half the default humanize amount; kicks held high and even.
        "params": {"humanize": 10, "kick_vel_min": 100, "kick_vel_max": 114,
                   "kick_run_band": [106, 112]},
        # Both feet together: 16ths at 225 BPM is 15 strokes per second.
        "limits": {"max_kick_strokes_per_second": 15.0},
    },
    "raw_black_metal": {
        "description": "Looser, rawer playing: more drift, a wider kick band.",
        "params": {"humanize": 30, "kick_vel_min": 90, "kick_vel_max": 112,
                   "kick_run_band": [98, 110]},
        # Blast-beat tempos: 16ths at 255 BPM is 17 strokes per second.
        "limits": {"max_kick_strokes_per_second": 17.0},
    },
}


def get(name):
    """A copy of a named profile, or None for no profile."""
    if name is None:
        return None
    if name not in PROFILES:
        raise ValueError(f"Unknown performer profile {name!r}; known: {sorted(PROFILES)}")
    profile = PROFILES[name]
    return {"name": name, "version": PROFILE_VERSION, "description": profile["description"],
            "params": dict(profile["params"]), "limits": dict(profile["limits"])}


def fastest_kick_rate(events, tempo):
    """Strokes per second of the closest pair of kick onsets (score units = bars)."""
    kicks = sorted(t for t, family in events if family == "kick")
    gaps = [b - a for a, b in zip(kicks, kicks[1:]) if b > a]
    if not gaps:
        return 0.0
    seconds = min(gaps) * Fraction(240) / tempo  # a 4/4 bar lasts 240/tempo s
    return round(float(1 / seconds), 2)
