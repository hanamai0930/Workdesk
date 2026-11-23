"""Exact original-root adjustment input and preview; no save or apply permit."""
from copy import deepcopy
import time
import math

from .store import digest
from .plans import PlanCommands
from .work_adjustments import SCHEMA, changes
from .work_transitions import plan_ref, source_ref
from .work_coordinator_handoff_records import PERMISSIONS, versioned
from .work_coordinator_handoff_runtime import reader
from .work_coordinator_handoff_context import WorkCoordinatorHandoffContext
from .work_coordinator_handoff_adjustment_runtime import PROTOCOL


INPUT = 'COORDINATOR_FILE_HANDOFF_ADJUSTMENT_INPUT_V1'
PREVIEW = 'COORDINATOR_FILE_HANDOFF_ADJUSTMENT_PREVIEW_V1'
ALLOWED = 'ORDINARY_DEPENDENCIES_ONLY_OUTSIDE_PROTECTED_FRONTIER'


def proposal_state(snapshot):
    """Both immutable generations retain the same exact independent protocol."""
    return (snapshot.get('state') in {'ADJUSTMENT_PREVIEW', 'ADJUSTMENT_READY'} and
            snapshot.get('adjustment', {}).get('protocol') == PREVIEW)


def protected(units, relation):
    graph = {u['key']: u['depends'] for u in units}
    found = {relation['source_unit'], relation['target_unit']}
    while True:
        expanded = found | {k for key in found for k in graph[key]}
        if expanded == found:
            return found
        found = expanded


def qualifications(plan, phase, frames):
    by_key = {f['run']['unit_key']: f for f in frames}
    result = []
    for unit in plan['units']:
        frame = by_key.get(unit['key']); key = unit['key']
        qualification = ('HISTORICAL_RETIRED' if phase == 'B_COMPLETED' and key == plan['file_handoffs'][0]['source_unit'] else
            'HISTORICAL_COORDINATOR' if frame and unit['role'] == 'coordinate' else
            'ELIGIBLE_RESULT' if frame and frame['evidence']['result_available'] else
            'RECORDED' if frame else 'NOT_RUN')
        result.append({'key': key, 'qualification': qualification,
            'source': deepcopy(frame['evidence']) if frame else None})
    return result


def input_context(plan, context, frames):
    return versioned({'protocol': INPUT, 'root': context['root'], 'plan': plan_ref(plan),
        'capability': context['adjustment_capability'], 'saved_path': context.get('saved_path'), 'phase': context['phase'],
        'units': plan['units'], 'protected_keys': sorted(protected(plan['units'], plan['file_handoffs'][0])),
        'source_qualifications': qualifications(plan, context['phase'], frames), 'allowed_changes': ALLOWED,
        'schema': SCHEMA, 'prior_frontier': context['prior_frontier'],
        'material_observation': context['material_observation'], **PERMISSIONS,
        'scope': 'FROZEN_ORIGINAL_ROOT_ADJUSTMENT_CONTEXT; NO_SAVED_EDGE_PERMISSION'})


def _require_result(evidence, unit):
    """The same result/files/checked gates, over already verified evidence."""
    if not evidence['result_available']:
        raise ValueError('准确文字结果不可用')
    files = evidence['file_artifacts']
    if unit['checks'] or unit['outputs']:
        if not files['required_outputs'] or files['state'] != 'CURRENT':
            raise ValueError('前置文件产物尚未登记或版本失效')
        if unit['checks'] and files['check_status'] != 'PASS':
            raise ValueError('前置本地检查尚未全部通过')


