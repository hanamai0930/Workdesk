"""Actual first ROUND edge over ROOT/ADJUSTMENT history, before child runs.

This read-only saved proof grants no input or dispatch permission. Additional
ROUND ancestry and new child run protocols require their own reconstruction.
"""
from copy import deepcopy
import json

from .store import digest
from .work_read_observation import database_observation
from .work_transitions import plan_ref
from .work_coordinator_handoff_records import (
    PERMISSIONS, versioned, fields, reference, sha, DELIVERY_KIND, COMPLETION_KIND)
from .work_coordinator_handoff_adjustment_contracts import closed
from .work_coordinator_handoff_round_contracts import adjacent, successor_plan
from .work_coordinator_handoff_round_records import (
    CHECKPOINT, CANDIDATE, APPLICATION, REGISTRATION, SAVED_PATH, SAVE_EVENT,
    KIND_FOR, EVENTS, event_ref, record_ref, WorkCoordinatorHandoffRoundRecords)
from .work_coordinator_handoff_round_checkpoints import WorkCoordinatorHandoffRoundCheckpoints
from .work_coordinator_handoff_adjustment_path import WorkCoordinatorHandoffAdjustmentPath


PATH_SCOPE = 'FIRST_ROUND_SAVED_EDGE_WITHOUT_SUCCESSOR_RUNS; NO_INPUT_OR_EXECUTION_PERMISSION'
PATH_FIELDS = {'protocol', 'root', 'plan', 'parent_kind', 'parent_path', 'edge', 'history_frontier',
    'material_observation', 'usage', 'original_dispatch_before', 'scope', 'version', *PERMISSIONS}


@closed
def validate_path(value):
    from .work_coordinator_handoff_adjustment_records import (
        hashed, permissions, complete_frontier, usage, finite, validate_path as adjustment_path)
    from .work_coordinator_handoff_records import observation
    fields(value, PATH_FIELDS); hashed(value); permissions(value)
    if value['protocol'] != SAVED_PATH or value['scope'] != PATH_SCOPE:
        raise ValueError('回合首边路径须为准确独立闭合协议。')
    for k in ('root', 'plan'): reference(value[k])
    complete_frontier(value['history_frontier']); observation(value['material_observation'])
    usage(value['usage']); finite(value['original_dispatch_before'])
    if value['parent_kind'] == 'ROOT':
        if value['parent_path'] is not None: raise ValueError('回合原根父不得附带保存路径。')
    elif value['parent_kind'] == 'ADJUSTMENT':
        adjustment_path(value['parent_path'])
        if value['parent_path']['root'] != value['root']: raise ValueError('回合旧调整父根不符。')
    else: raise ValueError('本版首边路径尚未开放ROUND父或后继运行。')
    edge = value['edge']
    fields(edge, {'parent_plan', 'child_plan', 'checkpoint', 'candidate', 'application', 'registration', 'save_event'})
    for k in ('parent_plan', 'child_plan'): reference(edge[k])
    for k, protocol in (('checkpoint', CHECKPOINT), ('candidate', CANDIDATE),
            ('application', APPLICATION), ('registration', REGISTRATION)): record_ref(edge[k], protocol)
    event_ref(edge['save_event'], SAVE_EVENT)
    if (edge['child_plan'] != value['plan'] or edge['parent_plan'] == value['plan'] or
            edge['parent_plan'] != (value['root'] if value['parent_kind'] == 'ROOT' else value['parent_path']['plan'])):
        raise ValueError('回合首边须绑定准确根、父、子保存身份。')
    return deepcopy(value)


