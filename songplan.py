#!/usr/bin/env python3
"""Song planner: a song sheet, approved, then drafted into a fresh project.

A sheet is JSON (see docs/song-planner.md for the full format):

  {"title": "...", "tempo": 140, "meter": "4/4", "key": "E phrygian",
   "source": {"kind": "brief|riff|reference", "detail": "..."},
   "parts": {"drums": "monarch-drums", "guitar_l": "argent-l",
             "guitar_r": "argent-r", "bass": "nolly-bass",
             "layers": {"pad": "dark-pad"}},
   "sections": [
     {"name": "intro", "bars": 8, "energy": 2, "feel": "mf",
      "description": "...", "chords": "E5 | F5 | ...",
      "drums": {"grid": 16, "lanes": {"kick": "X...", "snare": "....X..."}},
      "riff": ["Xxx.Xx.xXx.xXxx.", "..."],
      "layers": {"pad": [{"time": 0, "duration": 16, "pitch": 40, "velocity": 90}]},
      "wildcard": false}]}

The flow is fixed: check -> show -> (person approves) -> approve -> draft.
`draft` refuses a sheet whose content changed since it was approved, and writes
only into a NEW project tab, so an open song is never touched.

Limits inherited from the generators: 4/4 only and one tempo per song. The
drum grid and riff grid are 16 steps per bar for riffs; drum grids may vary.
"""

import argparse
import datetime
import hashlib
import json
import os
import re
import sys

import instruments as inst

FEELS = ("ppp", "pp", "p", "mp", "mf", "f", "ff", "fff")
REST_BAR = "................"
SLUG_RE = re.compile(r"[^a-z0-9]+")
SECTION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _-]{0,31}$")


class SheetError(ValueError):
    def __init__(self, problems):
        self.problems = problems
        super().__init__("; ".join(problems))


def slug(text):
    return SLUG_RE.sub("-", str(text).lower()).strip("-") or "song"


def load(path):
    with open(path, "r", encoding="utf-8") as f:
        sheet = json.load(f)
    if not isinstance(sheet, dict):
        raise SheetError(["a sheet is a JSON object"])
    return sheet


def save(path, sheet):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(sheet, f, indent=1, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp, path)


def fingerprint(sheet):
    """Hash of everything but the approval stamp: any edit invalidates approval."""
    body = {k: v for k, v in sheet.items() if k != "approval"}
    return hashlib.sha256(json.dumps(body, sort_keys=True).encode("utf-8")).hexdigest()


# --- structure ----------------------------------------------------------------

def section_starts(sheet):
    """1-based start bar of each section, in order."""
    bar, out = 1, []
    for sec in sheet["sections"]:
        out.append(bar)
        bar += sec["bars"]
    return out


def total_bars(sheet):
    return sum(sec["bars"] for sec in sheet["sections"])


def drum_dsl(sheet):
    """The whole song's drums as one DSL stream, one [section] per section."""
    lines = [f"@tempo {sheet['tempo']}"]
    for i, sec in enumerate(sheet["sections"]):
        drums = sec.get("drums")
        name = slug(sec["name"]) + f"-{i + 1}"
        lines.append(f"[{name}] bars={sec['bars']} feel={sec.get('feel', 'mf')}")
        if not drums:
            lines += ["grid 16", f"kick | {REST_BAR} |"]
            continue
        lines.append(f"grid {drums.get('grid', 16)}")
        for lane, cells in drums["lanes"].items():
            lines.append(f"{lane} | {cells} |")
    return "\n".join(lines) + "\n"


def riff_bars(sheet):
    """Every bar of the guitar/bass riff, sections back to back. A section's
    riff tiles to fill its bars; a section without one rests."""
    out = []
    for sec in sheet["sections"]:
        riff = sec.get("riff") or [REST_BAR]
        out += [riff[i % len(riff)] for i in range(sec["bars"])]
    return out


def layer_events(sheet, layer):
    """insert_midi_events beats-mode events for one layer, song-relative."""
    events = []
    for sec, start in zip(sheet["sections"], section_starts(sheet)):
        offset = (start - 1) * 4
        for ev in (sec.get("layers") or {}).get(layer, []):
            e = {"type": "note", "pitch": ev["pitch"], "velocity": ev["velocity"],
                 "time": offset + ev["time"], "duration": ev["duration"]}
            if "channel" in ev:
                e["channel"] = ev["channel"]
            events.append(e)
    return events