def rebuild_preview(plan, history, run, decision, *, observed_at):
    """Pure semantics over independently verified facts; grants no authority.

    Live callers supply current evidence and the clock. Historical callers
    supply snapshot evidence and the actual proposal time, never current().
    All source/run/event/byte verification belongs to the caller's reader.
    """
    context = run['input'].get('handoff_coordination_context', {})
    if (context.get('protocol') != PROTOCOL or decision['action'] != 'adjust_dependencies' or
            context.get('allowed_actions') != ['dispatch', 'pause', 'adjust_dependencies']):
        raise ValueError('依赖调整须来自真实V3冻结来源，旧回复不可补许可。')
    sources = [f for f in history['frames'] if f['run']['input']['unit']['role'] == 'coordinate']
    source = sources[-1] if sources else None
    if (not source or source['run'] != run or
            history['phase'] not in {'BEFORE_A', 'A_CAPTURED', 'B_COMPLETED'} or
            not history['authorization'] or history['driver']['state'] != 'RESERVED'):
        raise ValueError('调整来源不是当前准确可操作阶段或最新真实coordinate。')
    evidence = source['source_evidence']
    if not evidence['exact_identity'] or not evidence['result_available']:
        raise ValueError('调整来源缺少准确成功退出结果。')
    from .work_proposals import parse
    if parse(evidence['answer']) != decision:
        raise ValueError('调整决定不是准确原回复，不能替换后重算预览。')
    if (type(observed_at) not in (int, float) or not math.isfinite(observed_at) or
            observed_at < run['created'] or observed_at >= history['authorization']['dispatch_before'] or
            history['usage']['attempts'] + 2 > history['authorization']['max_attempts_total']):
        raise ValueError('原授权不足以恢复一次新协调与普通工作。')
    prior = [f for f in history['current_frames'] if f['run']['id'] != run['id']]
    full_prior = [f for f in history['frames'] if f['run']['id'] != run['id']]
    current = {**context, 'phase': history['phase'], 'prior_frontier': [WorkCoordinatorHandoffContext.frontier_entry(f) for f in full_prior],
               'material_observation': history['material_observation']}
    frozen = run['input']['coordination_context'].get('adjustment_context')
    if frozen != input_context(plan, current, prior):
        raise ValueError('调整冻结完整前沿、原材料或当前资格已变化，须新声明协调来源。')
    changes(decision['changes'])
    original = {u['key']: u for u in plan['units']}; replacements = {}
    for change in decision['changes']:
        unit = original.get(change['unit_key'])
        if not unit or unit['role'] == 'coordinate' or set(unit['depends']) == set(change['depends']):
            raise ValueError('调整只接受原声明普通单元的实际依赖变化。')
        replacements[unit['key']] = {**unit, 'depends': sorted(change['depends'])}
    candidate = [replacements.get(u['key'], u) for u in plan['units']]
    PlanCommands.validate_graph({u['key']: u['depends'] for u in candidate})
    for unit in candidate:
        for key in unit['depends']:
            dependency = original[key]
            if unit['dependency_gate'] in {'files', 'checked'} and not dependency['outputs']:
                raise ValueError('文件依赖须有声明产物。')
            if unit['dependency_gate'] == 'checked' and not dependency['checks']:
                raise ValueError('checked依赖须有声明检查。')
    affected = set(replacements)
    while True:
        expanded = affected | {u['key'] for u in candidate if set(u['depends'] + original[u['key']]['depends']) & affected}
        if expanded == affected: break
        affected = expanded
    guarded = protected(plan['units'], plan['file_handoffs'][0]) | protected(candidate, plan['file_handoffs'][0])
    if affected & guarded:
        raise ValueError('调整影响旧/新图的A/B及完整保护上游。')
    current_frames = {f['run']['unit_key']: f for f in history['current_frames']}
    retired = plan['file_handoffs'][0]['source_unit'] if history['phase'] == 'B_COMPLETED' else None
    carry, historical = {}, []
    for unit in plan['units']:
        key = unit['key']; frame = current_frames.get(key)
        if not frame: continue
        if unit['role'] == 'coordinate':
            historical.append(key); continue
        if key == retired: continue
        full = frame['source_evidence']
        _require_result(full, unit)
        if key in affected:
            if unit['outputs'] or unit['write_paths']:
                raise ValueError('受影响已运行普通文件工作须用专用文件修订合同。')
        else:
            carry[key] = source_ref(frame['run'], full)
    blockers, resumes = {}, []
    def ready(unit, pending=None):
        if unit['key'] == retired: return False
        if unit['key'] == plan['file_handoffs'][0]['target_unit'] and history['phase'] != 'A_CAPTURED': return False
        return all(key == pending or (key in carry and key != retired) for key in unit['depends'])
    pending_targets = [u for u in candidate if u['role'] != 'coordinate' and u['key'] not in carry and u['key'] != retired]
    for unit in candidate:
        if unit['role'] != 'coordinate' or unit['key'] in historical: continue
        if not ready(unit):
            blockers[unit['key']] = 'DEPENDENCY_NOT_CARRIED_OR_HISTORICAL'; continue
        if not any(ready(target, unit['key']) for target in pending_targets):
            blockers[unit['key']] = 'NO_ORDINARY_CANDIDATE_AFTER_NEW_PROPOSAL'; continue
        resumes.append(unit['key'])
    # An uncompleted protected A/B must retain a real route after old coordinators retire.
    for key in guarded:
        if key == retired or key in current_frames or original[key]['role'] == 'coordinate': continue
        if any(dep in historical for dep in original[key]['depends']):
            raise ValueError('保存后未完成保护工作依赖历史coordinate，无法真实恢复。')
    if not resumes:
        raise ValueError('保存后没有可达的未运行原声明coordinate及普通候选。')
    return versioned({'protocol': PREVIEW, 'root': context['root'], 'parent_plan': plan_ref(plan),
        'capability': context['adjustment_capability'], 'operation': 'ADJUSTMENT', **PERMISSIONS,
        'scope': 'PREVIEW_ONLY; SAVED_EDGE_NOT_OPEN', 'item_revision': history['task']['revision'],
        'driver': {k: history['driver'][k] for k in ('id', 'generation', 'revision')}, 'authorization': {k: history['authorization'][k] for k in ('id', 'revision')},
        'source': {k: evidence[k] for k in ('run_id', 'task_id', 'attempt_id', 'owner', 'session_id', 'turn_id',
                                        'connection_fingerprint', 'result_digest', 'version')},
        'frozen_context': {k: context[k] for k in ('protocol', 'version', 'root')}, 'phase': history['phase'],
        'prior_frontier': frozen['prior_frontier'], 'frontier': digest([{'run_id': f['run']['id'], 'evidence': f['evidence']['version']} for f in history['frames']]),
        'candidate_units': candidate, 'direct_changes': sorted(replacements), 'affected_keys': sorted(affected),
        'protected_keys': sorted(guarded), 'qualifications': qualifications(plan, history['phase'], history['current_frames']),
        'carry_sources': carry, 'historical_coordinators': sorted(historical),
        'resume_coordinate_keys': sorted(resumes), 'resume_blockers': blockers,
        'material_observation': history['material_observation'], 'usage': history['usage']})


def preview(service, db, item, driver, plan, rows, run, decision):
    runtime = reader(service, db, plan)
    history = runtime.read(db, plan)
    if (rows != service.work_units.rows(db, item['id']) or history['driver'] != driver or
            history['task'] != {'id': item['id'], 'revision': item['revision']}):
        raise ValueError('调整来源不是当前准确责任、任务修订或完整运行集合。')
    history['usage'] = service.work_units.usage(db, rows)
    return rebuild_preview(plan, history, run, decision, observed_at=time.time())
