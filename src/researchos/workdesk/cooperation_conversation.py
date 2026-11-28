"""Task-bound requirements discussion and completed PDCA results."""

import json
import math
import re
import time
from urllib.parse import unquote
from uuid import NAMESPACE_URL, uuid5

from . import work_pdca
from .contracts import bounded_text
from .cooperation_followup import COMMANDS as FOLLOWUP_COMMANDS, CooperationFollowup, preparing_successor, total_rounds
from .cooperation_followup_intent import parse_followup
from .store import ACTIVE, canonical, digest, uid
from .work_leader import unique_fields
from .workbench import scratch


KIND = 'work_cooperation_conversation'
OPEN = 'work-item.cooperation-open'
MESSAGE = 'work-item.cooperation-message'
CONFIRM = 'work-item.cooperation-confirm'
SETTLE = 'work-item.cooperation-discussion-settle'
RESUME = 'work-item.cooperation-resume'
INTERVENE = 'work-item.cooperation-intervene'
ORGANIZE = 'work-item.cooperation-organize'
COMMANDS = {OPEN, MESSAGE, CONFIRM, SETTLE, RESUME, INTERVENE, *FOLLOWUP_COMMANDS}
PROTOCOL = 'WORKDESK_COOPERATION_REQUIREMENTS_V1'
BRIEF_FIELDS = ('goal', 'deliverables', 'constraints', 'acceptance')


def key(identity):
    return 'cooperation-conversation:' + identity


def brief_value(value):
    if not isinstance(value, dict) or set(value) != set(BRIEF_FIELDS):
        raise ValueError('请核对目标、交付物、约束和验收条件。')
    if any(not isinstance(text, str) or len(text)>4000 for text in value.values()):
        raise ValueError('每项任务说明须为最多 4,000 字的文字。')
    return {**{field: value[field].strip() for field in BRIEF_FIELDS},
            'constraints': formal_constraints(value['constraints']),
            'goal': bounded_text(value['goal'], '任务目标', 4000)}


DISCUSSION_ONLY = re.compile(
    r'^\s*(?:本回合|本轮讨论|本次预讨论|第\s*[0０零]\s*轮(?:预讨论)?)\s*(?:仅|只)(?:用于|负责)?'
    r'(?:确定|确认|澄清|讨论|整理|收束)\s*(?:任务)?(?:需求|要求)')


def formal_constraints(text):
    clauses = re.split(r'(?<=[。；;\n])',text)
    return ''.join(clause for clause in clauses if not DISCUSSION_ONLY.match(clause)).strip().rstrip('；;')


def new_message(role, text):
    return {'id': uid(), 'role': role, 'text': text}


def delivery_reply(answer, manifest):
    labels = {}
    paragraphs = []
    for paragraph in re.split(r'\n\s*\n', answer.strip()):
        prose = []
        for line in paragraph.splitlines():
            link = re.fullmatch(r'\s*(?:[-*]\s+)?\[([^\]]+)\]\((.+)\)\s*', line)
            if link:
                target = unquote(link[2].strip(' <>')).replace('\\', '/')
                name = next((name for name in manifest if target == name or target.endswith('/' + name)), None)
                if name:
                    labels[name] = link[1][:160]
                    continue
            prose.append(line)
        if prose:
            paragraphs.append('\n'.join(prose))
        elif paragraphs and paragraphs[-1].endswith(('：', ':')) and len(paragraphs[-1]) < 120:
            paragraphs.pop()
    summary = '\n\n'.join(paragraphs)
    limit = 800 if manifest else 4000
    return (summary[:limit].rstrip() + '…' if len(summary) > limit else summary), labels


