"""Pure complete-graph text-rework previews, without source/current permission.

The real owner supplies independently reconstructed frames. No SQL, resource
selection, Current facade or execution-terminal decoder is provided here.
"""
from copy import deepcopy
import json

from .store import canonical, digest
from .work_transitions import plan_ref, source_ref
from .work_artifacts import WorkArtifacts
from .work_coordinator_handoff_context import TERMINAL
from .work_coordinator_handoff_records import fields, reference, versioned, PERMISSIONS
from .work_coordinator_handoff_roots import CONFIG_FIELDS
from . import work_coordinator_handoff_text_rework_contracts as c


def protected_keys(plan):
    relation = plan['file_handoffs']
    if len(relation) != 1:
        raise ValueError('文字返工保持唯一原A/B关系。')
    units = {unit['key']: unit for unit in plan['units']}
    result = {relation[0]['source_unit'], relation[0]['target_unit']}
    while True:
        more = {dependency for key in result for dependency in units[key]['depends']} - result
        if not more:
            return sorted(result)
        if not more <= units.keys():
            raise ValueError('文字返工保护前沿存在未声明依赖。')
        result.update(more)


def _ordinary(unit):
    return (unit['role'] != 'coordinate' and not unit['outputs'] and not unit['checks'] and
        not unit['write_paths'] and unit['dependency_gate'] == 'result')


def _current_frames(plan, history):
    units = {unit['key']: unit for unit in plan['units']}
    result = {}
    for frame in history['current_frames']:
        run = frame['run']; key = run['unit_key']
        if key not in units or units[key]['role'] == 'coordinate':
            continue
        if key in result or run['unit_revision'] != digest(units[key]) or run['input']['unit'] != units[key]:
            raise ValueError('文字返工当前来源须为唯一准确原定义。')
        evidence = frame['source_evidence']
        if not evidence['exact_identity'] or evidence['blockers'] or evidence['state'] not in TERMINAL:
            raise ValueError('文字返工原来源须由完整owner证明准确已知退出。')
        if frame['files']['input_problems']:
            raise ValueError('文字返工不修复损坏的原冻结输入。')
        result[key] = frame
    return result


def ordinary_sources(plan, history, original_plans):
    """Complete data from the proven frames, never from a run-input assertion."""
    result = []
    for key, frame in _current_frames(plan, history).items():
        if not _ordinary(next(unit for unit in plan['units'] if unit['key'] == key)):
            continue
        run = frame['run']; source_plan = original_plans[run['plan_id']]
        definition = next(unit for unit in source_plan['units'] if unit['key'] == key)
        if plan_ref(source_plan) != {'id': run['plan_id'], 'version': run['plan_version']} or definition != run['input']['unit']:
            raise ValueError('文字返工原来源须属于实际已重建方案及定义。')
        result.append({'key': key, 'definition_revision': run['unit_revision'],
            'run': deepcopy(run), 'original_plan': plan_ref(source_plan),
            'original_definition': deepcopy(definition), 'original_input': deepcopy(run['input']),
            'evidence': deepcopy(frame['evidence']), 'files': deepcopy(frame['files'])})
    order = {unit['key']: index for index, unit in enumerate(plan['units'])}
    return sorted(result, key=lambda value: order[value['key']])


def review_context(capability):
    cap = c.validate_capability(capability); body = cap['snapshot']; obs = body['observation']
    plan = obs['parent_plan_snapshot']
    return versioned({'protocol': c.REVIEW_INPUT, 'capability': {k: cap[k] for k in ('id', 'version')},
        'root': body['root'], 'parent_plan': body['parent_plan'], 'task': obs['task'],
        'driver': obs['driver'], 'authorization': obs['authorization'], 'phase': obs['phase'],
        'prior_frontier': obs['frontier'], 'material_observation': obs['material_observation'],
        'selected_unit': next(unit for unit in plan['units'] if unit['key'] == obs['review_coordinate']['key']),
        'configuration': {k: plan[k] for k in CONFIG_FIELDS}, 'work_requirements': plan['work_requirements'],
        'units': plan['units'], 'ordinary_sources': obs['ordinary_sources'],
        'coordinate_definitions': obs['coordinate_definitions'], 'protected_keys': obs['protected_keys'],
        'allowed_actions': c.ACTIONS, 'rework_schema': c.REWORK_SCHEMA, 'pause_schema': c.PAUSE_SCHEMA,
        **PERMISSIONS})


