"""Candidate dependency changes and exact impact previews; never replace a plan."""
from .plans import PlanCommands
from .store import digest
from .work_transitions import source_ref


PROTOCOL = 'DEPENDENCY_ADJUSTMENT_V1'
SCHEMA = {'schema_version': 1, 'action': 'adjust_dependencies',
          'changes': [{'unit_key': 'declared ordinary unit', 'depends': ['declared key']}],
          'reason': 'adjustment reason'}


def context(plan):
    return {'protocol': PROTOCOL, 'plan': {k: plan[k] for k in ('id', 'version')},
            'units': plan['units'], 'schema': SCHEMA,
            'scope': 'DEPENDENCIES_ONLY; PREVIEW_ONLY; ORIGINAL_RESOURCES_AND_AUTHORIZATION'}


def prior_frontier(service, db, rows):
    return digest([{'run_id': r['id'], 'evidence': service.work_units.evidence(db, r)['version']} for r in rows])


def changes(value):
    if not isinstance(value, list) or not 1 <= len(value) <= 20:
        raise ValueError('依赖调整须含1至20项改动。')
    seen = set()
    for change in value:
        if not isinstance(change, dict) or set(change) != {'unit_key', 'depends'}:
            raise ValueError('依赖调整只接受单元身份与前置工作。')
        key, deps = change['unit_key'], change['depends']
        if not isinstance(key, str) or not 1 <= len(key) <= 64 or key in seen:
            raise ValueError('依赖调整的单元身份无效或重复。')
        seen.add(key)
        if (not isinstance(deps, list) or len(deps) > 20 or
                any(not isinstance(k, str) or not 1 <= len(k) <= 64 for k in deps) or len(set(deps)) != len(deps)):
            raise ValueError('依赖调整的前置身份无效或重复。')
    return value


def preview(service, db, item, driver, plan, rows, run, decision):
    """All referenced results retain their original identities and immutable input."""
    frozen = run['input']['coordination_context'].get('adjustment_context')
    if frozen != context(plan):
        raise ValueError('本次准确提案输入未声明当前依赖调整协议与方案，不能补授旧回复。')
    current_prior = prior_frontier(service, db, [r for r in rows if r['id'] != run['id']])
    if run['input']['coordination_context'].get('adjustment_prior_frontier') != current_prior:
        raise ValueError('调整提案冻结输入中的执行或文件前沿已变化；旧回复不能重新绑定新成果，须使用新的已声明提案回合。')
    changes(decision['changes'])
    original = {u['key']: u for u in plan['units']}
    replacements = {}
    for change in decision['changes']:
        key = change['unit_key']
        unit = original.get(key)
        if not unit or unit['role'] == 'coordinate':
            raise ValueError('依赖调整只支持已声明普通单元。')
        if set(change['depends']) == set(unit['depends']):
            raise ValueError('依赖调整须实际改变前置关系。')
        replacements[key] = {**unit, 'depends': sorted(change['depends'])}
    candidate = [replacements.get(u['key'], u) for u in plan['units']]
    PlanCommands.validate_graph({u['key']: u['depends'] for u in candidate})
    for unit in candidate:
        for key in unit['depends']:
            source = original[key]
            if unit['dependency_gate'] in {'files', 'checked'} and not source['outputs']:
                raise ValueError('调整后的文件依赖须有已声明产物。')
            if unit['dependency_gate'] == 'checked' and not source['checks']:
                raise ValueError('调整后的检查依赖须有已声明本地检查。')

    resolved = {u['key']: service.work_units.resolve(db, plan, u['key'], rows) for u in plan['units']}
    latest = {key: value['run'] for key, value in resolved.items() if value['run']}
    evidence = {key: value['evidence'] for key, value in resolved.items() if value['run']}
    reasons = {u['key']: (['DEPENDENCIES_CHANGED'] if u['key'] in replacements else []) for u in candidate}
    for key, old in latest.items():
        e, unit = evidence[key], original[key]
        if old['unit_revision'] != digest(unit) or not e['result_available'] or not e['exact_identity']:
            reasons[key].append('RESULT_UNAVAILABLE')
        elif unit['outputs']:
            try:
                service.work_artifacts.require(e, 'checked' if unit['checks'] else 'files')
            except ValueError:
                reasons[key].append('ARTIFACT_UNAVAILABLE')
        try:
            source_plan = service.collaboration.get(db, 'teamwork_plan', old['plan_id'])
            source_unit = next(u for u in source_plan['units'] if u['key'] == key)
            service.work_transitions.validate_run(db, source_plan, source_unit, old, rows,
                                                  frozenset({(source_plan['id'], key)}))
            if resolved[key]['problems']:
                raise ValueError('沿用原来源已变化')
        except ValueError:
            reasons[key].append('FROZEN_INPUT_CHANGED')

    # Old and new graphs both matter when an edge is removed or redirected.
    affected = {key for key, values in reasons.items() if values}
    while True:
        more = {u['key'] for u in candidate if set(u['depends'] + original[u['key']]['depends']) & affected} - affected
        if not more:
            break
        for key in more:
            reasons[key].append('AFFECTED_DEPENDENCY')
        affected.update(more)
    units = []
    for unit in candidate:
        key = unit['key']; old, e = latest.get(key), evidence.get(key)
        state = ('AFFECTED' if key in affected else 'NOT_RUN' if not old else
                 'HISTORICAL_COORDINATOR' if unit['role'] == 'coordinate' else 'ELIGIBLE_RESULT')
        source = None
        if old:
            source = source_ref(old, e)
        units.append({'key': key, 'title': unit['title'], 'state': state, 'reasons': reasons[key],
                      'old_depends': original[key]['depends'], 'candidate_depends': unit['depends'],
                      'candidate_revision': digest(unit), 'source': source})
    auth = service.work_units.authorization(db, driver)
    body = {'base_plan': {k: plan[k] for k in ('id', 'version')}, 'candidate_units': candidate}
    return {**body, 'candidate_version': digest(body), 'units': units,
            'direct_changes': [u['key'] for u in candidate if u['key'] in replacements],
            'affected_units': [u['key'] for u in candidate if u['key'] in affected],
            'eligible_results': [u['key'] for u in units if u['state'] == 'ELIGIBLE_RESULT'],
            'proposal_lineage': 'OLD_ARRANGEMENTS_INVALID_AFTER_PLAN_CHANGE; COORDINATOR_RESULTS_HISTORICAL_ONLY',
            'usage': service.work_units.usage(db, rows), 'authorization': auth,
            'effect': 'PREVIEW_ONLY; NO_PLAN_CHANGE_OR_DISPATCH',
            'independent_review': 'NOT_PERFORMED', 'human_acceptance': 'NOT_RECORDED'}
