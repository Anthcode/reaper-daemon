"""Composition boundaries and similarity failures from the cymbal revisions."""
import json
from pathlib import Path
import subprocess
import sys

import pytest

import drum_workshop as workshop


def brief(**changes):
    return {'request_id': 'song-a-v1', 'project_id': 'song-a',
            'description': 'Heavy groove with space for riffs',
            'tempo': 182, 'bars': 4, 'map': 'RS Monarch',
            'exclude_families': ['ride', 'choke'], **changes}


def dsl(kick='X..x..x.X..x..x.', snare='........X.......', cymbal='crash_l', bars=4, tempo=182):
    return (f'@tempo {tempo}\n@map RS Monarch\n[Phrase] bars={bars} feel=f\n'
            f'grid 16\nkick | {kick} |\nsnare | {snare} |\n'
            f'{cymbal} | X.......x....... |\n')


def populate(path, names=None):
    names = names or ['fresh', 'contrast', 'wildcard']
    for i, name in enumerate(names):
        (path / name / 'candidate.dsl').write_text(dsl(cymbal=['crash_l', 'crash_r', 'china'][i]))
        (path / name / 'intent.json').write_text(json.dumps({
            'premise': 'A test phrase', 'development': 'Repeat for comparison',
            'exploration': 'Deliberately unchanged backbone to exercise the warning'}))


def test_reference_blind_requests_and_context_scopes(tmp_path):
    reference = tmp_path / 'reference.dsl'
    reference.write_text(dsl())
    out = tmp_path / 'workshop'
    preferences = [
        {'scope': 'request', 'target_id': 'other', 'text': 'No hats'},
        {'scope': 'request', 'target_id': 'song-a-v1', 'text': 'Open space'},
        {'scope': 'project', 'target_id': 'other', 'text': 'No china'},
        {'scope': 'global', 'confirmed': True, 'text': 'Phrases should develop'},
        {'scope': 'example', 'text': 'I liked the china in reference one'},
    ]
    workshop.prepare(brief(preferences=preferences, references=[{
        'name': 'Reference secret', 'path': str(reference), 'approved': True}]), out)
    for role in ['fresh', 'wildcard']:
        request = workshop.read_json(out / role / 'request.json')
        assert not request['reference_access']
        assert 'references' not in request and 'preferences' not in request
        assert 'Reference secret' not in json.dumps(request)
    assert workshop.read_json(out / 'reference/request.json')['references'][0]['dsl'] == dsl()
    manifest = workshop.read_json(out / 'workshop.json')
    assert len(manifest['preferences']) == 3
    assert manifest['brief']['exclude_families'] == ['ride', 'choke']
    reference.write_text('changed later')
    assert manifest['references'][0]['dsl'] == dsl()


def test_reference_or_preference_cannot_silently_become_global(tmp_path):
    with pytest.raises(ValueError, match='confirmed'):
        workshop.prepare(brief(preferences=[{'scope': 'global', 'text': 'No ride'}]), tmp_path/'a')
    with pytest.raises(ValueError, match='approved'):
        workshop.prepare(brief(references=[{'name': 'unapproved', 'path': 'a'}]), tmp_path/'b')


def test_repeated_backbone_is_not_hidden_by_kit_cymbals_velocity_or_tempo():
    a = workshop.parse(dsl())
    b = workshop.parse(dsl(cymbal='china', tempo=120).replace('feel=f', 'feel=ff'))
    result = workshop.compare(a, b)
    assert result['backbone_similarity'] == 1
    assert result['orchestration_similarity'] < 1
    assert result['closest_phrase']['similarity'] == 1
    assert workshop.compare(a, workshop.parse(dsl(kick='X...X...X...X...', snare='....X.......X...')))['backbone_similarity'] < .8


def test_moved_reference_phrase_is_found():
    a = workshop.parse(dsl(bars=2))
    b = workshop.parse(dsl(kick='X...X...X...X...', snare='....X.......X...', bars=2) +
                       dsl(bars=2).split('[Phrase]', 1)[1].join(['[Copied]', '']))
    result = workshop.compare(a, b)
    assert result['closest_phrase'] == {'similarity': 1, 'a_bar': 1, 'b_bar': 3, 'bars': 2}


