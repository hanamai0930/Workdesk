"""Exact internal post-ROUND preparation, with no saved-edge permission.

The current-parent gate is intentional. The saved successor reader must use
the separate event-bounded component and its own whole-tree namespace audit.
"""
from contextlib import contextmanager
from copy import deepcopy
import json
import time
import uuid

from .store import digest, uid
from .work_transitions import plan_ref
from .work_read_observation import database_observation
from .work_coordinator_handoff_records import PERMISSIONS, versioned, fields, reference
from .work_coordinator_handoff_adjustment_contracts import closed
from .work_coordinator_handoff_adjustment_records import hashed, finite, permissions
from .work_coordinator_handoff_runtime import reader
from .work_coordinator_handoff_post_round_runtime import belongs
from .work_coordinator_handoff_post_round_preview import proposal_state, rebuild_preview
from .work_coordinator_handoff_post_round_materials import transaction, track

PREFIX = 'COORDINATOR_FILE_HANDOFF_POST_ROUND_ADJUSTMENT_'
CHECKPOINT = PREFIX + 'CHECKPOINT_V1'
CANDIDATE = PREFIX + 'CANDIDATE_V1'
LINEAGE = PREFIX + 'LINEAGE_V1'
LINEAGE_FIELD = 'coordinator_file_handoff_post_round_lineage'
OPERATION = 'POST_ROUND_ADJUSTMENT'
SCOPE = 'CURRENT_PARENT_PREPARATION; NO_SAVED_EDGE_OR_SUCCESSOR_INPUT_PERMISSION'
KINDS = {CHECKPOINT: 'work_coordinator_handoff_post_round_checkpoint',
         CANDIDATE: 'work_coordinator_handoff_post_round_candidate'}
EVENTS = {CHECKPOINT: 'coordinator_post_round_adjustment_checkpointed',
          CANDIDATE: 'coordinator_post_round_adjustment_candidate_prepared'}
CHECKPOINT_EVENT = EVENTS[CHECKPOINT]
HISTORY_FIELDS = {'protocol', 'root', 'marker', 'capability', 'adjustment_capability',
    'round_capability', 'post_round_capability', 'task', 'driver', 'authorization',
    'phase', 'frontier', 'saved_path', 'material_observation', 'delivery', 'completion', 'units', *PERMISSIONS}
COMMON = {'protocol', 'version', 'operation', 'scope', 'item_id', 'root', 'parent_plan',
          'proposal', 'capability', *PERMISSIONS}
EXTRA = {CHECKPOINT: {'plan', 'history', 'preview', 'cutoff', 'original_dispatch_before'},
    CANDIDATE: {'checkpoint', 'child_seed', 'child_plan', 'transition'}}


def ref(row):
    return {k: row[k] for k in ('id', 'version')}


def preparation_marker(value):
    """Find immutable preparation identities through renamed outer wrappers."""
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
    body = row['snapshot']; fields(body, COMMON | EXTRA[protocol]); hashed(body); permissions(body)
    finite(row['created']); uuid.UUID(row['command_id'])
    for key in ('root', 'parent_plan', 'proposal', 'capability'): reference(body[key])
    if (body['protocol'] != protocol or body['operation'] != OPERATION or body['scope'] != SCOPE or
            row['item_id'] != body['item_id'] or row['version'] != body['version'] or
            row['id'] != KINDS[protocol] + ':' + body['version']):
        raise ValueError('新调整准备记录的完整归属、操作或范围不符。')
    if protocol == CHECKPOINT:
        fields(body['history'], HISTORY_FIELDS); permissions(body['history'])
        if (body['parent_plan'] != plan_ref(body['plan']) or type(body['cutoff']) is not int or body['cutoff'] < 1 or
                body['history']['saved_path']['plan'] != body['parent_plan'] or
                body['history']['root'] != body['root'] or
                body['history']['post_round_capability'] != body['capability'] or
                body['preview']['operation'] != OPERATION):
            raise ValueError('新检查点须保持完整父路径和新调整能力。')
    else:
        reference(body['checkpoint']); fields(body['child_seed'], {'id', 'created'})
        finite(body['child_seed']['created']); hashed(body['child_plan'])
        if (plan_ref(body['child_plan'])['id'] != body['child_seed']['id'] or
                body['child_plan'][LINEAGE_FIELD] != {'protocol': LINEAGE, 'root': body['root'],
                    'parent_plan': body['parent_plan'], 'checkpoint': body['checkpoint'], 'proposal': body['proposal']}):
            raise ValueError('新候选须保留独立lineage和准确原来源。')
    return deepcopy(row)


