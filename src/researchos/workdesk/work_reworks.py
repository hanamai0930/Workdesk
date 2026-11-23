"""Exact ordinary text rework diagnostics and previews. No plan change or dispatch."""
import time

from .store import digest
from .work_adjustments import prior_frontier
from .work_transitions import plan_ref, source_ref


PROTOCOL = 'ORDINARY_TEXT_REWORK_PREVIEW_V1'
SCHEMA = {'schema_version': 1, 'action': 'rework_units',
          'changes': [{'unit_key': 'declared ordinary unit', 'source_run_id': 'exact run',
                       'source_version': 'exact raw evidence version'}], 'reason': 'rework reason'}
TERMINAL = {'COMPLETED', 'FAILED_NO_SEND', 'FAILED', 'INTERRUPTED', 'STOPPED_RUNTIME'}


def changes(value):
    if not isinstance(value, list) or not 1 <= len(value) <= 20:
        raise ValueError('文字返工须含1至20项准确来源。')
    seen = set()
    for change in value:
        if not isinstance(change, dict) or set(change) != {'unit_key', 'source_run_id', 'source_version'}:
            raise ValueError('文字返工只接受单元身份、原运行和准确证据版本。')
        if any(not isinstance(change[k], str) or not change[k].strip() or len(change[k]) > limit
               for k, limit in [('unit_key', 64), ('source_run_id', 200), ('source_version', 128)]):
            raise ValueError('文字返工的准确来源字段无效。')
        if change['unit_key'] in seen:
            raise ValueError('文字返工不能重复选择同一单元。')
        seen.add(change['unit_key'])
    return value


def diagnostics(service, db, plan, rows):
    values = []
    for unit in plan['units']:
        if unit['role'] == 'coordinate':
            continue
        resolved = service.work_units.resolve(db, plan, unit['key'], rows)
        run = resolved['run']
        raw = service.work_units.evidence(db, run) if run else None
        problems, identity = [], True
        if run:
            original = service.collaboration.get(db, 'teamwork_plan', run['plan_id'])
            old_unit = next((u for u in original['units'] if u['key'] == unit['key']), None)
            identity = bool(old_unit == unit and digest({k: v for k, v in original.items() if k != 'version'}) == original['version']
                            and run['plan_version'] == original['version'] and run['unit_revision'] == digest(unit))
            try:
                if not old_unit:
                    raise ValueError('返工原单元不存在')
                service.work_transitions.validate_run(db, original, old_unit, run, rows,
                                                       frozenset({(original['id'], unit['key'])}), consumer_plan=plan)
            except ValueError as exc:
                problems.append(str(exc))
        values.append({'key': unit['key'], 'source': source_ref(run, raw) if run else None,
                       'state': raw['state'] if raw else 'NOT_RUN',
                       'exact_identity': bool(identity and (not raw or raw['exact_identity'])),
                       'result_available': bool(raw and raw['result_available']),
                       'blockers': raw['blockers'] if raw else [],
                       'lineage_problems': resolved['problems'], 'frozen_input_problems': problems,
                       'inherited': resolved['inherited']})
    return values


def context(service, db, plan, rows):
    return {'protocol': PROTOCOL, 'plan': plan_ref(plan), 'units': plan['units'], 'schema': SCHEMA,
            'diagnostics': diagnostics(service, db, plan, rows),
            'scope': 'TEXT_REWORK_PREVIEW_ONLY; UNCHANGED_DEFINITIONS_RESOURCES_AND_AUTHORIZATION'}


