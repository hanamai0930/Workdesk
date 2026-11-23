"""Read-only file rework proposals with protected versions and actual input audits."""
import json
import time

from . import workspace
from .store import digest
from .work_reworks import changes as source_changes, diagnostics
from .work_transitions import plan_ref
from .work_unit_revisions import require_history
from . import work_file_sources


PROTOCOL = 'ORDINARY_FILE_REWORK_PREVIEW_V1'
SCHEMA = {'schema_version': 1, 'action': 'file_rework_units',
          'changes': [{'unit_key': 'declared ordinary unit', 'source_run_id': 'exact run',
                       'source_version': 'exact raw evidence version'}], 'reason': 'file rework reason'}


def changes(value):
    try:
        return source_changes(value)
    except ValueError as exc:
        raise ValueError(str(exc).replace('文字返工', '文件返工')) from None


def file_work(unit):
    return bool(unit['outputs'] or unit['checks'] or unit['write_paths'] or unit['dependency_gate'] != 'result')


def saved_outputs(snapshot, unit):
    missing = snapshot.get('missing_outputs', [])
    present = set(snapshot['manifest'])
    return (isinstance(missing, list) and all(isinstance(name, str) for name in missing)
            and len(set(missing)) == len(missing) and not present.intersection(missing)
            and present.union(missing) == set(unit['outputs'])
            and (not missing or snapshot['check_status'] == 'FAIL')
            and [o['definition'] for o in snapshot['observations']] == unit['checks'])


def check_rework_materials(service, db, run, original, repair):
    """Audit the failed own-output version attached in addition to dependencies."""
    source_ref = {'run_id': repair['run_id'], 'artifact': repair['artifact']}
    if (run['input_version'] != digest(run['input']) or run.get('retry_of') != repair['run_id']
            or run.get('check_rework') != source_ref or run['cooperative'].get('check_rework') != source_ref):
        raise ValueError('检查返工的原输入或失败产物引用不一致')
    source = service.collaboration.get(db, 'work_unit_run', repair['run_id'])
    linked = ('item_id', 'plan_id', 'plan_version', 'unit_key', 'unit_revision', 'authorization', 'generation')
    if (source['id'] == run['id'] or any(source[key] != run[key] for key in linked)
            or source['input_version'] != repair['input_version']
            or source['input']['dependencies'] != run['input']['dependencies']):
        raise ValueError('检查返工未绑定同一单元及原授权的失败运行')
    record = service.collaboration.get(db, 'work_unit_artifact', repair['artifact']['id'])
    snapshot = record['snapshot']
    unit = next(u for u in original['units'] if u['key'] == run['unit_key'])
    raw = service.work_units.base_evidence(db, source)
    if (record['id'] != 'unit-artifact:' + record['version'] or digest(snapshot) != record['version']
            or service.work_artifacts.reference(record) != repair['artifact']
            or record['item_id'] != run['item_id'] or snapshot['run_id'] != source['id']
            or any(snapshot[k] != source[k] for k in ('plan_id', 'plan_version', 'unit_revision'))
            or not raw['result_available'] or snapshot['result_version'] != raw['version']
            or snapshot['result_version'] != repair['result_version'] or snapshot['identity'] != repair['identity']
            or any(snapshot['identity'][k] != raw[k] for k in
                   ('task_id', 'attempt_id', 'owner', 'session_id', 'turn_id', 'connection_fingerprint'))
            or snapshot['check_status'] != 'FAIL' or not saved_outputs(snapshot, unit)
            or snapshot['manifest'] != repair['manifest'] or snapshot['observations'] != repair['checks']
            or snapshot.get('missing_outputs', []) != repair.get('missing_outputs', [])):
        raise ValueError('检查返工的原失败快照或检查证据已变化')
    if workspace.current_manifest(service.work_artifacts.folder(record), repair['manifest']) != repair['manifest']:
        raise ValueError('检查返工的原失败文件快照已变化或缺失')
    return repair['manifest']


