"""reference_compare — measure the mix against a reference track.

Every other measurement in this repo compares the project with itself
(before/after, track/track). This one answers the question an engineer asks
with a reference loaded in the session: at the SAME loudness, where does my
mix have more or less than the record I am aiming at?

Flow: validate the reference WAV header (cheap, before any render), capture
the mix with verifyloop.measure, pick the reference window, then analyze both
WAVs with the same two meters:

- loudness: the ITU-R BS.1770-4 integrated meter below, run on BOTH files, so
  the loudness match is never one meter's number against another's;
- spectrum, crest factor, stereo image: Post Mortem's analyze_wav, also on
  both files.

The mix is then gain-matched to the reference on paper (no audio is
changed) and every comparison is mix minus reference. Positive = the mix has
more of it than the reference.

Read-only toward the project: one capture render, nothing mutated. Stdlib
only. Post Mortem (and with it numpy) is required for the spectrum, so the
tool refuses BEFORE rendering when it is not installed; the loudness meter
does not need it.
"""

import math
import operator
import os
import secrets
import struct
import tempfile
from array import array
from datetime import datetime, timezone

import verifyloop

# A reference longer than this is not a song; reading it costs real time.
MAX_REFERENCE_SECONDS = 30 * 60
MIN_WINDOW_SECONDS = 1.0

# BS.1770-4 gating.
ABSOLUTE_GATE_LUFS = -70.0
RELATIVE_GATE_LU = -10.0
SUBBLOCK_SECONDS = 0.1   # 400 ms blocks at 75% overlap = 4 sub-blocks, step 1

# Region boundaries sit at geometric midpoints between nominal 1/3-octave
# centers, so a band reported at 794 Hz or at 800 Hz lands in the same region.
REGIONS = (
    ("sub", "20-50 Hz", 18.0, 56.0),
    ("bass", "63-200 Hz", 56.0, 224.0),
    ("low_mid", "250-630 Hz", 224.0, 710.0),
    ("mid", "800-2k Hz", 710.0, 2240.0),
    ("presence", "2.5k-5k Hz", 2240.0, 5600.0),
    ("air", "6.3k-20k Hz", 5600.0, 24000.0),
)
# A band this far below the loudest band of either file is noise floor on
# both sides; its delta is reported but flagged, never ranked.
LOW_ENERGY_BELOW_PEAK_DB = 60.0
# Region differences smaller than this are not called out as deviations.
NOTABLE_DELTA_DB = 1.0

STATUS_COMPARED = "COMPARED"
STATUS_REFUSED = "REFUSED"


def _error(code, details, **extra):
    return {"ok": False, "status": STATUS_REFUSED,
            "error": {"code": code, "details": details}, **extra}


# ---------------------------------------------------------------------------
# WAV reading (PCM 16/24/32 and IEEE float 32/64, plain or EXTENSIBLE)
# ---------------------------------------------------------------------------

class WavError(ValueError):
    pass


