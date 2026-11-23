"""Closed continuation values. Only a complete, fresh owner grants admission."""
from copy import deepcopy

from .store import digest
from .work_transitions import plan_ref
from .work_coordinator_handoff_records import (
    fields, reference, versioned, PERMISSIONS, observation, task_revision, sha,
    identity, FIELDS, INTENT as ROOT_INTENT, frontier)
from .work_coordinator_handoff_adjustment_records import hashed, permissions, finite, complete_frontier, usage
from .work_coordinator_handoff_adjustment_contracts import closed
from .work_coordinator_handoff_post_round_successor_contracts import bounded, REQUEST_FIELDS
from .work_coordinator_handoff_post_round_v3_responsibility_records import _uuid

KIND = 'work_coordinator_handoff_post_round_declared_continuation_capability_v3'
OPENED = 'coordinator_post_round_declared_continuation_opened_v3'
CAPABILITY = 'COORDINATOR_FILE_HANDOFF_V3_DECLARED_CONTINUATION_CAPABILITY_V1'
OBSERVATION = 'COORDINATOR_FILE_HANDOFF_V3_DECLARED_CONTINUATION_OBSERVATION_V1'
PATH = 'COORDINATOR_FILE_HANDOFF_V3_DECLARED_CONTINUATION_PATH_V1'
COORDINATION = 'COORDINATOR_FILE_HANDOFF_COORDINATION_INPUT_V10'
UNIT = 'COORDINATOR_FILE_HANDOFF_UNIT_INPUT_V8'
INTENT = 'COORDINATOR_FILE_HANDOFF_V3_DECLARED_CONTINUATION_B_INTENT_V1'
SCOPE = 'ORIGINAL_DECLARED_SERIAL_FIRST_RUNS; ORIGINAL_AUTHORIZATION; STRICT_PROPOSAL; ORIGINAL_SINGLE_AB_RELATION'
ACTIONS = ['dispatch', 'pause']
OBS_FIELDS = {'protocol','version','scope','root','plan','plan_snapshot','task','driver','authorization',
    'saved_registration','saved_path_reference','responsibility_prefix','first_capability','first_receipt',
    'phase','frontier','material_observation','selected_unit','qualifications','dependencies','usage',
    'proposal','observed_at','request','request_hash', *PERMISSIONS}
PATH_FIELDS = {'protocol','version','scope','root','plan','saved_registration','saved_path_reference',
    'responsibility_prefix','first_capability','continuation_capability','driver_history','driver',
    'authorization','history_frontier','current_qualifications','material_observation','phase', *PERMISSIONS}
COMMON_CONTEXT = {'protocol','version','scope','root','marker','plan','task','driver','authorization',
    'phase','saved_path','prior_frontier','material_observation', *PERMISSIONS}


def path_reference(path):
    """Typed value reference; caller must rebuild the complete referenced path."""
    result = {k:path[k] for k in ('protocol','version')}
    validate_path_reference(result)
    return result


def validate_path_reference(value):
    fields(value, {'protocol','version'}); sha(value['version'])
    from .work_coordinator_handoff_post_round_v3_path import PATH as SAVED_PATH
    if value['protocol'] != SAVED_PATH: raise ValueError('V3续行须引用原完整保存路径。')


def request_for(value):
    return {'id':value['task']['id'], 'revision':value['task']['revision'], 'plan':value['plan'],
        'current_driver':{k:value['driver'][k] for k in ('id','generation','revision')},
        'authorization':{k:value['authorization'][k] for k in ('id','revision')},
        'frontier':digest([{'run_id':f['run_id'],'evidence':f['evidence']['version']} for f in value['frontier']]),
        'unit':{'key':value['selected_unit']['definition']['key'],'revision':value['selected_unit']['revision']},
        'retry_of':None, 'proposal':value['proposal']}


