"""Actual committed typed-boundary discovery for the text-rework parent.

An edge identifies where independent parent replay must stop. It does not
certify its own observation or provide current-resource/dispatch permission.
The enclosing new owner must reconstruct both sides and close the real ledger.
"""
from copy import deepcopy
from pathlib import Path
import sqlite3
import time

from .store import digest
from .work_transitions import plan_ref
from .work_readiness_observations import (
    EVENT as READINESS_EVENT, PROTOCOL as READINESS_PROTOCOL,
    SCOPE as READINESS_SCOPE, RESOURCE_FIELDS, selected_resources, resource_changes)
from .work_coordinator_handoff_records import fields, reference
from .work_coordinator_handoff_adjustment_records import finite, permissions
from .work_coordinator_handoff_historical_assets import HistoricalSources, _json
from . import work_coordinator_handoff_text_rework_contracts as c


def require_service_db(service, db):
    from .service import Service
    if type(service) is not Service or type(db) is not sqlite3.Connection or not db.in_transaction:
        raise ValueError('返工须使用真实Service的实际SQLite事务。')
    main = [row for row in db.execute('PRAGMA database_list') if row[1] == 'main']
    if len(main) != 1 or not main[0][2] or Path(main[0][2]).resolve() != Path(service.store.path).resolve():
        raise ValueError('返工连接不属于本Service的实际数据库。')


