"""Exact internal coordinator history before opening the new A/B ledger.

Public reads never grant execution or exclude a reservation. Old protocols and
the empty-history preparation reader are deliberately separate.
"""
from copy import deepcopy
from pathlib import Path
import math
import time

from .store import canonical, digest
from .work_coordinator_handoff_roots import (
    CONFIG_FIELDS, MARKER_FIELD, PLAN_FIELDS, WorkCoordinatorHandoffRoots, root_marker)
from .work_file_reworks import original_files
from .work_transitions import plan_ref


PROTOCOL = 'COORDINATOR_FILE_HANDOFF_COORDINATION_INPUT_V1'
READ = 'COORDINATOR_FILE_HANDOFF_ROOT_HISTORY_V1'
PERMISSIONS = {'save_permission': False, 'current_input_permission': False,
               'execution_permission': False, 'no_dispatch': True}
TERMINAL = {'COMPLETED', 'FAILED_NO_SEND', 'FAILED', 'INTERRUPTED', 'STOPPED_RUNTIME'}
PROMPT = ('本次为新原方案的内部协调回合，普通交付运行尚未开放。'
          '读取完整冻结历史，仅返回 pause_schema 对应的严格 JSON；不写文件。\n输入快照 JSON：\n')


class WorkCoordinatorHandoffContext:
    def __init__(self, service):
        self.s = service

    def _root(self, db, plan):
        if (not isinstance(plan, dict) or set(plan) != PLAN_FIELDS or
                not isinstance(plan['id'], str) or not plan['id'].startswith('teamwork:') or
                len(plan['id']) <= len('teamwork:') or type(plan['created']) not in (int, float) or
                not math.isfinite(plan['created']) or plan['created'] < 0 or
                plan['state'] != 'PREPARED' or plan['previous_plan'] is not None or
                plan['version'] != digest({k: v for k, v in plan.items() if k != 'version'}) or
                self.s.teamwork.current(db, plan['item_id']) != plan or
                [p for p in self.s.collaboration.all(db, 'teamwork_plan')
                 if p.get('item_id') == plan['item_id']] != [plan]):
            raise ValueError('协调根须为唯一准确当前完整新原方案。')
        item = self.s.work_items.item(db, plan['item_id'])
        self.s.work_items.project(db, item['project_id'])
        root = self.s.work_file_rework_applications.root(db, item)
        if (item['state'] != 'OPEN' or item['project_id'] != plan['project_id'] or
                plan[MARKER_FIELD] != root_marker(plan, root) or
                plan['work_requirements'] != {k: item[k] for k in
                    ('title', 'goal', 'constraints_text', 'acceptance')}):
            raise ValueError('协调根的原要求、项目或准确目录已变化。')
        raw = {k: deepcopy(plan[k]) for k in CONFIG_FIELDS}
        raw['coordinator'] = raw['coordinator']['reference']
        raw['integrator'] = raw['integrator']['reference']
        for unit in raw['units']:
            unit['member'] = unit['member']['reference']
        if WorkCoordinatorHandoffRoots(self.s)._configuration(db, raw, Path(root)) != {
                k: plan[k] for k in CONFIG_FIELDS}:
            raise ValueError('协调根的完整规范定义或原资源已变化。')
        self.s.work_flows.require_stopped(db, item['id'])
        return item, root

    def _driver(self, db, plan, reference):
        row = self.s.collaboration.get(db, 'work_driver_revision', 'driver-version:' + reference['revision'])
        value = {k: v for k, v in row.items() if k != 'driver_id'}
        value['id'] = row['driver_id']
        if (self.s.work_drivers.reference(value) != reference or
                row['id'] != 'driver-version:' + reference['revision'] or
                value['item_id'] != plan['item_id'] or value['plan_id'] != plan['id'] or
                value['plan_version'] != plan['version'] or value['previous_generation'] is not None):
            raise ValueError('协调根的原责任版本或generation不一致。')
        if value['kind'] != 'COOPERATIVE' or value['state'] not in {'RESERVED', 'PAUSED', 'RELEASED'}:
            raise ValueError('原责任类型或实际状态无效。')
        return value

    def _auth(self, db, plan, reference):
        row = self.s.collaboration.get(db, 'work_unit_authorization_revision',
                                       'unit-auth-version:' + reference['revision'])
        value = {**row, 'id': reference['id']}
        if (row['id'] != 'unit-auth-version:' + reference['revision'] or
                self.s.work_units.auth_ref(value) != reference or value['plan_id'] != plan['id'] or
                value['plan_version'] != plan['version'] or value['item_id'] != plan['item_id'] or
                value['already_used'] != 0 or type(value['max_attempts_total']) is not int or
                not 1 <= value['max_attempts_total'] <= 1000 or
                type(value['dispatch_before']) not in (int, float) or not math.isfinite(value['dispatch_before']) or
                value['dispatch_before'] <= value['created'] or
                value['id'] != 'unit-auth:' + value['driver_generation'] or
                value['native_permissions'] != 'VENDOR_DEFAULTS'):
            raise ValueError('协调根的原累计授权或原生权限不一致。')
        return value

    @staticmethod
    def _coordinator(unit):
        if unit['role'] != 'coordinate' or unit['depends'] or unit['outputs'] or unit['checks'] or unit['write_paths']:
            raise ValueError('内部运行首批仅开放无普通依赖的首次coordinate，A/B交付运行待接通。')

    def _context(self, plan, task, driver, auth, prior, observation):
        states = {r['run']['unit_key']: r for r in prior}
        body = {'protocol': PROTOCOL, 'root': plan_ref(plan), 'marker': plan[MARKER_FIELD],
                'task': task, 'work_requirements': plan['work_requirements'],
                'configuration': {k: plan[k] for k in CONFIG_FIELDS}, 'driver': driver,
                'authorization': auth, 'phase': 'COORDINATION_ONLY_BEFORE_A',
                'prior_frontier': [self.frontier_entry(r) for r in prior], 'material_observation': observation,
                'units': [{'definition': u, 'state': states[u['key']]['evidence']['state']
                           if u['key'] in states else 'NOT_RUN',
                           'source_run': states[u['key']]['run']['id'] if u['key'] in states else None}
                          for u in plan['units']], 'allowed_actions': ['pause'], **PERMISSIONS,
                'scope': 'FROZEN_PRIOR_HISTORY; COORDINATION_ONLY; NO_ORDINARY_RUNTIME_OR_MATERIAL_PERMISSION'}
        return {**body, 'version': digest(body)}

    @staticmethod
    def frontier_entry(frame):
        # Full authoritative records are read and verified above. References
        # avoid recursively embedding every earlier input and its full prompt.
        return {'run_id': frame['run']['id'], 'run_version': digest(frame['run']),
                'input_version': frame['run']['input_version'],
                'task_spec_version': digest(frame['task_spec']), 'workspace': frame['workspace'],
                'evidence': frame['evidence'], 'files': frame['files']}

    @staticmethod
    def forecast(plan, context, source_unit):
        reference = {k: context[k] for k in ('protocol', 'version', 'root')}
        return {'mode': plan['mode'], 'handoff_context': reference,
                'candidates': [{'unit': u, 'ready': False, 'ready_after_proposal': False,
                    'blocked_reason': 'ROOT_DELIVERY_RUNTIME_NOT_OPEN', 'retry_of': None,
                    'awaits_this_proposal': source_unit['key'] in u['depends'], 'latest_result': None,
                    'lineage': {'inherited': False, 'source': None, 'consumer_plan': plan_ref(plan), 'problems': []}}
                    for u in plan['units'] if u['role'] != 'coordinate'],
                'allowed_actions': ['pause'],
                'readiness_scope': 'FORECAST_ONLY; NOT_DISPATCH_PERMISSION',
                'pause_schema': {'schema_version': 1, 'action': 'pause', 'reason': 'issue requiring attention'},
                **{k: {'state': 'NOT_OPEN', 'root': plan_ref(plan), 'execution_permission': False}
                   for k in ('adjustment_context', 'rework_context', 'file_rework_context', 'unit_revision_context')},
                'adjustment_prior_frontier': context['prior_frontier'],
                'scope': 'NO_ORDINARY_DISPATCH_OR_REVISION; NO_NEW_AUTHORIZATION'}

    def _input(self, plan, unit, context):
        return {'item_id': plan['item_id'], 'item_revision': context['task']['revision'],
                'plan_id': plan['id'], 'plan_version': plan['version'],
                'work_requirements': plan['work_requirements'], 'unit': unit,
                'dependencies': [], 'check_evidence': [], 'file_artifacts': [],
                'coordination_context': self.forecast(plan, context, unit),
                'handoff_coordination_context': context}

    def _read(self, db, plan):
        writes = db.total_changes
        item, root = self._root(db, plan)
        rows = self.s.work_units.rows(db, item['id'])
        driver = self.s.work_drivers.current(db, item['id'])
        auth = self.s.work_units.authorization(db, driver)
        revisions = [r for r in self.s.collaboration.all(db, 'work_driver_revision')
                     if r.get('item_id') == item['id']]
        if driver:
            if self._driver(db, plan, self.s.work_drivers.reference(driver)) != driver:
                raise ValueError('当前责任与不可变版本记录不一致。')
            if any(r['generation'] != driver['generation'] for r in revisions):
                raise ValueError('新原方案首批不接受旧generation历史。')
            for saved in revisions:
                self._driver(db, plan, {'id': saved['driver_id'], 'generation': saved['generation'],
                                       'revision': saved['revision']})
        elif rows or revisions or auth:
            raise ValueError('运行历史缺少原责任。')
        auths = [r for r in self.s.collaboration.all(db, 'work_unit_authorization_revision')
                 if r.get('item_id') == item['id']]
        if auth:
            if (self._auth(db, plan, self.s.work_units.auth_ref(auth)) != auth or len(auths) != 1 or
                    auth['driver_generation'] != driver['generation']):
                raise ValueError('原授权版本被替换或重复授权。')
        elif rows or auths:
            raise ValueError('运行历史缺少原累计授权。')
        by_key = {u['key']: u for u in plan['units']}
        prior, seen, revision = [], set(), plan['item_revision']
        for run in rows:
            unit = by_key.get(run['unit_key'])
            if not unit:
                raise ValueError('运行包含未声明单元。')
            self._coordinator(unit)
            raw = self.s.work_units.evidence(db, run)
            task = self.s.store.task(run['task_id'], db)
            attempt = self.s.store.attempt(run['attempt_id'], db)
            old_driver = self._driver(db, plan, {'id': driver['id'], 'generation': run['generation'],
                                                'revision': run['driver_revision']})
            old_auth = self._auth(db, plan, run['authorization'])
            context = run['input'].get('handoff_coordination_context')
            if (not context or run['unit_key'] in seen or run['retry_of'] is not None or
                    run['plan_id'] != plan['id'] or run['plan_version'] != plan['version'] or
                    run['item_id'] != item['id'] or run['item_revision'] != revision or
                    run['ordinal_total'] != len(prior) + 1 or run['ordinal_total'] > auth['max_attempts_total'] or
                    run['created'] >= auth['dispatch_before'] or old_auth != auth or
                    old_driver['state'] != 'RESERVED' or old_driver['item_revision'] != revision or
                    old_driver['generation'] != auth['driver_generation'] or
                    run['driver_id'] != driver['id'] or run['unit_revision'] != digest(unit) or
                    run['input_version'] != digest(run['input']) or not raw['exact_identity'] or
                    run['scheduling'] != 'EXPLICIT_SERIAL_STEP' or run['flow'] is not None or
                    attempt['workspace'] != root or run['task_spec_version'] != digest(task['spec'])):
                raise ValueError('协调运行的原身份、输入、责任、授权、目录或累计合同不一致。')
            observation = context['material_observation']
            if (observation['root'] != root or observation['paths'] != sorted(plan['file_handoffs'][0]['paths']) or
                    set(observation['manifest']) | set(observation['missing']) != set(observation['paths']) or
                    set(observation['manifest']) & set(observation['missing'])):
                raise ValueError('冻结观察不是完整原关系材料。')
            self.s.work_file_rework_applications.require_baseline(observation)
            expected = self._context(plan, {'id': item['id'], 'revision': revision},
                                     old_driver, old_auth, prior, observation)
            if run['input'] != self._input(plan, unit, expected):
                raise ValueError('原协调冻结输入不等于完整有序历史重建，不能补写或重算hash升级。')
            spec = task['spec']
            connection = self.s.resources.selection(db, unit['member']['reference']['member_id'])['connection']
            binding = {k: connection[k] for k in ('id', 'provider', 'credential_reference')}
            from .native_permissions import choice
            meta = {'item_id': item['id'], 'source_item_revision': revision, 'driver_id': old_driver['id'],
                    'generation': old_driver['generation'], 'driver_revision': old_driver['revision'],
                    'plan_id': plan['id'], 'plan_version': plan['version'], 'unit_key': unit['key'],
                    'unit_revision': digest(unit), 'input_version': digest(run['input']),
                    'authorization': self.s.work_units.auth_ref(old_auth)}
            if (spec['goal'] != PROMPT + canonical(run['input']) or
                    spec['materials'] or spec['attachments'] or spec['writable'] or spec['checks'] or
                    spec['work_mode'] != 'cooperative' or spec['native'] != {'configuration': 'VENDOR_DEFAULTS'} or
                    spec['access'] != choice(None) or spec['execution_connection'] != binding or
                    any(spec[k] != unit['member'][k] for k in ('provider', 'model', 'effort', 'tier')) or
                    run['cooperative'] != meta or any(run.get(k) != v for k, v in meta.items())):
                raise ValueError('原实际spec、模型、材料或原生权限脱离完整冻结输入。')
            links = [l for r in revisions for l in r.get('revision_lineage', [])
                     if l['unit_run_id'] == run['id']]
            links = list({digest(l): l for l in links}.values())
            if links:
                if len(links) != 1 or links[0]['from'] != revision:
                    raise ValueError('原任务绑定修订历史不一致。')
                revision = links[0]['to']
            elif not (attempt['state'] == 'RESERVED' and run['command_id'] == self.s.command_identity):
                raise ValueError('原运行缺少准确任务绑定历史。')
            files = original_files(self.s, db, run)
            if files['input_problems'] or files['output_state'] == 'DAMAGED':
                raise ValueError('原实际输入或全部旧快照损坏。')
            prior.append({'run': deepcopy(run), 'task_spec': deepcopy(spec), 'workspace': attempt['workspace'],
                          'evidence': raw, 'files': files})
            seen.add(unit['key'])
        if revision != item['revision'] or (driver and driver['item_revision'] != item['revision']):
            raise ValueError('当前任务修订脱离原方案和完整绑定历史。')
        observation = self.s.work_file_rework_applications.observe(root, plan['file_handoffs'][0]['paths'])
        if db.total_changes != writes or self.s.teamwork.current(db, item['id']) != plan:
            raise ValueError('协调只读观察期间数据库或当前方案发生变化。')
        return deepcopy({'protocol': READ, 'root': plan_ref(plan), 'marker': plan[MARKER_FIELD],
                         'task': {'id': item['id'], 'revision': item['revision']}, 'driver': driver,
                         'authorization': auth, 'prior_frontier': prior, 'material_observation': observation,
                         'phase': 'COORDINATION_ONLY_BEFORE_A', **PERMISSIONS})

    def read(self, db, plan):
        try:
            return self._read(db, plan)
        except (LookupError, TypeError, AttributeError, StopIteration) as exc:
            raise ValueError('协调根或完整持久历史字段无效。') from exc

    def input_for(self, db, item, plan, unit, rows):
        self._coordinator(unit)
        history = self.read(db, plan)
        if (rows != self.s.work_units.rows(db, item['id']) or
                unit not in plan['units'] or history['task'] != {'id': item['id'], 'revision': item['revision']}):
            raise ValueError('当前完整前沿、任务或声明单元已变化。')
        if not history['driver'] or history['driver']['state'] != 'RESERVED' or not history['authorization']:
            raise ValueError('须先取得准确原责任和累计授权。')
        if (any(r['evidence']['state'] not in TERMINAL or r['evidence']['blockers']
                for r in history['prior_frontier']) or
                any(r['run']['unit_key'] == unit['key'] for r in history['prior_frontier'])):
            raise ValueError('活动、未知或同key历史不能作为新首次协调回合。')
        auth = history['authorization']
        if len(rows) >= auth['max_attempts_total'] or time.time() >= auth['dispatch_before']:
            raise ValueError('原累计授权已到上限或截止。')
        context = self._context(plan, history['task'], history['driver'], auth,
                                history['prior_frontier'], history['material_observation'])
        return deepcopy(self._input(plan, unit, context))

    def recheck(self, db, plan, frozen):
        item = self.s.work_items.item(db, plan['item_id'])
        rows = self.s.work_units.rows(db, item['id'])
        if self.input_for(db, item, plan, frozen['unit'], rows) != frozen:
            raise ValueError('协调派发最终独立前沿或材料观察已变化。')

    def reservation(self, db, plan, run):
        saved = self.s.work_units.reservation(db, run['attempt_id'])
        attempt = self.s.store.attempt(run['attempt_id'], db)
        if (saved != run or run['command_id'] != self.s.command_identity or attempt['state'] != 'RESERVED'):
            raise ValueError('只允许当前命令的准确持久RESERVED进行最终核对。')
        history = self.read(db, plan)
        if not history['prior_frontier'] or history['prior_frontier'][-1]['run'] != run:
            raise ValueError('当前命令不是完整前沿的唯一最后保留。')
        if (not history['driver'] or history['driver']['state'] != 'RESERVED' or
                history['driver']['generation'] != run['generation'] or
                history['authorization'] != run['input']['handoff_coordination_context']['authorization']):
            raise ValueError('最终绑定的原责任或原累计授权发生变化。')
        if run['input']['handoff_coordination_context']['material_observation'] != history['material_observation']:
            raise ValueError('保留或绑定后原关系材料发生晚变。')
