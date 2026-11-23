"""Detached FILE source/material facts; command admission stays in the verifier."""
from copy import deepcopy

from .store import digest
from .work_file_handoff_apply import APPLICATION_KIND
from .work_file_handoff_diagnostics import PERMISSIONS
from .work_file_handoff_file_reworks import project_sources
from .work_file_reworks import original_files
from . import work_file_sources


def file_scope(service, db, plan, item, sources, observation, operational):
    proof = observation.require(service.work_file_handoff_applications, db, plan)
    rows = service.work_units.rows(db, item['id'])
    files = [original_files(service, db, r) for r in rows]
    current = work_file_sources.current_observations(service, db, rows)
    root = service.work_file_handoff_ledger.get(db, APPLICATION_KIND,
        proof['edges'][0])['snapshot']['lineage']['proof']['project_root']
    # No reader protocol, path hash, current candidate or transient button state.
    material = {'units': [{k: deepcopy(v) for k, v in u.items() if k != 'selectable'}
                         for u in sources['units']],
                'original_files': files, 'current_project_observations': current,
                'work_requirements_version': digest(plan['work_requirements'])}
    units = deepcopy(material['units'])
    seeds = []

    def qualify(selected):
        request = {'selections': [{'unit_key': u['key'],
            'source_run_id': u['source']['run_id'],
            'source_version': u['evidence_reference']['version']} for u in selected],
            'reason': '公共只读文件来源资格'}
        value = project_sources(service, db, plan, request, rows, observation.evidence, proof)
        names = sorted({n for paths in value['write_scope'].values() for n in paths})
        baseline = service.work_file_rework_applications.observe(root, names)
        if baseline['missing']:
            raise ValueError('当前声明文件缺失。')
        for entry in value['units']:
            if entry['key'] in value['write_scope'] and entry['prior_source']:
                manifest = entry['prior_source']['evidence']['file_artifacts']['manifest']
                if any(baseline['manifest'].get(n) != manifest.get(n)
                       for n in value['write_scope'][entry['key']]):
                    raise ValueError('当前文件与准确原快照不一致。')
        return value

    for unit in units:
        unit.update(selectable=False, selection_problems=[])
        if not unit['source'] or not unit['definition']['outputs']:
            continue
        try:
            value = qualify([unit])
            unit.update(selectable=True, affected_units=value['affected_units'], write_scope=value['write_scope'])
            seeds.append(unit)
        except ValueError as exc:
            unit['selection_problems'] = [str(exc)]
    for unit in units:
        if unit['definition']['outputs'] or not unit['source'] or not seeds:
            continue
        try:
            value = qualify([seeds[0], unit])
            unit.update(selectable=True, affected_units=value['affected_units'], write_scope=value['write_scope'])
        except ValueError as exc:
            unit['selection_problems'] = [str(exc)]
    problems = ['原输入或全部旧快照损坏。'] if any(
        f['input_problems'] or f['output_state'] == 'DAMAGED' for f in files) else []
    capture = []
    runs = {r['id']: r for r in rows}
    for unit in (item.get('cooperation') or {}).get('units', []):
        run = runs.get(unit.get('latest_run_id'))
        if not run or run['plan_id'] != plan['id'] or not run['input'].get('handoff_file_rework_context'):
            continue
        result = unit.get('result') or {}
        artifact = result.get('file_artifacts') or {}
        can_capture = bool(unit['can_check'])
        capture.append({'key': unit['key'], 'run_id': run['id'],
            'result_version': result.get('result_version'), 'artifact': artifact.get('reference'),
            'state': 'READY_TO_CAPTURE' if can_capture and artifact.get('state') == 'MISSING'
                else artifact.get('state', 'UNAVAILABLE'),
            'can_capture': can_capture, 'check_status': artifact.get('check_status'),
            'context': run['input']['handoff_file_rework_context']})
    return deepcopy({**material, 'sources': {'units': units}, 'scope_version': digest(material),
        'can_check': bool(operational and seeds and not problems), 'problems': problems,
        'capture_states': capture, **PERMISSIONS})
