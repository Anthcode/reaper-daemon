# Drum workshop

The workshop prepares separate briefs for drum ideas, compares the resulting
MIDI structures, and keeps audition feedback attached to the version you heard.
The composing agent still writes the notes. This tool doesn't call a model,
train one, or decide which part sounds best.

## Prepare a comparison

Save a brief as JSON:

```json
{
  "request_id": "song-a-first-pass",
  "project_id": "song-a",
  "description": "Eight bars of death metal groove with room to write riffs. Develop a motif and lead into the next section.",
  "tempo": 182,
  "bars": 8,
  "map": "RS Monarch",
  "seed": 182,
  "exclude_families": ["ride", "choke"],
  "preferences": [],
  "references": []
}
```

The exclusions in this example belong to this request. Leave the list empty
when the user hasn't excluded anything. Supported families are kick, snare,
tom, hat, ride, crash, china, splash, stack, bell and choke.

```powershell
python reaperd.py drum-workshop prepare brief.json --output workshop
```

This creates three folders, each with its own `request.json`:

| Candidate | Input |
| --- | --- |
| fresh | Current brief, without reference patterns or stored preferences |
| contrast | Another independent composition from the current brief |
| wildcard | Current brief, with a request to explore an unexpected musical idea |

If you supply approved references, `reference` replaces `contrast`. Only that
composition request includes the reference notes. References are DSL files:

```json
{"name": "Approved phrase", "path": "reference.dsl", "approved": true}
```

Paths are relative to the brief file unless absolute. The workshop stores a
snapshot, so editing the original reference later won't change the comparison.
Use `approved: true` only for material the user has actually approved.

## Plan the sections

Before composing a whole song, you can write an arrangement brief and turn it
into a plan of sections:

```json
{
  "tempo": 182,
  "kit_map": "RS Monarch",
  "exclude_families": ["choke"],
  "sections": [
    {"id": "verse", "bars": 8, "role": "verse", "kick_strategy": "riff_selective"},
    {"id": "breakdown", "bars": 4, "role": "breakdown", "exclude_families": ["ride"]}
  ],
  "riff": {"rpp": "song.rpp", "track": "GTR_DI"}
}
```

```powershell
python reaperd.py drum-workshop plan arrangement.json --output plan.json
python reaperd.py drum-workshop check-plan plan.json
```

The plan file contains the plan only, so you can edit it and run `check-plan`
again. Every section records where each decision came from: `user_explicit`
for what you wrote, `audio_inferred` for what riff analysis suggested, and
`default` otherwise. Instead of `sections`, you can give `total_bars` and let the
riff analysis propose where sections start. Without a riff, that gives one
section.

The optional `riff` reads the first audio item of a track in a saved project
file, not the live project. It proposes section starts and a kick grid for each
section. Your sections and kick grids always win. When the audio disagrees, the
result lists the disagreement and keeps your value. Audio-based choices come
with a caveat, because an onset detector's reading isn't a transcription of
intent.

Plans are 4/4, 40 to 320 BPM and at most 64 bars. Split a longer song into
several plans. A family that one section both requires and excludes is an error.

## Prepare from a plan

Add the plan file to the workshop brief as `"plan": "plan.json"`. The brief's
tempo, map and bar count must match the plan. The workshop then writes a
version 2 manifest with a snapshot of the plan, and each `request.json` gets
the part of the plan its composer may see:

| Request | Sees |
| --- | --- |
| fresh, contrast | Sections, bars, exclusions, required families, your kick grids, and your strategies, energy and transitions |
| wildcard | The same, without strategies, energy or transitions |
| reference | Everything fresh sees, plus any kick grid inferred from audio, as a sketch |

A kick grid inferred from audio counts as reference material. When the plan has
one, the reference request replaces contrast, even without reference DSL files,
and only that request sees the sketch. A kick grid you wrote yourself is an
instruction, so every request gets it.

Write one DSL section per plan section, in order, with the plan's section id
and bar count. Evaluation rejects a candidate whose sections differ from the
plan or that uses a family excluded in that section. For every section with a
kick grid, it reports how many of the grid's onsets the kick lands on.

## Compose without leaking the examples

Give each composer only its `request.json`, the drum DSL syntax and the kit
mapping. A genuinely independent composition needs a separate model context
that hasn't seen the references or sibling candidates. The folders themselves
aren't a sandbox. If one agent already knows the examples, disclose that the
comparison wasn't reference-blind.

Write `candidate.dsl` and `intent.json` in each candidate folder. The intent
file contains three short strings:

```json
{
  "premise": "The rhythmic idea this candidate establishes",
  "development": "How it repeats, changes and lands",
  "exploration": "What this candidate tries beyond the expected treatment"
}
```

Choose a musical premise before writing the grid. Give cymbals a purpose within
each phrase, such as keeping time, opening a phrase or answering an accent.
Avoid converting a preference into a fixed cymbal quota. Leave room for a
deliberately repetitive passage when the music calls for it.

The workshop currently supports 4/4, 1 to 64 bars, 40 to 320 BPM, and grids of
8, 12, 16, 24, 32, 48 or 64 steps per bar. Each candidate must match the brief's
tempo, bar count and kit. Exact second-based endings need a separate arrangement
step when the length doesn't fall on a bar boundary.

## Evaluate and audition

```powershell
python reaperd.py drum-workshop evaluate workshop
```