class CooperationConversation:
    def __init__(self, service):
        self.s = service
        self.followup = CooperationFollowup(self)

    def handlers(self):
        return {OPEN: self.open, MESSAGE: self.message, CONFIRM: self.confirm, SETTLE: self.settle,
                RESUME:self.resume,INTERVENE:self.intervene,ORGANIZE:self.organize,**self.followup.handlers()}

    def exists(self, db, identity):
        return db.execute('SELECT 1 FROM collaboration WHERE id=? AND kind=?',(key(identity),KIND)).fetchone() is not None

    def sidebar_entries(self, db):
        visible = self.s.work_records.visible('item', 'i.id')
        rows = db.execute(
            """SELECT i.id, coalesce(json_extract(d.data, '$.title'), i.title) AS title,
                      CASE WHEN json_type(d.data, '$.project') IS NULL THEN i.project_id
                           ELSE json_extract(d.data, '$.project') END AS project, i.created,
                      json_extract(c.data, '$.formal.work_scene') AS scene,
                      coalesce(json_extract(d.data, '$.pinned'), 0) AS pinned
               FROM work_items i
               JOIN collaboration c ON c.kind=? AND c.id=?||i.id
               LEFT JOIN collaboration d ON d.kind='display' AND d.id='display:item:'||i.id
               WHERE coalesce(json_extract(d.data, '$.archived'), 0)=0 AND """ + visible,
            (KIND, key('')))
        return [{**dict(row), 'pinned':bool(row['pinned'])} for row in rows]

    def organize(self, db, payload):
        if set(payload) != {'id','revision','changes'}:
            raise ValueError('合作聊天整理字段无效。')
        identity = payload['id']
        row = self.row(db, identity)
        display = self.s.work_records.display(db, 'item', identity)
        if type(payload['revision']) is not int or payload['revision'] != display.get('revision', 0):
            raise ValueError('合作聊天的整理记录已更新，请刷新后再操作。')
        changes = payload['changes']
        if not isinstance(changes,dict) or len(changes)!=1 or set(changes)-{'title','pinned','archived','project'}:
            raise ValueError('请选择名称、置顶、归档或项目归属。')
        changes = dict(changes)
        if 'title' in changes:
            changes['title'] = bounded_text(changes['title'], '聊天名称', 160)
        if any(type(changes[name]) is not bool for name in ('pinned','archived') if name in changes):
            raise ValueError('置顶及归档状态无效。')
        if 'project' in changes or changes.get('archived'):
            flow = self.s.work_flows.current(db, identity)
            queued = row.get('queued_followup')
            if (row['pending'] or (queued and not queued.get('problem')) or not flow or flow['state'] != 'RESULTS_AVAILABLE'
                    or preparing_successor(row,self.s.teamwork.current(db,identity))):
                raise ValueError('可在合作完成后归档或整理项目归属。')
        if 'project' in changes:
            changes['project'] = self.s.work_items.project(db, changes['project'])
        self.s.work_records.update_display(db, 'item', identity, changes)
        return {'id':identity,'revision':display.get('revision',0)+1,'no_dispatch':True}

    def row(self, db, identity):
        self.s.work_records.guard(db, 'item', identity)
        return self.s.collaboration.get(db, KIND, key(identity))

    def active(self, identity):
        worker = self.s.workers.get(identity)
        return bool(worker and worker['thread'].is_alive())

    def save(self, db, row):
        row['revision'] += 1
        self.s.collaboration.put(db, KIND, row)

    @staticmethod
    def authorization(payload, mode, rounds):
        hours = payload.get('dispatch_hours', 24)
        if type(hours) not in (int, float) or not math.isfinite(hours) or not 0 < hours <= 168:
            raise ValueError('派发时长须为大于 0、最多 168 小时。')
        minimum = 1 + (4 * rounds if mode == 'fixed' else 8 * rounds + 1)
        limit = payload.get('max_work_rounds', minimum + 5 if mode == 'fixed' else 20 + 10 * (rounds - 1))
        if type(limit) is not int or not minimum <= limit <= 1000:
            raise ValueError(f'工作回合上限须为 {minimum} 至 1,000 的整数。')
        access = payload.get('access', 'workspace')
        if access not in ('workspace', 'full'):
            raise ValueError('请选择请求批准或完全访问权限。')
        return {'hours': hours, 'limit': limit, 'access': access}

    def open(self, db, payload):
        formal = dict(payload)
        options = {field: formal.pop(field) for field in ('dispatch_hours','max_work_rounds','access') if field in formal}
        experts = formal.pop('discussion_experts', [])
        configuration, _, _, _, project, goal, expected = work_pdca.setup(
            self.s, db, formal, separated=formal.get('mode') != 'fixed')
        mode = formal['mode']
        authorization = self.authorization(options, mode, configuration['rounds'])
        if not isinstance(experts,list) or (not 1 <= len(experts) <= 4 if mode=='host_experts' else bool(experts)):
            raise ValueError('主持人—专家模式须选择 1 至 4 名专家。')
        experts = [work_pdca.member(self.s, db, ref)['reference'] for ref in experts]
        project = project if project is not None else scratch(self.s, db, {})['project_id']
        formal['project_id'] = project
        item = work_pdca.new_item(self.s, db, formal, project, goal)
        row = {'id': key(item['id']), 'item_id': item['id'], 'item_revision': item['revision'],
               'revision': 0, 'mode': mode, 'formal': formal, 'authorization': authorization, 'manifest': expected,
               'state': 'DISCUSSING', 'messages': [new_message('user', goal)], 'brief': None, 'pending': None,
               'experts': experts, 'expert_notes': [], 'problem': None}
        if mode == 'fixed' and item['acceptance']:
            row['brief'] = {'goal':goal,'deliverables':'','constraints':'','acceptance':item['acceptance']}
        result = self.start_formal(db, row, item) if mode=='fixed' else self.launch_discussion(db, row, item)
        return {**result, 'work_item': {'id': item['id'], 'revision': row['item_revision']}}

    def launch_discussion(self, db, row, item):
        index = len(row['expert_notes'])
        expert = index < len(row['experts'])
        member = work_pdca.member(self.s, db, row['experts'][index] if expert else row['formal']['coordinator'])
        self.s.dispatch.guard(db, item, set())
        context = {'confirmed_by_user': row['formal'].get('acceptance', ''),
                   'conversation': [{'role': msg['role'],'text':msg['text']} for msg in row['messages']]}
        if expert:
            prompt = ('WORKDESK_COOPERATION_EXPERT_V1\n作为主持人邀请的第 '+str(index+1)+
                      ' 位讨论专家，提供独立建议。前两位分别着重任务范围与成果结构、证据与验收，'
                      '其他专家补充交叉领域、可行性和遗漏。说明需用户决定的关键取舍。'
                      '只提交内部建议，不执行任务、不写文件。\n'+canonical(context))
        else:
            context['private_expert_notes'] = [note['text'] for note in row['expert_notes']]
            prompt = ('你是第 0 轮讨论的'+('主持人' if row['mode']=='host_experts' else '负责人')+
                      '，直接和用户对话。只问会改变目标或交付的重要问题，保持用户已经明确的要求。'
                      '面向用户的 message 用几句自然对话说清结论和待决定的问题。用户交由你决定的做法自行选择。'
                      '详细分工、技术选项和验收清单写进 brief。专家意见作为内部依据。'
                      '信息足够时给出可以执行和验收的任务说明，否则 brief 为 null。当前只讨论需求，不执行任务、不写文件。'
                      'brief 的约束只写正式执行也适用的用户要求。当前预讨论的行为限制只用于本回合，不得写进 brief。'
                      '\n只返回 JSON，格式为：\n'+canonical({'protocol':PROTOCOL,'message':'直接发给用户的完整消息',
                        'brief':{field:'' for field in BRIEF_FIELDS}})+'\n输入：\n'+canonical(context))
        if len(prompt)>160000:
            raise ValueError('讨论记录已超过可交接的长度，请先确认已有任务说明。')
        payload = {**{k: member[k] for k in ('provider','model','effort','tier')},
                   'project_id':item['project_id'],'work_scene':row['formal'].get('work_scene'),
                   'resource_selection':member['reference'],'goal':prompt,'access':'workspace','plan_only':True}
        payload, resource = self.s.resources.before(db, 'independent.send', payload)
        meta = {'item_id':item['id'],'discussion': 'expert' if expert else 'host', 'expert_index':index if expert else None}
        launch = self.s.scenes.send(db, payload, cooperative=meta, fixed=True)
        result = self.s.resources.complete(db, launch, resource)
        result = self.s.dispatch.complete(db, 'work-item.cooperation-discussion', payload, result,
                                          {k:item[k] for k in ('id','revision')})
        row.update(item_revision=result['work_item']['revision'], problem=None,
                   pending={'task_id':launch['task_id'], 'attempt_id':launch['launch_attempt'],
                            'kind':meta['discussion'], 'member':member['name']})
        self.save(db, row)
        return result

    def checked(self, db, p, fields):
        if set(p) != {'id','revision',*fields}:
            raise ValueError('合作聊天字段无效。')
        row = self.row(db, p['id'])
        item = self.s.work_items.item(db,p['id'])
        if type(p['revision']) is not int or p['revision']!=row['revision'] or item['revision']!=row['item_revision']:
            raise ValueError('讨论或任务已更新，请重新查看后操作。')
        if row['state']!='DISCUSSING' or row['pending']:
            raise ValueError('当前讨论正在运行或已进入正式计划。')
        return row,item

    def message(self, db, p):
        row,item = self.checked(db,p,('text',))
        row['messages'].append(new_message('user',bounded_text(p['text'],'讨论消息',16000)))
        return self.launch_discussion(db,row,item)

    def start_formal(self, db, row, item):
        settings = row['authorization']
        deadline = row.get('deadline') or time.time() + settings['hours']*3600
        result = work_pdca.start(self.s, db, row['formal'], item=item, material_manifest=row['manifest'],
            authorization={'max_attempts_total':settings['limit'],'dispatch_before':deadline,'access':settings['access']},
            continuation=row.get('followup_cycle'))
        row.update(state='RUNNING', deadline=deadline, item_revision=result['work_item']['revision'],
                   preparation=result['preparation']['id'], problem=None)
        self.save(db,row)
        return result

    def confirm(self, db, p):
        row,item = self.checked(db,p,('brief',))
        if not row['brief']:
            raise ValueError('请先讨论并生成可确认的任务说明。')
        summary = brief_value(p['brief'])
        # Preserve the original user text in the conversation and freeze the
        # exact edited brief in the work item consumed by PDCA preparation.
        goal = summary['goal'] + ('\n\n交付物\n'+summary['deliverables'] if summary['deliverables'] else '')
        changes = {'goal':goal,'constraints_text':summary['constraints'],'acceptance':summary['acceptance']}
        item = self.s.work_items.update(db, {'id':item['id'],'revision':item['revision'],'changes':changes})['work_item']
        row['brief'] = summary
        row['formal'].update(goal=goal,acceptance=summary['acceptance'])
        return self.start_formal(db,row,item)

    def preparation(self, row):
        return {'fixed':self.s.fixed_preparation,'leader':self.s.leader_preparation,
                'host_experts':self.s.host_preparation}[row['mode']]

    def stored(self, db, identity):
        raw = db.execute('SELECT data FROM collaboration WHERE id=? AND kind=?',(key(identity),KIND)).fetchone()
        return json.loads(raw[0]) if raw else {}

    def preparation_paused(self, db, identity, preparation_id):
        return self.stored(db,identity).get('paused_preparation')==preparation_id

    def adjustments(self, db, identity):
        return self.adjustment_inputs(self.stored(db,identity))

    @staticmethod
    def adjustment_inputs(row):
        return [{**note,'text':note.get('feedback',note['text'])}
                for note in row.get('resume_notes',[]) if not note.get('settings_only')]

    def add_runtime_input(self, db, identity, frozen):
        row = self.stored(db,identity)
        if not row:
            return
        requirements = frozen['work_requirements']
        frozen['work_requirements'] = {**requirements,'constraints_text':formal_constraints(requirements['constraints_text'])}
        notes = [entry['text'] for entry in self.adjustment_inputs(row)]
        if notes:
            frozen['human_adjustments'] = notes

    @staticmethod
    def save_note(row, note, title):
        if not isinstance(note,str) or len(note)>4000:
            raise ValueError('调整或续跑说明须为最多 4,000 字。')
        if note.strip():
            row.setdefault('resume_notes',[]).append({'id':uid(),'title':title,'text':note.strip()})

    def control_context(self, db, p, fields):
        if set(p)!={'id','revision',*fields}:
            raise ValueError('合作聊天操作字段无效。')
        row = self.row(db,p['id'])
        item = self.s.work_items.item(db,p['id'])
        if type(p['revision']) is not int or row['revision']!=p['revision']:
            raise ValueError('聊天已更新，请重新核对后操作。')
        if row['state']!='RUNNING' or item['state']!='OPEN':
            raise ValueError('当前任务无法调整或续跑。')
        return row,item,self.s.work_flows.current(db,item['id']),self.s.teamwork.current(db,item['id'])

    def intervene(self, db, p):
        row,item,flow,plan = self.control_context(db,p,('action','note'))
        action = p['action']
        if action not in ('adjust','interrupt'):
            raise ValueError('请选择调整下一步或打断并暂停。')
        if action=='adjust':
            note = bounded_text(p['note'],'运行调整说明',4000)
            intent = parse_followup(note)
            if any(intent[field] for field in ('add_rounds','add_attempts','add_hours')):
                return self.followup.preview(db,{'id':p['id'],'revision':p['revision'],'text':note})
        latest = None
        if flow and flow['state']=='RUNNING':
            runs = self.s.work_units.rows(db,item['id'])
            selected = next((run for run in reversed(runs) if (run.get('flow') or {}).get('generation')==flow['generation']),None)
            latest = self.s.store.attempt(selected['attempt_id'],db) if selected else None
        elif not (flow or plan) or preparing_successor(row,plan):
            saved = self.preparation(row).records(db,item['id'])
            pending = saved[-1] if saved else None
            if not pending or pending['state']!='PREPARING' or row.get('paused_preparation') or pending['instance']!=self.s.instance:
                raise ValueError('当前方案准备已经停止，请刷新后处理。')
            latest = self.s.store.attempt(pending['attempt_id'],db)
        else:
            raise ValueError('当前没有运行中的合作，请刷新后处理。')
        self.save_note(row,p['note'],'运行调整' if action=='adjust' else '打断说明')
        receipt = {'no_dispatch':True}
        if action=='interrupt':
            if flow and flow['state']=='RUNNING':
                self.s.work_flows.change(db,{'id':item['id'],'revision':item['revision'],
                    'flow':self.s.work_flows.reference(flow),'action':'pause'})
            else:
                row['paused_preparation'] = pending['id']
            if latest and latest['state'] in ACTIVE and self.active(latest['id']):
                task = self.s.store.task(latest['task_id'],db)
                receipt.update(self.s._stop(db,{'task_id':task['id'],'attempt_id':latest['id'],'owner':latest['owner']}))
        self.save(db,row)
        return receipt

    def preparation_recovery(self, db, row, item):
        preparation = self.preparation(row)
        records = preparation.records(db,item['id'])
        saved = records[-1] if records else None
        if not saved or not (row.get('paused_preparation')==saved['id'] or saved['state']=='BLOCKED'
                             or saved['state']=='PREPARING' and saved['instance']!=self.s.instance):
            return None,None
        a = self.s.store.attempt(saved['attempt_id'],db)
        result = {'id':None,'task_id':a['task_id'],'title':'方案准备','can_resume':False}
        if self.active(a['id']) or a['state'] in ACTIVE:
            return {**result,'message':'正在等待准备回合退出。'},None
        driver = self.s.work_drivers.current(db,item['id'])
        if self.s.work_drivers.execution_issues(db,item['id'],driver) or saved['item_revision']!=item['revision']:
            return {**result,'message':'请先在任务详情核对准备回合和任务版本。'},None
        if row['deadline']<=time.time():
            return {**result,'message':'新增派发授权已到期。'},None
        minimum = 1 + (4 if row['mode']=='fixed' else 8)*row['formal']['rounds'] + (row['mode']!='fixed')
        finish_existing = (saved['state']=='PREPARING' and saved['instance']==self.s.instance and a['state']=='COMPLETED')
        if not finish_existing and len(records)+len(self.s.work_units.rows(db,item['id']))+minimum>row['authorization']['limit']:
            return {**result,'message':'剩余回合不足以重新准备并执行当前方案。'},None
        return {**result,'id':digest({'saved':preparation.reference(saved),'end':a['state']}),
                'can_resume':True,'message':'从方案准备继续，沿用本次授权。'},(preparation,saved,finish_existing)

    def resume(self, db, p):
        from .cooperation_recovery import preview
        row,item,flow,plan = self.control_context(db,p,('recovery_id','note'))
        successor = preparing_successor(row,plan)
        if flow and not successor:
            candidate, detail = preview(self.s,db,item,flow,plan)
        elif not plan or successor:
            candidate, detail = self.preparation_recovery(db,row,item)
        else:
            candidate, detail = None,None
        if not candidate or not candidate['can_resume'] or candidate['id']!=p['recovery_id']:
            raise ValueError((candidate or {}).get('message') or '中断位置已变化，请刷新后核对。')
        self.save_note(row,p['note'],'续跑说明')
        row.pop('paused_preparation',None)
        self.save(db,row)
        if flow and not successor:
            scope,retry = detail
            driver = self.s.work_drivers.current(db,item['id'])
            if driver['state']=='PAUSED':
                self.s.work_drivers.change(db,{**self.s.work_flows.scope(db,item,driver,plan),'action':'resume'})
                checked, detail = preview(self.s,db,item,flow,plan)
                if not checked or not checked['can_resume']:
                    raise ValueError((checked or {}).get('message') or '合作责任已变化，请重新核对。')
                scope,retry = detail
            return self.s.work_flows.change(db,scope,recovery=retry)
        preparation,saved,finish_existing = detail
        expected = {'id':item['id'],'preparation':preparation.reference(saved)}
        if finish_existing:
            return preparation.settle(db,expected)
        preparation.discard(db,expected)
        return self.start_formal(db,row,item)

    def settle(self, db, p):
        if set(p)!={'id','attempt_id'}:
            raise ValueError('讨论回执身份无效。')
        row = self.row(db,p['id'])
        pending = row['pending']
        if not pending or pending['attempt_id']!=p['attempt_id']:
            return {'no_dispatch':True}
        attempt = self.s.store.attempt(pending['attempt_id'],db)
        if attempt['state'] in ACTIVE or self.active(attempt['id']):
            raise ValueError('讨论仍在进行。')
        result = attempt['result'] or {}
        answer = result.get('answer')
        row['pending'] = None
        row['last_discussion_task_id'] = pending['task_id']
        task = self.s.store.task(pending['task_id'],db)
        meta = task['spec'].get('cooperative') or {}
        exact = (attempt['task_id']==task['id'] and meta.get('item_id')==row['item_id']
                 and meta.get('discussion')==pending['kind']
                 and all(result.get(k)==v for k,v in (
                     ('task_id',task['id']),('attempt_id',attempt['id']),('owner',attempt['owner']))))
        if not exact or attempt['state']!='COMPLETED' or result.get('known_tools_exited') is not True or not isinstance(answer,str) or not answer.strip():
            row['problem'] = '这个讨论回合未保存完整回复。请在任务详情核对后继续。'
            self.save(db,row)
            return {'no_dispatch':True}
        item = self.s.work_items.item(db,row['item_id'])
        if item['revision']!=row['item_revision'] or item['state']!='OPEN':
            row['problem'] = '任务设置在讨论期间已变化。请打开任务详情核对。'
        elif pending['kind']=='expert':
            row['expert_notes'].append({'attempt_id':attempt['id'],'text':answer[:8000]})
            db.execute('SAVEPOINT next_cooperation_discussion')
            try:
                from .workspace import task_snapshot_transaction
                with task_snapshot_transaction(self.s.locations.data/'tasks'):
                    return self.launch_discussion(db,row,item)
            except ValueError:
                db.execute('ROLLBACK TO next_cooperation_discussion')
                row['problem'] = '下一步讨论暂时无法开始。请核对工作端后继续。'
            finally:
                db.execute('RELEASE next_cooperation_discussion')
        else:
            try:
                answer = answer.strip()
                if answer.startswith('```json\n') and answer.endswith('```'):
                    answer = answer[len('```json\n'):-3]
                value = json.loads(answer, object_pairs_hook=unique_fields)
                if not isinstance(value,dict) or set(value)!={'protocol','message','brief'} or value['protocol']!=PROTOCOL:
                    raise ValueError('讨论结构无效。')
                text = bounded_text(value['message'],'讨论回复',16000)
                brief = brief_value(value['brief']) if value['brief'] is not None else None
                row['messages'].append({**new_message('host',text), 'member':pending.get('member'), 'model':task['spec']['model']})
                row.update(brief=brief,problem=None)
            except (ValueError, TypeError, RecursionError):
                row['problem'] = '工作端的回复格式暂时无法读取，可以补充一条消息后再试。'
        self.save(db,row)
        return {'no_dispatch':True}

    def tick(self):
        if self.s.retiring:
            return
        visible = self.s.work_records.visible('item','i.id')
        with self.s.store.read() as db:
            pending = db.execute("""SELECT i.id AS item, a.id FROM collaboration c
                JOIN work_items i ON i.id=json_extract(c.data,'$.item_id')
                JOIN attempts a ON a.id=json_extract(c.data,'$.pending.attempt_id')
                WHERE c.kind=? AND """+visible+' AND a.state NOT IN ('+','.join('?' for _ in ACTIVE)+')',
                (KIND,*ACTIVE)).fetchall()
        for row in pending:
            if not self.active(row['id']):
                self.s.command(SETTLE, {'id':row['item'],'attempt_id':row['id']},
                               str(uuid5(NAMESPACE_URL,'workdesk-cooperation-discussion:'+row['id'])))
        self.followup.tick()

    def handoff(self, db, item, flow, runs):
        delivery = self.s.work_flows.saved_delivery(db, flow, runs)
        if not delivery:
            return None
        final_units = []
        by_id = {r['id']: r for r in runs}
        for entry in delivery['units']:
            if not entry['final']:
                continue
            run = by_id.get(entry['run_id'])
            evidence = self.s.work_units.base_evidence(db, run) if run else None
            answer = evidence['answer'] if (evidence and evidence['result_available']
                                            and evidence['version'] == entry['result_version']) else ''
            summary, labels = delivery_reply(answer, entry['manifest'])
            final_units.append({**entry, 'summary': summary, 'file_labels': labels})
        project = db.execute('SELECT name FROM projects WHERE id=?', (item['project_id'],)).fetchone()
        return {'project_name': project['name'] if project else None, 'units': final_units}

    def view(self, identity):
        with self.s.lock, self.s.store.lock, self.s.store.read() as db:
            row = self.row(db,identity)
            item = self.s.work_items.item(db,identity)
            record = self.s.work_records.display(db,'item',identity)
            state, problem = row['state'], row['problem']
            stages = {}
            used = 0
            limit = row['authorization']['limit']
            deadline = row.get('deadline')
            recovery = None
            capability_wait = None
            handoff = None
            handoff_history = []
            pending_followup = row.get('queued_followup')
            followup_phase = None
            discussion_model = next((message['model'] for message in reversed(row['messages'])
                                     if message['role']=='host' and message.get('model')), None)
            if not discussion_model and row.get('last_discussion_task_id') and any(
                    message['role']=='host' for message in row['messages']):
                try:
                    spec = self.s.store.task(row['last_discussion_task_id'],db)['spec']
                except LookupError:
                    spec = {}
                origin = spec.get('cooperative') or {}
                if origin.get('item_id')==identity and origin.get('discussion')=='host':
                    discussion_model = spec.get('model')
            attention = (row['pending'] or {}).get('task_id') or (row.get('last_discussion_task_id') if problem else None)
            if state!='DISCUSSING':
                plan = self.s.teamwork.current(db,identity)
                flow = self.s.work_flows.current(db,identity)
                preparation = self.preparation(row)
                prepared = preparation.view(db,item)
                saved = prepared['record']
                preparing = preparing_successor(row,plan)
                if not preparing and flow and flow['state']=='RESULTS_AVAILABLE':
                    state = 'COMPLETED'
                elif plan and row['formal'].get('plan_only') and not flow:
                    state = 'PLAN_READY'
                elif ((not preparing and flow and flow['state']=='PAUSED') or row.get('paused_preparation')
                      or (saved or {}).get('state') in ('BLOCKED','PAUSED_AFTER_RESTART','NEEDS_RECONCILIATION','NEEDS_CAPABILITY')):
                    state = 'PAUSED'
                    problem = ((not preparing and (flow or {}).get('reason')) or (saved or {}).get('reason'))
                    if (saved or {}).get('state') == 'NEEDS_CAPABILITY' and (not plan or preparing):
                        requests = saved.get('requests', [])
                        problem = f'等待补充条件：共 {len(requests)} 项，请处理后重新规划。'
                        capability_wait = {
                            'state': 'NEEDS_CAPABILITY', 'requests': requests,
                            'bindings': saved.get('capability_bindings', {}),
                            'preparation': saved['reference'], 'message': problem,
                            'details_href': '#work-items/item/' + identity,
                        }
                    if flow and not preparing:
                        from .cooperation_recovery import preview
                        recovery,_ = preview(self.s,db,item,flow,plan)
                    elif not plan or preparing:
                        recovery,_ = self.preparation_recovery(db,row,item)
                runs = self.s.work_units.rows(db,identity)
                for entries in row.get('handoff_history',[]):
                    historical = self.s.collaboration.get(db,'work_flow_revision','flow-version:'+entries['revision'])
                    summary = self.handoff(db,item,historical,runs)
                    if summary:
                        handoff_history.append({**summary,'after_round':entries['after_round']})
                if state == 'COMPLETED':
                    handoff = self.handoff(db, item, flow, runs)
                used = prepared['attempts_used'] + len(runs)
                auth = self.s.work_units.authorization(db,self.s.work_drivers.current(db,identity))
                if auth:
                    deadline = auth['dispatch_before']
                    limit = auth['max_attempts_total'] + prepared['attempts_used']
                followup_phase = self.followup.state(db,row,item,flow,plan)
                for run in runs:
                    attempt,task,result,exact = self.s.work_units.recorded_attempt(db,run)
                    if attempt['state'] in ('WAITING_APPROVAL','WAITING_INPUT') or (state=='PAUSED' and attempt['state'] in ACTIVE):
                        attention = task['id']
                    stage = run['unit_key'].split('_')
                    if len(stage)!=3 or stage[0]!='pdca' or stage[1] not in work_pdca.STAGES or not stage[2].isdigit():
                        continue
                    answer = result.get('answer')
                    number = int(stage[2])
                    position = (number,work_pdca.STAGES.index(stage[1]))
                    stages.pop(position,None)
                    if exact and attempt['state']=='COMPLETED' and result.get('known_tools_exited') is True and isinstance(answer,str) and answer.strip():
                        unit = self.s.work_artifacts.unit(db,run)
                        if unit['outputs']:
                            evidence = self.s.work_units.evidence(db,run)
                            try:
                                self.s.work_artifacts.require(evidence,'checked' if unit['checks'] else 'files')
                            except ValueError:
                                continue
                        stages[position] = {'id':run['id'],'round':number,'stage':stage[1],
                            'text':answer[:4000],'member':(unit.get('member') or {}).get('name'),'provider':task['spec']['provider'],'model':task['spec']['model'],'task_id':task['id']}
            can_send = state=='DISCUSSING' and row['pending'] is None and item['revision']==row['item_revision'] and item['state']=='OPEN'
            return {'id':identity,'project_id':item['project_id'],'title':record.get('title') or item['title'],'mode':row['mode'],'state':state,
                    'record':{'revision':record.get('revision',0),'pinned':record.get('pinned',False),
                              'archived':record.get('archived',False),'project':record.get('project',item['project_id']),
                              'can_archive_or_move':state=='COMPLETED' and row['pending'] is None and
                                  (not pending_followup or bool(pending_followup.get('problem')))},
                    'revision':row['revision'],'rounds':total_rounds(row),'pending':bool(row['pending']),
                    'problem':problem,'brief':row['brief'],'messages':row['messages'],'discussion_model':discussion_model,
                    'stages':[stages[k] for k in sorted(stages)],'can_send':can_send,
                    'can_confirm':can_send and row['brief'] is not None, 'plan_only':row['formal'].get('plan_only',False),
                    'discussion_task_id':attention if state=='DISCUSSING' else None,
                    'attention_task_id':attention if state!='DISCUSSING' else None,
                    'access':row['authorization']['access'],
                    'recovery':recovery,'resume_notes':row.get('resume_notes',[]),'handoff':handoff,'handoff_history':handoff_history,
                    'capability_wait':capability_wait,
                    'can_follow_up':followup_phase=='complete' and not record.get('archived'),
                    'can_adjust_budget':followup_phase=='active' and not record.get('archived'),
                    'followup_pending':({'text':pending_followup['proposal']['text'],
                        'rounds':pending_followup['proposal']['rounds_after'],'problem':pending_followup.get('problem')}
                        if pending_followup else None),
                    'authorization':{'limit':limit,'used':used,'hours':row['authorization']['hours'],'deadline':deadline}}
