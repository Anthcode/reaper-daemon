# Archetype Misha Mansoor X Parameter Map

Captured live on 2026-09-20 from `expounding.rpp` (tracks `gtr-l` / `gtr-r`)
with `get_fx_parameters`. The plugin reports 2264 parameters; the first 181 are
the named controls, 181+ are the MIDI CC slots. Indices below are verified.

## Laser (Special FX section)

| Index | Name | Type | Notes |
| --- | --- | --- | --- |
| 7 | Active Special FX Section | toggle | On/Off for the whole Laser+Glitch section |
| 8 | Laser Mix | 0-100% | linear, `norm = pct/100` |
| 9 | Laser Preset | enum, 19 values | table below |
| 10 | Laser Auto | enum | `0.0 = Pedal`, `1.0 = Auto` (envelope follower) |
| 11 | Laser Envelope Speed | 0-100% | only used when 10 = Auto |
| 12 | Laser Envelope Sensitivity | 0-100% | only used when 10 = Auto |
| 13 | Laser Expression Pedal | 0-100% | the swept control when 10 = Pedal |
| 14 | Laser Active | toggle | the pedal's footswitch |
| 15 | Laser Trigger | toggle | |
| 16 | Laser Full Mix Latch | toggle | |

### Laser Preset (index 9) normalized values

Probed at 0.01 steps; midpoint of each plateau is safe to write.

| Normalized | Preset |
| --- | --- |
| 0.00 | PITCH - Dissonant Wham |
| 0.05 | PITCH - Dual Wham |
| 0.11 | PITCH - Inv 5th Chord |
| 0.17 | PITCH - Inverted Atonal |
| 0.22 | PITCH - Inverted Wham |
| 0.28 | PITCH - Inverted Wham2 |
| 0.33 | PITCH - Lead FX |
| 0.39 | PITCH - Lead FX2 |
| 0.44 | PITCH - Low to High 5th |
| 0.50 | PITCH - Oct + 5th up |
| 0.55 | PITCH - Off-Key Drop1 |
| 0.61 | PITCH - Off-Key Drop2 |
| 0.66 | PITCH - Super Wham |
| 0.72 | RING - Crazy |
| 0.775 | RING - Extreme Lasers |
| 0.83 | RING - Faux Vibe |
| 0.89 | RING - Pitch Tracked |
| 0.94 | RING - Pure RingMod |
| 0.99 | RING - Sanity |

## Other sections (index ranges, names verified)

17-29 Glitch · 30-36 Horizon Devices Compressor · 37-44 Tape Echo ·
45-48 Dual Octaver · 49-54 Horizon Devices Precision Drive · 55-56 Chaos ·
57-84 Amp (Clean / Rhythm / Lead) · 85-108 Cab + mics + room ·
109-145 per-amp EQ · 146-158 Modulator · 159-170 Stereo Delay ·
171-177 Reverb · 178-180 Preset Prev/Next/Bypass · 181+ MIDI CC.

Re-probe an individual parameter with `set_fx_param` (binary-searches a
formatted target) then `get_fx_parameters` with `offset`/`limit`, and restore
the original normalized value afterwards — the probe writes for real.

## Notes for automating this plugin

- **`Laser Auto` gates which control matters.** Left at `Auto` (1.0), index 13
  does nothing and the sound follows the player's picking envelope. Drop it to
  `Pedal` (0.0) for the duration of the effect if you want to draw the sweep.
- **Preset switching is a parameter move, not a program change.** Step index 9
  with `"shape": "square"` while index 14 (Laser Active) is 0, so the change
  lands silently.
- **Use `square` on 9, 10 and 14.** Any interpolating shape on the preset
  selector sweeps through every preset between the two endpoints.

## Gotcha: creating a new FX-parameter envelope

REAPER inserts its own point at time 0 (current parameter value) the moment an
FX-parameter envelope is created. `write_fx_param_automation` snapshots the
envelope *before* it exists, so that auto point is not in the replacement
preimage: if the write also submits a point at time 0 the reread finds two and
the whole write rolls back with `AUTOMATION_WRITE_UNCONFIRMED`.

Workaround: create the envelope first with a one-point write at a time other
than 0 (value = the parameter's current value), then do the real write. The
second write's declared range covers time 0, so the auto point is deleted and
replaced normally.
