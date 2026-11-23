"""Confirmed language adjustments and successor PDCA batches for one cooperation chat."""

import json
from pathlib import Path
import time
from uuid import NAMESPACE_URL, uuid5

from . import attachments as native_attachments, workspace
from .contracts import bounded_text
from .cooperation_followup_intent import is_authorization_comment, parse_followup
from .store import uid


PREVIEW = 'work-item.cooperation-followup-preview'
CONFIRM = 'work-item.cooperation-followup-confirm'
ADVANCE = 'work-item.cooperation-followup-advance'
COMMANDS = {PREVIEW, CONFIRM, ADVANCE}
PROPOSAL = 'work_cooperation_followup_proposal'
AMENDMENT = 'work_cooperation_dispatch_amendment'


def plan_ref(plan):
    return {key: plan[key] for key in ('id', 'version')} if plan else None


def total_rounds(row):
    return row.get('round_offset', 0) + row['formal']['rounds']


def preparing_successor(row, plan):
    cycle = row.get('followup_cycle')
    return bool(cycle and cycle['prior_plan'] == plan_ref(plan))


def authorization_with_amendment(service, db, base):
    """Keep the signed identity bound to prior runs; only extend the two dispatch ceilings."""
    entry = db.execute('SELECT data FROM collaboration WHERE id=? AND kind=?',
                       ('dispatch-extension:' + base['revision'], AMENDMENT)).fetchone()
    if not entry:
        return base
    amendment = json.loads(entry[0])
    if amendment['authorization'] != service.work_units.auth_ref(base):
        raise ValueError('派发续授权与原授权身份不一致。')
    return {**base, 'max_attempts_total': amendment['unit_limit'],
            'dispatch_before': amendment['deadline']}


