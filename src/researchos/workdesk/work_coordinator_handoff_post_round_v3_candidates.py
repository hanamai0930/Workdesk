"""Independent V3 preparation for an actual V2 saved-successor V8 proposal.

This component verifies preparation data, not a saved edge or full namespace.
The eventual atomic save must enclose preparation and its own full tree audit.
"""
from copy import deepcopy
import json
import time
import uuid

from .store import digest, uid
from .work_transitions import plan_ref
from .work_read_observation import database_observation
from .work_coordinator_handoff_records import PERMISSIONS, fields, reference, versioned
from .work_coordinator_handoff_adjustment_records import hashed, permissions, finite
from .work_coordinator_handoff_adjustment_contracts import closed
from .work_coordinator_handoff_post_round_candidates import (
    ref, LINEAGE_FIELD, HISTORY_FIELDS as OLD_HISTORY)
from .work_coordinator_handoff_post_round_saved_records import REQUEST_FIELDS
from .work_coordinator_handoff_post_round_v2_history import WorkCoordinatorHandoffPostRoundV2History
from .work_coordinator_handoff_post_round_v2_preview import proposal_state
from .work_coordinator_handoff_post_round_materials import transaction, track
from .work_coordinator_handoff_post_round_runtime import belongs

PREFIX = 'COORDINATOR_FILE_HANDOFF_POST_ROUND_ADJUSTMENT_'
CHECKPOINT = PREFIX + 'CHECKPOINT_V3'
CANDIDATE = PREFIX + 'CANDIDATE_V3'
LINEAGE = PREFIX + 'LINEAGE_V3'
OPERATION = 'POST_ROUND_ADJUSTMENT'
SCOPE = 'CURRENT_ACTUAL_V8_PARENT_PREPARATION; NO_SAVED_EDGE_OR_SUCCESSOR_INPUT_PERMISSION'
PARENT_KIND = 'POST_ROUND_V2_SUCCESSOR'
KINDS = {CHECKPOINT: 'work_coordinator_handoff_post_round_checkpoint_v3',
    CANDIDATE: 'work_coordinator_handoff_post_round_candidate_v3'}
EVENTS = {CHECKPOINT: 'coordinator_post_round_adjustment_checkpointed_v3',
    CANDIDATE: 'coordinator_post_round_adjustment_candidate_prepared_v3'}
from .work_coordinator_handoff_post_round_v2_candidates import LINEAGE as PARENT_LINEAGE
HISTORY_FIELDS = OLD_HISTORY | {'successor_capability'}
COMMON = {'protocol', 'version', 'operation', 'scope', 'parent_kind', 'item_id',
    'root', 'parent_plan', 'proposal', 'capability', 'request', *PERMISSIONS}
EXTRA = {CHECKPOINT: {'plan', 'history', 'preview', 'source_check_event', 'cutoff', 'original_dispatch_before'},
    CANDIDATE: {'checkpoint', 'child_seed', 'child_plan', 'transition'}}


def marker(value):
    pending = [value]
    while pending:
        node = pending.pop()
        if isinstance(node, dict):
            identity = node.get('id')
            if node.get('protocol') in KINDS or (isinstance(identity, str) and
                    any(identity.startswith(kind + ':') for kind in KINDS.values())):
                return True
            pending.extend(node.values())
        elif isinstance(node, list):
            pending.extend(node)
    return False