def test_evaluate_keeps_wildcard_even_when_it_matches_and_preserves_dsl(tmp_path):
    workspace = tmp_path / 'workshop'
    workshop.prepare(brief(), workspace)
    populate(workspace)
    source = (workspace / 'wildcard/candidate.dsl').read_bytes()
    result = workshop.evaluate(workspace)
    assert result['ok'], result
    assert result['audition_candidates'] == ['fresh', 'contrast', 'wildcard']
    assert result['candidates'][-1]['protected_wildcard']
    assert all(r['backbone_similarity'] == 1 for r in result['comparisons'])
    assert len(result['review_flags']) == 3
    assert all(c['audition_status'] == 'not_auditioned' for c in result['candidates'])
    assert 'winner' not in result
    assert (workspace / 'wildcard/candidate.dsl').read_bytes() == source
    again = workshop.evaluate(workspace)
    assert result['report'] != again['report']
    assert Path(result['candidates'][0]['midi']).read_bytes() == Path(again['candidates'][0]['midi']).read_bytes()
    # Read the SMF delta times to check that trailing silence reaches the requested length.
    data = Path(result['candidates'][0]['midi']).read_bytes()
    pos, tick = 22, 0
    while pos < len(data):
        delta = 0
        while True:
            byte = data[pos]; pos += 1
            delta = (delta << 7) | (byte & 127)
            if not byte & 128:
                break
        tick += delta
        if data[pos] == 255:
            kind, length = data[pos+1:pos+3]
            pos += 3 + length
            if kind == 47:
                break
        else:
            pos += 3
    assert tick == int.from_bytes(data[12:14], 'big') * 16


@pytest.mark.parametrize('cymbal', ['ride', 'ride_bell', 'china_choke'])
def test_wildcard_cannot_evade_explicit_exclusions(tmp_path, cymbal):
    workspace = tmp_path/'workshop'
    workshop.prepare(brief(), workspace)
    populate(workspace)
    (workspace/'wildcard/candidate.dsl').write_text(dsl(cymbal=cymbal))
    result = workshop.evaluate(workspace)
    assert not result['ok']
    assert result['audition_candidates'] == ['fresh', 'contrast']
    assert 'Excluded families' in result['candidates'][-1]['error']


def test_missing_candidate_and_wrong_duration_are_reported(tmp_path):
    workspace = tmp_path/'workshop'
    workshop.prepare(brief(), workspace)
    populate(workspace)
    (workspace/'fresh/candidate.dsl').unlink()
    (workspace/'wildcard/candidate.dsl').write_text(dsl(bars=5))
    result = workshop.evaluate(workspace)
    assert not result['ok']
    assert result['audition_candidates'] == ['contrast']


def test_feedback_is_scoped_and_bound_to_evaluated_version(tmp_path):
    workspace = tmp_path/'workshop'
    workshop.prepare(brief(), workspace)
    populate(workspace)
    result = workshop.evaluate(workspace)
    (workspace/'fresh/candidate.dsl').write_text('an unrelated later edit')
    record = {'candidate_id': 'fresh', 'report': result['report'],
              'usefulness': 'revise', 'novelty': 'familiar', 'reason': 'Backbone repeats an old part'}
    saved = workshop.feedback(workspace, record)['feedback']
    assert saved['scope'] == 'request'
    assert saved['dsl_sha256'] == result['candidates'][0]['dsl_sha256']
    assert saved['preference_promotion'].startswith('none')
    with pytest.raises(ValueError, match='confirmed'):
        workshop.feedback(workspace, {**record, 'scope': 'global'})
    with pytest.raises(ValueError, match='inside'):
        workshop.feedback(workspace, {**record, 'report': '../foreign.json'})


@pytest.mark.parametrize('changes', [{'bars': 0}, {'bars': 65}, {'tempo': float('nan')},
                                  {'exclude_families': ['unknowable']}, {'unused_knob': True}])
def test_bad_briefs_leave_no_workspace(tmp_path, changes):
    with pytest.raises(ValueError):
        workshop.prepare(brief(**changes), tmp_path/'out')
    assert not (tmp_path/'out').exists()


def test_existing_workspace_is_not_overwritten(tmp_path):
    out = tmp_path/'out'
    workshop.prepare(brief(), out)
    with pytest.raises(FileExistsError):
        workshop.prepare(brief(), out)


