"""Full saved-tree audit through an actual post-ROUND PAUSED transfer.

This version accepts the real first/mixed ROUND ancestry and no successor runs.
Each complete read reconstructs twice, then rechecks all bytes and the database.
"""
from copy import deepcopy
import json

from .store import digest
from .work_read_observation import database_observation
from .work_transitions import plan_ref
from .work_coordinator_handoff_records import PERMISSIONS, versioned
from .work_coordinator_handoff_adjustment_contracts import closed
from .work_coordinator_handoff_mixed_runtime import WorkCoordinatorHandoffMixedRuntime
from .work_coordinator_handoff_post_round_candidates import LINEAGE_FIELD, ref, CHECKPOINT, CANDIDATE
from .work_coordinator_handoff_post_round_preparation_history import WorkCoordinatorHandoffPostRoundPreparationHistory
from .work_coordinator_handoff_post_round_saved_records import (
    SavedRecords, APPLICATION, REGISTRATION, SAVE_EVENT, EVENTS, KIND_FOR, successor_plan,
    adjacent, successor_driver, transferred_authorization, bodies, save_payload, REQUEST_FIELDS)

READ = 'COORDINATOR_FILE_HANDOFF_POST_ROUND_SAVED_HISTORY_V1'
PATH = 'COORDINATOR_FILE_HANDOFF_POST_ROUND_SAVED_PATH_V1'