def has_riff(sheet):
    return any(sec.get("riff") for sec in sheet["sections"])


# --- validation ---------------------------------------------------------------

def _skills(bridge_root):
    for sub in ("drum-apparatus", "guitar-apparatus"):
        p = os.path.join(bridge_root, "skills", sub)
        if p not in sys.path:
            sys.path.insert(0, p)


def check(sheet, bridge_root, library=None):
    """Every problem with the sheet, as plain sentences. Empty list = good."""
    problems = []
    p = problems.append
    tempo = sheet.get("tempo")
    if not isinstance(tempo, int) or not 40 <= tempo <= 320:
        p("tempo must be a whole number of BPM from 40 to 320")
    if sheet.get("meter", "4/4") != "4/4":
        p("only 4/4 is supported for now (the drum and riff engines are 4/4)")
    if not isinstance(sheet.get("title"), str) or not sheet["title"].strip():
        p("the sheet needs a title")
    sections = sheet.get("sections")
    if not isinstance(sections, list) or not sections:
        p("the sheet needs at least one section")
        return problems
    library = inst.load_library(bridge_root) if library is None else library
    parts = sheet.get("parts") or {}
    layers = parts.get("layers") or {}
    for role in ("drums", "guitar_l", "guitar_r", "bass"):
        name = parts.get(role)
        if name is not None and name not in library:
            p(f"parts.{role}: no instrument named {name!r} in the library")
    for layer, name in layers.items():
        if not re.match(r"^[a-z0-9][a-z0-9_-]{0,30}$", layer):
            p(f"layer name {layer!r}: use lowercase letters, digits, - or _")
        if name not in library:
            p(f"parts.layers.{layer}: no instrument named {name!r} in the library")
    guitar_map = guitar_map_for(sheet, library)
    if has_riff(sheet) and not parts.get("guitar_l") and not parts.get("guitar_r") \
            and not parts.get("bass"):
        p("sections have riffs but parts names no guitar or bass")

    _skills(bridge_root)
    try:
        from drumgen.groovekit import parse_dsl, DSLError, FEEL_CENTER
        from guitargen.riffs import parse_bars
        from guitargen.maps import GUITAR_MAPS
    except Exception as e:  # pragma: no cover - broken install
        p(f"could not load the drum/guitar engines: {e}")
        return problems
    if has_riff(sheet) and guitar_map not in GUITAR_MAPS:
        p(f"guitar map {guitar_map!r} is not one of {', '.join(sorted(GUITAR_MAPS))}")

    names = set()
    for i, sec in enumerate(sections):
        where = f"section {i + 1}"
        if not isinstance(sec, dict):
            p(f"{where} is not an object")
            continue
        name = sec.get("name")
        if not isinstance(name, str) or not SECTION_RE.match(name):
            p(f"{where}: name must be 1-32 letters, digits, spaces, - or _")
        else:
            where = f"section {i + 1} ({name})"
            names.add(name.lower())
        bars = sec.get("bars")
        if not isinstance(bars, int) or not 1 <= bars <= 128:
            p(f"{where}: bars must be a whole number from 1 to 128")
            continue
        energy = sec.get("energy")
        if energy is not None and (not isinstance(energy, int) or not 1 <= energy <= 5):
            p(f"{where}: energy is 1 (lowest) to 5")
        if sec.get("feel", "mf") not in FEEL_CENTER:
            p(f"{where}: feel must be one of {', '.join(FEELS)}")
        drums = sec.get("drums")
        if drums is not None:
            if not isinstance(drums, dict) or not isinstance(drums.get("lanes"), dict) \
                    or not drums["lanes"]:
                p(f"{where}: drums needs lanes, e.g. {{\"lanes\": {{\"kick\": \"X...\"}}}}")
            else:
                one = {"title": "x", "tempo": tempo if isinstance(tempo, int) else 120,
                       "sections": [dict(sec, name="s")]}
                try:
                    parse_dsl(drum_dsl(one))
                except DSLError as e:
                    p(f"{where}: drums: {e}")
        riff = sec.get("riff")
        if riff is not None:
            if not isinstance(riff, list) or not riff or \
                    not all(isinstance(b, str) for b in riff):
                p(f"{where}: riff is a list of bar strings, 16 cells each")
            else:
                try:
                    parse_bars(riff)
                except (ValueError, KeyError) as e:
                    p(f"{where}: riff: {e}")
        for layer, evs in (sec.get("layers") or {}).items():
            if layer not in layers:
                p(f"{where}: layer {layer!r} is not named in parts.layers")
                continue
            if not isinstance(evs, list):
                p(f"{where}: layer {layer!r} is a list of notes")
                continue
            for j, ev in enumerate(evs):
                tag = f"{where}: layer {layer} note {j + 1}"
                if not isinstance(ev, dict):
                    p(f"{tag} is not an object")
                    continue
                ok = (isinstance(ev.get("pitch"), int) and 0 <= ev["pitch"] <= 127 and
                      isinstance(ev.get("velocity"), int) and 1 <= ev["velocity"] <= 127 and
                      isinstance(ev.get("time"), (int, float)) and ev["time"] >= 0 and
                      isinstance(ev.get("duration"), (int, float)) and ev["duration"] > 0)
                if not ok:
                    p(f"{tag}: needs pitch 0-127, velocity 1-127, time >= 0, duration > 0 "
                      "(time and duration in beats from the section start)")
                elif ev["time"] + ev["duration"] > bars * 4 + 1e-9:
                    p(f"{tag}: runs past the end of the section ({bars * 4} beats)")
                else:
                    rng = library.get(layers[layer], {}).get("range")
                    if rng and not rng[0] <= ev["pitch"] <= rng[1]:
                        p(f"{tag}: pitch {ev['pitch']} is outside {layers[layer]}'s "
                          f"range {rng[0]}-{rng[1]}")
    if len(names) != len(sections) and not any("name must" in x for x in problems):
        p("section names must be unique (number repeats: verse-1, verse-2)")
    return problems


