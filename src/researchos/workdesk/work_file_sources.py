"""Read-only historical file evidence matching. No current input or execution grant."""
from .store import digest
from .work_transitions import plan_ref
from . import workspace


PROTOCOL = 'FILE_REPLACEMENT_SOURCE_AUDIT_V1'
FILE_FIELDS = ('outputs', 'checks', 'write_paths', 'dependency_gate')


def resource_status(service, db, plans):
    catalog = service.resources.catalog(db)
    values = []
    for plan in plans:
        record = plan.get('unit_revision_lineage')
        references = [u['member']['reference'] for u in plan['units']]
        references += [m['reference'] for m in [plan.get('coordinator'), plan.get('integrator')] if m]
        references += record['original_resources'] if record else []
        for reference in {digest(r): r for r in references}.values():
            member = next((m for m in catalog['members'] if m['id'] == reference['member_id']), None)
            connection = next((c for c in catalog['connections'] if c['id'] == reference['connection_id']), None)
            quota = next((q for q in catalog['quota_sources'] if q['id'] == reference['quota_id']), None)
            current = ({'member_id': member['id'], 'member_revision': member['revision'],
                        'connection_id': connection['id'], 'connection_revision': connection['revision'],
                        'quota_id': quota['id'], 'quota_revision': quota['revision']}
                       if member and connection and quota else None)
            valid = bool(current == reference and member['available'] and
                         member['connection_id'] == connection['id'] and connection['quota_id'] == quota['id'])
            values.append({'plan': plan_ref(plan), 'original': reference, 'current': current, 'valid': valid})
    return values


def source_context(service, db, plan, rows, files):
    if [r['id'] for r in rows] != [f['run_id'] for f in files]:
        raise ValueError('历史输入/快照核对清单与原运行集合不一致。')
    plans = service.work_unit_revision_applications.ancestors(db, plan)
    return {'protocol': PROTOCOL, 'plans': [plan_ref(p) for p in plans],
            'resources': resource_status(service, db, plans),
            'protected_runs': [{'run_id': r['id'], 'run_version': digest(r),
                                'base_evidence_version': service.work_units.base_evidence(db, r)['version'],
                                'original_files_version': digest(f)} for r, f in zip(rows, files)],
            'scope': 'HISTORICAL_INTEGRITY_AND_CANDIDATES_ONLY; NO_CURRENT_INPUT_OR_PERMISSION'}


def current_observations(service, db, rows):
    values = []
    for run in rows:
        root = service.store.attempt(run['attempt_id'], db)['workspace']
        unit = service.work_artifacts.unit(db, run)
        paths = {}
        for name in unit['outputs']:
            try:
                manifest = workspace.current_manifest(root, [name])
                paths[name] = {'state': 'OBSERVED' if name in manifest else 'MISSING', 'manifest': manifest}
            except (OSError, ValueError) as exc:
                paths[name] = {'state': 'UNAVAILABLE', 'problem': str(exc)}
        values.append({'run_id': run['id'], 'outputs': paths})
    return values


def replacement_candidates(service, db, plan, rows, affected, files):
    by_run = {f['run_id']: f for f in files}
    units = {u['key']: u for u in plan['units'] if u['key'] in affected and u['role'] != 'coordinate'}
    candidates = []
    for run in rows:
        unit = units.get(run['unit_key'])
        if not unit or not unit['outputs']:
            continue
        old = service.work_artifacts.unit(db, run)
        if any(old[k] != unit[k] for k in FILE_FIELDS):
            continue
        body = {'protocol': PROTOCOL, 'state': 'UNAPPROVED_CANDIDATE', 'unit_key': unit['key'],
                'consumer_plan': plan_ref(plan), 'candidate_revision': digest(unit),
                'source_plan': {'id': run['plan_id'], 'version': run['plan_version']},
                'run_id': run['id'], 'run_version': digest(run), 'unit_revision': run['unit_revision'],
                'declarations': {k: old[k] for k in FILE_FIELDS},
                'saved_versions': [s['reference'] for s in by_run[run['id']]['snapshots']],
                'permission': 'NONE; EXACT_HISTORICAL_MATCH_ONLY; NEW_PLAN_AND_RUN_REQUIRED'}
        candidates.append({**body, 'version': digest(body)})
    return candidates