class ParentCurrent:
    """A completed real parent read, valid only before this transaction writes."""
    @c.closed
    def __init__(self, service, db, plan):
        from .work_coordinator_handoff_runtime import reader
        from .work_coordinator_handoff_post_round_v3_path import plan_kind
        require_service_db(service, db)
        if plan.get(c.LINEAGE_FIELD):
            raise ValueError('返工父current须由真实Service事务读取，首批不接受第二条返工边。')
        self.s, self.db, self.plan = service, db, deepcopy(plan)
        self.changes = db.total_changes
        self.owner = reader(service, db, plan)
        self.history = self.owner.read(db, plan)
        self.parent_type = plan_kind(plan)
        if self.parent_type == 'ROOT':
            self.parent_type = ('ROOT_ADJUSTMENT' if self.history.get('adjustment_capability') else
                'ROOT_V2' if self.history.get('capability') else 'ROOT_V1')
        elif self.parent_type in {'FIRST_ROUND', 'MIXED_ROUND'} and self.history.get('post_round_capability'):
            self.parent_type = 'POST_ROUND'
        if self.parent_type not in c.PARENT_TYPES:
            raise ValueError('返工父current类型尚未实现。')
        self.plans = {}
        current = deepcopy(plan)
        while current is not None:
            if current['id'] in self.plans:
                raise ValueError('返工父祖先不能循环。')
            self.plans[current['id']] = current
            identity = current.get('previous_plan_reference')
            if identity is None:
                if current.get('previous_plan') is not None:
                    raise ValueError('返工父祖先缺少准确前驱版本。')
                break
            reference(identity)
            current = service.collaboration.get(db, 'teamwork_plan', identity['id'])
            if plan_ref(current) != identity:
                raise ValueError('返工父祖先须保持实际完整版本。')
        self.root = plan_ref(current)
        h = self.history
        if h['phase'] != 'B_COMPLETED' or not h['delivery'] or not h['completion']:
            raise ValueError('文字返工须在原准确A交付及B完成之后。')
        runs = service.work_units.rows(db, plan['item_id'])
        if sorted((r['id'], digest(r)) for r in runs) != sorted(
                (f['run']['id'], digest(f['run'])) for f in h['frames']):
            raise ValueError('返工父完整前沿未覆盖全部实际尝试。')
        for frame in h['frames']:
            evidence = frame['source_evidence']
            if (not evidence['exact_identity'] or evidence['blockers'] or
                    evidence['state'] not in {'COMPLETED', 'FAILED_NO_SEND', 'FAILED', 'INTERRUPTED', 'STOPPED_RUNTIME'} or
                    frame['files']['input_problems']):
                raise ValueError('返工父须由原完整owner证明全部准确已知退出及完整冻结输入。')
        self.catalog = deepcopy(service.resources.catalog(db))
        self.item, self.driver, self.authorization = self._conditions(self.catalog)
        self.tail = db.execute('SELECT COALESCE(MAX(seq),0) FROM events').fetchone()[0]
        self._fingerprint = c.memory_digest((self.plan, self.history, self.plans, self.catalog,
            self.item, self.driver, self.authorization, self.parent_type, self.root, self.tail))
        self.require()

    def _conditions(self, catalog):
        s, db, plan, h = self.s, self.db, self.plan, self.history
        item = s.work_items.item(db, plan['item_id']); s.work_items.project(db, item['project_id'])
        driver = s.work_drivers.current(db, item['id'])
        auth = s.work_units.authorization(db, driver)
        if (s.teamwork.current(db, item['id']) != plan or driver != h['driver'] or auth != h['authorization'] or
                not driver or driver['state'] != 'RESERVED' or driver['instance'] != s.instance or
                driver['item_revision'] != item['revision'] or h['task'] != {'id': item['id'], 'revision': item['revision']} or
                any(driver[k] != auth[k] for k in ('item_id', 'plan_id', 'plan_version')) or
                auth['driver_generation'] != driver['generation'] or
                h['usage']['unknown_requests'] or h['usage']['attempts'] != len(h['frames']) or
                h['usage']['attempts'] >= auth['max_attempts_total'] or time.time() >= auth['dispatch_before']):
            raise ValueError('返工父当前指针、实例、业务修订或原累计/最初截止已变化。')
        # This is the new command's own admission calculation. It does not
        # filter old readiness issues, whose V3 lifecycle policy is different.
        view = s.teamwork.view(db, item, plan, resource_catalog=catalog)
        if view['blockers']:
            raise ValueError('返工父真实资源或业务受阻：' + '；'.join(view['blockers']))
        members = {r['id']: r for r in selected_resources(plan, catalog)['members']}
        for choice in [plan['coordinator'], plan['integrator'], *(u['member'] for u in plan['units'])]:
            actual = members.get(choice['reference']['member_id'])
            if actual is None or any(actual[k] != choice[k] for k in ('name', 'role', 'provider', 'model', 'effort', 'tier')):
                raise ValueError('返工父同修订成员完整配置已变化。')
        manual = s.work_runtime.manual(db, item['id'])
        if manual and manual['state'] == 'MANUAL':
            raise ValueError('返工父当前由人工控制。')
        for task in sorted(s.work_drivers.tasks(db, item['id'])):
            row = db.execute("SELECT data FROM collaboration WHERE kind='manual_control' AND id=?", ('manual:' + task,)).fetchone()
            if row and _json(row[0])['state'] == 'MANUAL':
                raise ValueError('返工父关联任务当前由人工控制。')
            for other in s.work_drivers.item_ids(db, task):
                if other == item['id']:
                    continue
                held = s.work_runtime.manual(db, other)
                if held and held['state'] == 'MANUAL' or s.work_drivers.held(db, other):
                    raise ValueError('返工父关联任务被其它任务责任或人工控制持有。')
        issues = s.work_drivers.execution_issues(db, item['id'], driver)
        if issues:
            raise ValueError('返工父真实执行受阻：' + '；'.join(i['message'] for i in issues))
        s.work_flows.require_stopped(db, item['id'])
        material = h['material_observation']
        # A newly captured B is already proved against its frozen artifact by
        # the original Current. Its write-ahead baseline may not exist until
        # this first typed command; existing baseline bytes must still match.
        baseline = s.work_file_rework_applications.folder(material).parent
        if baseline.exists() or baseline.is_symlink():
            s.work_file_rework_applications.require_baseline(material)
        if s.work_file_rework_applications.observe(material['root'], material['paths']) != material:
            raise ValueError('返工父本机材料已变化。')
        return deepcopy(item), deepcopy(driver), deepcopy(auth)

    def require(self):
        require_service_db(self.s, self.db)
        if (not self.db.in_transaction or self.db.total_changes != self.changes or
                c.memory_digest((self.plan, self.history, self.plans, self.catalog, self.item, self.driver,
                    self.authorization, self.parent_type, self.root, self.tail)) != self._fingerprint or
                self.db.execute('SELECT COALESCE(MAX(seq),0) FROM events').fetchone()[0] != self.tail or
                self.s.resources.catalog(self.db) != self.catalog or
                self._conditions(self.catalog) != (self.item, self.driver, self.authorization)):
            raise ValueError('返工父current只能用于本次未写入的准确真实事务。')
        return self

    def observation(self, coordinate, readiness, observed_at):
        # Called after the actual readiness write; the exact command scope must
        # bind require() before that write and prove the new event separately.
        from .work_coordinator_handoff_text_rework_values import observation_value
        return observation_value(self.plan, self.history, self.plans, coordinate, readiness, observed_at)


