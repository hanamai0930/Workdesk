"""Exclusive late V3 restart reconciliation; never resumes or dispatches."""
from copy import deepcopy
import time

from .store import uid
from .work_transitions import plan_ref
from .work_coordinator_handoff_records import versioned, PERMISSIONS
from .work_coordinator_handoff_adjustment_contracts import closed
from .work_coordinator_handoff_post_round_candidates import ref
from . import work_coordinator_handoff_post_round_v3_responsibility_records as r


def potential(service, db, driver):
    """Protective routing only. Missing/malformed sources grant no proof."""
    try:
        # A damaged/relabelled plan must not hide a retained V3 transfer.
        if 'transition_registration' in driver:
            registration = driver['transition_registration']
            identity = registration.get('id') if isinstance(registration, dict) else None
            if not isinstance(identity, str) or not identity.startswith((
                    'work_coordinator_handoff_post_round_candidate:',
                    'work_coordinator_handoff_post_round_candidate_v2:')):
                return True
        plan = service.collaboration.get(db, 'teamwork_plan', driver['plan_id'])
        current = service.teamwork.current(db, driver['item_id'])
        for value in (plan, current):
            if not isinstance(value, dict): return True
            field = 'coordinator_file_handoff_post_round_lineage'
            if field not in value: continue
            lineage = value[field]
            # Retain existing V1/V2 startup routing; this is no admission proof.
            if not isinstance(lineage, dict) or lineage.get('protocol') not in {
                    'COORDINATOR_FILE_HANDOFF_POST_ROUND_ADJUSTMENT_LINEAGE_V1',
                    'COORDINATOR_FILE_HANDOFF_POST_ROUND_ADJUSTMENT_LINEAGE_V2'}:
                return True
        return False
    except (ValueError, KeyError, TypeError, AttributeError):
        return True


def reconcile(service):
    """Run after all complete-Current dependencies and before the scheduler."""
    report = {'instance': service.instance, 'paused': [], 'blocked': [],
        'no_dispatch': True, 'automatically_resumed': False}
    with service.store.write() as db:
        for driver in service.collaboration.all(db, 'work_driver'):
            if driver['state'] != 'RESERVED' or not potential(service, db, driver):
                continue
            try:
                row = _pause(service, db, driver)
            except (ValueError, OSError) as exc:
                report['blocked'].append({'driver_id': driver['id'], 'item_id': driver['item_id'],
                    'reason': str(exc)[:1200]})
            else:
                report['paused'].append({'driver': service.work_drivers.reference(row['snapshot']['driver_after']),
                    'transition': ref(row), 'restart_id': row['snapshot']['restart_id']})
    return report


@closed
def _pause(service, db, driver):
    if not db.in_transaction:
        raise ValueError('V3重启暂停须属于独立启动核对事务。')
    db.execute('SAVEPOINT post_round_v3_restart')
    try:
        from .work_coordinator_handoff_post_round_v3_responsibility_current import WorkCoordinatorHandoffPostRoundV3ResponsibilityCurrent as Current
        plan = service.teamwork.current(db, driver['item_id'])
        original = Current(service).read(db, plan)
        if (original['driver'] != driver or driver['state'] != 'RESERVED' or
                driver['instance'] == service.instance):
            raise ValueError('V3重启暂停须为新实例的准确原持有责任。')
        now = time.time(); action = 'restart_pause'
        after = {**deepcopy(driver), 'revision': uid(), 'updated': now,
            'state': 'PAUSED', 'reason': r.REASONS[action], 'instance': service.instance}
        previous = original['responsibility']['transitions']
        body = versioned({'protocol': r.TRANSITION, 'scope': r.SCOPE, 'action': action,
            'item_id': driver['item_id'], 'plan': plan_ref(plan),
            'saved_registration': ref(original['registration']),
            'original_driver': service.work_drivers.reference(original['original_driver']),
            'previous_transition': ref(previous[-1]) if previous else None,
            'driver_before': driver, 'driver_after': after, 'authorization': original['authorization'],
            'usage': original['usage'], 'original_dispatch_before': original['authorization']['dispatch_before'],
            'request': None, 'command_id': None, 'restart_id': uid(), 'instance': service.instance,
            'reason': r.REASONS[action], 'automatically_resumed': False, **PERMISSIONS})
        row = {'id': r.KIND + ':' + body['version'], 'version': body['version'], 'snapshot': body,
            'item_id': driver['item_id'], 'created': now, 'command_id': None}
        r.adjacent(row, {**original, 'driver': original['original_driver']}, previous[-1] if previous else None)
        service.collaboration.put(db, 'work_driver', after)
        service.collaboration.put(db, 'work_driver_revision', {**after,
            'id': 'driver-version:' + after['revision'], 'driver_id': after['id']})
        service.store.event(None, None, r.DRIVER_EVENTS[action], {**after, 'no_dispatch': True}, db)
        service.collaboration.put(db, r.KIND, row)
        service.store.event(None, None, r.EVENT, row, db)
        current = Current(service).read(db, plan)
        usage = lambda value: {k: v for k, v in value['usage'].items() if k != 'elapsed_observed_seconds'}
        if (current['driver'] != after or current['authorization'] != original['authorization'] or
                current['registration'] != original['registration'] or
                current['responsibility']['transitions'] != previous + [row] or
                current['material_observation'] != original['material_observation'] or
                current['saved_path'] != original['saved_path'] or usage(current) != usage(original)):
            raise ValueError('V3重启暂停末尾完整来源、累计或材料已变化。')
    except BaseException:
        db.execute('ROLLBACK TO post_round_v3_restart')
        db.execute('RELEASE post_round_v3_restart')
        raise
    else:
        db.execute('RELEASE post_round_v3_restart')
    return deepcopy(row)