def original_files(service, db, run):
    """Inspect saved bytes, never reconstruct old inputs/outputs from current files."""
    task = service.store.task(run['task_id'], db)
    original = service.collaboration.get(db, 'teamwork_plan', run['plan_id'])
    declared = task['spec']['materials']
    expected, problems = {}, []
    try:
        if (any(run['cooperative'].get(k) != run[k] for k in
                ('item_id', 'plan_id', 'plan_version', 'unit_key', 'unit_revision', 'input_version')) or
                run['input']['work_requirements'] != original['work_requirements']):
            raise ValueError('原冻结输入已脱离实际派发输入身份或原方案要求')
        dependency_files = [{'unit_key': d['unit_key'], **d['file_artifacts']}
                            for d in run['input']['dependencies'] if d['file_artifacts']['state'] == 'CURRENT']
        if run['input']['file_artifacts'] != dependency_files:
            raise ValueError('原文件清单与冻结依赖中的准确文件证据不一致')
        for dependency in run['input']['file_artifacts']:
            for name, record in dependency['manifest'].items():
                if name in expected and expected[name] != record:
                    raise ValueError('原冻结输入含不同版本的同路径文件')
                expected[name] = record
        repair = run['input'].get('check_rework')
        if repair is not None:
            materials = check_rework_materials(service, db, run, original, repair)
            for name, record in materials.items():
                if name in expected and expected[name] != record:
                    raise ValueError('检查返工的失败产物与冻结依赖文件版本冲突')
                expected[name] = record
        elif run.get('check_rework') is not None or run['cooperative'].get('check_rework') is not None:
            raise ValueError('检查返工缺少冻结输入证据')
        if declared != expected:
            raise ValueError('原实际 input manifest 与原冻结依赖材料不一致')
        if workspace.current_manifest(service.locations.data / 'tasks' / run['task_id'] / 'input', declared) != declared:
            raise ValueError('原实际输入快照已变化或缺失')
    except (OSError, ValueError, KeyError, TypeError) as exc:
        problems.append(str(exc))
    snapshots = []
    raw = service.work_units.base_evidence(db, run)
    unit = next(u for u in original['units'] if u['key'] == run['unit_key'])
    for row in db.execute("SELECT data FROM collaboration WHERE kind='work_unit_artifact' "
                          "AND json_extract(data,'$.snapshot.run_id')=? ORDER BY id", (run['id'],)):
        record = json.loads(row[0]); snapshot = record['snapshot']; issues = []
        if (digest(snapshot) != record['version'] or record['id'] != 'unit-artifact:' + record['version'] or
                record['item_id'] != run['item_id'] or snapshot['run_id'] != run['id'] or
                snapshot['plan_id'] != run['plan_id'] or snapshot['plan_version'] != run['plan_version'] or
                snapshot['unit_revision'] != run['unit_revision'] or
                snapshot['result_version'] != raw['version'] or
                snapshot['identity']['task_id'] != run['task_id'] or
                snapshot['identity']['attempt_id'] != run['attempt_id'] or
                snapshot['identity']['owner'] != run['owner'] or
                snapshot['identity']['session_id'] != raw['session_id'] or
                snapshot['identity']['turn_id'] != raw['turn_id'] or
                snapshot['identity']['connection_fingerprint'] != run['connection_fingerprint'] or
                not saved_outputs(snapshot, unit)):
            issues.append('旧文件快照的摘要、归属或原声明不一致')
        try:
            if workspace.current_manifest(service.work_artifacts.folder(record), snapshot['manifest']) != snapshot['manifest']:
                issues.append('保存的旧文件快照已变化或缺失')
        except (OSError, ValueError) as exc:
            issues.append(str(exc))
        snapshots.append({'reference': service.work_artifacts.reference(record),
                          'snapshot': snapshot, 'state': 'DAMAGED' if issues else 'INTACT', 'problems': issues})
    return {'run_id': run['id'], 'input_version': run['input_version'],
            'actual_input_manifest': declared, 'frozen_dependency_manifest': expected,
            'input_state': 'INVALID' if problems else 'INTACT', 'input_problems': problems,
            'snapshots': snapshots, 'output_state': 'DAMAGED' if any(s['problems'] for s in snapshots)
                else 'INTACT' if snapshots else 'NOT_RECORDED',
            'scope': 'HISTORICAL_BYTES_AND_DISPATCH_MATERIALS; NOT_CURRENT_INPUT_OR_AUTHORSHIP'}


def context(service, db, plan, rows):
    files = [original_files(service, db, r) for r in rows]
    return {'protocol': PROTOCOL, 'plan': plan_ref(plan), 'units': plan['units'], 'schema': SCHEMA,
            'work_requirements': plan['work_requirements'],
            'diagnostics': diagnostics(service, db, plan, rows),
            'prior_frontier': service.work_rework_applications.frontier(db, rows),
            'original_files': files,
            'history_source_context': work_file_sources.source_context(service, db, plan, rows, files),
            'current_project_observations': work_file_sources.current_observations(service, db, rows),
            'scope': 'FILE_REWORK_PREVIEW_ONLY; ORIGINAL_DECLARATIONS_RESOURCES_AND_AUTHORIZATION'}


