"""Opt-in scheduled continuation and durable automatic goals; never replay unknown sends."""
from datetime import datetime,timedelta
import json
from pathlib import Path
import time
import uuid
from zoneinfo import ZoneInfo

from . import workspace
from .contracts import bounded_text,integer
from .store import ACTIVE,UNKNOWN,digest,uid

MARKER='WORKDESK_GOAL_COMPLETE'
PARTNER_CONTINUATION_REASON='此伙伴连接的原会话自动接续尚未接通；请使用普通工作或合作重复计划，不以新会话代替原上下文。'


def zone(locations,name):
    # Use the shipped IANA data on Windows; no user-site dependency.
    if not isinstance(name,str) or len(name)>100 or any(x in name for x in ('..','\\',':')) or name.startswith('/'):raise ValueError('时区无效。')
    path=locations.runtime/'python-site/tzdata/zoneinfo'/name
    if not path.is_file():raise ValueError('此时区未打包。')
    with path.open('rb') as f:return ZoneInfo.from_file(f,key=name)


def next_due(locations,row,after):
    tz=zone(locations,row['timezone']);value=datetime.fromtimestamp(row['due'],tz)
    delta=timedelta(days=1 if row['frequency']=='daily' else 7)
    hour,minute,second=map(int,row.get('wall_time',value.strftime('%H:%M:%S')).split(':'))
    value=(value+delta).replace(hour=hour,minute=minute,second=second)
    while value.timestamp()<=after:value=(value+delta).replace(hour=hour,minute=minute,second=second)
    # Nonexistent wall times normalize forward; repeated times run only once.
    return value.timestamp()


