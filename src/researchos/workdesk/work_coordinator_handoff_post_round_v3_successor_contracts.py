"""Independent V3 first-coordinate values; semantic owners grant admission."""
from copy import deepcopy

from .store import digest
from .work_transitions import plan_ref
from .work_coordinator_handoff_records import fields, reference, versioned, PERMISSIONS, observation
from .work_coordinator_handoff_adjustment_records import hashed, permissions, finite, complete_frontier
from .work_coordinator_handoff_adjustment_contracts import closed
from .work_coordinator_handoff_post_round_successor_contracts import bounded, REQUEST_FIELDS
from .work_coordinator_handoff_post_round_v3_responsibility_records import _uuid

KIND = 'work_coordinator_handoff_post_round_successor_capability_v3'
OPENED = 'coordinator_post_round_successor_opened_v3'
CAPABILITY = 'COORDINATOR_FILE_HANDOFF_POST_ROUND_SUCCESSOR_CAPABILITY_V3'
OBSERVATION = 'COORDINATOR_FILE_HANDOFF_POST_ROUND_SUCCESSOR_OBSERVATION_V3'
PROTOCOL = 'COORDINATOR_FILE_HANDOFF_COORDINATION_INPUT_V9'
PATH = 'COORDINATOR_FILE_HANDOFF_POST_ROUND_SUCCESSOR_PATH_V3'
SCOPE = 'V3_FIRST_DECLARED_COORDINATE_ONLY; ORIGINAL_AUTHORIZATION; ORDINARY_AND_NEXT_CYCLES_CLOSED'
ACTIONS = ['dispatch', 'pause']


def request_for(obs):
    return {'id': obs['task']['id'], 'revision': obs['task']['revision'],
        'plan': obs['plan'], 'current_driver': {k: obs['driver'][k] for k in ('id', 'generation', 'revision')},
        'authorization': {k: obs['authorization'][k] for k in ('id', 'revision')},
        'frontier': digest([{'run_id': f['run_id'], 'evidence': f['evidence']['version']} for f in obs['frontier']]),
        'unit': {'key': obs['selected_unit']['definition']['key'], 'revision': obs['selected_unit']['revision']},
        'retry_of': None, 'proposal': None}


@closed
def validate_observation(value):
    fields(value, {'protocol','version','scope','root','plan','plan_snapshot','task','driver','authorization',
        'saved_path','responsibility_prefix','phase','frontier','material_observation','selected_unit',
        'qualifications','dependencies','usage','observed_at', *PERMISSIONS})
    hashed(value); permissions(value); bounded(value); finite(value['observed_at'])
    reference(value['root']); reference(value['plan']); observation(value['material_observation'])
    complete_frontier(value['frontier'])
    fields(value['selected_unit'], {'definition','revision'})
    plan = value['plan_snapshot']; unit = value['selected_unit']['definition']; driver = value['driver']; auth = value['authorization']
    if (value['protocol'] != OBSERVATION or value['scope'] != SCOPE or plan_ref(plan) != value['plan'] or
            value['task'] != {'id': plan['item_id'], 'revision': driver['item_revision']} or
            unit not in plan['units'] or unit['role'] != 'coordinate' or unit['key'] in plan['historical_coordinators'] or
            value['selected_unit']['revision'] != digest(unit) or driver['state'] != 'RESERVED' or
            driver['generation'] != auth['driver_generation'] or
            any(driver[k] != auth[k] for k in ('item_id','plan_id','plan_version')) or
            not driver['updated'] <= value['observed_at'] < auth['dispatch_before'] or
            value['usage']['unknown_requests'] or value['usage']['attempts'] != len(value['frontier']) or
            len(value['frontier']) >= auth['max_attempts_total'] or
            value['phase'] not in {'BEFORE_A','A_CAPTURED','B_COMPLETED'} or
            next(q for q in value['qualifications'] if q['key'] == unit['key'])['qualification'] != 'NOT_RUN'):
        raise ValueError('V3首次观察须绑定原声明未运行协调、明确恢复责任和原累计/截止。')
    return deepcopy(value)


@closed
def validate_capability(row):
    fields(row, {'id','version','snapshot','item_id','created','command_id'})
    b = row['snapshot']; fields(b, {'protocol','version','scope','observation','request','request_hash', *PERMISSIONS})
    hashed(b); permissions(b); bounded(b); finite(row['created']); _uuid(row['command_id'])
    obs = validate_observation(b['observation']); fields(b['request'], REQUEST_FIELDS)
    if (b['protocol'] != CAPABILITY or b['scope'] != SCOPE or
            row['id'] != KIND + ':' + b['version'] or row['version'] != b['version'] or
            row['item_id'] != obs['task']['id'] or b['request'] != request_for(obs) or
            b['request_hash'] != digest(b['request']) or
            not obs['observed_at'] <= row['created'] < obs['authorization']['dispatch_before']):
        raise ValueError('V3首次能力原UUID、九字段请求或完整观察不符。')
    return deepcopy(row)


def path_for(obs, capability):
    return versioned({'protocol': PATH, 'scope': SCOPE, 'root': obs['root'], 'plan': obs['plan'],
        'saved_path': obs['saved_path'], 'responsibility_prefix': obs['responsibility_prefix'],
        'history_frontier': obs['frontier'], 'driver': obs['driver'], 'authorization': obs['authorization'],
        'material_observation': obs['material_observation'],
        'successor_capability': {k: capability[k] for k in ('id','version')}, **PERMISSIONS})


@closed
def validate_context(value, capability):
    cap = validate_capability(capability); obs = cap['snapshot']['observation']
    fields(value, {'protocol','version','scope','root','marker','plan','task','driver','authorization','phase',
        'saved_path','prior_frontier','material_observation','selected_unit','work_requirements','configuration',
        'units','allowed_actions', *PERMISSIONS})
    hashed(value); permissions(value); bounded(value)
    from .work_coordinator_handoff_roots import CONFIG_FIELDS, MARKER_FIELD
    plan = obs['plan_snapshot']
    if (value['protocol'] != PROTOCOL or value['scope'] != SCOPE or
            value['saved_path'] != path_for(obs, cap) or value['prior_frontier'] != obs['frontier'] or
            any(value[k] != obs[k] for k in ('root','plan','task','driver','authorization','phase','material_observation','selected_unit')) or
            value['work_requirements'] != plan['work_requirements'] or value['marker'] != plan[MARKER_FIELD] or
            value['configuration'] != {k: plan[k] for k in CONFIG_FIELDS} or value['allowed_actions'] != ACTIONS or
            len(value['units']) != len(plan['units'])):
        raise ValueError('V3首次冻结输入须逐值绑定原观察、完整要求/配置和有序来源。')
    for entry, unit, q in zip(value['units'], plan['units'], obs['qualifications']):
        fields(entry, {'definition','state','source_run','qualification'})
        source = q['source']
        if entry != {'definition': unit, 'state': source['state'] if source else 'NOT_RUN',
                'source_run': source['run_id'] if source else None, 'qualification': q['qualification']}:
            raise ValueError('V3首次冻结输入的完整单元、来源和资格不符。')
    return deepcopy(value)