def test_cli_and_mcp_use_same_offline_workshop(tmp_path, monkeypatch):
    import reaper_mcp
    monkeypatch.setattr(reaper_mcp, '_send', lambda *a, **k: pytest.fail('No bridge calls allowed'))
    path = tmp_path/'brief.json'
    path.write_text(json.dumps(brief()))
    response = reaper_mcp.tool_drum_workshop({'action': 'prepare', 'path': str(path),
                                            'output': str(tmp_path/'mcp')})
    assert not response.get('isError')
    assert json.loads(response['content'][0]['text'])['ok']
    command = subprocess.run([sys.executable, str(workshop.ROOT/'reaperd.py'), 'drum-workshop',
                              'prepare', str(path), '--output', str(tmp_path/'cli')],
                             capture_output=True, text=True)
    assert command.returncode == 0, command.stderr
    assert workshop.read_json(tmp_path/'cli/workshop.json') == workshop.read_json(tmp_path/'mcp/workshop.json')
    assert 'drum_workshop' in reaper_mcp._TOOL_BY_NAME


def test_invalid_end_tick_refused():
    with pytest.raises(ValueError, match='end_tick'):
        workshop.smf.write_smf([{'tick': 0, 'pitch': 36, 'vel': 100, 'dur': 48}], end_tick=10)


def test_malformed_grid_and_intent_do_not_crash_evaluation(tmp_path):
    workspace = tmp_path/'workshop'
    workshop.prepare(brief(), workspace)
    populate(workspace)
    (workspace/'fresh/candidate.dsl').write_text(dsl().replace('grid 16', 'grid'))
    (workspace/'wildcard/intent.json').write_text('[]')
    report = workshop.evaluate(workspace)
    assert report['audition_candidates'] == ['contrast']


def test_aliases_cannot_create_duplicate_physical_hits(tmp_path):
    workspace = tmp_path/'workshop'
    workshop.prepare(brief(), workspace)
    populate(workspace)
    (workspace/'fresh/candidate.dsl').write_text(dsl(cymbal='crash') + 'crash_r | X.......x....... |\n')
    report = workshop.evaluate(workspace)
    assert 'Duplicate' in report['candidates'][0]['error']


def test_technical_report_is_written_next_to_each_candidate(tmp_path):
    workspace = tmp_path/'workshop'
    workshop.prepare(brief(), workspace)
    populate(workspace)
    (workspace/'wildcard/candidate.dsl').write_text(dsl(cymbal='ride'))
    report = workshop.evaluate(workspace)
    fresh = workshop.read_json(report['candidates'][0]['evaluation'])
    assert fresh['candidate_id'] == 'fresh' and fresh['version'] == 1
    assert {f['level'] for f in fresh['findings']} <= {'warning', 'info'}
    assert fresh['metrics']['length_bars'] == 4
    wildcard = workshop.read_json(report['candidates'][2]['evaluation'])
    assert wildcard['summary'][0].startswith('ERROR: Excluded families')
    assert not report['candidates'][2]['valid']


def test_unmapped_roles_fail_the_candidate_instead_of_vanishing(tmp_path, monkeypatch):
    real = workshop.groovekit.load_maps
    sparse = {'Sparse Kit': {'KICK_R': 36, 'SNARE': 38, 'CRASH_R': 49}}
    monkeypatch.setattr(workshop.groovekit, 'load_maps', lambda: {**real(), **sparse})
    workspace = tmp_path/'workshop'
    workshop.prepare(brief(map='Sparse Kit', exclude_families=[]), workspace)
    populate(workspace)
    for name in ('fresh', 'contrast', 'wildcard'):
        path = workspace/name/'candidate.dsl'
        path.write_text(path.read_text().replace('RS Monarch', 'Sparse Kit'))
    report = workshop.evaluate(workspace)
    # crash_l falls back to CRASH_R; the wildcard's china has nowhere to go.
    assert report['audition_candidates'] == ['fresh', 'contrast']
    assert 'cannot play' in report['candidates'][2]['error']