def preview(service, db, item, driver, plan, rows, run, decision):
    frozen = run['input']['coordination_context'].get('rework_context')
    current = context(service, db, plan, rows)
    if not frozen or frozen.get('protocol') != PROTOCOL:
        raise ValueError('本次准确输入未声明文字返工协议，不能补授旧回复。')
    if (frozen != current or run['input']['coordination_context'].get('adjustment_prior_frontier') !=
            prior_frontier(service, db, [r for r in rows if r['id'] != run['id']])):
        raise ValueError('返工诊断或执行/文件前沿已变化；旧回复不能重新绑定新来源。')
    changes(decision['changes'])
    units = {u['key']: u for u in plan['units']}
    observed = {d['key']: d for d in current['diagnostics']}
    selected = {c['unit_key'] for c in decision['changes']}
    for change in decision['changes']:
        d = observed.get(change['unit_key'])
        if not d or not d['source']:
            raise ValueError('文字返工须选择已运行的普通单元，不能新增工作或修订协调单元。')
        if change['source_run_id'] != d['source']['run_id'] or change['source_version'] != d['source']['version']:
            raise ValueError('文字返工须引用本次输入中的最新准确原运行和证据版本。')
    reasons = {key: (['EXPLICIT_REWORK'] if key in selected else []) for key in units}
    for key, d in observed.items():
        if not d['source']:
            continue
        if not d['exact_identity'] or d['blockers'] or d['state'] not in TERMINAL:
            raise ValueError('返工来源身份、终态或退出证据不足，未知不重发：' + key)
        if not d['result_available']:
            reasons[key].append('RESULT_UNAVAILABLE')
        if d['lineage_problems']:
            reasons[key].append('LINEAGE_INVALID')
        if d['frozen_input_problems']:
            reasons[key].append('FROZEN_INPUT_CHANGED')
        source = d['source']
        original_run = next(r for r in rows if r['id'] == source['run_id'])
        evidence = service.work_units.evidence(db, original_run)
        if units[key]['outputs']:
            try:
                service.work_artifacts.require(evidence, 'checked' if units[key]['checks'] else 'files')
            except ValueError:
                reasons[key].append('ARTIFACT_UNAVAILABLE')
    affected = {key for key, values in reasons.items() if values}
    while True:
        more = {key for key, u in units.items() if set(u['depends']) & affected} - affected
        if not more:
            break
        for key in more:
            reasons[key].append('AFFECTED_DEPENDENCY')
        affected.update(more)
    for key in affected:
        u = units[key]
        if u['role'] != 'coordinate' and (u['outputs'] or u['checks'] or u['write_paths'] or u['dependency_gate'] != 'result'):
            raise ValueError('本批只预览无写入/文件声明的文字返工，文件或写入影响须另行处理：' + key)
    entries = []
    for key, unit in units.items():
        d = observed.get(key)
        state = ('AFFECTED' if key in affected else 'HISTORICAL_COORDINATOR' if unit['role'] == 'coordinate'
                 else 'ELIGIBLE_RESULT' if d['source'] else 'NOT_RUN')
        entries.append({'key': key, 'title': unit['title'], 'state': state, 'reasons': reasons[key],
                        'source': d['source'] if d else None, 'diagnostic': d,
                        'candidate_revision': digest(unit)})
    auth = service.work_units.authorization(db, driver)
    usage = service.work_units.usage(db, rows)
    blockers = []
    if not auth or len(rows) >= auth['max_attempts_total']:
        blockers.append('原累计授权次数不足；预览不增加授权')
    if not auth or time.time() >= auth['dispatch_before']:
        blockers.append('原新增派发截止已到期；预览不延长截止')
    body = {'base_plan': plan_ref(plan), 'candidate_units': plan['units'], 'units': entries}
    return {**body, 'candidate_version': digest(body), 'direct_changes': sorted(selected),
            'affected_units': [key for key in units if key in affected],
            'eligible_results': [u['key'] for u in entries if u['state'] == 'ELIGIBLE_RESULT'],
            'usage': usage, 'authorization': auth, 'execution_blockers': blockers,
            'effect': 'PREVIEW_ONLY; NO_PLAN_CHANGE_OR_DISPATCH',
            'independent_review': 'NOT_PERFORMED', 'human_acceptance': 'NOT_RECORDED'}
