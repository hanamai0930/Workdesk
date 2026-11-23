"""Internal atomic V3 save; no service route or runtime activation."""
from contextvars import ContextVar
from copy import deepcopy
import time
import uuid

from .store import uid, digest
from .work_transitions import plan_ref
from .work_coordinator_handoff_adjustment_contracts import closed
from .work_coordinator_handoff_post_round_candidates import ref
from .work_coordinator_handoff_post_round_v3_candidates import WorkCoordinatorHandoffPostRoundV3Candidates
from .work_coordinator_handoff_post_round_v3_saved_component import SavedRecords, COMMAND, receipt
from .work_coordinator_handoff_post_round_materials import transaction, track
from . import work_coordinator_handoff_post_round_v3_saved_records as r

_pending = ContextVar('post_round_v3_atomic_save_owner', default=None)


def pending(service, db, owner, candidate, request, value):
    state = _pending.get()
    return bool(state is not None and state['owner'] is owner and owner.s is service and
        state['service'] is service and state['db'] is db and db.in_transaction and
        state['identity'] == candidate['command_id'] == service.command_identity and
        state['kind'] == service.command_kind == COMMAND and
        state['request'] == request and state['request_hash'] == digest(request) == service.command_payload_hash and
        state.get('candidate') == ref(candidate) and state.get('receipt') == receipt(value))


class WorkCoordinatorHandoffPostRoundV3Applications:
    def __init__(self, service):
        self.s = service
        self.preparation = WorkCoordinatorHandoffPostRoundV3Candidates(service)
        self.data = SavedRecords(service)

    @closed
    def save(self, db, request):
        r.request_shape(request)
        if (not db.in_transaction or self.s.command_kind != COMMAND or
                self.s.command_payload_hash != digest(request) or _pending.get() is not None):
            raise ValueError('V3原子保存须属于准确原请求的唯一拥有者事务。')
        uuid.UUID(self.s.command_identity)
        # Enclose preparation and preservation too. Failure caught by a caller
        # retaining its outer transaction still leaves neither rows nor files.
        with transaction(self.s):
            db.execute('SAVEPOINT post_round_v3_atomic_save')
            state = {'owner': self, 'service': self.s, 'db': db, 'identity': self.s.command_identity,
                'kind': COMMAND, 'request': deepcopy(request), 'request_hash': digest(request)}
            token = _pending.set(state)
            try:
                facts = self.preparation._live(db, request)
                state['resources'] = self.preparation._ancestry_resources(db, facts['plan'])
                track(self.s, facts['history']['material_observation'])
                ready = self.preparation.prepare(db, request)
                result = self._save(db, ready, request, state)
            except BaseException:
                db.execute('ROLLBACK TO post_round_v3_atomic_save')
                db.execute('RELEASE post_round_v3_atomic_save')
                raise
            else:
                db.execute('RELEASE post_round_v3_atomic_save')
            finally:
                _pending.reset(token)
        return receipt(result)

    def _save(self, db, ready, request, state):
        cp, candidate, child = (ready[k] for k in ('checkpoint', 'candidate', 'child_plan'))
        h = cp['snapshot']['history']; before, old_auth = h['driver'], h['authorization']
        self._source(db, request, cp['snapshot']['plan'], before, old_auth)
        from .work_coordinator_handoff_adjustment_contracts import AUTH_EXTRA as OA, DRIVER_EXTRA as OD
        auth = {k: deepcopy(v) for k, v in old_auth.items() if k not in r.AUTH_EXTRA | OA}
        auth.update(revision=uid(), plan_id=child['id'], plan_version=child['version'],
            previous_authorization=self.s.work_units.auth_ref(old_auth), transferred_at=time.time(),
            transfer_registration=ref(candidate), usage_at_transfer=deepcopy(child['transition']['usage_at_transition']),
            command_id=candidate['command_id'])
        driver = {k: deepcopy(v) for k, v in before.items() if k not in r.DRIVER_EXTRA | OD}
        driver.update(plan_id=child['id'], plan_version=child['version'], state='PAUSED', reason=r.PAUSE_REASON,
            previous_plan_reference=candidate['snapshot']['parent_plan'], transition_registration=ref(candidate))
        self.s.collaboration.put(db, 'teamwork_plan', child)
        self.s.collaboration.put(db, 'teamwork_current', {'id': 'teamwork-current:' + child['item_id'], 'plan_id': child['id']})
        self.s.collaboration.put(db, 'work_unit_authorization', auth)
        self.s.collaboration.put(db, 'work_unit_authorization_revision', {**auth, 'id': 'unit-auth-version:' + auth['revision']})
        after = self.s.work_drivers.save(db, driver, 'coordinator_post_round_adjustment_saved_v3')
        r.adjacent(cp, candidate, child, before, after, old_auth, auth)
        payload = r.save_payload(candidate, child, after, auth)
        self.s.store.event(None, None, r.SAVE_EVENT, payload, db)
        event = self.preparation._event(db, r.SAVE_EVENT, payload)
        app, reg = [self.data.put(db, protocol, body) for protocol, body in zip((r.APPLICATION, r.REGISTRATION),
            r.bodies(cp, candidate, child, before, after, old_auth, auth, event, request))]
        state['candidate'] = ref(candidate)
        state['receipt'] = receipt({'application': app, 'registration': reg, 'child_plan': child,
            'driver': after, 'authorization': auth})
        from .work_coordinator_handoff_post_round_v3_saved_tree import WorkCoordinatorHandoffPostRoundV3SavedTree
        tree = WorkCoordinatorHandoffPostRoundV3SavedTree(self.s, _save_owner=self)
        result = tree.read(db, child)
        changes = db.total_changes
        resources = [(p['id'], tree._resources(db, p)) for p in tree._collected]
        self._source(db, request, child, after, auth)
        # Recheck the immutable original source too, rather than replacing the
        # original request with today's transferred authorization reference.
        if (tree.old._driver(db, cp['snapshot']['plan'], request['current_driver']) != before or
                tree.old._auth(db, cp['snapshot']['plan'], request['authorization']) != old_auth):
            raise ValueError('V3保存末尾原责任或授权准确版本变化。')
        deadlines = [v['dispatch_before'] for v in self.s.collaboration.all(db, 'work_unit_authorization_revision')
            if v.get('plan_id') in tree._plan_ids]
        final_plans = tree._collect(db, child)
        final_resources = [(p['id'], tree._resources(db, p)) for p in tree._collected]
        original_resources = self.preparation._ancestry_resources(db, cp['snapshot']['plan'])
        tree._tail(db, result)
        expired = not deadlines or time.time() >= min(deadlines)
        if (result['material_observation'] != h['material_observation'] or
                result['usage'] != child['transition']['usage_at_transition'] or
                final_plans != tree._collected or final_resources != resources or
                original_resources != state['resources'] or expired or db.total_changes != changes):
            raise ValueError('V3保存结束材料、完整累计、祖先资源、DB或原最初截止变化。')
        return result

    def _source(self, db, request, plan, driver, auth):
        item = request['id']; proposal = self.s.work_proposals.current(db, item)
        if (self.s.teamwork.current(db, item) != plan or self.s.work_drivers.current(db, item) != driver or
                self.s.work_units.authorization(db, driver) != auth or
                self.s.work_items.item(db, item)['revision'] != request['revision'] or
                self.s.work_units.frontier(db, item) != request['frontier'] or
                not proposal or ref(proposal) != request['proposal'] or self.s.work_transitions.consumption(db, proposal)):
            raise ValueError('V3保存原七字段当前来源、提案或消费状态变化。')
