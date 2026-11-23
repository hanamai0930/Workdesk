"""Transactional responsibility for one business task. Registration never sends."""
import json
import time

from .execution_constraints import PENDING_OPS, blockers
from .store import ACTIVE, UNKNOWN, TERMINAL, uid
from .teamwork import single_step_supported


HELD = {'RESERVED', 'PAUSED'}
PREPARATION_DRIVER = 'FIXED_PREPARATION'
PREPARATION_DRIVERS = {PREPARATION_DRIVER, 'LEADER_PREPARATION', 'HOST_EXPERTS_PREPARATION'}
REASONS = {
    'CURRENT_TURN': '关联回合仍活动或状态未知',
    'LOCAL_WORKER': '关联本地线程仍在运行',
    'EXIT_UNPROVED': '关联执行的退出证据不足',
    'REMOTE_UNKNOWN': '关联执行的远端结果未知',
    'REQUEST_UNKNOWN': '关联执行仍有未知费用或请求回执',
    'NATIVE_OPERATION': '关联原生控制在途或回执未知',
    'NATIVE_REPLY': '关联审批答复尚未确认传输',
    'NONTERMINAL': '关联尝试没有已知终态',
    'MANUAL': '关联工作正在人工接管',
    'OTHER_DRIVER': '共享执行已由其他业务任务的合作责任持有',
    'UNAVAILABLE': '关联执行已不可用，无法核对',
    'RESOURCE_CHANGED_OR_UNAVAILABLE': '方案成员、连接或额度来源已变化／不可用',
    'WORK_ITEM_CHANGED': '任务要求或版本已变化，请释放责任后修订方案',
    'WORK_ITEM_NOT_OPEN': '任务当前未处于进行中',
    'HARD_CASH_LIMIT_UNSUPPORTED': '当前无法强制落实硬费用上限',
    'NO_PLAN': '请先保存合作方案',
    'FIXED_PREPARATION': '自然语言固定流程准备持有本任务的推进责任',
    'HOST_EXPERTS_PREPARATION': '自然语言主持人方案准备持有本任务的推进责任',
    'LEADER_PREPARATION': '自然语言负责人准备持有本任务的推进责任',
    'V3_LIFECYCLE_CLOSED': 'V3已保存并保留原暂停责任；后继运行及责任变更尚未开放',
}