def guitar_map_for(sheet, library):
    """The sheet's guitar_map, else the guitar instrument's map, else argent_e."""
    if sheet.get("guitar_map"):
        return sheet["guitar_map"]
    parts = sheet.get("parts") or {}
    for role in ("guitar_l", "guitar_r"):
        entry = library.get(parts.get(role) or "", {})
        if (entry.get("maps") or {}).get("guitar"):
            return entry["maps"]["guitar"]
    return "argent_e"


def drum_map_for(sheet, library):
    entry = library.get((sheet.get("parts") or {}).get("drums") or "", {})
    return (entry.get("maps") or {}).get("drums")


# --- the readable sheet -----------------------------------------------------

def render_text(sheet):
    """The song sheet as a person reads it before approving."""
    out = [f"# {sheet.get('title', '(untitled)')}", ""]
    meta = [f"{sheet.get('tempo')} BPM", sheet.get("meter", "4/4")]
    if sheet.get("key"):
        meta.append(sheet["key"])
    meta.append(f"{total_bars(sheet)} bars")
    secs = total_bars(sheet) * 4 * 60.0 / sheet["tempo"] if sheet.get("tempo") else 0
    meta.append(f"about {int(secs // 60)}:{int(secs % 60):02d}")
    out.append(" | ".join(meta))
    src = sheet.get("source") or {}
    if src:
        out.append(f"Started from: {src.get('kind', '?')}"
                   + (f" ({src['detail']})" if src.get("detail") else ""))
    parts = sheet.get("parts") or {}
    named = [f"{k.replace('_', ' ')}: {v}" for k, v in parts.items() if k != "layers" and v]
    named += [f"{k} layer: {v}" for k, v in (parts.get("layers") or {}).items()]
    if named:
        out.append("Parts: " + ", ".join(named))
    out += ["", "| # | Section | Bars | Starts | Energy | Drums | Guitar/bass | Notes |",
            "|---|---|---|---|---|---|---|---|"]
    for i, (sec, start) in enumerate(zip(sheet["sections"], section_starts(sheet))):
        drums = "rest" if not sec.get("drums") else (
            f"{sec.get('feel', 'mf')}, " + ", ".join(sec["drums"]["lanes"]))
        riff = "rest" if not sec.get("riff") else f"{len(sec['riff'])}-bar riff"
        notes = sec.get("description", "")
        if sec.get("chords"):
            notes = (notes + " " if notes else "") + f"[{sec['chords']}]"
        if sec.get("layers"):
            notes += " +" + ",".join(sec["layers"])
        if sec.get("wildcard"):
            notes = "WILDCARD. " + notes
        energy = "#" * sec["energy"] if sec.get("energy") else ""
        out.append(f"| {i + 1} | {sec['name']} | {sec['bars']} | bar {start} | {energy} | "
                   f"{drums} | {riff} | {notes.strip()} |")
    appr = sheet.get("approval") or {}
    out.append("")
    if appr.get("hash") == fingerprint(sheet):
        out.append(f"Approved {appr.get('at', '')}.")
    elif appr:
        out.append("Changed since it was approved: needs approval again.")
    else:
        out.append("Not approved yet.")
    return "\n".join(out)


