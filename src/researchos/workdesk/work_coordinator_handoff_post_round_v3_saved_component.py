"""Actual V3 saved-edge component, with exact carriers for its V8 parent.

This is a historical component, not the full saved-tree namespace owner or an
atomic application. It never dispatches, changes current pointers, or grants
permission to a wrapper merely because its protocol or shape is recognized.
"""
from copy import deepcopy
import json
import time
import uuid

from .store import digest
from .work_transitions import plan_ref
from .work_read_observation import database_observation
from .work_coordinator_handoff_records import reference, PERMISSIONS
from .work_coordinator_handoff_adjustment_contracts import closed
from .work_coordinator_handoff_context import WorkCoordinatorHandoffContext
from .work_coordinator_handoff_post_round_candidates import ref, LINEAGE_FIELD
from .work_coordinator_handoff_post_round_runtime import belongs
from .work_coordinator_handoff_post_round_v3_candidates import WorkCoordinatorHandoffPostRoundV3Candidates
from . import work_coordinator_handoff_post_round_v3_saved_records as r
from . import work_coordinator_handoff_post_round_v3_path as p

COMMAND = 'internal.post-round.v3.save'


def receipt(value):
    return {k: deepcopy(value[k]) for k in
        ('application', 'registration', 'child_plan', 'driver', 'authorization')} | {
        'no_dispatch': True, 'automatically_resumed': False}


class SavedRecords:
    """Typed storage only; callers own the surrounding atomic application."""
    def __init__(self, service): self.s = service

    @closed
    def get(self, db, protocol, identity):
        reference(identity)
        value = r.validate_row(self.s.collaboration.get(db, r.KIND_FOR[protocol], identity['id']), protocol)
        if ref(value) != identity: raise ValueError('V3实际保存记录引用已变化。')
        return value

    @closed
    def put(self, db, protocol, body):
        if (not db.in_transaction or self.s.command_kind != COMMAND or
                body['protocol'] != protocol or body.get('request', {}).get('id', body['item_id']) != body['item_id']):
            raise ValueError('V3保存记录须属于准确内部保存命令事务。')
        uuid.UUID(self.s.command_identity)
        if protocol not in (r.APPLICATION, r.REGISTRATION):
            raise ValueError('V3保存存储仅写应用与登记。')
        app = body if protocol == r.APPLICATION else self.get(db, r.APPLICATION, body['application'])['snapshot']
        if self.s.command_payload_hash != digest(app['request']):
            raise ValueError('V3保存存储原七字段命令摘要不符。')
        row = {'id': r.KIND_FOR[protocol] + ':' + body['version'], 'version': body['version'],
            'snapshot': deepcopy(body), 'item_id': body['item_id'], 'created': time.time(),
            'command_id': self.s.command_identity}
        r.validate_row(row, protocol)
        if db.execute('SELECT 1 FROM collaboration WHERE kind=? AND id=?', (r.KIND_FOR[protocol], row['id'])).fetchone():
            raise ValueError('V3保存身份已存在，须重放原命令。')
        self.s.collaboration.put(db, r.KIND_FOR[protocol], row)
        self.s.store.event(None, None, r.EVENTS[protocol], row, db)
        return row


