"""Exact root or saved-parent write checkpoints; no public handler.

Full history must be reconstructed independently of the candidate. The
original-root reader retains its current/unique-root assertions.
"""
from copy import deepcopy
import json
import time

from .store import digest, uid
from .work_transitions import plan_ref
from .work_coordinator_handoff_runtime import reader
from .work_coordinator_handoff_records import PERMISSIONS, versioned
from .work_coordinator_handoff_adjustment_consumption import require_unconsumed
from .work_coordinator_handoff_adjustments import preview as rebuild_preview, PREVIEW, proposal_state
from .work_coordinator_handoff_adjustment_records import (
    CHECKPOINT, CANDIDATE, SCOPE, LINEAGE, PAUSE_REASON, KIND_FOR, EVENTS,
    WorkCoordinatorHandoffAdjustmentRecords, HISTORY_FIELDS)


class WorkCoordinatorHandoffAdjustmentCandidates:
    def __init__(self, service):
        self.s = service
        self.records = WorkCoordinatorHandoffAdjustmentRecords(service)

    def _facts(self, db, proposal_ref):
        proposal = self.s.collaboration.get(db, 'work_proposal', proposal_ref['id'])
        if (set(proposal) != {'id', 'version', 'snapshot', 'item_id', 'created', 'command_id'} or
                self.s.work_proposals.reference(proposal) != proposal_ref or
                proposal['version'] != digest(proposal['snapshot'])):
            raise ValueError('候选须使用准确完整原提案。')
        item = self.s.work_items.item(db, proposal['item_id'])
        require_unconsumed(self.s,db,proposal_ref,item['id'])
        plan = self.s.teamwork.current(db, item['id'])
        if (not plan or
                self.s.work_proposals.current(db, item['id']) != proposal or
                not proposal_state(proposal['snapshot']) or
                self.s.work_transitions.consumption(db, proposal)):
            raise ValueError('写前检查点只接受当前已核方案的未消费V3预览。')
        history = reader(self.s, db, plan).read(db, plan)
        driver, auth = history['driver'], history['authorization']
        if (not driver or not auth or driver['state'] != 'RESERVED' or
                self.s.work_proposals.live_issues(db, item, proposal)):
            raise ValueError('写前检查点的责任、原授权或准确预览已变化。')
        snapshot = proposal['snapshot']; rows = self.s.work_units.rows(db, item['id'])
        run = next((r for r in rows if r['id'] == snapshot['source']['run_id']), None)
        if run is None: raise ValueError('写前检查点缺少真实原协调运行。')
        preview = rebuild_preview(self.s, db, item, driver, plan, rows, run, snapshot['decision'])
        if preview != snapshot['adjustment'] or preview['source'] != snapshot['source']:
            raise ValueError('写前预览不等于真实来源重建。')
        reader(self.s, db, plan)._event(db, 'coordinator_proposal_checked', proposal)
        actual = [json.loads(r['payload']) for r in db.execute(
            "SELECT payload FROM events WHERE kind='coordinator_proposal_checked' ORDER BY seq")
            if json.loads(r['payload']).get('item_id') == item['id']]
        if not actual or actual[-1] != proposal:
            raise ValueError('候选须使用实际最新提案，旧current指针不能回退来源。')
        return {'item': item, 'plan': plan, 'history': history, 'proposal': proposal, 'preview': preview}

    @staticmethod
    def _common(protocol, facts):
        preview = facts['preview']
        return {'protocol': protocol, 'item_id': facts['item']['id'], 'root': preview['root'],
            'parent_plan': plan_ref(facts['plan']), 'capability': preview['capability'],
            'operation': 'ADJUSTMENT', **PERMISSIONS, 'scope': SCOPE}

    def _checkpoint(self, facts):
        h, preview = facts['history'], facts['preview']
        return versioned({**self._common(CHECKPOINT, facts), 'preview': preview,
            'proposal': self.s.work_proposals.reference(facts['proposal']), 'plan': facts['plan'],
            'history': {k: deepcopy(h[k]) for k in HISTORY_FIELDS}, 'usage': preview['usage'],
            'original_dispatch_before': h['authorization']['dispatch_before'],
            'authorization_from': self.s.work_units.auth_ref(h['authorization'])})

    def _candidate(self, facts, checkpoint, preview_row, seed, command_id):
        preview = facts['preview']
        transition = {'proposal': self.s.work_proposals.reference(facts['proposal']), 'source': preview['source'],
            'impact': {k: preview[k] for k in ('direct_changes', 'affected_keys', 'protected_keys')},
            'usage_at_transition': preview['usage'], 'authorization_from': preview['authorization'],
            'command_id': command_id, 'no_dispatch': True}
        return versioned({**self._common(CANDIDATE, facts), 'preview': self.records.ref(preview_row),
            'checkpoint': self.records.ref(checkpoint), 'proposal': transition['proposal'],
            'child_seed': seed, 'candidate_units': preview['candidate_units'],
            'carry_sources': preview['carry_sources'], 'historical_coordinators': preview['historical_coordinators'],
            'transition': transition, 'expected_pause_reason': PAUSE_REASON})

    def _put(self, db, protocol, body, item_id):
        identity = KIND_FOR[protocol] + ':' + body['version']
        existed = db.execute('SELECT 1 FROM collaboration WHERE id=?', (identity,)).fetchone()
        row = self.records.put(db, protocol, body, item_id)
        if not existed: self.s.store.event(None, None, EVENTS[protocol], row, db)
        self._event(db, protocol, row)
        return row

    def _event(self, db, protocol, row):
        matches = [r['seq'] for r in db.execute('SELECT seq,payload FROM events WHERE kind=?', (EVENTS[protocol],))
            if json.loads(r['payload']) == row]
        if len(matches) != 1: raise ValueError('调整记录缺少唯一准确实际事件。')
        return matches[0]

    def prepare(self, db, proposal_ref):
        """Internal, caller-owned transaction. Does not change plan/auth/driver."""
        facts = self._facts(db, proposal_ref)
        existing = [r for r in self.s.collaboration.all(db, KIND_FOR[CANDIDATE])
            if r.get('item_id') == facts['item']['id'] and r.get('snapshot', {}).get('proposal') == proposal_ref]
        if existing:
            if len(existing) != 1: raise ValueError('同提案有多份候选身份。')
            return self.require_save(db, self.records.ref(existing[0]))
        preview_row = self._put(db, PREVIEW, facts['preview'], facts['item']['id'])
        checkpoint = self._put(db, CHECKPOINT, self._checkpoint(facts), facts['item']['id'])
        seed = {'id': 'teamwork:' + uid(), 'created': time.time()}
        candidate = self._put(db, CANDIDATE,
            self._candidate(facts, checkpoint, preview_row, seed, self.s.command_identity), facts['item']['id'])
        result = self.require_save(db, self.records.ref(candidate))
        if self._facts(db, proposal_ref) != facts:
            raise ValueError('候选保存最终原历史、当前材料或授权已变化。')
        return result

    def require_save(self, db, candidate_ref):
        """Read-only original-parent precondition, not a complete saved-edge verifier."""
        writes = db.total_changes
        candidate = self.records.get(db, CANDIDATE, candidate_ref); body = candidate['snapshot']
        facts = self._facts(db, body['proposal'])
        checkpoint = self.records.get(db, CHECKPOINT, body['checkpoint'])
        preview_row = self.records.get(db, PREVIEW, body['preview'])
        related = [r for r in self.s.collaboration.all(db, KIND_FOR[CANDIDATE])
            if r.get('item_id') == candidate['item_id'] and r.get('snapshot', {}).get('proposal') == body['proposal']]
        if related != [candidate]: raise ValueError('同准确提案须有唯一候选。')
        for row in (candidate, checkpoint, preview_row):
            if row['item_id'] != facts['item']['id'] or row['created'] < facts['proposal']['created']:
                raise ValueError('候选记录外壳的原提案归属或时间不一致。')
        if (checkpoint['snapshot'] != self._checkpoint(facts) or preview_row['snapshot'] != facts['preview'] or
                body != self._candidate(facts, checkpoint, preview_row, body['child_seed'], candidate['command_id']) or
                body['transition']['command_id'] != candidate['command_id'] or
                not checkpoint['created'] <= body['child_seed']['created'] <= candidate['created']):
            raise ValueError('候选、检查点或原seed不等于准确原历史重建。')
        sequences = [self._event(db, protocol, row) for protocol, row in
            ((PREVIEW, preview_row), (CHECKPOINT, checkpoint), (CANDIDATE, candidate))]
        proposal_seq = reader(self.s, db, facts['plan'])._event(db, 'coordinator_proposal_checked', facts['proposal'])
        if not proposal_seq < sequences[0] < sequences[1] < sequences[2]:
            raise ValueError('候选与原提案的实际写前事件顺序不符。')
        child = {**deepcopy(facts['plan']), **body['child_seed'], 'item_revision': facts['item']['revision'],
            'units': deepcopy(body['candidate_units']), 'previous_plan': facts['plan']['id'],
            'previous_plan_reference': plan_ref(facts['plan']), 'result_lineage': deepcopy(body['carry_sources']),
            'historical_coordinators': deepcopy(body['historical_coordinators']), 'transition': deepcopy(body['transition']),
            'coordinator_file_handoff_lineage': {'protocol': LINEAGE, 'root': body['root'],
                'parent_plan': body['parent_plan'], 'candidate': candidate_ref}}
        child.pop('version'); child = versioned(child)
        if (db.total_changes != writes or self._facts(db, body['proposal']) != facts or
                time.time() >= checkpoint['snapshot']['original_dispatch_before']):
            raise ValueError('候选最终只读前沿、材料、原累计或最初截止已变化。')
        return deepcopy({'candidate': candidate, 'checkpoint': checkpoint, 'preview': preview_row,
            'child_plan': child, 'history': facts['history'], 'no_dispatch': True,
            'saved_edge_verified': False, 'public_apply': False})
