"""Public creation and read-only stage projection for original coordinator roots."""
from copy import deepcopy
import time

from .store import digest, uid
from .work_coordinator_handoff_roots import WorkCoordinatorHandoffRoots, root_marker
from .work_coordinator_handoff_runtime import WorkCoordinatorHandoffRuntime, reader
from .work_coordinator_handoff_records import PERMISSIONS
from .work_transitions import plan_ref

PROTOCOL = 'COORDINATOR_FILE_HANDOFF_PUBLIC_STAGE_V1'
PROTOCOL_V2 = 'COORDINATOR_FILE_HANDOFF_PUBLIC_STAGE_V2'


def save(service, db, request):
    prepared = WorkCoordinatorHandoffRoots(service).prepare(db, request)
    task = prepared['task']
    plan = {'id': 'teamwork:' + uid(), 'item_id': task['id'],
            'item_revision': task['revision'], 'project_id': task['project_id'],
            'work_requirements': task['work_requirements'], **prepared['configuration'],
            'previous_plan': None, 'state': 'PREPARED', 'created': time.time()}
    plan['coordinator_file_handoff_root'] = root_marker(plan, prepared['project_root'])
    plan['version'] = digest(plan)
    service.collaboration.put(db, 'teamwork_plan', plan)
    service.collaboration.put(db, 'teamwork_current',
                              {'id': 'teamwork-current:' + task['id'], 'plan_id': plan['id']})
    service.store.event(None, None, 'teamwork_plan_prepared',
                        {'work_item_id': task['id'], 'item_revision': task['revision'],
                         'plan_id': plan['id'], 'version': plan['version'],
                         'mode': plan['mode'], 'no_dispatch': True}, db)
    runtime = WorkCoordinatorHandoffRuntime(service)
    runtime.activate(db, plan)
    history = runtime.read(db, plan)
    if history['material_observation'] != prepared['write_before_observation']:
        raise ValueError('保存新原根期间原目录材料变化，请重新保存。')
    item = service.work_items.item(db, task['id'])
    catalog = service.resources.catalog(db)
    result = service.teamwork.view(db, item, plan, resource_catalog=catalog)
    from .work_readiness_observations import record
    record(service, db, item, plan, None, request, 'work-item.teamwork-save', catalog)
    return {'teamwork': result,
            'no_dispatch': True, 'execution_unchanged': True}