class WorkCoordinatorHandoffPostRoundCandidates:
    def __init__(self, service):
        self.s = service

    def _event(self, db, kind, payload):
        matches = [r for r in db.execute('SELECT * FROM events WHERE kind=?', (kind,))
            if json.loads(r['payload']) == payload and r['task_id'] is None and r['attempt_id'] is None]
        if len(matches) != 1: raise ValueError('新准备记录缺少唯一真实无任务归属事件。')
        from .work_coordinator_handoff_round_sources import WorkCoordinatorHandoffRoundSources
        return WorkCoordinatorHandoffRoundSources._event(matches[0])

    def _get(self, db, protocol, identity):
        reference(identity)
        row = self.s.collaboration.get(db, KINDS[protocol], identity['id'])
        row = validate_row(row, protocol)
        if ref(row) != identity: raise ValueError('新准备记录引用不是准确原版本。')
        self._event(db, EVENTS[protocol], row)
        return row

    def _put(self, db, protocol, body):
        row = {'id': KINDS[protocol] + ':' + body['version'], 'version': body['version'],
            'snapshot': body, 'item_id': body['item_id'], 'created': time.time(), 'command_id': self.s.command_identity}
        validate_row(row, protocol)
        if db.execute('SELECT 1 FROM collaboration WHERE kind=? AND id=?', (KINDS[protocol], row['id'])).fetchone():
            raise ValueError('同一新调整检查点不能重复创建，须由原命令重放回执。')
        self.s.collaboration.put(db, KINDS[protocol], row)
        self.s.store.event(None, None, EVENTS[protocol], row, db)
        return row

    def _facts(self, db, proposal_ref):
        reference(proposal_ref)
        proposal = self.s.collaboration.get(db, 'work_proposal', proposal_ref['id'])
        if not proposal or ref(proposal) != proposal_ref or self.s.work_proposals.current(db, proposal['item_id']) != proposal:
            raise ValueError('新准备须绑定当前实际最新检查提案。')
        if not proposal_state(proposal['snapshot']): raise ValueError('新准备只接受真实POST_ROUND_ADJUSTMENT预览。')
        plan = self.s.teamwork.current(db, proposal['item_id'])
        h = reader(self.s, db, plan).read(db, plan)
        item = self.s.work_items.item(db, proposal['item_id'])
        if (h['driver']['state'] != 'RESERVED' or h['task'] != {'id': item['id'], 'revision': item['revision']} or
                h['post_round_capability'] != proposal['snapshot']['adjustment']['capability']):
            raise ValueError('新准备的真实任务、责任或开启能力已变化。')
        source = next((f for f in h['frames'] if f['run']['id'] == proposal['snapshot']['source']['run_id']), None)
        if source is None: raise ValueError('新准备缺少准确实际协调来源。')
        preview = rebuild_preview(plan, h, source['run'], proposal['snapshot']['decision'], observed_at=proposal['created'])
        if preview != proposal['snapshot']['adjustment']:
            raise ValueError('新准备语义不等于实际原回复和完整来源重建。')
        if (time.time() >= h['authorization']['dispatch_before'] or
                h['usage']['attempts'] + 2 > h['authorization']['max_attempts_total'] or
                self.s.work_transitions.consumption(db, proposal)):
            raise ValueError('新准备的原授权余量、最初截止或消费状态不符。')
        events = [json.loads(r['payload']) for r in db.execute(
            "SELECT payload FROM events WHERE kind='coordinator_proposal_checked' ORDER BY seq")
            if belongs(json.loads(r['payload']), item['id'], (plan['id'], plan['version']))]
        if not events or events[-1] != proposal: raise ValueError('新准备不可回退实际最新检查。')
        return {'proposal': proposal, 'plan': plan, 'history': h, 'preview': preview}

    def _common(self, protocol, facts):
        h = facts['history']
        return {'protocol': protocol, 'operation': OPERATION, 'scope': SCOPE,
            'item_id': facts['plan']['item_id'], 'root': h['root'], 'parent_plan': plan_ref(facts['plan']),
            'proposal': ref(facts['proposal']), 'capability': h['post_round_capability'], **PERMISSIONS}

    def _checkpoint(self, facts, cutoff):
        h = facts['history']
        return versioned({**self._common(CHECKPOINT, facts), 'plan': facts['plan'],
            'history': {k: deepcopy(h[k]) for k in HISTORY_FIELDS}, 'preview': facts['preview'],
            'cutoff': cutoff, 'original_dispatch_before': h['authorization']['dispatch_before']})

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
        return versioned({**self._common(CANDIDATE, facts), 'checkpoint': ref(checkpoint),
            'child_seed': seed, 'child_plan': child, 'transition': transition})

    def _namespace(self, db, facts, checkpoint, candidate):
        item = facts['plan']['item_id']; ids = (facts['plan']['id'], facts['plan']['version'],
            facts['history']['root']['id'], ref(facts['proposal'])['id'])
        expected = {CHECKPOINT: checkpoint, CANDIDATE: candidate}
        rows = {k: [] for k in KINDS}; events = {k: [] for k in KINDS}
        for row in db.execute('SELECT kind,data FROM collaboration'):
            value = json.loads(row['data']); protocol = value.get('snapshot', {}).get('protocol')
            if (row['kind'] in KINDS.values() or preparation_marker(value)) and belongs(value, item, ids):
                if protocol not in KINDS or row['kind'] != KINDS[protocol]:
                    raise ValueError('新准备namespace有改名或错误协议记录。')
                rows[protocol].append(validate_row(value, protocol))
        for row in db.execute('SELECT * FROM events'):
            value = json.loads(row['payload']); protocol = value.get('snapshot', {}).get('protocol')
            if (row['kind'] in EVENTS.values() or preparation_marker(value)) and belongs(value, item, ids):
                if protocol not in KINDS or row['kind'] != EVENTS[protocol] or row['task_id'] is not None or row['attempt_id'] is not None:
                    raise ValueError('新准备namespace有改名或错误归属事件。')
                events[protocol].append(validate_row(value, protocol))
        for protocol in KINDS:
            if rows[protocol] != [expected[protocol]] or events[protocol] != [expected[protocol]]:
                raise ValueError('新准备记录与真实事件全集有孤立、重复或遗漏。')

    @closed
    def prepare(self, db, proposal_ref):
        """Internal transaction component. Plan, driver, auth and runs stay put."""
        if not db.in_transaction: raise ValueError('新准备必须属于调用方命令写事务。')
        uuid.UUID(self.s.command_identity)
        with transaction(self.s):
            db.execute('SAVEPOINT post_round_preparation')
            try:
                facts = self._facts(db, proposal_ref)
                material = facts['history']['material_observation']
                track(self.s, material); self.s.work_file_rework_applications.preserve(material)
                if self._facts(db, proposal_ref) != facts: raise ValueError('新准备材料保存期间来源已变化。')
                cutoff = db.execute('SELECT COALESCE(MAX(seq),0) FROM events').fetchone()[0]
                checkpoint = self._put(db, CHECKPOINT, self._checkpoint(facts, cutoff))
                candidate = self._put(db, CANDIDATE, self._candidate(facts, checkpoint,
                    {'id': 'teamwork:' + uid(), 'created': time.time()}))
                result = self.read(db, ref(candidate))
            except BaseException:
                db.execute('ROLLBACK TO post_round_preparation'); db.execute('RELEASE post_round_preparation')
                raise
            else:
                db.execute('RELEASE post_round_preparation')
                return result

    def _once(self, db, identity):
        candidate = self._get(db, CANDIDATE, identity); b = candidate['snapshot']
        checkpoint = self._get(db, CHECKPOINT, b['checkpoint']); cp = checkpoint['snapshot']
        facts = self._facts(db, b['proposal'])
        event = self._event(db, CHECKPOINT_EVENT, checkpoint)
        ce = self._event(db, EVENTS[CANDIDATE], candidate)
        if (cp != self._checkpoint(facts, cp['cutoff']) or b != self._candidate(facts, checkpoint, b['child_seed']) or
                event['seq'] != cp['cutoff'] + 1 or ce['seq'] != event['seq'] + 1 or
                candidate['command_id'] != checkpoint['command_id'] or
                not facts['proposal']['created'] <= checkpoint['created'] <= b['child_seed']['created'] <= candidate['created']):
            raise ValueError('新准备数据、完整语义、命令或实际事件顺序不符。')
        from .work_coordinator_handoff_post_round_history import reconstruct
        _, h, actual = reconstruct(self.s, db, cp['plan'], event, cp['history']['material_observation'])
        if {k: h[k] for k in HISTORY_FIELDS} != cp['history']:
            raise ValueError('新检查点不等于真实事件截止完整父层重建。')
        self._namespace(db, facts, checkpoint, candidate)
        return {'checkpoint': checkpoint, 'candidate': candidate, 'child_plan': b['child_plan'],
            'frames': h['frames'], 'observation': {'history': cp['history']},
            'current_material_observation': actual, 'candidate_data_verified': True,
            'bounded_parent_components_verified': True, 'saved_edge_verified': False, 'public_apply': False, **PERMISSIONS}

    @closed
    def read(self, db, identity):
        if not db.in_transaction: raise ValueError('新候选读取须属于明确数据库事务。')
        with database_observation(db):
            changes = db.total_changes; first = self._once(db, identity); again = self._once(db, identity)
            from .work_coordinator_handoff_round_checkpoints import WorkCoordinatorHandoffRoundCheckpoints
            WorkCoordinatorHandoffRoundCheckpoints(self.s)._recheck(db, again)
            if first != again or changes != db.total_changes:
                raise ValueError('新候选两次独立完整重建、末尾材料或数据库发生变化。')
            return deepcopy(again)
