"""Public explicit ROUND command and immutable record projection."""
from copy import deepcopy

from .work_transitions import plan_ref


def observe(service, db, item, driver, plan):
    service.work_units.checked(db, {'id': item['id'], 'revision': item['revision'],
        'current_driver': service.work_drivers.reference(driver), 'plan': plan_ref(plan)})
    service.work_flows.require_stopped(db, item['id'])
    if plan.get('coordinator_file_handoff_round_lineage'):
        from .work_coordinator_handoff_mixed_checkpoints import WorkCoordinatorHandoffMixedCheckpoints
        return WorkCoordinatorHandoffMixedCheckpoints(service).observe(db, plan)
    from .work_coordinator_handoff_round_sources import WorkCoordinatorHandoffRoundSources
    return WorkCoordinatorHandoffRoundSources(service).observe(db, plan)


def view(service, db, item, driver, plan):
    result = {'can_revise': False, 'source': None, 'unit_key': None,
              'record': plan.get('coordination_revision'), 'problems': []}
    try:
        parent = observe(service, db, item, driver, plan)
        result.update(can_revise=True, source=parent['source'],
                      unit_key=parent['selected_unit']['definition']['key'])
    except (ValueError, OSError) as exc:
        result['problems'] = [str(exc)]
    return deepcopy(result)


def revise(service, db, p, item, driver, plan, reason):
    parent = observe(service, db, item, driver, plan)
    if (p['authorization'] != service.work_units.auth_ref(parent['history']['authorization']) or
            p['frontier'] != service.work_units.frontier(db, item['id'])):
        raise ValueError('新提案回合的原授权或准确执行前沿已变化。')
    source = parent['source']
    if p['run_id'] != source['run_id'] or p['result_version'] != source['version']:
        raise ValueError('须引用本方案最新准确协调回合及结果版本。')
    if plan.get('coordinator_file_handoff_round_lineage'):
        from .work_coordinator_handoff_mixed_apply import WorkCoordinatorHandoffMixedApplications
        saved = WorkCoordinatorHandoffMixedApplications(service).save(db, plan, reason)
    else:
        from .work_coordinator_handoff_round_apply import WorkCoordinatorHandoffRoundApplications
        saved = WorkCoordinatorHandoffRoundApplications(service).save(db, plan, reason)
    child = service.teamwork.current(db, item['id'])
    return deepcopy({'coordination_revision': child['coordination_revision'], 'plan': plan_ref(child),
        'driver': service.work_drivers.reference(saved['driver']),
        'authorization': service.work_units.auth_ref(saved['authorization']),
        'registration': saved['saved_path']['edge']['registration'],
        'no_dispatch': True, 'automatically_resumed': False})


def stage(service, db, item, plan, history):
    """The caller's verified read supplies the path; no proof is persisted here."""
    from .work_coordinator_handoff_round_records import KIND_FOR
    from .work_coordinator_handoff_mixed_records import KIND_FOR as MIXED_KINDS
    records = [r for kind in (*KIND_FOR.values(), *MIXED_KINDS.values())
               for r in service.collaboration.all(db, kind) if r.get('item_id') == item['id']] if history else []
    path = history.get('saved_path') if history else None
    edges = []
    cursor = path
    while cursor and 'edge' in cursor:
        edges.append(deepcopy(cursor['edge']))
        cursor = cursor['parent_path']
    return {'protocol': 'COORDINATOR_FILE_HANDOFF_PUBLIC_STAGE_V3', 'plan': plan_ref(plan),
        'saved_path': path, 'adjustment_saved_path': cursor,
        'round_edges': list(reversed(edges)), 'round_records': records,
        'round_capability': history.get('round_capability') if history else None,
        'round_source': plan.get('coordination_revision', {}).get('source'),
        'post_round_adjustment': 'NOT_OPEN' if plan.get('coordinator_file_handoff_round_lineage') else None}