def test_plan_and_check_plan_share_one_path_on_cli_and_mcp(tmp_path, monkeypatch):
    import reaper_mcp
    monkeypatch.setattr(reaper_mcp, '_send', lambda *a, **k: pytest.fail('No bridge calls allowed'))
    path = tmp_path/'arrangement.json'
    path.write_text(json.dumps({'tempo': 182, 'kit_map': 'RS Monarch', 'exclude_families': ['choke'],
                                'sections': [{'id': 'verse', 'bars': 8, 'role': 'verse'},
                                             {'id': 'breakdown', 'bars': 4, 'role': 'breakdown',
                                              'exclude_families': ['ride']}]}))
    command = subprocess.run([sys.executable, str(workshop.ROOT/'reaperd.py'), 'drum-workshop',
                              'plan', str(path), '--output', str(tmp_path/'cli.json')],
                             capture_output=True, text=True)
    assert command.returncode == 0, command.stderr
    summary = json.loads(command.stdout)
    assert summary['total_bars'] == 12 and summary['caveats'] == []
    response = reaper_mcp.tool_drum_workshop({'action': 'plan', 'path': str(path),
                                            'output': str(tmp_path/'mcp.json')})
    assert not response.get('isError')
    assert workshop.read_json(tmp_path/'cli.json') == workshop.read_json(tmp_path/'mcp.json')
    # The plan file is the plan itself: edit it and check it again.
    edited = workshop.read_json(tmp_path/'cli.json')
    edited['sections'][1]['require_families'] = ['ride']
    (tmp_path/'edited.json').write_text(json.dumps(edited))
    checked = reaper_mcp.tool_drum_workshop({'action': 'check-plan', 'path': str(tmp_path/'edited.json')})
    assert checked.get('isError') and 'both requires and excludes' in checked['content'][0]['text']
    ok = workshop.run('check-plan', str(tmp_path/'mcp.json'))
    assert ok['sections'][1]['effective_exclusions'] == ['choke', 'ride']
    with pytest.raises(FileExistsError):
        workshop.run('plan', str(path), str(tmp_path/'cli.json'))


# ---- PR 4: workshops built on an arrangement plan ----

def write_plan(tmp_path, kick_grid=None, grid_source=None, name='plan.json'):
    """A two-section plan; optionally a verse kick grid from the planner."""
    arrangement = {'tempo': 182, 'kit_map': 'RS Monarch', 'exclude_families': ['stack'],
                   'sections': [{'id': 'verse', 'bars': 2, 'role': 'verse',
                                 'kick_strategy': 'riff_selective', 'energy': 0.5},
                                {'id': 'chorus', 'bars': 2, 'role': 'chorus',
                                 'exclude_families': ['china']}]}
    observations = None
    if grid_source == 'user_explicit':
        arrangement['sections'][0]['kick_grid'] = kick_grid
    elif grid_source == 'audio_inferred':
        observations = {'kick_grid': kick_grid + '.' * 32}
    from drumgen.arrangement import build_plan
    plan = build_plan(arrangement, observations)['plan']
    (tmp_path/name).write_text(json.dumps(plan))
    return name


def plan_dsl(verse_cymbal='crash_l', chorus_cymbal='crash_r', names=('verse', 'chorus')):
    lanes = 'grid 16\nkick | X..x..x.X..x..x. |\nsnare | ........X....... |\n'
    return (f'@tempo 182\n@map RS Monarch\n'
            f'[{names[0]}] bars=2 feel=f\n{lanes}{verse_cymbal} | X.......x....... |\n'
            f'[{names[1]}] bars=2 feel=f\n{lanes}{chorus_cymbal} | X.......x....... |\n')


def populate_plan(workspace, roles, sources=None):
    for i, role in enumerate(roles):
        source = (sources or {}).get(role, plan_dsl(chorus_cymbal=['crash_r', 'crash_l', 'splash'][i]))
        (workspace/role/'candidate.dsl').write_text(source)
        (workspace/role/'intent.json').write_text(json.dumps(
            {'premise': 'p', 'development': 'd', 'exploration': 'e'}))


def plan_brief(tmp_path, **changes):
    return brief(exclude_families=['ride'], plan=str(tmp_path/'plan.json'), **changes)


