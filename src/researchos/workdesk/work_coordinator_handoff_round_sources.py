"""Current root/adjustment parent observation for explicit rounds, with no writes.

Saved round reconstruction must use its own event-bounded path reader. A current
observation is data and cannot replace that future saved-edge proof.
"""
from copy import deepcopy
import json
import time

from .store import digest
from .work_transitions import plan_ref, source_ref
from .work_coordinator_handoff_roots import MARKER_FIELD, PLAN_FIELDS
from .work_coordinator_handoff_records import PERMISSIONS, versioned
from .work_coordinator_handoff_runtime import enabled, reader
from .work_coordinator_handoff_adjustments import _require_result
from .work_coordinator_handoff_adjustment_contracts import closed, successor_plan
from .work_coordinator_handoff_round_records import (
    PARENT, PARENT_SCOPE, HISTORY, validate_parent)


class WorkCoordinatorHandoffRoundSources:
    def __init__(self, service): self.s = service

    @staticmethod
    def _event(row):
        return {'seq': row['seq'], 'kind': row['kind'],
                'payload_digest': digest(json.loads(row['payload']))}

    def _unique_event(self, db, kind, payload, task=None, attempt=None):
        rows = [r for r in db.execute('SELECT * FROM events WHERE kind=? ORDER BY seq', (kind,))
                if json.loads(r['payload']) == payload and r['task_id'] == task and r['attempt_id'] == attempt]
        if len(rows) != 1: raise ValueError('回合父观察缺少唯一准确实际事件：' + kind)
        return self._event(rows[0])

    def _prior(self, db, plan, run, evidence):
        events = [r for r in db.execute(
            "SELECT * FROM events WHERE kind='coordinator_proposal_checked' ORDER BY seq")
            if json.loads(r['payload']).get('item_id') == plan['item_id']]
        actual = [json.loads(r['payload']) for r in events]; checks = []; seen = set()
        runs = {r['id']: r for r in self.s.work_units.rows(db, plan['item_id'])}
        for event, row in zip(events, actual):
            if (set(row) != {'id', 'version', 'snapshot', 'item_id', 'created', 'command_id'} or
                    row['version'] != digest(row['snapshot']) or row['id'] in seen or
                    self.s.collaboration.get(db, 'work_proposal', row['id']) != row):
                raise ValueError('回合父观察全部原提案检查的内容、事件或唯一性不符。')
            original = runs.get(row['snapshot']['source']['run_id'])
            if not original or event['task_id'] != original['task_id'] or event['attempt_id'] != original['attempt_id']:
                raise ValueError('回合父观察原提案检查不属于准确实际旧运行。')
            seen.add(row['id']); checks.append({'reference': self.s.work_proposals.reference(row),
                'state': row['snapshot']['state'], 'event': self._event(event)})
        if seen != {r['id'] for r in self.s.collaboration.all(db, 'work_proposal') if r.get('item_id') == plan['item_id']}:
            raise ValueError('回合父观察不能遗漏无准确实际事件的旧检查。')
        latest = actual[-1] if actual else None
        if self.s.work_proposals.current(db, plan['item_id']) != latest:
            raise ValueError('回合父观察的最新实际检查与当前指针不符。')
        if latest is None: return None, None, checks
        event = self._unique_event(db, 'coordinator_proposal_checked', latest, run['task_id'], run['attempt_id']) if (
            latest['snapshot']['source']['run_id'] == run['id']) else None
        body = latest['snapshot']
        if body['source']['run_id'] != run['id']: return None, None, checks
        if body['source']['version'] != evidence['version'] or body['plan'] != plan_ref(plan):
            raise ValueError('回合父观察的同版本原检查来源不符。')
        return {'reference': self.s.work_proposals.reference(latest),
                'state': body['state'], 'problems': deepcopy(body['problems'])}, event, checks

    def _collect(self, db, plan):
        if not db.in_transaction: raise ValueError('回合父观察须在同一明确数据库事务内完成。')
        if not plan or MARKER_FIELD not in plan or not enabled(self.s, db, plan):
            raise ValueError('回合父观察只接受已启用真实原runtime的交接方案。')
        if plan.get('coordinator_file_handoff_round_lineage'):
            raise ValueError('本批尚未开放round父路径重建。')
        if plan.get('coordinator_file_handoff_lineage'):
            successor_plan(plan); kind = 'ADJUSTMENT'
        else:
            if set(plan) != PLAN_FIELDS: raise ValueError('回合父观察原根完整字段不符。')
            kind = 'ROOT'
        h = reader(self.s, db, plan).read(db, plan)
        if kind == 'ROOT':
            # Reuse only the existing event/source check component, over the
            # actual root and fully reconstructed frames. No saved path is
            # fabricated and the original runtime/codec remain unchanged.
            from .work_coordinator_handoff_adjustment_path import WorkCoordinatorHandoffAdjustmentPath
            checks = WorkCoordinatorHandoffAdjustmentPath(self.s); checks._root_plan = plan
            checks._check_proposals(db, plan, h)
        item = self.s.work_items.item(db, plan['item_id']); driver, auth = h['driver'], h['authorization']
        if not driver or not auth: raise ValueError('回合父观察缺少准确原责任或原授权。')
        self.s.work_units.checked(db, {'id': item['id'], 'revision': item['revision'],
            'current_driver': self.s.work_drivers.reference(driver), 'plan': plan_ref(plan)})
        self.s.work_flows.require_stopped(db, item['id'])
        rows = self.s.work_units.rows(db, item['id']); used = self.s.work_units.usage(db, rows)
        if (len(rows) >= auth['max_attempts_total'] or time.time() >= auth['dispatch_before'] or
                h['phase'] not in {'BEFORE_A', 'A_CAPTURED', 'B_COMPLETED'}):
            raise ValueError('原累计/最初截止或真实材料阶段不能登记新回合。')
        candidates = [f for f in h['current_frames'] if f['run']['plan_id'] == plan['id'] and
                      f['run']['generation'] == driver['generation'] and
                      f['run']['input']['unit']['role'] == 'coordinate']
        if not candidates: raise ValueError('当前方案尚无准确可登记的协调回合。')
        frame = candidates[-1]; run, evidence = frame['run'], frame['source_evidence']
        unit = next(u for u in plan['units'] if u['key'] == run['unit_key'])
        if (evidence['state'] != 'COMPLETED' or not evidence['exact_identity'] or
                not evidence['result_available'] or not isinstance(evidence['answer'], str) or
                not evidence['answer'] or run['unit_revision'] != digest(unit) or
                run['input']['unit'] != unit or unit['member']['reference'] != plan['coordinator']['reference']):
            raise ValueError('源须为当前方案最新准确完成且实际退出的协调文字结果。')
        # Rebuilt historical inputs keep original dependencies. Current evidence
        # is required only for results carried into the new plan.
        lineage = {}; current = {f['run']['unit_key']: f for f in h['current_frames']}
        retired = plan['file_handoffs'][0]['source_unit'] if h['phase'] == 'B_COMPLETED' else None
        for ordinary in plan['units']:
            if ordinary['role'] == 'coordinate' or ordinary['key'] == retired: continue
            old = current.get(ordinary['key'])
            if old is None: continue
            ev = old['source_evidence']
            if (ev['state'] != 'COMPLETED' or not ev['exact_identity'] or
                    old['run']['input']['unit'] != ordinary):
                raise ValueError('普通成果必须保持完整原定义并准确完成，须先明确返工。')
            _require_result(ev, ordinary)
            lineage[ordinary['key']] = source_ref(old['run'], ev)
        prior, prior_event, checks = self._prior(db, plan, run, evidence)
        history = {k: deepcopy(h.get(k)) for k in HISTORY}
        event_runs = []
        for old in h['frames']:
            r = old['run']; attempt = self.s.store.attempt(r['attempt_id'], db)
            reservation = self._unique_event(db, 'cooperative_unit_reserved', r, r['task_id'], r['attempt_id'])
            result = self._unique_event(db, 'attempt_result_saved', attempt['result'], r['task_id'], r['attempt_id'])
            captures = [self._event(e) for e in db.execute(
                "SELECT * FROM events WHERE task_id=? AND attempt_id=? AND kind='cooperative_files_checked' ORDER BY seq",
                (r['task_id'], r['attempt_id']))]
            event_runs.append({'run_id': r['id'], 'reservation': reservation, 'result': result, 'captures': captures})
        historical = set(plan.get('historical_coordinators', [])) | {
            f['run']['unit_key'] for f in h['frames'] if f['run']['input']['unit']['role'] == 'coordinate'}
        historical.discard(unit['key'])
        body = versioned({'protocol': PARENT, 'item_id': item['id'], 'root': h['root'], 'plan': plan_ref(plan),
            'parent_kind': kind, 'saved_path': deepcopy(h.get('saved_path')), 'history': history,
            'source': source_ref(run, evidence), 'selected_unit': {'definition': unit, 'revision': digest(unit)},
            'prior_check': prior, 'carry_sources': lineage, 'historical_coordinators': sorted(historical),
            'usage': used, 'original_dispatch_before': auth['dispatch_before'],
            'events': {'cutoff': db.execute('SELECT COALESCE(MAX(seq),0) FROM events').fetchone()[0],
                       'runs': event_runs, 'checks': checks, 'prior_check': prior_event}, **PERMISSIONS, 'scope': PARENT_SCOPE})
        return validate_parent(body), h

    def _recheck(self, db, plan, value, h):
        from .work_coordinator_handoff_ledger import WorkCoordinatorHandoffLedger
        files = WorkCoordinatorHandoffLedger(self.s)
        for frame in h['frames']:
            if files._saved_files(db, frame['run'])[0] != frame['files']:
                raise ValueError('回合父观察末尾全部历史快照已变化。')
            context = frame['run']['input'].get('handoff_coordination_context') or frame['run']['input'].get('handoff_unit_context')
            if context: self.s.work_file_rework_applications.require_baseline(context['material_observation'])
        # This current observation may follow a newly captured A, without any
        # later input having preserved its bytes yet. Re-read the originals;
        # only frozen input observations require an immutable baseline here.
        for frame in h['current_frames']:
            if self.s.work_units.evidence(db, frame['run']) != frame['source_evidence']:
                raise ValueError('回合父观察末尾当前完整来源已变化。')
        if self.s.work_file_rework_applications.observe(plan[MARKER_FIELD]['project_root'], plan['file_handoffs'][0]['paths']) != value['history']['material_observation']:
            raise ValueError('回合父观察末尾真实材料字节已变化。')

    @closed
    def observe(self, db, plan):
        """Two independent reconstructions plus byte rechecks; never a save gate."""
        changes = db.total_changes; transaction = db.in_transaction
        value, _ = self._collect(db, plan)
        again, history = self._collect(db, plan)
        self._recheck(db, plan, again, history)
        if (value != again or db.total_changes != changes or db.in_transaction != transaction or
                self.s.teamwork.current(db, plan['item_id']) != plan or time.time() >= value['original_dispatch_before']):
            raise ValueError('回合父观察完整重建、数据库、当前方案或最初截止已变化。')
        return deepcopy(value)