Each evaluation writes a new folder with MIDI files, frozen candidate DSL and
`report.json`. It uses the existing drum humanizer and the shared velocity
rule, then checks the serialized MIDI notes. Trailing silence extends to the
requested bar count. It doesn't insert anything into REAPER.

The report compares kick/snare onsets separately from drum-family onsets.
Changing velocities, kit pitches or cymbals won't hide an unchanged kick/snare
pattern. It also searches for the closest one- or two-bar phrase at different
bar positions. Similarity runs from 0 to 1 and measures shared onsets. A common
backbeat can score highly; that isn't proof of copying. The comparison doesn't
recognize every transformation, including half-time or double-time rewrites.

Next to `report.json`, each candidate gets `<candidate>.evaluation.json` with
the technical findings and raw metrics. Findings have three levels. An `error`
blocks the candidate: a brief mismatch, an excluded family, an empty part, a kit
role with no pitch and no fallback, a duplicate trigger, a velocity the shared
rule can't fix, or MIDI that doesn't read back. A `warning` asks for a listen:
more than two hands or two feet on one step, or eight or more bars that never
change. An `info` finding records measurements and kit fallbacks, such as a
rimshot played on the plain snare. There's no score, and a sparse part isn't
penalized for having few notes.

All valid candidates remain available, including the wildcard. Similarity
doesn't remove a candidate or select a winner. Explicit exclusions still apply
to every candidate. The hands-and-feet warning is a rough review prompt,
not a complete test of whether a drummer could play the part.

Audition the candidates through the same verified kit and routing. A
structural pass alone doesn't establish musical quality.

To render with a performer profile, add `"performer": "tight_modern_metal"` or
`"performer": "raw_black_metal"` to the workshop brief. A profile changes timing
looseness and the kick velocity band, not the notes, and composers never see
it. Without a profile, rendering is unchanged. Each profile also sets a kick
speed. A part whose closest pair of kicks is faster than that gets a warning,
not an error. Treat the speeds as starting points to adjust after listening.

The evaluation also finds fills: dense tom and snare runs that include a tom.
It warns when a fill runs over the start of a section, when a fill ends right
before a section whose downbeat has no kick or cymbal, and when the same fill
appears three or more times. When a plan asks for a pickup or tom run, a
section whose last bar has no fill gets an info note. Plan requests also carry
a transition hint with a suggested length in beats. The wildcard request
doesn't get one.

## Record what the user heard

Save the user's feedback in a JSON file:

```json
{
  "candidate_id": "wildcard",
  "report": "evaluation-REPLACE_WITH_ACTUAL_ID/report.json",
  "usefulness": "revise",
  "novelty": "new",
  "reason": "The displaced snare gave me a riff idea. Keep that and simplify the fill.",
  "scope": "request"
}
```

```powershell
python reaperd.py drum-workshop feedback workshop --feedback feedback.json
```

Add `"section_id": "chorus"` to say which section the note is about. It must
name a section of the evaluated candidate.

`usefulness` accepts `use`, `revise` or `reject`. `novelty` accepts `familiar`,
`new` or `unsure`. The tool binds the record to the evaluated DSL hash, even if
someone has since edited the working candidate. The caller must supply the
user's actual feedback; the tool can't verify who wrote it or whether they listened.

Feedback defaults to the request. Project scope applies to the named project.
Global scope requires explicit `confirmed: true`. Nothing automatically turns
a rating into a preference or changes a shared taste model.

Future briefs can carry explicit preference records with `text` and `scope`.
Request and project records also need `target_id`; unrelated records are
ignored. Global preferences require `confirmed: true`. Example scope preserves
an observation without treating it as a general rule. Active preferences appear
in the evaluation report for judging, not in the fresh composition requests.

## Revise one section

After a `revise` note on a section, edit that section of the candidate and
evaluate against the report you heard:

```powershell
python reaperd.py drum-workshop evaluate workshop --parent evaluation-ID/report.json
```

Each candidate then lists its changed and unchanged sections. If the revision
changed a section that no `revise` note asked for, the candidate gets a
warning. The comparison uses score onsets. Every render re-humanizes velocities
and timing, so those always differ.

## Put the chosen part in REAPER

Record `use` feedback for the candidate first, then pick it:

```json
{"candidate_id": "fresh", "report": "evaluation-ID/report.json"}
```

```powershell
python reaperd.py drum-workshop pick workshop --feedback choice.json
```

`pick` checks that the MIDI file still matches the one you heard and returns an
`insert_midi_file` payload for it. It doesn't contact REAPER. Before inserting,
check the bridge, resolve the destination track by GUID or verified name, and
use `dry_run` when unsure. The payload never replaces existing items. Insert
with `insert_midi_file`, read the take back with `get_midi_notes`, and add the
result to the choice as `readback`:

```powershell
python reaperd.py drum-workshop verify workshop --feedback choice-with-readback.json
```

`verify` compares pitches, velocities and positions with the auditioned MIDI.
It accounts for REAPER's take resolution. If they differ, one REAPER undo
removes the insert. REAPER may import MIDI by reference, so keep the workshop
folder while the item uses the file.

The MCP tool `drum_workshop` exposes the same actions with `path`, optional
`output` and `parent`, and an inline `feedback` object that also carries the
`pick` and `verify` records. The workshop itself only reads and writes local
files; the insert is a separate `insert_midi_file` call.
