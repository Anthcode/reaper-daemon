import json
from pathlib import Path

import pytest

from lighting_inventory import load_rig, rig_inventory, validate_rig

EXAMPLE = Path(__file__).resolve().parent.parent / 'config/lighting/lighting_rig.example.json'


@pytest.fixture
def rig():
    return json.loads(EXAMPLE.read_text(encoding='utf-8'))


def fixture(rig, fid):
    return next(fx for fx in rig['fixtures'] if fx['id'] == fid)


def fixture_result(rig, fid):
    return next(fx for fx in rig_inventory(rig)['fixtures'] if fx['id'] == fid)


def test_example_is_valid_and_strobe_is_off(rig):
    assert validate_rig(rig) == []
    result = load_rig(EXAMPLE)
    assert result['safety']['strobe_enabled'] is False
    assert not any(fx['strobe_allowed'] for fx in result['fixtures'])
    strobe = next(fx for fx in result['fixtures'] if fx['type'] == 'strobe')
    assert strobe['enabled'] is False
    assert 'effect' not in result['roles']
    assert 'Nothing was sent' in result['caveat']


def test_inventory_maps_channels_and_free_ranges(rig):
    result = rig_inventory(rig)
    par = next(fx for fx in result['fixtures'] if fx['id'] == 'par_front_right')
    assert (par['address'], par['end_address'], par['footprint']) == (7, 12, 6)
    assert par['channel_map']['strobe'] == 11
    assert par['capabilities'] == ['dimmer', 'rgb', 'strobe']
    assert par['locked_channels'] == ['strobe']
    head = next(fx for fx in result['fixtures'] if fx['id'] == 'spot_center')
    assert 'movement' in head['capabilities']
    assert result['roles']['front_wash'] == ['par_front_left', 'par_front_right']
    assert result['channels_used'] == 6 + 6 + 5 + 10 + 2 + 2
    assert result['free_ranges'][0] == [18, 19]
    assert result['free_ranges'][-1] == [52, 512]


def test_fixture_cannot_exceed_rig_intensity(rig):
    fixture(rig, 'bar_back')['max_intensity'] = 0.95
    assert any('exceeds safety.max_intensity' in e for e in validate_rig(rig))


def test_overlap_names_both_fixtures(rig):
    fixture(rig, 'par_front_right')['address'] = 5
    errors = validate_rig(rig)
    assert any("channel 5 already used by fixture 'par_front_left'" in e for e in errors)
    with pytest.raises(ValueError, match='invalid lighting rig'):
        rig_inventory(rig)


def test_footprint_past_universe_end(rig):
    fixture(rig, 'spot_center')['address'] = 505
    assert any('run past universe size 512' in e for e in validate_rig(rig))


@pytest.mark.parametrize('address', [0, 513, '1', True, None])
def test_address_must_be_integer_in_range(rig, address):
    fixture(rig, 'bar_back')['address'] = address
    assert any('address must be an integer 1..512' in e for e in validate_rig(rig))


def test_strobe_needs_global_switch(rig):
    strobe = fixture(rig, 'strobe_main')
    strobe['enabled'] = True
    errors = validate_rig(rig)
    assert any('strobe fixture must be disabled' in e for e in errors)
    strobe['strobe_allowed'] = True
    assert any('safety.strobe_enabled is false' in e for e in validate_rig(rig))
    rig['safety']['strobe_enabled'] = True
    assert validate_rig(rig) == []
    assert fixture_result(rig, 'strobe_main')['strobe_allowed'] is True
    assert fixture_result(rig, 'strobe_main')['locked_channels'] == []
    # The global switch alone does not unlock fixtures that never opted in.
    assert fixture_result(rig, 'par_front_left')['strobe_allowed'] is False


def test_strobe_allowed_requires_flash_channel(rig):
    rig['safety']['strobe_enabled'] = True
    fixture(rig, 'bar_back')['strobe_allowed'] = True
    assert any('no strobe or shutter channel' in e for e in validate_rig(rig))


def test_hazer_needs_atmosphere_switch(rig):
    fixture(rig, 'hazer')['enabled'] = True
    assert any('fog fixture must be disabled' in e for e in validate_rig(rig))
    rig['safety']['atmosphere_enabled'] = True
    assert validate_rig(rig) == []


def test_unknown_keys_and_values_are_rejected(rig):
    rig['rig']['usb_serial'] = 'x'
    fixture(rig, 'bar_back')['colour'] = 'red'
    fixture(rig, 'bar_back')['channels'].append('laser')
    fixture(rig, 'bar_back')['role'] = 'headliner'
    errors = validate_rig(rig)
    assert "rig: unknown key 'usb_serial'" in errors
    assert "fixture 'bar_back': unknown key 'colour'" in errors
    assert any("unknown channel kinds ['laser']" in e for e in errors)
    assert any('role must be one of' in e for e in errors)


def test_underscore_keys_are_comments(rig):
    rig['_note'] = 'free text'
    fixture(rig, 'bar_back')['_why'] = 'free text'
    assert validate_rig(rig) == []


def test_ids_and_channels_must_be_unique(rig):
    fixture(rig, 'bar_back')['id'] = 'par_front_left'
    fixture(rig, 'hazer')['channels'] = ['fog', 'fog']
    errors = validate_rig(rig)
    assert "fixture 'par_front_left': duplicate id" in errors
    assert any("listed twice ['fog']" in e for e in errors)


def test_structure_errors_do_not_crash():
    assert validate_rig([]) == ['rig config must be a JSON object']
    errors = validate_rig({'schema_version': 2, 'fixtures': []})
    assert 'schema_version must be 1' in errors
    assert 'rig must be an object' in errors
    assert 'safety must be an object' in errors
    assert 'fixtures must be a non-empty list' in errors


def test_smaller_universe_size(rig):
    rig['rig']['universe_size'] = 50
    assert any('run past universe size 50' in e or 'address must be an integer 1..50' in e
               for e in validate_rig(rig))


def test_load_rig_reports_bad_json(tmp_path):
    path = tmp_path / 'rig.json'
    path.write_text('{"schema_version": 1,', encoding='utf-8')
    with pytest.raises(ValueError, match='not valid JSON'):
        load_rig(path)
