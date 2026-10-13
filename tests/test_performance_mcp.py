import reaper_mcp as mcp


def test_midi_forwarding_preserves_false_and_channels(monkeypatch):
    calls = []
    monkeypatch.setattr(mcp, '_send', lambda kind, data, **kw: calls.append((kind, data, kw)) or {'ok': True, 'data': {}})
    mcp._TOOL_BY_NAME['configure_midi_input']['handler']({'track': 'Lead', 'device': 62, 'channel': 1, 'arm': False, 'monitor': True})
    kind, data, _ = calls[0]
    assert kind == 'configure_midi_input'
    assert data['target_track_name'] == 'Lead'
    assert data['arm'] is False and data['channel'] == 1


def test_preset_selector_and_template_guids_forwarded(monkeypatch):
    calls = []
    monkeypatch.setattr(mcp, '_send', lambda kind, data, **kw: calls.append((kind, data, kw)) or {'ok': True, 'data': {}})
    mcp._TOOL_BY_NAME['set_fx_preset']['handler']({'target_track_guid': '{a}', 'fx_name_contains': 'Serum', 'name': 'Saved host sound'})
    assert calls[-1][1]['fx_name_contains'] == 'Serum'
    assert calls[-1][1]['name'] == 'Saved host sound'
    mcp._TOOL_BY_NAME['save_project_as']['handler']({'path': '/tmp/test.RTrackTemplate', 'template': True, 'track_guids': ['{a}', '{b}'], 'dry_run': True})
    assert calls[-1][1]['track_guids'] == ['{a}', '{b}']
    assert calls[-1][2]['dry_run'] is True


def test_audition_uses_shared_event_command(monkeypatch):
    calls = []
    monkeypatch.setattr(mcp, '_send', lambda kind, data, **kw: calls.append((kind, data, kw)) or {'ok': True, 'data': {}})
    mcp.tool_insert_performance_audition({'target_track_guid': '{a}', 'dry_run': True})
    assert calls[0][0] == 'insert_midi_events'
    assert len(calls[0][1]['events']) == 15
    assert calls[0][2]['dry_run']


def test_events_forward_beat_timing(monkeypatch):
    calls = []
    monkeypatch.setattr(mcp, '_send', lambda kind, data, **kw: calls.append((kind, data, kw)) or {'ok': True, 'data': {}})
    note = {'type': 'note', 'time': 0, 'duration': 1, 'pitch': 40, 'velocity': 100}
    mcp._TOOL_BY_NAME['insert_midi_events']['handler']({
        'track': 'Lead', 'time_unit': 'beats', 'start_bar': 9, 'length_beats': 8, 'events': [note]})
    kind, data, _ = calls[0]
    assert kind == 'insert_midi_events'
    assert data['time_unit'] == 'beats' and data['start_bar'] == 9 and data['length_beats'] == 8
    assert data.get('start_seconds') is None  # _send strips None before the bridge sees it
    assert mcp._TOOL_BY_NAME['insert_midi_events']['inputSchema']['required'] == ['events']