def read_wav_header(path):
    """Parse a RIFF/WAVE header. Returns a dict with fmt fields, the raw fmt
    chunk bytes (so a slice can be written in the identical format), and the
    data chunk's offset and usable size."""
    try:
        size = os.path.getsize(path)
        f = open(path, "rb")
    except OSError as e:
        raise WavError(f"cannot open {path}: {e}") from e
    with f:
        head = f.read(12)
        if len(head) < 12 or head[:4] != b"RIFF" or head[8:12] != b"WAVE":
            if head[:4] == b"RF64":
                raise WavError("RF64 WAV files are not supported; export the "
                               "reference as a standard WAV")
            raise WavError("not a WAV file (no RIFF/WAVE header); convert the "
                           "reference to WAV first")
        fmt_raw = None
        data_offset = data_size = None
        while True:
            chunk = f.read(8)
            if len(chunk) < 8:
                break
            cid, csize = chunk[:4], struct.unpack("<I", chunk[4:])[0]
            if cid == b"fmt ":
                fmt_raw = f.read(csize)
            elif cid == b"data":
                data_offset = f.tell()
                # Streamed writers leave 0 or 0xFFFFFFFF here: trust the file.
                data_size = min(csize, size - data_offset)
                if fmt_raw is not None:
                    break
            if cid != b"fmt ":
                f.seek(csize, 1)
            if csize % 2:
                f.seek(1, 1)
    if fmt_raw is None or len(fmt_raw) < 16:
        raise WavError("WAV has no usable fmt chunk")
    if data_offset is None:
        raise WavError("WAV has no data chunk")
    tag, channels, rate, _byte_rate, block_align, bits = struct.unpack(
        "<HHIIHH", fmt_raw[:16])
    if tag == 0xFFFE and len(fmt_raw) >= 26:
        tag = struct.unpack("<H", fmt_raw[24:26])[0]
    if tag == 1 and bits in (16, 24, 32):
        kind = "int"
    elif tag == 3 and bits in (32, 64):
        kind = "float"
    else:
        raise WavError(f"unsupported WAV encoding (format tag {tag}, {bits} "
                       "bit); use 16/24/32-bit PCM or 32/64-bit float")
    if channels not in (1, 2):
        raise WavError(f"{channels}-channel WAV is not supported; use a mono "
                       "or stereo reference")
    if rate <= 0 or block_align != channels * bits // 8:
        raise WavError("WAV fmt chunk is inconsistent")
    frames = data_size // block_align
    return {"kind": kind, "bits": bits, "channels": channels,
            "sample_rate": rate, "block_align": block_align,
            "frames": frames, "duration_seconds": frames / rate,
            "fmt_raw": fmt_raw, "data_offset": data_offset}


def _decode(raw, hdr):
    """Interleaved samples as floats in [-1, 1] (array('d'))."""
    bits = hdr["bits"]
    if hdr["kind"] == "float":
        return array("d", array("f" if bits == 32 else "d", raw))
    if bits == 24:
        n = len(raw) // 3
        wide = bytearray(n * 4)
        wide[1::4] = raw[0::3]
        wide[2::4] = raw[1::3]
        wide[3::4] = raw[2::3]
        ints, scale = array("i", bytes(wide)), 1.0 / 2147483648.0
    elif bits == 16:
        ints, scale = array("h", raw), 1.0 / 32768.0
    else:
        ints, scale = array("i", raw), 1.0 / 2147483648.0
    return array("d", [v * scale for v in ints])


def read_frames(path, hdr, start_frame, n_frames):
    start_frame = max(0, min(start_frame, hdr["frames"]))
    n_frames = max(0, min(n_frames, hdr["frames"] - start_frame))
    with open(path, "rb") as f:
        f.seek(hdr["data_offset"] + start_frame * hdr["block_align"])
        raw = f.read(n_frames * hdr["block_align"])
    return _decode(raw[: len(raw) - len(raw) % hdr["block_align"]], hdr)


def write_wav_slice(src, hdr, start_frame, n_frames, dst):
    """Copy a frame range into a new WAV with the source's exact fmt chunk."""
    with open(src, "rb") as f:
        f.seek(hdr["data_offset"] + start_frame * hdr["block_align"])
        data = f.read(n_frames * hdr["block_align"])
    fmt = hdr["fmt_raw"]
    pad = b"\x00" if len(fmt) % 2 else b""
    body = (b"WAVE" + b"fmt " + struct.pack("<I", len(fmt)) + fmt + pad
            + b"data" + struct.pack("<I", len(data)) + data)
    with open(dst, "wb") as out:
        out.write(b"RIFF" + struct.pack("<I", len(body)) + body)


# ---------------------------------------------------------------------------
# ITU-R BS.1770-4 integrated loudness
# ---------------------------------------------------------------------------

