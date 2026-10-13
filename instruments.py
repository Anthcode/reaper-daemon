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


def load_library(bridge_root):
    """{name: entry} with `source` set to catalog or user."""
    lib = {}
    for source in ("catalog", "user"):
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
    bits.append("chain " + entry["chain"] if entry.get("chain") else "NO CHAIN (load the sound once, then save)")
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
