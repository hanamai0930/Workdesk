"""Post-ROUND backup data closure, without dispatch or save authority."""
import json
from .store import digest
from .work_coordinator_handoff_records import versioned
from .work_coordinator_handoff_post_round_contracts import (
    KIND, OPENED, PROTOCOL, UNIT, validate_capability, validate_context, validate_path)
from .work_coordinator_handoff_round_assets import get


def require_path_assets(db,path):
    validate_path(path)
    cap=validate_capability(get(db,KIND,path['post_round_capability']))
    obs=cap['snapshot']['observation']
    from .work_coordinator_handoff_post_round_context import observation_ref
    if (path['activation_observation']!=observation_ref(obs) or path['plan']!=obs['plan'] or
            path['root']!=obs['root'] or path['authorization']!=obs['authorization']):
        raise ValueError('新输入备份路径与真实开启观察不符。')
    projection={k:v for k,v in path.items() if k not in
        {'post_round_capability','activation_observation','round_protocol','round_scope','version'}}
    projection.update(protocol=path['round_protocol'],scope=path['round_scope'])
    projection=versioned(projection)
    if projection['protocol']=='COORDINATOR_FILE_HANDOFF_ROUND_RUNTIME_PATH_V1':
        from .work_coordinator_handoff_round_assets import require_path_assets as old
    else:
        from .work_coordinator_handoff_mixed_assets import require_path_assets as old
    old(db,projection);old(db,obs['saved_path'])
    return cap


def require_context_assets(db,context):
    cap=require_path_assets(db,context['saved_path'])
    validate_context(context,cap)


def required_records(db,file_baseline):
    rows=[validate_capability(json.loads(r[0])) for r in db.execute('SELECT data FROM collaboration WHERE kind=?',(KIND,))]
    events=list(db.execute('SELECT payload,task_id,attempt_id FROM events WHERE kind=?',(OPENED,)))
    if (sorted(map(digest,rows))!=sorted(digest(json.loads(r[0])) for r in events) or
            any(r[1] is not None or r[2] is not None for r in events)):
        raise ValueError('新能力备份记录与实际开启事件全集不闭合。')
    for row in rows:
        obs=row['snapshot']['observation'];file_baseline(obs['material_observation'])
        get(db,'teamwork_plan',obs['plan'])
        request=row['snapshot']['request']
        command=db.execute('SELECT kind,payload_hash,receipt FROM commands WHERE id=?',(row['command_id'],)).fetchone()
        if not command or command[0]!='work-item.unit-send' or command[1]!=digest(request):
            raise ValueError('新能力备份缺少准确开启命令。')
        receipt=json.loads(command[2]);runs=[json.loads(r[0]) for r in db.execute("SELECT data FROM collaboration WHERE kind='work_unit_run'")]
        first=[r for r in runs if r['command_id']==row['command_id']]
        if len(first)!=1 or first[0]['attempt_id']!=receipt.get('launch_attempt'):
            raise ValueError('新能力备份缺少准确同命令首派发。')
    from .work_coordinator_handoff_post_round_intent import PROTOCOL as INTENT, validate_intent
    for raw in db.execute("SELECT data FROM collaboration WHERE kind='work_unit_run'"):
        run=json.loads(raw[0]);frozen=run['input']
        context=frozen.get('handoff_coordination_context') or frozen.get('handoff_unit_context',{})
        if context.get('protocol') in (PROTOCOL,UNIT): require_context_assets(db,context)
        intent=frozen.get('handoff_delivery_intent')
        if intent and intent.get('protocol')==INTENT:
            validate_intent(intent);require_path_assets(db,intent['saved_path']);file_baseline(intent['baseline'])
            from .work_coordinator_handoff_records import DELIVERY_KIND
            actual=[json.loads(r[0]) for r in db.execute('SELECT data FROM collaboration WHERE kind=?',(DELIVERY_KIND,))
                if json.loads(r[0])['snapshot']['run_id']==run['id']]
            if len(actual)!=1 or actual[0]['snapshot']['intent']!=intent:
                raise ValueError('新B冻结意图缺少唯一准确交付记录。')
