"""Original plan, driver and business-link facts for a historical catalog.

This private reader has no runtime service, resource catalog or filesystem
access. Saved-edge codecs still own each initial transferred driver; this
reader proves subsequent execution links and returns actual event boundaries.
No result from this module is a current-input or execution permission.
"""
from copy import deepcopy
import re

from .store import canonical, digest
from .work_transitions import plan_ref
from .work_coordinator_handoff_records import fields, reference
from .work_coordinator_handoff_adjustment_records import finite, member, units
from .work_coordinator_handoff_historical_assets import _json, _uuid


class HistoricalTimeline:
    def __init__(self, sources, ledger, leaf):
        if ledger.sources is not sources or not sources.db.in_transaction:
            raise ValueError('历史时间线须属于本次实际来源及读取事务。')
        self.sources = sources
        self.ledger = ledger
        self.db = sources.db
        self.plans = self._ancestry(leaf)
        self.root = self.plans[0]
        self.item_id = self.root['item_id']
        self._driver_cache = {}

    def get_ref(self, kind, identity):
        reference(identity)
        value = self.sources.get(self.db, kind, identity['id'])
        if plan_ref(value) != identity:
            raise ValueError('历史时间线引用须等于实际数据库完整版本。')
        return value

    def _ancestry(self, leaf):
        from .work_coordinator_handoff_post_round_v3_path import plan_kind
        pending, seen = leaf, set()
        reverse = []
        while pending is not None:
            plan = self.get_ref('teamwork_plan', pending)
            if plan['id'] in seen:
                raise ValueError('历史方案祖先循环。')
            seen.add(plan['id'])
            if (plan['version'] != digest({k: v for k, v in plan.items() if k != 'version'}) or
                    reverse and (plan['item_id'] != reverse[0]['item_id'] or
                        plan['project_id'] != reverse[0]['project_id'] or
                        plan['coordinator_file_handoff_root'] != reverse[0]['coordinator_file_handoff_root'])):
                raise ValueError('历史祖先方案摘要、业务归属或原根不符。')
            finite(plan['created'])
            reverse.append(plan)
            kind = plan_kind(plan)
            if kind == 'ROOT':
                if plan['previous_plan'] is not None or 'previous_plan_reference' in plan:
                    raise ValueError('历史根不能携带未消费的前驱。')
                pending = None
            else:
                pending = plan['previous_plan_reference']
                reference(pending)
                if plan['previous_plan'] != pending['id']:
                    raise ValueError('历史前驱裸身份和准确版本引用不符。')
        result = list(reversed(reverse))
        if not result:
            raise ValueError('历史时间线缺少实际原根。')
        for previous, current in zip(result, result[1:]):
            if previous['created'] > current['created']:
                raise ValueError('历史后继方案创建早于其祖先。')
        return result

    def business(self, revision):
        if type(revision) is not int or revision < 1:
            raise ValueError('历史业务修订须为正整数。')
        raw = self.db.execute('SELECT snapshot FROM work_item_versions WHERE item_id=? AND revision=?',
            (self.item_id, revision)).fetchone()
        if raw is None:
            raise ValueError('历史时间线缺少原完整业务版本。')
        value = _json(raw['snapshot'])
        from .work_items import TEXT_FIELDS, STATES
        fields(value, {'id', 'project_id', *TEXT_FIELDS, 'state', 'revision', 'created', 'updated', 'record_refs'})
        if (value['id'] != self.item_id or value['revision'] != revision or value['state'] not in STATES or
                not isinstance(value['record_refs'], list)):
            raise ValueError('历史业务版本身份或状态不符。')
        finite(value['created']); finite(value['updated'])
        if value['updated'] < value['created']:
            raise ValueError('历史业务版本更新时间早于创建。')
        for key, limit in TEXT_FIELDS.items():
            text = value[key]
            if (not isinstance(text, str) or text != text.strip() or len(text) > limit or
                    key in {'title', 'goal'} and not text):
                raise ValueError('历史业务版本文字不符合原规范。')
        refs = value['record_refs']
        for row in refs:
            fields(row, {'kind', 'record_id', 'source_revision'})
            if not all(isinstance(v, str) and v for v in row.values()):
                raise ValueError('历史业务版本记录引用无效。')
        if refs != sorted(refs, key=lambda r: (r['kind'], r['record_id'])) or len(
                {(r['kind'], r['record_id']) for r in refs}) != len(refs):
            raise ValueError('历史业务版本记录引用须为原有序唯一集合。')
        return value

    def business_before(self, boundary):
        prior = []
        for raw in self.db.execute("SELECT * FROM events WHERE kind IN "
                "('work_item_created','work_item_updated','work_item_linked') AND seq<? ORDER BY seq", (boundary,)):
            body = _json(raw['payload'])
            if body.get('work_item_id') != self.item_id:
                continue
            if (set(body) != {'work_item_id', 'revision', 'no_dispatch'} or body['no_dispatch'] is not True or
                    raw['task_id'] is not None or raw['attempt_id'] is not None):
                raise ValueError('历史业务边界事件缺少原完整归属。')
            value = self.business(body['revision'])
            finite(raw['at'])
            if value['updated'] > raw['at']:
                raise ValueError('历史业务边界修订晚于其实际事件。')
            prior.append((raw['seq'], value))
        if not prior:
            raise ValueError('原事件边界之前缺少完整业务修订。')
        return deepcopy(prior[-1][1])

    @staticmethod
    def _write_paths(paths):
        # The runtime's final resolve() enforces its live root. Here the root is
        # a historical string, so validate the same canonical relative grammar
        # without probing the original project, symlinks or directory bytes.
        if not isinstance(paths, list) or len(paths) > 40:
            raise ValueError('历史写入声明超过原范围。')
        seen = set()
        for path in paths:
            if not isinstance(path, str) or not 1 <= len(path) <= 500 or '\\' in path:
                raise ValueError('历史写入声明不是原规范路径。')
            if path != '.' and any(not part or part in {'.', '..'} or part.endswith((' ', '.')) or
                    re.search(r'[\x00-\x1f<>:"|?*]', part) or
                    re.fullmatch(r'(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?', part, re.I)
                    for part in path.split('/')):
                raise ValueError('历史写入声明包含目录别名或越界路径。')
            if path.casefold() in seen:
                raise ValueError('历史写入声明重复。')
            seen.add(path.casefold())

    def root_facts(self):
        """Rebuild the root from its original business snapshot and event."""
        from .work_coordinator_handoff_roots import PLAN_FIELDS, CONFIG_FIELDS, root_marker
        from .work_file_handoff_config import definition as relations
        from .work_artifacts import definition as artifacts
        from .teamwork import ROLES
        plan = self.root
        fields(plan, PLAN_FIELDS)
        item = self.business(plan['item_revision'])
        if (item['state'] != 'OPEN' or item['project_id'] != plan['project_id'] or
                plan['work_requirements'] != {k: item[k] for k in ('title', 'goal', 'constraints_text', 'acceptance')} or
                plan['state'] != 'PREPARED' or plan['mode'] not in {'leader', 'host_experts'} or
                type(plan['max_parallel']) is not int or plan['max_parallel'] != 1 or
                plan['coordinator_file_handoff_root'] != root_marker(plan,
                    plan['coordinator_file_handoff_root']['project_root'])):
            raise ValueError('历史原根不等于原业务要求、配置和准确根标记。')
        cash = plan['hard_cash_limit_cny']
        if cash is not None:
            finite(cash)
            if not 0 <= cash <= 1000000:
                raise ValueError('历史原根硬费用声明无效。')
        member(plan['coordinator']); member(plan['integrator']); units(plan['units'])
        keys = {u['key']: u for u in plan['units']}
        coordinators = []
        owners = {}
        for unit in plan['units']:
            if not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', unit['key']) or unit['role'] not in ROLES:
                raise ValueError('历史原根单元身份或职责无效。')
            if any(unit[k] != unit[k].strip() for k in ('title', 'goal', 'acceptance')):
                raise ValueError('历史原根单元文字不是原规范值。')
            self._write_paths(unit['write_paths'])
            if artifacts(unit) != {k: unit[k] for k in ('outputs', 'checks', 'dependency_gate')}:
                raise ValueError('历史原根产物及检查声明不是原规范值。')
            if unit['role'] == 'coordinate':
                if unit['member'] != plan['coordinator'] or unit['write_paths'] or unit['outputs'] or unit['checks']:
                    raise ValueError('历史原根协调单元不能写入或另选成员。')
                coordinators.append(unit)
            for key in unit['depends']:
                if (unit['dependency_gate'] in {'files', 'checked'} and not keys[key]['outputs'] or
                        unit['dependency_gate'] == 'checked' and not keys[key]['checks']):
                    raise ValueError('历史原根依赖门槛缺少原产物或检查声明。')
            for name in unit['outputs']:
                owners.setdefault(name.casefold(), []).append(unit['key'])
        if not coordinators or relations(plan['file_handoffs'], plan['units'], 1) != plan['file_handoffs'] or len(
                plan['file_handoffs']) != 1:
            raise ValueError('历史原根须保留原协调及唯一关系声明。')
        relation = plan['file_handoffs'][0]
        pair, shared = {relation['source_unit'], relation['target_unit']}, {p.casefold() for p in relation['paths']}
        if any(len(ks) > 1 and (name not in shared or len(ks) != 2 or set(ks) != pair)
                for name, ks in owners.items()):
            raise ValueError('历史原根非关系单元重复拥有同一产物。')
        body = {'work_item_id': self.item_id, 'item_revision': item['revision'],
            'plan_id': plan['id'], 'version': plan['version'], 'mode': plan['mode'], 'no_dispatch': True}
        # The internal original-root writer predates a public root-save command:
        # it persists the complete root and pointer without a prepared event.
        # Preserve that actual format; never invent a legacy event or receipt.
        matches = [raw for raw in self.db.execute("SELECT * FROM events WHERE kind='teamwork_plan_prepared'")
            if _json(raw['payload']) == body]
        if len(matches) > 1:
            raise ValueError('历史原根准备事件不能重复。')
        event = self.sources._event(self.db, 'teamwork_plan_prepared', body) if matches else None
        if not item['updated'] <= plan['created'] or event is not None and plan['created'] > event['created']:
            raise ValueError('历史根实际保存早于其完整业务版本。')
        return {'plan': deepcopy(plan), 'item': item, 'event': event,
            'configuration': {k: deepcopy(plan[k]) for k in CONFIG_FIELDS}}

    def driver_versions(self, plan):
        """Decode actual immutable versions; saved/transition owners prove starts."""
        if plan not in self.plans:
            raise ValueError('责任时间线只读取本次实际祖先方案。')
        if plan['id'] in self._driver_cache:
            return deepcopy(self._driver_cache[plan['id']])
        result = []
        for raw in self.db.execute("SELECT id,data FROM collaboration WHERE kind='work_driver_revision'"):
            row = _json(raw['data'])
            if row.get('plan_id') != plan['id']:
                continue
            value = {k: v for k, v in row.items() if k != 'driver_id'}
            value['id'] = row['driver_id']
            if (row['id'] != raw['id'] or row['id'] != 'driver-version:' + value['revision'] or
                    value['id'] != 'work-driver:' + self.item_id or value['item_id'] != self.item_id or
                    value['plan_version'] != plan['version'] or value['previous_generation'] is not None or
                    value['kind'] != 'COOPERATIVE' or value['state'] not in {'RESERVED', 'PAUSED', 'RELEASED'}):
                raise ValueError('历史责任版本数据库键、方案或原generation不符。')
            _uuid(value['revision']); _uuid(value['generation']); _uuid(value['instance'])
            finite(value['created']); finite(value['updated'])
            events = [e for e in self.db.execute("SELECT * FROM events WHERE kind LIKE 'work_driver_%'")
                if _json(e['payload']) == {**value, 'no_dispatch': True}]
            if len(events) != 1:
                raise ValueError('历史责任版本须有唯一完整实际事件。')
            raw_event = events[0]
            event = self.sources._event(self.db, raw_event['kind'], {**value, 'no_dispatch': True})
            if not value['created'] <= value['updated'] <= event['created']:
                raise ValueError('历史责任版本创建、修订与事件时间不符。')
            result.append((event, value))
        result.sort(key=lambda entry: entry[0]['seq'])
        if not result:
            raise ValueError('历史方案缺少原实际责任版本。')
        self._driver_cache[plan['id']] = result
        return deepcopy(result)

    def driver_before(self, plan, boundary):
        prior = [(event, driver) for event, driver in self.driver_versions(plan) if event['seq'] < boundary]
        if not prior:
            raise ValueError('原事件边界之前缺少实际责任。')
        return deepcopy(prior[-1][1])

    def execution_link(self, run, previous, *, delivery=None, owner):
        """Prove full driver transform, business versions and original task ref."""
        proof = self.sources.exited(run)
        plan, dispatch, attempt, task = (proof[k] for k in ('plan', 'dispatch', 'attempt', 'task'))
        versions = self.driver_versions(plan)
        matches = [(event, value) for event, value in versions
            if event['kind'] == 'work_driver_execution_linked' and value.get('last_unit_run') == run['id']]
        if len(matches) != 1:
            raise ValueError('每次原派发须有唯一准确责任执行绑定。')
        event, value = matches[0]
        actual_previous = self.driver_before(plan, event['seq'])
        if previous != actual_previous or previous != self.driver_before(plan, proof['reserved']['seq']):
            raise ValueError('历史执行绑定必须消费原保留前最后责任。')
        expected = {**previous, 'item_revision': run['item_revision'] + 1,
            'task_ids': sorted(set(previous['task_ids']) | {run['task_id']}), 'last_unit_run': run['id'],
            'reason': 'OWNED_EXECUTION_LINKED', 'revision_lineage': [*previous.get('revision_lineage', []),
                {'from': run['item_revision'], 'to': run['item_revision'] + 1, 'unit_run_id': run['id']}][-100:],
            'revision': value['revision'], 'updated': value['updated']}
        dispatch_event = self.sources._event(self.db, 'work_dispatch_bound', dispatch,
            None, run['task_id'], run['attempt_id'])
        if (previous['state'] != 'RESERVED' or run['driver_revision'] != previous['revision'] or
                run['item_revision'] != previous['item_revision'] or expected != value or
                not proof['reserved']['seq'] < dispatch_event['seq'] < event['seq'] < proof['exited']['seq'] or
                not previous['updated'] <= dispatch['created'] <= value['updated']):
            raise ValueError('历史执行绑定责任未按原保留、dispatch和退出准确变换。')
        before, after = self.business(run['item_revision']), self.business(dispatch['item_revision'])
        cursor = proof['reserved']['seq']
        if delivery is not None:
            if (delivery['snapshot']['run_id'] != run['id'] or
                    delivery['snapshot']['input_version'] != run['input_version']):
                raise ValueError('历史业务绑定交付游标不属于本次准确run。')
            de = self.sources._event(self.db, 'coordinator_handoff_delivered', delivery,
                dispatch_event['seq'], run['task_id'], run['attempt_id'])
            if de['seq'] <= cursor:
                raise ValueError('历史业务绑定交付须在本次保留之后。')
            cursor = de['seq']
        if bool(run['input'].get('handoff_delivery_intent')) != (delivery is not None):
            raise ValueError('历史业务绑定须准确消费本次B交付。')
        source = {k: task[k] for k in ('id', 'project_id', 'title', 'created', 'spec_hash')}
        source.update(state='RESERVED', updated=attempt['started'], cursor=cursor)
        source.update({k: task['spec'].get(k) for k in ('interaction', 'work_scene', 'work_mode', 'provider', 'model')})
        record = {'kind': 'task', 'record_id': run['task_id'],
            'source_revision': canonical(digest({'kind': 'task', 'source': source, 'display': {}}))}
        expected_after = {**before, 'revision': before['revision'] + 1, 'updated': after['updated'],
            'record_refs': sorted([*before['record_refs'], record], key=lambda r: (r['kind'], r['record_id']))}
        raw_record = self.db.execute("SELECT * FROM work_item_records WHERE item_id=? AND kind='task' AND record_id=?",
            (self.item_id, task['id'])).fetchone()
        linked_body = {'work_item_id': self.item_id, 'revision': after['revision'], 'no_dispatch': True}
        linked = self.sources._event(self.db, 'work_item_linked', linked_body)
        if (after != expected_after or raw_record is None or raw_record['source_revision'] != record['source_revision'] or
                not cursor < linked['seq'] < dispatch_event['seq'] or
                not run['created'] <= raw_record['created'] <= after['updated'] <= dispatch['created'] or
                dispatch['item_revision'] != before['revision'] + 1):
            raise ValueError('历史业务绑定完整前后版本或冻结记录修订不符。')
        # A later display change is outside this initial-link hash; the hash
        # above always reconstructs the original empty display at reservation.
        self.ledger.business_version(self.item_id, before['revision'], before, 'business:' + str(before['revision']))
        self.ledger.business_version(self.item_id, after['revision'], after, 'business:' + str(after['revision']))
        self.ledger.business_record(dict(raw_record), owner)
        self.ledger.event('work_item_linked', linked_body, owner)
        self.ledger.row('work_driver_revision', {**value, 'id': 'driver-version:' + value['revision'],
            'driver_id': value['id']}, owner)
        self.ledger.event(event['kind'], {**value, 'no_dispatch': True}, owner)
        return {'driver': value, 'event': event, 'business_event': linked, 'before': before, 'after': after,
            'record': dict(raw_record)}
