# Reference

`verify` compares the project with itself. `reference` compares it with a
record you are aiming at. Both sides are measured at the same loudness, so a
louder reference cannot pass for a brighter one.

## Example

Park the edit cursor on the chorus, or select it, then:

```bash
python3 reaperd.py reference ~/refs/target-master.wav --seconds 15
```

```text
[reference] target-master.wav  window 62.4s +15.0s (loudest_window)
[reference] mix: MASTER  scope=master_output  window 48.0s +15.0s
[reference] LUFS-I mix -15.8 | reference -9.6 -> mix compared at +6.2 dB
[reference] tonal balance at matched loudness (mix minus reference):
[reference]   sub       20-50 Hz       -3.4 dB
[reference]   bass      63-200 Hz      +1.2 dB
[reference]   low_mid   250-630 Hz     +2.6 dB
[reference]   mid       800-2k Hz      +0.4 dB
[reference]   presence  2.5k-5k Hz     -0.8 dB
[reference]   air       6.3k-20k Hz    -2.1 dB
[reference] crest factor mix 13.1 | ref 9.4 dB (+3.7)
[reference] peak-to-loudness mix 15.2 | ref 9.1 dB (+6.1)
[reference] width (side-mid) -2.4 dB, correlation +0.08
[reference] largest: sub -3.4 dB, low_mid +2.6 dB, air -2.1 dB
[reference] NOTE: Spectrum, crest factor and stereo are measured on ...
```

The numbers above are illustrative. Every `NOTE` line is a caveat; pass it
on with the result.

The MCP tool is `compare_to_reference` with the same options:
`reference_file`, `track`, `seconds`, `start_seconds` and
`reference_start_seconds`.

## What it does

1. Checks the reference WAV before rendering anything.
2. Captures the mix once (default: the master) at the usual bounds: an
   explicit start, the time selection, or the edit cursor.
3. Picks the reference window. It is as long as the mix capture and starts at
   `--ref-start`, or at the reference's loudest stretch when no start is
   given.
4. Measures both WAVs with the same meters. Integrated loudness comes from a
   built-in ITU-R BS.1770-4 meter. Spectrum, crest factor and stereo image
   come from Post Mortem.
5. Moves the mix to the reference loudness on paper and reports mix minus
   reference. A positive number means the mix has more.

Nothing in the project changes. The only side effect is one capture render.

## Reading it

- **Regions** are power sums of the 1/3-octave bands inside each range.
  Bands that sit 60 dB or more below the loudest band in both files count as
  noise floor. They are flagged `low_energy` and kept out of the region sums.
- **Peak-to-loudness ratio** (sample peak minus LUFS-I) and crest factor do
  not depend on gain. A higher number means the mix is less limited than the
  reference.
- **Width** is side RMS minus mid RMS. A positive delta means the mix is
  wider. When either side has no side signal at all, the result says it is
  mono (`mix_is_mono` / `reference_is_mono`) and reports no width delta.
- `reaper_lufs_i` is REAPER's own figure for the mix capture, included for
  comparison. The match uses the built-in meter on both files.

## Limits

- Compare like with like. A verse window against a chorus window measures
  the arrangement, not the mix.
- WAV only: 16, 24 or 32-bit PCM, or 32/64-bit float, in mono or stereo, up
  to 30 minutes. Convert MP3 or FLAC first.
- Post Mortem's analysis module must be importable from the Python that
  runs the tool. A `pipx` install alone is not enough; see the install steps
  in [MCP server](mcp.md). Without it the tool refuses before rendering, and
  the refusal names the exact install command.
- A silent mix capture or a silent reference window is refused. It is never
  compared.
- Capture needs `allow_audio_writes`, like `measure` and `verify`.
- Capturing a named track instead of the master measures that track or bus
  before the master. The result says so.