class WorkCoordinatorHandoffPostRoundSavedTree(WorkCoordinatorHandoffMixedRuntime):
    def __init__(self, service):
        super().__init__(service)
        self.data = SavedRecords(service)
        self.preparation = WorkCoordinatorHandoffPostRoundPreparationHistory(service)

    def _base_saved(self, db, plan):
        # The guard follows actual previous_plan_reference and retains the
        # original authorization ceiling and cycle checks before codec dispatch.
        self.checkpoints._guard(db, plan)
        candidates = [r for r in self.s.collaboration.all(db, KIND_FOR[CANDIDATE])
            if r.get('snapshot', {}).get('child_plan') == plan]
        if len(candidates) != 1: raise ValueError('新保存须有唯一准确完整child候选。')
        candidate = candidates[0]
        cp = self.preparation._get(db, CHECKPOINT, candidate['snapshot']['checkpoint'])
        parent = cp['snapshot']['plan']
        if parent.get(LINEAGE_FIELD):
            raise ValueError('本保存阶段尚无post-round父新运行，不能套用旧ROUND父codec。')
        ready = self.preparation._from_history(db, ref(candidate))
        parent_reader = self.preparation._parent_reader
        successor_plan(plan, ready['candidate'])
        if self.s.collaboration.get(db, 'teamwork_plan', plan['id']) != plan:
            raise ValueError('新保存方案完整实际记录已变化。')
        self._parent_reader = parent_reader; self._parent_plan = parent
        self._parents = parent_reader._parents + [parent]
        self._layers = getattr(parent_reader, '_layers', []) + [parent_reader]
        self._root_plan = self._parents[0]
        self._plan_ids = {p['id'] for p in self._parents + [plan]}
        self._identity_ids = set(self._plan_ids) | {p['version'] for p in self._parents + [plan]}
        self._task_ids = set(); self._attempt_ids = set(); self._frame_identities(ready['frames'])
        self._identity_ids.update(ready['checkpoint']['snapshot']['history']['driver'][k] for k in ('id', 'generation', 'revision'))
        self._identity_ids.update(ready['checkpoint']['snapshot']['history']['authorization'][k] for k in ('id', 'revision'))
        apps = [r for r in self._related(db, KIND_FOR[APPLICATION], plan['item_id']) if r['snapshot']['child_plan'] == plan_ref(plan)]
        regs = [r for r in self._related(db, KIND_FOR[REGISTRATION], plan['item_id']) if r['snapshot']['to_plan'] == plan_ref(plan)]
        if len(apps) != 1 or len(regs) != 1: raise ValueError('新保存缺少唯一实际应用与登记。')
        app = self.data.get(db, APPLICATION, ref(apps[0])); reg = self.data.get(db, REGISTRATION, ref(regs[0]))
        b = app['snapshot']; h = ready['observation']['history']
        after = self.old._driver(db, plan, b['driver_after']); auth = self.old._auth(db, plan, b['authorization_after'])
        adjacent(ready['checkpoint'], candidate, plan, h['driver'], after, h['authorization'], auth)
        request = b['request']
        # Recompute all seven values from actual parent facts, not just a stored
        # self-consistent request hash. The frontier contract is run/evidence based.
        expected_request = {'id': plan['item_id'], 'revision': h['task']['revision'],
            'current_driver': self.s.work_drivers.reference(h['driver']), 'plan': plan_ref(parent),
            'authorization': self.s.work_units.auth_ref(h['authorization']),
            'frontier': self.s.work_units.frontier(db, plan['item_id']), 'proposal': candidate['snapshot']['proposal']}
        if request != expected_request: raise ValueError('新保存七字段不是准确真实父版本。')
        save = self.preparation._event(db, SAVE_EVENT, save_payload(self.s, candidate, plan, after, auth))
        ae = self.preparation._event(db, EVENTS[APPLICATION], app); re = self.preparation._event(db, EVENTS[REGISTRATION], reg)
        if (bodies(self.s, ready['checkpoint'], candidate, plan, h['driver'], after, h['authorization'], auth, save, request)
                != (b, reg['snapshot']) or
                not ready['candidate_event']['seq'] < self._driver_event(db, after) < save['seq'] < ae['seq'] < re['seq'] or
                any(r['command_id'] != candidate['command_id'] for r in (app, reg)) or
                not after['updated'] <= app['created'] <= reg['created'] < auth['dispatch_before']):
            raise ValueError('新保存实际事件、完整登记、命令或最初截止不符。')
        self._ready = ready; self._auth = auth; self._round = None
        self._edge_value = {'parent_plan': plan_ref(parent), 'child_plan': plan_ref(plan),
            'checkpoint': ref(ready['checkpoint']), 'candidate': ref(candidate), 'application': ref(app),
            'registration': ref(reg), 'save_event': save, 'registration_event': re}
        self._application = app; self._registration = reg
        return ready, after, auth

    def _check_proposals(self, db, plan, h):
        # Exact actual prefix was reconstructed semantically by the parent. A
        # separate full namespace comparison prevents that prefix hiding extras.
        expected = self._parent_reader._proposal_events(db, plan['item_id'])
        actual = self._proposal_events(db, plan['item_id'])
        if actual != expected: raise ValueError('新保存后存在额外或迟到的父提案检查。')
        self._same(self._related(db, 'work_proposal', plan['item_id']), [v for _, v in expected],
            '新保存全部提案记录与重建前缀不符。')

    def _command(self, db, result):
        from .work_coordinator_handoff_post_round_apply import COMMAND, pending, receipt
        app = self._application; request = app['snapshot']['request']
        command = db.execute('SELECT * FROM commands WHERE id=?', (app['command_id'],)).fetchone()
        if command is None:
            if not pending(self.s, db, app['command_id'], request):
                raise ValueError('新保存缺少准确真实已提交命令。')
        elif (command['kind'] != COMMAND or command['payload_hash'] != digest(request) or
                json.loads(command['receipt']) != receipt({**result, 'driver': self._versions[0][1]}) or command['created'] < self._registration['created']):
            raise ValueError('新保存已提交命令种类、请求摘要或完整回执不符。')

    def _once(self, db, plan):
        ready, start, auth = self._base_saved(db, plan)
        self._versions = []
        for row in self._related(db, 'work_driver_revision', plan['item_id']):
            if row['plan_id'] != plan['id']: continue
            v = self.old._driver(db, plan, {'id': row['driver_id'], 'generation': row['generation'], 'revision': row['revision']})
            successor_driver(v); self._versions.append((self._driver_event(db, v), v, row))
        self._versions.sort(key=lambda v: v[0])
        if not self._versions or self._versions[0][1] != start:
            raise ValueError('新保存责任缺少准确暂停起点。')
        self._driver_transitions(db, self._versions)
        driver = self._versions[-1][1]
        if any(r['plan_id'] == plan['id'] for r in self.s.work_units.rows(db, plan['item_id'])):
            raise ValueError('新保存阶段尚未开放后继运行。')
        ph = ready['observation']['history']; frames = ready['frames']
        material = ready['current_material_observation']
        current = deepcopy(frames)
        for f in current: f['source_evidence'] = self.s.work_units.evidence(db, f['run'])
        eligible = self._eligible(plan, current)
        phase = ph['phase']
        relation = plan['file_handoffs'][0]
        key = relation['target_unit'] if phase == 'B_COMPLETED' else relation['source_unit'] if phase == 'A_CAPTURED' else None
        if key and next(f for f in reversed(current) if f['run']['unit_key'] == key)['source_evidence']['file_artifacts']['state'] != 'CURRENT':
            phase = 'BLOCKED'
        from .work_coordinator_handoff_adjustments import qualifications
        result = {**deepcopy(ph), 'protocol': READ, 'task': {'id': plan['item_id'], 'revision': driver['item_revision']},
            'driver': driver, 'authorization': auth, 'frames': frames, 'current_frames': eligible,
            'phase': phase, 'material_observation': material, 'usage': self.s.work_units.usage(db, [f['run'] for f in frames]),
            'units': [{'definition': u, 'qualification': q['qualification'], **PERMISSIONS}
                for u, q in zip(plan['units'], qualifications(plan, phase, eligible))],
            'application': self._application, 'registration': self._registration, 'child_plan': plan,
            'saved_edge_verified': True, 'full_saved_tree_verified': True,
            'preparation_namespace_verified': True, 'successor_runtime_open': False, 'public_apply': False, **PERMISSIONS}
        result['saved_path'] = versioned({'protocol': PATH, 'root': ph['root'], 'plan': plan_ref(plan),
            'parent_path': ph['saved_path'], 'edge': self._edge_value,
            'history_frontier': ph['frontier'], 'driver': driver, 'authorization': auth,
            'material_observation': material, **PERMISSIONS})
        from .work_coordinator_handoff_post_round_saved_audit import audit
        audit(self, db, plan, result)
        self._command(db, result)
        return deepcopy(result)

    @closed
    def read(self, db, plan):
        if not db.in_transaction: raise ValueError('新保存树读取须属于明确事务。')
        with database_observation(db):
            changes = db.total_changes
            first = self._once(db, plan); again = self._once(db, plan)
            for f in again['current_frames']:
                if self.s.work_units.evidence(db, f['run']) != f['source_evidence']:
                    raise ValueError('新保存沿用来源末尾变化。')
            self.checkpoints._recheck(db, {'frames': again['frames'], 'observation': self._ready['observation'],
                'current_material_observation': again['material_observation']})
            for layer in self._layers:
                self.s.work_file_rework_applications.require_baseline(layer._ready['observation']['history']['material_observation'])
            comparable = lambda v: {**v, 'usage': {k: x for k, x in v['usage'].items() if k != 'elapsed_observed_seconds'}}
            if comparable(first) != comparable(again) or changes != db.total_changes:
                raise ValueError('新保存树双遍完整重建、末尾文件或数据库发生变化。')
            return deepcopy(again)

    def input_for(self, *args, **kwargs):
        raise ValueError('新保存后继运行须接独立V7/普通V6合同；当前保持暂停。')

    def activate(self, *args, **kwargs):
        raise ValueError('新保存后继运行合同尚未开放。')

    def activate_round(self, *args, **kwargs):
        raise ValueError('新保存方案须接独立后继能力，不可使用旧ROUND激活。')
