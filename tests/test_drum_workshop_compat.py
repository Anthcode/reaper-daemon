"""Compatibility contract for the arrangement work (docs/drum-arrangement-plan.md).

Later PRs add a plan, manifest v2 and richer reports. These tests pin what a
v1 workshop reads and writes today so those additions stay optional.
"""
import json

import pytest

import drum_workshop as workshop
from test_drum_workshop import brief, populate

V1_MANIFEST_KEYS = {'version', 'context', 'brief', 'seed', 'candidates',
                    'references', 'preferences'}
V1_REPORT_KEYS = {'version', 'ok', 'candidates', 'comparisons', 'reference_matches',
                  'review_flags', 'judge_preferences', 'audition_candidates',
                  'policy', 'quality', 'report'}
V1_CANDIDATE_KEYS = {'candidate_id', 'protected_wildcard', 'audition_status', 'warnings',
                     'valid', 'dsl_sha256', 'intent', 'midi', 'notes', 'length_seconds',
                     'length_bars', 'family_hits'}
V1_FEEDBACK_KEYS = {'candidate_id', 'usefulness', 'novelty', 'reason', 'scope', 'context',
                    'dsl_sha256', 'report', 'source', 'preference_promotion'}


def test_v1_brief_needs_no_arrangement_fields(tmp_path):
    minimal = {k: v for k, v in brief().items() if k != 'exclude_families'}
    result = workshop.prepare(minimal, tmp_path / 'ws')
    assert result['candidates'] == ['fresh', 'contrast', 'wildcard']
    manifest = workshop.read_json(tmp_path / 'ws' / 'workshop.json')
    assert set(manifest) == V1_MANIFEST_KEYS and manifest['version'] == 1


def test_v1_report_and_feedback_shapes(tmp_path):
    ws = tmp_path / 'ws'
    workshop.prepare(brief(), ws)
    populate(ws)
    report = workshop.evaluate(ws)
    assert set(report) == V1_REPORT_KEYS and report['version'] == 1
    assert all(set(c) == V1_CANDIDATE_KEYS for c in report['candidates'])
    saved = workshop.feedback(ws, {'candidate_id': 'wildcard', 'report': report['report'],
                                   'usefulness': 'use', 'novelty': 'new',
                                   'reason': 'Keeps the pulse'})['feedback']
    assert set(saved) == V1_FEEDBACK_KEYS


def test_wildcard_is_part_of_every_v1_workspace(tmp_path):
    ws = tmp_path / 'ws'
    workshop.prepare(brief(), ws)
    manifest = workshop.read_json(ws / 'workshop.json')
    manifest['candidates'] = ['fresh', 'contrast', 'reference']
    (ws / 'workshop.json').write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match='manifest'):
        workshop.load_workspace(ws)
