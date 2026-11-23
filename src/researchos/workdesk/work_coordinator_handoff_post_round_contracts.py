"""Closed post-ROUND runtime data. Actual authority is rebuilt by the reader."""
from copy import deepcopy
import json
import uuid

from .store import digest
from .work_coordinator_handoff_records import fields, reference, versioned, PERMISSIONS
from .work_coordinator_handoff_adjustment_records import hashed, permissions, finite
from .work_coordinator_handoff_adjustment_contracts import closed
from .work_coordinator_handoff_post_round_context import (
    PLANNED_CAPABILITY as CAPABILITY, PLANNED_COORDINATION as PROTOCOL,
    PLANNED_UNIT as UNIT, validate_observation, observation_ref)

KIND = 'work_coordinator_handoff_post_round_adjustment_capability'
OPENED = 'coordinator_post_round_adjustment_opened'
PATH = 'COORDINATOR_FILE_HANDOFF_POST_ROUND_RUNTIME_PATH_V1'
SCOPE = 'CURRENT_ROUND_POST_ACTIVATION_RUNTIME; DISPATCH_PAUSE_ADJUSTMENT_PREVIEW; NEW_ADJUSTMENT_EDGE_NOT_OPEN'
# Actual operations in this batch. The draft's planned actions do not grant them.
ACTIONS = ['dispatch', 'pause', 'adjust_dependencies']


def old_path(value):
    from .work_coordinator_handoff_round_runtime import PATH as FIRST, validate_path as first
    from .work_coordinator_handoff_mixed_runtime import validate_path as mixed
    return (first if value.get('protocol') == FIRST else mixed)(value)


@closed
def validate_capability(row):
    fields(row, {'id', 'version', 'snapshot', 'item_id', 'created', 'command_id'})
    b = row['snapshot']
    fields(b, {'protocol', 'observation', 'coordination_protocol', 'unit_protocol',
        'allowed_actions', 'request', 'request_hash', 'scope', 'version', *PERMISSIONS})
    hashed(b); permissions(b); obs = validate_observation(b['observation']); finite(row['created'])
    uuid.UUID(row['command_id'])
    from .work_coordinator_handoff_records import sha
    sha(b['request_hash'])
    request=b['request']
    fields(request, {'id','revision','current_driver','plan','authorization','frontier','unit','retry_of','proposal','post_round_adjustment'})
    if (digest(request)!=b['request_hash'] or request['post_round_adjustment'] is not True or
            request['id']!=obs['task']['id'] or request['revision']!=obs['task']['revision'] or
            request['current_driver']!={k:obs['driver'][k] for k in ('id','generation','revision')} or
            request['plan']!=obs['plan'] or request['authorization']!={k:obs['authorization'][k] for k in ('id','revision')} or
            request['unit']!={'key':obs['selected_unit']['definition']['key'],'revision':obs['selected_unit']['revision']} or
            request['retry_of'] is not None or request['proposal'] is not None or
            request['frontier']!=digest([{'run_id':f['run_id'],'evidence':f['evidence']['version']} for f in obs['frontier']])):
        raise ValueError('新能力须绑定明确开启命令的全部原请求及准确前沿。')
    if (b['protocol'] != CAPABILITY or b['coordination_protocol'] != PROTOCOL or
            b['unit_protocol'] != UNIT or b['allowed_actions'] != ACTIONS or b['scope'] != SCOPE or
            row['item_id'] != obs['task']['id'] or row['version'] != b['version'] or
            row['id'] != KIND + ':' + b['version'] or obs['round_capability'] is None or
            not obs['observed_at'] <= row['created'] < obs['authorization']['dispatch_before']):
        raise ValueError('新能力须绑定准确ROUND观察、原授权和独立真实输入版本。')
    return deepcopy(row)