class WorkDrivers:
    def __init__(self, service):
        self.s = service
        # A process restart retains ownership and requires an explicit recheck.
        from .work_coordinator_handoff_post_round_v3_restart import potential
        with self.s.store.write() as db:
            for row in self.s.collaboration.all(db, 'work_driver'):
                if row.get('kind') in PREPARATION_DRIVERS and row['state'] in HELD:
                    self.save(db, {**row, 'state': 'PAUSED', 'reason': 'CONTROLLER_RESTART',
                                   'instance': self.s.instance}, 'restart_paused')
                elif row['state'] == 'RESERVED' and not potential(self.s, db, row):
                    self.save(db, {**row, 'state': 'PAUSED', 'reason': 'CONTROLLER_RESTART'}, 'restart_paused')

    def handlers(self):
        return {'work-item.driver': self.change}

    def current(self, db, item_id):
        row = db.execute("SELECT data FROM collaboration WHERE kind='work_driver' AND id=?",
                         ('work-driver:' + item_id,)).fetchone()
        return json.loads(row[0]) if row else None

    @staticmethod
    def reference(row):
        return {k: row[k] for k in ('id', 'generation', 'revision')} if row else None

    def held(self, db, item_id):
        row = self.current(db, item_id)
        return bool(row and row['state'] in HELD)

    def protected(self, db, item_id):
        if self.held(db, item_id):
            return True
        if not db.execute("SELECT 1 FROM collaboration WHERE kind='work_driver' AND json_extract(data,'$.state') IN ('RESERVED','PAUSED') LIMIT 1").fetchone():
            return False
        return any(self.held(db, other) for task in self.tasks(db, item_id)
                   for other in self.item_ids(db, task) if other != item_id)

    def guard(self, db, item_id, *, shared=False):
        blocked = self.protected(db, item_id) if shared else self.held(db, item_id)
        if blocked:
            raise ValueError('该业务任务由合作责任持有；暂停仍保留责任，释放并核对后再安排其他派发。')

    def tasks(self, db, item_id):
        refs = list(db.execute('SELECT kind,record_id FROM work_item_records WHERE item_id=?', (item_id,)))
        tasks = {r['record_id'] for r in refs if r['kind'] == 'task'}
        dialogues = {r['record_id'] for r in refs if r['kind'] == 'dialogue'}
        if dialogues:
            tasks.update(link['id'] for link in self.s.collaboration.all(db, 'link')
                         if link.get('kind') == 'dialogue' and link.get('dialogue_id') in dialogues)
        # Old plans and derived reviews share a chain; retain their full frontier.
        chains = {r[0] for task in tasks for r in db.execute('SELECT chain_id FROM chain_members WHERE task_id=?', (task,))}
        tasks.update(r[0] for chain in chains for r in db.execute('SELECT task_id FROM chain_members WHERE chain_id=?', (chain,)))
        return tasks

    def item_ids(self, db, task_id):
        # Includes legacy discussion-only links, before W01 attached task refs.
        tasks = {task_id}
        chain = db.execute('SELECT chain_id FROM chain_members WHERE task_id=?', (task_id,)).fetchone()
        if chain:
            tasks.update(r[0] for r in db.execute('SELECT task_id FROM chain_members WHERE chain_id=?', (chain[0],)))
        items = set()
        for identity in tasks:
            items.update(self.s.dispatch.references(db, 'task', identity))
            row = db.execute("SELECT data FROM collaboration WHERE kind='link' AND id=?", (identity,)).fetchone()
            link = json.loads(row[0]) if row else None
            if link and link.get('kind') == 'dialogue':
                items.update(self.s.dispatch.references(db, 'dialogue', link['dialogue_id']))
        return sorted(items)

    @staticmethod
    def issue(code, **scope):
        return {'code': code, 'message': REASONS.get(code, code), **scope}

    def execution_issues(self, db, item_id, driver=None, *, excluding=frozenset()):
        issues = []
        tasks = self.tasks(db, item_id) | set((driver or {}).get('task_ids', []))
        for identity in sorted(tasks):
            task = self.s.store.task(identity, db)
            try:
                self.s.work_records.guard(db, 'task', identity)
            except ValueError:
                issues.append(self.issue('UNAVAILABLE', task_id=identity))
            attempts = db.execute('SELECT * FROM attempts WHERE task_id=? ORDER BY started,id', (identity,)).fetchall()
            if task['state'] in ACTIVE | UNKNOWN and not attempts:
                issues.append(self.issue('CURRENT_TURN', task_id=identity))
            for raw in attempts:
                attempt = self.s.store.attempt_row(raw)
                if attempt['id'] in excluding:
                    continue
                reasons = blockers(self.s, db, task, attempt)
                if attempt['state'] not in TERMINAL:
                    reasons.append('NONTERMINAL')
                issues.extend(self.issue(code, task_id=identity, attempt_id=attempt['id']) for code in dict.fromkeys(reasons))
            if not attempts:
                if self.s.store.usage(task=identity, db=db)['unknown_requests']:
                    issues.append(self.issue('REQUEST_UNKNOWN', task_id=identity))
                if any(o['state'] in PENDING_OPS for o in self.s.native_controls.rows(db, identity)):
                    issues.append(self.issue('NATIVE_OPERATION', task_id=identity))
        return issues

    def readiness(self, db, item, driver=None, *, excluding=frozenset(), resource_catalog=None):
        plan = self.s.teamwork.current(db, item['id'])
        issues = []
        if not plan:
            issues.append(self.issue('NO_PLAN'))
        else:
            view = self.s.teamwork.view(db, item, plan, resource_catalog=resource_catalog)
            issues.extend(self.issue(code) for code in view['blockers'] if code != 'COOPERATIVE_DISPATCH_NOT_CONNECTED')
            from .work_coordinator_handoff_post_round_v3_public import is_v3
            if is_v3(plan):
                issues.append(self.issue('V3_LIFECYCLE_CLOSED'))
        manual = self.s.work_runtime.manual(db, item['id'])
        if manual and manual['state'] == 'MANUAL':
            issues.append(self.issue('MANUAL'))
        for task in sorted(self.tasks(db, item['id'])):
            row = db.execute("SELECT data FROM collaboration WHERE kind='manual_control' AND id=?", ('manual:' + task,)).fetchone()
            if row and json.loads(row[0])['state'] == 'MANUAL':
                issues.append(self.issue('MANUAL', task_id=task))
            for other in self.item_ids(db, task):
                if other == item['id']:
                    continue
                held = self.s.work_runtime.manual(db, other)
                if held and held['state'] == 'MANUAL':
                    issues.append(self.issue('MANUAL', task_id=task, item_id=other))
                if self.held(db, other):
                    issues.append(self.issue('OTHER_DRIVER', task_id=task, item_id=other))
        issues.extend(self.execution_issues(db, item['id'], driver, excluding=excluding))
        return plan, issues

    def view(self, db, item, *, resource_catalog=None):
        from .work_coordinator_handoff_text_rework_runtime import present
        plan = self.s.teamwork.current(db, item['id'])
        if present(self.s, db, plan):
            from .work_coordinator_handoff_text_rework_public import driver_view
            return driver_view(self.s, db, item, plan)
        row = self.current(db, item['id'])
        if row and row.get('kind') in PREPARATION_DRIVERS and row['state'] in HELD:
            return {'record': None, 'reference': None, 'held': True, 'stale': False,
                    'issues': [self.issue(row['kind'])], 'can_acquire': False,
                    'can_pause': False, 'can_resume': False, 'can_release': False,
                    'no_dispatch': True, 'dispatch_supported': False}
        if not row and not self.s.teamwork.current(db, item['id']):
            return {'record': None, 'reference': None, 'held': False, 'stale': False,
                    'issues': [self.issue('NO_PLAN')], 'can_acquire': False, 'can_pause': False,
                    'can_resume': False, 'can_release': False, 'no_dispatch': True, 'dispatch_supported': False}
        held = bool(row and row['state'] in HELD)
        plan, issues = self.readiness(db, item, row, resource_catalog=resource_catalog)
        stale = bool(row and (row['item_revision'] != item['revision'] or not plan or
                             row['plan_id'] != plan['id'] or row['plan_version'] != plan['version']))
        from .work_coordinator_handoff_post_round_v3_public import is_v3
        closed = is_v3(plan)
        return {'record': row, 'reference': self.reference(row), 'held': held, 'stale': stale,
                'issues': issues, 'can_acquire': not held and not issues,
                'can_resume': bool(held and row['state'] == 'PAUSED' and not stale and not issues),
                'can_pause': bool(not closed and held and row['state'] == 'RESERVED'),
                'can_release': not closed and held and not self.execution_issues(db, item['id'], row),
                'no_dispatch': True, 'dispatch_supported': not closed and single_step_supported(plan),
                'single_step_supported': not closed and single_step_supported(plan),
                'continuous_supported': bool(plan and plan['mode'] == 'fixed')}

    def save(self, db, row, action):
        row = {**row, 'revision': uid(), 'updated': time.time()}
        self.s.collaboration.put(db, 'work_driver', row)
        self.s.collaboration.put(db, 'work_driver_revision', {**row, 'id': 'driver-version:' + row['revision'], 'driver_id': row['id']})
        self.s.store.event(None, None, 'work_driver_' + action, {**row, 'no_dispatch': True}, db)
        return row

    def change(self, db, p):
        if set(p) - {'id', 'revision', 'action', 'current_driver', 'plan'}:
            raise ValueError('合作责任操作字段不支持。')
        action = p.get('action')
        if not isinstance(action, str) or action not in {'acquire', 'pause', 'resume', 'release'}:
            raise ValueError('合作责任操作无效。')
        item = self.s.work_items.checked(db, p)
        self.s.work_items.project(db, item['project_id'])
        from .work_coordinator_handoff_text_rework_runtime import present
        if present(self.s, db, self.s.teamwork.current(db, item['id'])):
            raise ValueError('文字返工责任须使用专属同generation变更入口。')
        from .work_coordinator_handoff_post_round_v3_public import is_v3
        if is_v3(self.s.teamwork.current(db, item['id'])):
            raise ValueError(REASONS['V3_LIFECYCLE_CLOSED'])
        catalog = self.s.resources.catalog(db)
        old = self.current(db, item['id'])
        if old and old.get('kind') in PREPARATION_DRIVERS and old['state'] in HELD:
            raise ValueError('请在自然语言固定流程区处理当前准备责任。')
        if 'current_driver' not in p or p['current_driver'] != self.reference(old):
            raise ValueError('合作责任版本已变化，请刷新后操作。')
        if action == 'acquire':
            if old and old['state'] in HELD:
                raise ValueError('本任务已有合作责任；暂停仍保留责任。')
            plan, issues = self.readiness(db, item, resource_catalog=catalog)
            if not plan or p.get('plan') != {k: plan[k] for k in ('id', 'version')}:
                raise ValueError('合作方案版本已变化，请刷新后选定。')
            if issues:
                raise ValueError('合作责任尚不可选定：' + '；'.join(dict.fromkeys(i['message'] for i in issues)))
            row = {'id': 'work-driver:' + item['id'], 'item_id': item['id'], 'item_revision': item['revision'],
                   'generation': uid(), 'kind': 'COOPERATIVE', 'plan_id': plan['id'], 'plan_version': plan['version'],
                   'state': 'RESERVED', 'reason': 'EXPLICIT_SELECTION', 'instance': self.s.instance,
                   'previous_generation': old['generation'] if old else None,
                   'task_ids': sorted(self.tasks(db, item['id'])), 'created': time.time(),
                   'scope': 'WORKDESK_DISPATCH_GUARD; NOT_AN_OS_LOCK'}
        else:
            if not old or old['state'] not in HELD:
                raise ValueError('当前没有可操作的合作责任。')
            if p.get('plan') != {'id': old['plan_id'], 'version': old['plan_version']}:
                raise ValueError('合作责任绑定的方案版本不符。')
            if action == 'pause':
                if old['state'] != 'RESERVED':
                    raise ValueError('合作责任已暂停。')
                row = {**old, 'state': 'PAUSED', 'reason': 'EXPLICIT_PAUSE'}
            elif action == 'resume':
                if old['state'] != 'PAUSED':
                    raise ValueError('合作责任尚未暂停。')
                view = self.view(db, item, resource_catalog=catalog)
                if not view['can_resume']:
                    raise ValueError('合作责任尚不可重新选定，请核对要求、成员及全部关联执行。')
                row = {**old, 'state': 'RESERVED', 'reason': 'EXPLICIT_RECHECK', 'instance': self.s.instance}
            else:
                if self.execution_issues(db, item['id'], old):
                    raise ValueError('合作责任不能释放：关联执行仍活动、未知或退出证据不足，请先核对。')
                row = {**old, 'state': 'RELEASED', 'reason': 'EXPLICIT_RELEASE', 'released': time.time()}
        row = self.save(db, row, action)
        if action in {'pause', 'release'}:
            self.s.work_flows.pause_for_driver(db, item['id'])
        view = self.view(db, item, resource_catalog=catalog)
        from .work_readiness_observations import record
        record(self.s, db, item, self.s.teamwork.current(db, item['id']), row, p, 'work-item.driver', catalog)
        return {'driver': view, 'no_dispatch': True, 'automatically_resumed': False}
