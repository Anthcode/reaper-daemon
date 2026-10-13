"""Named instrument library: pick a sound by name, not by plugin index.

Most sounds live inside Kontakt or Serum, where REAPER's preset API sees
nothing. The fix is to load each sound by hand once, save that track's FX chain
(plugin state included, so the Kontakt library or Serum preset comes along) and
register it under a name with what an agent needs to play it: its role, the
note/articulation map the generators use, and its playable range.

Two layers, merged by name (the user layer wins):

  instruments/catalog.json   shipped; libraries the generators already know
                             (Argent, Nolly, the drum kits). No chains: a chain
                             is one machine's plugin state.
  instruments/user.json      this machine; written by `save`. Gitignored.

Chains are written to REAPER's FXChains folder as `instrument-<name>.RfxChain`,
so the same sound also shows up in REAPER's own FX chain browser.

A multi-output instrument (a Kontakt drum kit feeding Kick, Snare, room tracks
and buses) only sounds through that routing, which a chain can't hold. Those
are saved as a RIG: the instrument track plus every track its sends and folders
reach, as a REAPER track template (`TrackTemplates/instrument-<name>`). Loading
a rig adds all of its tracks; MIDI goes to its `play_track`.
"""

import json
import os
import re

ROLES = ("guitar", "bass", "drums", "synth", "keys", "orchestral", "vocal", "other")
MAP_KINDS = ("guitar", "bass", "drums")
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}$")
CHAIN_PREFIX = "instrument-"


class InstrumentError(ValueError):
    pass


def _dir(bridge_root):
    return os.path.join(bridge_root, "instruments")


def _read(path):
    if not os.path.isfile(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict) or not isinstance(data.get("instruments", {}), dict):
        raise InstrumentError(f"{path} is not an instrument library")
    return data.get("instruments", {})


def load_library(bridge_root, include_user=True):
    """{name: entry} with `source` set to catalog or user."""
    lib = {}
    for source in ("catalog", "user") if include_user else ("catalog",):
        for name, entry in _read(os.path.join(_dir(bridge_root), source + ".json")).items():
            lib[name] = dict(entry, name=name, source=source)
    return lib


def chain_name_for(name):
    return CHAIN_PREFIX + name


def validate_entry(name, entry, known_maps=None):
    """Raise InstrumentError for anything a later load or generator would trip on.

    `known_maps` is {kind: set(names)}; maps are only checked when it is given,
    so the library still loads where the skills are not importable.
    """
    if not NAME_RE.match(name or ""):
        raise InstrumentError(f"instrument name {name!r}: use lowercase letters, digits, - or _")
    role = entry.get("role")
    if role not in ROLES:
        raise InstrumentError(f"{name}: role must be one of {', '.join(ROLES)}")
    maps = entry.get("maps") or {}
    if not isinstance(maps, dict):
        raise InstrumentError(f"{name}: maps must be an object")
    for kind, map_name in maps.items():
        if kind not in MAP_KINDS:
            raise InstrumentError(f"{name}: map kind {kind!r} must be one of {', '.join(MAP_KINDS)}")
        if known_maps is not None and map_name not in known_maps.get(kind, ()):
            raise InstrumentError(f"{name}: no {kind} map named {map_name!r}")
    rng = entry.get("range")
    if rng is not None:
        if (not isinstance(rng, list) or len(rng) != 2 or
                not all(isinstance(v, int) and 0 <= v <= 127 for v in rng) or rng[0] > rng[1]):
            raise InstrumentError(f"{name}: range must be [low, high] MIDI notes 0-127")
    arts = entry.get("articulations") or {}
    if not isinstance(arts, dict) or not all(
            isinstance(v, int) and 0 <= v <= 127 for v in arts.values()):
        raise InstrumentError(f"{name}: articulations must map names to MIDI notes 0-127")
    tags = entry.get("tags") or []
    if not isinstance(tags, list) or not all(isinstance(t, str) for t in tags):
        raise InstrumentError(f"{name}: tags must be a list of words")
    return True


