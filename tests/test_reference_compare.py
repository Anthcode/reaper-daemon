"""Tests for reference_compare (mix vs reference at matched loudness).

No live REAPER: the mix capture runs through verifyloop.measure against an
in-process scripted sender that writes a real WAV, and Post Mortem is a fake
analyzer, so the suite needs no numpy. The loudness meter is checked against
EBU Tech 3341 test signals.
"""

import json
import math
import os
import struct
import sys
import types
import wave

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import reaperd  # noqa: E402
import reaper_mcp  # noqa: E402
import reference_compare as rc  # noqa: E402
import verifyloop  # noqa: E402
from bridge_fakes import fake_bridge_script  # noqa: E402


# --- WAV helpers --------------------------------------------------------------

def _tone(seconds, rate, db, freq=1000.0):
    amp = 10.0 ** (db / 20.0)
    return [amp * math.sin(2 * math.pi * freq * i / rate)
            for i in range(int(seconds * rate))]


def _write(path, left, right=None, rate=48000, bits=16, kind="int",
           extensible=False):
    """Write interleaved samples in any supported encoding."""
    chans = [left] if right is None else [left, right]
    nch = len(chans)
    frames = bytearray()
    for i in range(len(left)):
        for ch in chans:
            v = max(-1.0, min(1.0, ch[i]))
            if kind == "float":
                frames += struct.pack("<f" if bits == 32 else "<d", v)
            elif bits == 16:
                frames += struct.pack("<h", int(round(v * 32767)))
            elif bits == 24:
                frames += struct.pack("<i", int(round(v * 8388607)))[:3]
            else:
                frames += struct.pack("<i", int(round(v * 2147483647)))
    align = nch * bits // 8
    tag = 3 if kind == "float" else 1
    if extensible:
        guid_tail = b"\x00\x00\x00\x00\x10\x00\x80\x00\x00\xaa\x00\x38\x9b\x71"
        fmt = struct.pack("<HHIIHHHHI", 0xFFFE, nch, rate, rate * align, align,
                          bits, 22, bits, 0) + struct.pack("<H", tag) + guid_tail
    else:
        fmt = struct.pack("<HHIIHH", tag, nch, rate, rate * align, align, bits)
    body = (b"WAVE" + b"fmt " + struct.pack("<I", len(fmt)) + fmt
            + b"LIST" + struct.pack("<I", 4) + b"INFO"   # a chunk to skip
            + b"data" + struct.pack("<I", len(frames)) + bytes(frames))
    with open(path, "wb") as f:
        f.write(b"RIFF" + struct.pack("<I", len(body)) + body)


# --- WAV reading -----------------------------------------------------------------

@pytest.mark.parametrize("bits,kind,ext", [
    (16, "int", False), (24, "int", False), (32, "int", False),
    (32, "float", False), (64, "float", False), (24, "int", True),
    (32, "float", True)])
def test_header_and_decode_every_encoding(tmp_path, bits, kind, ext):
    path = str(tmp_path / "x.wav")
    left = [0.5, -0.25, 0.0, 0.125]
    _write(path, left, [-v for v in left], rate=44100, bits=bits, kind=kind,
           extensible=ext)
    hdr = rc.read_wav_header(path)
    assert (hdr["channels"], hdr["sample_rate"], hdr["frames"]) == (2, 44100, 4)
    x = rc.read_frames(path, hdr, 0, 4)
    tol = 1e-4 if bits == 16 else 1e-6
    for i, v in enumerate(left):
        assert abs(x[2 * i] - v) < tol and abs(x[2 * i + 1] + v) < tol
    # a frame range reads only those frames
    assert len(rc.read_frames(path, hdr, 1, 2)) == 4


