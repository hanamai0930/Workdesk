"""Closed V2-saved successor admission values; no activation or input authority.

V8 coordination/V7 ordinary names are reserved here for the independent runtime.
An observation or draft is never a persisted capability or frozen run input.
"""
from copy import deepcopy
from .store import digest
from .work_transitions import plan_ref
from .work_coordinator_handoff_records import fields, reference, versioned, PERMISSIONS, task_revision
from .work_coordinator_handoff_adjustment_records import hashed, permissions, finite, usage, complete_frontier
from .work_coordinator_handoff_adjustment_contracts import closed
from .work_coordinator_handoff_mixed_records import selected
from .work_coordinator_handoff_post_round_saved_records import successor_driver, transferred_authorization
from .work_coordinator_handoff_post_round_candidates import LINEAGE_FIELD
from .work_coordinator_handoff_post_round_v2_candidates import LINEAGE
from .work_coordinator_handoff_post_round_v2_path import validate_path as saved_path
from .work_coordinator_handoff_post_round_successor_contracts import bounded
from .work_coordinator_handoff_roots import CONFIG_FIELDS, MARKER_FIELD

OBSERVATION = 'COORDINATOR_FILE_HANDOFF_POST_ROUND_SUCCESSOR_OBSERVATION_V2'
DRAFT = 'COORDINATOR_FILE_HANDOFF_POST_ROUND_SUCCESSOR_DRAFT_V2'
CAPABILITY = 'COORDINATOR_FILE_HANDOFF_POST_ROUND_SUCCESSOR_CAPABILITY_V2'
PROTOCOL = 'COORDINATOR_FILE_HANDOFF_COORDINATION_INPUT_V8'
UNIT = 'COORDINATOR_FILE_HANDOFF_UNIT_INPUT_V7'
OBS_SCOPE = 'CURRENT_ORDERED_SAVED_V2_ADMISSION; READ_ONLY; NO_ACTIVATION_OR_FROZEN_INPUT'
ACTIONS = ['dispatch', 'pause', 'adjust_dependencies']


