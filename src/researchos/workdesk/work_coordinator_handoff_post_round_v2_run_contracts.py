"""Independent V2 successor value contracts, without activation authority.

The ordered path holds each saved edge and the frontier once. A valid value is
not evidence of an actual command, capability event, reservation or frozen task.
The runtime reader must independently reconstruct those durable facts.
"""
from copy import deepcopy
import json
import uuid

from .store import digest
from .work_coordinator_handoff_records import fields, reference, versioned, PERMISSIONS, sha, task_revision
from .work_coordinator_handoff_adjustment_records import hashed, permissions, finite, complete_frontier, qualifications
from .work_coordinator_handoff_adjustment_contracts import closed
from .work_coordinator_handoff_mixed_records import selected
from .work_coordinator_handoff_round_records import event_ref
from .work_coordinator_handoff_post_round_successor_contracts import bounded, REQUEST_FIELDS
from .work_coordinator_handoff_post_round_v2_path import validate_path as saved_path, PATH as SAVED_PATH, SCOPE as SAVED_SCOPE
from .work_coordinator_handoff_post_round_v2_saved_records import successor_driver, transferred_authorization, DRIVER_EVENT, PAUSE_REASON
from .work_coordinator_handoff_post_round_v2_successor_contracts import (
    OBSERVATION, CAPABILITY, PROTOCOL, UNIT, ACTIONS, validate_observation)
from .work_coordinator_handoff_roots import CONFIG_FIELDS, MARKER_FIELD

KIND = 'work_coordinator_handoff_post_round_successor_capability_v2'
OPENED = 'coordinator_post_round_successor_opened_v2'
PATH = 'COORDINATOR_FILE_HANDOFF_POST_ROUND_SUCCESSOR_PATH_V2'
SCOPE = 'ORDERED_SAVED_V2_SUCCESSOR; DISPATCH_PAUSE_ADJUSTMENT_PREVIEW; DEEPER_SAVE_REQUIRES_TYPED_RECONSTRUCTION'


def observation_ref(value):
    return {k: deepcopy(value[k]) for k in ('protocol', 'version', 'root', 'plan')}


def frontier_reference(path):
    return {**observation_ref(path), 'field': 'history_frontier'}


@closed
def validate_capability(row):
    fields(row, {'id', 'version', 'snapshot', 'item_id', 'created', 'command_id'})
    body = row['snapshot']
    fields(body, {'protocol', 'observation', 'coordination_protocol', 'unit_protocol',
        'allowed_actions', 'request', 'request_hash', 'scope', 'version', *PERMISSIONS})
    hashed(body); permissions(body); bounded(body)
    obs = validate_observation(body['observation'])
    finite(row['created']); uuid.UUID(row['command_id']); sha(body['request_hash'])
    request = body['request']; fields(request, REQUEST_FIELDS)
    expected = {'id': obs['task']['id'], 'revision': obs['task']['revision'],
        'current_driver': {k: obs['driver'][k] for k in ('id', 'generation', 'revision')},
        'authorization': {k: obs['authorization'][k] for k in ('id', 'revision')}, 'plan': obs['plan'],
        'unit': {'key': obs['selected_unit']['definition']['key'], 'revision': obs['selected_unit']['revision']},
        'frontier': digest([{'run_id': f['run_id'], 'evidence': f['evidence']['version']} for f in obs['frontier']]),
        'retry_of': None, 'proposal': None}
    if (body['protocol'] != CAPABILITY or body['scope'] != SCOPE or
            body['coordination_protocol'] != PROTOCOL or body['unit_protocol'] != UNIT or
            body['allowed_actions'] != ACTIONS or row['id'] != KIND + ':' + body['version'] or
            row['version'] != body['version'] or row['item_id'] != obs['task']['id'] or
            not obs['observed_at'] <= row['created'] < obs['authorization']['dispatch_before'] or
            request != expected or digest(request) != body['request_hash']):
        raise ValueError('V2后继能力须绑定准确九字段首次请求、完整观察和原截止。')
    return deepcopy(row)