def test_plan_workshop_is_version_2_and_splits_the_plan_by_role(tmp_path):
    write_plan(tmp_path)
    ws = tmp_path/'ws'
    result = workshop.prepare(plan_brief(tmp_path), ws)
    assert result['candidates'] == ['fresh', 'contrast', 'wildcard']
    manifest = workshop.read_json(ws/'workshop.json')
    assert manifest['version'] == 2 and manifest['generator_version'] == 2
    assert manifest['brief']['exclude_families'] == ['ride', 'stack']
    assert set(manifest['inputs_sha256']) == {'brief', 'plan_file'}
    fresh = workshop.read_json(ws/'fresh/request.json')['plan']
    wildcard = workshop.read_json(ws/'wildcard/request.json')['plan']
    assert [s['id'] for s in fresh['sections']] == ['verse', 'chorus']
    assert fresh['sections'][0]['kick_strategy'] == 'riff_selective'
    assert 'kick_strategy' not in wildcard['sections'][0] and 'energy' not in wildcard['sections'][0]
    assert wildcard['sections'][1]['effective_exclusions'] == ['china', 'stack']
    assert fresh['plan_direction'].startswith('tight') and 'breathing' in \
        workshop.read_json(ws/'contrast/request.json')['plan']['plan_direction']


def test_audio_kick_sketch_goes_only_to_the_reference_request(tmp_path):
    sketch = 'x..x....x..x....' * 2
    write_plan(tmp_path, sketch, 'audio_inferred')
    ws = tmp_path/'ws'
    assert workshop.prepare(plan_brief(tmp_path), ws)['candidates'] == ['fresh', 'reference', 'wildcard']
    requests = {r: workshop.read_json(ws/r/'request.json') for r in ('fresh', 'reference', 'wildcard')}
    assert requests['reference']['plan']['sections'][0]['kick_sketch']['grid'] == sketch
    assert requests['reference']['reference_access'] and 'sketch' in requests['reference']['direction']
    for role in ('fresh', 'wildcard'):
        assert sketch not in json.dumps(requests[role]) and not requests[role]['reference_access']


def test_user_kick_grid_binds_every_request(tmp_path):
    grid = 'x.......x.......' * 2
    write_plan(tmp_path, grid, 'user_explicit')
    ws = tmp_path/'ws'
    assert workshop.prepare(plan_brief(tmp_path), ws)['candidates'] == ['fresh', 'contrast', 'wildcard']
    for role in ('fresh', 'contrast', 'wildcard'):
        assert workshop.read_json(ws/role/'request.json')['plan']['sections'][0]['kick_grid'] == grid


def test_plan_must_match_the_brief(tmp_path):
    write_plan(tmp_path)
    with pytest.raises(ValueError, match='must match the brief'):
        workshop.prepare(plan_brief(tmp_path, bars=8), tmp_path/'ws')
    assert not (tmp_path/'ws').exists()


def test_evaluation_holds_candidates_to_the_plan(tmp_path):
    write_plan(tmp_path, 'x..x..x.x..x..x.' * 2, 'audio_inferred')
    ws = tmp_path/'ws'
    workshop.prepare(plan_brief(tmp_path), ws)
    populate_plan(ws, ['fresh', 'reference', 'wildcard'], {
        'reference': plan_dsl(names=('verse', 'bridge')),
        'wildcard': plan_dsl(chorus_cymbal='china')})
    report = workshop.evaluate(ws)
    assert report['audition_candidates'] == ['fresh']
    assert 'plan' in report['candidates'][1]['error']
    assert 'chorus' in report['candidates'][2]['error']
    fresh = workshop.read_json(report['candidates'][0]['evaluation'])
    (riff,) = [f for f in fresh['findings'] if f['check'] == 'kick_riff']
    assert riff['data']['section'] == 'verse' and riff['data']['coverage'] == 1
    assert 'saved .rpp' in riff['data']['caveat']


def test_changed_plan_snapshot_is_refused(tmp_path):
    write_plan(tmp_path)
    ws = tmp_path/'ws'
    workshop.prepare(plan_brief(tmp_path), ws)
    manifest = workshop.read_json(ws/'workshop.json')
    manifest['plan']['sections'][1]['exclude_families'] = []
    (ws/'workshop.json').write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match='Plan snapshot changed'):
        workshop.evaluate(ws)
    del manifest['plan']
    (ws/'workshop.json').write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match='version 2'):
        workshop.evaluate(ws)


# ---- PR 5: performer, revisions, pick and verify ----

def audition_ready(tmp_path, **changes):
    write_plan(tmp_path)
    ws = tmp_path/'ws'
    workshop.prepare(plan_brief(tmp_path, **changes), ws)
    populate_plan(ws, ['fresh', 'contrast', 'wildcard'])
    return ws, workshop.evaluate(ws)