def known_maps(bridge_root):
    """{kind: set(names)} from the guitar and drum skills, or None if unavailable."""
    import sys
    out = {}
    try:
        gpath = os.path.join(bridge_root, "skills", "guitar-apparatus")
        if gpath not in sys.path:
            sys.path.insert(0, gpath)
        from guitargen.maps import GUITAR_MAPS, BASS_MAPS
        out["guitar"], out["bass"] = set(GUITAR_MAPS), set(BASS_MAPS)
        dpath = os.path.join(bridge_root, "skills", "drum-apparatus")
        if dpath not in sys.path:
            sys.path.insert(0, dpath)
        from drumgen.catalog import load_maps
        out["drums"] = set(load_maps())
    except Exception:
        return None
    return out


def register(bridge_root, name, entry, maps_known=None):
    """Add or replace one entry in the user layer. Returns the stored entry."""
    entry = {k: v for k, v in entry.items() if v not in (None, [], {}) and k not in ("name", "source")}
    validate_entry(name, entry, maps_known)
    path = os.path.join(_dir(bridge_root), "user.json")
    lib = _read(path)
    lib[name] = entry
    os.makedirs(_dir(bridge_root), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"instruments": dict(sorted(lib.items()))}, f, indent=1)
        f.write("\n")
    os.replace(tmp, path)
    return entry


def remove(bridge_root, name):
    """Drop a user entry. Catalog entries are not removable; the chain file stays."""
    path = os.path.join(_dir(bridge_root), "user.json")
    lib = _read(path)
    if name not in lib:
        raise InstrumentError(f"{name!r} is not in your library (catalog entries can't be removed)")
    del lib[name]
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"instruments": lib}, f, indent=1)
        f.write("\n")


def find(lib, role=None, tag=None):
    rows = sorted(lib.values(), key=lambda e: (e["role"], e["name"]))
    if role:
        rows = [e for e in rows if e["role"] == role]
    if tag:
        rows = [e for e in rows if tag in (e.get("tags") or [])]
    return rows


def describe(entry):
    bits = [entry["name"], entry["role"]]
    if entry.get("library"):
        bits.append(entry["library"])
    if entry.get("maps"):
        bits.append("maps " + ", ".join(f"{k}={v}" for k, v in entry["maps"].items()))
    if entry.get("range"):
        bits.append(f"notes {entry['range'][0]}-{entry['range'][1]}")
    if entry.get("rig"):
        bits.append(f"rig of {len(entry.get('rig_tracks') or [])} tracks, play {entry.get('play_track')}")
    elif entry.get("chain"):
        bits.append("chain " + entry["chain"])
    else:
        bits.append("NO CHAIN (load the sound once, then save)")
    if entry.get("tags"):
        bits.append("tags " + " ".join(entry["tags"]))
    return " | ".join(bits)


# --- live operations (sender = reaperd.send_type-compatible callable) ---------

def save_from_track(bridge_root, sender, name, track, role, *, library=None, plugin=None,
                    maps=None, range_=None, articulations=None, tags=None, notes=None,
                    overwrite=False):
    """Capture a configured track's FX chain and register it under `name`."""
    lib = load_library(bridge_root)
    existing = lib.get(name, {})
    if existing.get("source") == "user" and existing.get("chain") and not overwrite:
        raise InstrumentError(f"{name!r} already has a saved chain; pass overwrite to replace it")
    entry = {k: v for k, v in existing.items() if k not in ("name", "source")}
    entry.update({k: v for k, v in {
        "role": role, "library": library, "plugin": plugin, "maps": maps, "range": range_,
        "articulations": articulations, "tags": tags, "notes": notes}.items() if v is not None})
    mk = known_maps(bridge_root)
    validate_entry(name, entry, mk)
    chain = chain_name_for(name)
    res = sender("save_fx_chain", {"target_track_name": track, "chain_name": chain,
                                   "overwrite": bool(overwrite or existing.get("chain"))})
    if not res.get("ok"):
        raise InstrumentError(f"saving the chain failed: {_err(res)}")
    data = res.get("data") or {}
    entry["chain"] = chain
    fx = data.get("fx")
    if fx:
        entry["fx"] = fx
    if not entry.get("plugin") and fx:
        entry["plugin"] = fx[0]
    return register(bridge_root, name, entry, mk), data


