"""Full saved-tree audit through an actual post-ROUND PAUSED transfer.

V2 reconstructs the first/mixed ROUND and first POST/V7 ancestors, then the second saved edge.
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
from .work_coordinator_handoff_post_round_candidates import LINEAGE_FIELD, ref
from .work_coordinator_handoff_post_round_v2_candidates import CHECKPOINT, CANDIDATE
from .work_coordinator_handoff_post_round_v2_candidates import WorkCoordinatorHandoffPostRoundV2Candidates
from .work_coordinator_handoff_post_round_v2_saved_records import (
    SavedRecords, APPLICATION, REGISTRATION, SAVE_EVENT, EVENTS, KIND_FOR, successor_plan,
    adjacent, successor_driver, transferred_authorization, bodies, save_payload, REQUEST_FIELDS)

READ = 'COORDINATOR_FILE_HANDOFF_POST_ROUND_SAVED_HISTORY_V2'
PATH = 'COORDINATOR_FILE_HANDOFF_POST_ROUND_SAVED_PATH_V2'


class _SavedPreparation(WorkCoordinatorHandoffPostRoundV2Candidates):
    """Private bounded reconstruction; the owning full audit checks all namespaces."""
    def _namespace(self, db, facts, checkpoint, candidate):
        pass

    def _once(self, db, identity):
        value = super()._once(db, identity)
        value['preparation_namespace_verified'] = False
        return value


class WorkCoordinatorHandoffPostRoundV2SavedTree(WorkCoordinatorHandoffMixedRuntime):
    def __init__(self, service):
        super().__init__(service)
        self.data = SavedRecords(service)
        self.preparation = _SavedPreparation(service)

    def _base_saved(self, db, plan):
        # The guard follows actual previous_plan_reference and retains the
        # original authorization ceiling and cycle checks before codec dispatch.
        self._collected = self._collect(db, plan)
        candidates = [r for r in self.s.collaboration.all(db, KIND_FOR[CANDIDATE])
            if r.get('snapshot', {}).get('child_plan') == plan]
        if len(candidates) != 1: raise ValueError('新保存须有唯一准确完整child候选。')
        candidate = candidates[0]
        cp = self.preparation._get(db, CHECKPOINT, candidate['snapshot']['checkpoint'])
        parent = cp['snapshot']['plan']
        from .work_coordinator_handoff_post_round_candidates import LINEAGE as FIRST_POST
        if parent.get(LINEAGE_FIELD, {}).get('protocol') != FIRST_POST:
            raise ValueError('V2保存当前仅接受真实首POST保存后V7父。')
        ready = self.preparation.read(db, ref(candidate))
        parent_reader = self.preparation._parent_reader
        successor_plan(plan, ready['candidate'])
        if self.s.collaboration.get(db, 'teamwork_plan', plan['id']) != plan:
            raise ValueError('新保存方案完整实际记录已变化。')
        self._parent_reader = parent_reader; self._parent_plan = parent
        self._parents = parent_reader._parents + [parent]
        self._layers = getattr(parent_reader, '_layers', []) + [parent_reader]
        if self._collected != self._parents + [plan]:
            raise ValueError('V2有序祖先与实际逐层重建不符。')
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
            'frontier': digest([{'run_id': f['run']['id'], 'evidence': f['evidence']['version']} for f in ready['frames']]), 'proposal': candidate['snapshot']['proposal']}
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
        from .work_coordinator_handoff_post_round_v2_apply import COMMAND, pending, receipt
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
        result['saved_path'] = self._ordered_path(db, plan, result)
        from .work_coordinator_handoff_post_round_v2_saved_audit import audit
        audit(self, db, plan, result)
        self._command(db, result)
        return deepcopy(result)

    @closed
    def read(self, db, plan):
        if not db.in_transaction: raise ValueError('新保存树读取须属于明确事务。')
        with database_observation(db):
            resources = self._resources(db, plan)
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
            if comparable(first) != comparable(again) or changes != db.total_changes or self._resources(db, plan) != resources:
                raise ValueError('新保存树双遍完整重建、末尾文件或数据库发生变化。')
            return deepcopy(again)

    def _resources(self, db, plan):
        return {u['key']: {k: v for k, v in self.s.resources.selection(db,
            u['member']['reference']['member_id']).items() if k != 'selected_at'} for u in plan['units']}

    def _collect(self, db, plan):
        ceiling = self.checkpoints._guard(db, plan)
        chain = []; seen = set(); current = plan
        while True:
            if (current['id'] in seen or len(chain) > ceiling or
                    self.s.collaboration.get(db, 'teamwork_plan', current['id']) != current):
                raise ValueError('V2祖先须有限、无循环且等于完整实际方案。')
            seen.add(current['id']); chain.append(current)
            identity = current.get('previous_plan_reference')
            if identity is None: break
            current = self.s.collaboration.get(db, 'teamwork_plan', identity['id'])
            if plan_ref(current) != identity: raise ValueError('V2祖先准确引用不符。')
        return list(reversed(chain))

    def _ordered_path(self, db, plan, h):
        # Each ancestor and edge occurs once. Frontiers and material are shared
        # by explicit field references, never recursively copied parent proofs.
        from .work_coordinator_handoff_post_round_v2_path import validate_path, PATH
        layers = self._layers + [self]
        old = layers[0]._ready['observation']['saved_path']
        edges = list(old['edges'] if old else []) + [l._edge_value for l in layers]
        entries = []
        for p in self._collected:
            e = next((v for v in edges if v.get('child_plan', v.get('to_plan')) == plan_ref(p)), None)
            layer = next((l for l in layers if l._edge_value['child_plan'] == plan_ref(p)), None)
            cutoff = (layer._ready['checkpoint']['snapshot'].get('cutoff',
                layer._ready['checkpoint_event']['seq'] - 1) if layer else None)
            if e and cutoff is None:
                rows = list(db.execute('SELECT kind,data FROM collaboration WHERE id=?', (e['checkpoint']['id'],)))
                if len(rows) != 1: raise ValueError('有序祖先检查点身份不唯一。')
                value = json.loads(rows[0]['data'])
                found = [r for r in db.execute('SELECT * FROM events') if json.loads(r['payload']) == value]
                if len(found) != 1: raise ValueError('有序祖先检查点事件不唯一。')
                cutoff = found[0]['seq'] - 1
            from .work_coordinator_handoff_post_round_v2_path import plan_kind
            entries.append({'plan': plan_ref(p), 'kind': plan_kind(p),
                'edge': deepcopy(e), 'checkpoint_cutoff': cutoff})
        return validate_path(versioned({'protocol': PATH, 'root': h['root'], 'plan': plan_ref(plan),
            'ancestors': entries, 'history_frontier': h['frontier'], 'driver': h['driver'],
            'authorization': h['authorization'], 'material_observation': h['material_observation'],
            'scope': 'ORDERED_SAVED_V2; NO_RUNTIME_INPUT_PERMISSION', **PERMISSIONS}))

    def input_for(self, *args, **kwargs):
        raise ValueError('新保存后继运行须接独立V7/普通V6合同；当前保持暂停。')

    def activate(self, *args, **kwargs):
        raise ValueError('新保存后继运行合同尚未开放。')

    def activate_round(self, *args, **kwargs):
        raise ValueError('新保存方案须接独立后继能力，不可使用旧ROUND激活。')