def view(service, db, item, plan):
    from .work_coordinator_handoff_text_rework_runtime import present as text_present
    if text_present(service, db, plan):
        from .work_coordinator_handoff_text_rework_public import view as text_view
        return text_view(service, db, item, plan)
    from .work_coordinator_handoff_post_round_v3_public import is_v3, view as v3_view
    if is_v3(plan):
        return v3_view(service, db, item, plan)
    runtime = reader(service, db, plan)
    rows = service.work_units.rows(db, item['id'])
    driver = service.work_drivers.current(db, item['id'])
    auth = service.work_units.authorization(db, driver)
    issues = service.work_drivers.view(db, item)
    flow = service.work_flows.view(db, item)
    problems = []
    try:
        history = runtime.read(db, plan)
    except (ValueError, OSError) as exc:
        history = None
        problems.append(str(exc))
    frames = {f['run']['unit_key']: f for f in history['current_frames']} if history else {}
    qualifications = {u['definition']['key']: u['qualification'] for u in history['units']} if history else {}
    base = bool(history and driver and driver['state'] == 'RESERVED' and not issues['stale']
                and not issues['issues'] and not flow['running'])
    allowed = bool(base and auth and len(rows) < auth['max_attempts_total'] and time.time() < auth['dispatch_before'])
    try:
        proposal = service.work_proposals.view(db, item)
    except (ValueError, OSError) as exc:
        proposal = {'record': None, 'reference': None, 'state': 'STALE', 'problems': [str(exc)]}
    units = []
    latest_coordinator = next((r for r in reversed(rows) if r['input']['unit']['role'] == 'coordinate'), None)
    relation = plan['file_handoffs'][0]
    if history and history['phase'] == 'B_COMPLETED':
        qualifications[relation['source_unit']] = 'HISTORICAL_RETIRED'
        qualifications[relation['target_unit']] = 'CURRENT_COMPLETED'
    for unit in plan['units']:
        frame = frames.get(unit['key'])
        run = frame['run'] if frame else next((r for r in reversed(rows) if r['unit_key'] == unit['key'] and r['plan_id'] == plan['id']), None)
        result = frame['source_evidence'] if frame else service.work_units.base_evidence(db, run) if run else None
        ready, reason = False, '；'.join(problems)
        if history:
            try:
                runtime._admit(plan, unit, history['current_frames'], history['phase'])
                ready = True
            except ValueError as exc:
                reason = str(exc)
        proposal_ok = bool(unit['role'] == 'coordinate' or (proposal['state'] == 'VALID' and
            proposal['record']['snapshot']['target'] == {'key': unit['key'], 'revision': digest(unit)} and
            proposal['record']['snapshot']['decision']['retry_of'] is None))
        if ready and not proposal_ok:
            reason = '等待当前有效的负责人／主持人提案选择本单元'
        current_run = bool(run and run['plan_id'] == plan['id'] and run['plan_version'] == plan['version'])
        capture = bool(base and current_run and result['result_available'] and unit['outputs'] and not result['blockers'] and
            not (unit['key'] == relation['source_unit'] and history['delivery']) and
            not (unit['key'] == relation['target_unit'] and history['completion']))
        frozen_history = None
        if history and unit['key'] == relation['target_unit'] and history['delivery']:
            frozen_history = {'state': 'VERIFIED', 'problems': [],
                'delivery': runtime.ref(history['delivery'][0]),
                'completion': runtime.ref(history['completion'][0]) if history['completion'] else None}
        units.append({'key': unit['key'], 'revision': digest(unit), 'title': unit['title'],
            'member_name': unit['member']['name'], 'role': unit['role'],
            'qualification': qualifications.get(unit['key'], 'UNVERIFIED'),
            'ready': ready, 'can_send': bool(allowed and ready and proposal_ok), 'can_retry': False,
            'dispatch_ready': ready, 'dispatch_blocked_reason': reason, 'blocked_reason': reason,
            'frozen_history': frozen_history, 'dependency_gate': unit['dependency_gate'],
            'lineage': {'inherited': unit['key'] in plan.get('result_lineage', {}),
                'source': plan.get('result_lineage', {}).get(unit['key']), 'consumer_plan': plan_ref(plan), 'problems': list(problems)},
            'can_check_proposal': bool(base and auth and current_run and run == latest_coordinator and unit['role'] == 'coordinate'
                and result and result['result_available'] and run['generation'] == driver['generation']),
            'can_check': capture, 'latest_run_id': run['id'] if run else None, 'result': result,
            'capture_result_version': service.work_units.base_evidence(db, run)['version'] if capture else None})
    stage = {'protocol': PROTOCOL, 'root': history['root'] if history else plan.get('coordinator_file_handoff_lineage', {}).get('root', plan_ref(plan)),
             'phase': history['phase'] if history else 'BLOCKED',
             'historical_integrity': 'VERIFIED' if history else 'BLOCKED',
             'capability': history['capability'] if history else None,
             'delivery': history['delivery'] if history else [],
             'completion': history['completion'] if history else [], 'problems': problems, **PERMISSIONS}
    from .work_coordinator_handoff_adjustment_runtime import enabled as adjustment_enabled
    if adjustment_enabled(service, db, plan) or plan.get('coordinator_file_handoff_lineage'):
        from .work_coordinator_handoff_adjustment_records import KIND_FOR, APPLICATION, CONSUMPTION
        records = {protocol: [r for r in service.collaboration.all(db, kind) if r.get('item_id') == item['id']]
                   for protocol, kind in KIND_FOR.items()}
        stage.update(protocol=PROTOCOL_V2, plan=plan_ref(plan),
            saved_path=history.get('saved_path') if history else None,
            adjustment_capability=history.get('adjustment_capability') if history else None,
            adjustment_preview=(proposal.get('record') or {}).get('snapshot', {}).get('adjustment'),
            adjustment_applications=records[APPLICATION], adjustment_consumptions=records[CONSUMPTION],
            adjustment_records=[r for values in records.values() for r in values],
            adjustment_problems=proposal.get('problems', []), can_apply=bool(history and proposal.get('can_apply')))
    from .work_coordinator_handoff_round_public import stage as round_stage
    stage.update(round_stage(service, db, item, plan, history))
    from .work_coordinator_handoff_post_round_v3_save_public import action as v3_save_action
    save_action = v3_save_action(service, db, item, plan, proposal,
        current_verified=bool(history and base))
    if not history:
        root = service.collaboration.get(db, 'teamwork_plan', plan['coordinator_file_handoff_root']['plan_id'])
        stage['root'] = plan_ref(root) if root else None
    all_frames = {f['run']['id']: f for f in history['frames']} if history else {}
    return deepcopy({'supported': True, 'single_step_supported': True, 'continuous_supported': False,
        'scheduling': 'EXPLICIT_SERIAL_STEP', 'automatic_dispatch': False, 'flow': {**flow, 'can_start': False},
        'authorization': auth, 'authorization_reference': service.work_units.auth_ref(auth),
        'can_authorize': bool(base and not auth), 'frontier': service.work_units.frontier(db, item['id']),
        'units': units, 'usage': service.work_units.usage(db, rows), 'proposal': proposal,
        'post_round_v3_save': save_action,
        'coordination_revision': service.work_round_revisions.view(db, item, driver, plan, rows),
        'rework_diagnostic': None, 'file_rework_diagnostic': None,
        'runs': [{**r, 'result': all_frames[r['id']]['source_evidence'] if r['id'] in all_frames else
                 service.work_units.base_evidence(db, r)} for r in rows], 'handoff_stage': stage, 'no_dispatch': True})