def approve(sheet):
    sheet["approval"] = {"hash": fingerprint(sheet),
                         "at": datetime.datetime.now().astimezone().isoformat(timespec="seconds")}
    return sheet


def is_approved(sheet):
    return (sheet.get("approval") or {}).get("hash") == fingerprint(sheet)


# --- drafting ---------------------------------------------------------------

def draft(sheet, bridge_root, sender, *, seed=101, out_dir=None, render=None):
    """Write an approved sheet into a NEW project tab. Returns a report dict.

    `sender(type, payload, timeout_ms=...) -> reply` reaches the bridge;
    `render` is reaperd (injectable for tests). Every leg is attempted and
    reported on its own, like `band`, so a half-drafted song says which part
    is missing instead of hiding it.
    """
    if render is None:
        import reaperd as render
    library = inst.load_library(bridge_root)
    problems = check(sheet, bridge_root, library)
    if problems:
        raise SheetError(problems)
    if not is_approved(sheet):
        raise SheetError(["the sheet is not approved (or changed after approval); "
                          "show it, get a yes, then approve it"])
    report = {"title": sheet["title"], "steps": [], "problems": []}

    def step(label, res):
        ok = bool(res.get("ok"))
        report["steps"].append({"step": label, "ok": ok,
                                **({} if ok else {"error": (res.get("error") or {}).get("code")
                                                  or res.get("message")})})
        return ok

    def need(label, res):
        if not step(label, res):
            raise SheetError([f"{label} failed: {report['steps'][-1].get('error')}; "
                              "stopped before writing any parts"])

    def send(kind, payload, timeout_ms=30000):
        return sender(kind, payload, timeout_ms=timeout_ms)

    need("open a new project tab", send("new_project_tab", {}))
    need(f"set tempo {sheet['tempo']}", send("set_tempo", {"bpm": sheet["tempo"]}))

    parts = sheet.get("parts") or {}
    tracks = []  # (track name, instrument name)
    for role, label in (("drums", "drums"), ("guitar_l", "guitar-l"),
                        ("guitar_r", "guitar-r"), ("bass", "bass")):
        if parts.get(role):
            tracks.append((label, parts[role]))
    for layer, name in (parts.get("layers") or {}).items():
        tracks.append((layer, name))
    for track, instrument in tracks:
        need(f"add track {track}", send("add_track", {"name": track}))
        entry = library[instrument]
        if entry.get("chain"):
            step(f"load {instrument} on {track}",
                 send("add_fx_chain", {"target_track_name": track, "chain_name": entry["chain"]}))
        else:
            report["problems"].append(
                f"{track}: {instrument} has no saved sound yet; load it by hand on this track "
                f"(then: reaperd.py instrument save {instrument} --track {track})")

    for sec, start in zip(sheet["sections"], section_starts(sheet)):
        step(f"region {sec['name']}", send("add_region", {
            "start": {"type": "bar", "bar": start}, "length_bars": sec["bars"],
            "name": sec["name"]}))

    def rsend(kind, payload, timeout_ms):
        return sender(kind, payload, timeout_ms=timeout_ms)

    at = {"type": "bar", "bar": 1}
    if parts.get("drums") and any(sec.get("drums") for sec in sheet["sections"]):
        rc, msg = render.render_groove(bridge_root, dsl_text=drum_dsl(sheet), track="drums",
                                       kit_map=drum_map_for(sheet, library), seed=seed,
                                       position=at, tempo=sheet["tempo"], sender=rsend,
                                       tag="draft/drums")
        report["steps"].append({"step": "drums", "ok": rc == 0, "detail": msg})
    if has_riff(sheet):
        out_dir = out_dir or os.path.join(bridge_root, "rendered-midi")
        os.makedirs(out_dir, exist_ok=True)
        bars_file = os.path.join(out_dir, f"songplan-{slug(sheet['title'])}-riff.txt")
        with open(bars_file, "w", encoding="utf-8") as f:
            f.write("\n".join(riff_bars(sheet)) + "\n")
        gmap = guitar_map_for(sheet, library)
        for role, track, part, s in (("guitar_l", "guitar-l", "guitar", seed + 101),
                                     ("guitar_r", "guitar-r", "guitar", seed + 202),
                                     ("bass", "bass", "bass", seed + 303)):
            if not parts.get(role):
                continue
            rc, msg = render.render_shred(bridge_root, part=part, track=track, seed=s,
                                          map_name=gmap, tempo=sheet["tempo"],
                                          bars_file=bars_file,
                                          low_string=sheet.get("low_string"), position=at,
                                          sender=rsend, tag=f"draft/{track}")
            report["steps"].append({"step": track, "ok": rc == 0, "detail": msg})
    for layer in (parts.get("layers") or {}):
        events = layer_events(sheet, layer)
        if not events:
            continue
        step(f"layer {layer}", send("insert_midi_events", {
            "target_track_name": layer, "time_unit": "beats", "start_bar": 1,
            "length_beats": total_bars(sheet) * 4, "events": events}))
    report["ok"] = all(s["ok"] for s in report["steps"]) and not report["problems"]
    report["note"] = ("Drafted into a new, unsaved project tab. Listen, then save it "
                      "yourself if you want to keep it.")
    return report