def test_header_rejects_non_wav_and_odd_layouts(tmp_path):
    mp3 = tmp_path / "song.mp3"
    mp3.write_bytes(b"ID3\x03" + b"\x00" * 64)
    with pytest.raises(rc.WavError, match="not a WAV"):
        rc.read_wav_header(str(mp3))
    rf64 = tmp_path / "big.wav"
    rf64.write_bytes(b"RF64" + b"\x00" * 64)
    with pytest.raises(rc.WavError, match="RF64"):
        rc.read_wav_header(str(rf64))
    surround = str(tmp_path / "six.wav")
    with wave.open(surround, "wb") as w:
        w.setnchannels(6)
        w.setsampwidth(2)
        w.setframerate(48000)
        w.writeframes(b"\x00" * 12 * 10)
    with pytest.raises(rc.WavError, match="6-channel"):
        rc.read_wav_header(surround)
    eight_bit = str(tmp_path / "u8.wav")
    with wave.open(eight_bit, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(1)
        w.setframerate(8000)
        w.writeframes(b"\x80" * 10)
    with pytest.raises(rc.WavError, match="unsupported"):
        rc.read_wav_header(eight_bit)


def test_write_wav_slice_keeps_format_and_range(tmp_path):
    src, dst = str(tmp_path / "src.wav"), str(tmp_path / "dst.wav")
    ramp = [i / 1000.0 for i in range(1000)]
    _write(src, ramp, ramp, rate=8000, bits=24, extensible=True)
    hdr = rc.read_wav_header(src)
    rc.write_wav_slice(src, hdr, 100, 50, dst)
    out = rc.read_wav_header(dst)
    assert out["fmt_raw"] == hdr["fmt_raw"] and out["frames"] == 50
    x = rc.read_frames(dst, out, 0, 50)
    assert abs(x[0] - 0.1) < 1e-6 and abs(x[-1] - 0.149) < 1e-6


# --- loudness meter ------------------------------------------------------------

def test_k_weighting_matches_bs1770_table_at_48k():
    shelf, hp = rc.k_weighting_coefficients(48000)
    assert shelf == pytest.approx((1.53512485958697, -2.69169618940638,
                                   1.19839281085285, -1.69065929318241,
                                   0.73248077421585), abs=1e-9)
    assert hp == pytest.approx((1.0, -2.0, 1.0, -1.99004745483398,
                                0.99007225036621), abs=1e-9)


@pytest.mark.parametrize("rate", [48000, 44100])
def test_ebu3341_case1_stereo_sine_reads_minus_23(rate):
    tone = _tone(4, rate, -23.0)
    x = [v for v in tone for _ in (0, 1)]
    assert rc.integrated_loudness(x, 2, rate) == pytest.approx(-23.0, abs=0.1)


def test_ebu3341_case3_relative_gate_ignores_quiet_parts():
    rate = 8000   # 1 kHz is still well below Nyquist; keeps the test fast
    mono = _tone(3, rate, -36.0) + _tone(10, rate, -23.0) + _tone(3, rate, -36.0)
    x = [v for v in mono for _ in (0, 1)]
    # The quiet tails are > 10 LU down, so the relative gate drops them.
    assert rc.integrated_loudness(x, 2, rate) == pytest.approx(-23.0, abs=0.15)


def test_meter_returns_none_for_silence_and_too_short_audio():
    assert rc.integrated_loudness([0.0] * 2 * 8000, 2, 8000) is None
    assert rc.integrated_loudness(_tone(0.3, 8000, -10.0), 1, 8000) is None


def test_loudest_window_finds_the_loud_section(tmp_path):
    rate = 8000
    path = str(tmp_path / "ref.wav")
    sig = _tone(4, rate, -40.0) + _tone(2, rate, -12.0) + _tone(4, rate, -40.0)
    _write(path, sig, sig, rate=rate)
    hdr = rc.read_wav_header(path)
    assert rc.loudest_window(path, hdr, 2.0) == pytest.approx(4.0, abs=0.1)
    # a window as long as the file starts at 0
    assert rc.loudest_window(path, hdr, 30.0) == 0.0


# --- comparison arithmetic --------------------------------------------------------

def _spectrum(levels):
    return [{"freq_hz": f, "level_db": lv} for f, lv in levels.items()]


def test_compare_metrics_matches_loudness_before_comparing():
    # Mix is 6 dB quieter overall but otherwise identical, except 2 dB
    # extra in 100 Hz and 3 dB less at 8 kHz.
    ref_levels = {31.5: -40.0, 100: -20.0, 400: -22.0, 1000: -24.0,
                  3150: -28.0, 8000: -30.0, 16000: -150.0}
    mix_levels = {f: lv - 6.0 for f, lv in ref_levels.items()}
    mix_levels[100] += 2.0
    mix_levels[8000] -= 3.0
    mix = {"lufs_i": -20.0, "sample_peak_db": -3.0, "crest_factor_db": 12.0,
           "spectrum_third_octave": _spectrum(mix_levels),
           "stereo": {"correlation": 0.6, "mid_rms_db": -20.0,
                      "side_rms_db": -30.0, "balance_db": 0.5}}
    ref = {"lufs_i": -14.0, "sample_peak_db": -0.5, "crest_factor_db": 9.0,
           "spectrum_third_octave": _spectrum(ref_levels),
           "stereo": {"correlation": 0.4, "mid_rms_db": -14.0,
                      "side_rms_db": -21.0, "balance_db": 0.0}}
    res = rc.compare_metrics(mix, ref)
    assert res["loudness_match"]["gain_applied_to_mix_db"] == 6.0
    bands = {b["freq_hz"]: b for b in res["bands"]}
    assert bands[100]["delta_db"] == 2.0
    assert bands[8000]["delta_db"] == -3.0
    assert bands[1000]["delta_db"] == 0.0
    assert bands[16000].get("low_energy") is True
    regions = {r["region"]: r for r in res["regions"]}
    assert regions["bass"]["delta_db"] == 2.0
    assert regions["air"]["delta_db"] == -3.0   # 16 kHz floor band excluded
    assert regions["mid"]["delta_db"] == 0.0
    assert [r["region"] for r in res["largest_deviations"]] == ["air", "bass"]
    d = res["dynamics"]
    assert d["crest_factor_delta_db"] == 3.0
    # PLR: mix -3 - (-20) = 17; ref -0.5 - (-14) = 13.5
    assert (d["mix_plr_db"], d["reference_plr_db"], d["plr_delta_db"]) == (
        17.0, 13.5, 3.5)
    s = res["stereo"]
    assert s["correlation_delta"] == 0.2
    assert s["width_db_delta"] == -3.0     # mix -10 vs ref -7: mix narrower
    assert s["balance_db_delta"] == 0.5


def test_compare_metrics_never_fabricates_missing_values():
    mix = {"lufs_i": -16.0, "crest_factor_db": None, "stereo": None,
           "spectrum_third_octave": _spectrum({100: -20.0, 200: float("nan")})}
    ref = {"lufs_i": -16.0, "crest_factor_db": 10.0, "sample_peak_db": -1.0,
           "spectrum_third_octave": _spectrum({100: -20.0, 200: -20.0})}
    res = rc.compare_metrics(mix, ref)
    assert [b["freq_hz"] for b in res["bands"]] == [100.0]
    assert res["dynamics"] == {} and res["stereo"] == {}
    assert res["largest_deviations"] == []


# --- compare_to_reference end to end ----------------------------------------

PREFLIGHT_OK = {"ok": True, "data": {
    "capture_allowed": True, "blockers": [], "warnings": [],
    "risk_gate": {"apply_change_with": "reload_bridge"}}}
CONTEXT = {"ok": True, "data": {"cursor": {"seconds": 30.0},
                                "time_selection": {"active": False}}}

MIX_SPECTRUM = {63: -30.0, 125: -18.0, 500: -22.0, 1000: -24.0, 4000: -30.0,
                10000: -36.0}
REF_SPECTRUM = {63: -24.0, 125: -14.0, 500: -20.0, 1000: -22.0, 4000: -26.0,
                10000: -30.0}


def _fake_analyzer():
    """Spectrum by file: the mix capture vs the reference slice."""
    def analyze(path):
        is_ref = os.path.basename(path).startswith("reference-")
        spec = REF_SPECTRUM if is_ref else MIX_SPECTRUM
        return types.SimpleNamespace(
            sample_peak_db=-1.0 if is_ref else -4.0, rms_db=-15.0,
            crest_factor_db=10.0 if is_ref else 13.0, silence_fraction=0.0,
            spectrum_third_octave=_spectrum(spec),
            stereo={"correlation": 0.5, "mid_rms_db": -15.0,
                    "side_rms_db": -24.0, "balance_db": 0.0})
    return analyze


class FakeBridge:
    """In-process sender: answers like the bridge and writes the capture."""

    def __init__(self, mix_db=-20.0, scope="master_output", silent=False):
        self.mix_db, self.scope, self.silent = mix_db, scope, silent
        self.commands = []

    def __call__(self, cmd_type, payload, timeout_ms=None):
        self.commands.append(cmd_type)
        if cmd_type == "get_capture_preflight":
            return PREFLIGHT_OK
        if cmd_type == "get_context":
            return CONTEXT
        if cmd_type == "capture_track_audio":
            rate = 8000
            n = int(payload["duration_seconds"] * rate)
            sig = [0.0] * n if self.silent else _tone(n / rate, rate, self.mix_db)
            _write(payload["output_file"], sig, sig, rate=rate)
            return {"ok": True, "data": {
                "track": {"index": -1, "name": "MASTER", "guid": "{M}"},
                "file_path": payload["output_file"],
                "start_seconds": payload["start_seconds"],
                "duration_seconds": payload["duration_seconds"],
                "render_loudness_lufs": None if self.silent else self.mix_db,
                "render_stats_raw": None,
                "capture_scope": self.scope,
                "isolation_verified": self.scope == "isolated_track",
                "note": "Master output capture."}}
        return {"ok": False, "error": {"code": "UNEXPECTED", "details": cmd_type}}


@pytest.fixture
def reference_wav(tmp_path):
    rate = 8000
    quiet, loud = _tone(4, rate, -40.0), _tone(3, rate, -14.0)
    sig = quiet + loud + quiet
    path = str(tmp_path / "reference.wav")
    _write(path, sig, sig, rate=rate)
    return path


def _leftovers():
    import tempfile
    found = []
    for sub in ("reaper-reference", "reaper-verify"):
        d = os.path.join(tempfile.gettempdir(), sub)
        if os.path.isdir(d):
            found += os.listdir(d)
    return set(found)


def test_compare_to_reference_happy_path(reference_wav):
    bridge = FakeBridge(mix_db=-20.0)
    before = _leftovers()
    res = rc.compare_to_reference(bridge, reference_wav, seconds=2,
                                  _analyzer_loader=_fake_analyzer)
    assert res["ok"] is True and res["status"] == "COMPARED"
    # read-only: preflight, cursor, one capture; nothing else was sent
    assert bridge.commands == ["get_capture_preflight", "get_context",
                               "capture_track_audio"]
    # the reference window landed on the loud section, not the quiet intro
    assert res["reference"]["window"]["source"] == "loudest_window"
    assert res["reference"]["window"]["start_seconds"] == pytest.approx(4.0, abs=0.1)
    assert res["reference"]["lufs_i"] == pytest.approx(-14.0, abs=0.2)
    assert res["mix"]["lufs_i"] == pytest.approx(-20.0, abs=0.2)
    gain = res["loudness_match"]["gain_applied_to_mix_db"]
    assert gain == pytest.approx(6.0, abs=0.3)
    regions = {r["region"]: r for r in res["regions"]}
    # Spectra are fixed by the fake analyzer, so each region delta is its
    # exact-6 dB value shifted by however far the measured match is from 6.
    assert regions["bass"]["delta_db"] == pytest.approx(
        _bass_delta() + (gain - 6.0), abs=0.06)
    assert regions["air"]["delta_db"] == pytest.approx(gain - 6.0, abs=0.06)
    assert res["dynamics"]["crest_factor_delta_db"] == 3.0
    # temp WAVs (mix capture and reference slice) are cleaned up
    assert _leftovers() <= before
    assert "COMPARED" not in rc.format_comparison(res)   # formatter runs
    assert "tonal balance" in rc.format_comparison(res)
    json.dumps(res, allow_nan=False)


def _bass_delta():
    """Expected bass-region delta at an exact 6 dB match, from the fixtures."""
    def psum(levels):
        return 10 * math.log10(sum(10 ** (lv / 10) for lv in levels))
    mix = psum([MIX_SPECTRUM[63] + 6, MIX_SPECTRUM[125] + 6])
    ref = psum([REF_SPECTRUM[63], REF_SPECTRUM[125]])
    return round(mix - ref, 1)


def test_explicit_reference_start_is_honored(reference_wav):
    res = rc.compare_to_reference(FakeBridge(), reference_wav, seconds=2,
                                  reference_start_seconds=5.0,
                                  _analyzer_loader=_fake_analyzer)
    assert res["reference"]["window"] == {"start_seconds": 5.0,
                                          "duration_seconds": 2.0,
                                          "source": "explicit_start"}


def test_refuses_without_post_mortem_before_rendering(reference_wav):
    bridge = FakeBridge()
    res = rc.compare_to_reference(bridge, reference_wav,
                                  _analyzer_loader=lambda: None)
    assert res["status"] == "REFUSED"
    assert res["error"]["code"] == "POSTMORTEM_MISSING"
    assert bridge.commands == []


@pytest.mark.parametrize("ref_start", [-1, 10.5, "soon"])
def test_refuses_bad_reference_start_before_rendering(reference_wav, ref_start):
    bridge = FakeBridge()
    res = rc.compare_to_reference(bridge, reference_wav,
                                  reference_start_seconds=ref_start,
                                  _analyzer_loader=_fake_analyzer)
    assert res["error"]["code"] == "BAD_REFERENCE"
    assert bridge.commands == []


def test_refuses_missing_reference_before_rendering(tmp_path):
    bridge = FakeBridge()
    res = rc.compare_to_reference(bridge, str(tmp_path / "nope.wav"),
                                  _analyzer_loader=_fake_analyzer)
    assert res["error"]["code"] == "BAD_REFERENCE"
    assert bridge.commands == []


def test_silent_mix_is_refused_and_cleaned_up(reference_wav):
    before = _leftovers()
    res = rc.compare_to_reference(FakeBridge(silent=True), reference_wav,
                                  seconds=2, _analyzer_loader=_fake_analyzer)
    assert res["status"] == "REFUSED"
    assert res["error"]["code"] == "SILENT_CAPTURE"
    assert _leftovers() <= before


def test_silent_reference_window_is_refused(tmp_path):
    rate = 8000
    path = str(tmp_path / "ref.wav")
    sig = [0.0] * (rate * 3) + _tone(3, rate, -14.0)
    _write(path, sig, sig, rate=rate)
    res = rc.compare_to_reference(FakeBridge(), path, seconds=2,
                                  reference_start_seconds=0.0,
                                  _analyzer_loader=_fake_analyzer)
    assert res["error"]["code"] == "SILENT_REFERENCE"


def test_isolated_bus_capture_carries_a_caveat(reference_wav):
    res = rc.compare_to_reference(FakeBridge(scope="isolated_track"),
                                  reference_wav, track="Mix Bus", seconds=2,
                                  _analyzer_loader=_fake_analyzer)
    assert res["status"] == "COMPARED"
    assert any("pre-master" in c for c in res["caveats"])


def test_capture_gate_refusal_passes_through(reference_wav):
    def gated(cmd_type, payload, timeout_ms=None):
        return {"ok": True, "data": {"capture_allowed": False, "blockers": [
            {"code": "capture_gated", "message": "off"}], "risk_gate": {}}}
    res = rc.compare_to_reference(gated, reference_wav,
                                  _analyzer_loader=_fake_analyzer)
    assert res["status"] == "REFUSED"
    assert res["error"]["code"] == "CAPTURE_BLOCKED"


# --- surfaces ------------------------------------------------------------------

def test_mcp_tool_is_registered_and_errors_are_tool_errors(tmp_path,
                                                           monkeypatch):
    resp = reaper_mcp.handle_message({"jsonrpc": "2.0", "id": 1,
                                      "method": "tools/list"})
    tools = {t["name"]: t for t in resp["result"]["tools"]}
    assert tools["compare_to_reference"]["inputSchema"]["required"] == [
        "reference_file"]
    monkeypatch.setattr(verifyloop, "_load_analyzer", lambda: None)
    resp = reaper_mcp.handle_message({
        "jsonrpc": "2.0", "id": 2, "method": "tools/call",
        "params": {"name": "compare_to_reference",
                   "arguments": {"reference_file": str(tmp_path / "r.wav")}}})
    assert resp["result"]["isError"] is True
    body = json.loads(resp["result"]["content"][0]["text"])
    assert body["error"]["code"] == "POSTMORTEM_MISSING"


def test_mcp_tool_through_the_file_queue(root, reference_wav, monkeypatch):
    monkeypatch.setattr(reaper_mcp, "BRIDGE_ROOT", root)
    monkeypatch.setattr(verifyloop, "_load_analyzer", _fake_analyzer)
    bridge = FakeBridge()
    fake_bridge_script(root, [lambda c: bridge(c["type"], c["payload"])] * 3)
    resp = reaper_mcp.handle_message({
        "jsonrpc": "2.0", "id": 3, "method": "tools/call",
        "params": {"name": "compare_to_reference",
                   "arguments": {"reference_file": reference_wav,
                                 "seconds": 2}}})
    assert resp["result"].get("isError") is not True
    body = json.loads(resp["result"]["content"][0]["text"])
    assert body["status"] == "COMPARED"
    assert body["mix"]["track"]["name"] == "MASTER"


def test_cli_refusal_exits_1(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(verifyloop, "_load_analyzer", lambda: None)
    rc_code = reaperd.main(["reference", str(tmp_path / "r.wav"), "--json"])
    assert rc_code == 1
    body = json.loads(capsys.readouterr().out)
    assert body["error"]["code"] == "POSTMORTEM_MISSING"
