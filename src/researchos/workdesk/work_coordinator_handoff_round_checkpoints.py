"""Real round-event checkpoints and independent bounded root/adjustment history.

No fabricated adjustment checkpoint or model proposal is used. The preserving
context must enclose the future whole save, so an exception also removes only
the exact material baseline first created by that transaction.
"""
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
import json
import shutil
import time

from .store import digest, uid
from .work_read_observation import database_observation
from .work_transitions import plan_ref, source_ref
from .work_coordinator_handoff_roots import (
    PLAN_FIELDS, CONFIG_FIELDS, MARKER_FIELD, root_marker, WorkCoordinatorHandoffRoots)
from .work_coordinator_handoff_runtime import WorkCoordinatorHandoffRuntime, READ
from .work_coordinator_handoff_adjustment_runtime import (
    WorkCoordinatorHandoffAdjustmentRuntime, enabled as adjustment_enabled, PROTOCOL, UNIT)
from .work_coordinator_handoff_adjustment_sources import WorkCoordinatorHandoffAdjustmentSources
from .work_coordinator_handoff_adjustment_path import WorkCoordinatorHandoffAdjustmentPath
from .work_coordinator_handoff_adjustment_contracts import closed
from .work_coordinator_handoff_records import PERMISSIONS, versioned, DELIVERY_KIND, COMPLETION_KIND
from .work_coordinator_handoff_round_records import (
    CHECKPOINT, CANDIDATE, EVENTS, HISTORY, OPERATION, SCOPE, KIND_FOR, parent_ref, validate_parent,
    WorkCoordinatorHandoffRoundRecords)
from .work_coordinator_handoff_round_sources import WorkCoordinatorHandoffRoundSources