def load_onto_track(bridge_root, sender, name, track, allow_existing_fx=False):
    """Splice a saved instrument chain onto a track and verify it landed."""
    lib = load_library(bridge_root)
    entry = lib.get(name)
    if not entry:
        raise InstrumentError(f"no instrument named {name!r} (try: instrument list)")
    if not entry.get("chain"):
        raise InstrumentError(f"{name!r} has no saved chain yet: load the sound by hand once, "
                              f"then run instrument save {name} --track <that track>")
    if not allow_existing_fx:
        ctx = sender("get_context", {})
        tracks = ((ctx.get("data") or {}).get("tracks") or []) if ctx.get("ok") else None
        if tracks is None:
            raise InstrumentError(f"could not read the project: {_err(ctx)}")
        hit = [t for t in tracks if t.get("name") == track]
        if len(hit) != 1:
            raise InstrumentError(f"{len(hit)} tracks are named {track!r}; need exactly one")
        if hit[0].get("fx"):
            raise InstrumentError(f"track {track!r} already has FX; an instrument goes on an "
                                  f"empty track (pass allow_existing_fx to append anyway)")
    res = sender("add_fx_chain", {"target_track_name": track, "chain_name": entry["chain"]})
    if not res.get("ok"):
        raise InstrumentError(f"loading the chain failed: {_err(res)}")
    return entry, res.get("data") or {}


def _err(res):
    e = res.get("error") or {}
    return e.get("code") or res.get("message") or "unknown error"


# --- rigs: an instrument plus the tracks it feeds -------------------------------

def rig_span(tracks, sends, play_guid):
    """GUIDs of the contiguous track block that makes up the rig, in order.

    `tracks` is get_context's list (index, guid, name, folder_depth); `sends`
    maps guid -> [destination guids]. Start at the instrument track, follow
    sends and folder children until nothing new is reached, then take the
    contiguous block from the first to the last reached track. The block must
    be self-contained: its folders open and close inside it, so the template
    can't drag in, or cut off, part of someone else's folder.
    """
    ordered = [t for t in tracks if t.get("index", 0) > 0]
    pos = {t["guid"]: i for i, t in enumerate(ordered)}
    if play_guid not in pos:
        raise InstrumentError("the instrument track isn't in the project")

    def children(i):
        depth, out = 0, []
        if ordered[i].get("folder_depth", 0) != 1:
            return out
        for j in range(i, len(ordered)):
            if j > i:
                out.append(ordered[j]["guid"])
            depth += int(ordered[j].get("folder_depth", 0))
            if depth <= 0 and j > i:
                break
        return out

    reached, todo = {play_guid}, [play_guid]
    while todo:
        g = todo.pop()
        for nxt in list(sends.get(g, [])) + children(pos[g]):
            if nxt in pos and nxt not in reached:
                reached.add(nxt)
                todo.append(nxt)
    lo, hi = min(pos[g] for g in reached), max(pos[g] for g in reached)
    block = ordered[lo:hi + 1]
    depth = 0
    for t in block:
        depth += int(t.get("folder_depth", 0))
        if depth < 0:
            break
    if depth != 0:
        raise InstrumentError(
            "the tracks this instrument feeds don't form a self-contained block "
            f"({block[0]['name']} to {block[-1]['name']}); its folders reach outside it")
    stray = [t["name"] for t in block if t["guid"] not in reached]
    if stray:
        raise InstrumentError("these tracks sit inside the rig's block but aren't fed by the "
                              f"instrument: {', '.join(stray)}. Move them out, or save a chain")
    return [t["guid"] for t in block]


