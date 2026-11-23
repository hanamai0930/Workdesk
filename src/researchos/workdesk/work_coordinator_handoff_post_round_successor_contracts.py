"""Closed successor data contracts; actual authority belongs to the runtime.

These codecs never reinterpret the retained ROUND lineage as a new ROUND edge.
The admission draft and all value permissions remain read-only.
"""
from copy import deepcopy
import json
import uuid

from .store import canonical, digest
from .work_transitions import plan_ref
from .work_coordinator_handoff_records import (
    fields, reference, versioned, PERMISSIONS, observation, task_revision, sha)
from .work_coordinator_handoff_adjustment_records import hashed, permissions, finite, usage
from .work_coordinator_handoff_adjustment_contracts import closed
from .work_coordinator_handoff_mixed_records import selected
from .work_coordinator_handoff_round_records import event_ref
from .work_coordinator_handoff_adjustment_records import complete_frontier
from .work_coordinator_handoff_post_round_saved_records import (
    CHECKPOINT, CANDIDATE, APPLICATION, REGISTRATION, SAVE_EVENT, EVENTS,
    PAUSE_REASON, successor_driver, transferred_authorization)
from .work_coordinator_handoff_post_round_candidates import LINEAGE_FIELD, LINEAGE
from .work_coordinator_handoff_roots import CONFIG_FIELDS, MARKER_FIELD

OBSERVATION = 'COORDINATOR_FILE_HANDOFF_POST_ROUND_SUCCESSOR_OBSERVATION_V1'
DRAFT = 'COORDINATOR_FILE_HANDOFF_POST_ROUND_SUCCESSOR_DRAFT_V1'
CAPABILITY = 'COORDINATOR_FILE_HANDOFF_POST_ROUND_SUCCESSOR_CAPABILITY_V1'
KIND = 'work_coordinator_handoff_post_round_successor_capability'
OPENED = 'coordinator_post_round_successor_opened'
PROTOCOL = 'COORDINATOR_FILE_HANDOFF_COORDINATION_INPUT_V7'
UNIT = 'COORDINATOR_FILE_HANDOFF_UNIT_INPUT_V6'
PATH = 'COORDINATOR_FILE_HANDOFF_POST_ROUND_SUCCESSOR_PATH_V1'
OBS_SCOPE = 'CURRENT_SAVED_POST_ROUND_ADMISSION; READ_ONLY; NO_ACTIVATION'
SCOPE = 'SAVED_POST_ROUND_SUCCESSOR; DISPATCH_PAUSE_ADJUSTMENT_PREVIEW; SUBSEQUENT_SAVE_REQUIRES_TYPED_RECONSTRUCTION'
ACTIONS = ['dispatch', 'pause', 'adjust_dependencies']
MAX_PATH_CHARACTERS = 160000
REQUEST_FIELDS = {'id', 'revision', 'current_driver', 'plan', 'authorization', 'frontier', 'unit', 'retry_of', 'proposal'}


def record_ref(value, protocol):
    from .work_coordinator_handoff_post_round_saved_records import KIND_FOR
    reference(value)
    if value['id'] != KIND_FOR[protocol] + ':' + value['version']:
        raise ValueError('后继边引用须属于准确POST_ROUND保存记录。')


def bounded(value):
    if len(canonical(value)) > MAX_PATH_CHARACTERS:
        raise ValueError('后继完整路径超过既有160000字符上限。')


@closed
def saved_path(value):
    """Decode only the installed first post-round save, with its real edge type."""
    from .work_coordinator_handoff_post_round_saved_tree import PATH as SAVED_PATH
    from .work_coordinator_handoff_post_round_contracts import validate_path as parent_path
    fields(value, {'protocol', 'root', 'plan', 'parent_path', 'edge', 'history_frontier',
        'driver', 'authorization', 'material_observation', 'version', *PERMISSIONS})
    hashed(value); permissions(value); bounded(value)
    if value['protocol'] != SAVED_PATH:
        raise ValueError('后继准入必须使用真实POST_ROUND保存路径。')
    parent = parent_path(value['parent_path'])
    for k in ('root', 'plan'): reference(value[k])
    edge(value['edge'], parent['plan'], value['plan'])
    complete_frontier(value['history_frontier']); observation(value['material_observation'])
    d = successor_driver(value['driver']); a = transferred_authorization(value['authorization'])
    if (value['root'] != parent['root'] or value['history_frontier'] != parent['history_frontier'] or
            value['plan'] in (parent['plan'], value['root']) or
            d['previous_plan_reference'] != parent['plan'] or d['transition_registration'] != value['edge']['candidate'] or
            a['transfer_registration'] != value['edge']['candidate'] or
            a['previous_authorization'] != {k: parent['authorization'][k] for k in ('id', 'revision')} or
            any(x['plan_id'] != value['plan']['id'] or x['plan_version'] != value['plan']['version'] for x in (d, a)) or
            d['generation'] != a['driver_generation'] or
            any(a[k] != parent['authorization'][k] for k in ('id', 'driver_generation', 'max_attempts_total', 'dispatch_before')) or
            value['material_observation']['root'] != parent['material_observation']['root'] or
            value['material_observation']['paths'] != parent['material_observation']['paths']):
        raise ValueError('后继保存路径的真实父、边、前沿或原授权沿袭不符。')
    return deepcopy(value)


