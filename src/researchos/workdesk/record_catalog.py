"""Unified read-only conversation directory with bounded keyset pagination."""
import base64
import json
import math

from .cooperation_conversation import KIND as COOPERATION_KIND, key as cooperation_key
from .cooperation_followup import preparing_successor, total_rounds
from .store import digest

PROJECT_SCOPE="CASE WHEN EXISTS(SELECT 1 FROM record_scopes s WHERE s.kind=records.kind AND s.record_id=records.id) THEN (SELECT s.project_id FROM record_scopes s WHERE s.kind=records.kind AND s.record_id=records.id) ELSE project END"

_RECORDS="""WITH owned AS (
 SELECT json_extract(m.value,'$.task_id') AS id FROM collaboration c,json_each(c.data,'$.messages') m
 WHERE c.kind='dialogue' AND json_extract(m.value,'$.task_id') IS NOT NULL
 UNION SELECT json_extract(data,'$.pending') FROM collaboration WHERE kind='dialogue'
), records AS (
 SELECT 'task' AS kind,t.id,coalesce(json_extract(d.data,'$.title'),t.title) AS title,
 json_extract(t.spec,'$.provider') AS provider,t.state,t.created,t.updated,
 coalesce(json_extract(d.data,'$.pinned'),0) AS pinned,coalesce(json_extract(d.data,'$.archived'),0) AS archived,
 json_extract(t.spec,'$.goal') AS body,
 coalesce(json_extract(d.data,'$.project'),t.project_id) AS project,
 coalesce(json_extract(d.data,'$.scene'),json_extract(t.spec,'$.work_scene')) AS scene
 FROM tasks t LEFT JOIN collaboration d ON d.kind='display' AND d.id='display:task:'||t.id
 WHERE coalesce(json_extract(t.spec,'$.interaction'),'')!='chat'
 AND NOT EXISTS(SELECT 1 FROM owned WHERE owned.id=t.id)
 AND coalesce(json_extract(d.data,'$.deleted'),0)=0
 UNION ALL
 SELECT 'chat',c.id,json_extract(c.data,'$.title'),json_extract(c.data,'$.provider'),
 CASE WHEN json_extract(c.data,'$.pending') IS NOT NULL THEN 'RUNNING' ELSE 'SAVED' END,
 json_extract(c.data,'$.created'),coalesce(json_extract(c.data,'$.updated'),c.updated),
 coalesce(json_extract(c.data,'$.pinned'),0),coalesce(json_extract(c.data,'$.archived'),0),c.data,NULL,NULL
 FROM collaboration c WHERE c.kind='ui_chat'
 UNION ALL
 SELECT 'dialogue',c.id,coalesce(json_extract(d.data,'$.title'),json_extract(c.data,'$.title')),
 json_extract(c.data,'$.config.provider'),CASE WHEN json_extract(c.data,'$.pending') IS NOT NULL THEN 'RUNNING' ELSE 'DISCUSSION' END,
 json_extract(c.data,'$.created'),c.updated,coalesce(json_extract(d.data,'$.pinned'),0),coalesce(json_extract(d.data,'$.archived'),0),c.data,
 coalesce(json_extract(d.data,'$.project'),json_extract(c.data,'$.project_id')),
 coalesce(json_extract(d.data,'$.scene'),json_extract(c.data,'$.work_scene'))
 FROM collaboration c LEFT JOIN collaboration d ON d.kind='display' AND d.id='display:dialogue:'||c.id
 WHERE c.kind='dialogue' AND coalesce(json_extract(d.data,'$.deleted'),0)=0
"""

CTE=_RECORDS+') '
_DIRECTORY_CTE=_RECORDS+""" UNION ALL
 SELECT 'cooperation',i.id,coalesce(json_extract(d.data,'$.title'),i.title),
 NULL,json_extract(c.data,'$.state'),i.created,max(i.updated,c.updated,coalesce(d.updated,0)),
 coalesce(json_extract(d.data,'$.pinned'),0),coalesce(json_extract(d.data,'$.archived'),0),
 i.goal||char(10)||i.constraints_text||char(10)||i.acceptance||char(10)||coalesce((
     SELECT group_concat(json_extract(m.value,'$.text'),char(10))
     FROM json_each(c.data,'$.messages') m
 ),''),
 CASE WHEN json_type(d.data,'$.project') IS NULL THEN i.project_id ELSE json_extract(d.data,'$.project') END,
 json_extract(c.data,'$.formal.work_scene')
 FROM collaboration c JOIN work_items i ON c.id='cooperation-conversation:'||i.id
 LEFT JOIN collaboration d ON d.kind='display' AND d.id='display:item:'||i.id
 WHERE c.kind='work_cooperation_conversation' AND coalesce(json_extract(d.data,'$.deleted'),0)=0
) """
_PUBLIC_TASK="""(kind!='task' OR EXISTS (
    SELECT 1 FROM tasks t WHERE t.id=records.id
    AND coalesce(json_extract(t.spec,'$.work_mode'),'')!='cooperative'
    AND json_type(t.spec,'$.cooperative') IS NOT 'object'
))"""