class TextReworkParent:
    @staticmethod
    def current(service, db, plan):
        return ParentCurrent(service, db, plan)

    @staticmethod
    def history_at_edge(db, asset_root, capability_ref, ledger, *, command_owner=None):
        from .work_coordinator_handoff_text_rework_history import parent_at_edge
        return parent_at_edge(db, asset_root, capability_ref, ledger, command_owner=command_owner)


class _TypedEdge:
    """Always fetched from the real sources; no caller-supplied cutoff API."""
    @c.closed
    def __init__(self, sources, capability_ref, *, command_owner=None):
        if type(sources) is not HistoricalSources:
            raise ValueError('返工边界须来自真实历史SQL来源。')
        sources.unchanged(sources.db)
        reference(capability_ref)
        cap = c.validate_capability(sources.get(sources.db, c.KINDS[c.CAPABILITY], capability_ref['id']))
        if {k: cap[k] for k in ('id', 'version')} != capability_ref:
            raise ValueError('返工边界能力引用不是准确实际版本。')
        body, db = cap['snapshot'], sources.db
        obs = body['observation']; ready_ref = obs['readiness_observation']
        raw = db.execute('SELECT * FROM work_readiness_observations WHERE id=?', (ready_ref['id'],)).fetchone()
        if raw is None:
            raise ValueError('返工边界缺少原命令真实资源观察。')
        ready = _json(raw['data']); fields(ready, {'id', 'item_id', 'command_id', 'created', 'snapshot', 'version'})
        finite(ready['created'])
        snapshot = ready['snapshot']
        fields(snapshot, {'protocol', 'item_revision', 'plan', 'driver', 'command_kind',
            'request_hash', 'resources', 'scope', 'no_dispatch'})
        resources = snapshot['resources']; fields(resources, RESOURCE_FIELDS)
        for kind, names in RESOURCE_FIELDS.items():
            if not isinstance(resources[kind], list) or len(resources[kind]) > len(obs['parent_plan_snapshot']['units']) + 2:
                raise ValueError('返工资源观察列表超出原声明范围。')
            for item in resources[kind]:
                fields(item, names)
                if (not isinstance(item['id'], str) or not item['id'] or
                        type(item['revision']) not in (str, int) or
                        kind == 'members' and type(item['available']) is not bool):
                    raise ValueError('返工资源观察身份、修订或可用性格式无效。')
                for key in names - {'revision', 'available'}:
                    if not isinstance(item[key], str) and not (key in {'model', 'effort', 'tier'} and item[key] is None):
                        raise ValueError('返工资源观察不能包装嵌套载体。')
        plan = sources.get(db, 'teamwork_plan', body['parent_plan']['id'])
        if plan != obs['parent_plan_snapshot'] or plan_ref(plan) != body['parent_plan']:
            raise ValueError('返工父方案须为实际SQL准确完整版本。')
        selected = selected_resources(plan, resources)
        expected = {'protocol': READINESS_PROTOCOL, 'item_revision': obs['task']['revision'],
            'plan': plan_ref(plan), 'driver': {k: obs['driver'][k] for k in ('id', 'generation', 'revision')},
            'command_kind': c.SEND, 'request_hash': digest(body['request']),
            'resources': selected, 'scope': READINESS_SCOPE, 'no_dispatch': True}
        pointer = {'id': ready['id'], 'version': ready['version'], 'no_dispatch': True}
        ready_event = sources._event(db, READINESS_EVENT, pointer)
        opened = sources._event(db, c.EVENTS[c.CAPABILITY], cap)
        if (dict(raw) != {'id': ready['id'], 'item_id': ready['item_id'], 'command_id': ready['command_id'], 'data': raw['data']} or
                ready['id'] != 'work-readiness:' + cap['command_id'] or
                ready['item_id'] != cap['item_id'] or ready['command_id'] != cap['command_id'] or
                ready['version'] != digest(expected) or snapshot != expected or resource_changes(plan, resources) or
                {k: ready_event[k] for k in ('seq', 'kind', 'payload_digest')} != ready_ref['event'] or
                ready_ref['version'] != ready['version'] or ready_event['seq'] != body['parent_tail'] + 1 or
                opened['seq'] != ready_event['seq'] + 1 or
                not obs['observed_at'] <= ready['created'] <= ready_event['created'] <= cap['created'] <= opened['created']):
            raise ValueError('返工边界资源观察、原请求、事件顺序或实际归属不符。')
        members = {row['id']: row for row in selected['members']}
        for choice in [plan['coordinator'], plan['integrator'], *(unit['member'] for unit in plan['units'])]:
            actual = members.get(choice['reference']['member_id'])
            if actual is None or any(actual[name] != choice[name] for name in ('name', 'role', 'provider', 'model', 'effort', 'tier')):
                raise ValueError('返工资源观察须保持原同修订成员完整配置。')
        command = db.execute('SELECT * FROM commands WHERE id=?', (cap['command_id'],)).fetchone()
        self._pending_owner = None
        if command is None:
            from .work_coordinator_handoff_text_rework_command_scope import pending_review
            self._pending_owner = pending_review(command_owner, sources, cap, ready_event, opened)
            self._set(sources, cap, ready, opened, ready_event, None, None, None, plan)
            return
        if command_owner is not None:
            raise ValueError('已提交返工边不能复用pending命令owner。')
        if command is None or command['kind'] != c.SEND or command['payload_hash'] != digest(body['request']):
            raise ValueError('返工历史边界须有原准确已提交命令。')
        finite(command['created'])
        # The following witness is only located here. Its run, dispatch, driver
        # and complete receipt must be generated independently by suffix replay.
        witnesses = [row for row in sources.all(db, c.KINDS[c.RECEIPT])
            if row.get('command_id') == cap['command_id']]
        if len(witnesses) != 1:
            raise ValueError('返工边界须有唯一原完整回执见证。')
        witness = witnesses[0]
        fields(witness, {'id', 'version', 'snapshot', 'item_id', 'created', 'command_id'})
        proof = witness['snapshot']
        fields(proof, {'protocol', 'version', 'operation', 'request', 'request_hash',
            'capability', 'registration', 'run', 'driver', 'receipt', *c.PERMISSIONS})
        permissions(proof); finite(witness['created'])
        if (proof['protocol'] != c.RECEIPT or proof['operation'] != 'REVIEW_SEND' or
                proof['version'] != digest({k: v for k, v in proof.items() if k != 'version'}) or
                witness['id'] != c.KINDS[c.RECEIPT] + ':' + proof['version'] or witness['version'] != proof['version'] or
                witness['item_id'] != cap['item_id'] or proof['capability'] != capability_ref or
                proof['registration'] is not None or proof['request'] != body['request'] or
                proof['request_hash'] != command['payload_hash'] or proof['receipt'] != _json(command['receipt'])):
            raise ValueError('返工边界完整命令见证不符。')
        receipt_event = sources._event(db, c.EVENTS[c.RECEIPT], witness)
        if not opened['seq'] < receipt_event['seq'] or not opened['created'] <= witness['created'] <= receipt_event['created'] <= command['created']:
            raise ValueError('返工边界完整回执须在实际开启和绑定之后提交。')
        self._set(sources, cap, ready, opened, ready_event, witness, receipt_event, dict(command), plan)

    def _set(self, sources, cap, ready, opened, ready_event, witness, receipt_event, command, plan):
        self.sources = sources
        self.capability, self.readiness = deepcopy(cap), deepcopy(ready)
        self.opened, self.readiness_event = opened, ready_event
        self.receipt, self.receipt_event, self.command = deepcopy(witness), receipt_event, deepcopy(command)
        self.parent_plan = deepcopy(plan)
        self._fingerprint = digest((cap, ready, opened, ready_event, witness, receipt_event, command, plan))

    @property
    def boundary(self):
        return self.readiness_event['seq']

    def require(self, sources, plan):
        if (sources is not self.sources or plan != self.parent_plan or not sources.db.in_transaction or
                digest((self.capability, self.readiness, self.opened, self.readiness_event,
                    self.receipt, self.receipt_event, self.command, self.parent_plan)) != self._fingerprint):
            raise ValueError('返工边界仅属于本次真实连接、父方案和完整原载体。')
        sources.unchanged(sources.db)
        if self._pending_owner is not None:
            self._pending_owner.require_edge(sources, self.capability, self.readiness_event, self.opened)
        # Re-fetch the exact original primary keys, not a filtered view.
        if sources.get(sources.db, c.KINDS[c.CAPABILITY], self.capability['id']) != self.capability:
            raise ValueError('返工边界能力在本次历史读取中变化。')
        return self.boundary