def k_weighting_coefficients(rate):
    """The two K-weighting biquads for any sample rate, from the analog
    prototype (same derivation as libebur128). At 48 kHz this reproduces the
    BS.1770 table: shelf b0=1.53512486, high-pass a1=-1.99004745."""
    f0, gain_db, q = 1681.974450955533, 3.999843853973347, 0.7071752369554196
    k = math.tan(math.pi * f0 / rate)
    vh = 10.0 ** (gain_db / 20.0)
    vb = vh ** 0.4996667741545416
    a0 = 1.0 + k / q + k * k
    shelf = ((vh + vb * k / q + k * k) / a0, 2.0 * (k * k - vh) / a0,
             (vh - vb * k / q + k * k) / a0,
             2.0 * (k * k - 1.0) / a0, (1.0 - k / q + k * k) / a0)
    f0, q = 38.13547087602444, 0.5003270373238773
    k = math.tan(math.pi * f0 / rate)
    a0 = 1.0 + k / q + k * k
    highpass = (1.0, -2.0, 1.0,
                2.0 * (k * k - 1.0) / a0, (1.0 - k / q + k * k) / a0)
    return shelf, highpass


def _k_weighted_subblock_power(x, rate, subblock):
    """Run one channel through both K-weighting stages and return the summed
    square of each consecutive sub-block (the tail shorter than a sub-block
    is dropped, as the gating blocks would drop it)."""
    (b0, b1, b2, a1, a2), (c0, c1, c2, d1, d2) = k_weighting_coefficients(rate)
    x1 = x2 = y1 = y2 = z1 = z2 = 0.0
    sums = []
    acc, count = 0.0, 0
    for s in x:
        y = b0 * s + b1 * x1 + b2 * x2 - a1 * y1 - a2 * y2
        x2, x1 = x1, s
        z = c0 * y + c1 * y1 + c2 * y2 - d1 * z1 - d2 * z2
        y2, y1 = y1, y
        z2, z1 = z1, z
        acc += z * z
        count += 1
        if count == subblock:
            sums.append(acc)
            acc, count = 0.0, 0
    return sums


def integrated_loudness(samples, channels, rate):
    """BS.1770-4 integrated loudness (LUFS) of interleaved float samples, or
    None when nothing passes the absolute gate (silence) or the audio is
    shorter than one 400 ms block. Channel weights are 1.0 (mono/stereo)."""
    subblock = int(round(rate * SUBBLOCK_SECONDS))
    per_channel = [_k_weighted_subblock_power(samples[ch::channels], rate,
                                              subblock)
                   for ch in range(channels)]
    n_sub = min(len(p) for p in per_channel)
    if n_sub < 4:
        return None
    sub = [sum(p[i] for p in per_channel) for i in range(n_sub)]
    block_len = 4 * subblock
    blocks = [(sub[i] + sub[i + 1] + sub[i + 2] + sub[i + 3]) / block_len
              for i in range(n_sub - 3)]

    def lufs(power):
        return -0.691 + 10.0 * math.log10(power) if power > 0 else -math.inf

    gated = [z for z in blocks if lufs(z) > ABSOLUTE_GATE_LUFS]
    if not gated:
        return None
    relative = lufs(sum(gated) / len(gated)) + RELATIVE_GATE_LU
    final = [z for z in gated if lufs(z) > relative]
    if not final:
        return None
    return round(lufs(sum(final) / len(final)), 2)


def file_loudness(path, start_seconds=0.0, duration_seconds=None):
    hdr = read_wav_header(path)
    rate = hdr["sample_rate"]
    start = int(round(start_seconds * rate))
    n = (hdr["frames"] - start if duration_seconds is None
         else int(round(duration_seconds * rate)))
    return integrated_loudness(read_frames(path, hdr, start, n),
                               hdr["channels"], rate)


def loudest_window(path, hdr, window_seconds):
    """Start (seconds) of the loudest window of the given length, judged by
    plain RMS over 100 ms steps. A heuristic for picking the chorus-like part
    of the reference; the loudness that matters is measured afterwards with
    the real meter. Streams the file, so a long reference stays cheap."""
    rate, align = hdr["sample_rate"], hdr["block_align"]
    step = max(1, int(round(rate * SUBBLOCK_SECONDS)))
    energies = []
    with open(path, "rb") as f:
        f.seek(hdr["data_offset"])
        remaining = hdr["frames"]
        while remaining >= step:
            raw = f.read(step * align)
            if len(raw) < step * align:
                break
            x = _decode(raw, hdr)
            energies.append(sum(map(operator.mul, x, x)))
            remaining -= step
    width = max(1, int(round(window_seconds / SUBBLOCK_SECONDS)))
    if len(energies) <= width:
        return 0.0
    run = sum(energies[:width])
    best, best_i = run, 0
    for i in range(1, len(energies) - width + 1):
        run += energies[i + width - 1] - energies[i - 1]
        if run > best:
            best, best_i = run, i
    return best_i * step / rate