def template_path(name, resource_dir=None):
    if resource_dir is None:
        import reaperd
        resource_dir = reaperd.find_resource_dir()
    if not resource_dir:
        raise InstrumentError("REAPER's resource folder wasn't found (set REAPER_RESOURCE_PATH)")
    return os.path.join(resource_dir, "TrackTemplates", CHAIN_PREFIX + name + ".RTrackTemplate")


def save_rig(bridge_root, sender, name, play_track, role, *, library=None, maps=None,
             tags=None, notes=None, overwrite=False, resource_dir=None):
    """Save an instrument track and everything it feeds as a named rig."""
    lib = load_library(bridge_root)
    existing = lib.get(name, {})
    entry = {k: v for k, v in existing.items() if k not in ("name", "source")}
    entry.update({k: v for k, v in {"role": role, "library": library, "maps": maps,
                                    "tags": tags, "notes": notes}.items() if v is not None})
    mk = known_maps(bridge_root)
    validate_entry(name, entry, mk)
    ctx = sender("get_context", {})
    if not ctx.get("ok"):
        raise InstrumentError(f"could not read the project: {_err(ctx)}")
    tracks = (ctx.get("data") or {}).get("tracks") or []
    hit = [t for t in tracks if t.get("name") == play_track and t.get("index", 0) > 0]
    if len(hit) != 1:
        raise InstrumentError(f"{len(hit)} tracks are named {play_track!r}; need exactly one")
    sends = {}
    for t in tracks:
        if t.get("index", 0) <= 0:
            continue
        r = sender("get_track_routing", {"target_track_guid": t["guid"]})
        if not r.get("ok"):
            raise InstrumentError(f"could not read routing of {t['name']!r}: {_err(r)}")
        sends[t["guid"]] = [s.get("target_track_guid") for s in
                            (r.get("data") or {}).get("sends") or []]
    guids = rig_span(tracks, sends, hit[0]["guid"])
    names = [t["name"] for g in guids for t in tracks if t["guid"] == g]
    path = template_path(name, resource_dir)
    if os.path.exists(path):
        if not overwrite:
            raise InstrumentError(f"{name!r} already has a saved rig at {path}; "
                                  "pass overwrite to replace it")
        os.remove(path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    res = sender("save_project_as", {"path": path, "template": True, "track_guids": guids})
    if not res.get("ok"):
        raise InstrumentError(f"saving the rig failed: {_err(res)}")
    entry.update({"rig": path, "play_track": play_track, "rig_tracks": names})
    return register(bridge_root, name, entry, mk), names


def load_rig(bridge_root, sender, name):
    """Add a saved rig's tracks to the active project. Returns (entry, added, play_guid)."""
    entry = load_library(bridge_root).get(name)
    if not entry:
        raise InstrumentError(f"no instrument named {name!r} (try: instrument list)")
    if not entry.get("rig"):
        raise InstrumentError(f"{name!r} is a chain, not a rig; load it onto a track")
    if not os.path.isfile(entry["rig"]):
        raise InstrumentError(f"the rig file is missing: {entry['rig']}")
    res = sender("insert_track_template", {"path": entry["rig"]})
    if not res.get("ok"):
        raise InstrumentError(f"loading the rig failed: {_err(res)}")
    added = (res.get("data") or {}).get("added") or []
    play = [t for t in added if t.get("name") == entry.get("play_track")]
    if len(play) != 1:
        raise InstrumentError(f"the rig loaded but its play track {entry.get('play_track')!r} "
                              f"wasn't found exactly once among {[t.get('name') for t in added]}")
    return entry, added, play[0]["guid"]
