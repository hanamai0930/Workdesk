"""Saved ROUND responsibility history at actual event boundaries.

This is a separate observational protocol. It accounts for resume/pause/restart/
release after the first saved ROUND, while rejecting successor runs, new rounds
and capabilities. It is not the future mixed runtime path or an input permit.
"""
from copy import deepcopy

from .store import digest
from .work_read_observation import database_observation
from .work_transitions import plan_ref
from .work_coordinator_handoff_records import (
    PERMISSIONS, versioned, fields, reference, DELIVERY_KIND, COMPLETION_KIND)
from .work_coordinator_handoff_adjustment_contracts import closed
from .work_coordinator_handoff_adjustment_records import (
    hashed, permissions, complete_frontier, usage, finite, validate_path as adjustment_path)
from .work_coordinator_handoff_records import observation
from .work_coordinator_handoff_round_contracts import (
    adjacent, successor_plan, successor_driver, transferred_authorization)
from .work_coordinator_handoff_round_records import (
    CHECKPOINT, CANDIDATE, APPLICATION, REGISTRATION, SAVE_EVENT, PAUSE_REASON,
    KIND_FOR, EVENTS, event_ref, record_ref)
from .work_coordinator_handoff_round_path import WorkCoordinatorHandoffRoundPath
from .work_coordinator_handoff_adjustment_path import WorkCoordinatorHandoffAdjustmentPath


PROTOCOL = 'COORDINATOR_FILE_HANDOFF_ROUND_SAVED_DRIVER_HISTORY_V1'
SCOPE = 'FIRST_ROUND_SAVE_AND_RESPONSIBILITY_HISTORY; NO_SUCCESSOR_RUN_OR_INPUT_PERMISSION'
FIELDS = {'protocol', 'root', 'plan', 'parent_kind', 'parent_path', 'edge',
    'driver_history', 'driver', 'authorization', 'history_frontier',
    'material_observation', 'usage', 'original_dispatch_before', 'cutoff',
    'boundary_event', 'scope', 'version', *PERMISSIONS}
DRIVER_EVENTS = {'work_driver_resume', 'work_driver_pause',
    'work_driver_restart_paused', 'work_driver_release'}
SAVED_DRIVER_EVENT = 'work_driver_coordinator_handoff_round_revised'


