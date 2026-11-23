"""Bounded ordinary-unit revision proposals and immutable, non-executable previews."""
import re
import time

from .contracts import bounded_text
from .plans import PlanCommands
from .store import digest
from .work_reworks import TERMINAL, diagnostics
from .work_transitions import plan_ref


PROTOCOL = 'ORDINARY_UNIT_REVISION_PREVIEW_V1'
SCHEMA = {'schema_version': 1, 'action': 'revise_units', 'changes': [
    {'operation': 'add or revise', 'unit_key': 'ordinary key',
     'basis': {'unit_key': 'original ordinary key', 'unit_revision': 'exact definition digest',
               'work_requirements_version': 'exact original requirements digest', 'reason': 'duty/scope basis'},
     'definition': {'title': 'local title', 'goal': 'local goal', 'acceptance': 'local completion requirements',
                    'member': 'one exact reference from allowed_members', 'depends': ['candidate key']}}],
    'reason': 'local revision reason'}


def key(value):
    return isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9_-]{1,64}', value)


def changes(value):
    if not isinstance(value, list) or not 1 <= len(value) <= 20:
        raise ValueError('普通单元修订须含1至20项改动。')
    seen = set()
    for change in value:
        if (not isinstance(change, dict) or set(change) != {'operation', 'unit_key', 'basis', 'definition'} or
                not isinstance(change['operation'], str) or change['operation'] not in {'add', 'revise'} or
                not key(change['unit_key']) or change['unit_key'] in seen):
            raise ValueError('普通单元修订操作或身份无效/重复。')
        seen.add(change['unit_key'])
        basis, definition = change['basis'], change['definition']
        if (not isinstance(basis, dict) or set(basis) != {'unit_key', 'unit_revision', 'work_requirements_version', 'reason'} or
                not key(basis['unit_key']) or any(not isinstance(basis[k], str) or not 1 <= len(basis[k]) <= 128
                    for k in ('unit_revision', 'work_requirements_version'))):
            raise ValueError('须声明准确原职责和任务要求版本。')
        bounded_text(basis['reason'], '原职责/范围依据', 2000)
        if not isinstance(definition, dict) or set(definition) != {'title', 'goal', 'acceptance', 'member', 'depends'}:
            raise ValueError('局部定义只接受名称、局部目标/要求、原成员和完整前置关系。')
        for name, limit in [('title', 160), ('goal', 4000), ('acceptance', 4000)]:
            bounded_text(definition[name], '局部' + name, limit)
        if not isinstance(definition['member'], dict):
            raise ValueError('须选择原方案的准确成员引用。')
        deps = definition['depends']
        if not isinstance(deps, list) or len(deps) > 20 or any(not key(k) for k in deps) or len(set(deps)) != len(deps):
            raise ValueError('候选前置工作身份无效或重复。')
    return value


def allowed_members(plan):
    members = [plan['coordinator'], plan['integrator'], *(u['member'] for u in plan['units'])]
    return list({digest(m['reference']): m['reference'] for m in members}.values())


def historical_keys(db, item_id):
    import json
    return sorted({u['key'] for r in db.execute(
        "SELECT data FROM collaboration WHERE kind='teamwork_plan' AND json_extract(data,'$.item_id')=?",
        (item_id,)) for u in json.loads(r[0])['units']})


def context(service, db, plan, rows):
    return {'protocol': PROTOCOL, 'plan': plan_ref(plan), 'units': plan['units'], 'schema': SCHEMA,
            'work_requirements': plan['work_requirements'], 'work_requirements_version': digest(plan['work_requirements']),
            'allowed_members': allowed_members(plan), 'historical_keys': historical_keys(db, plan['item_id']),
            'diagnostics': diagnostics(service, db, plan, rows),
            'prior_frontier': service.work_rework_applications.frontier(db, rows),
            'scope': 'MODEL_PROPOSED_LOCAL_DUTY_ONLY; ORIGINAL_RESOURCES_AND_AUTHORIZATION; PREVIEW_ONLY'}


def require_history(service, db, rows):
    for run in rows:
        raw = service.work_units.evidence(db, run)
        original = service.collaboration.get(db, 'teamwork_plan', run['plan_id'])
        unit = next((u for u in original['units'] if u['key'] == run['unit_key']), None)
        if (not raw['exact_identity'] or raw['blockers'] or raw['state'] not in TERMINAL or not unit or
                digest({k: v for k, v in original.items() if k != 'version'}) != original['version'] or
                run['plan_version'] != original['version'] or run['input']['unit'] != unit or
                run['unit_revision'] != digest(unit)):
            raise ValueError('原运行身份、终态或退出证据不足；未知不能通过局部修订重发。')


