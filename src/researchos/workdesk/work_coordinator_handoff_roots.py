"""Internal preparation of new coordinator handoff roots. No persistence or grant.

This first reader deliberately closes as soon as driver/run history exists.
Its empty-frontier seed is not an executable coordination context.
"""
from copy import deepcopy
import math
from pathlib import Path
import re

from .contracts import bounded_text
from .plans import PlanCommands
from .store import digest
from .teamwork import ROLES, write_paths
from .work_artifacts import definition as artifact_definition
from .work_file_handoff_config import definition as relation_definition
from .work_transitions import plan_ref


ROOT = 'COORDINATOR_FILE_HANDOFF_ROOT_V1'
PREPARATION = 'COORDINATOR_FILE_HANDOFF_PREPARATION_V1'
EMPTY_FRONTIER = 'COORDINATOR_FILE_HANDOFF_EMPTY_FRONTIER_V1'
MARKER_FIELD = 'coordinator_file_handoff_root'
CONFIG_FIELDS = ('mode', 'coordinator', 'integrator', 'max_parallel',
                 'hard_cash_limit_cny', 'units', 'file_handoffs')
REQUEST_FIELDS = {'id', 'revision', 'current_plan', *CONFIG_FIELDS}
PLAN_FIELDS = {'id', 'item_id', 'item_revision', 'project_id', 'work_requirements',
               *CONFIG_FIELDS, 'previous_plan', 'state', 'created', MARKER_FIELD, 'version'}
UNIT_FIELDS = {'key', 'title', 'goal', 'acceptance', 'role', 'member', 'depends',
               'write_paths', 'outputs', 'checks', 'dependency_gate'}


def root_marker(plan, project_root):
    """Pure server-side construction; a marker alone proves no saved identity."""
    if not isinstance(project_root, str) or str(Path(project_root).absolute()) != project_root:
        raise ValueError('根目录须为准确绝对路径。')
    body = {'protocol': ROOT, 'plan_id': plan['id'], 'item_id': plan['item_id'],
            'item_revision': plan['item_revision'], 'project_id': plan['project_id'],
            'project_root': project_root,
            'work_requirements_version': digest(plan['work_requirements']),
            'configuration_version': digest({k: plan[k] for k in CONFIG_FIELDS})}
    return {**body, 'version': digest(body)}