@closed
def validate_row(row, protocol):
    fields(row, {'id', 'version', 'snapshot', 'item_id', 'created', 'command_id'})
    b = row['snapshot']; fields(b, COMMON | EXTRA[protocol]); hashed(b); permissions(b)
    finite(row['created']); uuid.UUID(row['command_id']); fields(b['request'], REQUEST_FIELDS)
    for key in ('root', 'parent_plan', 'proposal', 'capability'): reference(b[key])
    if (b['protocol'] != protocol or b['scope'] != SCOPE or b['parent_kind'] != PARENT_KIND or
            b['operation'] != OPERATION or row['item_id'] != b['item_id'] or row['version'] != b['version'] or
            row['id'] != KINDS[protocol] + ':' + b['version'] or b['request']['id'] != b['item_id'] or
            b['request']['plan'] != b['parent_plan'] or b['request']['proposal'] != b['proposal']):
        raise ValueError('V3准备须保持独立协议、完整请求及原归属。')
    if protocol == CHECKPOINT:
        fields(b['history'], HISTORY_FIELDS); permissions(b['history'])
        from .work_coordinator_handoff_post_round_v2_run_contracts import validate_path
        validate_path(b['history']['saved_path'])
        if (type(b['cutoff']) is not int or b['cutoff'] < 1 or
                b['parent_plan'] != plan_ref(b['plan']) or b['plan'].get(LINEAGE_FIELD, {}).get('protocol') != PARENT_LINEAGE or
                b['history']['saved_path']['plan'] != b['parent_plan'] or
                b['history']['root'] != b['root'] or b['history']['successor_capability'] != b['capability'] or
                b['history']['post_round_capability'] == b['capability'] or
                b['preview']['capability'] != b['capability']):
            raise ValueError('V3检查点须绑定真实V8路径及两份不同能力。')
        finite(b['original_dispatch_before'])
    else:
        reference(b['checkpoint']); fields(b['child_seed'], {'id', 'created'})
        finite(b['child_seed']['created']); hashed(b['child_plan'])
        if (b['child_plan']['id'] != b['child_seed']['id'] or
                b['child_plan'][LINEAGE_FIELD] != {'protocol': LINEAGE, 'root': b['root'],
                    'parent_plan': b['parent_plan'], 'checkpoint': b['checkpoint'], 'proposal': b['proposal']}):
            raise ValueError('V3候选须保留独立lineage与实际父引用。')
    return deepcopy(row)


class _CheckpointParent(WorkCoordinatorHandoffPostRoundV2History):
    """Private preparation owner; rebuild exact carriers before namespace use."""
    def __init__(self, owner, cutoff, owned):
        super().__init__(owner.s)
        self.owner = owner; self.cutoff = cutoff; self.owned = owned

    def _boundary(self, db, plan, identity):
        proposal, source = super()._boundary(db, plan, identity)
        if source['seq'] > self.cutoff:
            raise ValueError('V3来源检查晚于实际检查点截止。')
        self._cutoff = self.cutoff + 1
        return proposal, source

    def _audit_namespace(self, db, plan, h):
        from .work_coordinator_handoff_post_round_v2_history_namespace import audit
        carriers = ()
        if self.owned is not None:
            facts = {'plan': plan, 'proposal': self._proposal_row, 'history': h,
                'preview': self._proposal_row['snapshot']['adjustment'],
                'source_check_event': self._boundary_event}
            carriers = self.owner._canonical_carriers(db, facts, *self.owned)
        audit(self, db, plan, h, _owned=carriers)