class WorkCoordinatorHandoffRoundPath:
    def __init__(self, service):
        self.s = service
        self.checkpoints = WorkCoordinatorHandoffRoundCheckpoints(service)
        self.data = self.checkpoints.data

    def _related(self, db, kind, item_id, plan_ids):
        return [v for v in self.s.collaboration.all(db, kind) if
            v.get('item_id') == item_id or v.get('plan_id') in plan_ids or
            v.get('snapshot', {}).get('item_id') == item_id or
            v.get('root', {}).get('id') in plan_ids or
            v.get('snapshot', {}).get('root', {}).get('id') in plan_ids]

    def _events(self, db, kind, item_id, plan_ids):
        rows = []
        for e in db.execute('SELECT * FROM events WHERE kind=? ORDER BY seq', (kind,)):
            v = json.loads(e['payload'])
            if (v.get('item_id') == item_id or v.get('plan_id') in plan_ids or
                    v.get('root', {}).get('id') in plan_ids or
                    v.get('snapshot', {}).get('item_id') == item_id or
                    v.get('snapshot', {}).get('root', {}).get('id') in plan_ids):
                if (kind.startswith(('work_driver_', 'coordinator_handoff_round_', 'coordinator_handoff_adjustment_')) or
                        kind in {'coordinator_runtime_opened', 'coordinator_adjustment_opened', 'cooperation_authorized'}) and (
                        e['task_id'] is not None or e['attempt_id'] is not None):
                    raise ValueError('保存记录事件不能伪装成运行事件。')
                rows.append((e, v))
        return rows

    @staticmethod
    def _same(actual, expected, message):
        if sorted(map(digest, actual)) != sorted(map(digest, expected)): raise ValueError(message)

    def _scope(self, db, plan, ready, application, registration, after, auth, save, parent_path):
        """Account for every plan/run/auth/driver and saved record in this scope."""
        cp = ready['checkpoint']; obs = ready['observation']; parent = cp['snapshot']['plan']
        old = WorkCoordinatorHandoffAdjustmentPath(self.s)
        if obs['parent_kind'] == 'ROOT': parents = [parent]
        else:
            root, chain = old._chain(db, parent); parents = [root] + chain
        plans = parents + [plan]; ids = {p['id'] for p in plans}; item = plan['item_id']
        related = lambda kind: self._related(db, kind, item, ids)
        events = lambda kind: self._events(db, kind, item, ids)
        self._same(related('teamwork_plan'), plans, '回合首边存在未重建方案或其它保存层。')
        self._same(related('work_unit_run'), [f['run'] for f in ready['frames']], '回合首边尚未开放后继运行或未知运行层。')
        artifacts = [s['reference'] for f in ready['frames'] for s in f['files']['snapshots']]
        self._same([self.s.work_artifacts.reference(r) for r in related('work_unit_artifact')],
            artifacts, '回合首边包含未核原快照或其它运行的成果。')
        # The original readers verify every canonical current pointer. An extra
        # pointer alias to one of these artifacts is also in this namespace.
        artifact_ids = {r['id'] for r in artifacts}
        run_ids = {f['run']['id'] for f in ready['frames']}
        for pointer in self.s.collaboration.all(db, 'work_unit_artifact_current'):
            if pointer.get('artifact_id') in artifact_ids and pointer['id'] not in {'unit-artifact-current:' + r for r in run_ids}:
                raise ValueError('回合首边包含额外原成果指针。')
        if (self.s.teamwork.current(db, item) != plan or self.s.work_drivers.current(db, item) != after or
                self.s.work_units.authorization(db, after) != auth or
                self.s.work_items.item(db, item)['revision'] != after['item_revision'] or
                related('work_driver') != [after] or related('work_unit_authorization') != [auth]):
            raise ValueError('回合首边当前指针、任务、暂停责任或原授权已变化。')
        # Reconstruct all pre-checkpoint driver versions/transforms. The new
        # layer has exactly its saved PAUSED version; future versions fail closed.
        self.checkpoints._driver_history(db, parent, obs['events']['cutoff'])
        values = []
        for r in related('work_driver_revision'):
            p = next((p for p in plans if p['id'] == r['plan_id']), None)
            if p is None: raise ValueError('回合首边责任引用未知方案。')
            v = old.old._driver(db, p, {'id': r['driver_id'], 'generation': r['generation'], 'revision': r['revision']})
            e = old._driver_event(db, v)
            if p == plan:
                if v != after: raise ValueError('本版回合首边尚未重建后继责任变化。')
            elif e > obs['events']['cutoff']: raise ValueError('冻结父方案后责任仍变化。')
            values.append(v)
        self._same([v for _, v in events('work_driver_coordinator_handoff_round_revised')],
            [{**after, 'no_dispatch': True}], '回合首边须有唯一实际新责任事件。')
        driver_events = []
        for kind in {r[0] for r in db.execute("SELECT DISTINCT kind FROM events WHERE kind LIKE 'work_driver_%'")}:
            driver_events.extend(v for _, v in events(kind))
        self._same(driver_events, [{**v, 'no_dispatch': True} for v in values], '回合首边全部责任事件存在孤立或遗漏。')
        old_auths = []
        for p in parents:
            versions = [r for r in related('work_unit_authorization_revision') if r['plan_id'] == p['id']]
            if len(versions) != 1: raise ValueError('每个原父层须有唯一完整授权版本。')
            a = old.old._auth(db, p, {'id': 'unit-auth:' + versions[0]['driver_generation'], 'revision': versions[0]['revision']})
            old_auths.append(a)
        if old_auths[-1] != obs['history']['authorization']: raise ValueError('回合检查点原授权版本不符。')
        self._same(related('work_unit_authorization_revision'),
            [{**v, 'id': 'unit-auth-version:' + v['revision']} for v in old_auths + [auth]], '回合首边全层原授权有遗漏。')
        self._same([v for _, v in events('cooperation_authorized')], [old_auths[0]], '回合首边原授权事件不唯一。')
        # Exact record/event namespaces, including pre-existing old preview data.
        expected = {CHECKPOINT: cp, CANDIDATE: ready['candidate'], APPLICATION: application, REGISTRATION: registration}
        for protocol, row in expected.items():
            self._same(related(KIND_FOR[protocol]), [row], '回合首边包含未验证的明确登记数据。')
            self._same([v for _, v in events(EVENTS[protocol])], [row], '回合首边记录与实际事件不闭合。')
        self._same([self.checkpoints.live._event(e) for e, _ in events(SAVE_EVENT)], [save], '回合首边存在其它保存事件。')
        from . import work_coordinator_handoff_adjustment_records as ar
        from .work_coordinator_handoff_adjustment_sources import WorkCoordinatorHandoffAdjustmentSources
        old_edges = parent_path['edges'] if parent_path else []
        old_data = ar.WorkCoordinatorHandoffAdjustmentRecords(self.s)
        old_sources = WorkCoordinatorHandoffAdjustmentSources(self.s)
        for protocol in ar.KIND_FOR:
            rows = related(ar.KIND_FOR[protocol])
            for r in rows:
                old_data.get(db, protocol, old_data.ref(r))
                seq = old_sources._event(db, ar.EVENTS[protocol], r)
                if seq > obs['events']['cutoff']: raise ValueError('冻结父历史后出现额外旧调整数据。')
                if protocol == ar.CANDIDATE: old_sources.read_prepared(db, old_data.ref(r))
                elif protocol == ar.CHECKPOINT: old_sources.read_checkpoint(db, old_data.ref(r))
            self._same([v for _, v in events(ar.EVENTS[protocol])], rows, '旧调整全部记录事件有孤立或遗漏。')
            if protocol in (ar.APPLICATION, ar.CONSUMPTION):
                field = 'application' if protocol == ar.APPLICATION else 'consumption'
                self._same([old_data.ref(r) for r in rows], [e[field] for e in old_edges], '旧调整存在未核保存边。')
        self._same([self.checkpoints.live._event(e) for e, _ in events('coordinator_handoff_adjustment_plan_saved')],
            [e['save_event'] for e in old_edges], '旧调整保存事件存在遗漏。')
        for kind, name, event in ((DELIVERY_KIND, 'delivery', 'coordinator_handoff_delivered'),
                (COMPLETION_KIND, 'completion', 'coordinator_handoff_completed')):
            self._same(related(kind), obs['history'][name], '首边冻结后正式交付或完成发生变化。')
            self._same([v for _, v in events(event)], obs['history'][name], '全部交付/完成事件与记录不符。')
        checks = obs['events']['checks']
        proposals = related('work_proposal')
        self._same([self.s.work_proposals.reference(p) for p in proposals], [c['reference'] for c in checks],
            '首边冻结后存在未核协调检查。')
        latest = next((p for p in proposals if checks and self.s.work_proposals.reference(p) == checks[-1]['reference']), None)
        if self.s.work_proposals.current(db, item) != latest: raise ValueError('回合首边原检查指针已变化。')
        proposal_ids = {p['id'] for p in proposals}
        for kind in ('work_proposal_consumption', 'work_file_handoff_revision_consumption'):
            for row in self.s.collaboration.all(db, kind):
                body = row.get('snapshot', row)
                if (row.get('item_id') == item or body.get('item_id') == item or
                        row.get('proposal', {}).get('id') in proposal_ids or
                        body.get('proposal', {}).get('id') in proposal_ids or
                        row['id'] in {'proposal-consumption:' + p for p in proposal_ids}):
                    raise ValueError('回合原提案不得混入其它旧修订消费。')
        # Capabilities cannot silently expand after the frozen parent observation.
        for kind, event in (('work_coordinator_handoff_capability', 'coordinator_runtime_opened'),
                ('work_coordinator_handoff_adjustment_capability', 'coordinator_adjustment_opened')):
            for row in related(kind):
                if self.checkpoints._event(db, event, row)['seq'] > obs['events']['cutoff']:
                    raise ValueError('回合首边尚未开放新增能力。')
        self.s.work_flows.require_stopped(db, item)

    def _once(self, db, plan):
        successor_plan(plan)
        if self.s.collaboration.get(db, 'teamwork_plan', plan['id']) != plan:
            raise ValueError('回合首边须读取实际完整新方案。')
        ready = self.checkpoints.read_candidate(db, plan['coordinator_file_handoff_round_lineage']['candidate'])
        cp, candidate = ready['checkpoint'], ready['candidate']; obs = ready['observation']
        ids = {obs['root']['id'], obs['plan']['id'], plan['id']}; item = plan['item_id']
        matches = self._related(db, KIND_FOR[APPLICATION], item, ids)
        regs = self._related(db, KIND_FOR[REGISTRATION], item, ids)
        if len(matches) != 1 or len(regs) != 1: raise ValueError('回合首边须有唯一准确应用与明确登记。')
        application = self.data.get(db, APPLICATION, self.data.ref(matches[0]))
        registration = self.data.get(db, REGISTRATION, self.data.ref(regs[0]))
        a = application['snapshot']; old = WorkCoordinatorHandoffAdjustmentPath(self.s)
        after = old.old._driver(db, plan, a['driver_after']); auth = old.old._auth(db, plan, a['authorization_after'])
        h = obs['history']; adjacent(cp, candidate, plan, h['authorization'], auth, h['driver'], after)
        from .work_coordinator_handoff_round_apply import bodies, save_payload
        save = self.checkpoints._event(db, SAVE_EVENT, save_payload(self.s, candidate, plan, after, auth))
        expected = bodies(self.s, cp, candidate, plan, h['driver'], after, h['authorization'], auth, save)
        app_event = self.checkpoints._event(db, EVENTS[APPLICATION], application)
        reg_event = self.checkpoints._event(db, EVENTS[REGISTRATION], registration)
        if (expected != (a, registration['snapshot']) or
                not ready['candidate_event']['seq'] < old._driver_event(db, after) < save['seq'] < app_event['seq'] < reg_event['seq'] or
                any(r['command_id'] != candidate['command_id'] for r in (application, registration)) or
                not candidate['created'] <= auth['transferred_at'] <= after['updated'] <= application['created'] <=
                    registration['created'] < auth['dispatch_before']):
            raise ValueError('回合首边实际事件、时间、完整应用或登记不一致。')
        self._scope(db, plan, ready, application, registration, after, auth, save, obs['saved_path'])
        usage = self.s.work_units.usage(db, [f['run'] for f in ready['frames']])
        if usage != obs['usage']: raise ValueError('回合首边不能重置全层原累计。')
        edge = {'parent_plan': obs['plan'], 'child_plan': plan_ref(plan), 'checkpoint': self.data.ref(cp),
            'candidate': self.data.ref(candidate), 'application': self.data.ref(application),
            'registration': self.data.ref(registration), 'save_event': save}
        path = validate_path(versioned({'protocol': SAVED_PATH, 'root': obs['root'], 'plan': plan_ref(plan),
            'parent_kind': obs['parent_kind'], 'parent_path': obs['saved_path'], 'edge': edge,
            'history_frontier': h['frontier'], 'material_observation': h['material_observation'],
            'usage': usage, 'original_dispatch_before': obs['original_dispatch_before'], 'scope': PATH_SCOPE, **PERMISSIONS}))
        return deepcopy({'root': obs['root'], 'plan': plan, 'saved_path': path, 'candidate': candidate,
            'checkpoint': cp, 'application': application, 'registration': registration, 'driver': after,
            'authorization': auth, 'frames': ready['frames'], 'history': h, 'usage': usage,
            'current_material_observation': ready['current_material_observation'], 'saved_edge_verified': True,
            'successor_layers_verified': False, 'verified_scope': PATH_SCOPE, 'public_apply': False, **PERMISSIONS})

    @closed
    def read(self, db, plan):
        with database_observation(db):
            before = db.total_changes
            first = self._once(db, plan); again = self._once(db, plan)
            self.checkpoints._recheck(db, {'frames': again['frames'],
                'observation': again['checkpoint']['snapshot']['observation'],
                'current_material_observation': again['current_material_observation']})
            if first != again or before != db.total_changes or self.s.teamwork.current(db, plan['item_id']) != plan:
                raise ValueError('回合首边末尾完整历史、记录、材料或指针已变化。')
            return deepcopy(again)