def preview(service, db, item, driver, plan, rows, run, decision):
    frozen = run['input']['coordination_context'].get('file_rework_context')
    if not frozen or frozen.get('protocol') != PROTOCOL:
        raise ValueError('本次准确输入未声明文件返工协议，不能补授旧回复。')
    current = context(service, db, plan, [r for r in rows if r['id'] != run['id']])
    if frozen != current:
        raise ValueError('文件返工的原运行、输入或旧快照已变化；旧回复不能重新绑定新来源。')
    require_history(service, db, rows)
    changes(decision['changes'])
    source_files = original_files(service, db, run)
    for files in [*current['original_files'], source_files]:
        if files['input_problems'] or files['output_state'] == 'DAMAGED':
            raise ValueError('原实际输入或已登记旧文件快照损坏，须先恢复准确历史：' + files['run_id'])
    units = {u['key']: u for u in plan['units']}
    observed = {d['key']: d for d in current['diagnostics']}
    selected = {c['unit_key'] for c in decision['changes']}
    for change in decision['changes']:
        d = observed.get(change['unit_key'])
        if (not d or not d['source'] or change['source_run_id'] != d['source']['run_id'] or
                change['source_version'] != d['source']['version']):
            raise ValueError('文件返工须引用本次输入中已运行普通单元的准确原来源。')
    reasons = {key: (['EXPLICIT_FILE_REWORK'] if key in selected else []) for key in units}
    for key, d in observed.items():
        if not d['source']:
            continue
        for field, reason in [('result_available', 'RESULT_UNAVAILABLE'), ('lineage_problems', 'LINEAGE_INVALID'),
                              ('frozen_input_problems', 'FROZEN_INPUT_CHANGED')]:
            unavailable = not d[field] if field == 'result_available' else bool(d[field])
            if unavailable:
                reasons[key].append(reason)
        original_run = next(r for r in rows if r['id'] == d['source']['run_id'])
        evidence = service.work_units.evidence(db, original_run)
        if units[key]['outputs']:
            try:
                service.work_artifacts.require(evidence, 'checked' if units[key]['checks'] else 'files')
            except ValueError:
                reasons[key].append('ARTIFACT_UNAVAILABLE')
    affected = {key for key, values in reasons.items() if values}
    while True:
        more = {key for key, u in units.items() if set(u['depends']) & affected} - affected
        if not more:
            break
        for key in more:
            reasons[key].append('AFFECTED_DEPENDENCY')
        affected.update(more)
    if not any(file_work(units[key]) for key in affected if units[key]['role'] != 'coordinate'):
        raise ValueError('纯文字返工请使用文字返工协议；文件预览须涉及原文件或写入声明。')
    files_by_run = {f['run_id']: f for f in current['original_files']}
    entries = []
    for key, unit in units.items():
        d = observed.get(key); source = d['source'] if d else None
        files = files_by_run.get(source['run_id']) if source else None
        state = ('AFFECTED' if key in affected else 'HISTORICAL_COORDINATOR' if unit['role'] == 'coordinate'
                 else 'ELIGIBLE_RESULT' if source else 'NOT_RUN')
        outputs = [{'path': name, 'unit_key': key, 'unit_revision': digest(unit),
                    'predecessors': [s['reference'] for s in files['snapshots']] if files else [],
                    'identity_state': 'PENDING_NEW_PLAN_AND_RUN', 'version_state': 'NOT_CREATED',
                    'downstream': 'NEW_EXACT_RESULT_FILES_AND_DECLARED_CHECKS_REQUIRED'}
                   for name in unit['outputs']] if key in affected else []
        entries.append({'key': key, 'title': unit['title'], 'state': state, 'reasons': reasons[key],
                        'source': source, 'diagnostic': d, 'original_files': files,
                        'candidate_revision': digest(unit), 'output_intents': outputs,
                        'write_paths': unit['write_paths'], 'dependency_gate': unit['dependency_gate']})
    auth = service.work_units.authorization(db, driver)
    source_audit = work_file_sources.audit(service, db, plan, rows, affected)
    blockers = []
    blockers.extend(source_audit['problems'])
    from .work_file_rework_applications import scope
    try:
        write_scope = scope(plan, affected)
        baseline = service.work_file_rework_applications.observe(
            service.work_file_rework_applications.root(db, item), sorted({n for names in write_scope.values() for n in names}))
    except (ValueError, OSError) as exc:
        write_scope, baseline = None, None
        blockers.append(str(exc))
    if not auth or len(rows) >= auth['max_attempts_total']:
        blockers.append('原累计授权次数不足；预览不增加授权')
    if not auth or time.time() >= auth['dispatch_before']:
        blockers.append('原新增派发截止已到期；预览不延长截止')
    body = {'base_plan': plan_ref(plan), 'candidate_units': plan['units'], 'units': entries,
            'original_files': current['original_files'], 'source_actual_input': source_files,
            'history_source_audit': source_audit,
            'write_scope': write_scope, 'write_before_observation': baseline,
            'original_frontier': current['prior_frontier']}
    return {**body, 'candidate_version': digest(body), 'direct_changes': sorted(selected),
            'affected_units': [key for key in units if key in affected],
            'eligible_results': [u['key'] for u in entries if u['state'] == 'ELIGIBLE_RESULT'],
            'usage': service.work_units.usage(db, rows), 'authorization': auth, 'execution_blockers': blockers,
            'effect': 'PREVIEW_ONLY; NO_PLAN_CHANGE_DISPATCH_OR_FILE_WRITE',
            'provenance': 'OBSERVED_BYTES_NOT_PROOF_OF_AUTHORSHIP',
            'independent_review': 'NOT_PERFORMED', 'human_acceptance': 'NOT_RECORDED'}