class WorkCoordinatorHandoffRoundCheckpoints:
    def __init__(self, service):
        self.s = service
        self.data = WorkCoordinatorHandoffRoundRecords(service)
        self.live = WorkCoordinatorHandoffRoundSources(service)

    @contextmanager
    def preserving(self, db, plan, reason):
        """Internal preparation only, scoped through the caller's entire save."""
        if not db.in_transaction: raise ValueError('回合检查点必须属于同一保存事务。')
        if not isinstance(reason, str) or not reason.strip() or len(reason) > 2000:
            raise ValueError('明确新回合须说明原因，最多2000字。')
        obs = self.live.observe(db, plan)
        baseline = obs['history']['material_observation']; files = self.s.work_file_rework_applications
        asset = files.folder(baseline).parent
        results = (self.s.locations.data / 'results').resolve()
        if asset.resolve().parent != results or asset.name != 'file-rework-base-' + baseline['version'] or asset.is_symlink():
            raise ValueError('回合材料保存目录不是准确服务资产位置。')
        existed = asset.exists()
        db.execute('SAVEPOINT round_checkpoint_assets')
        try:
            files.preserve(baseline)
            # Preservation can race external files; observe again before the
            # first event. This remains the actual current parent, not a proof.
            if self.live.observe(db, plan) != obs: raise ValueError('回合材料保存期间父观察已变化。')
            body = versioned({'protocol': CHECKPOINT, 'item_id': plan['item_id'], 'root': obs['root'],
                'parent_plan': plan_ref(plan), 'parent_path': parent_ref(obs), 'source': obs['source'],
                'selected_unit': obs['selected_unit'], 'reason': reason, 'operation': OPERATION,
                'plan': plan, 'observation': obs, **PERMISSIONS, 'scope': SCOPE})
            row = self.data.put(db, CHECKPOINT, body)
            result = self.read(db, self.data.ref(row))
            if result['current_material_observation'] != baseline or time.time() >= obs['original_dispatch_before']:
                raise ValueError('回合检查点保存末尾当前材料已变化。')
            yield deepcopy(row)
            result = self.read(db, self.data.ref(row))
            if result['current_material_observation'] != baseline or time.time() >= obs['original_dispatch_before']:
                raise ValueError('回合保存末尾当前材料已变化。')
        except BaseException:
            db.execute('ROLLBACK TO round_checkpoint_assets')
            db.execute('RELEASE round_checkpoint_assets')
            if not existed and asset.exists():
                # This exact path was checked before creation. Never enumerate
                # or delete other baseline assets, including pre-existing ones.
                if asset.resolve().parent != results or asset.is_symlink():
                    raise ValueError('失败回合材料目录发生变化，保留现场待核对。')
                shutil.rmtree(asset)
            raise
        else:
            db.execute('RELEASE round_checkpoint_assets')

    @contextmanager
    def preparing(self, db, plan, reason):
        """Prepare real candidate data; caller still owes an entire saved path."""
        with self.preserving(db, plan, reason) as checkpoint:
            cp = checkpoint['snapshot']; obs = cp['observation']
            answer = next(f for f in obs['history']['frontier'] if f['run_id'] == obs['source']['run_id'])['evidence']['answer']
            common = {k: deepcopy(cp[k]) for k in ('item_id', 'root', 'parent_plan', 'parent_path',
                'source', 'selected_unit', 'reason', 'operation', 'scope')}
            body = versioned({**common, 'protocol': CANDIDATE, 'checkpoint': self.data.ref(checkpoint),
                'child_seed': {'id': 'teamwork:' + uid(), 'created': time.time()},
                'carry_sources': obs['carry_sources'], 'historical_coordinators': obs['historical_coordinators'],
                'coordination_revision': {'id': 'coordination-revision:' + uid(), 'item_id': cp['item_id'],
                    'unit_key': obs['selected_unit']['definition']['key'], 'source': obs['source'], 'reason': reason,
                    'prior_check': obs['prior_check'], 'answer': answer if len(answer) <= 32000 else None,
                    'answer_chars': len(answer), 'answer_digest': digest(answer)},
                'transition': {'kind': OPERATION, 'source': obs['source'], 'reason': reason,
                    'usage_at_transition': obs['usage'],
                    'authorization_from': {k: obs['history']['authorization'][k] for k in ('id', 'revision')},
                    'command_id': self.s.command_identity, 'no_dispatch': True}, **PERMISSIONS})
            candidate = self.data.put(db, CANDIDATE, body)
            result = self.read_candidate(db, self.data.ref(candidate))
            yield deepcopy(result)
            if self.read_candidate(db, self.data.ref(candidate)) != result:
                raise ValueError('回合保存末尾候选完整历史或材料已变化。')

    @closed
    def read_candidate(self, db, reference):
        """Reconstruct actual immutable prepared records, without current proof."""
        from .work_coordinator_handoff_round_contracts import prepared
        with database_observation(db):
            before = db.total_changes
            candidate = self.data.get(db, CANDIDATE, reference); body = candidate['snapshot']
            checkpoint = self.data.get(db, CHECKPOINT, body['checkpoint'])
            source = self.read(db, body['checkpoint']); child = prepared(checkpoint, candidate)
            related = [r for r in self.s.collaboration.all(db, KIND_FOR[CANDIDATE]) if
                       r.get('snapshot', {}).get('source', {}).get('run_id') == body['source']['run_id']]
            event = self._event(db, EVENTS[CANDIDATE], candidate)
            if (related != [candidate] or event['seq'] <= source['checkpoint_event']['seq'] or
                    candidate['created'] < checkpoint['created']):
                raise ValueError('回合候选须为该父源唯一准确实际登记准备。')
            again = self.read(db, body['checkpoint']); self._recheck(db, again)
            if source != again or before != db.total_changes: raise ValueError('回合候选最终完整历史或材料重读已变化。')
            return deepcopy({**source, 'candidate': candidate, 'candidate_event': event,
                             'child_plan': child, 'candidate_data_verified': True})

    def _event(self, db, kind, payload, cutoff=None, task=None, attempt=None):
        rows = [r for r in db.execute('SELECT * FROM events WHERE kind=? ORDER BY seq', (kind,))
                if json.loads(r['payload']) == payload and r['task_id'] == task and r['attempt_id'] == attempt]
        if len(rows) != 1 or cutoff is not None and rows[0]['seq'] > cutoff:
            raise ValueError('回合历史缺少截止前唯一准确实际事件：' + kind)
        return self.live._event(rows[0])

    def _root(self, db, plan):
        """Validate the actual root without consulting or moving current."""
        from .work_coordinator_handoff_records import fields
        from .work_coordinator_handoff_adjustment_records import hashed, finite
        fields(plan, PLAN_FIELDS); hashed(plan); finite(plan['created'])
        actual = self.s.collaboration.get(db, 'teamwork_plan', plan['id'])
        roots = [p for p in self.s.collaboration.all(db, 'teamwork_plan')
                 if p.get('item_id') == plan['item_id'] and p.get('previous_plan') is None]
        if actual != plan or roots != [plan] or plan['state'] != 'PREPARED' or not plan['id'].startswith('teamwork:'):
            raise ValueError('回合历史须绑定唯一真实原根。')
        item = self.s.work_items.item(db, plan['item_id']); self.s.work_items.project(db, item['project_id'])
        root = self.s.work_file_rework_applications.root(db, item)
        if (item['state'] != 'OPEN' or item['project_id'] != plan['project_id'] or
                plan['work_requirements'] != {k: item[k] for k in ('title', 'goal', 'constraints_text', 'acceptance')} or
                plan[MARKER_FIELD] != root_marker(plan, root)):
            raise ValueError('回合历史原要求、项目或真实原目录已变化。')
        raw = {k: deepcopy(plan[k]) for k in CONFIG_FIELDS}
        for name in ('coordinator', 'integrator'): raw[name] = raw[name]['reference']
        for unit in raw['units']: unit['member'] = unit['member']['reference']
        if WorkCoordinatorHandoffRoots(self.s)._configuration(db, raw, Path(root)) != {k: plan[k] for k in CONFIG_FIELDS}:
            raise ValueError('回合历史完整配置或原资源已变化。')
        self.s.work_flows.require_stopped(db, item['id'])
        return item, root

    def _root_history(self, db, plan, boundary):
        item, root = self._root(db, plan)
        helper = WorkCoordinatorHandoffAdjustmentSources(self.s)
        runtime = WorkCoordinatorHandoffAdjustmentRuntime(self.s) if adjustment_enabled(self.s, db, plan) else WorkCoordinatorHandoffRuntime(self.s)
        cap, activated = runtime.capability(db, plan)
        if activated >= boundary: raise ValueError('回合检查点之前尚未启用真实原runtime。')
        if isinstance(runtime, WorkCoordinatorHandoffAdjustmentRuntime):
            runtime._adjustment, runtime._opened = runtime.adjustment_capability(db, plan)
            if runtime._opened >= boundary: raise ValueError('回合原调整能力晚于检查点。')
        driver, auth, revisions = helper._original_versions(db, plan, boundary)
        rows = [r for r in self.s.work_units.rows(db, item['id']) if r['plan_id'] == plan['id']]
        if any(helper.sources._reservation_event(db, r) >= boundary for r in rows):
            raise ValueError('回合冻结原父方案后出现额外运行。')
        records = helper._records_before(db, plan, boundary)
        frames, revision, seen = runtime._reconstruct_runs(db, plan, item, root, cap, activated, driver, auth, revisions, rows, records)
        if revision != driver['item_revision']: raise ValueError('回合原历史任务绑定未到达截止责任。')
        runtime._match_records(db, plan, frames, records)
        adjustment = None
        if isinstance(runtime, WorkCoordinatorHandoffAdjustmentRuntime):
            adjustment = runtime.ref(runtime._adjustment)
            before = [f for f in frames if runtime.sources._reservation_event(db, f['run']) < runtime._opened]
            after = [f for f in frames if runtime.sources._reservation_event(db, f['run']) > runtime._opened]
            prior = [runtime.sources._frame_before(db, f['run'], after[0]['run']) for f in before] if after else before
            eligible = sorted(u['key'] for u in plan['units'] if u['role'] == 'coordinate' and u['key'] not in {f['run']['unit_key'] for f in before})
            if (runtime._adjustment['snapshot']['activation_frontier'] != [runtime.old.frontier_entry(f) for f in prior] or
                    runtime._adjustment['snapshot']['eligible_coordinate_keys'] != eligible):
                raise ValueError('回合原调整能力的完整首次资格或激活前沿不符。')
            for f in after:
                frozen = f['run']['input']; ctx = frozen.get('handoff_coordination_context') or frozen['handoff_unit_context']
                if ctx['protocol'] != (PROTOCOL if frozen['unit']['role'] == 'coordinate' else UNIT):
                    raise ValueError('回合原调整能力后冻结输入协议不符。')
        phase = runtime._phase(plan, frames, records)
        h = {'protocol': READ, 'root': plan_ref(plan), 'marker': plan[MARKER_FIELD], 'capability': runtime.ref(cap),
            'adjustment_capability': adjustment, 'task': {'id': item['id'], 'revision': revision},
            'driver': driver, 'authorization': auth, 'phase': phase, 'frames': frames,
            'frontier': [runtime.old.frontier_entry(f) for f in frames],
            'delivery': [r for r, _ in records[DELIVERY_KIND]], 'completion': [r for r, _ in records[COMPLETION_KIND]],
            'units': [{'definition': u, 'qualification': 'HISTORICAL_RETIRED' if phase == 'B_COMPLETED' and
                u['key'] == plan['file_handoffs'][0]['source_unit'] else 'CURRENT_COMPLETED' if phase == 'B_COMPLETED' and
                u['key'] == plan['file_handoffs'][0]['target_unit'] else 'RECORDED' if u['key'] in seen else 'NOT_RUN',
                **PERMISSIONS} for u in plan['units']], **PERMISSIONS}
        return h

    def _checks(self, db, plan, h, cutoff):
        """Rebuild all old checks against real runs, inputs, specs and events."""
        from .work_proposals import parse
        from .work_coordinator_handoff_adjustment_records import SOURCE_FIELDS
        from .work_coordinator_handoff_adjustments import proposal_state
        helper = WorkCoordinatorHandoffAdjustmentPath(self.s)
        root = self.s.collaboration.get(db, 'teamwork_plan', h['root']['id'])
        _, activated = helper.capability(db, root)
        pairs = [(r, json.loads(r['payload'])) for r in db.execute(
            "SELECT * FROM events WHERE kind='coordinator_proposal_checked' ORDER BY seq")
            if json.loads(r['payload']).get('item_id') == plan['item_id']]
        saved = [r for r in self.s.collaboration.all(db, 'work_proposal') if r.get('item_id') == plan['item_id']]
        if sorted(map(digest, saved)) != sorted(digest(v) for _, v in pairs):
            raise ValueError('回合历史全部检查与实际事件存在遗漏或孤立版本。')
        checks = []; rows = {}
        for event, row in pairs:
            if event['seq'] > cutoff: continue
            body = row['snapshot']
            if (set(row) != {'id', 'version', 'snapshot', 'item_id', 'created', 'command_id'} or
                    row['version'] != digest(body) or row['id'] in rows):
                raise ValueError('回合历史原检查完整wrapper、唯一性或摘要无效。')
            frame = next((f for f in h['frames'] if f['run']['id'] == body['source']['run_id']), None)
            if not frame or frame['run']['input']['unit']['role'] != 'coordinate': raise ValueError('回合旧检查缺少真实协调来源。')
            run = frame['run']; source_plan = self.s.collaboration.get(db, 'teamwork_plan', run['plan_id'])
            driver = helper.old._driver(db, source_plan, body['driver']); auth = helper.old._auth(db, source_plan, body['authorization'])
            known = [f for f in h['frames'] if helper.sources._reservation_event(db, f['run']) < event['seq']]
            full = frame['source_evidence']; frozen = run['input']
            if (event['task_id'] != run['task_id'] or event['attempt_id'] != run['attempt_id'] or
                    body['plan'] != plan_ref(source_plan) or body['item_revision'] != driver['item_revision'] or
                    helper._driver_event(db, driver) >= event['seq'] or
                    helper.sources._reservation_event(db, run) >= event['seq'] or
                    body['source'] != {k: full[k] for k in SOURCE_FIELDS} or body['answer_digest'] != digest(full['answer']) or
                    body['answer'] != (full['answer'] if len(full['answer']) <= 32000 else None) or
                    body['handoff_context'] != frozen['coordination_context']['handoff_context'] or
                    body['frontier'] != digest([{'run_id': f['run']['id'], 'evidence': f['evidence' if event['seq'] > activated else 'source_evidence']['version']} for f in known]) or
                    not known or [f for f in known if f['run']['input']['unit']['role'] == 'coordinate'][-1]['run']['id'] != run['id'] or
                    driver['generation'] != run['generation'] or auth['driver_generation'] != driver['generation']):
                raise ValueError('回合旧检查来源、完整前沿、原回复或原责任授权不符。')
            if body['state'] != 'INVALID':
                decision = parse(full['answer'])
                if decision != body['decision']: raise ValueError('回合旧检查决定不是准确原回复。')
                if decision['action'] == 'dispatch':
                    target = next((v for v in frozen['coordination_context']['candidates'] if v['unit']['key'] == decision['unit_key']), None)
                    if (body['state'] != 'VALID' or not target or not target['ready_after_proposal'] or decision['retry_of'] is not None or
                            body['target'] != {'key': target['unit']['key'], 'revision': digest(target['unit'])}):
                        raise ValueError('回合旧派发提案与冻结可达候选不符。')
                elif decision['action'] == 'pause':
                    if body['state'] != 'PAUSE_SUGGESTED' or body['target'] is not None: raise ValueError('回合旧暂停提案身份无效。')
                elif decision['action'] == 'adjust_dependencies':
                    if not proposal_state(body) or body['target'] is not None: raise ValueError('回合旧调整提案身份无效。')
                else: raise ValueError('回合原输入未开放该决定。')
            rows[row['id']] = row
            checks.append({'reference': self.s.work_proposals.reference(row), 'state': body['state'], 'event': self.live._event(event)})
        return checks, rows

    def _driver_history(self, db, plan, cutoff):
        """Every pre-cutoff immutable driver version must have its real event."""
        helper = WorkCoordinatorHandoffAdjustmentPath(self.s)
        if plan.get('coordinator_file_handoff_lineage'):
            root, chain = helper._chain(db, plan); plans = [root] + chain
        else: plans = [plan]
        allowed = {p['id'] for p in plans}; prior = []
        for row in self.s.collaboration.all(db, 'work_driver_revision'):
            if row.get('item_id') != plan['item_id']: continue
            original = self.s.collaboration.get(db, 'teamwork_plan', row['plan_id'])
            value = helper.old._driver(db, original, {'id': row['driver_id'], 'generation': row['generation'], 'revision': row['revision']})
            seq = helper._driver_event(db, value)
            if seq > cutoff: continue
            if row['plan_id'] not in allowed: raise ValueError('回合截止前存在未重建方案的责任版本。')
            prior.append((seq, value, row))
        events = [json.loads(r['payload']) for r in db.execute(
            "SELECT seq,payload FROM events WHERE kind LIKE 'work_driver_%' ORDER BY seq")
            if r['seq'] <= cutoff and json.loads(r['payload']).get('item_id') == plan['item_id']]
        if sorted(map(digest, events)) != sorted(digest({**v, 'no_dispatch': True}) for _, v, _ in prior):
            raise ValueError('回合截止前全部责任事件或不可变版本存在遗漏。')
        for p in plans:
            values = sorted([v for v in prior if v[1]['plan_id'] == p['id']], key=lambda v: v[0])
            if not values: raise ValueError('回合父路径缺少准确原责任历史。')
            helper._driver_transitions(db, values)

    def _once(self, db, reference):
        row = self.data.get(db, CHECKPOINT, reference); cp = row['snapshot']; frozen = cp['observation']; plan = cp['plan']
        event = self._event(db, EVENTS[CHECKPOINT], row); boundary = event['seq']; cutoff = frozen['events']['cutoff']
        if boundary != cutoff + 1 or row['created'] >= frozen['original_dispatch_before']:
            raise ValueError('回合检查点须为完整父观察后的准确首次事件且未过最初截止。')
        if self.s.collaboration.get(db, 'teamwork_plan', plan['id']) != plan:
            raise ValueError('回合父方案完整实际记录已变化。')
        if frozen['parent_kind'] == 'ROOT': h = self._root_history(db, plan, boundary)
        else:
            # _walk is the actual adjustment chain reconstruction component;
            # boundary is this real ROUND event. No adjustment data is forged.
            root, _ = WorkCoordinatorHandoffAdjustmentPath(self.s)._chain(db, plan)
            self._root(db, root)
            h = WorkCoordinatorHandoffAdjustmentPath(self.s)._walk(db, plan, boundary=boundary)
        self._driver_history(db, plan, cutoff)
        baseline = frozen['history']['material_observation']
        self.s.work_file_rework_applications.require_baseline(baseline)
        h['material_observation'] = deepcopy(baseline)
        if h.get('saved_path'):
            h['saved_path'] = versioned({k: deepcopy(v) for k, v in h['saved_path'].items() if k != 'version'} |
                                        {'material_observation': deepcopy(baseline)})
        checks, check_rows = self._checks(db, plan, h, cutoff)
        candidates = [f for f in h['frames'] if f['run']['plan_id'] == plan['id'] and
                      f['run']['generation'] == h['driver']['generation'] and f['run']['input']['unit']['role'] == 'coordinate']
        if not candidates: raise ValueError('回合历史缺少当前父方案最新真实协调来源。')
        selected = candidates[-1]; run = selected['run']; full = selected['source_evidence']
        if (full['state'] != 'COMPLETED' or not full['exact_identity'] or not full['result_available'] or
                not isinstance(full['answer'], str) or not full['answer']): raise ValueError('回合历史源不是准确完成的文字结果。')
        event_runs = []
        for f in h['frames']:
            r = f['run']; attempt = self.s.store.attempt(r['attempt_id'], db)
            event_runs.append({'run_id': r['id'],
                'reservation': self._event(db, 'cooperative_unit_reserved', r, cutoff, r['task_id'], r['attempt_id']),
                'result': self._event(db, 'attempt_result_saved', attempt['result'], cutoff, r['task_id'], r['attempt_id']),
                'captures': [self.live._event(e) for e in db.execute(
                    "SELECT * FROM events WHERE task_id=? AND attempt_id=? AND kind='cooperative_files_checked' ORDER BY seq",
                    (r['task_id'], r['attempt_id']))]})
        prior = None; prior_event = None
        if checks:
            latest = check_rows[checks[-1]['reference']['id']]; b = latest['snapshot']
            if b['source']['run_id'] == run['id']:
                if b['source']['version'] != full['version'] or b['plan'] != plan_ref(plan): raise ValueError('回合历史同版本检查不符。')
                prior = {'reference': checks[-1]['reference'], 'state': b['state'], 'problems': b['problems']}
                prior_event = checks[-1]['event']
        if frozen['parent_kind'] == 'ROOT': current = h['frames']
        else: current = h['current_frames']
        carry = {}; retired = plan['file_handoffs'][0]['source_unit'] if h['phase'] == 'B_COMPLETED' else None
        from .work_coordinator_handoff_adjustments import _require_result
        for unit in plan['units']:
            if unit['role'] == 'coordinate' or unit['key'] == retired: continue
            frame = next((f for f in reversed(current) if f['run']['unit_key'] == unit['key']), None)
            if frame is None: continue
            if frame['run']['input']['unit'] != unit or frame['source_evidence']['state'] != 'COMPLETED' or not frame['source_evidence']['exact_identity']:
                raise ValueError('回合历史普通成果不是原定义准确完成来源。')
            _require_result(frame['source_evidence'], unit)
            carry[unit['key']] = source_ref(frame['run'], frame['source_evidence'])
        historical = set(plan.get('historical_coordinators', [])) | {
            f['run']['unit_key'] for f in h['frames'] if f['run']['input']['unit']['role'] == 'coordinate'}
        historical.discard(run['unit_key'])
        reconstructed = versioned({k: deepcopy(v) for k, v in frozen.items() if k not in {
            'version', 'history', 'saved_path', 'source', 'selected_unit', 'prior_check', 'carry_sources',
            'historical_coordinators', 'usage', 'original_dispatch_before', 'events'}} | {
                'history': {k: deepcopy(h.get(k)) for k in HISTORY}, 'saved_path': deepcopy(h.get('saved_path')),
                'source': source_ref(run, full), 'selected_unit': {'definition': run['input']['unit'], 'revision': run['unit_revision']},
                'prior_check': prior, 'carry_sources': carry, 'historical_coordinators': sorted(historical),
                'usage': self.s.work_units.usage(db, [f['run'] for f in h['frames']]),
                'original_dispatch_before': h['authorization']['dispatch_before'],
                'events': {'cutoff': cutoff, 'runs': event_runs, 'checks': checks, 'prior_check': prior_event}})
        validate_parent(reconstructed)
        if reconstructed != frozen: raise ValueError('回合检查点不等于按真实事件截止逐项重建的完整父历史。')
        current_observation = self.s.work_file_rework_applications.observe(plan[MARKER_FIELD]['project_root'], plan['file_handoffs'][0]['paths'])
        return {'checkpoint': row, 'checkpoint_event': event, 'observation': reconstructed, 'frames': h['frames'],
                'current_material_observation': current_observation, 'saved_edge_verified': False,
                'public_apply': False, **PERMISSIONS}

    def _recheck(self, db, result):
        files = self.s.work_file_rework_applications
        from .work_coordinator_handoff_ledger import WorkCoordinatorHandoffLedger
        ledger = WorkCoordinatorHandoffLedger(self.s)
        for f in result['frames']:
            if ledger._saved_files(db, f['run'])[0] != f['files']: raise ValueError('回合历史末尾全部旧快照已变化。')
            frozen = f['run']['input']; ctx = frozen.get('handoff_coordination_context') or frozen.get('handoff_unit_context')
            if ctx: files.require_baseline(ctx['material_observation'])
        files.require_baseline(result['observation']['history']['material_observation'])
        current = result['current_material_observation']
        if files.observe(current['root'], current['paths']) != current: raise ValueError('回合历史末尾当前真实文件已变化。')

    @closed
    def read(self, db, reference):
        with database_observation(db):
            before = db.total_changes
            row = self.data.get(db, CHECKPOINT, reference)
            pointer = deepcopy(self.s.teamwork.current(db, row['item_id']))
            first = self._once(db, reference); again = self._once(db, reference)
            self._recheck(db, again)
            if first != again or before != db.total_changes or self.s.teamwork.current(db, row['item_id']) != pointer:
                raise ValueError('回合截止历史完整重读、数据库或当前指针已变化。')
            return deepcopy(again)