@closed
def validate_path(value, capability=None):
    fields(value, {'protocol', 'root', 'plan', 'ancestors', 'history_frontier',
        'current_qualifications', 'material_observation', 'driver_history', 'driver', 'authorization',
        'successor_capability', 'activation_observation', 'scope', 'version', *PERMISSIONS})
    hashed(value); permissions(value); bounded(value)
    if value['protocol'] != PATH or value['scope'] != SCOPE:
        raise ValueError('V2后继运行路径须使用独立有序合同。')
    # Reuse the unchanged saved-edge codec on a newly constructed closed value;
    # never upgrade or mutate the persisted saved path into an input permit.
    saved_path(versioned({'protocol': SAVED_PATH, 'scope': SAVED_SCOPE,
        **{k: deepcopy(value[k]) for k in ('root', 'plan', 'ancestors', 'history_frontier',
            'driver', 'authorization', 'material_observation')}, **PERMISSIONS}))
    qualifications(value['current_qualifications'])
    driver = successor_driver(value['driver']); auth = transferred_authorization(value['authorization'])
    reference(value['successor_capability'])
    if (value['successor_capability']['id'] != KIND + ':' + value['successor_capability']['version'] or
            type(auth['max_attempts_total']) is not int or auth['max_attempts_total'] < 1 or
            len(value['history_frontier']) > auth['max_attempts_total'] or driver['item_id'] != auth['item_id']):
        raise ValueError('V2后继路径的独立能力或原累计上限不符。')
    ref = value['activation_observation']; fields(ref, {'protocol', 'version', 'root', 'plan'})
    sha(ref['version']); reference(ref['root']); reference(ref['plan'])
    if ref['protocol'] != OBSERVATION or any(ref[k] != value[k] for k in ('root', 'plan')):
        raise ValueError('V2后继路径缺少准确首次观察引用。')
    edge = value['ancestors'][-1]['edge']; history = value['driver_history']
    if not isinstance(history, list) or not history: raise ValueError('V2后继路径须保留完整实际责任前缀。')
    seq = -1; revisions = set()
    for entry in history:
        fields(entry, {'event', 'driver'}); event_ref(entry['event'])
        v = successor_driver(entry['driver']); event = entry['event']
        if (event['seq'] <= seq or v['revision'] in revisions or
                any(v[k] != driver[k] for k in ('id', 'item_id', 'generation', 'plan_id', 'plan_version',
                    'previous_plan_reference', 'transition_registration'))):
            raise ValueError('V2后继责任前缀顺序、重复修订或来源不符。')
        seq = event['seq']; revisions.add(v['revision'])
    start = history[0]
    if (history[-1]['driver'] != driver or start['driver']['state'] != 'PAUSED' or
            start['driver']['reason'] != PAUSE_REASON or start['event']['kind'] != DRIVER_EVENT or
            start['event']['seq'] >= edge['save_event']['seq'] or
            any(e['event']['seq'] <= edge['registration_event']['seq'] for e in history[1:])):
        raise ValueError('V2后继责任须从准确V2保存暂停版本明确恢复。')
    by_run = {f['run_id']: f['evidence'] for f in value['history_frontier']}
    for q in value['current_qualifications']:
        if q['source'] is not None and by_run.get(q['source']['run_id']) != q['source']:
            raise ValueError('V2后继当前资格来源须逐值属于完整前沿。')
    if capability is not None:
        cap = validate_capability(capability); obs = cap['snapshot']['observation']; base = obs['saved_path']
        if (value['successor_capability'] != {k: cap[k] for k in ('id', 'version')} or
                ref != observation_ref(obs) or value['ancestors'] != base['ancestors'] or
                any(value[k] != obs[k] for k in ('root', 'plan', 'authorization')) or
                value['history_frontier'][:len(obs['frontier'])] != obs['frontier'] or
                not any(e['driver'] == obs['driver'] for e in history) or
                any(value['material_observation'][k] != obs['material_observation'][k] for k in ('root', 'paths')) or
                [q['key'] for q in value['current_qualifications']] != [u['key'] for u in obs['plan_snapshot']['units']] or
                (value['history_frontier'] == obs['frontier'] and value['current_qualifications'] != obs['qualifications'])):
            raise ValueError('V2后继完整有序祖先、原授权、观察或保留前沿不符。')
    return deepcopy(value)


