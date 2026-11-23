"""Continuous work objectives and record references, separate from execution."""
import json
import math
import re
import time
from urllib.parse import urlsplit
from .contracts import bounded_text
from .store import ACTIVE, UNKNOWN, canonical, digest, uid


STATES={'OPEN','PAUSED','READY_FOR_ACCEPTANCE','ACCEPTED','CANCELLED'}
TEXT_FIELDS={'title':160,'goal':16000,'constraints_text':16000,'acceptance':16000,'progress':16000,'next_step':16000}
TOOL_PAGE_SIZE = 20
TOOL_LABEL = """COALESCE(json_extract(e.payload,'$.item.name'),
    json_extract(e.payload,'$.item.tool_name'), json_extract(e.payload,'$.item.tool'),
    json_extract(e.payload,'$.item.type'))"""
TOOL_OUTCOME = """CASE
    WHEN t.state!='COMPLETED' THEN t.state
    WHEN json_extract(t.result,'$.not_dispatched')=1
        OR json_extract(t.result,'$.status') IN ('declined','rejected') THEN 'REJECTED'
    WHEN json_extract(t.result,'$.is_error')=1 OR json_extract(t.result,'$.isError')=1
        OR json_extract(t.result,'$.message.isError')=1
        OR json_extract(t.result,'$.status') IN ('failed','error')
        OR (json_type(t.result,'$.exitCode')='integer' AND json_extract(t.result,'$.exitCode')!=0) THEN 'FAILED'
    WHEN json_extract(t.result,'$.status') IN ('cancelled','canceled','interrupted') THEN 'CANCELLED'
    ELSE t.state END"""
SENSITIVE_TOOL_TEXT = re.compile(
    r'(?i)\b(?:bearer|authorization|password|passphrase|credentials?|secrets?|'
    r'(?:api|client|private)[_ -]?key|(?:access|auth|refresh|session|id)[_ -]?token)\b'
    r'|\b(?:sk-[\w-]{8,}|gh[pousr]_[\w]{8,}|github_pat_[\w]{8,}|AKIA[A-Z0-9]{16})\b'
    r'|(?:^|[\\/])(?:\.env(?:[.\\/]|$)|\.ssh(?:[\\/]|$)|\.aws(?:[\\/]|$)|'
    r'\.npmrc(?:[\\/]|$)|\.netrc(?:[\\/]|$)|backend\.json(?:$|[\\/])|auth\.json(?:$|[\\/]))'
)
TOOL_NAME_TEXT = re.compile(r'[\w .:/-]{1,160}\Z')


