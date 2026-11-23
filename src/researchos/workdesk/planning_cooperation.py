"""Bounded recurring cooperative work; ordinary commands retain execution ownership."""
from copy import deepcopy
from pathlib import Path
import time
import uuid

from .contracts import integer
from .planning import next_due, zone
from .store import digest, uid

KIND = 'cooperative_job'


class CooperativePlanning:
    def __init__(self, planning):
        self.p = planning
        self.s = planning.s
        self.c = planning.c
        with self.s.store.write() as db:
            for row in self.c.all(db, KIND):
                if row['state'] == 'RUNNING':
                    self.put(db, row, state='PAUSED', reason='服务重启，核对原任务、责任和授权后显式继续。')

    def handlers(self):
        return {'planning.cooperation-save': self.save, 'planning.cooperation-state': self.state,
                'planning.cooperation-advance': self.advance}

    def put(self, db, row, **changes):
        row = {**row, **changes, 'revision': uid(), 'updated': time.time()}
        self.c.put(db, KIND, row)
        self.c.put(db, 'cooperative_job_revision', {**row, 'id': 'cooperative-job-version:' + row['revision'], 'job_id': row['id']})
        return row

    def checked(self, db, p):
        row = self.c.get(db, KIND, p.get('id'))
        if row['revision'] != p.get('revision'):
            raise ValueError('合作重复计划版本已变化，请刷新。')
        return row

    def scope(self, db, item, driver, plan):
        return self.s.work_flows.scope(db, item, driver, plan)

    def binding(self, db, row, *, paused=False):
        item = self.s.work_items.item(db, row['item_id'])
        driver = self.s.work_drivers.current(db, item['id'])
        plan = self.s.teamwork.current(db, item['id'])
        flow = self.s.work_flows.current(db, item['id'])
        auth = self.s.work_units.authorization(db, driver)
        if (not driver or driver['generation'] != row['driver_generation'] or
                driver['state'] not in ({'RESERVED', 'PAUSED'} if paused else {'RESERVED'}) or
                not plan or {k: plan[k] for k in ('id', 'version')} != row['plan'] or
                not flow or flow['generation'] != row['flow_generation'] or
                flow['plan'] != row['plan'] or flow['driver_generation'] != row['driver_generation'] or
                flow['item_revision'] != item['revision'] or item['state'] != 'OPEN' or
                self.s.work_units.auth_ref(auth) != row['authorization']):
            raise ValueError('原任务、方案、推进责任、流程或授权已变化；重复计划未自动跟随。')
        if row['credential_revision'] != self.s.locations.credential_revision:
            raise ValueError('工作端凭据引用已变化，请取消并重新安排合作计划。')
        root = self.s.file_inspector.project(db, item['project_id'])['root']
        if str(Path(root).resolve()) != row['project_root']:
            raise ValueError('项目工作目录已变化，请重新安排合作计划。')
        if time.time() >= row['limits']['dispatch_before']:
            raise ValueError('原新增派发授权已到期；重复计划不会自动延长。')
        if auth['dispatch_before'] != row['limits']['dispatch_before'] or auth['max_attempts_total'] != row['limits']['max_attempts_total']:
            raise ValueError('重复计划原累计次数或截止不符。')
        if row['phase'] == 'WAITING' and row['frontier'] != self.s.work_units.frontier(db, item['id']):
            raise ValueError('周期交付后的执行前沿已变化，请核对后重新安排。')
        return item, driver, plan, flow, auth

    @staticmethod
    def recipe(plan):
        value = {k: deepcopy(plan[k]) for k in ('mode', 'max_parallel', 'hard_cash_limit_cny')}
        value.update(coordinator=plan['coordinator']['reference'], integrator=plan['integrator']['reference'])
        for name in ('access', 'member_access'):
            if name in plan:
                value[name] = deepcopy(plan[name])
        value['units'] = [{**{k: deepcopy(u[k]) for k in ('key', 'title', 'goal', 'acceptance', 'role', 'depends', 'write_paths', 'outputs', 'checks', 'dependency_gate')},
                           'member': u['member']['reference'],
                           **({'scene_preset': {k: u['scene_preset'][k] for k in ('id', 'revision')}} if u.get('scene_preset') else {})}
                          for u in plan['units']]
        return value

    def save(self, db, p):
        allowed = {'id', 'revision', 'current_driver', 'plan', 'authorization', 'frontier', 'flow',
                   'frequency', 'timezone', 'local_time', 'due', 'max_cycles', 'confirm',
                   'long_term', 'dispatch_before_local', 'confirm_long_term'}
        if set(p) - allowed or p.get('confirm') is not True:
            raise ValueError('请明确确认合作重复计划及原累计派发授权。')
        item, driver, plan = self.s.work_units.checked(db, p)
        if not item['project_id'] or plan['mode'] != 'fixed' or plan.get('file_handoffs'):
            raise ValueError('合作重复计划用于已关联项目的普通固定方案；负责人、主持人和同路径交接沿用各自连续流程。')
        if any(r['item_id'] == item['id'] and r['state'] in {'RUNNING', 'PAUSED'} for r in self.c.all(db, KIND)):
            raise ValueError('本任务已有合作重复计划；暂停仍保留计划，请先取消。')
        frequency = p.get('frequency')
        if frequency not in {'daily', 'weekly'}:
            raise ValueError('合作重复方式请选择每天或每周。')
        timezone = p.get('timezone', 'Asia/Shanghai')
        tz = zone(self.s.locations, timezone)
        due = p.get('due')
        if p.get('local_time'):
            from datetime import datetime
            local = datetime.fromisoformat(p['local_time'])
            if local.tzinfo is not None:
                raise ValueError('请使用所选时区的本地时间。')
            due = local.replace(tzinfo=tz).timestamp()
            if datetime.fromtimestamp(due, tz).replace(tzinfo=None) != local:
                raise ValueError('首次时间落在夏令时跳过区间。')
        auth = self.s.work_units.authorization(db, driver)
        if type(p.get('long_term', False)) is not bool:
            raise ValueError('长期派发选项无效。')
        authorization = p['authorization']
        if p.get('long_term'):
            if p.get('confirm_long_term') is not True or not p.get('dispatch_before_local'):
                raise ValueError('请明确确认长期授权，并填写最初派发截止。')
            from datetime import datetime
            local = datetime.fromisoformat(p['dispatch_before_local'])
            if local.tzinfo is not None:
                raise ValueError('长期截止请使用所选时区的本地时间。')
            expires = local.replace(tzinfo=tz).timestamp()
            if datetime.fromtimestamp(expires, tz).replace(tzinfo=None) != local:
                raise ValueError('长期截止落在夏令时跳过区间。')
            if not auth:
                raise ValueError('请先在合作任务中确认累计派发次数。')
            auth = self.s.work_units.authorize(db, {**self.scope(db, item, driver, plan),
                'authorization': authorization, 'max_attempts_total': auth['max_attempts_total'],
                'dispatch_before': expires, 'auto_rework_checks': auth.get('auto_rework_checks', False), 'long_term': True})['authorization']
            authorization = self.s.work_units.auth_ref(auth)
        elif p.get('dispatch_before_local') or p.get('confirm_long_term'):
            raise ValueError('请勾选并明确确认长期派发授权，或清空长期截止。')
        # The existing flow command checks exact references, frontier and readiness.
        flow = self.s.work_flows.change(db, {k: p[k] for k in ('id', 'revision', 'current_driver', 'plan', 'authorization', 'frontier', 'flow')} |
                                        {'authorization': authorization, 'action': 'schedule', 'start_at': due})['flow']
        from datetime import datetime
        row = {'id': 'cooperative-job:' + uid(), 'item_id': item['id'], 'title': item['title'],
               'state': 'RUNNING', 'phase': 'CYCLE', 'reason': '等待首周期预约；每个周期沿用同一任务和原累计授权。',
               'frequency': frequency, 'timezone': timezone, 'wall_time': datetime.fromtimestamp(due, tz).strftime('%H:%M:%S'),
               'due': due, 'max_cycles': integer(p.get('max_cycles', 30), 1, 1000, '最多周期'), 'cycles': [], 'skipped': [],
               'recipe': self.recipe(plan), 'origin_plan': p['plan'], 'plan': p['plan'],
               'driver_generation': driver['generation'], 'flow_generation': flow['generation'],
               'authorization': authorization, 'authorization_origin': authorization, 'superseded_authorization': p['authorization'] if p.get('long_term') else None,
               'limits': {k: auth[k] for k in ('dispatch_before', 'max_attempts_total', 'auto_rework_checks', 'long_term') if k in auth},
               'credential_revision': self.s.locations.credential_revision,
               'project_root': str(Path(self.s.file_inspector.project(db, item['project_id'])['root']).resolve()), 'created': time.time()}
        row = self.put(db, row)
        return {'job': row, 'no_dispatch': True}

    def state(self, db, p):
        row = self.checked(db, p)
        if set(p) - {'id', 'revision', 'state', 'confirm'} or row['state'] in {'COMPLETE', 'CANCELLED'}:
            raise ValueError('合作重复计划当前不可操作。')
        target = p.get('state')
        if target not in {'RUNNING', 'PAUSED', 'CANCELLED'}:
            raise ValueError('合作重复计划状态无效。')
        if target == 'RUNNING':
            if p.get('confirm') is not True:
                raise ValueError('请明确确认继续合作重复计划。')
            item, driver, plan, flow, auth = self.binding(db, row, paused=True)
            if driver['state'] == 'PAUSED':
                self.s.work_drivers.change(db, {**self.scope(db, item, driver, plan), 'action': 'resume'})
                driver = self.s.work_drivers.current(db, item['id'])
            if row['due'] <= time.time():
                row['skipped'].append(row['due'])
                row['due'] = next_due(self.s.locations, row, time.time())
            if row['phase'] == 'CYCLE' and flow['state'] == 'PAUSED':
                # Incomplete/unknown rounds remain blocked by the ordinary readiness checks.
                # The explicit resume is atomic: its binding guard must see the
                # requested running state during recheck; any failure rolls back.
                self.c.put(db, KIND, {**row, 'state': 'RUNNING'})
                rechecked = {**flow, 'driver_revision': driver['revision'], 'instance': self.s.instance}
                ready, reason, delivery = self.s.work_flows.decision(db, item, rechecked)
                if ready == 'RESULTS_AVAILABLE':
                    flow = self.s.work_flows.save(db, {**rechecked, 'state': ready, 'reason': reason, 'delivery': delivery}, 'repeat_rechecked')
                else:
                    flow = self.s.work_flows.change(db, {**self.scope(db, item, driver, plan),
                        'authorization': self.s.work_units.auth_ref(auth), 'frontier': self.s.work_units.frontier(db, item['id']),
                        'flow': self.s.work_flows.reference(flow), 'action': 'schedule', 'start_at': row['due']})['flow']
                row['flow_generation'] = flow['generation']
        else:
            flow = self.s.work_flows.current(db, row['item_id'])
            if flow and flow['generation'] == row['flow_generation'] and flow['state'] in {'SCHEDULED', 'RUNNING'}:
                item = self.s.work_items.item(db, row['item_id'])
                self.s.work_flows.change(db, {'id': item['id'], 'revision': item['revision'], 'flow': self.s.work_flows.reference(flow), 'action': 'pause'})
        row = self.put(db, row, state=target, reason='' if target == 'RUNNING' else '已停止后续周期派发；在途回合请在运行监控处理。')
        return {'job': row, 'no_dispatch': True}

    def advance(self, db, p):
        row = self.checked(db, p)
        if row['state'] != 'RUNNING':
            return {'no_dispatch': True}
        item, driver, plan, flow, auth = self.binding(db, row)
        if row['phase'] == 'CYCLE':
            if flow['state'] in {'SCHEDULED', 'RUNNING'}:
                return {'no_dispatch': True}
            if flow['state'] != 'RESULTS_AVAILABLE':
                raise ValueError('本周期流程已暂停，核对原回合后显式继续。')
            view = self.s.work_flows.view(db, item)
            if not view['result_evidence_current']:
                raise ValueError('本周期交付证据已变化，未安排下一周期。')
            row['cycles'].append({'index': len(row['cycles']) + 1, 'due': row['due'], 'plan': row['plan'],
                                  'driver_generation': row['driver_generation'], 'flow_generation': row['flow_generation'],
                                  'delivery': deepcopy(flow['delivery']), 'finished': time.time()})
            row['frontier'] = self.s.work_units.frontier(db, item['id'])
            row['due'] = next_due(self.s.locations, row, time.time())
            row = self.put(db, row, phase='WAITING', state='COMPLETE' if len(row['cycles']) >= row['max_cycles'] else 'RUNNING',
                           reason='本周期成果已按准确版本登记；下一周期使用同一任务的新方案和责任代次。')
            return {'job': row, 'no_dispatch': True}
        if row['due'] > time.time():
            return {'no_dispatch': True}
        if time.time() - row['due'] >= 60:
            row['skipped'].append(row['due'])
            row['due'] = next_due(self.s.locations, row, time.time())
            return {'job': self.put(db, row, reason='错过启动窗口，已跳过且不补发。'), 'no_dispatch': True}
        if len(self.s.work_units.rows(db, item['id'])) >= row['limits']['max_attempts_total']:
            raise ValueError('原累计尝试次数已到上限；重复计划不会自动增加。')
        # A single command transaction rolls ownership and plan forward. No attempt
        # starts here; the ordinary flow scheduler later issues its usual command.
        self.s.work_drivers.change(db, {**self.scope(db, item, driver, plan), 'action': 'release'})
        self.s.teamwork.save(db, {'id': item['id'], 'revision': item['revision'], 'current_plan': row['plan'], **deepcopy(row['recipe'])})
        plan = self.s.teamwork.current(db, item['id'])
        driver = self.s.work_drivers.current(db, item['id'])
        self.s.work_drivers.change(db, {**self.scope(db, item, driver, plan), 'action': 'acquire'})
        driver = self.s.work_drivers.current(db, item['id'])
        auth = self.s.work_units.authorize(db, {**self.scope(db, item, driver, plan), 'authorization': None, **row['limits']})['authorization']
        flow = self.s.work_flows.change(db, {**self.scope(db, item, driver, plan), 'authorization': self.s.work_units.auth_ref(auth),
                    'frontier': self.s.work_units.frontier(db, item['id']), 'flow': self.s.work_flows.reference(flow), 'action': 'start'})['flow']
        row = self.put(db, row, phase='CYCLE', plan={k: plan[k] for k in ('id', 'version')}, driver_generation=driver['generation'],
                       authorization=self.s.work_units.auth_ref(auth), flow_generation=flow['generation'], reason='本周期已取得唯一推进责任，沿用原累计次数和最初截止。')
        return {'job': row, 'no_dispatch': True}

    def catalog(self, db):
        jobs = self.c.all(db, KIND)
        occupied = {r['item_id'] for r in jobs if r['state'] in {'RUNNING', 'PAUSED'}}
        candidates = []
        fixed_candidates = []
        for raw in db.execute("SELECT id FROM work_items WHERE state='OPEN'"):
            item = self.s.work_items.item(db, raw['id'])
            plan = self.s.teamwork.current(db, item['id'])
            driver = self.s.work_drivers.current(db, item['id'])
            if item['id'] in occupied or not plan or plan['mode'] != 'fixed' or not driver or driver['state'] != 'RESERVED':
                continue
            view = self.s.work_units.view(db, item)
            if view['flow']['can_start']:
                fixed_candidates.append({'id': item['id'], 'title': item['title']})
                if not item['project_id'] or plan.get('file_handoffs'):
                    continue
                candidates.append({'id': item['id'], 'title': item['title'], 'request': {**self.scope(db, item, driver, plan),
                    'authorization': view['authorization_reference'], 'frontier': view['frontier'], 'flow': view['flow']['reference']},
                    'dispatch_before': view['authorization']['dispatch_before'], 'max_attempts_total': view['authorization']['max_attempts_total']})
        return {'cooperative_jobs': jobs, 'cooperative_candidates': candidates, 'fixed_candidates': fixed_candidates,
                'cooperative_repeat_scope': 'PROJECT_FIXED_PLAN; ORIGINAL_CUMULATIVE_LIMIT_AND_DEADLINE; NO_WAKE_OR_CATCHUP'}

    def flow_block(self, db, flow):
        # Flow dispatch runs before planning.tick in the shared scheduler. Bind
        # its exact generation here as well, so a changed project/account cannot
        # slip one turn through before the recurring-job tick observes it.
        raw = db.execute("SELECT id FROM collaboration WHERE kind=? AND json_extract(data,'$.flow_generation')=?",
                         (KIND, flow.get('generation'))).fetchone()
        if raw:
            row = self.c.get(db, KIND, raw['id'])
            if row['state'] != 'RUNNING':
                return '所属合作重复计划已暂停或结束。'
            try:
                self.binding(db, row)
            except (ValueError, OSError, LookupError) as exc:
                return str(exc)
        return None

    def tick(self):
        with self.s.lock:
            if self.s.retiring or (self.s.locations.data / 'retired-target.json').exists():
                return
            with self.s.store.read() as db:
                rows = self.c.all(db, KIND)
            for row in rows:
                if row['state'] != 'RUNNING':
                    continue
                with self.s.store.read() as db:
                    flow = self.s.work_flows.current(db, row['item_id'])
                    frontier = self.s.work_units.frontier(db, row['item_id'])
                payload = {'id': row['id'], 'revision': row['revision']}
                identity = str(uuid.uuid5(uuid.NAMESPACE_URL, digest(['cooperative-job', payload, self.s.work_flows.reference(flow), frontier,
                                                                      int(time.time() >= row['due']), int(time.time() >= row['limits']['dispatch_before'])])))
                try:
                    self.s.command('planning.cooperation-advance', payload, identity)
                except (ValueError, OSError, LookupError) as exc:
                    with self.s.store.write() as db:
                        current = self.c.get(db, KIND, row['id'])
                        if current['revision'] == row['revision'] and current['state'] == 'RUNNING':
                            flow = self.s.work_flows.current(db, row['item_id'])
                            if flow and flow['generation'] == row['flow_generation'] and flow['state'] in {'SCHEDULED', 'RUNNING'}:
                                self.s.work_flows.save(db, {**flow, 'state': 'PAUSED', 'reason': '合作重复计划暂停：' + str(exc)[:800]}, 'repeat_paused')
                            self.put(db, current, state='PAUSED', reason=str(exc)[:1000])