@closed
def validate_history(value):
    fields(value, FIELDS); hashed(value); permissions(value)
    if value['protocol'] != PROTOCOL or value['scope'] != SCOPE:
        raise ValueError('回合责任历史须为准确独立闭合协议。')
    for key in ('root', 'plan'): reference(value[key])
    complete_frontier(value['history_frontier']); observation(value['material_observation'])
    usage(value['usage']); finite(value['original_dispatch_before'])
    if type(value['cutoff']) is not int or value['cutoff'] < 1:
        raise ValueError('回合责任历史须有实际事件截止。')
    boundary = value['boundary_event']
    if boundary is not None:
        event_ref(boundary)
        if boundary['seq'] != value['cutoff'] + 1:
            raise ValueError('回合历史边界须为截止后的准确实际事件。')
    if value['parent_kind'] == 'ROOT':
        if value['parent_path'] is not None: raise ValueError('原根父不能附加路径。')
        parent = value['root']
    elif value['parent_kind'] == 'ADJUSTMENT':
        parent_path = adjustment_path(value['parent_path'])
        if parent_path['root'] != value['root']: raise ValueError('回合责任历史原根不符。')
        parent = parent_path['plan']
    else:
        raise ValueError('责任历史尚未开放ROUND父和新层运行。')
    edge = value['edge']
    fields(edge, {'parent_plan', 'child_plan', 'checkpoint', 'candidate', 'application',
        'registration', 'save_event', 'registration_event'})
    for key in ('parent_plan', 'child_plan'): reference(edge[key])
    for key, protocol in (('checkpoint', CHECKPOINT), ('candidate', CANDIDATE),
            ('application', APPLICATION), ('registration', REGISTRATION)):
        record_ref(edge[key], protocol)
    event_ref(edge['save_event'], SAVE_EVENT)
    event_ref(edge['registration_event'], EVENTS[REGISTRATION])
    if (edge['parent_plan'] != parent or edge['child_plan'] != value['plan'] or
            edge['child_plan'] in (parent, value['root']) or
            not edge['save_event']['seq'] < edge['registration_event']['seq'] <= value['cutoff']):
        raise ValueError('回合保存边须在准确完整登记截止前。')
    history = value['driver_history']
    if not isinstance(history, list) or not history: raise ValueError('回合责任历史不能为空。')
    previous = None
    for index, entry in enumerate(history):
        fields(entry, {'event', 'driver'}); event_ref(entry['event'])
        driver = successor_driver(entry['driver']); seq = entry['event']['seq']
        if (driver['plan_id'] != value['plan']['id'] or driver['plan_version'] != value['plan']['version'] or
                driver['transition_registration'] != edge['candidate'] or driver['previous_plan_reference'] != parent or
                seq > value['cutoff']):
            raise ValueError('回合责任版本不能跨方案、登记或截止。')
        if index == 0:
            if (entry['event']['kind'] != SAVED_DRIVER_EVENT or driver['state'] != 'PAUSED' or
                    driver['reason'] != PAUSE_REASON or
                    not seq < edge['save_event']['seq']):
                raise ValueError('回合责任须从真实保存暂停版本开始。')
        elif (entry['event']['kind'] not in DRIVER_EVENTS or seq <= previous['event']['seq'] or
                seq <= edge['registration_event']['seq']):
            raise ValueError('回合责任变化须为登记后有序实际事件。')
        if previous is not None:
            old = previous['driver']; expected = deepcopy(old); kind = entry['event']['kind']
            if kind == 'work_driver_resume':
                if old['state'] != 'PAUSED': raise ValueError('恢复前须为暂停责任。')
                expected.update(state='RESERVED', reason='EXPLICIT_RECHECK', instance=driver['instance'])
            elif kind in {'work_driver_pause', 'work_driver_restart_paused'}:
                if old['state'] != 'RESERVED': raise ValueError('暂停前须为可用责任。')
                expected.update(state='PAUSED', reason='EXPLICIT_PAUSE' if kind == 'work_driver_pause' else 'CONTROLLER_RESTART')
            else:
                if old['state'] not in {'PAUSED', 'RESERVED'}: raise ValueError('释放前须持有责任。')
                expected.update(state='RELEASED', reason='EXPLICIT_RELEASE', released=driver['released'])
            expected.update(revision=driver['revision'], updated=driver['updated'])
            if (expected != driver or driver['revision'] in {v['driver']['revision'] for v in history[:index]} or
                    driver['updated'] < old['updated']):
                raise ValueError('回合责任不可变字段或实际状态变化不符。')
        previous = entry
    if value['driver'] != history[-1]['driver']: raise ValueError('回合责任须取截止前末版本。')
    auth = transferred_authorization(value['authorization']); driver = value['driver']
    if (auth['plan_id'] != value['plan']['id'] or auth['plan_version'] != value['plan']['version'] or
            auth['transfer_registration'] != edge['candidate'] or
            auth['driver_generation'] != driver['generation'] or auth['item_id'] != driver['item_id'] or
            auth['dispatch_before'] != value['original_dispatch_before'] or auth['usage_at_transfer'] != value['usage']):
        raise ValueError('回合责任历史不得重置原授权或累计。')
    return deepcopy(value)


