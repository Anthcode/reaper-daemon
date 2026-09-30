# Lighting rig inventory

Stage one of DMX lighting support. You describe the rig in a JSON file and
`lighting_inventory.py` checks it. The result tells an agent which fixtures exist,
where they sit in the DMX universe, which channels are taken, what each fixture
can do, and which features the safety settings lock.

This stage does not control lights. It sends no DMX, Art-Net or MIDI, does not
load or talk to the dmXis plugin, and adds no bridge, MCP or `reaperd.py`
commands. The addresses in the file are what you declare, not what the hardware
reports. Check them on the fixtures themselves.

```bash
python lighting_inventory.py                                 # validates the example
python lighting_inventory.py config/lighting/lighting_rig.json
```

Copy `config/lighting/lighting_rig.example.json` to `lighting_rig.json` in the same
folder and edit it. On success the script prints the inventory as JSON. On failure it
lists every problem it found and exits non-zero. From Python:

```python
from lighting_inventory import load_rig, validate_rig
inventory = load_rig('config/lighting/lighting_rig.json')   # raises ValueError
errors = validate_rig(data)                                  # [] when valid
```

Keep the file generic. Do not put serial numbers, USB identifiers, local paths,
network addresses or credentials in it. Unknown keys are rejected, so a typo
cannot slip through quietly. Keys that start with `_` are comments and are ignored.

## File format

| Key | Meaning |
| --- | --- |
| `schema_version` | Must be `1`. |
| `rig.name` | Free text. |
| `rig.controller` | `dmxis`. The only controller the schema knows today. |
| `rig.universe` | Universe number, default `1`. |
| `rig.universe_size` | Channels in the universe, `1..512`, default `512`. |
| `safety.strobe_enabled` | Global strobe switch. `false` in the example. |
| `safety.atmosphere_enabled` | Global switch for hazers and foggers. `false` in the example. |
| `safety.max_intensity` | Ceiling for every fixture, `0..1`. |
| `fixtures[]` | One entry per fixture, described below. |

Each fixture has:

| Key | Meaning |
| --- | --- |
| `id` | Stable identifier, lowercase `a-z0-9_`, starting with a letter. Unique. |
| `name` | Display name. |
| `type` | `par`, `wash`, `bar`, `spot`, `moving_head`, `blinder`, `strobe`, `hazer`. |
| `role` | `front_wash`, `back_wash`, `side_wash`, `key`, `spot`, `audience`, `effect`, `ambient`, `atmosphere`. |
| `position` | `stage_left`, `stage_right`, `center`, `upstage`, `downstage`, `floor`, `truss`, `drum_riser`. |
| `address` | First DMX channel, `1..universe_size`. |
| `channels` | Channel kinds in DMX order. The list length is the fixture's footprint. |
| `enabled` | Optional, default `true`. Disabled fixtures keep their channels reserved but get no role. |
| `max_intensity` | Optional. May lower the rig ceiling, never raise it. |
| `strobe_allowed` | Optional, default `false`. Opt-in for this fixture's strobe or shutter channel. |
| `notes` | Optional free text. |

Channel kinds: `dimmer`, `red`, `green`, `blue`, `white`, `amber`, `uv`,
`color_wheel`, `gobo`, `pan`, `pan_fine`, `tilt`, `tilt_fine`, `speed`, `zoom`,
`focus`, `strobe`, `shutter`, `macro`, `mode`, `fog`, `fan`, `control`. List every
channel the fixture's DMX mode uses, including ones you never plan to drive, so the
footprint matches the hardware.

## What gets checked

- Every fixture fits in the universe: `address + len(channels) - 1` stays within
  `universe_size`.
- No two fixtures share a DMX channel. The error names the channel and the fixture
  that already holds it.
- Ids are unique, and no channel kind appears twice in one fixture.
- Types, roles, positions and channel kinds come from the lists above.
- A fixture's `max_intensity` does not exceed `safety.max_intensity`.
- With `safety.strobe_enabled: false`, no fixture may set `strobe_allowed: true`
  and every fixture of type `strobe` must have `enabled: false`.
- With `safety.atmosphere_enabled: false`, every fixture with a `fog` channel must
  have `enabled: false`.
- `strobe_allowed: true` only makes sense on a fixture with a `strobe` or `shutter`
  channel.

Strobe needs two switches: the global `safety.strobe_enabled` and the fixture's own
`strobe_allowed`. Turning on the global switch does not unlock fixtures that never
opted in.

## Inventory output

For each fixture the inventory reports `address`, `end_address`, `footprint`, a
`channel_map` from channel kind to absolute DMX channel, derived `capabilities`
(`rgb` needs red, green and blue; `movement` needs pan and tilt; `atmosphere`
needs fog), the effective `max_intensity`, `strobe_allowed`, and
`locked_channels`: the strobe or shutter channels a future controller must hold
closed. The rig level adds `roles` (enabled fixture ids per role),
`channels_used`, `free_ranges` for patching new fixtures, and a `caveat` that
the data is declared and unverified.

`locked_channels` is a promise the future control layer has to keep. Nothing in
this stage enforces it at the DMX level, because nothing in this stage sends DMX.
