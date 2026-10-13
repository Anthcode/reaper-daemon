# Song planner and instrument library

Two pieces that work together: a named **instrument library**, so a sound is
picked by name, and a **song planner** that turns an approved song sheet into
a drafted project. The composing workflow an agent follows is in
[skills/song-planner/SKILL.md](../skills/song-planner/SKILL.md).

## Instrument library

```powershell
python reaperd.py instrument list [--role synth] [--tag dark]
python reaperd.py instrument show argent-l
python reaperd.py instrument save dark-pad --track Pad --role synth --library "Serum 2: PD - Dark Matter" --range 36-96 --tag pad --tag dark
python reaperd.py instrument load dark-pad --track Pad
python reaperd.py instrument remove dark-pad
python reaperd.py instrument save-rig monarch-drums --track Monarch --role drums
python reaperd.py instrument load monarch-drums          # a rig adds its own tracks
```

MCP: the `instruments` tool, with the same actions.

Kontakt and Serum keep their presets where REAPER can't see them. So load each
sound by hand once, then `save` it. That writes the track's FX chain, plugin
state included, to REAPER's FXChains folder as `instrument-<name>.RfxChain`,
and registers the name with its role, note maps, range and tags. `load`
splices it onto an empty track and checks that it landed.

| Field | Meaning |
| --- | --- |
| `role` | guitar, bass, drums, synth, keys, orchestral, vocal, other |
| `maps` | `{guitar\|bass\|drums: map name}` the generators use (`argent_e`, `nolly_e`, `RS Monarch`, ...) |
| `range` | `[low, high]` playable MIDI notes, when known |
| `articulations` | `{name: keyswitch note}` for libraries not covered by a map |
| `chain` | saved chain name, or absent until the sound is saved once |
| `rig`, `play_track`, `rig_tracks` | for multi-output instruments: the track template, the track that takes MIDI, and the tracks it adds |

**Rigs.** A Kontakt drum kit with multiple outputs plays through its routing:
Kick, Snare, room and bus tracks fed from separate outputs. A chain can't hold
that, so loaded alone such a kit is silent. `save-rig` starts at the
instrument track, follows its sends and folders, and saves that self-contained
block of tracks as `TrackTemplates/instrument-<name>.RTrackTemplate` (no media
items). It refuses a block that contains an unrelated track or a folder
reaching outside it. Loading a rig adds all of its tracks (bridge
`insert_track_template`); the planner sends that part's MIDI to the play track.

`instruments/catalog.json` ships the libraries the generators already know,
without chains. Your saved sounds go in `instruments/user.json`, which stays
on this machine and wins over the catalog by name.

## Song sheet

```json
{
 "title": "Working Title", "tempo": 140, "meter": "4/4", "key": "E phrygian",
 "source": {"kind": "brief", "detail": "the brief, verbatim"},
 "parts": {"drums": "monarch-drums", "guitar_l": "argent-l", "guitar_r": "argent-r",
           "bass": "nolly-bass", "layers": {"pad": "dark-pad"}},
 "guitar_map": "argent_e",
 "sections": [
  {"name": "intro", "bars": 8, "energy": 2, "feel": "mp",
   "description": "what this section does", "chords": "E5 | F5",
   "drums": {"grid": 16, "lanes": {"kick": "X.......X.......", "ride": "x.x.x.x.x.x.x.x."}},
   "riff": ["o...............", "o.......M......."],
   "layers": {"pad": [{"time": 0, "duration": 16, "pitch": 52, "velocity": 84}]},
   "wildcard": false}
 ]
}
```

- `sections` play in order; each starts on the bar after the previous one ends.
- `drums` lanes and cells follow the drum DSL. Leave `drums` out for silence.
- `riff` is 16-cell guitar riff notation. It repeats to fill the section; leave
  it out and guitars and bass rest. Bass follows the guitar riff.
- `layers` notes are in beats from the section start.
- `guitar_map` is optional; otherwise the guitar instrument's map is used.
  `low_string` overrides the riff root note.
- Limits: 4/4 only, one tempo per song, 1-128 bars per section.

## Flow

```powershell
python songplan.py check songs/working-title/sheet.json
python songplan.py show songs/working-title/sheet.json
python songplan.py approve songs/working-title/sheet.json   # only after a person said yes
python songplan.py draft songs/working-title/sheet.json
```

`check` lists every problem at once. `approve` stamps a hash of the sheet, so
any later edit cancels the approval and `draft` refuses. `draft` opens a new
project tab (the bridge's `new_project_tab`), sets the tempo, adds one track
per part and loads its saved sound, adds a region per section, then writes the
drums as one item, both guitars and the bass as one item each, and each layer
as one item. Every step is reported separately. A track whose instrument has no
saved sound gets its MIDI but stays silent until the sound is loaded.

The draft tab is never saved for you.