# ---------------------------------------------------------------------------
# Comparison arithmetic (pure: dicts in, dict out)
# ---------------------------------------------------------------------------

def _fin(v):
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return float(v) if math.isfinite(v) else None


def _band_map(spectrum):
    out = {}
    for b in spectrum or []:
        if isinstance(b, dict):
            f, lv = _fin(b.get("freq_hz")), _fin(b.get("level_db"))
            if f is not None and lv is not None:
                out[f] = lv
    return out


def _power_sum(levels):
    if not levels:
        return None
    total = sum(10.0 ** (db / 10.0) for db in levels)
    return round(10.0 * math.log10(total), 2) if total > 0 else None


def compare_metrics(mix, ref):
    """mix/ref: {"lufs_i", "sample_peak_db", "crest_factor_db",
    "spectrum_third_octave", "stereo"} measured by the same meters. Returns
    the comparison after gain-matching the mix to the reference loudness.
    Every delta is mix minus reference; a value missing on either side yields
    no delta, never a fabricated zero."""
    mix_lufs, ref_lufs = _fin(mix.get("lufs_i")), _fin(ref.get("lufs_i"))
    gain = round(ref_lufs - mix_lufs, 2)
    result = {"loudness_match": {
        "mix_lufs_i": mix_lufs, "reference_lufs_i": ref_lufs,
        "gain_applied_to_mix_db": gain}}

    mix_bands = {f: lv + gain for f, lv in _band_map(
        mix.get("spectrum_third_octave")).items()}
    ref_bands = _band_map(ref.get("spectrum_third_octave"))
    common = sorted(f for f in mix_bands if f in ref_bands)
    bands = []
    if common:
        peak = max(max(mix_bands[f], ref_bands[f]) for f in common)
        for f in common:
            m, r = mix_bands[f], ref_bands[f]
            band = {"freq_hz": f, "mix_db": round(m, 1),
                    "reference_db": round(r, 1), "delta_db": round(m - r, 1)}
            if max(m, r) < peak - LOW_ENERGY_BELOW_PEAK_DB:
                band["low_energy"] = True
            bands.append(band)
    result["bands"] = bands

    regions = []
    for name, label, lo, hi in REGIONS:
        live = [b for b in bands if lo <= b["freq_hz"] < hi
                and not b.get("low_energy")]
        m = _power_sum([mix_bands[b["freq_hz"]] for b in live])
        r = _power_sum([ref_bands[b["freq_hz"]] for b in live])
        if m is None or r is None:
            continue
        regions.append({"region": name, "range": label, "mix_db": m,
                        "reference_db": r, "delta_db": round(m - r, 1)})
    result["regions"] = regions
    result["largest_deviations"] = sorted(
        (r for r in regions if abs(r["delta_db"]) >= NOTABLE_DELTA_DB),
        key=lambda r: -abs(r["delta_db"]))[:3]

    dynamics = {}
    a, b = _fin(mix.get("crest_factor_db")), _fin(ref.get("crest_factor_db"))
    if a is not None and b is not None:
        dynamics["mix_crest_factor_db"] = a
        dynamics["reference_crest_factor_db"] = b
        dynamics["crest_factor_delta_db"] = round(a - b, 2)
    # Peak-to-loudness ratio: gain-independent, so it survives the match.
    a, b = _fin(mix.get("sample_peak_db")), _fin(ref.get("sample_peak_db"))
    if a is not None and b is not None:
        dynamics["mix_plr_db"] = round(a - mix_lufs, 2)
        dynamics["reference_plr_db"] = round(b - ref_lufs, 2)
        dynamics["plr_delta_db"] = round(
            dynamics["mix_plr_db"] - dynamics["reference_plr_db"], 2)
    result["dynamics"] = dynamics

    stereo = {}
    ms, rs = mix.get("stereo") or {}, ref.get("stereo") or {}
    for key, nd in (("correlation", 3), ("balance_db", 2)):
        a, b = _fin(ms.get(key)), _fin(rs.get(key))
        if a is not None and b is not None:
            stereo[f"mix_{key}"] = a
            stereo[f"reference_{key}"] = b
            stereo[f"{key}_delta"] = round(a - b, nd)

    def width(st):
        side, mid = _fin(st.get("side_rms_db")), _fin(st.get("mid_rms_db"))
        return side - mid if side is not None and mid is not None else None
    a, b = width(ms), width(rs)
    if a is not None and b is not None:
        # side minus mid, dB: positive delta = the mix is wider.
        stereo["mix_width_db"] = round(a, 2)
        stereo["reference_width_db"] = round(b, 2)
        stereo["width_db_delta"] = round(a - b, 2)
    result["stereo"] = stereo
    return result