class WorkCoordinatorHandoffPostRoundV3Candidates:
    def __init__(self, service):
        self.s = service

    def _event(self, db, kind, payload):
        from .work_coordinator_handoff_round_sources import WorkCoordinatorHandoffRoundSources
        matches = [r for r in db.execute('SELECT * FROM events WHERE kind=?', (kind,))
            if json.loads(r['payload']) == payload and r['task_id'] is None and r['attempt_id'] is None]
        if len(matches) != 1: raise ValueError('V3准备须有唯一真实无任务归属事件。')
        return WorkCoordinatorHandoffRoundSources._event(matches[0])

    def _get(self, db, protocol, identity):
        reference(identity)
        row = validate_row(self.s.collaboration.get(db, KINDS[protocol], identity['id']), protocol)
        if ref(row) != identity: raise ValueError('V3准备引用已变化。')
        self._event(db, EVENTS[protocol], row)
        return row

    def _put(self, db, protocol, body):
        row = {'id': KINDS[protocol] + ':' + body['version'], 'version': body['version'],
            'snapshot': body, 'item_id': body['item_id'], 'created': time.time(), 'command_id': self.s.command_identity}
        validate_row(row, protocol)
        if db.execute('SELECT 1 FROM collaboration WHERE kind=? AND id=?', (KINDS[protocol], row['id'])).fetchone():
            raise ValueError('V3准备不得重复写入，须重放原命令。')
        self.s.collaboration.put(db, KINDS[protocol], row)
        self.s.store.event(None, None, EVENTS[protocol], row, db)
        return row

    def _request(self, h, plan, proposal):
        return {'id': plan['item_id'], 'revision': h['task']['revision'],
            'current_driver': self.s.work_drivers.reference(h['driver']), 'plan': plan_ref(plan),
            'authorization': self.s.work_units.auth_ref(h['authorization']),
            'frontier': digest([{'run_id': f['run']['id'], 'evidence': f['evidence']['version']} for f in h['frames']]),
            'proposal': ref(proposal)}

    def _facts(self, db, plan, identity, cutoff, owned=None):
        parent = _CheckpointParent(self, cutoff, owned)
        value = parent.read_at_proposal(db, plan, identity)
        h = value['history']; proposal = value['proposal']
        return {'plan': plan, 'proposal': proposal, 'history': h,
            'preview': proposal['snapshot']['adjustment'], 'source_check_event': value['boundary_event'],
            'current_material_observation': value['current_material_observation'], 'parent_reader': parent}

    def _live(self, db, request):
        fields(request, REQUEST_FIELDS)
        item = self.s.work_items.item(db, request['id']); plan = self.s.teamwork.current(db, item['id'])
        proposal = self.s.work_proposals.current(db, item['id'])
        if not proposal or ref(proposal) != request['proposal'] or not proposal_state(proposal['snapshot']):
            raise ValueError('V3准备须为当前准确最新V8提案。')
        driver = self.s.work_drivers.current(db, item['id'])
        authorization = self.s.work_units.authorization(db, driver)
        expected = {'id': item['id'], 'revision': item['revision'],
            'current_driver': self.s.work_drivers.reference(driver), 'plan': plan_ref(plan),
            'authorization': self.s.work_units.auth_ref(authorization),
            'frontier': self.s.work_units.frontier(db, item['id']), 'proposal': ref(proposal)}
        if request != expected: raise ValueError('V3准备原七字段所指当前版本已变化。')
        # Full current namespace audit BEFORE any V3 preparation record exists.
        from .work_coordinator_handoff_runtime import reader
        live = reader(self.s, db, plan).read(db, plan)
        cutoff = db.execute('SELECT COALESCE(MAX(seq),0) FROM events').fetchone()[0]
        facts = self._facts(db, plan, request['proposal'], cutoff); h = facts['history']
        if (request != self._request(h, plan, proposal) or
                request['revision'] != item['revision'] or h['driver'] != live['driver'] or
                h['authorization'] != live['authorization'] or h['frontier'] != live['frontier'] or
                h['material_observation'] != live['material_observation'] or
                self.s.work_drivers.current(db, item['id']) != h['driver'] or
                self.s.work_units.authorization(db, h['driver']) != h['authorization'] or
                h['usage']['attempts'] + 2 > h['authorization']['max_attempts_total'] or
                time.time() >= h['authorization']['dispatch_before'] or self.s.work_transitions.consumption(db, proposal)):
            raise ValueError('V3原七字段、当前责任授权、累计、期限或消费状态已变化。')
        return facts

    def _common(self, protocol, facts, request):
        h = facts['history']
        return {'protocol': protocol, 'operation': OPERATION, 'scope': SCOPE, 'parent_kind': PARENT_KIND,
            'item_id': facts['plan']['item_id'], 'root': h['root'], 'parent_plan': plan_ref(facts['plan']),
            'proposal': ref(facts['proposal']), 'capability': h['successor_capability'], 'request': request, **PERMISSIONS}

    def _checkpoint(self, facts, request, cutoff):
        return versioned({**self._common(CHECKPOINT, facts, request), 'plan': facts['plan'],
            'history': {k: deepcopy(facts['history'][k]) for k in HISTORY_FIELDS},
            'preview': facts['preview'], 'source_check_event': facts['source_check_event'], 'cutoff': cutoff,
            'original_dispatch_before': facts['history']['authorization']['dispatch_before']})

    def _candidate(self, facts, checkpoint, seed):
        h = facts['history']; preview = facts['preview']; parent = facts['plan']
        transition = {'kind': OPERATION, 'proposal': ref(facts['proposal']), 'source': preview['source'],
            'impact': {k: preview[k] for k in ('direct_changes', 'affected_keys', 'protected_keys')},
            'usage_at_transition': preview['usage'], 'authorization_from': preview['authorization'],
            'command_id': checkpoint['command_id'], 'no_dispatch': True}
        child = {**deepcopy(parent), **seed, 'item_revision': h['task']['revision'],
            'units': deepcopy(preview['candidate_units']), 'previous_plan': parent['id'],
            'previous_plan_reference': plan_ref(parent), 'result_lineage': deepcopy(preview['carry_sources']),
            'historical_coordinators': sorted(set(parent['historical_coordinators']) | set(preview['historical_coordinators'])),
            'transition': transition, LINEAGE_FIELD: {'protocol': LINEAGE, 'root': h['root'],
                'parent_plan': plan_ref(parent), 'checkpoint': ref(checkpoint), 'proposal': ref(facts['proposal'])}}
        child.pop('version'); child = versioned(child)
        return versioned({**self._common(CANDIDATE, facts, checkpoint['snapshot']['request']),
            'checkpoint': ref(checkpoint), 'child_seed': seed, 'child_plan': child, 'transition': transition})

    def _namespace(self, db, facts, checkpoint, candidate):
        item = facts['plan']['item_id']; ids = (facts['plan']['id'], facts['plan']['version'], facts['history']['root']['id'],
            *ref(checkpoint).values(), *ref(candidate).values())
        expected = {CHECKPOINT: checkpoint, CANDIDATE: candidate}
        rows = {k: [] for k in KINDS}; events = {k: [] for k in KINDS}
        for table, column, kinds, groups in (('collaboration', 'data', KINDS, rows), ('events', 'payload', EVENTS, events)):
            for row in db.execute('SELECT * FROM ' + table):
                value = json.loads(row[column])
                if (row['kind'] in kinds.values() or marker(value) or belongs(value, '', ids[3:])) and belongs(value, item, ids):
                    protocol = value.get('snapshot', {}).get('protocol')
                    if (protocol not in KINDS or row['kind'] != kinds[protocol] or
                            (table == 'events' and (row['task_id'] is not None or row['attempt_id'] is not None))):
                        raise ValueError('V3准备namespace有改名、嵌套或错误归属。')
                    if table == 'collaboration' and row['id'] != value['id']:
                        raise ValueError('V3数据库键不等于实际记录身份。')
                    groups[protocol].append(validate_row(value, protocol))
        for protocol in KINDS:
            if rows[protocol] != [expected[protocol]] or events[protocol] != [expected[protocol]]:
                raise ValueError('V3准备记录与事件全集有孤立、重复或遗漏。')

    def _ancestry_resources(self, db, plan):
        # Retain the bounded parent's obligation through this owner's return,
        # including members declared by earlier actual plans.
        plans = WorkCoordinatorHandoffPostRoundV2History(self.s)._collect(db, plan)
        selections = [(p['id'], {u['key']: {k: v for k, v in self.s.resources.selection(db,
            u['member']['reference']['member_id']).items() if k != 'selected_at'}
            for u in p['units']}) for p in plans]
        return plans, selections

    @closed
    def prepare(self, db, request):
        if (not db.in_transaction or self.s.command_payload_hash != digest(request)):
            raise ValueError('V3准备须属于准确原七字段命令写事务。')
        uuid.UUID(self.s.command_identity)
        with transaction(self.s):
            db.execute('SAVEPOINT post_round_v3_preparation')
            try:
                facts = self._live(db, request); material = facts['history']['material_observation']
                resources = self._ancestry_resources(db, facts['plan'])
                track(self.s, material); self.s.work_file_rework_applications.preserve(material)
                # Still before V3 writes, independently repeat current full audit.
                again = self._live(db, request)
                if self._checkpoint(facts, request, 1) != self._checkpoint(again, request, 1):
                    raise ValueError('V3准备保存材料期间完整来源已变化。')
                cutoff = db.execute('SELECT COALESCE(MAX(seq),0) FROM events').fetchone()[0]
                checkpoint = self._put(db, CHECKPOINT, self._checkpoint(again, request, cutoff))
                candidate = self._put(db, CANDIDATE, self._candidate(again, checkpoint,
                    {'id': 'teamwork:' + uid(), 'created': time.time()}))
                result = self.read(db, ref(candidate))
                changes = db.total_changes; h = again['history']
                # Historical reads deliberately ignore live pointers. Preparation
                # must still bind the current seven-field request at its end.
                if (self.s.teamwork.current(db, request['id']) != again['plan'] or
                        self.s.work_proposals.current(db, request['id']) != again['proposal'] or
                        self.s.work_drivers.current(db, request['id']) != h['driver'] or
                        self.s.work_units.authorization(db, h['driver']) != h['authorization'] or
                        self.s.work_items.item(db, request['id'])['revision'] != request['revision'] or
                        self.s.work_units.frontier(db, request['id']) != request['frontier'] or
                        self.s.work_transitions.consumption(db, again['proposal'])):
                    raise ValueError('V3准备结束当前原七字段、责任授权或消费状态已变化。')
                from .work_coordinator_handoff_round_checkpoints import WorkCoordinatorHandoffRoundCheckpoints
                WorkCoordinatorHandoffRoundCheckpoints(self.s)._recheck(db, result)
                if (result['current_material_observation'] != material or db.total_changes != changes or
                        self._ancestry_resources(db, facts['plan']) != resources or
                        time.time() >= h['authorization']['dispatch_before']):
                    raise ValueError('V3准备结束实际材料、祖先资源、数据库或原最初截止已变化。')
            except BaseException:
                db.execute('ROLLBACK TO post_round_v3_preparation'); db.execute('RELEASE post_round_v3_preparation')
                raise
            else:
                db.execute('RELEASE post_round_v3_preparation')
                return result

    def _canonical_carriers(self, db, facts, checkpoint, candidate):
        body = checkpoint['snapshot']; b = candidate['snapshot']; h = facts['history']
        event = self._event(db, EVENTS[CHECKPOINT], checkpoint)
        ce = self._event(db, EVENTS[CANDIDATE], candidate)
        if (body['request'] != self._request(h, facts['plan'], facts['proposal']) or
                body != self._checkpoint(facts, body['request'], body['cutoff']) or
                b != self._candidate(facts, checkpoint, b['child_seed']) or
                event['seq'] != body['cutoff'] + 1 or ce['seq'] != event['seq'] + 1 or
                candidate['command_id'] != checkpoint['command_id'] or
                not facts['proposal']['created'] <= checkpoint['created'] <= b['child_seed']['created'] <= candidate['created'] < h['authorization']['dispatch_before']):
            raise ValueError('V3完整准备载体不是准确原请求及真实截止重建。')
        self._namespace(db, facts, checkpoint, candidate)
        return ((KINDS[CHECKPOINT], checkpoint, event), (KINDS[CANDIDATE], candidate, ce))

    def _once(self, db, identity):
        candidate = self._get(db, CANDIDATE, identity); b = candidate['snapshot']
        cp = self._get(db, CHECKPOINT, b['checkpoint']); body = cp['snapshot']
        event = self._event(db, EVENTS[CHECKPOINT], cp); ce = self._event(db, EVENTS[CANDIDATE], candidate)
        facts = self._facts(db, body['plan'], b['proposal'], body['cutoff'], (cp, candidate)); h = facts['history']
        self._parent_reader = facts['parent_reader']  # This traversal only, never a cached proof.
        self._parent_reader.checkpoints._guard(db, b['child_plan'])
        if (body['request'] != self._request(h, facts['plan'], facts['proposal']) or
                body != self._checkpoint(facts, body['request'], body['cutoff']) or
                b != self._candidate(facts, cp, b['child_seed']) or event['seq'] != body['cutoff'] + 1 or
                ce['seq'] != event['seq'] + 1 or candidate['command_id'] != cp['command_id'] or
                not facts['proposal']['created'] <= cp['created'] <= b['child_seed']['created'] <= candidate['created'] < h['authorization']['dispatch_before']):
            raise ValueError('V3完整原请求、候选语义、实际事件截止或原期限不符。')
        self._namespace(db, facts, cp, candidate)
        return {'checkpoint': cp, 'candidate': candidate, 'child_plan': b['child_plan'],
            'checkpoint_event': event, 'candidate_event': ce, 'frames': h['frames'],
            'observation': {'history': body['history']}, 'current_material_observation': facts['current_material_observation'],
            'candidate_data_verified': True, 'bounded_parent_components_verified': True,
            'preparation_namespace_verified': True, 'full_saved_tree_verified': False,
            'saved_edge_verified': False, 'public_apply': False, **PERMISSIONS}

    @closed
    def read(self, db, identity):
        if not db.in_transaction: raise ValueError('V3历史准备读取须属于明确数据库事务。')
        with database_observation(db):
            changes = db.total_changes
            candidate = self._get(db, CANDIDATE, identity)
            plan = self._get(db, CHECKPOINT, candidate['snapshot']['checkpoint'])['snapshot']['plan']
            resources = self._ancestry_resources(db, plan)
            first = self._once(db, identity); again = self._once(db, identity)
            from .work_coordinator_handoff_round_checkpoints import WorkCoordinatorHandoffRoundCheckpoints
            WorkCoordinatorHandoffRoundCheckpoints(self.s)._recheck(db, again)
            for layer in self._parent_reader._layers:
                self.s.work_file_rework_applications.require_baseline(layer._ready['observation']['history']['material_observation'])
            if first != again or db.total_changes != changes or self._ancestry_resources(db, plan) != resources:
                raise ValueError('V3历史准备双遍、末尾材料或DB变化。')
            return deepcopy(again)