class WorkItems:
    def __init__(self,service):self.s=service

    def handlers(self):
        return {'work-item.create':self.create,'work-item.update':self.update,
                'work-item.link':self.link,'work-item.chat-create':self.chat_create,
                'work-item.prepare':self.prepare}

    def project(self,db,identity):
        if identity is not None:
            if not isinstance(identity,str) or not db.execute('SELECT 1 FROM projects WHERE id=?',(identity,)).fetchone():
                raise ValueError('目标项目不存在。')
            self.s.work_records.guard_project(db,identity)
        return identity

    def item(self,db,identity):
        if not isinstance(identity,str):raise ValueError('工作任务身份无效。')
        row=db.execute('SELECT * FROM work_items WHERE id=?',(identity,)).fetchone()
        if not row:raise ValueError('工作任务不存在。')
        return dict(row)

    def checked(self,db,p):
        row=self.item(db,p.get('id'))
        self.s.work_records.guard(db,'item',row['id'])
        if type(p.get('revision')) is not int or row['revision']!=p['revision']:
            raise ValueError('工作任务已变化，请刷新后再操作。')
        return row

    def text(self,name,value):
        if name in {'title','goal'}:return bounded_text(value,{'title':'任务名称','goal':'任务目标'}[name],TEXT_FIELDS[name])
        if not isinstance(value,str) or len(value)>TEXT_FIELDS[name]:raise ValueError('工作要求文字无效或过长。')
        return value.strip()

    def save(self,db,row,action):
        snapshot={**row,'record_refs':[dict(r) for r in db.execute('SELECT kind,record_id,source_revision FROM work_item_records WHERE item_id=? ORDER BY kind,record_id',(row['id'],))]}
        db.execute('INSERT INTO work_item_versions VALUES(?,?,?)',(row['id'],row['revision'],canonical(snapshot)))
        self.s.store.event(None,None,'work_item_'+action,{'work_item_id':row['id'],'revision':row['revision'],'no_dispatch':True},db)
        return {'work_item':row,'no_dispatch':True,'execution_unchanged':True}

    def create(self,db,p):
        if set(p)-set(TEXT_FIELDS)-{'project_id'}:raise ValueError('工作任务字段不支持。')
        now=time.time();row={'id':uid(),'project_id':self.project(db,p.get('project_id')),
            **{name:self.text(name,p.get(name,'')) for name in TEXT_FIELDS},'state':'OPEN','revision':1,'created':now,'updated':now}
        db.execute('INSERT INTO work_items VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',tuple(row[k] for k in
            ('id','project_id','title','goal','constraints_text','acceptance','progress','next_step','state','revision','created','updated')))
        return self.save(db,row,'created')

    def update(self,db,p):
        if set(p)-{'id','revision','changes'}:raise ValueError('工作任务更新字段不支持。')
        row=self.checked(db,p);changes=p.get('changes')
        if not isinstance(changes,dict) or not changes or set(changes)-set(TEXT_FIELDS)-{'state','project_id'}:
            raise ValueError('工作任务更新内容无效。')
        for name,value in changes.items():
            if name=='project_id':value=self.project(db,value)
            elif name=='state':
                if not isinstance(value,str) or value not in STATES:raise ValueError('工作任务状态无效。')
            else:value=self.text(name,value)
            row[name]=value
        row.update(revision=row['revision']+1,updated=time.time())
        names=[*TEXT_FIELDS,'project_id','state','revision','updated']
        db.execute('UPDATE work_items SET '+','.join(n+'=?' for n in names)+' WHERE id=?',[*(row[n] for n in names),row['id']])
        return self.save(db,row,'updated')

    def record(self,db,kind,identity):
        if not isinstance(kind,str) or not isinstance(identity,str) or not identity or len(identity)>200:
            raise ValueError('关联记录身份无效。')
        if kind=='chat':
            source=self.s.collaboration.get(db,'ui_chat',identity)
            return {'kind':kind,'id':source['id'],'title':source['title'],'revision':source['revision'],
                    'href':'#chat/'+source['id'],'state':'RUNNING' if source.get('pending') else 'SAVED'}
        if kind not in {'task','dialogue'}:raise ValueError('关联记录类型无效。')
        source=self.s.work_records.source(db,kind,identity)
        view=self.s.work_records.view(db,kind,source)
        if view['deleted']:raise ValueError('该记录已删除。')
        return {**view,'href':('#control/task/' if source.get('work_mode')=='cooperative' and kind=='task' else '#work/'+kind+'/')+identity}

    def link(self,db,p):
        if set(p)-{'id','revision','kind','record_id','record_revision'}:raise ValueError('记录关联字段不支持。')
        row=self.checked(db,p);record=self.record(db,p.get('kind'),p.get('record_id'))
        if record['revision']!=p.get('record_revision'):raise ValueError('关联记录已变化，请重新选择。')
        exists=db.execute('SELECT 1 FROM work_item_records WHERE item_id=? AND kind=? AND record_id=?',
            (row['id'],record['kind'],record['id'])).fetchone()
        if exists:return {'work_item':row,'no_dispatch':True,'already_linked':True}
        db.execute('INSERT INTO work_item_records VALUES(?,?,?,?,?)',(row['id'],record['kind'],record['id'],canonical(record['revision']),time.time()))
        row.update(revision=row['revision']+1,updated=time.time())
        db.execute('UPDATE work_items SET revision=?,updated=? WHERE id=?',(row['revision'],row['updated'],row['id']))
        return self.save(db,row,'linked')

    def chat_create(self,db,p):
        if set(p)-{'project_id','title','work_item_id','revision','provider','route'}:raise ValueError('项目对话字段不支持。')
        project=self.project(db,p.get('project_id'))
        item=None
        if p.get('work_item_id'):
            item=self.checked(db,{'id':p['work_item_id'],'revision':p.get('revision')})
            if item['project_id']!=project:raise ValueError('工作任务与目标项目不一致。')
        result=self.s.ui_features.chats.chat_create(db,{'provider':p.get('provider','deepseek'),'route':p.get('route','api'),'title':p.get('title') or '项目新对话'})
        chat=result['chat'];db.execute('INSERT INTO record_scopes VALUES(?,?,?)',('chat',chat['id'],project))
        if item:self.link(db,{'id':item['id'],'revision':item['revision'],'kind':'chat','record_id':chat['id'],'record_revision':chat['revision']})
        return {**result,'project_id':project,'work_item_id':item['id'] if item else None}

    def prepare(self,db,p):
        if set(p)-{'id','revision','member_id'}:raise ValueError('任务接续字段不支持。')
        item=self.checked(db,p);self.s.dispatch.guard(db,item,set())
        selected=self.s.resources.selection(db,p['member_id']) if p.get('member_id') else None
        refs=[]
        for r in db.execute('SELECT * FROM work_item_records WHERE item_id=? ORDER BY created',(item['id'],)):
            try:record=self.record(db,r['kind'],r['record_id'])
            except ValueError:record={'kind':r['kind'],'id':r['record_id'],'title':'原记录已删除或不可用','unavailable':True}
            if r['kind']=='task' and not record.get('unavailable'):
                last=db.execute('SELECT * FROM attempts WHERE task_id=? ORDER BY started DESC LIMIT 1',(r['record_id'],)).fetchone()
                if last:
                    a=self.s.store.attempt_row(last);result=a.get('result') or {}
                    record['execution_evidence']={'attempt_id':a['id'],'state':a['state'],
                        'answer_excerpt':str(result.get('answer',''))[:6000],'manifest':result.get('manifest'),
                        'known_tools_exited':result.get('known_tools_exited')}
            refs.append(record)
        handoff={'id':'handoff:'+uid(),'item_id':item['id'],'item_revision':item['revision'],
                 'target':'independent','sources':refs,'created':time.time(),'dispatch_id':None}
        prompt='\n\n'.join(label+'：\n'+item[key] for label,key in [('任务','title'),('目标','goal'),
            ('约束与已确认要求','constraints_text'),('完成要求','acceptance'),('已有进展','progress'),('下一步','next_step')] if item[key])
        prompt+='\n\n任务来源：'+item['id']+'，修订 '+str(item['revision'])
        if refs:prompt+='\n关联记录（按需从 Workdesk 原记录及文件核对）：\n'+'\n'.join(
            r['kind']+' / '+r['id']+' / '+r['title']+(' / 不可用' if r.get('unavailable') else '') for r in refs)
        for record in refs:
            if record.get('execution_evidence'):
                prompt+='\n\n已有执行依据（回答最多摘录6000字，文件按当前工作目录核对）：\n'+canonical(record['execution_evidence'])
        prompt+='\n\n继续完成本任务，保留已有成果；报告实际完成、检查结果和待解决事项。'
        reference={'id':item['id'],'revision':item['revision'],'handoff_id':handoff['id']}
        draft=self.s.ui_features.draft_save(db,{'id':'work-item-draft:'+uid(),'value':{
            'mode':'work','type':'independent','scene':'research','project':item['project_id'] or '',
            'provider':selected['member']['provider'] if selected else 'codex',
            'model':selected['member']['model'] if selected else '',
            'workEffort':selected['member']['effort'] if selected else '',
            'workTier':selected['member']['tier'] if selected else '',
            'title':item['title'],'input':prompt,'files':'','attachments':[],
            'formDraft':{'workItem':reference,**({'resourceSelection':selected['reference'],
                'resourceName':selected['member']['name']} if selected else {})}}})['draft']
        self.s.collaboration.put(db,'work_handoff',handoff)
        return {'draft':draft,'handoff':handoff,'work_item':item,'no_dispatch':True}

    def teamwork_history(self, db, item):
        """Read saved plans and exact-attempt evidence for the task detail."""
        from .teamwork import frozen_access
        from .work_leader import organization

        plans = []
        for row in db.execute("""SELECT data FROM collaboration WHERE kind='teamwork_plan'
                AND json_extract(data,'$.item_id')=? ORDER BY json_extract(data,'$.created'), id""",
                (item['id'],)):
            plan = json.loads(row['data'])
            try:
                permissions = organization(self.s, db, plan) or plan
            except ValueError:
                permissions = None
            units = [{**{key: unit.get(key) for key in ('key', 'title', 'role', 'goal', 'acceptance', 'depends', 'outputs')},
                      'member': {key: unit['member'].get(key) for key in ('name', 'provider', 'model')},
                      'access': frozen_access(permissions, unit['member']) if permissions is not None else None}
                     for unit in plan['units']]
            plans.append({**{key: plan.get(key) for key in ('id', 'version', 'mode', 'created', 'previous_plan')},
                          'reason': (plan.get('transition') or {}).get('reason'), 'units': units})
        evidence = {}
        for run in item['cooperation']['runs']:
            result = run.get('result') or {}
            if not result.get('exact_identity'):
                continue
            args = (run['task_id'], run['attempt_id'])
            tools = self.tool_page(db, *args)
            plan = ''
            if result.get('session_id') and result.get('turn_id'):
                row = db.execute("""SELECT payload FROM events WHERE task_id=? AND attempt_id=?
                    AND kind='native_event' AND json_extract(payload,'$.params.threadId')=?
                    AND json_extract(payload,'$.params.turnId')=?
                    AND (json_extract(payload,'$.method')='turn/plan/updated'
                         OR (json_extract(payload,'$.method')='item/completed'
                             AND json_extract(payload,'$.params.item.type')='plan'))
                    ORDER BY seq DESC LIMIT 1""", (*args, result['session_id'], result['turn_id'])).fetchone()
                if row:
                    params = json.loads(row['payload'])['params']
                    text = (params.get('item') or {}).get('text')
                    plan = text if isinstance(text, str) else '\n'.join(
                        step['step'] for step in (params.get('plan') or [])
                        if isinstance(step, dict) and isinstance(step.get('step'), str))
            evidence[run['id']] = {'plan': plan[:12000], 'tools': tools['tools'],
                                  'more_tools': tools['more']}
        return {'plans': plans, 'evidence': evidence}

    def observed_run(self, db, identity, run_identity):
        self.item(db, identity)
        self.s.work_records.guard(db, 'item', identity)
        run = self.s.collaboration.get(db, 'work_unit_run', run_identity)
        if run['item_id'] != identity:
            raise ValueError('这个回合不属于当前合作任务。')
        attempt, task, _, exact = self.s.work_units.recorded_attempt(db, run)
        return run, attempt, task, exact

    def tool_page(self, db, task_id, attempt_id, before=None):
        # The first observed event identifies the call, even if a later
        # callback repeats it. Pagination remains stable as new calls arrive.
        rows = db.execute(f"""WITH page AS (
            SELECT t.id, MIN(e.seq) AS seq FROM events e
            JOIN tool_calls t ON t.id=json_extract(e.payload,'$.call_id') AND t.attempt_id=e.attempt_id
            WHERE e.task_id=? AND e.attempt_id=? AND e.kind='native_tool_started'
            GROUP BY t.id HAVING ? IS NULL OR MIN(e.seq)<? ORDER BY seq DESC LIMIT ?
        )
        SELECT t.id, page.seq, e.at, t.state, {TOOL_LABEL} AS label, {TOOL_OUTCOME} AS outcome
        FROM page JOIN events e ON e.seq=page.seq JOIN tool_calls t ON t.id=page.id
        ORDER BY page.seq DESC""", (task_id, attempt_id, before, before, TOOL_PAGE_SIZE + 1)).fetchall()
        tools = [{**dict(row), 'label': self.tool_label(row['label'])}
                 for row in reversed(rows[:TOOL_PAGE_SIZE])]
        more = len(rows) > TOOL_PAGE_SIZE
        return {'tools': tools, 'more': more, 'next_before': tools[0]['seq'] if more else None}

    def trajectory(self, identity, run_identity, before=None):
        if before is not None:
            before = int(before)
            if not 0 < before <= 2**63-1:
                raise ValueError('轨迹位置无效。')
        with self.s.store.read() as db:
            run, attempt, _, exact = self.observed_run(db, identity, run_identity)
            if not exact:
                raise ValueError('这个回合的执行身份暂无法核对。')
            return self.tool_page(db, run['task_id'], attempt['id'], before)

    def tool_record(self, identity, run_identity, call_id):
        with self.s.store.read() as db:
            run, attempt, task, exact = self.observed_run(db, identity, run_identity)
            if not exact:
                raise ValueError('这个回合的执行身份暂无法核对。')
            row = db.execute(f"""SELECT e.seq, e.at, e.payload, {TOOL_LABEL} AS label,
                {TOOL_OUTCOME} AS outcome, t.state, t.result
                FROM events e JOIN tool_calls t ON t.id=json_extract(e.payload,'$.call_id')
                    AND t.attempt_id=e.attempt_id
                WHERE e.task_id=? AND e.attempt_id=? AND e.kind='native_tool_started' AND t.id=?
                ORDER BY e.seq LIMIT 1""", (run['task_id'], attempt['id'], call_id)).fetchone()
            if not row:
                raise ValueError('这条工具记录不属于所选回合。')
            item = json.loads(row['payload']).get('item')
            item = item if isinstance(item, dict) else {}
            result = json.loads(row['result']) if row['result'] else {}
            result = result if isinstance(result, dict) else {}
            saved = {}
            for source, name in (('exitCode', 'exit_code'), ('durationMs', 'duration_ms'),
                                 ('completedAtMs', 'completed_at_ms')):
                value = result.get(source)
                if type(value) is int:
                    saved[name] = value
            return {
                'call_id': call_id, 'label': self.tool_label(row['label']), 'state': row['state'],
                'outcome': row['outcome'], 'at': row['at'],
                'input': self.tool_input(item),
                'output': self.tool_output(result, saved),
                'receipt': {'provider': task['spec']['provider'], 'seq': row['seq'],
                            'state': row['state'], 'result_saved': row['result'] is not None, **saved}
            }

    @staticmethod
    def tool_label(value):
        if isinstance(value, str) and TOOL_NAME_TEXT.fullmatch(value) and not SENSITIVE_TOOL_TEXT.search(value):
            return value
        return None

    @staticmethod
    def tool_input(item):
        args = item.get('input') or item.get('arguments') or {}
        args = args if isinstance(args, dict) else {}
        lines = []
        description = args.get('description')
        if isinstance(description, str) and len(description) <= 180 and description.isprintable() and not SENSITIVE_TOOL_TEXT.search(description):
            lines.append(description[:180])
        url = args.get('url')
        if isinstance(url, str):
            try:
                parts = urlsplit(url[:4096])
                if parts.hostname and parts.scheme in {'http', 'https'} and not SENSITIVE_TOOL_TEXT.search(parts.hostname):
                    lines.append('网站 ' + parts.hostname[:253])
            except ValueError:
                pass
        paths = [args.get(key) for key in ('file_path', 'filePath', 'path')] + [item.get('path')]
        changes = item.get('changes')
        if isinstance(changes, list):
            paths.extend(change.get('path') for change in changes[:8] if isinstance(change, dict))
        lines.extend('文件 ' + path[:260] for path in dict.fromkeys(path for path in paths
                     if isinstance(path, str) and path.isprintable() and not SENSITIVE_TOOL_TEXT.search(path)))
        return '\n'.join(lines) or None

    @staticmethod
    def tool_output(result, receipt):
        lines = []
        if 'exit_code' in receipt:
            lines.append('退出码 ' + str(receipt['exit_code']))
        path = result.get('savedPath')
        if isinstance(path, str) and path.isprintable() and not SENSITIVE_TOOL_TEXT.search(path):
            lines.append('已保存 ' + path[:260])
        # Show the shape of the saved reply. Tool output may contain a whole
        # file, environment or provider payload, even for ordinary commands.
        message = result.get('message')
        content = message.get('content') if isinstance(message, dict) else result.get('content')
        stdout = result.get('aggregatedOutput')
        size = len(stdout) if isinstance(stdout, str) else 0
        if isinstance(content, str):
            size += len(content)
        elif isinstance(content, list):
            size += sum(len(block['text']) for block in content if isinstance(block, dict)
                        and block.get('type') == 'text' and isinstance(block.get('text'), str))
        if size:
            lines.append(f'已记录 {size} 字符的文本输出')
        return '\n'.join(lines) or None

    def public_thoughts(self, identity, run_identity):
        with self.s.store.read() as db:
            run, attempt, task, exact = self.observed_run(db, identity, run_identity)
            provider = task['spec']['provider']
            notes = []
            if exact and attempt['session_id']:
                if provider == 'codex' and attempt['turn_id']:
                    notes = self.codex_summaries(db, attempt)
                elif provider in {'qwen', 'deepseek'}:
                    notes = self.sdk_thoughts(db, attempt, provider)
            return {'notes': notes[-80:], 'more': len(notes) > 80, 'provider': provider,
                    'run_id': run['id'], 'no_dispatch': True}

    def codex_summaries(self, db, attempt):
        summaries = {}
        rows = db.execute("""SELECT at,payload FROM events WHERE task_id=? AND attempt_id=?
            AND kind='native_event' AND json_extract(payload,'$.params.threadId')=?
            AND json_extract(payload,'$.params.turnId')=?
            AND (json_extract(payload,'$.method')='item/reasoning/summaryTextDelta'
                OR (json_extract(payload,'$.method')='item/completed'
                    AND json_extract(payload,'$.params.item.type')='reasoning')) ORDER BY seq""",
            (attempt['task_id'], attempt['id'], attempt['session_id'], attempt['turn_id']))
        for row in rows:
            payload = json.loads(row['payload'])
            params = payload['params']
            native = params.get('item') or {}
            identity = native.get('id') or params.get('itemId')
            if not isinstance(identity, str):
                continue
            if payload['method'] == 'item/reasoning/summaryTextDelta':
                index, delta = params.get('summaryIndex', 0), params.get('delta')
                if isinstance(index, int) and index >= 0 and isinstance(delta, str):
                    part = summaries.setdefault(identity, {}).setdefault(index, {'at': row['at'], 'text': ''})
                    part['text'] = (part['text'] + delta)[:12000]
            elif isinstance(native.get('summary'), list):
                # Codex can emit multiple kinds of events for a reasoning item.
                # The public summary fields are the only ones served here.
                parts = [part if isinstance(part, str) else part.get('text') if
                         isinstance(part, dict) and part.get('type') == 'summary_text' else None
                         for part in native['summary']]
                text = '\n'.join(part for part in parts if isinstance(part, str) and part).strip()
                if text:
                    summaries[identity] = {0: {'at': row['at'], 'text': text[:12000]}}
        return sorted((sections[index] for sections in summaries.values() for index in sorted(sections)
                       if sections[index]['text'].strip()), key=lambda part: part['at'])

    def sdk_thoughts(self, db, attempt, provider):
        scope = (attempt['task_id'], attempt['id'], attempt['session_id'])
        if provider == 'qwen':
            rows = db.execute("""SELECT at, json_extract(payload,'$.message') AS message FROM events
                WHERE task_id=? AND attempt_id=? AND kind='model_event'
                AND json_extract(payload,'$.session_id')=? AND json_extract(payload,'$.type')='assistant'
                AND json_extract(payload,'$.parent_tool_use_id') IS NULL
                AND json_extract(payload,'$.turn_id') IS ? ORDER BY seq""", (*scope, attempt['turn_id']))
            block_type, field = 'thinking', 'thinking'
        else:
            start = db.execute("""SELECT seq,json_extract(payload,'$.payload.event.data.turn') AS turn FROM events
                WHERE task_id=? AND attempt_id=? AND kind='model_event'
                AND json_extract(payload,'$.payload.sessionId')=?
                AND json_extract(payload,'$.method')='session.event'
                AND json_extract(payload,'$.payload.event.type')='turn/start' LIMIT 2""", scope).fetchall()
            if len(start) != 1 or type(start[0]['turn']) is not int:
                return []
            rows = db.execute("""SELECT at,json_extract(payload,'$.payload.event.data.message') AS message FROM events
                WHERE task_id=? AND attempt_id=? AND kind='model_event'
                AND json_extract(payload,'$.payload.sessionId')=?
                AND json_extract(payload,'$.method')='session.event'
                AND json_extract(payload,'$.payload.event.type')='assistant/message'
                AND seq>? AND json_extract(payload,'$.payload.event.data.turn')=? ORDER BY seq""",
                (*scope, start[0]['seq'], start[0]['turn']))
            block_type, field = 'reasoning', 'text'
        notes = {}
        for row in rows:
            message = json.loads(row['message']) if row['message'] else {}
            if not isinstance(message, dict) or message.get('role') != 'assistant' or not isinstance(message.get('id'), str):
                continue
            content = message.get('content')
            if not isinstance(content, list):
                continue
            for index, part in enumerate(content):
                if isinstance(part, dict) and part.get('type') == block_type:
                    text = part.get(field)
                    if isinstance(text, str) and text.strip():
                        notes[(message['id'], index)] = {'at': row['at'], 'text': text[:12000]}
        return list(notes.values())

    def acceptance_notices(self):
        with self.s.store.read() as db:
            rows = db.execute("""SELECT item.id, item.title, item.state, item.updated
                FROM work_items AS item
                LEFT JOIN projects AS project ON project.id = item.project_id
                LEFT JOIN collaboration AS meta
                    ON meta.kind = 'work_project' AND meta.id = 'work-project:' || project.id
                WHERE item.state = 'READY_FOR_ACCEPTANCE' AND """+self.s.work_records.visible('item','item.id')+"""
                    AND (item.project_id IS NULL OR (project.id IS NOT NULL
                        AND NOT COALESCE(json_extract(meta.data, '$.removed'), 0)))
                ORDER BY item.updated DESC, item.id LIMIT 24""")
            return {'work_items': [dict(row) for row in rows]}

    def project_entry(self, db, project_id, items):
        """Summarize project tasks and resolve its single-task landing when available."""
        from .record_catalog import CTE, PROJECT_SCOPE

        for item in items:
            try:
                plan = self.s.teamwork.current(db, item['id'])
            except (KeyError, ValueError, TypeError):
                plan = None
            if plan and plan.get('item_id') != item['id']:
                plan = None
            completed = None
            if plan:
                flow = self.s.work_flows.current(db, item['id'])
                completed = self.s.work_flows.latest_delivery(db, flow)
            conversation = self.s.cooperation_conversation
            item['cooperation_conversation'] = conversation.exists(db,item['id'])
            item['mode'] = plan.get('mode') if plan else conversation.row(db,item['id'])['mode'] if item['cooperation_conversation'] else None
            item['delivery_saved'] = bool(completed and completed.get('delivery'))

        if len(items) != 1 or items[0]['mode'] not in {'fixed', 'leader', 'host_experts'}:
            return None
        identity = items[0]['id']
        separate = db.execute(CTE + """SELECT 1 FROM records WHERE """ + PROJECT_SCOPE + """=?
            AND NOT EXISTS (
                SELECT 1 FROM work_item_records AS linked
                WHERE linked.item_id=? AND linked.kind=records.kind AND linked.record_id=records.id
            )
            AND (records.kind!='task' OR NOT EXISTS (
                SELECT 1 FROM collaboration AS run WHERE run.kind='work_unit_run'
                    AND json_extract(run.data,'$.item_id')=?
                    AND json_extract(run.data,'$.task_id')=records.id
            )) LIMIT 1""", (project_id, identity, identity)).fetchone()
        return None if separate else identity

    def leader_decisions(self, db, item_id, runs):
        """Read recorded coordination decisions without checking dispatch eligibility."""
        from .work_leader import PROTOCOL, WorkLeader
        from .work_leader_revisions import ACTIONS, PROTOCOL as REVISION_PROTOCOL

        states = {'dispatch': 'VALID', 'deliver': 'DELIVERY_READY', 'pause': 'PAUSE_SUGGESTED'}
        states.update({action: spec[0] for action, spec in ACTIONS.items()})
        coordinators = {run['id']: run for run in runs if run['input']['unit']['role'] == 'coordinate'}
        rows = db.execute("""SELECT data FROM collaboration WHERE kind='work_proposal'
            AND json_extract(data,'$.item_id')=? AND json_extract(data,'$.snapshot.protocol') IN (?,?)
            ORDER BY json_extract(data,'$.created') DESC,id DESC""", (item_id, PROTOCOL, REVISION_PROTOCOL))
        saved_plans, decisions = {}, {}
        for row in rows:
            try:
                proposal = json.loads(row[0])
                snapshot, at = proposal['snapshot'], proposal['created']
                if (not isinstance(snapshot, dict) or proposal['version'] != digest(snapshot) or snapshot.get('problems')
                        or type(at) not in (int, float) or not math.isfinite(at) or at <= 0):
                    continue
                source = snapshot['source']
                run_id = source['run_id']
                run = coordinators.get(run_id)
                if run is None or run_id in decisions:
                    continue
                plan_id = run['plan_id']
                if snapshot['plan'] != {'id': plan_id, 'version': run['plan_version']}:
                    continue
                if plan_id not in saved_plans:
                    saved_plans[plan_id] = self.s.collaboration.get(db, 'teamwork_plan', plan_id)
                plan = saved_plans[plan_id]
                if (plan['id'] != plan_id or plan['item_id'] != item_id or plan['mode'] != 'leader'
                        or plan['version'] != run['plan_version']):
                    continue
                attempt, _, result, exact = self.s.work_units.recorded_attempt(db, run)
                answer = result.get('answer')
                if (not exact or attempt['state'] != 'COMPLETED' or not isinstance(answer, str)
                        or snapshot['answer_digest'] != digest(answer) or snapshot['answer'] != answer):
                    continue
                expected = {key: attempt[key] for key in ('task_id', 'owner', 'session_id', 'turn_id')}
                expected.update(attempt_id=attempt['id'], connection_fingerprint=run['connection_fingerprint'],
                                result_digest=digest(result))
                if any(source.get(key) != value for key, value in expected.items()):
                    continue
                decision = WorkLeader.parse(answer, snapshot['protocol'])
                action = decision['action']
                if decision != snapshot['decision'] or snapshot['state'] != states.get(action):
                    continue
                titles = {unit['key']: unit['title'] for unit in plan['units']}
                keys = decision.get('unit_keys', [])
                if action == 'dispatch':
                    keys = [decision['unit_key']]
                elif action in ACTIONS:
                    changes = decision['changes']
                    keys = [change['unit_key'] for change in changes]
                    titles.update({change['unit_key']: change['definition']['title'] for change in changes
                                   if change.get('operation') == 'add'})
                decisions[run_id] = {'run_id': run_id, 'at': at, 'action': action, 'reason': decision['reason'],
                                     'member_name': run['input']['unit']['member']['name'],
                                     'unit_titles': [titles[key] for key in keys]}
            except (LookupError, OSError, OverflowError, TypeError, ValueError):
                continue
        return list(reversed(decisions.values()))

    def directory(self, db):
        """Summaries of saved, visible business tasks across projects."""
        rows = db.execute("""SELECT i.id, i.title, i.state, i.goal, i.next_step, i.project_id,
                   i.updated, COALESCE(plan.item_mode, json_extract(chat.data,'$.mode')) AS mode,
                   chat.id IS NOT NULL AS cooperation_conversation
            FROM work_items i
            LEFT JOIN projects AS project ON project.id=i.project_id
            LEFT JOIN collaboration AS meta ON meta.kind='work_project' AND meta.id='work-project:'||project.id
            LEFT JOIN (
                SELECT link.id, json_extract(p.data,'$.mode') AS item_mode
                FROM collaboration AS link JOIN collaboration AS p
                ON p.kind='teamwork_plan' AND p.id=json_extract(link.data,'$.plan_id')
                WHERE link.kind='teamwork_current'
            ) AS plan ON plan.id='teamwork-current:'||i.id
            LEFT JOIN collaboration AS chat ON chat.kind='work_cooperation_conversation'
                AND chat.id='cooperation-conversation:'||i.id
            WHERE """+self.s.work_records.visible('item','i.id')+"""
                AND (i.project_id IS NULL OR (project.id IS NOT NULL
                    AND NOT COALESCE(json_extract(meta.data,'$.removed'),0)))
            ORDER BY i.updated DESC,i.id""")
        items = [dict(row) for row in rows]
        pending_snapshots = dict(db.execute("""WITH latest AS (
            SELECT json_extract(data,'$.task_id') AS task_id,
                   json_extract(data,'$.decision') AS decision,
                   row_number() OVER (PARTITION BY json_extract(data,'$.task_id')
                       ORDER BY json_extract(data,'$.created') DESC,id DESC) AS position
            FROM collaboration WHERE kind='work_result'
        )
        SELECT ref.item_id, latest.task_id FROM work_item_records AS ref
        JOIN latest ON latest.task_id=ref.record_id
        JOIN tasks AS execution ON execution.id=ref.record_id
        WHERE ref.kind='task' AND latest.position=1 AND latest.decision IS NULL
            AND json_extract(execution.spec,'$.cooperative') IS NULL
            AND """+self.s.work_records.visible('task','ref.record_id')+
            ' ORDER BY ref.created,ref.record_id'))
        for item in items:
            item['needs_acceptance'] = item['state'] == 'READY_FOR_ACCEPTANCE'
            if item['state'] not in {'OPEN','READY_FOR_ACCEPTANCE'}:
                continue
            if item['mode']:
                flow = self.s.work_flows.current(db, item['id'])
                if flow and flow['state'] == 'RESULTS_AVAILABLE':
                    runs = self.s.work_units.rows(db, item['id']) if flow.get('delivery') is None else []
                    try:
                        item['delivery_saved'] = bool(self.s.work_flows.saved_delivery(db, flow, runs))
                    except (LookupError, TypeError, ValueError):
                        item['delivery_saved'] = False
                    item['needs_acceptance'] |= item['delivery_saved']
            if execution_id := pending_snapshots.get(item['id']):
                item['needs_acceptance'] = True
                item['result_task_id'] = execution_id
        return {'work_items': items, 'no_dispatch': True}

    def history_catalog(self, identity=None, project_id=None, scope=None):
        """Read saved cooperation records without rebuilding dispatch eligibility."""
        with self.s.store.read() as db:
            if scope is not None:
                if scope != 'all' or identity is not None or project_id is not None:
                    raise ValueError('任务总览范围无效。')
                return self.directory(db)
            if not identity:
                self.project(db, project_id)
                rows = db.execute("""SELECT id, title, state, goal, next_step FROM work_items
                    WHERE project_id IS ? AND """+self.s.work_records.visible('item','work_items.id')+
                    ' ORDER BY updated DESC, id', (project_id,))
                items = [{**dict(row),'cooperation_conversation':self.s.cooperation_conversation.exists(db,row['id'])} for row in rows]
                result = {'work_items': items}
                if project_id is not None:
                    result['open_item_id'] = self.project_entry(db, project_id, items)
                return result
            item = self.item(db, identity)
            self.s.work_records.guard(db,'item',identity)
            item['cooperation_conversation'] = self.s.cooperation_conversation.exists(db,identity)
            plan = self.s.teamwork.current(db, identity)
            if not plan:
                return {'work_items': []}
            item['records'] = []
            for ref in db.execute("SELECT record_id FROM work_item_records WHERE item_id=? AND kind='dialogue'",
                                  (identity,)):
                try:
                    item['records'].append(self.record(db, 'dialogue', ref['record_id']))
                except ValueError:
                    continue
            runs = []
            stored_runs = self.s.work_units.rows(db, identity)
            for run in stored_runs:
                try:
                    attempt, _, observed, exact = self.s.work_units.recorded_attempt(db, run)
                    answer = observed.get('answer') if exact else ''
                    result = {'state': attempt['state'] if exact else 'UNKNOWN', 'exact_identity': exact,
                              'session_id': attempt['session_id'], 'turn_id': attempt['turn_id'],
                              'answer': answer if isinstance(answer, str) else '',
                              'result_available': bool(exact and attempt['state'] == 'COMPLETED'
                                                       and isinstance(answer, str) and answer.strip())}
                    artifact = self.s.work_artifacts.current(db, run['id'])
                    if artifact and result['exact_identity']:
                        snapshot = artifact['snapshot']
                        if digest(snapshot) == artifact['version'] and snapshot['run_id'] == run['id']:
                            result['file_artifacts'] = {'state': 'SAVED', 'manifest': snapshot['manifest'],
                                                        'checks': snapshot['observations']}
                            result['check_status'] = snapshot['check_status']
                except (ValueError, OSError):
                    result = {'state': 'UNKNOWN', 'exact_identity': False}
                frozen = run['input']
                runs.append({**{key: run.get(key) for key in ('id', 'plan_id', 'plan_version', 'unit_key',
                                     'task_id', 'attempt_id', 'created', 'retry_of')},
                             'input': {'unit': frozen['unit'],
                                       'dependencies': [{key: ref.get(key) for key in ('run_id', 'unit_key', 'task_id')}
                                                        for ref in frozen.get('dependencies', [])],
                                       'check_rework': frozen.get('check_rework')},
                             'result': result})
            latest = {run['unit_key']: run for run in runs
                      if (run['plan_id'], run['plan_version']) == (plan['id'], plan['version'])}
            units = [{'key': unit['key'], 'title': unit['title'], 'member_name': unit['member']['name'],
                      'latest_run_id': run['id'], 'result': run['result']}
                     for unit in plan['units'] if (run := latest.get(unit['key']))]
            for key, source in plan.get('result_lineage', {}).items():
                if key not in latest and any(run['id'] == source['run_id'] and
                        run['plan_id'] == source['plan_id'] and run['plan_version'] == source['plan_version']
                        for run in runs):
                    units.append({'key': key, 'lineage': {'source': source}})
            flow = self.s.work_flows.current(db, identity)
            completed = self.s.work_flows.latest_delivery(db, flow)
            delivery = self.s.work_flows.saved_delivery(db, completed, stored_runs)
            project = db.execute('SELECT name FROM projects WHERE id=?', (item['project_id'],)).fetchone()
            item['project_name'] = project['name'] if project else None
            states = {run['result']['state'] for run in latest.values()}
            pending_task = next((run['task_id'] for run in reversed(runs)
                                 if latest.get(run['unit_key']) is run and
                                 run['result']['state'] in {'WAITING_APPROVAL','WAITING_INPUT'}), None)
            item['runtime'] = {'state': 'NEEDS_RECONCILIATION' if states & (UNKNOWN | {'UNKNOWN'})
                               else 'WAITING_INPUT' if states & {'WAITING_APPROVAL', 'WAITING_INPUT'}
                               else 'RUNNING' if states & ACTIVE else 'IDLE',
                               'pending_task_id': pending_task}
            item['teamwork'] = {'plan': plan}
            item['cooperation'] = {'runs': runs, 'units': units,
                                   'flow': {'record': flow, 'running': bool(flow and flow['state'] == 'RUNNING'),
                                            'delivery': delivery}}
            if plan['mode'] == 'leader':
                item['cooperation']['leader_decisions'] = self.leader_decisions(db, identity, stored_runs)
            elif plan['mode'] == 'host_experts':
                from .work_host_experts import PROTOCOL
                experts = []
                for unit in plan['units']:
                    run = latest.get(unit['key'])
                    if unit['role'] == 'plan' and run and run['result'].get('result_available'):
                        try:
                            feedback = self.s.host_experts.expert_feedback(run['result']['answer'])
                        except ValueError:
                            continue
                        experts.append({'member_name': unit['member']['name'], 'feedback': feedback})
                rows = db.execute("""SELECT data FROM collaboration WHERE kind='work_proposal'
                    AND json_extract(data,'$.item_id')=? AND json_extract(data,'$.snapshot.protocol')=?
                    ORDER BY json_extract(data,'$.created'),id""", (identity, PROTOCOL))
                decisions = [{'decision': proposal['snapshot'].get('decision')}
                             for row in rows if (proposal := json.loads(row[0]))['version'] == digest(proposal['snapshot'])]
                item['cooperation']['host_discussion'] = {'experts': experts, 'decisions': decisions}
            item['teamwork_history'] = self.teamwork_history(db, item)
            return {'work_items': [item], 'history_only': True, 'no_dispatch': True}

    def _detail_records(self, db, item):
        item['records']=[]
        for r in db.execute('SELECT * FROM work_item_records WHERE item_id=? ORDER BY created,kind,record_id',(item['id'],)):
            try:record=self.record(db,r['kind'],r['record_id'])
            except ValueError:record={'kind':r['kind'],'id':r['record_id'],'title':'原记录已删除或不可用','unavailable':True}
            item['records'].append(record)
        item['version_count']=db.execute('SELECT COUNT(*) FROM work_item_versions WHERE item_id=?',(item['id'],)).fetchone()[0]

    def control_catalog(self, identity):
        # Run controls need current ownership and exit evidence. Advanced
        # dispatch eligibility is read only when its disclosure is opened.
        with self.s.lock, self.s.store.lock:
            data = self.history_catalog(identity)
            item = next(iter(data['work_items']), None)
            if not item or not item.get('cooperation_conversation') or not item['teamwork']['plan'].get('pdca'):
                return self.catalog(identity=identity)
            with self.s.store.read() as db:
                self._detail_records(db, item)
                item['runtime'] = self.s.work_runtime.view(db, item)
                driver = self.s.work_drivers.current(db, identity)
                item['cooperation']['authorization'] = self.s.work_units.authorization(db, driver)
            return {**data, 'history_only': False, 'compact_controls': True}

    def catalog(self,project_id=None,identity=None):
        # Keep the complete detail read ahead of same-Service background writes.
        # Lock order matches commands; a pending SQLite commit must not block a
        # nested read while this catalogue still holds its outer read snapshot.
        with self.s.lock, self.s.store.lock, self.s.store.read() as db:
            self.project(db,project_id)
            if identity:
                self.s.work_records.guard(db,'item',identity)
                items=[self.item(db,identity)]
            else:
                items=[dict(r) for r in db.execute('SELECT * FROM work_items WHERE project_id IS ? AND '+
                    self.s.work_records.visible('item','work_items.id')+' ORDER BY updated DESC,id',(project_id,))]
            for item in items:
                item['cooperation_conversation']=self.s.cooperation_conversation.exists(db,item['id'])
                self._detail_records(db, item)
                item['dispatches']=[json.loads(r[0]) for r in db.execute(
                    "SELECT data FROM collaboration WHERE kind='work_dispatch' AND json_extract(data,'$.item_id')=? ORDER BY updated DESC",(item['id'],))]
                for dispatch in item['dispatches']:
                    a=self.s.store.attempt(dispatch['attempt_id'],db)
                    dispatch['state']=a['state'];dispatch['known_tools_exited']=(a.get('result') or {}).get('known_tools_exited')
                item['runtime']=self.s.work_runtime.view(db,item)
                item['fixed_preparation']=self.s.fixed_preparation.view(db,item)
                item['leader_preparation']=self.s.leader_preparation.view(db,item)
                item['host_preparation']=self.s.host_preparation.view(db,item)
                item['teamwork']=self.s.teamwork.view(db,item)
                item['driver']=self.s.work_drivers.view(db,item)
                item['cooperation']=self.s.work_units.view(db,item)
                if identity and item['teamwork']:
                    if item['teamwork']['plan']['mode'] == 'leader':
                        item['cooperation']['leader_decisions'] = self.leader_decisions(db, identity, item['cooperation']['runs'])
                    item['teamwork_history']=self.teamwork_history(db,item)
                coordinator_root=bool(item['teamwork'] and item['teamwork']['plan'].get('coordinator_file_handoff_root'))
                item['file_handoff']=None if coordinator_root else self.s.work_file_handoffs.view(db,item)
                item['file_handoff_revision']=None if coordinator_root else self.s.work_file_handoff_applications.view(db,item)
            return {'work_items':items,'project_id':project_id,'no_dispatch':True,'execution_unchanged':True}
