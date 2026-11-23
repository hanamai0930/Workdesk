"""Internal atomic first ROUND save. No public handler or execution authority."""
from copy import deepcopy
import time

from .store import uid
from .work_transitions import plan_ref
from .work_coordinator_handoff_records import PERMISSIONS, versioned
from .work_coordinator_handoff_adjustment_contracts import closed
from .work_coordinator_handoff_mixed_contracts import (
    adjacent, AUTH_EXTRA, DRIVER_EXTRA, OLD_AUTH_EXTRA, OLD_DRIVER_EXTRA)
from .work_coordinator_handoff_mixed_records import (
    APPLICATION, REGISTRATION, SAVE_EVENT, PAUSE_REASON, WorkCoordinatorHandoffMixedRecords)
from .work_coordinator_handoff_mixed_checkpoints import WorkCoordinatorHandoffMixedCheckpoints


def save_payload(service, candidate, child, driver, auth):
    body = candidate['snapshot']
    return {'item_id': child['item_id'], 'root': body['root'], 'parent_plan': body['parent_plan'],
        'child_plan': plan_ref(child), 'candidate': WorkCoordinatorHandoffMixedRecords.ref(candidate),
        'driver': service.work_drivers.reference(driver), 'authorization': service.work_units.auth_ref(auth),
        'command_id': candidate['command_id'], 'no_dispatch': True}


def bodies(service, checkpoint, candidate, child, before, after, old_auth, auth, event):
    ref = WorkCoordinatorHandoffMixedRecords.ref
    body = candidate['snapshot']
    common = {k: deepcopy(body[k]) for k in ('item_id', 'root', 'parent_plan', 'parent_path',
        'source', 'selected_unit', 'reason', 'operation', 'scope')}
    application = versioned({**common, 'protocol': APPLICATION, 'checkpoint': ref(checkpoint),
        'candidate': ref(candidate), 'child_plan': plan_ref(child),
        'driver_before': service.work_drivers.reference(before), 'driver_after': service.work_drivers.reference(after),
        'authorization_before': service.work_units.auth_ref(old_auth), 'authorization_after': service.work_units.auth_ref(auth),
        'usage': body['transition']['usage_at_transition'], 'save_event': event,
        'pause_reason': PAUSE_REASON, **PERMISSIONS})
    from .work_coordinator_handoff_mixed_records import KIND_FOR
    registration = versioned({**common, 'protocol': REGISTRATION, 'candidate': ref(candidate),
        'application': {'id': KIND_FOR[APPLICATION] + ':' + application['version'], 'version': application['version']},
        'from_plan': body['parent_plan'], 'to_plan': plan_ref(child),
        'command_id': candidate['command_id'], **PERMISSIONS})
    return application, registration


class WorkCoordinatorHandoffMixedApplications:
    def __init__(self, service):
        self.s = service
        self.checkpoints = WorkCoordinatorHandoffMixedCheckpoints(service)
        self.data = self.checkpoints.data

    @closed
    def save(self, db, plan, reason):
        """The caller's store.command owns the receipt and outer transaction.

        The context covers every write and the final full read; even if a caller
        catches an exception, no partial plan, pointer or new baseline remains.
        Command replay belongs to Store.command, never to a second save here.
        """
        if not db.in_transaction: raise ValueError('明确回合保存必须属于同一命令写事务。')
        with self.checkpoints.preparing(db, plan, reason) as ready:
            return self._save(db, ready)

    def _save(self, db, ready):
        checkpoint, candidate, child = (ready[k] for k in ('checkpoint', 'candidate', 'child_plan'))
        body = candidate['snapshot']; obs = ready['observation']; h = obs['history']
        before, old_auth = h['driver'], h['authorization']; ref = self.data.ref(candidate)
        if (candidate['command_id'] != self.s.command_identity or
                self.s.teamwork.current(db, child['item_id']) != checkpoint['snapshot']['plan'] or
                self.s.work_drivers.current(db, child['item_id']) != before or
                self.s.work_units.authorization(db, before) != old_auth):
            raise ValueError('明确回合保存的原方案、责任或命令已变化。')
        auth = {k: deepcopy(v) for k, v in old_auth.items() if k not in AUTH_EXTRA | OLD_AUTH_EXTRA}
        auth.update(revision=uid(), plan_id=child['id'], plan_version=child['version'],
            previous_authorization=self.s.work_units.auth_ref(old_auth), transferred_at=time.time(),
            transfer_registration=ref, usage_at_transfer=obs['usage'], command_id=candidate['command_id'])
        driver = {k: deepcopy(v) for k, v in before.items() if k not in DRIVER_EXTRA | OLD_DRIVER_EXTRA}
        driver.update(plan_id=child['id'], plan_version=child['version'], state='PAUSED', reason=PAUSE_REASON,
            previous_plan_reference=body['parent_plan'], transition_registration=ref)
        self.s.collaboration.put(db, 'teamwork_plan', child)
        self.s.collaboration.put(db, 'teamwork_current', {'id': 'teamwork-current:' + child['item_id'], 'plan_id': child['id']})
        self.s.collaboration.put(db, 'work_unit_authorization', auth)
        self.s.collaboration.put(db, 'work_unit_authorization_revision', {**auth, 'id': 'unit-auth-version:' + auth['revision']})
        after = self.s.work_drivers.save(db, driver, 'coordinator_handoff_mixed_round_revised')
        adjacent(checkpoint, candidate, child, old_auth, auth, before, after)
        payload = save_payload(self.s, candidate, child, after, auth)
        self.s.store.event(None, None, SAVE_EVENT, payload, db)
        event = self.checkpoints._event(db, SAVE_EVENT, payload)
        for protocol, snapshot in zip((APPLICATION, REGISTRATION),
                bodies(self.s, checkpoint, candidate, child, before, after, old_auth, auth, event)):
            self.data.put(db, protocol, snapshot)
        from .work_coordinator_handoff_mixed_runtime import WorkCoordinatorHandoffMixedRuntime
        result = WorkCoordinatorHandoffMixedRuntime(self.s).read(db, child)
        if (result['material_observation'] != h['material_observation'] or
                result['usage'] != obs['usage'] or time.time() >= obs['original_dispatch_before']):
            raise ValueError('明确回合保存末尾材料、原累计或最初截止已变化。')
        return result