def say(ws, report, usefulness='use', **extra):
    return workshop.feedback(ws, {'candidate_id': 'fresh', 'report': report['report'],
                                  'usefulness': usefulness, 'novelty': 'new',
                                  'reason': 'Heard it through the kit', **extra})


def test_performer_is_recorded_and_used_for_rendering(tmp_path):
    ws, report = audition_ready(tmp_path, performer='raw_black_metal')
    assert workshop.read_json(ws/'workshop.json')['performer']['params']['humanize'] == 30
    assert 'performer' not in workshop.read_json(ws/'fresh/request.json')
    evaluation = workshop.read_json(report['candidates'][0]['evaluation'])
    assert evaluation['metrics']['performer'] == 'raw_black_metal'
    with pytest.raises(ValueError, match='performer'):
        workshop.prepare(plan_brief(tmp_path, performer='polka'), tmp_path/'other')


def test_section_feedback_names_a_real_section(tmp_path):
    ws, report = audition_ready(tmp_path)
    assert say(ws, report, 'revise', section_id='chorus')['feedback']['section_id'] == 'chorus'
    with pytest.raises(ValueError, match='section_id'):
        say(ws, report, 'revise', section_id='bridge')


def test_revision_reports_which_sections_changed(tmp_path):
    ws, first = audition_ready(tmp_path)
    say(ws, first, 'revise', section_id='chorus')
    (ws/'fresh/candidate.dsl').write_text(plan_dsl(chorus_cymbal='splash'))
    second = workshop.evaluate(ws, first['report'])
    assert second['parent_report'] == first['report']
    change = second['candidates'][0]['revision']
    assert change['changed_sections'] == ['chorus'] and change['unchanged_sections'] == ['verse']
    assert 'outside_request' not in change
    assert second['candidates'][1]['revision']['changed_sections'] == []
    (ws/'fresh/candidate.dsl').write_text(plan_dsl(verse_cymbal='splash', chorus_cymbal='splash'))
    third = workshop.evaluate(ws, first['report'])
    assert third['candidates'][0]['revision']['outside_request'] == ['verse']
    assert any('did not ask for' in w for w in third['candidates'][0]['warnings'])


def test_pick_needs_use_feedback_and_the_untouched_midi(tmp_path):
    ws, report = audition_ready(tmp_path)
    choice = {'candidate_id': 'fresh', 'report': report['report']}
    with pytest.raises(ValueError, match="'use' feedback"):
        workshop.pick(ws, choice)
    say(ws, report, 'revise')
    with pytest.raises(ValueError, match="'use' feedback"):
        workshop.pick(ws, choice)
    say(ws, report, 'use')
    picked = workshop.pick(ws, choice)
    payload = picked['insert_midi_file']
    assert payload['midi_path'] == report['candidates'][0]['midi']
    assert payload['length'] == {'type': 'bars', 'bars': 4}
    assert payload['replace_existing_in_range'] is False
    assert picked['expected_notes'] == report['candidates'][0]['notes']
    Path(payload['midi_path']).write_bytes(b'MThd tampered')
    with pytest.raises(ValueError, match='changed since the audition'):
        workshop.pick(ws, choice)


def test_verify_compares_reaper_readback_with_the_auditioned_midi(tmp_path):
    ws, report = audition_ready(tmp_path)
    frozen = workshop.smf.parse_smf(Path(report['candidates'][0]['midi']).read_bytes())
    # REAPER reports take-relative ticks at its own resolution.
    notes = [{'ppq': n['tick'] * 2, 'end_ppq': n['tick'] * 2 + 100, 'pitch': n['pitch'],
              'velocity': n['vel'], 'muted': False} for n in frozen['notes']]
    readback = {'ok': True, 'data': {'notes': notes, 'ppq_per_quarter': frozen['ppq'] * 2,
                                     'truncated': False}}
    choice = {'candidate_id': 'fresh', 'report': report['report'], 'readback': readback}
    assert workshop.verify(ws, choice)['ok']
    readback['data']['notes'] = notes[1:]
    result = workshop.verify(ws, choice)
    assert not result['ok'] and result['missing'] == 1 and 'undo' in result['verdict']
    readback['data']['truncated'] = True
    with pytest.raises(ValueError, match='truncated'):
        workshop.verify(ws, choice)