def preview(service, db, item, driver, plan, rows, run, decision):
    frozen = run['input']['coordination_context'].get('unit_revision_context')
    prior = [r for r in rows if r['id'] != run['id']]
    if not frozen or frozen.get('protocol') != PROTOCOL:
        raise ValueError('本次准确输入未声明普通单元修订协议，不能补授旧回复。')
    current = context(service, db, plan, prior)
    if frozen != current:
        raise ValueError('原职责、资源或执行/文件前沿已变化；旧回复不能重新绑定新来源。')
    require_history(service, db, rows)
    changes(decision['changes'])
    original = {u['key']: u for u in plan['units']}
    replacements, additions, bases = {}, [], {}
    for change in decision['changes']:
        name, basis, definition = change['unit_key'], change['basis'], change['definition']
        source = original.get(basis['unit_key'])
        if (not source or source['role'] == 'coordinate' or basis['unit_revision'] != digest(source) or
                basis['work_requirements_version'] != current['work_requirements_version']):
            raise ValueError('局部修订须引用当前准确原普通职责和原任务要求。')
        if change['operation'] == 'add':
            if name in current['historical_keys']:
                raise ValueError('新增单元不能复用当前或历史方案单元身份。')
        elif name != source['key']:
            raise ValueError('修订依据必须就是被修订单元的准确原定义。')
        if definition['member'] not in current['allowed_members']:
            raise ValueError('只能使用本次原方案已声明的成员/连接/额度来源。')
        member = service.teamwork.member(db, definition['member'])
        unit = {**source, 'key': name, 'member': member, 'depends': sorted(definition['depends']),
                **{k: bounded_text(definition[k], '局部' + k, limit)
                   for k, limit in [('title', 160), ('goal', 4000), ('acceptance', 4000)]}}
        if change['operation'] == 'revise':
            if {**source, 'depends': sorted(source['depends'])} == unit:
                raise ValueError('局部修订须实际改变允许的定义字段。')
            replacements[name] = unit
        else:
            additions.append(unit)
        bases[name] = basis
    candidate = [replacements.get(u['key'], u) for u in plan['units']] + additions
    if len(candidate) > 20:
        raise ValueError('候选完整方案仍最多20项工作单元。')
    units = {u['key']: u for u in candidate}
    PlanCommands.validate_graph({u['key']: u['depends'] for u in candidate})
    for u in candidate:
        for dep in u['depends']:
            if u['dependency_gate'] in {'files', 'checked'} and not units[dep]['outputs']:
                raise ValueError('候选文件依赖须有原声明产物。')
            if u['dependency_gate'] == 'checked' and not units[dep]['checks']:
                raise ValueError('候选检查依赖须有原声明检查。')

    observed = {d['key']: d for d in current['diagnostics']}
    reasons = {u['key']: (['UNIT_ADDED'] if u['key'] not in original else
                          ['DEFINITION_CHANGED'] if u['key'] in replacements else []) for u in candidate}
    for name, d in observed.items():
        if not d['source']:
            continue
        if not d['result_available']:
            reasons[name].append('RESULT_UNAVAILABLE')
        if d['lineage_problems']:
            reasons[name].append('LINEAGE_INVALID')
        if d['frozen_input_problems']:
            reasons[name].append('FROZEN_INPUT_CHANGED')
        if original[name]['outputs']:
            raw = service.work_units.evidence(db, next(r for r in rows if r['id'] == d['source']['run_id']))
            try:
                service.work_artifacts.require(raw, 'checked' if original[name]['checks'] else 'files')
            except ValueError:
                reasons[name].append('ARTIFACT_UNAVAILABLE')
    affected = {name for name, values in reasons.items() if values}
    while True:
        more = {u['key'] for u in candidate if
                set(u['depends'] + original.get(u['key'], {}).get('depends', [])) & affected} - affected
        if not more:
            break
        for name in more:
            reasons[name].append('AFFECTED_DEPENDENCY')
        affected.update(more)
    for name in affected:
        u = units[name]
        if u['role'] != 'coordinate' and (u['outputs'] or u['checks'] or u['write_paths'] or u['dependency_gate'] != 'result'):
            raise ValueError('新增或受影响文件/写入工作须另行处理：' + name)
    entries = []
    for u in candidate:
        name, d = u['key'], observed.get(u['key'])
        state = ('AFFECTED' if name in affected else 'HISTORICAL_COORDINATOR' if u['role'] == 'coordinate'
                 else 'ELIGIBLE_RESULT' if d['source'] else 'NOT_RUN')
        entries.append({'key': name, 'title': u['title'], 'state': state, 'reasons': reasons[name],
                        'basis': bases.get(name), 'original_definition': original.get(name), 'candidate_definition': u,
                        'old_depends': original.get(name, {}).get('depends', []), 'candidate_depends': u['depends'],
                        'original_revision': digest(original[name]) if name in original else None,
                        'candidate_revision': digest(u), 'source': d['source'] if d else None, 'diagnostic': d})
    auth = service.work_units.authorization(db, driver)
    blockers = []
    if not auth or len(rows) >= auth['max_attempts_total']:
        blockers.append('原累计授权次数不足；预览不增加授权')
    if not auth or time.time() >= auth['dispatch_before']:
        blockers.append('原新增派发截止已到期；预览不延长截止')
    body = {'base_plan': plan_ref(plan), 'candidate_units': candidate, 'units': entries,
            'work_requirements_version': current['work_requirements_version']}
    return {**body, 'candidate_version': digest(body),
            'direct_changes': [u['key'] for u in candidate if u['key'] in bases],
            'added_units': [u['key'] for u in additions], 'revised_units': list(replacements),
            'affected_units': [u['key'] for u in candidate if u['key'] in affected],
            'eligible_results': [u['key'] for u in entries if u['state'] == 'ELIGIBLE_RESULT'],
            'usage': service.work_units.usage(db, rows), 'authorization': auth, 'execution_blockers': blockers,
            'scope_assessment': 'MODEL_PROPOSED_LOCAL_DUTY; STRUCTURE_CHECKED; NO_NEW_AUTHORIZATION',
            'effect': 'PREVIEW_ONLY; NO_PLAN_CHANGE_OR_DISPATCH',
            'independent_review': 'NOT_PERFORMED', 'human_acceptance': 'NOT_RECORDED'}