@closed
def validate_observation(value):
    fields(value, OBS_FIELDS); hashed(value); permissions(value); bounded(value)
    finite(value['observed_at']); reference(value['root']); reference(value['plan'])
    for key in ('saved_registration','first_capability','first_receipt'): reference(value[key])
    validate_path_reference(value['saved_path_reference'])
    for ref in value['responsibility_prefix']: reference(ref)
    complete_frontier(value['frontier']); observation(value['material_observation'])
    usage(value['usage'])
    fields(value['selected_unit'], {'definition','revision'}); fields(value['request'],REQUEST_FIELDS)
    plan=value['plan_snapshot']; unit=value['selected_unit']['definition']; driver=value['driver']; auth=value['authorization']
    from .work_coordinator_handoff_post_round_v3_saved_records import driver_shape as successor_driver, auth_shape as transferred_authorization
    successor_driver(driver); transferred_authorization(auth)
    if (value['protocol']!=OBSERVATION or value['scope']!=SCOPE or plan_ref(plan)!=value['plan'] or
            value['task']!={'id':plan['item_id'],'revision':driver['item_revision']} or unit not in plan['units'] or
            unit['key'] in plan['historical_coordinators'] or value['selected_unit']['revision']!=digest(unit) or
            driver['state']!='RESERVED' or driver['generation']!=auth['driver_generation'] or
            any(driver[k]!=auth[k] for k in ('item_id','plan_id','plan_version')) or
            any(driver[k]!=plan[v] for k,v in (('item_id','item_id'),('plan_id','id'),('plan_version','version'))) or
            not driver['updated']<=value['observed_at']<auth['dispatch_before'] or
            value['usage']['unknown_requests'] or value['usage']['attempts']!=len(value['frontier']) or
            len(value['frontier'])>=auth['max_attempts_total'] or value['phase'] not in {'BEFORE_A','A_CAPTURED','B_COMPLETED'} or
            len(value['qualifications'])!=len(plan['units']) or
            [q['key'] for q in value['qualifications']]!=[u['key'] for u in plan['units']] or
            next(q for q in value['qualifications'] if q['key']==unit['key'])['qualification']!='NOT_RUN' or
            value['request']!=request_for(value) or value['request_hash']!=digest(value['request'])):
        raise ValueError('V3续行观察须绑定原声明首次、真实完整前沿、原责任/授权及本次准确请求。')
    if unit['role']=='coordinate':
        if value['proposal'] is not None: raise ValueError('V3协调续行不消费普通派发提案。')
    else: reference(value['proposal'])
    return deepcopy(value)


@closed
def validate_capability(row):
    fields(row, {'id','version','snapshot','item_id','created','command_id'})
    body=row['snapshot']; fields(body, {'protocol','version','scope','observation',*PERMISSIONS})
    hashed(body); permissions(body); bounded(body); finite(row['created']); _uuid(row['command_id'])
    obs=validate_observation(body['observation'])
    if (body['protocol']!=CAPABILITY or body['scope']!=SCOPE or row['id']!=KIND+':'+body['version'] or
            row['version']!=body['version'] or row['item_id']!=obs['task']['id'] or
            not obs['observed_at']<=row['created']<obs['authorization']['dispatch_before']):
        raise ValueError('V3续行能力不等于本次原请求、原授权和完整开启观察。')
    return deepcopy(row)


@closed
def validate_path(value):
    fields(value, PATH_FIELDS); hashed(value); permissions(value); bounded(value)
    for key in ('root','plan','saved_registration','first_capability','continuation_capability'): reference(value[key])
    validate_path_reference(value['saved_path_reference'])
    for ref in value['responsibility_prefix']: reference(ref)
    complete_frontier(value['history_frontier']); observation(value['material_observation'])
    from .work_coordinator_handoff_post_round_v3_saved_records import driver_shape, auth_shape
    from .work_coordinator_handoff_round_records import event_ref
    driver_shape(value['driver']); auth_shape(value['authorization'])
    if (value['protocol']!=PATH or value['scope']!=SCOPE or not value['driver_history'] or
            value['driver_history'][-1]['driver']!=value['driver'] or
            any(value['driver'][k]!=value['authorization'][k] for k in ('item_id','plan_id','plan_version')) or
            value['driver']['generation']!=value['authorization']['driver_generation'] or
            value['driver']['state']!='RESERVED' or
            value['phase'] not in {'BEFORE_A','A_CAPTURED','B_COMPLETED'}):
        raise ValueError('V3动态路径须包含到本步边界的完整责任和材料前沿。')
    previous=0
    for entry in value['driver_history']:
        fields(entry, {'event','driver'}); event=entry['event']
        event_ref(event); driver_shape(entry['driver'])
        if type(event['seq']) is not int or event['seq']<=previous:
            raise ValueError('V3动态路径真实责任事件须准确有序。')
        previous=event['seq']
    return deepcopy(value)


