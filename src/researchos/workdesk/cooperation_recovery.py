"""Exact continuation of an interrupted stage in a cooperation chat."""

from pathlib import Path

from . import workspace
from .store import digest
from .work_flows import READY_TO_START


RETRYABLE = {'FAILED_NO_SEND', 'FAILED', 'INTERRUPTED', 'STOPPED_RUNTIME'}


def target(service, db, item, plan, runs):
    """Retry the latest ordinary stage only if its result is still unusable."""
    stages = {u['key']: u for u in plan['units'] if u['key'].startswith('pdca_')}
    last = next((run for run in reversed(runs)
                 if run['plan_id']==plan['id'] and run['unit_key'] in stages), None)
    if last is None:
        return None
    unit = stages[last['unit_key']]
    e = service.work_units.evidence(db,last)
    if not e['exact_identity'] or e['blockers']:
        return None
    files = e['file_artifacts']
    missing = []
    if e['result_available'] and files['state']=='MISSING':
        root = Path(service.store.attempt(last['attempt_id'],db)['workspace'])
        for name in unit['outputs']:
            try:
                workspace.select_files(root,[name],service.locations.data)
            except ValueError as exc:
                if str(exc)!='文件不存在。':
                    raise
                missing.append(name)
    if not (e['state'] in RETRYABLE or missing
            or files['state']=='CURRENT' and files['check_status']=='FAIL'):
        return None
    if any(d.get('run_id')==last['id'] for r in runs for d in r['input'].get('dependencies', [])):
        raise ValueError('此结果已有后续工作使用，请在任务详情核对交接。')
    frozen = service.work_units.input_for(db,item,plan,unit,runs)
    if frozen['dependencies']!=last['input']['dependencies']:
        raise ValueError('前置结果版本已经变化，请在任务详情核对。')
    return {'unit':{'key':unit['key'],'revision':digest(unit)},'retry_of':last['id'],
            'task_id':last['task_id'],'title':unit['title'],'result_version':e['version'],
            'checkpoint_state':e['state'],'missing_outputs':missing or files.get('missing_outputs',[])}


def preview(service, db, item, flow, plan):
    result = {'id':None,'can_resume':False,'title':'从中断处继续','message':None,'task_id':None}
    if not flow or flow['state']!='PAUSED' or not plan or not plan.get('pdca'):
        return None, None
    driver = service.work_drivers.current(db,item['id'])
    auth = service.work_units.authorization(db,driver)
    if not driver or driver['state'] not in ('RESERVED','PAUSED') or not auth:
        return {**result,'message':'请先在任务详情核对合作责任和派发授权。'},None
    if driver['state']=='PAUSED' and not service.work_drivers.view(db,item)['can_resume']:
        return {**result,'message':'请先在任务详情核对已暂停的合作责任与关联执行。'},None
    runs = service.work_units.rows(db,item['id'])
    blocked = service.work_flows.dispatch_block(auth,runs)
    if blocked:
        return {**result,'message':blocked},None
    try:
        retry = target(service,db,item,plan,runs)
        candidate = {**flow, 'state':'RUNNING','generation':'cooperation-preview:'+flow['generation'],
            'driver_generation':driver['generation'],'driver_revision':driver['revision'],
            'item_revision':item['revision'],'plan':{k:plan[k] for k in ('id','version')},
            'authorization':service.work_units.auth_ref(auth),'chat_recovery':retry}
        state, reason, _ = service.work_flows.decision(
            db,item,candidate,driver_override={**driver,'state':'RESERVED','instance':service.instance})
        if state not in READY_TO_START:
            return {**result,'message':reason},None
    except (OSError,ValueError) as exc:
        return {**result,'message':str(exc)},None
    front = service.work_units.frontier(db,item['id'])
    scope = {**service.work_flows.scope(db,item,driver,plan),'flow':service.work_flows.reference(flow),
             'authorization':candidate['authorization'],'frontier':front,'action':'start'}
    identity = digest({'scope':scope,'retry':retry})
    return {**result,'id':identity,'can_resume':True,
            'title':retry['title'] if retry else '接着当前进度继续',
            'message':'将从这个阶段重新执行，已核对的前置成果会沿用。' if retry else '按已有进度继续派发。',
            'task_id':retry['task_id'] if retry else None}, (scope,retry)