class WorkCoordinatorHandoffPostRoundV3SavedComponent(WorkCoordinatorHandoffPostRoundV3Candidates):
    """Two independent actual V8 reconstructions; full-tree proof stays false."""
    def __init__(self, service):
        super().__init__(service)
        self.data = SavedRecords(service)

    def _saved(self, db, facts, checkpoint, candidate):
        # Called only after the V8 owner rebuilt facts from its actual prefix.
        # It must also be safe to repeat within that same pass: no proof cache.
        b = candidate['snapshot']; h = facts['history']; child = b['child_plan']
        r.successor_plan(child, candidate)
        if self.s.collaboration.get(db, 'teamwork_plan', child['id']) != child:
            raise ValueError('V3实际保存child不等于准确候选。')
        def one(protocol, key):
            rows = [v for v in self.s.collaboration.all(db, r.KIND_FOR[protocol])
                if v.get('snapshot', {}).get(key) == plan_ref(child)]
            if len(rows) != 1: raise ValueError('V3须有唯一实际应用与登记。')
            return self.data.get(db, protocol, ref(rows[0]))
        app = one(r.APPLICATION, 'child_plan'); reg = one(r.REGISTRATION, 'to_plan')
        context = WorkCoordinatorHandoffContext(self.s)
        driver = context._driver(db, child, app['snapshot']['driver_after'])
        auth = context._auth(db, child, app['snapshot']['authorization_after'])
        r.adjacent(checkpoint, candidate, child, h['driver'], driver, h['authorization'], auth)
        events = {'checkpoint_event': self._event(db, r.EVENTS[r.CHECKPOINT], checkpoint),
            'candidate_event': self._event(db, r.EVENTS[r.CANDIDATE], candidate),
            'driver_event': self._event(db, r.DRIVER_EVENT, {**driver, 'no_dispatch': True}),
            'save_event': self._event(db, r.SAVE_EVENT, r.save_payload(candidate, child, driver, auth)),
            'application_event': self._event(db, r.EVENTS[r.APPLICATION], app),
            'registration_event': self._event(db, r.EVENTS[r.REGISTRATION], reg)}
        path = p.extend_path(checkpoint, candidate, driver, auth, app, reg, events)
        value = {'application': app, 'registration': reg, 'child_plan': child,
            'driver': driver, 'authorization': auth, 'saved_path': path, 'events': events}
        request = {'id': child['item_id'], 'revision': h['task']['revision'],
            'current_driver': self.s.work_drivers.reference(h['driver']), 'plan': plan_ref(facts['plan']),
            'authorization': self.s.work_units.auth_ref(h['authorization']),
            'frontier': digest([{'run_id': f['run_id'], 'evidence': f['evidence']['version']} for f in h['frontier']]),
            'proposal': ref(facts['proposal'])}
        if checkpoint['snapshot']['request'] != request:
            raise ValueError('V3实际保存原七字段未准确重建。')
        self._command(db, candidate, request, value)
        return value

    def _command(self, db, candidate, request, value):
        # Historical/default readers accept committed commands only. A private
        # atomic owner may override this hook with its exact owning witness.
        command = db.execute('SELECT * FROM commands WHERE id=?', (candidate['command_id'],)).fetchone()
        if (command is None or
                command['kind'] != COMMAND or command['payload_hash'] != digest(request) or
                json.loads(command['receipt']) != receipt(value) or command['created'] < value['registration']['created']):
            raise ValueError('V3实际保存缺少准确已提交原命令及完整回执。')

    def _carriers(self, checkpoint, candidate, value):
        events = value['events']; driver = value['driver']; auth = value['authorization']
        rows = [(r.KIND_FOR[protocol], row, events[name]) for protocol, row, name in
            ((r.CHECKPOINT, checkpoint, 'checkpoint_event'), (r.CANDIDATE, candidate, 'candidate_event'),
             (r.APPLICATION, value['application'], 'application_event'),
             (r.REGISTRATION, value['registration'], 'registration_event'))]
        # Only these exact canonical values may carry the parent's V8 proof.
        rows += [('teamwork_plan', value['child_plan'], None), ('work_driver', driver, None),
            ('work_driver_revision', {**driver, 'id': 'driver-version:' + driver['revision'], 'driver_id': driver['id']}, None),
            ('work_unit_authorization', auth, None),
            ('work_unit_authorization_revision', {**auth, 'id': 'unit-auth-version:' + auth['revision']}, None)]
        return rows

    def _namespace(self, db, facts, checkpoint, candidate):
        value = self._saved(db, facts, checkpoint, candidate)
        carriers = self._carriers(checkpoint, candidate, value)
        identities = {v for row in (checkpoint, candidate, value['application'], value['registration'],
                value['child_plan'], value['driver'], value['authorization'])
            for k, v in row.items() if k in ('id', 'version', 'revision', 'generation')}
        identities.update(facts['history']['root'].values())
        identities.update(candidate['snapshot']['parent_plan'].values())
        protocols = {*r.KIND_FOR, p.PATH, candidate['snapshot']['child_plan'][LINEAGE_FIELD]['protocol']}
        # Attribution includes bare typed refs, even under unknown outer kinds.
        # Only V3 markers define this component's scope. Parent namespaces are
        # still checked by the actual bounded V8 owner, not accepted here.
        typed = {x for row in (checkpoint, candidate, value['application'], value['registration'])
            for x in ref(row).values()}
        def v3_marked(value):
            pending = [value]
            while pending:
                node = pending.pop()
                if isinstance(node, dict):
                    if node.get('protocol') in protocols: return True
                    if any(isinstance(node.get(k), str) and (node[k] in typed or
                            any(node[k].startswith(kind + ':') for kind in r.KIND_FOR.values()))
                            for k in ('id', 'version')): return True
                    pending.extend(node.values())
                elif isinstance(node, list): pending.extend(node)
            return False
        expected_rows = {(kind, row['id']): row for kind, row, _ in carriers}
        expected_events = {(e['kind'], e['seq']): row for _, row, e in carriers if e is not None}
        expected_events[(r.DRIVER_EVENT, value['events']['driver_event']['seq'])] = {**value['driver'], 'no_dispatch': True}
        expected_events[(r.SAVE_EVENT, value['events']['save_event']['seq'])] = r.save_payload(
            candidate, value['child_plan'], value['driver'], value['authorization'])
        for row in db.execute('SELECT * FROM collaboration'):
            body = json.loads(row['data'])
            if ((row['kind'] in r.KIND_FOR.values() or v3_marked(body)) and
                    belongs(body, candidate['item_id'], identities) and
                    expected_rows.get((row['kind'], row['id'])) != body):
                raise ValueError('V3实际记录全集存在改名、嵌套、错误键或未知边。')
        for row in db.execute('SELECT * FROM events'):
            body = json.loads(row['payload'])
            if ((row['kind'] in {*r.EVENTS.values(), r.SAVE_EVENT, r.DRIVER_EVENT} or v3_marked(body)) and
                    belongs(body, candidate['item_id'], identities) and
                    (expected_events.get((row['kind'], row['seq'])) != body or
                     row['task_id'] is not None or row['attempt_id'] is not None)):
                raise ValueError('V3实际事件全集存在重复、改名、嵌套或错误归属。')
        # No child lifecycle or successor input is covered by this contract.
        for kind, expected in (('work_driver_revision', [expected_rows[('work_driver_revision', 'driver-version:' + value['driver']['revision'])]]),
                ('work_unit_authorization_revision', [expected_rows[('work_unit_authorization_revision', 'unit-auth-version:' + value['authorization']['revision'])]]),
                ('work_unit_run', [])):
            actual = [v for v in self.s.collaboration.all(db, kind) if v.get('plan_id') == value['child_plan']['id']]
            if actual != expected: raise ValueError('V3保存组件仅接受准确原暂停起点且无后继运行。')
        return value

    def _canonical_carriers(self, db, facts, checkpoint, candidate):
        # Base reconstructs both complete preparation envelopes from actual
        # parent facts before calling our exact saved-namespace comparison.
        super()._canonical_carriers(db, facts, checkpoint, candidate)
        value = self._namespace(db, facts, checkpoint, candidate)
        rows = self._carriers(checkpoint, candidate, value)
        # The bounded audit checks event fields for every supplied tuple.
        # Record-only carriers have no V8-bearing event; omit these unless a
        # corresponding actual event can be reconstructed without invention.
        return tuple((k, v, e) for k, v, e in rows if e is not None)

    def _once(self, db, identity):
        ready = super()._once(db, identity)
        checkpoint = ready['checkpoint']; candidate = ready['candidate']
        h = ready['observation']['history']
        facts = {'history': h, 'plan': checkpoint['snapshot']['plan'],
            'proposal': self.s.collaboration.get(db, 'work_proposal', candidate['snapshot']['proposal']['id'])}
        saved = self._namespace(db, facts, checkpoint, candidate)
        return {**ready, **saved, 'actual_saved_values_verified': True,
            'typed_saved_namespace_verified': True, 'preparation_namespace_verified': False,
            'full_saved_tree_verified': False, 'saved_edge_verified': False,
            'successor_runtime_open': False, **PERMISSIONS}

    @closed
    def read(self, db, identity):
        # Inherit the preparation owner's two fresh complete V8 reads and all
        # source/capture/material tails. Add the installed child to resources.
        if not db.in_transaction: raise ValueError('V3保存组件读取须属于明确事务。')
        with database_observation(db):
            changes = db.total_changes
            candidate = self._get(db, r.CANDIDATE, identity)
            child = candidate['snapshot']['child_plan']
            resources = self._ancestry_resources(db, child)
            result = super().read(db, identity)
            if db.total_changes != changes or self._ancestry_resources(db, child) != resources:
                raise ValueError('V3保存组件末尾实际祖先、资源或数据库变化。')
            return deepcopy(result)