class CooperationFollowup:
    def __init__(self, conversation):
        self.chat = conversation
        self.s = conversation.s

    def handlers(self):
        return {PREVIEW: self.preview, CONFIRM: self.confirm, ADVANCE: self.advance}

    def state(self, db, row, item, flow, plan):
        if (row['state'] != 'RUNNING' or row.get('pending') or item['state'] != 'OPEN'
                or not plan or not plan.get('pdca') or not flow or flow['plan'] != plan_ref(plan)
                or preparing_successor(row, plan) or plan.get('file_handoffs')
                or plan.get('file_handoff_lineage') or plan.get('coordinator_file_handoff_root')):
            return None
        if flow['state'] == 'RESULTS_AVAILABLE':
            return 'complete'
        if flow['state'] in ('RUNNING', 'PAUSED'):
            return 'active'
        return None

    def scope(self, db, payload, fields):
        row, item, flow, plan = self.chat.control_context(db, payload, fields)
        phase = self.state(db, row, item, flow, plan)
        record = self.s.work_records.display(db, 'item', item['id'])
        if not phase or record.get('archived'):
            raise ValueError('请在已有方案开始执行或完成交付后调整派发安排。')
        driver = self.s.work_drivers.current(db, item['id'])
        auth = self.s.work_units.authorization(db, driver)
        if not driver or not auth or driver.get('plan_id') != plan['id'] or flow['authorization'] != self.s.work_units.auth_ref(auth):
            raise ValueError('当前方案的派发责任或授权已变化，请在任务详情核对。')
        if phase == 'active' and (driver['state'] not in ('RESERVED','PAUSED') or
                                  driver['generation'] != flow['driver_generation']):
            raise ValueError('请先在任务详情核对当前工作责任。')
        return row, item, flow, plan, driver, auth, phase

    def ledger(self, db, row, auth):
        prepared = len(self.chat.preparation(row).records(db, row['item_id']))
        runs = self.s.work_units.rows(db, row['item_id'])
        return prepared, runs, auth['max_attempts_total'] + prepared

    @staticmethod
    def minimum(mode, rounds):
        return 1 + (4 * rounds if mode == 'fixed' else 8 * rounds + 1) if rounds else 0

    def preview(self, db, payload):
        row, item, flow, plan, driver, auth, phase = self.scope(db, payload, ('text',))
        if row.get('queued_followup') and not row['queued_followup'].get('problem'):
            raise ValueError('已有一项续作排在当前方案之后。')
        text = bounded_text(payload['text'], '续作消息', 4000)
        intent = parse_followup(text)
        count = intent['add_rounds']
        if (phase == 'complete' and not count and intent['feedback']
                and not is_authorization_comment(intent['feedback'])):
            count = 1
        if not count and not intent['add_attempts'] and not intent['add_hours']:
            raise ValueError('请填写要修改的内容，或明确增加的工作回合或时长。')
        prepared, runs, before = self.ledger(db, row, auth)
        used = prepared + len(runs)
        after = before + intent['add_attempts']
        required = used + self.minimum(row['mode'], count)
        if count and after < required:
            if intent['add_attempts']:
                raise ValueError(f'本次续作的累计派发上限至少需 {required} 次，请增加工作回合后再确认。')
            after = required
        if after > 1000:
            raise ValueError('累计派发上限最多 1,000 次。')

        now = time.time()
        previous_deadline = auth['dispatch_before']
        deadline = previous_deadline
        renew_hours = 0
        if intent['add_hours']:
            deadline = max(now, previous_deadline) + intent['add_hours'] * 3600
        elif deadline <= now and (count or phase == 'active'):
            renew_hours = row['authorization']['hours']
            deadline = now + renew_hours * 3600
        if deadline > now + 168 * 3600:
            raise ValueError('派发截止最多可设在未来 168 小时。')
        old_round = total_rounds(row)
        proposal = {'id': uid(),'revision': row['revision'], 'text': text, 'feedback': intent['feedback'],
                    'kind': 'continue' if count else 'budget','queue': bool(count and phase == 'active'),
                    'rounds_before': old_round,'rounds_after': old_round + count,
                    'limit_before': before,'limit_after': after,'used': used,
                    'deadline_before': previous_deadline,'deadline': deadline,'renew_hours': renew_hours or None,
                    'access': row['authorization']['access'],
                    'requires_full_confirmation': row['authorization']['access']=='full' and (bool(count) or phase=='active')}
        self.s.collaboration.put(db, PROPOSAL, {'id': 'cooperation-followup:' + proposal['id'],
            'item_id': item['id'],'proposal': proposal,'plan': plan_ref(plan),'generation': flow['generation'],
            'authorization': self.s.work_units.auth_ref(auth),'expires': now + 600})
        return {'proposal': proposal,'no_dispatch': True}

    def confirm(self, db, payload):
        row, item, flow, plan, driver, auth, phase = self.scope(db, payload, ('proposal_id','confirm_full'))
        if not isinstance(payload['proposal_id'],str) or type(payload['confirm_full']) is not bool:
            raise ValueError('请确认当前续作安排。')
        record = self.s.collaboration.get(db, PROPOSAL, 'cooperation-followup:' + payload['proposal_id'])
        p = record['proposal']
        prepared, runs, limit = self.ledger(db, row, auth)
        if (record['item_id'] != item['id'] or p['revision'] != row['revision'] or record['expires'] <= time.time()
                or record['plan'] != plan_ref(plan) or record['generation'] != flow['generation']
                or record['authorization'] != self.s.work_units.auth_ref(auth)
                or p['limit_before'] != limit or p['deadline_before'] != auth['dispatch_before']
                or (row.get('queued_followup') and not row['queued_followup'].get('problem'))):
            raise ValueError('方案、派发配置或预览已变化，请重新查看续作安排。')
        if (p.get('requires_full_confirmation') or (p['access']=='full' and phase=='active')) and not payload['confirm_full']:
            raise ValueError('请明确确认新的派发安排使用完全访问权限。')
        if p['kind']=='continue':
            count = p['rounds_after'] - p['rounds_before']
            if prepared + len(runs) + self.minimum(row['mode'], count) > p['limit_after'] or p['deadline'] <= time.time():
                raise ValueError('剩余派发次数或时长已经不足，请重新预览。')
        if p['kind']=='continue' and phase=='complete':
            return self.launch(db,row,item,flow,plan,driver,p,runs)
        self.update_budget(db,row,auth,p,prepared)
        if p['kind']=='continue':
            row['queued_followup'] = {'proposal':p,'plan':plan_ref(plan),'generation':flow['generation'],
                                      'instance':self.s.instance}
        self.save_note(row,p,'续作安排' if p['kind']=='continue' else '派发设置',
                       feedback=p['feedback'] if phase=='active' else '',
                       after_round=p['rounds_before'] if phase=='complete' else None)
        self.chat.save(db,row)
        return {'proposal':p,'queued': p['kind']=='continue','no_dispatch': True}

    def update_budget(self, db, row, auth, proposal, prepared):
        if (proposal['limit_after'] - prepared != auth['max_attempts_total']
                or proposal['deadline'] != auth['dispatch_before']):
            record = {'id':'dispatch-extension:' + auth['revision'],
                'authorization':self.s.work_units.auth_ref(auth), 'item_id':row['item_id'],
                'unit_limit':proposal['limit_after'] - prepared,'deadline':proposal['deadline'],
                'before_unit_limit':auth['max_attempts_total'],'before_deadline':auth['dispatch_before'],
                'confirmation':proposal['id'],'command_id':self.s.command_identity,'created':time.time()}
            self.s.collaboration.put(db, AMENDMENT, record)
            self.s.store.event(None,None,'cooperation_dispatch_extended',record,db)
        row['authorization']['limit'] = proposal['limit_after']
        row['deadline'] = proposal['deadline']

    def save_note(self, row, proposal, title, *, feedback, after_round):
        notes = row.get('resume_notes',[])
        existing = next((note for note in reversed(notes) if note.get('proposal_id') == proposal['id']),None)
        if not existing:
            self.chat.save_note(row,proposal['text'],title)
            existing = row['resume_notes'][-1]
        existing.update(title=title,proposal_id=proposal['id'],after_round=after_round,
                        settings_only=not feedback,feedback=feedback)

    def launch(self, db, row, item, flow, plan, driver, proposal, runs):
        count = proposal['rounds_after'] - proposal['rounds_before']
        remaining = proposal['limit_after'] - len(runs) - len(self.chat.preparation(row).records(db,item['id']))
        if remaining < self.minimum(row['mode'],count) or proposal['deadline'] <= time.time():
            raise ValueError('剩余派发次数或时长不足以开始续作，请重新安排。')
        delivery = self.s.work_flows.saved_delivery(db,flow,runs)
        if not delivery:
            raise ValueError('原交付版本未能核对，请先查看成果与验收。')
        root = Path(db.execute('SELECT root FROM projects WHERE id=?',(item['project_id'],)).fetchone()[0])
        manifest = dict(plan['pdca']['manifest'])
        for entry in delivery['units']:
            if entry['final']:
                manifest.update(entry['manifest'])
        if workspace.current_manifest(root,manifest) != manifest:
            raise ValueError('原材料或交付文件已变化，请核对原项目版本后续作。')
        attachments = native_attachments.requested(plan['pdca'])
        attached_paths = {a['file'] for a in attachments}
        for name in sorted(manifest.keys() - attached_paths):
            try:
                workspace.read_text(root,name)
            except ValueError:
                attachments.append({'file':name,'route':'native'})
                attached_paths.add(name)
        native_attachments.normalize(attachments)
        if driver['state'] in ('RESERVED','PAUSED'):
            self.s.work_drivers.change(db,{**self.s.work_flows.scope(db,item,driver,plan),'action':'release'})
        if driver['state'] not in ('RESERVED','PAUSED','RELEASED'):
            raise ValueError('原合作责任仍不可接续，请在任务详情核对。')

        row.setdefault('handoff_history',[]).append({'after_round':proposal['rounds_before'],'revision':flow['revision']})
        row.pop('queued_followup',None)
        row['round_offset'] = proposal['rounds_before']
        row['followup_cycle'] = {'prior_plan':plan_ref(plan),'round_offset':row['round_offset']}
        row['formal'] = {key:value for key,value in row['formal'].items() if key not in ('file_selection','plan_only')}
        row['formal'].update(rounds=count,files=[name for name in manifest if name not in attached_paths],attachments=attachments)
        row['manifest'] = manifest
        row['deadline'] = proposal['deadline']
        row['authorization']['limit'] = proposal['limit_after']
        self.save_note(row,proposal,'续作要求',feedback=proposal['feedback'],after_round=proposal['rounds_before'])
        self.chat.save(db,row)
        return self.chat.start_formal(db,row,item)

    def advance(self, db, payload):
        row,item,flow,plan,driver,auth,phase = self.scope(db,payload,('proposal_id',))
        queued = row.get('queued_followup') or {}
        p = queued.get('proposal') or {}
        if (phase!='complete' or queued.get('instance')!=self.s.instance or queued.get('problem')
                or p.get('id')!=payload['proposal_id'] or queued.get('plan')!=plan_ref(plan)):
            raise ValueError('排定的续作尚未到达可启动的交付点。')
        if self.ledger(db,row,auth)[2] != p['limit_after'] or auth['dispatch_before'] != p['deadline']:
            raise ValueError('派发授权已经变化，请重新确认排定的续作。')
        return self.launch(db,row,item,flow,plan,driver,p,self.s.work_units.rows(db,item['id']))

    def tick(self):
        from .cooperation_conversation import KIND
        with self.s.store.read() as db:
            queued = [(row['item_id'],row['revision'],row['queued_followup']['proposal']['id']) for row in
                      self.s.collaboration.all(db,KIND) if row.get('queued_followup')
                      and not row['queued_followup'].get('problem')
                      and (row['queued_followup']['instance']!=self.s.instance or
                           (self.s.work_flows.current(db,row['item_id']) or {}).get('state')=='RESULTS_AVAILABLE')]
        for identity,revision,proposal_id in queued:
            try:
                self.s.command(ADVANCE,{'id':identity,'revision':revision,'proposal_id':proposal_id},
                               str(uuid5(NAMESPACE_URL,'workdesk-cooperation-followup:'+proposal_id)))
            except Exception as exc:
                with self.s.lock,self.s.store.write() as db:
                    row = self.chat.stored(db,identity)
                    saved = row.get('queued_followup') or {}
                    if row.get('revision') == revision and (saved.get('proposal') or {}).get('id')==proposal_id:
                        saved['problem'] = ('服务已重启，请重新确认这项续作。' if saved['instance']!=self.s.instance else str(exc)[:1000])
                        self.chat.save(db,row)