def observation_value(plan, history, original_plans, coordinate, readiness, observed_at):
    """Shared pure shape for live and independent historical observations."""
    c.coordinate_ref(coordinate); c.finite(observed_at)
    selected = next((u for u in plan['units'] if u['key'] == coordinate['key']), None)
    if selected is None or coordinate['revision'] != digest(selected):
        raise ValueError('返工提案须选择原准确协调定义。')
    problems = _coordinate_blockers(plan, selected, set(),
        {f['run']['unit_key']: f for f in history['current_frames']})
    if problems:
        raise ValueError('返工提案回合原依赖受阻：' + '；'.join(problems))
    return c.validate_observation(versioned({'protocol': c.OBSERVATION,
        'task': history['task'], 'parent_plan_snapshot': plan, 'driver': history['driver'],
        'authorization': history['authorization'], 'saved_path': history.get('saved_path'),
        'phase': history['phase'], 'frontier': history['frontier'],
        'current_frames': history['current_frames'], 'delivery': history['delivery'],
        'completion': history['completion'], 'material_observation': history['material_observation'],
        'configuration': {k: plan[k] for k in CONFIG_FIELDS},
        'work_requirements': plan['work_requirements'], 'review_coordinate': coordinate,
        'coordinate_definitions': [u for u in plan['units'] if u['role'] == 'coordinate'],
        'ordinary_sources': ordinary_sources(plan, history, original_plans),
        'protected_keys': protected_keys(plan), 'usage': history['usage'],
        'readiness_observation': readiness, 'observed_at': observed_at}))


def review_input(capability):
    """Generate the entire new review input from its proved observation."""
    cap = c.validate_capability(capability)
    obs = cap['snapshot']['observation']; plan = obs['parent_plan_snapshot']
    context = review_context(cap)
    unit = context['selected_unit']
    frames = {f['run']['unit_key']: f for f in obs['current_frames']}
    problems = _coordinate_blockers(plan, unit, set(), frames)
    if problems:
        raise ValueError('返工回合不能消费原依赖：' + '；'.join(problems))
    dependencies = []
    for key in unit['depends']:
        evidence = frames[key]['source_evidence']
        WorkArtifacts.require(None, evidence, unit['dependency_gate'])
        dependencies.append({'unit_key': key, **deepcopy(evidence)})
    value = {'item_id': plan['item_id'], 'item_revision': obs['task']['revision'],
        'plan_id': plan['id'], 'plan_version': plan['version'],
        'work_requirements': plan['work_requirements'], 'unit': unit,
        'dependencies': dependencies,
        'check_evidence': [{'unit_key': d['unit_key'], 'artifact': d['file_artifacts']['reference'],
            'check_status': d['check_status']} for d in dependencies],
        'file_artifacts': [{'unit_key': d['unit_key'], **d['file_artifacts']}
            for d in dependencies if d['file_artifacts']['state'] == 'CURRENT'],
        'handoff_coordination_context': context}
    c.bounded(value)
    return deepcopy(value)


def review_prompt(frozen):
    context = frozen.get('handoff_coordination_context')
    if not isinstance(context, dict) or context.get('protocol') != c.REVIEW_INPUT:
        raise ValueError('新返工提示只接受准确REWORK_REVIEW冻结输入。')
    c.bounded(frozen)
    return '读取完整冻结返工审阅输入，只返回新schema_version=2的rework_schema或pause_schema严格JSON；不写文件。\n输入快照 JSON：\n' + canonical(frozen)