_PARTNER_RECORDS=""" UNION ALL
 SELECT 'partner_run',r.id,
 coalesce(nullif(substr(json_extract(r.data,'$.prompt'),1,160),''),json_extract(r.data,'$.partner_id')||' 官方工作'),
 json_extract(r.data,'$.partner_id'),json_extract(r.data,'$.state'),
 coalesce(json_extract(r.data,'$.created'),r.updated,0),coalesce(json_extract(r.data,'$.updated'),r.updated,0),0,0,
 coalesce(json_extract(r.data,'$.prompt'),'')||char(10)||coalesce(json_extract(r.data,'$.result.text'),json_extract(r.data,'$.result.output'),''),
 json_extract(r.data,'$.project_id'),NULL
 FROM core_partner_objects r WHERE r.kind='run' AND json_extract(r.data,'$.mode')='run'
 AND coalesce(json_extract(r.data,'$.parent_task_id'),'')=''
"""


def _with_partner_records(db, cte):
    if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='core_partner_objects'").fetchone():
        before,after=cte.rsplit(')',1)
        return before+_PARTNER_RECORDS+')'+after
    return cte


def _cooperation_fields(service, db, row):
    identity = row['id']
    raw = db.execute('SELECT data FROM collaboration WHERE id=? AND kind=?',
                     (cooperation_key(identity), COOPERATION_KIND)).fetchone()
    record = json.loads(raw[0])
    display = service.work_records.display(db,'item',identity)
    progress = {'round_count': total_rounds(record),
                'dispatches': 0, 'limit': record['authorization']['limit']}
    state = row['state']
    can_archive = False
    if state != 'DISCUSSING':
        plan = service.teamwork.current(db,identity)
        flow = service.work_flows.current(db,identity)
        preparing = preparing_successor(record,plan)
        preparations = service.cooperation_conversation.preparation(record).records(db,identity)
        latest = preparations[-1] if preparations else {}
        if not preparing and flow and flow['state']=='RESULTS_AVAILABLE':
            state = 'COMPLETED'
            queued = record.get('queued_followup')
            can_archive = not record.get('pending') and (not queued or bool(queued.get('problem')))
        elif plan and record['formal'].get('plan_only') and not flow:
            state = 'PLAN_READY'
        elif ((not preparing and flow and flow['state']=='PAUSED') or record.get('paused_preparation')
              or latest.get('state') in {'BLOCKED','PAUSED_AFTER_RESTART','NEEDS_RECONCILIATION','NEEDS_CAPABILITY'}
              or latest.get('state')=='PREPARING' and latest.get('instance')!=service.instance):
            state = 'PAUSED'
        elif not preparing and flow and flow['state']=='SCHEDULED':
            state = 'SCHEDULED'
        unit_runs = db.execute("SELECT COUNT(*) FROM collaboration WHERE kind='work_unit_run' "
                               "AND json_extract(data,'$.item_id')=?",(identity,)).fetchone()[0]
        progress['dispatches'] = len(preparations) + unit_runs
        auth = service.work_units.authorization(db,service.work_drivers.current(db,identity))
        if auth:
            progress['limit'] = auth['max_attempts_total'] + len(preparations)
    row.update(revision=display.get('revision',0), cooperation_mode=record['mode'],
               progress=progress, state=state, can_archive=can_archive,
               href='#work-items/conversation/'+identity, detail_href='#work-items/item/'+identity+'/controls')