class WorkCoordinatorHandoffRoots:
    def __init__(self, service):
        self.s = service

    def _empty_history(self, db, item_id, plan=None):
        plans = [p for p in self.s.collaboration.all(db, 'teamwork_plan')
                 if p.get('item_id') == item_id]
        current = self.s.teamwork.current(db, item_id)
        if ((plan is None and (plans or current is not None)) or
                (plan is not None and (plans != [plan] or current != plan))):
            raise ValueError('新原方案准备不接受旧方案、旧current或多方案历史。')
        if self.s.work_units.rows(db, item_id):
            raise ValueError('空历史读取器不接受任何已有合作运行。')
        for kind in ('work_driver', 'work_driver_revision', 'work_unit_authorization',
                     'work_unit_authorization_revision'):
            if any(r.get('item_id') == item_id for r in self.s.collaboration.all(db, kind)):
                raise ValueError('空历史读取器不接受已有责任或授权历史。')
        self.s.work_drivers.guard(db, item_id, shared=True)
        self.s.work_flows.require_stopped(db, item_id)

    def _item(self, db, request):
        item = self.s.work_items.checked(db, request)
        self.s.work_items.project(db, item['project_id'])
        if item['state'] != 'OPEN' or item['project_id'] is None:
            raise ValueError('新原方案须属于准确项目及OPEN任务。')
        return item

    def _configuration(self, db, raw, root):
        if set(raw) != set(CONFIG_FIELDS) or raw['mode'] not in ('leader', 'host_experts'):
            raise ValueError('新根须为完整负责人／主持人配置。')
        if type(raw['max_parallel']) is not int or raw['max_parallel'] != 1:
            raise ValueError('新关系根首批须显式串行max_parallel=1。')
        cash = raw['hard_cash_limit_cny']
        if cash is not None and (type(cash) not in (int, float) or
                                 not math.isfinite(cash) or not 0 <= cash <= 1000000):
            raise ValueError('硬费用上限无效。')
        coordinator = self.s.teamwork.member(db, raw['coordinator'])
        integrator = self.s.teamwork.member(db, raw['integrator'])
        definitions = raw['units']
        if not isinstance(definitions, list) or not 1 <= len(definitions) <= 20:
            raise ValueError('新原方案需要1至20项完整工作单元。')
        units, keys = [], set()
        for value in definitions:
            if not isinstance(value, dict) or set(value) - UNIT_FIELDS:
                raise ValueError('工作单元字段不支持。')
            key, role = value.get('key'), value.get('role')
            if (not isinstance(key, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', key)
                    or key in keys):
                raise ValueError('工作单元身份无效或重复。')
            keys.add(key)
            if not isinstance(role, str) or role not in ROLES:
                raise ValueError('工作单元职责无效。')
            depends = value.get('depends', [])
            if (not isinstance(depends, list) or len(depends) > 20 or
                    any(not isinstance(k, str) for k in depends) or len(set(depends)) != len(depends)):
                raise ValueError('前置工作引用无效或重复。')
            unit = {'key': key, 'role': role,
                    'title': bounded_text(value.get('title'), '工作名称', 160),
                    'goal': bounded_text(value.get('goal'), '单元目标', 4000),
                    'acceptance': bounded_text(value.get('acceptance'), '完成要求', 4000),
                    'member': self.s.teamwork.member(db, value.get('member')),
                    'depends': list(depends), 'write_paths': write_paths(value.get('write_paths', []), root),
                    **artifact_definition(value)}
            if role == 'coordinate' and (unit['member'] != coordinator or unit['write_paths'] or
                                          unit['outputs'] or unit['checks']):
                raise ValueError('coordinate须由当前负责人／主持人承担且无文件写入、产物或检查。')
            units.append(unit)
        PlanCommands.validate_graph({u['key']: u['depends'] for u in units})
        if not any(u['role'] == 'coordinate' for u in units):
            raise ValueError('新关系根须预先声明真实coordinate单元。')
        relations = relation_definition(raw['file_handoffs'], units, 1)
        if len(relations) != 1:
            raise ValueError('新关系根须声明唯一完整A→B关系。')
        by_key = {u['key']: u for u in units}
        for unit in units:
            for key in unit['depends']:
                previous = by_key[key]
                if unit['dependency_gate'] in ('files', 'checked') and not previous['outputs']:
                    raise ValueError('文件依赖要求前置单元声明产物。')
                if unit['dependency_gate'] == 'checked' and not previous['checks']:
                    raise ValueError('检查依赖要求前置单元声明检查。')
        owners = {}
        for unit in units:
            for name in unit['outputs']:
                owners.setdefault(name.casefold(), []).append(unit['key'])
        relation = relations[0]
        pair = {relation['source_unit'], relation['target_unit']}
        shared = {n.casefold() for n in relation['paths']}
        if any(len(ks) > 1 and (n not in shared or len(ks) != 2 or set(ks) != pair)
               for n, ks in owners.items()):
            raise ValueError('仅准确关系两端可复用完整产物集合。')
        return {'mode': raw['mode'], 'coordinator': coordinator, 'integrator': integrator,
                'max_parallel': 1, 'hard_cash_limit_cny': cash, 'units': units,
                'file_handoffs': relations}

    def _snapshot(self, db, item, configuration):
        root = self.s.work_file_rework_applications.root(db, item)
        relation = configuration['file_handoffs'][0]
        observation = self.s.work_file_rework_applications.observe(root, relation['paths'])
        by_key = {u['key']: u for u in configuration['units']}
        protected, pending = set(), [relation['source_unit'], relation['target_unit']]
        while pending:
            key = pending.pop()
            if key not in protected:
                protected.add(key)
                pending.extend(by_key[key]['depends'])
        permissions = {'save_permission': False, 'current_input_permission': False,
                       'execution_permission': False, 'no_dispatch': True}
        seed = {'protocol': EMPTY_FRONTIER, 'phase': 'NO_RUNS', 'prior_frontier': [],
                'units': [{'key': u['key'], 'definition_version': digest(u), 'state': 'NOT_RUN'}
                          for u in configuration['units']],
                'coordinators': [{'key': u['key'], 'depends': list(u['depends']),
                                  'has_no_declared_dependencies': not u['depends']}
                                 for u in configuration['units'] if u['role'] == 'coordinate'],
                **permissions, 'scope': 'EMPTY_HISTORY_ONLY; NOT_A_RUNTIME_COORDINATION_CONTEXT'}
        body = {'protocol': PREPARATION, 'task': {'id': item['id'], 'revision': item['revision'],
                'project_id': item['project_id'], 'work_requirements': {k: item[k] for k in
                    ('title', 'goal', 'constraints_text', 'acceptance')}},
                'project_root': root, 'configuration': configuration, 'relation': relation,
                'protected_keys': sorted(protected), 'write_before_observation': observation,
                'coordination_seed': seed, **permissions,
                'scope': 'INTERNAL_NEW_ROOT_PREPARATION; NO_PLAN_SAVE_OR_DISPATCH'}
        return deepcopy({**body, 'version': digest(body)})

    def prepare(self, db, request):
        try:
            return self._prepare(db, request)
        except (KeyError, TypeError, AttributeError) as exc:
            raise ValueError('新原方案准备的完整字段或持久来源无效。') from exc

    def _prepare(self, db, request):
        if not isinstance(request, dict) or set(request) != REQUEST_FIELDS or request['current_plan'] is not None:
            raise ValueError('新原方案准备只接受完整明确配置及current_plan=null。')
        writes = db.total_changes
        item = self._item(db, request)
        self._empty_history(db, item['id'])
        root = Path(self.s.work_file_rework_applications.root(db, item))
        configuration = self._configuration(db, {k: request[k] for k in CONFIG_FIELDS}, root)
        result = self._snapshot(db, item, configuration)
        self._empty_history(db, item['id'])
        if db.total_changes != writes or self.s.work_items.item(db, item['id']) != item:
            raise ValueError('准备观察期间任务或数据库已变化。')
        return result

    def read_unstarted(self, db, plan):
        try:
            return self._read_unstarted(db, plan)
        except (KeyError, TypeError, AttributeError) as exc:
            raise ValueError('完整新原方案字段或持久来源无效。') from exc

    def _read_unstarted(self, db, plan):
        if (not isinstance(plan, dict) or set(plan) != PLAN_FIELDS or
                not isinstance(plan['id'], str) or not plan['id'].startswith('teamwork:') or
                len(plan['id']) <= len('teamwork:') or
                plan['previous_plan'] is not None or plan['state'] != 'PREPARED' or
                type(plan['created']) not in (int, float) or not math.isfinite(plan['created']) or plan['created'] < 0 or
                plan['version'] != digest({k: v for k, v in plan.items() if k != 'version'})):
            raise ValueError('完整新原方案或独立根身份不一致。')
        writes = db.total_changes
        item = self._item(db, {'id': plan['item_id'], 'revision': plan['item_revision']})
        root = self.s.work_file_rework_applications.root(db, item)
        if plan[MARKER_FIELD] != root_marker(plan, root):
            raise ValueError('根标记、原配置或准确项目目录已变化。')
        if (item['project_id'] != plan['project_id'] or
                {k: item[k] for k in ('title', 'goal', 'constraints_text', 'acceptance')} != plan['work_requirements']):
            raise ValueError('根原要求或项目已变化。')
        self._empty_history(db, item['id'], plan)
        raw = {k: deepcopy(plan[k]) for k in CONFIG_FIELDS}
        raw['coordinator'] = raw['coordinator']['reference']
        raw['integrator'] = raw['integrator']['reference']
        for unit in raw['units']:
            unit['member'] = unit['member']['reference']
        configuration = self._configuration(db, raw, Path(root))
        if configuration != {k: plan[k] for k in CONFIG_FIELDS}:
            raise ValueError('新根配置不是准确成员和完整规范定义。')
        result = self._snapshot(db, item, configuration)
        self._empty_history(db, item['id'], plan)
        if db.total_changes != writes or self.s.work_items.item(db, item['id']) != item:
            raise ValueError('新根观察期间任务或数据库已变化。')
        return deepcopy({'root': plan_ref(plan), 'marker': plan[MARKER_FIELD], 'preparation': result,
                         'save_permission': False, 'current_input_permission': False,
                         'execution_permission': False, 'no_dispatch': True})