@closed
def validate_context(value):
    coordinate=value.get('protocol')==COORDINATION
    fields(value, COMMON_CONTEXT | ({'selected_unit','work_requirements','configuration','units','allowed_actions'}
        if coordinate else {'unit_revision','proposal'}))
    hashed(value); permissions(value); bounded(value); path=validate_path(value['saved_path'])
    fields(value['task'],{'id','revision'}); task_revision(value['task']['revision'])
    if (value['protocol'] not in {COORDINATION,UNIT} or value['scope']!=SCOPE or
            any(value[k]!=path[k] for k in ('root','plan','driver','authorization','phase','material_observation')) or
            value['prior_frontier']!=path['history_frontier'] or
            value['task']!={'id':value['driver']['item_id'],'revision':value['driver']['item_revision']}):
        raise ValueError('V3续行输入必须绑定本步真实动态路径。')
    if coordinate:
        fields(value['selected_unit'], {'definition','revision'})
        if (value['allowed_actions']!=ACTIONS or value['selected_unit']['definition']['role']!='coordinate' or
                value['selected_unit']['revision']!=digest(value['selected_unit']['definition']) or
                len(value['units'])!=len(path['current_qualifications'])):
            raise ValueError('V3协调须冻结原完整单元、来源、资格和dispatch/pause候选。')
        from .work_coordinator_handoff_roots import CONFIG_FIELDS
        fields(value['configuration'],CONFIG_FIELDS)
        for entry,q in zip(value['units'],path['current_qualifications']):
            fields(entry,{'definition','state','source_run','qualification'}); source=q['source']
            if (entry['definition']['key']!=q['key'] or
                    entry['qualification']!=q['qualification'] or
                    entry['state']!=(source['state'] if source else 'NOT_RUN') or
                    entry['source_run']!=(source['run_id'] if source else None)):
                raise ValueError('V3协调完整单元与真实来源资格不符。')
    else:
        sha(value['unit_revision']); fields(value['proposal'],{'reference','source','decision'})
        reference(value['proposal']['reference'])
    return deepcopy(value)


@closed
def validate_intent(value):
    fields(value,FIELDS[ROOT_INTENT]|{'protocol','version','plan','saved_path'})
    hashed(value); bounded(value); identity(value); reference(value['plan'])
    path=validate_path(value['saved_path']); baseline=value['baseline']; evidence=value['source_evidence']
    observation(baseline); complete_frontier(value['protected_history']); complete_frontier(value['frontier'])
    task_revision(value['item_revision']); sha(value['target_revision']); sha(value['input_template_version'])
    fields(value['relation'],{'source_unit','target_unit','paths'})
    if (value['protocol']!=INTENT or value['scope']!=SCOPE or path['phase']!='A_CAPTURED' or
            any(value[k]!=path[k] for k in ('root','plan','driver','authorization')) or
            value['protected_history']!=path['history_frontier'] or value['frontier']!=value['protected_history'] or
            baseline!=path['material_observation'] or baseline['missing'] or
            baseline['root']!=value['marker']['project_root'] or baseline['paths']!=value['relation']['paths'] or
            value['driver']['item_revision']!=value['item_revision'] or
            evidence.get('run_id')!=value['source_run'] or evidence.get('state')!='COMPLETED' or
            not evidence.get('exact_identity') or not evidence.get('result_available') or evidence.get('blockers')!=[] or
            evidence.get('file_artifacts',{}).get('state')!='CURRENT' or
            evidence['file_artifacts']['manifest']!=baseline['manifest'] or
            value['relation']['source_unit']==value['relation']['target_unit'] or
            len(value['frontier'])>=value['authorization']['max_attempts_total']):
        raise ValueError('V3首次B意图须证明原关系、当前A全部文件、完整历史和准确续行路径。')
    source=[f for f in value['frontier'] if f['run_id']==value['source_run']]
    if len(source)!=1 or source[0]['evidence']['version']!=evidence['result_version']:
        raise ValueError('V3首次B意图缺少真实A完整历史。')
    return deepcopy(value)