def catalog(service,query='',mode='all',archive='all',cursor='',limit=40,scope='ordinary',project=None):
    allowed_modes={'all','chat','work','independent','cooperation'} if scope=='directory' else {'all','chat','work'}
    if mode not in allowed_modes or archive not in {'all','active','archived'}:raise ValueError('记录筛选无效。')
    if not isinstance(query,str) or len(query)>200:raise ValueError('搜索文字最多200字。')
    try:limit=int(limit)
    except (ValueError,TypeError):raise ValueError('分页大小无效。') from None
    if not 1<=limit<=100:raise ValueError('每页须为1至100条。')
    if scope not in {'ordinary','project','all','directory'}:raise ValueError('记录展示范围无效。')
    if scope=='project':
        if not project:raise ValueError('请明确项目范围。')
        with service.store.read() as db:service.work_items.project(db,project)
    elif project is not None:raise ValueError('项目参数仅用于项目范围。')
    query=query.strip();filters=digest([query,mode,archive,scope,project]);where=[];args=[]
    if scope=='ordinary':where.append("NOT EXISTS(SELECT 1 FROM record_scopes s WHERE s.kind=records.kind AND s.record_id=records.id AND s.project_id IS NOT NULL)")
    if scope=='project':
        where.append(PROJECT_SCOPE+'=?');args.append(project)
    if scope=='directory':where.append(_PUBLIC_TASK)
    if mode=='chat':where.append("kind='chat'")
    if mode=='work':where.append("kind!='chat'")
    if mode=='independent':where.append("kind IN ('task','partner_run')")
    if mode=='cooperation':where.append("kind IN ('dialogue','cooperation')")
    if archive!='all':where.append('archived=?');args.append(int(archive=='archived'))
    if query:
        where.append("""(instr(lower(coalesce(title,'')),lower(?))>0 OR instr(lower(coalesce(body,'')),lower(?))>0
          OR kind='task' AND (EXISTS(SELECT 1 FROM attempts a WHERE a.task_id=records.id AND instr(lower(coalesce(json_extract(a.result,'$.answer'),'')),lower(?))>0)
          OR EXISTS(SELECT 1 FROM events e WHERE e.task_id=records.id AND e.kind IN ('execution_reserved','model_delta','model_message') AND
          instr(lower(coalesce(json_extract(e.payload,'$.human_note'),json_extract(e.payload,'$.text'),'')),lower(?))>0)))""")
        args.extend([query]*4)
    filtered=' AND '.join(where) or '1=1';paged=filtered;page_args=list(args)
    if cursor:
        try:
            if len(cursor)>1200:raise ValueError()
            last=json.loads(base64.urlsafe_b64decode(cursor+'='*(-len(cursor)%4)))
            kinds={'task','chat','dialogue','cooperation','partner_run'} if scope=='directory' else {'task','chat','dialogue','partner_run'}
            if last['filters']!=filters or last['pinned'] not in (0,1) or not math.isfinite(last['created']) or last['kind'] not in kinds or not isinstance(last['id'],str):raise ValueError()
        except (ValueError,TypeError,KeyError,OverflowError):raise ValueError('分页位置无效，请重新搜索。') from None
        paged+=' AND (pinned<? OR pinned=? AND (created<? OR created=? AND (kind>? OR kind=? AND id>?)))'
        page_args.extend([last['pinned'],last['pinned'],last['created'],last['created'],last['kind'],last['kind'],last['id']])
    with service.store.read() as db:
        cte=_with_partner_records(db,_DIRECTORY_CTE if scope=='directory' else CTE)
        total=db.execute(cte+'SELECT COUNT(*) FROM records WHERE '+filtered,args).fetchone()[0]
        rows=[dict(r) for r in db.execute(cte+'SELECT kind,id,title,provider,state,created,updated,pinned,archived,'+PROJECT_SCOPE+' AS project,scene FROM records WHERE '+paged+' ORDER BY pinned DESC,created DESC,kind,id LIMIT ?',[*page_args,limit+1])]
        for row in rows[:limit]:
            if scope=='directory':
                project_row=db.execute('SELECT name FROM projects WHERE id=?',(row['project'],)).fetchone() if row['project'] else None
                row['project_name']=project_row['name'] if project_row else None
            if row['kind']=='cooperation':
                _cooperation_fields(service,db,row)
            elif row['kind']=='partner_run':
                raw=db.execute("SELECT data FROM core_partner_objects WHERE kind='run' AND id=?",(row['id'],)).fetchone()
                run=json.loads(raw[0])
                row.update(revision=str(run.get('revision',0)),independent_work=True,run_id=row['id'],
                           read_only=True,can_archive=False,can_delete=False,actions=[])
            elif row['kind']=='chat':
                row['revision']=service.collaboration.get(db,'ui_chat',row['id'])['revision']
            else:
                source=service.work_records.source(db,row['kind'],row['id'])
                row['revision']=service.work_records.view(db,row['kind'],source)['revision']
                if scope in {'project','directory'} and row['kind']=='task':
                    row['independent_work']=source.get('work_mode')=='independent'
    more=len(rows)>limit;rows=rows[:limit];next_cursor=None
    if more:
        last=rows[-1];value={k:last[k] for k in ('pinned','created','kind','id')};value['filters']=filters
        next_cursor=base64.urlsafe_b64encode(json.dumps(value,separators=(',',':')).encode()).decode().rstrip('=')
    for row in rows:
        if row['kind']=='partner_run':
            row['href']='#work/partners/run/'+row['id']
        elif row['kind']!='cooperation':
            row['href']=('#chat/'+row['id']) if row['kind']=='chat' else '#work/'+row['kind']+'/'+row['id']
    return {'records':rows,'total':total,'next_cursor':next_cursor,'has_more':more,'limit':limit,'query':query,'mode':mode,'archive':archive,'scope':scope,'project_id':project,'no_dispatch':True}