# --- CLI ------------------------------------------------------------------------

def main(argv=None):
    import reaperd
    ap = argparse.ArgumentParser(description="Song planner: check, show, approve, draft.")
    ap.add_argument("--bridge-root", default=reaperd.BRIDGE_ROOT)
    sub = ap.add_subparsers(dest="action", required=True)
    for name, helptext in (("check", "list every problem with a sheet"),
                           ("show", "print the readable song sheet"),
                           ("approve", "stamp the sheet as approved (after a person said yes)"),
                           ("draft", "write an approved sheet into a new project tab")):
        s = sub.add_parser(name, help=helptext)
        s.add_argument("sheet")
        if name == "draft":
            s.add_argument("--seed", type=int, default=101)
    args = ap.parse_args(argv)
    try:
        sheet = load(args.sheet)
        if args.action == "show":
            print(render_text(sheet))
            return 0
        problems = check(sheet, args.bridge_root)
        if problems:
            print("Problems:\n" + "\n".join("  - " + x for x in problems))
            return 1
        if args.action == "check":
            print("OK: the sheet is ready to show for approval" if not is_approved(sheet)
                  else "OK: approved and ready to draft")
            return 0
        if args.action == "approve":
            save(args.sheet, approve(sheet))
            print(f"Approved {args.sheet}")
            return 0
        if not reaperd.status_ok(args.bridge_root, quiet=True):
            print("error: REAPER bridge is not connected (reaperd.py status)", file=sys.stderr)
            return 1

        def sender(kind, payload, timeout_ms=30000):
            return reaperd.send_type(kind, payload, bridge_root=args.bridge_root,
                                     timeout_ms=timeout_ms, resolve=False, repair=False)
        report = draft(sheet, args.bridge_root, sender, seed=args.seed)
        for s in report["steps"]:
            print(("ok   " if s["ok"] else "FAIL ") + s["step"]
                  + (f": {s.get('error')}" if s.get("error") else ""))
        for prob in report["problems"]:
            print("todo " + prob)
        print(report["note"])
        return 0 if report["ok"] else 1
    except SheetError as e:
        print("Problems:\n" + "\n".join("  - " + x for x in e.problems))
        return 1
    except (OSError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