class Planning:
    def __init__(self,service):
        self.s=service;self.c=service.collaboration
        from .planning_cooperation import CooperativePlanning
        self.cooperative=CooperativePlanning(self)
        with service.store.write() as db:
            for kind in ('scheduled_job','goal_job'):
                for row in self.c.all(db,kind):
                    if row['state']=='RUNNING':
                        row.update(state='PAUSED',revision=uid(),reason='服务重启，核对后手动继续；不补发旧回合。');self.c.put(db,kind,row)

    def handlers(self):return {'planning.schedule-save':self.schedule_save,'planning.goal-save':self.goal_save,'planning.state':self.state,'planning.advance':self.advance,**self.cooperative.handlers()}

    def catalog(self):
        with self.s.store.read() as db:
            from .partner_connections import projection
            partners=projection(db)
            profiles=self.s.profiles.list(db)
            legacy={p['id'] for p in profiles}
            tasks=[{'id':r['id'],'title':r['title'],'state':r['state'],
                    'can_schedule':self.s.store.task(r['id'],db)['spec'].get('execution')!='partner_native',
                    'reason':PARTNER_CONTINUATION_REASON if self.s.store.task(r['id'],db)['spec'].get('execution')=='partner_native' else ''}
                   for r in self.s.work_records.catalog(db,True)['records'] if r['kind']=='task' and r['state']=='COMPLETED'
                   and self.s.store.task(r['id'],db)['spec'].get('runtime_mode')=='official']
            return {**self.cooperative.catalog(db),'schedules':self.c.all(db,'scheduled_job'),'goals':self.c.all(db,'goal_job'),'legacy':self.c.all(db,'ui_schedule'),
                    'fixed_schedules':self.s.work_flows.schedule_catalog(db),
                    'projects':self.s.work_records.projects(db),'tasks':tasks,
                    'providers':[{**p,'legacy_auto_goal_supported':p['id'] in legacy,'legacy_available':p['id'] in legacy,
                                  'auto_goal_supported':False,'reason':PARTNER_CONTINUATION_REASON} for p in partners['partners']],
                    'partner_connections':[{**c,'auto_continue_supported':False,'auto_goal_supported':False,
                                            'continuation_reason':PARTNER_CONTINUATION_REASON,'reason':c.get('reason') or PARTNER_CONTINUATION_REASON}
                                           for c in partners['connections']],
                    'service_required':True,'wake_when_closed':False,'missed_policy':'SKIP_NO_CATCHUP','no_dispatch':True}

    def checked(self,db,p):
        kind=p.get('kind')
        if kind not in {'scheduled_job','goal_job'}:raise ValueError('计划类型无效。')
        row=self.c.get(db,kind,p.get('id'))
        if row['revision']!=p.get('revision'):raise ValueError('计划版本已改变，请刷新。')
        return kind,row

    def eligible(self,db,identity):
        t=self.s.store.task(identity,db);self.s.work_records.guard(db,'task',identity);self.s.monitor.guard(db,{'task_id':identity})
        if t['spec'].get('runtime_mode')!='official':raise ValueError('本页自动接续只用于官方独立工作；有限任务沿用原计划机制。')
        if t['spec'].get('execution')=='partner_native':raise ValueError(PARTNER_CONTINUATION_REASON)
        if not self.s.independent.describe(t['spec']['provider'])['native_resume']:raise ValueError('此工作端不支持当前原生接续。')
        a=db.execute('SELECT * FROM attempts WHERE task_id=? ORDER BY started DESC LIMIT 1',(identity,)).fetchone()
        a=self.s.store.attempt_row(a) if a else None
        if not a or a['state']!='COMPLETED' or not self.s.operations.exit_proved(a):raise ValueError('请先完成并核对工作回合。')
        return t,a

    def schedule_save(self,db,p):
        if p.get('confirm') is not True:raise ValueError('请确认计划将自动发送工作消息。')
        t,a=self.eligible(db,p.get('task_id'));frequency=p.get('frequency')
        if frequency not in {'once','daily','weekly'}:raise ValueError('请选择一次、每天或每周。')
        timezone=p.get('timezone','Asia/Shanghai');zone(self.s.locations,timezone)
        due=p.get('due');now=time.time()
        if p.get('local_time'):
            local=datetime.fromisoformat(p['local_time'])
            if local.tzinfo is not None:raise ValueError('请用所选时区的本地时间，不附加其它偏移。')
            aware=local.replace(tzinfo=zone(self.s.locations,timezone));due=aware.timestamp()
            if datetime.fromtimestamp(due,aware.tzinfo).replace(tzinfo=None)!=local:raise ValueError('该时间落在夏令时跳过区间，请选择其它时间。')
        if type(due) not in (int,float) or not now+2<=due<=now+366*86400:raise ValueError('首次执行须在未来一年内。')
        row={'id':uid(),'revision':uid(),'task_id':t['id'],'title':t['title'],'binding':t['spec_hash'],'attempt_id':a['id'],
             'frequency':frequency,'timezone':timezone,'wall_time':datetime.fromtimestamp(due,zone(self.s.locations,timezone)).strftime('%H:%M:%S'),'due':due,'max_runs':integer(p.get('max_runs',30),1,1000,'最多执行次数'),
             'content':bounded_text(p.get('content'),'计划消息',4000),'credential_revision':self.s.locations.credential_revision,'state':'RUNNING','reason':'','runs':[],'created':now}
        self.c.put(db,'scheduled_job',row);return {'job':row,'no_dispatch':True,'service_must_be_running':True}

    def goal_save(self,db,p):
        if p.get('confirm') is not True:raise ValueError('请确认目标会连续调用所选官方工作端。')
        if any(k in p for k in ('connection_id','connection_revision','partner_connection','resource_selection','parser_reference')):
            raise ValueError(PARTNER_CONTINUATION_REASON)
        project=p.get('project_id');self.s.file_inspector.project(db,project)
        provider=p.get('provider','codex')
        from .independent import SERVICES
        if provider not in SERVICES:raise ValueError(PARTNER_CONTINUATION_REASON)
        defaults=self.s.profiles.defaults(provider)
        from .native_permissions import choice
        access=choice(p.get('access'));checks=p.get('checks',[])
        if not isinstance(checks,list) or len(checks)>20:raise ValueError('完成文件检查最多20项。')
        for check in checks:
            if not isinstance(check,dict) or set(check)-{'file','contains'}:raise ValueError('完成条件仅接受文件路径及应包含的文字。')
            workspace.relative(check.get('file',''))
            if not isinstance(check.get('contains',''),str) or len(check.get('contains',''))>4000:raise ValueError('检查文字过长。')
        row={'id':uid(),'revision':uid(),'title':bounded_text(p.get('title'),'目标名称',160),'goal':bounded_text(p.get('goal'),'目标',12000),
             'criteria':bounded_text(p.get('criteria'),'完成标准',8000),'project_id':project,'provider':provider,'config':defaults,'access':access,
             'max_rounds':integer(p.get('max_rounds',20),0,1000,'最多自动回合'),'checks':checks,'state':'PAUSED','reason':'尚未启动',
             'credential_revision':self.s.locations.credential_revision,'rounds':[],'task_id':None,'created':time.time()}
        self.c.put(db,'goal_job',row);return {'job':row,'no_dispatch':True}

    def state(self,db,p):
        kind,row=self.checked(db,p);state=p.get('state')
        if state not in {'RUNNING','PAUSED','CANCELLED'} or row['state'] in {'COMPLETE','MODEL_COMPLETE_REVIEW','CANCELLED'}:raise ValueError('此状态不能继续操作。')
        if state=='RUNNING':
            if p.get('confirm') is not True:raise ValueError('请明确确认自动运行。')
            if row.get('partner_connection') or row.get('connection_id'):
                raise ValueError(PARTNER_CONTINUATION_REASON)
            if row.get('task_id'):
                if self.s.store.task(row['task_id'],db)['spec'].get('execution')=='partner_native':
                    raise ValueError(PARTNER_CONTINUATION_REASON)
                self.s.monitor.guard(db,{'task_id':row['task_id']});self.s.work_records.guard(db,'task',row['task_id'])
            if kind=='scheduled_job' and row['due']<=time.time():
                if row['frequency']=='once':raise ValueError('一次计划已错过，请新建执行时间，不补发。')
                row['due']=next_due(self.s.locations,row,time.time())
        if state=='RUNNING':row['credential_revision']=self.s.locations.credential_revision
        row.update(state=state,revision=uid(),reason='' if state=='RUNNING' else '仅暂停后续派发；已运行回合请在运行监控中停止。')
        self.c.put(db,kind,row);return {'job':row,'no_dispatch':True}

    def prompt(self,row):
        return row['goal']+'\n\n完成标准：'+row['criteria']+'\n继续完成目标，核对实际结果。仅在确认完成上述目标后，在回复最后单独一行输出 '+MARKER+'。未完成则报告进度并继续下一步。不要用完成标记代替实际操作或检查。'

    def advance(self,db,p):
        kind,row=self.checked(db,p)
        if row['state']!='RUNNING':return {'no_dispatch':True}
        if row.get('partner_connection') or row.get('connection_id'):
            raise ValueError(PARTNER_CONTINUATION_REASON)
        if row.get('credential_revision')!=self.s.locations.credential_revision:raise ValueError('账号引用已变化，请核对后重新确认自动计划。')
        if kind=='scheduled_job' and row['due']>time.time():return {'no_dispatch':True}
        if kind=='scheduled_job' and time.time()-row['due']>300:
            row.setdefault('skipped',[]).append(row['due'])
            row.update(state='PAUSED' if row['frequency']=='once' else 'RUNNING',reason='错过执行窗口，已跳过，未补发。',revision=uid())
            if row['frequency']!='once':row['due']=next_due(self.s.locations,row,time.time())
            self.c.put(db,kind,row);return {'job':row,'no_dispatch':True}
        scope=None
        if row.get('task_id'):
            task=self.s.store.task(row['task_id'],db);self.s.monitor.guard(db,{'task_id':task['id']});self.s.work_records.guard(db,'task',task['id'])
            if task['spec'].get('execution')=='partner_native':raise ValueError(PARTNER_CONTINUATION_REASON)
            a=db.execute('SELECT * FROM attempts WHERE task_id=? ORDER BY started DESC LIMIT 1',(task['id'],)).fetchone();a=self.s.store.attempt_row(a)
            worker=self.s.workers.get(a['id'])
            if a['state'] in ACTIVE or worker and worker['thread'].is_alive():return {'no_dispatch':True}
            if a['id']!=row['attempt_id'] or task['spec_hash']!=row['binding']:raise ValueError('工作已由其它操作接续或更改，计划未自动跟随。')
            if a['state']!='COMPLETED' or not self.s.operations.exit_proved(a) or self.s.store.usage(task=task['id'],db=db)['unknown_requests']:raise ValueError('旧回合失败、中断或状态未知，请核对后重新安排。')
            project=db.execute('SELECT root FROM projects WHERE id=?',(task['project_id'],)).fetchone()
            if Path(project['root']).resolve()!=Path(a['workspace']).resolve():raise ValueError('项目目录已重新定位，请在新目录新建工作再安排自动任务。')
            scope={'task_id':task['id'],'attempt_id':a['id'],'owner':a['owner']}
            if kind=='goal_job':
                answer=(a.get('result') or {}).get('answer','');declared=isinstance(answer,str) and answer.rstrip().splitlines()[-1:]==[MARKER]
                checks=[]
                for item in row['checks']:
                    try:
                        data=workspace.read_blob(a['workspace'],item['file']);passed=not item.get('contains') or item['contains'] in data['raw'].decode('utf8')
                        checks.append({'file':item['file'],'passed':passed,'sha256':data['sha256']})
                    except (ValueError,OSError,UnicodeError):checks.append({'file':item['file'],'passed':False})
                row['last_check']=checks
                if declared and all(c['passed'] for c in checks):
                    saved=self.s.results.capture(db,{**scope,'files':[c['file'] for c in row['checks']]})
                    if any(saved['result']['snapshot']['manifest'][c['file']]['sha256']!=c['sha256'] for c in checks):raise ValueError('完成检查后文件版本发生变化，未记录目标完成。')
                    row.update(state='COMPLETE' if checks else 'MODEL_COMPLETE_REVIEW',reason='完成文件条件通过；待人工验收。' if checks else '模型报告完成；未提供机器可核对条件，待人工验收。',result_id=saved['result']['id'],revision=uid())
                    self.c.put(db,kind,row);return {'job':row,'no_dispatch':True}
        if kind=='goal_job' and row['max_rounds'] and len(row['rounds'])>=row['max_rounds']:raise ValueError('已达到目标任务的自动回合上限，未继续派发。')
        if kind=='scheduled_job':payload={**scope,'goal':row['content']}
        elif scope:payload={**scope,'goal':self.prompt(row)}
        else:payload={'project_id':row['project_id'],'provider':row['provider'],**row['config'],'access':row['access'],'goal':self.prompt(row)}
        launch=self.s.file_inspector.send(db,payload)
        if kind=='goal_job' and not scope:db.execute('UPDATE tasks SET title=? WHERE id=?',(row['title'],launch['task_id']))
        row.update(task_id=launch['task_id'],attempt_id=launch['launch_attempt'],binding=launch['spec_hash'],revision=uid(),reason='')
        row['runs' if kind=='scheduled_job' else 'rounds'].append({'attempt_id':launch['launch_attempt'],'at':time.time()})
        if kind=='scheduled_job':
            if row['frequency']=='once' or len(row['runs'])>=row['max_runs']:row['state']='COMPLETE'
            else:row['due']=next_due(self.s.locations,row,time.time())
        self.c.put(db,kind,row);self.s.store.event(row['task_id'],row['attempt_id'],'automatic_job_dispatched',{'job_id':row['id'],'kind':kind},db)
        return {**launch,'job':row}

    def tick(self):
        self.cooperative.tick()
        with self.s.lock:
            if self.s.retiring:return
            with self.s.store.read() as db:rows=[(k,r) for k in ('scheduled_job','goal_job') for r in self.c.all(db,k) if r['state']=='RUNNING']
            for kind,row in rows:
                if any(w['thread'].is_alive() for w in self.s.workers.values()):return
                if kind=='scheduled_job' and row['due']>time.time():continue
                with self.s.store.read() as db:
                    a=db.execute('SELECT id,state FROM attempts WHERE task_id=? ORDER BY started DESC LIMIT 1',(row.get('task_id'),)).fetchone()
                    worker=self.s.workers.get(a['id']) if a else None
                    frontier=[a['id'],a['state'],bool(worker and worker['thread'].is_alive())] if a else None
                identity=str(uuid.uuid5(uuid.NAMESPACE_URL,digest([kind,row['id'],row['revision'],frontier])))
                try:self.s.command('planning.advance',{'kind':kind,'id':row['id'],'revision':row['revision']},identity)
                except (ValueError,OSError,LookupError) as exc:
                    with self.s.store.write() as db:
                        current=self.c.get(db,kind,row['id']);current.update(state='PAUSED',revision=uid(),reason=str(exc)[:1000]);self.c.put(db,kind,current)
