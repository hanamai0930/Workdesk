"""Internal atomic explicit post-ROUND save. No public handler or dispatch."""
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
import time

from .store import uid, digest
from .work_transitions import plan_ref
from .work_coordinator_handoff_records import fields
from .work_coordinator_handoff_adjustment_contracts import closed
from .work_coordinator_handoff_post_round_v2_candidates import WorkCoordinatorHandoffPostRoundV2Candidates, ref
from .work_coordinator_handoff_post_round_materials import transaction, track
from .work_coordinator_handoff_post_round_v2_saved_records import (
    SavedRecords, APPLICATION, REGISTRATION, SAVE_EVENT, PAUSE_REASON, AUTH_EXTRA, DRIVER_EXTRA,
    REQUEST_FIELDS, adjacent, bodies, save_payload)

_pending = ContextVar('post_round_v2_save_pending_command', default=None)
COMMAND = 'internal.post-round.v2.save'


def pending(service, db, identity, request):
    value = _pending.get()
    return (value is not None and value['service'] is service and value['db'] is db and
        value['identity'] == identity and value['request'] == request and db.in_transaction and
        service.command_identity == identity and service.command_kind == COMMAND and
        service.command_payload_hash == digest(request))


def receipt(result):
    return {k: deepcopy(result[k]) for k in ('application', 'registration', 'child_plan', 'driver', 'authorization')} | {
        'no_dispatch': True, 'automatically_resumed': False}


class WorkCoordinatorHandoffPostRoundV2Applications:
    def __init__(self, service):
        self.s = service
        self.preparation = WorkCoordinatorHandoffPostRoundV2Candidates(service)
        self.data = SavedRecords(service)

    @closed
    def save(self, db, request):
        fields(request, REQUEST_FIELDS)
        if (not db.in_transaction or self.s.command_kind != COMMAND or
                self.s.command_payload_hash != digest(request)):
            raise ValueError('新保存须属于准确七字段原命令写事务。')
        item = self.s.work_items.item(db, request['id'])
        plan = self.s.teamwork.current(db, item['id']); driver = self.s.work_drivers.current(db, item['id'])
        auth = self.s.work_units.authorization(db, driver)
        expected = {'id': item['id'], 'revision': item['revision'],
            'current_driver': self.s.work_drivers.reference(driver), 'plan': plan_ref(plan),
            'authorization': self.s.work_units.auth_ref(auth),
            'frontier': self.s.work_units.frontier(db, item['id']), 'proposal': request['proposal']}
        if request != expected: raise ValueError('新保存七字段所指当前原版本已变化。')
        # Enclose preparation and all saved writes. A caught exception cannot
        # leave a partial save in a caller that retains its outer transaction.
        with transaction(self.s):
            db.execute('SAVEPOINT post_round_v2_atomic_save')
            token = _pending.set({'service': self.s, 'db': db, 'identity': self.s.command_identity,
                'request': deepcopy(request)})
            try:
                facts = self.preparation._live(db, request)
                track(self.s, facts['history']['material_observation'])
                prepared = self.preparation.prepare(db, request)
                result = self._save(db, prepared, request)
            except BaseException:
                db.execute('ROLLBACK TO post_round_v2_atomic_save'); db.execute('RELEASE post_round_v2_atomic_save')
                raise
            else:
                db.execute('RELEASE post_round_v2_atomic_save')
            finally:
                _pending.reset(token)
        return receipt(result)

    def _save(self, db, ready, request):
        cp, candidate, child = (ready[k] for k in ('checkpoint', 'candidate', 'child_plan'))
        h = ready['observation']['history']; before, old_auth = h['driver'], h['authorization']
        if (self.s.teamwork.current(db, child['item_id']) != cp['snapshot']['plan'] or
                self.s.work_drivers.current(db, child['item_id']) != before or
                self.s.work_units.authorization(db, before) != old_auth):
            raise ValueError('新保存准备后原方案、责任或授权已变化。')
        from .work_coordinator_handoff_adjustment_contracts import AUTH_EXTRA as OA, DRIVER_EXTRA as OD
        auth = {k: deepcopy(v) for k, v in old_auth.items() if k not in AUTH_EXTRA | OA}
        auth.update(revision=uid(), plan_id=child['id'], plan_version=child['version'],
            previous_authorization=self.s.work_units.auth_ref(old_auth), transferred_at=time.time(),
            transfer_registration=ref(candidate), usage_at_transfer=child['transition']['usage_at_transition'],
            command_id=candidate['command_id'])
        driver = {k: deepcopy(v) for k, v in before.items() if k not in DRIVER_EXTRA | OD}
        driver.update(plan_id=child['id'], plan_version=child['version'], state='PAUSED', reason=PAUSE_REASON,
            previous_plan_reference=candidate['snapshot']['parent_plan'], transition_registration=ref(candidate))
        self.s.collaboration.put(db, 'teamwork_plan', child)
        self.s.collaboration.put(db, 'teamwork_current', {'id': 'teamwork-current:' + child['item_id'], 'plan_id': child['id']})
        self.s.collaboration.put(db, 'work_unit_authorization', auth)
        self.s.collaboration.put(db, 'work_unit_authorization_revision', {**auth, 'id': 'unit-auth-version:' + auth['revision']})
        after = self.s.work_drivers.save(db, driver, 'coordinator_post_round_adjustment_saved_v2')
        adjacent(cp, candidate, child, before, after, old_auth, auth)
        payload = save_payload(self.s, candidate, child, after, auth)
        self.s.store.event(None, None, SAVE_EVENT, payload, db)
        event = self.preparation._event(db, SAVE_EVENT, payload)
        for protocol, body in zip((APPLICATION, REGISTRATION),
                bodies(self.s, cp, candidate, child, before, after, old_auth, auth, event, request)):
            self.data.put(db, protocol, body)
        from .work_coordinator_handoff_post_round_v2_saved_tree import WorkCoordinatorHandoffPostRoundV2SavedTree
        result = WorkCoordinatorHandoffPostRoundV2SavedTree(self.s).read(db, child)
        if (result['material_observation'] != h['material_observation'] or
                result['usage'] != child['transition']['usage_at_transition'] or time.time() >= auth['dispatch_before']):
            raise ValueError('新保存末尾材料、原累计或最初截止已变化。')
        return result