def child_value(plan, history, capability, proposal, impact, seed, command_id):
    """Pure same-definition child; actual candidate/save ownership is separate."""
    cap = c.validate_capability(capability); c.validate_preview(impact)
    reference(proposal); fields(seed, {'id', 'created'}); c.finite(seed['created'])
    c._uuid(command_id)
    if not isinstance(seed['id'], str) or not seed['id'].startswith('teamwork:'):
        raise ValueError('返工child须为真实待写的新方案身份。')
    c._uuid(seed['id'].removeprefix('teamwork:'))
    body = cap['snapshot']
    if (plan.get(c.LINEAGE_FIELD) or plan != body['observation']['parent_plan_snapshot'] or
            impact['blockers'] or impact['parent_plan'] != plan_ref(plan) or
            impact['capability'] != {k: cap[k] for k in ('id', 'version')} or
            impact['candidate_units'] != plan['units'] or impact['source_frontier'] != history['frontier'] or
            impact['authorization'] != history['authorization'] or impact['usage'] != history['usage'] or
            impact['material_observation'] != history['material_observation'] or
            seed['id'] in {plan['id'], body['root']['id']} or
            not plan['created'] <= seed['created'] < history['authorization']['dispatch_before']):
        raise ValueError('返工child只能来自准确完整可应用预览、原定义和原授权。')
    link = versioned({'protocol': c.LINEAGE, 'root': body['root'], 'parent_plan': plan_ref(plan),
        'capability': {k: cap[k] for k in ('id', 'version')}, 'proposal': proposal,
        'preview': impact['version'], 'next_coordinate': impact['next_coordinate'],
        'affected_keys': impact['affected_keys'],
        'ordinary_sources': body['observation']['ordinary_sources'],
        'source_frontier': impact['source_frontier'], **PERMISSIONS})
    transition = {'kind': 'COORDINATOR_TEXT_REWORK', 'proposal': proposal,
        'source': impact['source_run'], 'impact': {k: impact[k]
            for k in ('direct_changes', 'affected_keys', 'protected_keys')},
        'usage_at_transition': history['usage'],
        'authorization_from': history['authorization'], 'command_id': command_id, 'no_dispatch': True}
    child = {**deepcopy(plan), **deepcopy(seed), 'state': 'PREPARED',
        'item_revision': history['task']['revision'], 'previous_plan': plan['id'],
        'previous_plan_reference': plan_ref(plan), 'result_lineage': deepcopy(impact['carry_sources']),
        # Deliberately reopen this exact same key even if an old ancestor had
        # placed it in history. Every other original coordinate stays history.
        'historical_coordinators': deepcopy(impact['historical_coordinators']),
        'transition': transition, c.LINEAGE_FIELD: link}
    child.pop('version')
    return versioned(child)


def _coordinate_blockers(plan, selected, affected, frames):
    """Check original gates against unaffected actual consumable sources."""
    problems = []
    if selected['role'] != 'coordinate' or selected['member'] != plan['coordinator']:
        return ['NEXT_COORDINATE_NOT_ORIGINAL_COORDINATOR']
    retired_a = plan['file_handoffs'][0]['source_unit']
    for key in selected['depends']:
        if key == retired_a:
            problems.append('NEXT_COORDINATE_RETIRED_A:' + key)
        elif key in affected:
            problems.append('NEXT_COORDINATE_AFFECTED_DEPENDENCY:' + key)
        else:
            frame = frames.get(key)
            evidence = frame['source_evidence'] if frame else None
            if (evidence is None or not evidence['exact_identity'] or evidence['blockers'] or
                    evidence['state'] != 'COMPLETED' or not evidence['result_available']):
                problems.append('NEXT_COORDINATE_UNAVAILABLE_DEPENDENCY:' + key)
            elif selected['dependency_gate'] != 'result':
                try:
                    WorkArtifacts.require(None, evidence, selected['dependency_gate'])
                except ValueError:
                    problems.append('NEXT_COORDINATE_FILE_GATE:' + key)
    return problems