@closed
def validate_observation(value):
    fields(value, {'protocol', 'root', 'plan', 'plan_snapshot', 'task', 'driver', 'authorization',
        'saved_path', 'phase', 'frontier', 'material_observation', 'selected_unit',
        'qualifications', 'dependencies', 'usage', 'protected_keys', 'configuration_version',
        'observed_at', 'scope', 'version', *PERMISSIONS})
    hashed(value); permissions(value)
    if value['protocol'] != OBSERVATION or value['scope'] != OBS_SCOPE:
        raise ValueError('后继观察须使用独立只读协议。')
    path = saved_path(value['saved_path']); plan = value['plan_snapshot']; hashed(plan)
    d = successor_driver(value['driver']); a = transferred_authorization(value['authorization'])
    link = plan[LINEAGE_FIELD]; fields(link, {'protocol', 'root', 'parent_plan', 'checkpoint', 'proposal'})
    for k in ('root', 'plan'): reference(value[k])
    for k in ('root', 'plan', 'driver', 'authorization', 'material_observation'):
        if value[k] != path[k]: raise ValueError('后继观察与真实保存路径不符：' + k)
    edge = path['ancestors'][-1]['edge']
    fields(value['task'], {'id', 'revision'}); task_revision(value['task']['revision'])
    complete_frontier(value['frontier']); usage(value['usage']); finite(value['observed_at'])
    selected(value['selected_unit']); unit = value['selected_unit']['definition']
    from .work_coordinator_handoff_adjustments import protected
    from .work_coordinator_handoff_adjustment_records import qualifications as validate_qualifications
    validate_qualifications(value['qualifications'])
    if (link['protocol'] != LINEAGE or link['root'] != value['root'] or link['parent_plan'] != edge['parent_plan'] or
            link['checkpoint'] != edge['checkpoint'] or plan_ref(plan) != value['plan'] or
            plan[MARKER_FIELD]['plan_id'] != value['root']['id'] or
            value['task'] != {'id': plan['item_id'], 'revision': d['item_revision']} or
            d['state'] != 'RESERVED' or d['item_id'] != plan['item_id'] or
            value['phase'] not in {'BEFORE_A', 'A_CAPTURED', 'B_COMPLETED'} or
            value['frontier'] != path['history_frontier'] or value['usage']['attempts'] != len(value['frontier']) or
            value['usage']['attempts'] + 3 > a['max_attempts_total'] or
            not d['updated'] <= value['observed_at'] < a['dispatch_before'] or
            value['configuration_version'] != digest({k: plan[k] for k in CONFIG_FIELDS}) or
            value['protected_keys'] != sorted(protected(plan['units'], plan['file_handoffs'][0])) or
            unit not in plan['units'] or unit['role'] != 'coordinate' or unit['key'] in plan['historical_coordinators'] or
            unit['member']['reference'] != plan['coordinator']['reference'] or
            [q['key'] for q in value['qualifications']] != [u['key'] for u in plan['units']]):
        raise ValueError('后继观察的完整方案、原授权余量、阶段或所选协调单元不符。')
    qmap = {q['key']: q for q in value['qualifications']}; q = qmap[unit['key']]
    if q['qualification'] != 'NOT_RUN' or q['source'] is not None:
        raise ValueError('后继首次派发须选择未运行的原声明coordinate。')
    by_run = {f['run_id']: f for f in value['frontier']}
    for q in qmap.values():
        if q['source'] is not None and q['source'] != by_run[q['source']['run_id']]['evidence']:
            raise ValueError('后继当前资格须绑定完整冻结前沿。')
    # First activation has no child runs: only the exact saved carry sources
    # can qualify. A self-consistent hash must not invent another ordinary result.
    from .work_coordinator_handoff_adjustments import qualifications
    carried = [{'run': {'unit_key': k}, 'evidence': by_run[v['run_id']]['evidence']}
        for k, v in plan['result_lineage'].items()]
    if qualifications(plan, value['phase'], carried) != value['qualifications']:
        raise ValueError('后继首次资格只能来自准确保存沿用来源。')
    dependencies = value['dependencies']
    if not isinstance(dependencies, list) or [d['unit_key'] for d in dependencies] != unit['depends']:
        raise ValueError('后继所选coordinate的准确依赖不完整。')
    from .work_coordinator_handoff_post_round_context import _full_evidence
    for dep in dependencies:
        key = dep['unit_key']; source = qmap[key]['source']; full = {k: v for k, v in dep.items() if k != 'unit_key'}
        if source is None or qmap[key]['qualification'] != 'ELIGIBLE_RESULT':
            raise ValueError('后继依赖缺失或已经退役。')
        _full_evidence(full, source)
        files = full['file_artifacts']; gate = unit['dependency_gate']
        if (not full['result_available'] or (gate in {'files', 'checked'} and
                (not files['required_outputs'] or files['state'] != 'CURRENT')) or
                (gate == 'checked' and files['check_status'] != 'PASS')):
            raise ValueError('后继依赖的原结果、文件或检查不可用。')
    bounded(value)
    return deepcopy(value)


def capability_draft(observation):
    obs = validate_observation(observation)
    return versioned({'protocol': DRAFT, 'planned_capability': CAPABILITY,
        'coordination_protocol': PROTOCOL, 'unit_protocol': UNIT,
        'activation_observation': {k: deepcopy(obs[k]) for k in ('protocol', 'version', 'root', 'plan')},
        'root': obs['root'], 'plan': obs['plan'], 'driver': obs['driver'], 'authorization': obs['authorization'],
        'selected_unit': obs['selected_unit'], 'activation_state': 'DRAFT_ONLY',
        'allowed_actions': ACTIONS, 'scope': OBS_SCOPE, **PERMISSIONS})


@closed
def validate_draft(value, observation):
    if value != capability_draft(observation): raise ValueError('V2后继草案须逐值绑定只读观察。')
    return deepcopy(value)