# ---------------------------------------------------------------------------
# compare_to_reference
# ---------------------------------------------------------------------------

def _temp_path(tag):
    out_dir = os.path.join(tempfile.gettempdir(), "reaper-reference")
    os.makedirs(out_dir, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%f")
    return os.path.join(out_dir, f"{tag}-{stamp}-{secrets.token_hex(4)}.wav")


def _analyze(analyzer, path):
    stats = analyzer(path)
    return verifyloop._sanitize_nonfinite({
        "sample_peak_db": stats.sample_peak_db,
        "rms_db": stats.rms_db,
        "crest_factor_db": stats.crest_factor_db,
        "silence_fraction": stats.silence_fraction,
        "spectrum_third_octave": stats.spectrum_third_octave,
        "stereo": stats.stereo,
    })


def _unlink(path):
    try:
        os.unlink(path)
    except OSError:
        pass


def compare_to_reference(sender, reference_file, track="master",
                         seconds=None, start_seconds=None,
                         reference_start_seconds=None,
                         _analyzer_loader=None):
    """Capture `track` (default the master) at the usual verify bounds and
    compare it with a reference WAV at matched loudness.

    reference_start_seconds: where the reference window starts. Default: the
    loudest window of the same length as the mix capture.
    Returns {"ok": True, "status": "COMPARED", ...} or a REFUSED error.
    """
    # Resolved at call time so tests (and the MCP suite) can swap the loader.
    analyzer = (_analyzer_loader or verifyloop._load_analyzer)()
    if analyzer is None:
        return _error("POSTMORTEM_MISSING",
                      "compare_to_reference needs Post Mortem's analysis "
                      "module (spectrum, crest factor, stereo image) and it "
                      "is not importable. Install Post Mortem (docs/mcp.md); "
                      "nothing was rendered.")
    if not isinstance(reference_file, str) or not reference_file.strip():
        return _error("BAD_REFERENCE", "reference_file must be a WAV path")
    reference_file = os.path.abspath(os.path.expanduser(reference_file.strip()))
    try:
        hdr = read_wav_header(reference_file)
    except WavError as e:
        return _error("BAD_REFERENCE", str(e))
    if hdr["duration_seconds"] < MIN_WINDOW_SECONDS:
        return _error("BAD_REFERENCE",
                      f"reference is {hdr['duration_seconds']:.2f} s long; "
                      f"at least {MIN_WINDOW_SECONDS:g} s is needed")
    if hdr["duration_seconds"] > MAX_REFERENCE_SECONDS:
        return _error("BAD_REFERENCE",
                      f"reference is {hdr['duration_seconds'] / 60:.0f} min "
                      "long; trim it to the song")
    if reference_start_seconds is not None:
        try:
            reference_start_seconds = float(reference_start_seconds)
        except (TypeError, ValueError):
            return _error("BAD_REFERENCE",
                          "reference_start_seconds must be a number")
        if not (0 <= reference_start_seconds
                <= hdr["duration_seconds"] - MIN_WINDOW_SECONDS):
            return _error("BAD_REFERENCE",
                          f"reference_start_seconds {reference_start_seconds:g}"
                          f" is outside the reference (length "
                          f"{hdr['duration_seconds']:.1f} s)")

    # The one render. keep_wav: the mix WAV goes through the same meters as
    # the reference, then it is deleted here.
    mix = verifyloop.measure(sender, track, seconds=seconds,
                             start_seconds=start_seconds, keep_wav=True,
                             _analyzer_loader=lambda: analyzer)
    if not mix.get("ok"):
        return {**mix, "status": STATUS_REFUSED}
    mix_wav = mix.get("file_path")
    slice_path = None
    try:
        if mix.get("silent"):
            return _error("SILENT_CAPTURE",
                          f"the mix capture is silent ({mix.get('silence_reason')}); "
                          "no comparison is made on dead air. Park the edit "
                          "cursor where the song plays and rerun.")
        if mix.get("metrics_source") != "postmortem":
            return _error("ANALYSIS_FAILED",
                          "Post Mortem could not analyze the mix capture: "
                          f"{mix.get('analysis_error')}")
        try:
            mix_lufs = file_loudness(mix_wav)
        except WavError as e:
            return _error("ANALYSIS_FAILED", f"mix capture unreadable: {e}")
        if mix_lufs is None:
            return _error("SILENT_CAPTURE", "the mix capture never rises "
                          "above the -70 LUFS absolute gate")

        window = min(float(mix["bounds"]["duration_seconds"]),
                     hdr["duration_seconds"])
        if reference_start_seconds is None:
            ref_start, ref_source = loudest_window(reference_file, hdr,
                                                   window), "loudest_window"
        else:
            ref_start, ref_source = reference_start_seconds, "explicit_start"
        window = min(window, hdr["duration_seconds"] - ref_start)
        rate = hdr["sample_rate"]
        start_frame = int(round(ref_start * rate))
        n_frames = int(round(window * rate))

        slice_path = _temp_path("reference")
        write_wav_slice(reference_file, hdr, start_frame, n_frames, slice_path)
        ref_lufs = integrated_loudness(
            read_frames(reference_file, hdr, start_frame, n_frames),
            hdr["channels"], rate)
        try:
            ref = _analyze(analyzer, slice_path)
        except Exception as e:
            return _error("ANALYSIS_FAILED", "Post Mortem could not analyze "
                          f"the reference: {type(e).__name__}: {e}")
        ref["metrics_source"] = "postmortem"
        silent, reason = verifyloop._judge_silence(ref)
        if ref_lufs is None or silent:
            return _error("SILENT_REFERENCE",
                          "the reference window is silent "
                          f"({reason or 'below the -70 LUFS gate'}); pass "
                          "reference_start_seconds at a part that plays")
        ref["lufs_i"] = ref_lufs
        mix_for_compare = dict(mix, lufs_i=mix_lufs)
        comparison = compare_metrics(mix_for_compare, ref)
    finally:
        _unlink(mix_wav)
        if slice_path:
            _unlink(slice_path)

    caveats = [
        "Spectrum, crest factor and stereo are measured on the two windows "
        "named in the result. They only compare like with like if both windows are "
        "the same kind of section (chorus against chorus).",
        "The loudness match is computed, not applied: no audio and no "
        "project setting was changed.",
    ]
    scope = mix.get("capture_scope")
    if scope == "isolated_track":
        caveats.append(
            "The mix side is an isolated track/bus capture, pre-master. It "
            "stands for the whole mix only if everything routes through it.")
    elif scope != "master_output":
        caveats.append(
            f"The mix capture scope is {scope!r}, not master_output: read the "
            "provenance note before treating it as the whole mix.")
    if hdr["channels"] == 1:
        caveats.append("The reference is mono: BS.1770 counts its single "
                       "channel once, about 3 dB below the same audio as "
                       "dual-mono stereo, and stereo deltas are omitted "
                       "where Post Mortem reports none.")

    return {
        "ok": True,
        "status": STATUS_COMPARED,
        "reference": {
            "file": reference_file,
            "sample_rate": rate,
            "channels": hdr["channels"],
            "length_seconds": round(hdr["duration_seconds"], 2),
            "window": {"start_seconds": round(ref_start, 3),
                       "duration_seconds": round(n_frames / rate, 3),
                       "source": ref_source},
            "lufs_i": ref_lufs,
            "sample_peak_db": ref.get("sample_peak_db"),
            "rms_db": ref.get("rms_db"),
        },
        "mix": {
            "track": mix.get("track"),
            "capture_scope": scope,
            "isolation_verified": mix.get("isolation_verified"),
            "capture_note": mix.get("capture_note"),
            "bounds": mix.get("bounds"),
            "bounds_source": mix.get("bounds_source"),
            "lufs_i": mix_lufs,
            "reaper_lufs_i": mix.get("lufs_i"),
            "true_peak_db": mix.get("true_peak_db"),
            "sample_peak_db": mix.get("sample_peak_db"),
            "rms_db": mix.get("rms_db"),
        },
        **comparison,
        "convention": "every delta is mix minus reference after matching the "
                      "mix's integrated loudness to the reference; positive "
                      "= the mix has more",
        "meters": {"loudness": "ITU-R BS.1770-4 integrated, same meter on "
                               "both files",
                   "spectrum_dynamics_stereo": "Post Mortem analyze_wav on "
                                               "both files"},
        "caveats": caveats,
    }


def format_comparison(res):
    """Short human report for a COMPARED result."""
    lines = []
    ref, mix, lm = res["reference"], res["mix"], res["loudness_match"]
    w, b = ref["window"], mix.get("bounds") or {}
    name = (mix.get("track") or {}).get("name", "?")
    lines.append(f"[reference] {os.path.basename(ref['file'])}  "
                 f"window {w['start_seconds']:.1f}s +{w['duration_seconds']:.1f}s"
                 f" ({w['source']})")
    lines.append(f"[reference] mix: {name}  scope={mix.get('capture_scope')}  "
                 f"window {b.get('start_seconds', 0):.1f}s "
                 f"+{b.get('duration_seconds', 0):.1f}s")
    lines.append(f"[reference] LUFS-I mix {lm['mix_lufs_i']:.1f} | reference "
                 f"{lm['reference_lufs_i']:.1f} -> mix compared at "
                 f"{lm['gain_applied_to_mix_db']:+.1f} dB")
    if res.get("regions"):
        lines.append("[reference] tonal balance at matched loudness "
                     "(mix minus reference):")
        for r in res["regions"]:
            lines.append(f"[reference]   {r['region']:<9} {r['range']:<13} "
                         f"{r['delta_db']:+5.1f} dB")
    d = res.get("dynamics") or {}
    if "crest_factor_delta_db" in d:
        lines.append(f"[reference] crest factor mix {d['mix_crest_factor_db']:.1f}"
                     f" | ref {d['reference_crest_factor_db']:.1f} dB "
                     f"({d['crest_factor_delta_db']:+.1f})")
    if "plr_delta_db" in d:
        lines.append(f"[reference] peak-to-loudness mix {d['mix_plr_db']:.1f}"
                     f" | ref {d['reference_plr_db']:.1f} dB "
                     f"({d['plr_delta_db']:+.1f})")
    s = res.get("stereo") or {}
    if "width_db_delta" in s:
        lines.append(f"[reference] width (side-mid) {s['width_db_delta']:+.1f} dB"
                     + (f", correlation {s['correlation_delta']:+.2f}"
                        if "correlation_delta" in s else ""))
    dev = res.get("largest_deviations") or []
    if dev:
        lines.append("[reference] largest: " + ", ".join(
            f"{r['region']} {r['delta_db']:+.1f} dB" for r in dev))
    else:
        lines.append(f"[reference] no region differs by {NOTABLE_DELTA_DB:g} dB"
                     " or more")
    for c in res.get("caveats") or []:
        lines.append(f"[reference] NOTE: {c}")
    return "\n".join(lines)
