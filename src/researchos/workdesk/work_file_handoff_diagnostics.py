"""Read-only sources for the current saved fixed handoff path.

Each call creates a complete path observation. Its result is descriptive and
confers no save, input, or execution permission.
"""
from copy import deepcopy

from .store import digest
from .work_file_handoff_apply import APPLICATION_KIND
from .work_file_handoff_chain_apply import _PathObservation
from .work_transitions import plan_ref, source_ref


PROTOCOL = 'HANDOFF_PATH_SOURCE_DIAGNOSTIC_V1'
PERMISSIONS = {'save_permission': False, 'current_input_permission': False,
               'execution_permission': False, 'no_dispatch': True}


def read_sources(service, db, plan, *, _read=None):
    """Accept only the database's complete current plan; return detached facts."""
    if not isinstance(plan, dict) or plan.get('mode') != 'fixed' or not plan.get('file_handoff_lineage'):
        raise ValueError('来源诊断须读取当前已完整保存的固定交接路径。')
    observation = _read if _read is not None else _PathObservation(service, db, plan)
    proof = observation.require(service.work_file_handoff_applications, db, plan)
    rows = service.work_units.rows(db, plan['item_id'])
    by_id = {r['id']: r for r in rows}
    plans = {p['id']: p for p in proof['plans']}
    root = service.work_file_handoff_ledger.get(db, APPLICATION_KIND, proof['edges'][0])['snapshot']['lineage']['proof']
    consumer = plan_ref(plan)
    units = []
    for unit in plan['units']:
        key = unit['key']
        value = {'key': key, 'definition': unit, 'unit_revision': digest(unit),
                 'consumer_plan': consumer, 'source_plan': None, 'source': None,
                 'evidence_reference': None, 'raw_state': None, **PERMISSIONS}
        if key == proof['retired_source']:
            historical = proof['historical_source']
            run = by_id[historical['run_id']]
            raw = historical['evidence']
            value.update(qualification='HISTORICAL_RETIRED',
                         historical_source=historical,
                         handoff={'root_application': proof['edges'][0],
                                  'delivery': root['delivery'], 'completion': root['completion']})
        else:
            local = [r for r in rows if r['plan_id'] == plan['id'] and r['unit_key'] == key]
            inherited = plan.get('result_lineage', {}).get(key)
            run = local[0] if local else by_id.get(inherited['run_id']) if inherited else None
            raw = observation.evidence[run['id']] if run else None
            if run:
                # The complete verifier has already checked the exact definition,
                # original spec, frozen dependencies, and every historical file.
                if raw['result_available']:
                    value['qualification'] = 'CURRENT_RESULT'
                else:
                    value['qualification'] = 'KNOWN_TERMINAL_WITHOUT_RESULT'
                if key == proof['completed_key']:
                    value['handoff'] = {'root_application': proof['edges'][0],
                        'delivery': root['delivery'], 'completion': root['completion'],
                        'manifest': raw['file_artifacts']['manifest']}
            else:
                value['qualification'] = 'NOT_RUN'
        if run:
            value.update(source_plan=plans[run['plan_id']], source=source_ref(run, raw),
                evidence_reference={'run_id': run['id'], 'version': raw['version'],
                                    'result_version': raw['result_version']}, raw_state=raw['state'])
        units.append(value)
    frontier = service.work_units.frontier(db, plan['item_id'], _read=observation)
    observation.require(service.work_file_handoff_applications, db, plan)
    return deepcopy({'protocol': PROTOCOL, 'historical_integrity': 'VERIFIED',
        'consumer_plan': consumer, 'source_plans': proof['plans'][:-1],
        'path_version': digest(proof), 'frontier': frontier, 'units': units, **PERMISSIONS})
