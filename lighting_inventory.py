"""Lighting rig inventory. Reads and validates a declared DMX rig; sends nothing to any device."""
import argparse
import json
import re
from pathlib import Path

SCHEMA_VERSION = 1
UNIVERSE_SIZE = 512
CONTROLLERS = ('dmxis',)
FIXTURE_TYPES = ('par', 'wash', 'bar', 'spot', 'moving_head', 'blinder', 'strobe', 'hazer')
ROLES = ('front_wash', 'back_wash', 'side_wash', 'key', 'spot', 'audience', 'effect', 'ambient', 'atmosphere')
POSITIONS = ('stage_left', 'stage_right', 'center', 'upstage', 'downstage', 'floor', 'truss', 'drum_riser')
CHANNEL_KINDS = ('dimmer', 'red', 'green', 'blue', 'white', 'amber', 'uv', 'color_wheel', 'gobo',
                 'pan', 'pan_fine', 'tilt', 'tilt_fine', 'speed', 'zoom', 'focus', 'strobe', 'shutter',
                 'macro', 'mode', 'fog', 'fan', 'control')
# Capability -> channel kinds that must all be present.
CAPABILITIES = {'dimmer': ('dimmer',), 'rgb': ('red', 'green', 'blue'), 'white': ('white',),
                'amber': ('amber',), 'uv': ('uv',), 'color_wheel': ('color_wheel',), 'gobo': ('gobo',),
                'movement': ('pan', 'tilt'), 'zoom': ('zoom',), 'strobe': ('strobe',),
                'shutter': ('shutter',), 'atmosphere': ('fog',)}
FLASH_KINDS = ('strobe', 'shutter')
_ID_RE = re.compile(r'^[a-z][a-z0-9_]{0,47}$')
_TOP_KEYS = {'schema_version', 'rig', 'safety', 'fixtures'}
_RIG_KEYS = {'name', 'controller', 'universe', 'universe_size'}
_SAFETY_KEYS = {'strobe_enabled', 'max_intensity', 'atmosphere_enabled'}
_FIXTURE_KEYS = {'id', 'name', 'type', 'role', 'position', 'address', 'channels', 'enabled',
                 'max_intensity', 'strobe_allowed', 'notes'}
CAVEAT = ('Declared configuration only. Nothing was sent to any fixture, controller, or dmXis '
          'instance; addresses and channel layouts are not verified against hardware.')


def _keys(obj, allowed, where, errors):
    for key in obj:
        if not key.startswith('_') and key not in allowed:
            errors.append(f'{where}: unknown key {key!r}')