@closed
def validate_context(value, capability):
    coordinate = value['protocol'] == PROTOCOL
    fields(value, {'protocol', 'root', 'marker', 'plan', 'task', 'driver', 'authorization', 'phase',
        'prior_frontier_reference', 'material_observation', 'saved_path', 'successor_capability', 'selected_unit',
        'scope', 'version', *PERMISSIONS} | ({'work_requirements', 'configuration', 'units', 'allowed_actions'}
        if coordinate else {'unit_revision', 'proposal'}))
    hashed(value); permissions(value); bounded(value)
    cap = validate_capability(capability); obs = cap['snapshot']['observation']; plan = obs['plan_snapshot']
    path = validate_path(value['saved_path'], cap)
    fields(value['task'], {'id', 'revision'}); task_revision(value['task']['revision'])
    fields(value['selected_unit'], {'definition', 'revision'}); sha(value['selected_unit']['revision'])
    unit = value['selected_unit']['definition']
    if value['selected_unit']['revision'] != digest(unit): raise ValueError('V2所选单元完整修订不符。')
    if coordinate: selected(value['selected_unit'])
    if (value['protocol'] not in (PROTOCOL, UNIT) or value['scope'] != SCOPE or
            any(value[k] != path[k] for k in ('root', 'plan', 'driver', 'authorization',
                'material_observation', 'successor_capability')) or
            value['prior_frontier_reference'] != frontier_reference(path) or
            value['task'] != {'id': value['driver']['item_id'], 'revision': value['driver']['item_revision']} or
            value['driver']['state'] != 'RESERVED' or value['driver']['updated'] >= value['authorization']['dispatch_before'] or
            value['marker'] != plan[MARKER_FIELD] or value['phase'] not in {'BEFORE_A', 'A_CAPTURED', 'B_COMPLETED'} or
            unit not in plan['units'] or (unit['role'] == 'coordinate') != coordinate):
        raise ValueError('V8/普通V7须绑定完整V2路径、原声明单元、任务、责任与材料。')
    qmap = {q['key']: q for q in path['current_qualifications']}
    if coordinate:
        if (unit['key'] in plan['historical_coordinators'] or qmap[unit['key']]['qualification'] != 'NOT_RUN' or
                value['allowed_actions'] != ACTIONS or value['work_requirements'] != plan['work_requirements'] or
                value['configuration'] != {k: plan[k] for k in CONFIG_FIELDS} or
                not isinstance(value['units'], list) or len(value['units']) != len(plan['units'])):
            raise ValueError('V8原要求、配置、全部声明或新协调资格不符。')
        for entry, definition in zip(value['units'], plan['units']):
            fields(entry, {'definition', 'state', 'source_run', 'qualification'})
            q = qmap[definition['key']]; source = q['source']
            if (entry['definition'] != definition or entry['qualification'] != q['qualification'] or
                    entry['source_run'] != (source['run_id'] if source else None) or
                    entry['state'] != (source['state'] if source else 'NOT_RUN')):
                raise ValueError('V8单元资格、实际来源及状态不符。')
        if path['history_frontier'] == obs['frontier'] and value['selected_unit'] != obs['selected_unit']:
            raise ValueError('V8首次输入须使用准确开启coordinate。')
    else:
        sha(value['unit_revision']); fields(value['proposal'], {'reference', 'source', 'decision'})
        reference(value['proposal']['reference'])
        from .work_coordinator_handoff_adjustment_records import source, SOURCE_FIELDS
        from .work_proposals import parse
        source(value['proposal']['source']); decision = value['proposal']['decision']
        if (value['unit_revision'] != digest(unit) or parse(json.dumps(decision)) != decision or
                decision['action'] != 'dispatch' or decision['retry_of'] is not None or decision['unit_key'] != unit['key']):
            raise ValueError('普通V7须逐值绑定所选普通单元和准确派发提案。')
        added = path['history_frontier'][len(obs['frontier']):]
        coordinate_sources = {qmap[u['key']]['source']['run_id']: qmap[u['key']]['source']
            for u in plan['units'] if u['role'] == 'coordinate' and qmap[u['key']]['source'] is not None}
        latest = [f['evidence'] for f in added if f['run_id'] in coordinate_sources]
        if (not latest or not latest[-1]['exact_identity'] or not latest[-1]['result_available'] or
                {k: value['proposal']['source'][k] for k in SOURCE_FIELDS - {'version'}} != {k: latest[-1][k] for k in SOURCE_FIELDS - {'version'}} or
                parse(latest[-1]['answer']) != decision):
            raise ValueError('普通V7提案来源须属于本V2后继前沿的最新准确协调结果。')
        # The frontier stores base evidence. Proposal source.version is the
        # full evidence digest including file status, independently rebuilt by
        # runtime._proposal; it is not the base evidence digest above.
        # Source/run/proposal existence and actual V8 reservation/event ordering
        # are deliberately the reader's job, not inferred from these values.
    return deepcopy(value)