def edge(value, parent, child):
    fields(value, {'parent_plan', 'child_plan', 'checkpoint', 'candidate', 'application',
        'registration', 'save_event', 'registration_event'})
    for k in ('parent_plan', 'child_plan'): reference(value[k])
    for k, p in (('checkpoint', CHECKPOINT), ('candidate', CANDIDATE),
            ('application', APPLICATION), ('registration', REGISTRATION)):
        record_ref(value[k], p)
    event_ref(value['save_event'], SAVE_EVENT); event_ref(value['registration_event'], EVENTS[REGISTRATION])
    if (value['parent_plan'] != parent or value['child_plan'] != child or
            value['save_event']['seq'] >= value['registration_event']['seq']):
        raise ValueError('后继路径须绑定准确POST_ROUND保存边及登记事件。')


@closed
def validate_observation(value):
    fields(value, {'protocol', 'root', 'plan', 'plan_snapshot', 'task', 'driver', 'authorization',
        'saved_path', 'runtime_capability', 'adjustment_capability', 'round_capability',
        'post_round_capability', 'phase', 'frontier', 'material_observation', 'selected_unit',
        'qualifications', 'dependencies', 'usage', 'protected_keys', 'configuration_version',
        'observed_at', 'scope', 'version', *PERMISSIONS})
    hashed(value); permissions(value)
    if value['protocol'] != OBSERVATION or value['scope'] != OBS_SCOPE:
        raise ValueError('后继观察须使用独立只读协议。')
    path = saved_path(value['saved_path']); plan = value['plan_snapshot']; hashed(plan)
    d = successor_driver(value['driver']); a = transferred_authorization(value['authorization'])
    link = plan[LINEAGE_FIELD]; fields(link, {'protocol', 'root', 'parent_plan', 'checkpoint', 'proposal'})
    for k in ('root', 'plan', 'runtime_capability', 'round_capability', 'post_round_capability'): reference(value[k])
    if value['adjustment_capability'] is not None: reference(value['adjustment_capability'])
    for k in ('root', 'plan', 'driver', 'authorization', 'material_observation'):
        if value[k] != path[k]: raise ValueError('后继观察与真实保存路径不符：' + k)
    parent = path['parent_path']
    for k in ('runtime_capability', 'adjustment_capability', 'round_capability', 'post_round_capability'):
        if value[k] != parent[k]: raise ValueError('后继观察遗漏原实际能力。')
    fields(value['task'], {'id', 'revision'}); task_revision(value['task']['revision'])
    complete_frontier(value['frontier']); usage(value['usage']); finite(value['observed_at'])
    selected(value['selected_unit']); unit = value['selected_unit']['definition']
    from .work_coordinator_handoff_adjustments import protected
    from .work_coordinator_handoff_adjustment_records import qualifications as validate_qualifications
    validate_qualifications(value['qualifications'])
    if (link['protocol'] != LINEAGE or link['root'] != value['root'] or link['parent_plan'] != path['edge']['parent_plan'] or
            link['checkpoint'] != path['edge']['checkpoint'] or plan_ref(plan) != value['plan'] or
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
    return deepcopy(value)


def observation_ref(value):
    return {k: deepcopy(value[k]) for k in ('protocol', 'version', 'root', 'plan')}


def capability_draft(observation):
    obs = validate_observation(observation)
    return versioned({'protocol': DRAFT, 'planned_capability': CAPABILITY,
        'coordination_protocol': PROTOCOL, 'unit_protocol': UNIT, 'activation_observation': observation_ref(obs),
        'root': obs['root'], 'plan': obs['plan'], 'driver': obs['driver'], 'authorization': obs['authorization'],
        'selected_unit': obs['selected_unit'], 'activation_state': 'DRAFT_ONLY',
        'allowed_actions': ACTIONS, 'scope': OBS_SCOPE, **PERMISSIONS})


@closed
def validate_draft(value, observation):
    if value != capability_draft(observation): raise ValueError('后继草案必须逐值绑定实际只读观察。')
    return deepcopy(value)


@closed
def validate_capability(row):
    fields(row, {'id', 'version', 'snapshot', 'item_id', 'created', 'command_id'})
    body = row['snapshot']; fields(body, {'protocol', 'observation', 'coordination_protocol', 'unit_protocol',
        'allowed_actions', 'request', 'request_hash', 'scope', 'version', *PERMISSIONS})
    hashed(body); permissions(body); obs = validate_observation(body['observation'])
    finite(row['created']); uuid.UUID(row['command_id']); sha(body['request_hash'])
    p = body['request']; fields(p, REQUEST_FIELDS)
    if (body['protocol'] != CAPABILITY or body['scope'] != SCOPE or body['coordination_protocol'] != PROTOCOL or
            body['unit_protocol'] != UNIT or body['allowed_actions'] != ACTIONS or
            row['id'] != KIND + ':' + body['version'] or row['version'] != body['version'] or row['item_id'] != obs['task']['id'] or
            not obs['observed_at'] <= row['created'] < obs['authorization']['dispatch_before'] or
            digest(p) != body['request_hash'] or p['id'] != obs['task']['id'] or p['revision'] != obs['task']['revision'] or
            p['current_driver'] != {k: obs['driver'][k] for k in ('id', 'generation', 'revision')} or
            p['authorization'] != {k: obs['authorization'][k] for k in ('id', 'revision')} or p['plan'] != obs['plan'] or
            p['unit'] != {'key': obs['selected_unit']['definition']['key'], 'revision': obs['selected_unit']['revision']} or
            p['frontier'] != digest([{'run_id': f['run_id'], 'evidence': f['evidence']['version']} for f in obs['frontier']]) or
            p['retry_of'] is not None or p['proposal'] is not None):
        raise ValueError('后继能力须绑定完整首次派发请求、真实观察与原截止。')
    return deepcopy(row)


@closed
def validate_path(value):
    """One full typed parent path; no second embedded capability observation."""
    fields(value, {'protocol', 'root', 'plan', 'parent_kind', 'parent_path', 'edge',
        'history_frontier', 'current_qualifications', 'material_observation', 'driver_history',
        'driver', 'authorization', 'runtime_capability', 'adjustment_capability', 'round_capability',
        'post_round_capability', 'successor_capability', 'activation_observation', 'scope', 'version', *PERMISSIONS})
    hashed(value); permissions(value); bounded(value)
    from .work_coordinator_handoff_post_round_contracts import validate_path as parent_path
    parent = parent_path(value['parent_path'])
    if value['protocol'] != PATH or value['scope'] != SCOPE or value['parent_kind'] != 'POST_ROUND_ADJUSTMENT':
        raise ValueError('后继完整路径须按POST_ROUND实际保存类型编码。')
    for k in ('root', 'plan', 'runtime_capability', 'round_capability', 'post_round_capability', 'successor_capability'):
        reference(value[k])
    if value['adjustment_capability'] is not None: reference(value['adjustment_capability'])
    edge(value['edge'], parent['plan'], value['plan'])
    complete_frontier(value['history_frontier']); observation(value['material_observation'])
    from .work_coordinator_handoff_adjustment_records import qualifications
    qualifications(value['current_qualifications'])
    d = successor_driver(value['driver']); a = transferred_authorization(value['authorization'])
    obs = value['activation_observation']; fields(obs, {'protocol', 'version', 'root', 'plan'}); sha(obs['version'])
    for k in ('root', 'plan'): reference(obs[k])
    if (value['root'] != parent['root'] or value['plan'] in (value['root'], parent['plan']) or
            obs['protocol'] != OBSERVATION or obs['root'] != value['root'] or obs['plan'] != value['plan'] or
            value['successor_capability']['id'] != KIND + ':' + value['successor_capability']['version'] or
            d['generation'] != a['driver_generation'] or d['transition_registration'] != value['edge']['candidate'] or
            d['previous_plan_reference'] != parent['plan'] or a['transfer_registration'] != value['edge']['candidate'] or
            any(x['plan_id'] != value['plan']['id'] or x['plan_version'] != value['plan']['version'] for x in (d, a)) or
            a['previous_authorization'] != {k: parent['authorization'][k] for k in ('id', 'revision')} or
            any(a[k] != parent['authorization'][k] for k in ('id', 'driver_generation', 'max_attempts_total', 'dispatch_before')) or
            any(value[k] != parent[k] for k in ('runtime_capability', 'adjustment_capability', 'round_capability', 'post_round_capability')) or
            value['history_frontier'][:len(parent['history_frontier'])] != parent['history_frontier'] or
            len(value['history_frontier']) > a['max_attempts_total'] or
            any(value['material_observation'][k] != parent['material_observation'][k] for k in ('root', 'paths'))):
        raise ValueError('后继路径的完整原父、累计前沿、原授权或能力不符。')
    versions = value['driver_history']
    if not isinstance(versions, list) or not versions: raise ValueError('后继路径须有实际责任历史。')
    previous = 0
    from .work_coordinator_handoff_post_round_saved_records import DRIVER_EVENT
    for entry in versions:
        fields(entry, {'event', 'driver'}); event_ref(entry['event']); successor_driver(entry['driver'])
        v = entry['driver']; seq = entry['event']['seq']
        if (seq <= previous or v['plan_id'] != value['plan']['id'] or v['plan_version'] != value['plan']['version'] or
                v['transition_registration'] != value['edge']['candidate'] or v['generation'] != d['generation']):
            raise ValueError('后继责任历史顺序或来源不符。')
        previous = seq
    if (versions[-1]['driver'] != d or versions[0]['driver']['state'] != 'PAUSED' or
            versions[0]['driver']['reason'] != PAUSE_REASON or versions[0]['event']['kind'] != DRIVER_EVENT or
            versions[0]['event']['seq'] >= value['edge']['save_event']['seq']):
        raise ValueError('后继责任必须保留真实保存时首次暂停。')
    return deepcopy(value)


@closed
def validate_context(value, capability=None):
    coordinate = value['protocol'] == PROTOCOL
    fields(value, {'protocol', 'root', 'marker', 'plan', 'task', 'driver', 'authorization', 'phase',
        'prior_frontier', 'material_observation', 'saved_path', 'runtime_capability', 'adjustment_capability',
        'round_capability', 'post_round_capability', 'successor_capability', 'scope', 'version', *PERMISSIONS} |
        ({'work_requirements', 'configuration', 'units', 'allowed_actions'} if coordinate else {'unit_revision', 'proposal'}))
    hashed(value); permissions(value); path = validate_path(value['saved_path'])
    fields(value['task'], {'id', 'revision'}); task_revision(value['task']['revision'])
    if (value['protocol'] not in (PROTOCOL, UNIT) or value['scope'] != SCOPE or
            any(value[k] != path[k] for k in ('root', 'plan', 'driver', 'authorization', 'material_observation',
                'runtime_capability', 'adjustment_capability', 'round_capability', 'post_round_capability', 'successor_capability')) or
            value['prior_frontier'] != path['history_frontier'] or
            value['task'] != {'id': value['driver']['item_id'], 'revision': value['driver']['item_revision']} or
            value['marker']['plan_id'] != path['root']['id'] or
            value['marker']['project_root'] != path['material_observation']['root'] or
            value['phase'] not in {'BEFORE_A', 'A_CAPTURED', 'B_COMPLETED'}):
        raise ValueError('V7/普通V6须逐值绑定后继完整路径、任务、责任和材料。')
    if coordinate:
        fields(value['configuration'], CONFIG_FIELDS)
        if (value['allowed_actions'] != ACTIONS or not isinstance(value['units'], list) or
                len(value['units']) != len(path['current_qualifications'])):
            raise ValueError('V7须保留全部原声明单元和准确已开放操作。')
        for entry, q in zip(value['units'], path['current_qualifications']):
            fields(entry, {'definition', 'state', 'source_run', 'qualification'})
            if entry['definition']['key'] != q['key'] or entry['qualification'] != q['qualification']:
                raise ValueError('V7单元资格与后继路径不符。')
    else:
        sha(value['unit_revision']); fields(value['proposal'], {'reference', 'source', 'decision'})
        reference(value['proposal']['reference'])
        from .work_coordinator_handoff_adjustment_records import source
        from .work_proposals import parse
        source(value['proposal']['source']); decision = value['proposal']['decision']
        if parse(json.dumps(decision)) != decision or decision['action'] != 'dispatch' or decision['retry_of'] is not None:
            raise ValueError('普通V6须消费准确派发提案。')
    if capability is not None:
        cap = validate_capability(capability); obs = cap['snapshot']['observation']; plan = obs['plan_snapshot']
        if (value['successor_capability'] != {k: cap[k] for k in ('id', 'version')} or
                path['activation_observation'] != observation_ref(obs) or value['authorization'] != obs['authorization'] or
                value['root'] != obs['root'] or value['plan'] != obs['plan'] or value['marker'] != plan[MARKER_FIELD] or
                path['parent_path'] != obs['saved_path']['parent_path'] or path['edge'] != obs['saved_path']['edge'] or
                path['history_frontier'][:len(obs['frontier'])] != obs['frontier']):
            raise ValueError('后继输入与真实首次开启观察不符。')
        if coordinate and (value['work_requirements'] != plan['work_requirements'] or
                value['configuration'] != {k: plan[k] for k in CONFIG_FIELDS} or
                [u['definition'] for u in value['units']] != plan['units']):
            raise ValueError('V7原要求、配置或单元定义与开启方案不符。')
    return deepcopy(value)