def preview(plan, history, capability, review_run, exit_event, decision, *, observed_at):
    """Construct the full union once; the real owner must recheck before save."""
    cap = c.validate_capability(capability); body = cap['snapshot']; obs = body['observation']
    c.event_ref(exit_event); c.finite(observed_at)
    decision = c.parse_reply(json.dumps(decision, ensure_ascii=False))
    if decision['action'] != 'rework_units':
        raise ValueError('文字返工影响预览须来自新严格rework_units决定。')
    context = review_run['input']['handoff_coordination_context']
    if (plan != obs['parent_plan_snapshot'] or history['phase'] != 'B_COMPLETED' or
            review_run['plan_id'] != plan['id'] or review_run['plan_version'] != plan['version'] or
            context != review_context(cap) or review_run['retry_of'] is not None):
        raise ValueError('文字返工预览须消费准确新冻结回合和原父方案。')
    review_frames = [frame for frame in history['frames'] if frame['run'] == review_run]
    if len(review_frames) != 1:
        raise ValueError('文字返工预览缺少准确完整review运行。')
    review_evidence = review_frames[0]['source_evidence']
    if (not review_evidence['exact_identity'] or review_evidence['blockers'] or
            review_evidence['state'] != 'COMPLETED' or not review_evidence['result_available'] or
            c.parse_reply(review_evidence['answer']) != decision):
        raise ValueError('文字返工预览须等于本次准确已知成功退出的严格原回复。')
    frames = _current_frames(plan, history)
    units = {unit['key']: unit for unit in plan['units']}
    sources = {value['key']: value for value in obs['ordinary_sources']}
    if len(sources) != len(obs['ordinary_sources']):
        raise ValueError('文字返工冻结普通来源不能重复。')
    selected = {change['unit_key'] for change in decision['changes']}
    for change in decision['changes']:
        key = change['unit_key']; frozen = sources.get(key); frame = frames.get(key)
        if (frozen is None or frame is None or not _ordinary(units[key]) or
                change['source_run_id'] != frozen['run']['id'] or change['source_version'] != frozen['evidence']['version'] or
                frame['run'] != frozen['run'] or frame['evidence'] != frozen['evidence'] or
                frame['files'] != frozen['files']):
            raise ValueError('返工changes须绑定新输入的准确普通原来源，晚变须重新明确处理。')
    reasons = {key: (['EXPLICIT_REWORK'] if key in selected else []) for key in units}
    for key, frame in frames.items():
        if key == plan['file_handoffs'][0]['source_unit']:
            continue
        if not frame['source_evidence']['result_available']:
            reasons[key].append('RESULT_UNAVAILABLE')
        if units[key]['outputs']:
            try:
                WorkArtifacts.require(None, frame['source_evidence'], 'checked' if units[key]['checks'] else 'files')
            except ValueError:
                reasons[key].append('ARTIFACT_UNAVAILABLE')
    affected = {key for key, values in reasons.items() if values}
    while True:
        more = {key for key, unit in units.items() if set(unit['depends']) & affected} - affected
        if not more:
            break
        for key in more:
            reasons[key].append('AFFECTED_DEPENDENCY')
        affected.update(more)
    protected = protected_keys(plan)
    blockers = ['PROTECTED_UNIT:' + key for key in sorted(affected & set(protected))]
    blockers += ['NON_TEXT_AFFECTED_UNIT:' + key for key in units
        if key in affected and units[key]['role'] != 'coordinate' and not _ordinary(units[key])]
    next_ref = c.coordinate_ref(decision['next_coordinate'])
    next_unit = units.get(next_ref['key'])
    if next_unit is None or next_ref['revision'] != digest(next_unit):
        blockers.append('NEXT_COORDINATE_DEFINITION_CHANGED')
    else:
        blockers += _coordinate_blockers(plan, next_unit, affected,
            {f['run']['unit_key']: f for f in history['current_frames']})
    if len(history['frames']) + 2 > history['authorization']['max_attempts_total']:
        blockers.append('ORIGINAL_ATTEMPT_LIMIT')
    if observed_at >= history['authorization']['dispatch_before']:
        blockers.append('ORIGINAL_DISPATCH_DEADLINE')
    carry = {}; entries = []
    retired_a = plan['file_handoffs'][0]['source_unit']
    for key, unit in units.items():
        frame = frames.get(key); evidence = frame['source_evidence'] if frame else None
        state = ('HISTORICAL_COORDINATOR' if unit['role'] == 'coordinate' else
            'AFFECTED' if key in affected else 'HISTORICAL_RETIRED' if key == retired_a else
            'ELIGIBLE_RESULT' if evidence and evidence['state'] == 'COMPLETED' and evidence['result_available'] else 'NOT_RUN')
        if state == 'ELIGIBLE_RESULT':
            carry[key] = source_ref(frame['run'], evidence)
        entries.append({'key': key, 'definition_revision': digest(unit), 'state': state,
            'reasons': reasons[key], 'source': deepcopy(evidence),
            'original_input': deepcopy(frame['run']['input']) if frame else None,
            'original_evidence': deepcopy(evidence), 'files': deepcopy(frame['files']) if frame else None})
    return c.validate_preview(versioned({'protocol': c.PREVIEW, 'root': body['root'], 'parent_plan': plan_ref(plan),
        'capability': {k: cap[k] for k in ('id', 'version')},
        'source_run': {'id': review_run['id'], 'version': digest(review_run)}, 'source_exit_event': exit_event,
        'decision': decision, 'direct_changes': sorted(selected), 'affected_keys': [key for key in units if key in affected],
        'protected_keys': protected, 'candidate_units': plan['units'], 'next_coordinate': next_ref,
        'historical_coordinators': [key for key, unit in units.items() if unit['role'] == 'coordinate' and key != next_ref['key']],
        'carry_sources': carry, 'source_frontier': history['frontier'],
        'material_observation': history['material_observation'], 'authorization': history['authorization'],
        'usage': history['usage'], 'entries': entries, 'blockers': blockers,
        'effect': 'PREVIEW_ONLY; NO_PLAN_CHANGE_OR_DISPATCH', **PERMISSIONS}))