def _fraction(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and 0 <= value <= 1


def _int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def validate_rig(data):
    """Return a list of error strings. An empty list means the rig is valid."""
    errors = []
    if not isinstance(data, dict):
        return ['rig config must be a JSON object']
    _keys(data, _TOP_KEYS, 'config', errors)
    if data.get('schema_version') != SCHEMA_VERSION:
        errors.append(f'schema_version must be {SCHEMA_VERSION}')

    rig = data.get('rig')
    size = UNIVERSE_SIZE
    if not isinstance(rig, dict):
        errors.append('rig must be an object')
    else:
        _keys(rig, _RIG_KEYS, 'rig', errors)
        if not isinstance(rig.get('name'), str) or not rig['name'].strip():
            errors.append('rig.name must be a non-empty string')
        if rig.get('controller') not in CONTROLLERS:
            errors.append(f'rig.controller must be one of {", ".join(CONTROLLERS)}')
        if not _int(rig.get('universe', 1)) or rig.get('universe', 1) < 1:
            errors.append('rig.universe must be an integer >= 1')
        size = rig.get('universe_size', UNIVERSE_SIZE)
        if not _int(size) or not 1 <= size <= UNIVERSE_SIZE:
            errors.append(f'rig.universe_size must be 1..{UNIVERSE_SIZE}')
            size = UNIVERSE_SIZE

    safety = data.get('safety')
    strobe_on = atmosphere_on = False
    rig_max = 1.0
    if not isinstance(safety, dict):
        errors.append('safety must be an object')
    else:
        _keys(safety, _SAFETY_KEYS, 'safety', errors)
        for key in ('strobe_enabled', 'atmosphere_enabled'):
            if not isinstance(safety.get(key), bool):
                errors.append(f'safety.{key} must be true or false')
        strobe_on = safety.get('strobe_enabled') is True
        atmosphere_on = safety.get('atmosphere_enabled') is True
        if not _fraction(safety.get('max_intensity')):
            errors.append('safety.max_intensity must be a number 0..1')
        else:
            rig_max = safety['max_intensity']

    fixtures = data.get('fixtures')
    if not isinstance(fixtures, list) or not fixtures:
        errors.append('fixtures must be a non-empty list')
        return errors
    seen_ids, occupied = set(), {}
    for index, fx in enumerate(fixtures):
        where = f'fixtures[{index}]'
        if not isinstance(fx, dict):
            errors.append(f'{where} must be an object')
            continue
        fid = fx.get('id')
        if isinstance(fid, str) and _ID_RE.match(fid):
            where = f'fixture {fid!r}'
            if fid in seen_ids:
                errors.append(f'{where}: duplicate id')
            seen_ids.add(fid)
        else:
            errors.append(f'{where}: id must match {_ID_RE.pattern}')
        _keys(fx, _FIXTURE_KEYS, where, errors)
        if not isinstance(fx.get('name'), str) or not fx['name'].strip():
            errors.append(f'{where}: name must be a non-empty string')
        for key, allowed in (('type', FIXTURE_TYPES), ('role', ROLES), ('position', POSITIONS)):
            if fx.get(key) not in allowed:
                errors.append(f'{where}: {key} must be one of {", ".join(allowed)}')
        if 'notes' in fx and not isinstance(fx['notes'], str):
            errors.append(f'{where}: notes must be a string')
        enabled = fx.get('enabled', True)
        if not isinstance(enabled, bool):
            errors.append(f'{where}: enabled must be true or false')
        if 'max_intensity' in fx:
            if not _fraction(fx['max_intensity']):
                errors.append(f'{where}: max_intensity must be a number 0..1')
            elif fx['max_intensity'] > rig_max:
                errors.append(f'{where}: max_intensity {fx["max_intensity"]} exceeds safety.max_intensity {rig_max}')
        strobe_allowed = fx.get('strobe_allowed', False)
        if not isinstance(strobe_allowed, bool):
            errors.append(f'{where}: strobe_allowed must be true or false')
        elif strobe_allowed and not strobe_on:
            errors.append(f'{where}: strobe_allowed is true but safety.strobe_enabled is false')

        channels = fx.get('channels')
        if not isinstance(channels, list) or not channels:
            errors.append(f'{where}: channels must be a non-empty list')
            continue
        bad = [c for c in channels if c not in CHANNEL_KINDS]
        if bad:
            errors.append(f'{where}: unknown channel kinds {bad}')
        dupes = sorted({c for c in channels if isinstance(c, str) and channels.count(c) > 1})
        if dupes:
            errors.append(f'{where}: channel kinds listed twice {dupes}')
        if enabled is True and fx.get('type') == 'strobe' and not strobe_on:
            errors.append(f'{where}: strobe fixture must be disabled while safety.strobe_enabled is false')
        if enabled is True and 'fog' in channels and not atmosphere_on:
            errors.append(f'{where}: fog fixture must be disabled while safety.atmosphere_enabled is false')
        if strobe_allowed is True and not any(c in FLASH_KINDS for c in channels):
            errors.append(f'{where}: strobe_allowed is set but the fixture has no strobe or shutter channel')

        address = fx.get('address')
        if not _int(address) or not 1 <= address <= size:
            errors.append(f'{where}: address must be an integer 1..{size}')
            continue
        end = address + len(channels) - 1
        if end > size:
            errors.append(f'{where}: channels {address}-{end} run past universe size {size}')
            continue
        for ch in range(address, end + 1):
            if ch in occupied:
                errors.append(f'{where}: DMX channel {ch} already used by fixture {occupied[ch]!r}')
                break
            occupied[ch] = fid
    return errors


def _free_ranges(used, size):
    ranges, start = [], None
    for ch in range(1, size + 2):
        if ch <= size and ch not in used:
            start = ch if start is None else start
        elif start is not None:
            ranges.append([start, ch - 1])
            start = None
    return ranges


def rig_inventory(data):
    """Validate and summarise a rig. Raises ValueError listing every problem."""
    errors = validate_rig(data)
    if errors:
        raise ValueError('invalid lighting rig:\n  ' + '\n  '.join(errors))
    safety = data['safety']
    size = data['rig'].get('universe_size', UNIVERSE_SIZE)
    fixtures, used, roles = [], set(), {}
    for fx in data['fixtures']:
        channels = fx['channels']
        end = fx['address'] + len(channels) - 1
        used.update(range(fx['address'], end + 1))
        enabled = fx.get('enabled', True)
        caps = [cap for cap, need in CAPABILITIES.items() if all(k in channels for k in need)]
        flash = [k for k in FLASH_KINDS if k in channels]
        strobe_allowed = enabled and safety['strobe_enabled'] and fx.get('strobe_allowed', False)
        fixtures.append({
            'id': fx['id'], 'name': fx['name'], 'type': fx['type'], 'role': fx['role'],
            'position': fx['position'], 'enabled': enabled,
            'address': fx['address'], 'end_address': end, 'footprint': len(channels),
            'channel_map': {kind: fx['address'] + offset for offset, kind in enumerate(channels)},
            'capabilities': caps,
            'max_intensity': min(fx.get('max_intensity', safety['max_intensity']), safety['max_intensity']),
            'strobe_allowed': strobe_allowed,
            'locked_channels': [] if strobe_allowed else flash,
        })
        if enabled:
            roles.setdefault(fx['role'], []).append(fx['id'])
    return {'rig': data['rig']['name'], 'controller': data['rig']['controller'],
            'universe': data['rig'].get('universe', 1), 'universe_size': size,
            'safety': {key: safety[key] for key in sorted(_SAFETY_KEYS)},
            'fixtures': fixtures, 'roles': roles,
            'channels_used': len(used), 'free_ranges': _free_ranges(used, size),
            'caveat': CAVEAT}


def load_rig(path):
    """Read a rig JSON file and return its inventory. Raises ValueError on bad input."""
    try:
        data = json.loads(Path(path).read_text(encoding='utf-8'))
    except json.JSONDecodeError as exc:
        raise ValueError(f'{path}: not valid JSON ({exc})') from None
    return rig_inventory(data)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('config', nargs='?', default='config/lighting/lighting_rig.example.json')
    args = parser.parse_args()
    try:
        print(json.dumps(load_rig(args.config), indent=2))
    except (OSError, ValueError) as exc:
        raise SystemExit(str(exc))