def historical_match(service, db, run, expected_version, binding, files):
    """Match the entire old hash; never return a historical value as current evidence."""
    live = service.work_units.evidence(db, run)
    if files['input_problems'] or files['output_state'] == 'DAMAGED':
        raise ValueError('原实际输入或旧文件快照损坏，不能核对历史来源。')
    if live['version'] == expected_version:
        return {'kind': 'CURRENT_UNCHANGED', 'version': expected_version, 'artifact': live['file_artifacts']['reference']}
    unit = service.work_artifacts.unit(db, run)
    if (not binding or binding.get('protocol') != PROTOCOL or binding.get('state') != 'UNAPPROVED_CANDIDATE' or
            binding.get('version') != digest({k: v for k, v in binding.items() if k != 'version'}) or
            binding.get('run_id') != run['id'] or binding.get('run_version') != digest(run) or
            binding.get('unit_key') != run['unit_key'] or binding.get('unit_revision') != run['unit_revision'] or
            binding.get('source_plan') != {'id': run['plan_id'], 'version': run['plan_version']} or
            binding.get('declarations') != {k: unit[k] for k in FILE_FIELDS} or
            binding.get('saved_versions') != [s['reference'] for s in files['snapshots']]):
        raise ValueError('原证据已变化，且没有本次准确文件替代候选关系。')
    consumer = service.collaboration.get(db, 'teamwork_plan', binding['consumer_plan']['id'])
    candidate_unit = next((u for u in consumer['units'] if u['key'] == run['unit_key']), None)
    if (plan_ref(consumer) != binding['consumer_plan'] or
            digest({k:v for k,v in consumer.items() if k != 'version'}) != consumer['version'] or
            not candidate_unit or digest(candidate_unit) != binding['candidate_revision'] or
            any(candidate_unit[k] != unit[k] for k in FILE_FIELDS)):
        raise ValueError('文件替代候选已脱离准确消费方案或原文件声明。')
    raw = service.work_units.base_evidence(db, run)
    # Only current-project status is variable. All saved bytes and source identity
    # have already passed original_files; the complete expected hash must match.
    template = service.work_artifacts.status(db, run, raw)
    states = []
    for saved in files['snapshots']:
        snapshot = saved['snapshot']
        for changed in (False, True):
            status = {**template, 'state': 'STALE' if changed else 'CURRENT',
                      'reference': saved['reference'], 'manifest': snapshot['manifest'],
                      'problems': ['项目产物已变化或缺失'] if changed else [],
                      'checks': snapshot['observations'], 'check_status': 'STALE' if changed else snapshot['check_status']}
            states.append(('HISTORICAL_PROJECT_CHANGED' if changed else 'HISTORICAL_SAVED_SNAPSHOT', status))
    states.append(('ORIGINAL_NOT_RECORDED', {**template, 'state': 'MISSING' if unit['outputs'] else 'NOT_REQUIRED',
                  'reference': None, 'manifest': {}, 'problems': [], 'checks': [], 'check_status': 'NOT_RECORDED'}))
    for kind, status in states:
        value = {**raw, 'result_version': raw['version'], 'file_artifacts': status, 'check_status': status['check_status']}
        if digest(value) == expected_version:
            return {'kind': kind, 'version': expected_version, 'artifact': status['reference']}
    raise ValueError('准确旧证据摘要无法由原运行和完整保存版本重建；历史来源继续阻止。')


def compare_frontier(service, db, rows, expected, candidates, files):
    if [r['id'] for r in rows] != [e['run_id'] for e in expected]:
        raise ValueError('原运行集合或顺序已变化，含追加运行；不能重绑旧前沿。')
    by_run = {c['run_id']: c for c in candidates}
    protected = {f['run_id']: f for f in files}
    matched = []
    for run, entry in zip(rows, expected):
        if digest(run) != entry['run_version']:
            raise ValueError('原运行完整正文或输入已变化：' + run['id'])
        match = service.work_file_handoff_ledger.frontier_history(db, run, entry['evidence_version'], rows)
        if match is None:
            match = historical_match(service, db, run, entry['evidence_version'], by_run.get(run['id']), protected[run['id']])
        matched.append({'run_id': run['id'], **match})
    return matched


def audit(service, db, plan, rows, affected):
    from .work_file_reworks import original_files
    from .work_unit_revisions import require_history
    require_history(service, db, rows)
    files = [original_files(service, db, r) for r in rows]
    if any(f['input_problems'] or f['output_state'] == 'DAMAGED' for f in files):
        raise ValueError('原实际输入或全部已登记旧文件快照核对失败。')
    context = source_context(service, db, plan, rows, files)
    candidates = replacement_candidates(service, db, plan, rows, affected, files)
    applied = service.work_file_rework_applications.records(db, plan, rows)
    prior = [c for record in applied for c in record['replacement_candidates']]
    bindings = {c['run_id']: c for c in [*prior, *candidates]}
    problems = ['原方案或局部修订资源已变化或不可用'] if any(not r['valid'] for r in context['resources']) else []
    checks = []
    for original in service.work_unit_revision_applications.ancestors(db, plan):
        for name, protocol in [('rework_lineage', 'ORDINARY_TEXT_REWORK_REVISION_V1'),
                               ('unit_revision_lineage', 'ORDINARY_UNIT_REVISION_APPLICATION_V1')]:
            record = original.get(name)
            if not record:
                continue
            ids = {e['run_id'] for e in record['original_frontier']}
            selected = [r for r in rows if r['id'] in ids or r['plan_id'] in record['original_plan_ids']]
            try:
                if record['protocol'] != protocol:
                    raise ValueError('原修订协议已变化。')
                matches = compare_frontier(service, db, selected, record['original_frontier'], list(bindings.values()), files)
                checks.append({'plan': plan_ref(original), 'kind': name, 'state': 'MATCHED', 'matches': matches})
            except ValueError as exc:
                problems.append(str(exc))
                checks.append({'plan': plan_ref(original), 'kind': name, 'state': 'BLOCKED', 'problems': [str(exc)]})
    body = {'protocol': PROTOCOL, 'protected_history': context, 'replacement_candidates': candidates,
            'original_frontier_checks': checks, 'problems': problems,
            'state': 'BLOCKED' if problems else 'READY_FOR_EXPLICIT_REPLACEMENT',
            'current_delivery': 'SEPARATE_LIVE_EVIDENCE_AND_ACTUAL_INPUT_REQUIRED',
            'execution_permission': False, 'ordinary_validators': 'UNCHANGED; CANDIDATE_AUDIT_ONLY'}
    return {**body, 'version': digest(body)}