@closed
def validate_path(value):
    # Reuse only the closed structural codec of the actual ROUND lineage.
    # This projection is never returned as an old runtime proof.
    extra = {'post_round_capability', 'activation_observation', 'round_protocol', 'round_scope'}
    hashed(value); permissions(value)
    if value['protocol'] != PATH or value['scope'] != SCOPE:
        raise ValueError('新运行路径必须使用独立POST_ROUND协议。')
    projection = {k:v for k,v in value.items() if k not in extra | {'version'}}
    projection.update(protocol=value['round_protocol'], scope=value['round_scope'])
    projection = old_path(versioned(projection))
    fields(value, set(projection) | extra)
    reference(value['post_round_capability'])
    ref = value['activation_observation']; fields(ref, {'protocol', 'version', 'root', 'plan'})
    from .work_coordinator_handoff_post_round_context import OBSERVATION
    reference(ref['root']); reference(ref['plan'])
    from .work_coordinator_handoff_records import sha
    sha(ref['version'])
    if (ref['protocol'] != OBSERVATION or ref['root'] != value['root'] or ref['plan'] != value['plan'] or
            not value['post_round_capability']['id'].startswith(KIND + ':')):
        raise ValueError('新路径原ROUND身份、开启观察或能力引用不符。')
    return deepcopy(value)


@closed
def validate_context(value, capability=None):
    coordinate = value['protocol'] == PROTOCOL
    common = {'protocol', 'root', 'marker', 'plan', 'task', 'driver', 'authorization', 'phase',
        'prior_frontier', 'material_observation', 'saved_path', 'runtime_capability',
        'adjustment_capability', 'round_capability', 'post_round_capability',
        'scope', 'version', *PERMISSIONS}
    fields(value, common | ({'work_requirements', 'configuration', 'units', 'allowed_actions'}
        if coordinate else {'unit_revision', 'proposal'}))
    hashed(value); permissions(value); path = validate_path(value['saved_path'])
    from .work_coordinator_handoff_records import identity, task_revision, PHASES, sha
    identity(value); fields(value['task'], {'id', 'revision'}); task_revision(value['task']['revision'])
    if (value['protocol'] not in (PROTOCOL, UNIT) or value['scope'] != SCOPE or
            any(value[k] != path[k] for k in ('root', 'plan', 'driver', 'authorization',
                'runtime_capability', 'adjustment_capability', 'round_capability',
                'post_round_capability', 'material_observation')) or
            value['prior_frontier'] != path['history_frontier'] or
            value['task'] != {'id':value['driver']['item_id'], 'revision':value['driver']['item_revision']} or
            value['marker']['plan_id'] != path['root']['id'] or value['phase'] not in PHASES or
            value['material_observation']['root'] != value['marker']['project_root']):
        raise ValueError('新冻结输入须逐值绑定实际任务、责任、授权、完整前沿及材料。')
    if coordinate:
        from .work_coordinator_handoff_roots import CONFIG_FIELDS
        fields(value['configuration'], CONFIG_FIELDS)
        if value['allowed_actions'] != ACTIONS or len(value['units']) != len(path['current_qualifications']):
            raise ValueError('新协调输入须保存准确已开放操作与全部单元。')
        for entry, q in zip(value['units'], path['current_qualifications']):
            fields(entry, {'definition', 'state', 'source_run', 'qualification'})
            if entry['definition']['key'] != q['key'] or entry['qualification'] != q['qualification']:
                raise ValueError('新协调输入单元与准确资格不符。')
    else:
        sha(value['unit_revision']); fields(value['proposal'], {'reference', 'source', 'decision'})
        reference(value['proposal']['reference'])
        from .work_coordinator_handoff_adjustment_records import source
        from .work_proposals import parse
        source(value['proposal']['source']); decision=value['proposal']['decision']
        if parse(json.dumps(decision)) != decision or decision['action'] != 'dispatch' or decision['retry_of'] is not None:
            raise ValueError('普通V5须消费实际新V6准确派发提案。')
    if capability is not None:
        cap=validate_capability(capability); obs=cap['snapshot']['observation']
        if (value['post_round_capability'] != {k:cap[k] for k in ('id','version')} or
                path['activation_observation'] != observation_ref(obs) or
                value['authorization'] != obs['authorization'] or
                value['root'] != obs['root'] or value['plan'] != obs['plan'] or
                value['marker'] != obs['plan_snapshot']['coordinator_file_handoff_root'] or
                value['round_capability'] != obs['round_capability']):
            raise ValueError('新输入与真实开启能力的原方案授权不符。')
        if coordinate and (value['work_requirements'] != obs['plan_snapshot']['work_requirements'] or
                value['configuration'] != {k:obs['plan_snapshot'][k] for k in CONFIG_FIELDS} or
                [u['definition'] for u in value['units']] != obs['plan_snapshot']['units']):
            raise ValueError('新输入的原声明定义与开启实际方案不符。')
    return deepcopy(value)