class WorkCoordinatorHandoffRoundHistory(WorkCoordinatorHandoffRoundPath):
    """Uses only the old namespace helpers, never the first-edge read/proof."""

    def _bound(self, db, boundary):
        if boundary is None:
            return db.execute('SELECT COALESCE(MAX(seq),0) FROM events').fetchone()[0]
        event_ref(boundary)
        row = db.execute('SELECT * FROM events WHERE seq=?', (boundary['seq'],)).fetchone()
        if row is None or self.checkpoints.live._event(row) != boundary:
            raise ValueError('责任历史边界不是完整准确实际事件。')
        return boundary['seq'] - 1

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
        reservations = events('cooperative_unit_reserved')
        self._same([payload for _, payload in reservations], [f['run'] for f in ready['frames']],
            '回合责任历史存在孤立、重复或未重建的实际保留事件。')
        if any(event['task_id'] != payload['task_id'] or event['attempt_id'] != payload['attempt_id']
                for event, payload in reservations):
            raise ValueError('回合实际保留事件必须绑定原运行的准确任务和尝试。')
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
        # Validate all live immutable responsibility versions even for a bounded
        # projection. A bound selects the historical value, never hides corrupt
        # later rows, orphan events or a stale live pointer.
        self.checkpoints._driver_history(db, parent, obs['events']['cutoff'])
        values = []; child_versions = []
        for row in related('work_driver_revision'):
            p = next((p for p in plans if p['id'] == row['plan_id']), None)
            if p is None: raise ValueError('回合责任引用未知方案。')
            value = old.old._driver(db, p, {'id': row['driver_id'],
                'generation': row['generation'], 'revision': row['revision']})
            seq = old._driver_event(db, value)
            if p == plan:
                successor_driver(value)
                child_versions.append((seq, value, row))
            elif seq > obs['events']['cutoff']:
                raise ValueError('冻结原父后责任历史仍有变化。')
            values.append(value)
        child_versions.sort(key=lambda v: v[0])
        registered = self.checkpoints._event(db, EVENTS[REGISTRATION], registration)['seq']
        if not child_versions or child_versions[0][1] != after:
            raise ValueError('回合责任缺少准确首次保存暂停版本。')
        for seq, value, _ in child_versions[1:]:
            kind = db.execute('SELECT kind FROM events WHERE seq=?', (seq,)).fetchone()['kind']
            if kind not in DRIVER_EVENTS or seq <= registered:
                raise ValueError('未运行回合仅接受登记后的恢复、暂停、重启和释放。')
        old._driver_transitions(db, child_versions)
        latest = child_versions[-1][1]
        if (self.s.teamwork.current(db, item) != plan or
                self.s.work_drivers.current(db, item) != latest or
                self.s.work_units.authorization(db, latest) != auth or
                self.s.work_items.item(db, item)['revision'] != latest['item_revision'] or
                related('work_driver') != [latest] or related('work_unit_authorization') != [auth]):
            raise ValueError('回合责任当前指针、任务、责任或原授权不符。')
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
        return child_versions

    def _once(self, db, plan, boundary=None):
        successor_plan(plan); cutoff = self._bound(db, boundary)
        if self.s.collaboration.get(db, 'teamwork_plan', plan['id']) != plan:
            raise ValueError('回合责任历史须读取实际完整方案。')
        ready = self.checkpoints.read_candidate(db, plan['coordinator_file_handoff_round_lineage']['candidate'])
        cp, candidate = ready['checkpoint'], ready['candidate']; obs = ready['observation']
        ids = {obs['root']['id'], obs['plan']['id'], plan['id']}; item = plan['item_id']
        apps = self._related(db, KIND_FOR[APPLICATION], item, ids)
        regs = self._related(db, KIND_FOR[REGISTRATION], item, ids)
        if len(apps) != 1 or len(regs) != 1:
            raise ValueError('回合责任历史须有唯一准确应用和登记。')
        application = self.data.get(db, APPLICATION, self.data.ref(apps[0]))
        registration = self.data.get(db, REGISTRATION, self.data.ref(regs[0]))
        body = application['snapshot']; old = WorkCoordinatorHandoffAdjustmentPath(self.s)
        after = old.old._driver(db, plan, body['driver_after'])
        auth = old.old._auth(db, plan, body['authorization_after']); h = obs['history']
        adjacent(cp, candidate, plan, h['authorization'], auth, h['driver'], after)
        from .work_coordinator_handoff_round_apply import bodies, save_payload
        save = self.checkpoints._event(db, SAVE_EVENT, save_payload(self.s, candidate, plan, after, auth))
        app_event = self.checkpoints._event(db, EVENTS[APPLICATION], application)
        reg_event = self.checkpoints._event(db, EVENTS[REGISTRATION], registration)
        if (bodies(self.s, cp, candidate, plan, h['driver'], after, h['authorization'], auth, save) !=
                (body, registration['snapshot']) or
                not ready['candidate_event']['seq'] < old._driver_event(db, after) < save['seq'] <
                    app_event['seq'] < reg_event['seq'] <= cutoff or
                any(r['command_id'] != candidate['command_id'] for r in (application, registration)) or
                not candidate['created'] <= auth['transferred_at'] <= after['updated'] <=
                    application['created'] <= registration['created'] < auth['dispatch_before']):
            raise ValueError('回合责任历史实际保存事件、时间或完整登记不一致。')
        versions = self._scope(db, plan, ready, application, registration, after, auth, save, obs['saved_path'])
        driver_history = [{'event': self.checkpoints.live._event(db.execute(
            'SELECT * FROM events WHERE seq=?', (seq,)).fetchone()), 'driver': value}
            for seq, value, _ in versions if seq <= cutoff]
        used = self.s.work_units.usage(db, [f['run'] for f in ready['frames']])
        if used != obs['usage']: raise ValueError('回合责任历史原累计已变化。')
        edge = {'parent_plan': obs['plan'], 'child_plan': plan_ref(plan),
            'checkpoint': self.data.ref(cp), 'candidate': self.data.ref(candidate),
            'application': self.data.ref(application), 'registration': self.data.ref(registration),
            'save_event': save, 'registration_event': reg_event}
        path = validate_history(versioned({'protocol': PROTOCOL, 'root': obs['root'], 'plan': plan_ref(plan),
            'parent_kind': obs['parent_kind'], 'parent_path': obs['saved_path'], 'edge': edge,
            'driver_history': driver_history, 'driver': driver_history[-1]['driver'], 'authorization': auth,
            'history_frontier': h['frontier'], 'material_observation': h['material_observation'],
            'usage': used, 'original_dispatch_before': obs['original_dispatch_before'],
            'cutoff': cutoff, 'boundary_event': deepcopy(boundary), 'scope': SCOPE, **PERMISSIONS}))
        return deepcopy({'root': obs['root'], 'plan': plan, 'saved_driver_history': path,
            'checkpoint': cp, 'candidate': candidate, 'application': application, 'registration': registration,
            'driver': path['driver'], 'authorization': auth, 'frames': ready['frames'],
            'parent_history': h, 'usage': used, 'current_material_observation': ready['current_material_observation'],
            'saved_edge_verified': True, 'driver_history_verified': True,
            'full_mixed_path_verified': False, 'successor_layers_verified': False,
            'verified_scope': SCOPE, 'public_apply': False, **PERMISSIONS})

    def read(self, db, plan, *, before_event=None):
        return self._read(db, plan, before_event)

    @closed
    def _read(self, db, plan, boundary):
        if not db.in_transaction:
            raise ValueError('回合责任历史须在同一明确数据库事务内读取。')
        with database_observation(db):
            changes = db.total_changes
            pointers = (self.s.teamwork.current(db, plan['item_id']),
                self.s.work_drivers.current(db, plan['item_id']))
            first = self._once(db, plan, boundary); again = self._once(db, plan, boundary)
            self.checkpoints._recheck(db, {'frames': again['frames'],
                'observation': again['checkpoint']['snapshot']['observation'],
                'current_material_observation': again['current_material_observation']})
            if (first != again or changes != db.total_changes or pointers !=
                    (self.s.teamwork.current(db, plan['item_id']), self.s.work_drivers.current(db, plan['item_id']))):
                raise ValueError('回合责任历史完整重读、数据库、真实材料或当前指针已变化。')
            return deepcopy(again)
