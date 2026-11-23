"""Independent complete namespace audit of the saved post-ROUND tree."""
from .store import digest
from .work_coordinator_handoff_records import DELIVERY_KIND, COMPLETION_KIND
from .work_coordinator_handoff_adjustment_path import WorkCoordinatorHandoffAdjustmentPath
from .work_coordinator_handoff_post_round_saved_records import successor_driver
from .work_coordinator_handoff_post_round_saved_records import REGISTRATION, EVENTS
from . import work_coordinator_handoff_post_round_saved_records as sr
from . import work_coordinator_handoff_mixed_records as mr
from .work_coordinator_handoff_mixed_runtime import KIND, OPENED

def audit(self, db, plan, h):
    """Account for every plan/run/auth/driver and saved record in this scope."""
    ready = self._ready; cp = ready['checkpoint']; obs = ready['observation']
    old = WorkCoordinatorHandoffAdjustmentPath(self.s)
    layers = self._layers + [self]; first = layers[0]
    plans = self._parents + [plan]; parents = self._parents; frames = h['frames']; item = plan['item_id']
    auth = h['authorization']; after = self._versions[0][1]
    self._frame_identities(frames)
    for ref in (h['capability'], h['adjustment_capability']):
        if ref is not None: self._identity_ids.update(ref.values())
    parent_path = first._ready['observation']['saved_path']
    old_edges = parent_path['edges'] if parent_path else []
    for edge in old_edges:
        for k in ('checkpoint', 'candidate', 'application', 'consumption'): self._identity_ids.update(edge[k].values())
    for layer in layers:
        for k in ('checkpoint', 'candidate', 'application', 'registration'): self._identity_ids.update(layer._edge_value[k].values())
        self._identity_ids.update(v['revision'] for _, v, _ in layer._versions)
        if layer._round: self._identity_ids.update(layer.ref(layer._round).values())
    registration = self.data.get(db, REGISTRATION, self._edge_value['registration'])
    cutoffs = {p['id']: first._ready['observation']['events']['cutoff'] for p in first._parents}
    for before, later in zip(layers, layers[1:]):
        cutoffs[before._edge_value['child_plan']['id']] = (later._ready['checkpoint']['snapshot']['cutoff'] if later is self
            else later._ready['observation']['events']['cutoff'])
    related = lambda kind: self._related(db, kind, item)
    events = lambda kind: self._events(db, kind, item)
    proposals = related('work_proposal')
    for proposal in proposals: self._identity_ids.update(self.ref(proposal).values())
    self._same(related('teamwork_plan'), plans, '回合首边存在未重建方案或其它保存层。')
    self._same(related('teamwork_current'), [{'id': 'teamwork-current:' + item, 'plan_id': plan['id']}],
        'Mixed当前方案指针包含别名或未知记录。')
    self._same(related('work_unit_run'), [f['run'] for f in frames], '回合首边尚未开放后继运行或未知运行层。')
    reservations = events('cooperative_unit_reserved')
    self._same([payload for _, payload in reservations], [f['run'] for f in frames],
        '回合责任历史存在孤立、重复或未重建的实际保留事件。')
    if any(event['task_id'] != payload['task_id'] or event['attempt_id'] != payload['attempt_id']
            for event, payload in reservations):
        raise ValueError('回合实际保留事件必须绑定原运行的准确任务和尝试。')
    # Payload identity and task/attempt columns are independent attribution
    # channels. Neither can hide an orphan exit or artifact-capture event.
    exits = []; captures = []
    for frame in frames:
        run = frame['run']; attempt = self.s.store.attempt(run['attempt_id'], db)
        if attempt['result'] is not None:
            exits.append(self.checkpoints._event(db, 'attempt_result_saved', attempt['result'], None,
                run['task_id'], run['attempt_id']))
        for capture in self.sources._capture_events(db, run, frame['files']):
            captures.append(self.checkpoints.live._event(db.execute('SELECT * FROM events WHERE seq=?', (capture['seq'],)).fetchone()))
    for kind, expected in (('attempt_result_saved', exits), ('cooperative_files_checked', captures)):
        self._same([self.checkpoints.live._event(e) for e, _ in events(kind)], expected,
            'Mixed实际退出或成果捕获事件存在孤立、误绑定或遗漏。')
    artifacts = [s['reference'] for f in frames for s in f['files']['snapshots']]
    self._same([self.s.work_artifacts.reference(r) for r in related('work_unit_artifact')],
        artifacts, '回合首边包含未核原快照或其它运行的成果。')
    # The original readers verify every canonical current pointer. An extra
    # pointer alias to one of these artifacts is also in this namespace.
    artifact_ids = {r['id'] for r in artifacts}
    run_ids = {f['run']['id'] for f in frames}
    for pointer in self.s.collaboration.all(db, 'work_unit_artifact_current'):
        if pointer.get('artifact_id') in artifact_ids and pointer['id'] not in {'unit-artifact-current:' + r for r in run_ids}:
            raise ValueError('回合首边包含额外原成果指针。')
    # Validate all live immutable responsibility versions even for a bounded
    # projection. A bound selects the historical value, never hides corrupt
    # later rows, orphan events or a stale live pointer.
    first.checkpoints._driver_history(db, first._ready['checkpoint']['snapshot']['plan'], first._ready['observation']['events']['cutoff'])
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
        elif seq > cutoffs[p['id']]:
            raise ValueError('冻结原父后责任历史仍有变化。')
        values.append(value)
    child_versions.sort(key=lambda v: v[0])
    registered = self.checkpoints._event(db, EVENTS[REGISTRATION], registration)['seq']
    if not child_versions or child_versions[0][1] != after:
        raise ValueError('回合责任缺少准确首次保存暂停版本。')
    for seq, value, _ in child_versions[1:]:
        kind = db.execute('SELECT kind FROM events WHERE seq=?', (seq,)).fetchone()['kind']
        if kind not in {'work_driver_resume', 'work_driver_pause', 'work_driver_restart_paused', 'work_driver_release', 'work_driver_execution_linked'} or seq <= registered:
            raise ValueError('未运行回合仅接受登记后的恢复、暂停、重启和释放。')
    old._driver_transitions(db, child_versions)
    latest = child_versions[-1][1]
    if (self.s.teamwork.current(db, item) != plan or
            self.s.work_drivers.current(db, item) != latest or
            self.s.work_units.authorization(db, latest) != auth or
            self.s.work_items.item(db, item)['revision'] != latest['item_revision'] or
            related('work_driver') != [latest] or related('work_unit_authorization') != [auth]):
        raise ValueError('回合责任当前指针、任务、责任或原授权不符。')
    self._same([v for _, v in events('work_driver_coordinator_handoff_mixed_round_revised')],
        [{**layer._versions[0][1], 'no_dispatch': True} for layer in layers[1:-1]], 'Mixed新责任保存事件不闭合。')
    self._same([v for _, v in events(sr.DRIVER_EVENT)],
        [{**self._versions[0][1], 'no_dispatch': True}], '新保存责任事件不闭合。')
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
    from . import work_coordinator_handoff_round_records as fr
    from . import work_coordinator_handoff_round_runtime as ft
    for module, group in ((fr, [first]), (mr, layers[1:-1]), (sr, [self])):
        for protocol, field in ((module.CHECKPOINT, 'checkpoint'), (module.CANDIDATE, 'candidate'),
                (module.APPLICATION, 'application'), (module.REGISTRATION, 'registration')):
            expected = []
            for layer in group:
                data = (module.WorkCoordinatorHandoffRoundRecords(self.s) if module is fr else
                    module.WorkCoordinatorHandoffMixedRecords(self.s) if module is mr else module.SavedRecords(self.s))
                expected.append(data.get(db, protocol, layer._edge_value[field]))
            self._same(related(module.KIND_FOR[protocol]), expected, 'Mixed实际保存记录存在未知层。')
            self._same([v for _, v in events(module.EVENTS[protocol])], expected, 'Mixed记录与事件不闭合。')
        self._same([self.checkpoints.live._event(e) for e, _ in events(module.SAVE_EVENT)],
            [layer._edge_value['save_event'] for layer in group], 'Mixed存在未核保存事件。')
    self._same([v for _, v in events('work_driver_coordinator_handoff_round_revised')],
        [{**first._versions[0][1], 'no_dispatch': True}], 'Mixed首ROUND原责任事件不闭合。')
    for kind, event, group in ((ft.KIND, ft.OPENED, [first]), (KIND, OPENED, layers[1:-1])):
        expected = [layer._round for layer in group if layer._round is not None]
        self._same(related(kind), expected, 'Mixed能力包含未重建层。')
        self._same([v for _, v in events(event)], expected, 'Mixed能力事件包含孤立或遗漏。')
    # Optional standalone V2 checkpoints are not saved edges. Rebuild each at
    # its real event without the standalone reader's current-only gate.
    from .work_coordinator_handoff_round_parent_checkpoint import (
        WorkCoordinatorHandoffRoundParentCheckpoints, KIND as PARENT_KIND, EVENT as PARENT_EVENT, validate_row)
    from .work_coordinator_handoff_mixed_replay import FirstRoundReplay
    parent_rows = related(PARENT_KIND)
    self._same([v for _, v in events(PARENT_EVENT)], parent_rows,
        'Mixed可选父检查点记录与事件不闭合。')
    for row in parent_rows:
        validate_row(row)
        body = row['snapshot']; frozen = body['observation']; parent = body['plan']
        event = self.checkpoints._event(db, PARENT_EVENT, row)
        if parent != self.s.collaboration.get(db, 'teamwork_plan', parent['id']):
            raise ValueError('Mixed可选父检查点完整方案已变化。')
        if (parent['id'] != first._edge_value['child_plan']['id'] or
                event['seq'] != frozen['events']['cutoff'] + 1 or
                event['seq'] >= layers[1]._ready['checkpoint_event']['seq'] or
                row['created'] >= frozen['original_dispatch_before']):
            raise ValueError('Mixed可选V2检查点须属于真实首ROUND父冻结前历史。')
        history = FirstRoundReplay(self.s)
        rebuilt, current = history._walk(db, parent, event, frozen['history']['material_observation'])
        if WorkCoordinatorHandoffRoundParentCheckpoints(self.s)._assemble(db, parent, rebuilt, frozen['events']['cutoff']) != frozen:
            raise ValueError('Mixed可选V2检查点不是实际事件截止的独立重建。')
        self.checkpoints._recheck(db, {'frames': rebuilt['frames'], 'observation': frozen,
            'current_material_observation': current})
    from . import work_coordinator_handoff_adjustment_records as ar
    from .work_coordinator_handoff_adjustment_sources import WorkCoordinatorHandoffAdjustmentSources
    old_data = ar.WorkCoordinatorHandoffAdjustmentRecords(self.s)
    old_sources = WorkCoordinatorHandoffAdjustmentSources(self.s)
    for protocol in ar.KIND_FOR:
        rows = related(ar.KIND_FOR[protocol])
        for r in rows:
            old_data.get(db, protocol, old_data.ref(r))
            seq = old_sources._event(db, ar.EVENTS[protocol], r)
            if seq > first._ready['observation']['events']['cutoff']: raise ValueError('冻结父历史后出现额外旧调整数据。')
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
        self._same(related(kind), h[name], '首边冻结后正式交付或完成发生变化。')
        self._same([v for _, v in events(event)], h[name], '全部交付/完成事件与记录不符。')
    self._check_proposals(db, plan, h)
    proposal_ids = {p['id'] for p in proposals}
    pointers = [p for p in self.s.collaboration.all(db, 'work_proposal_current')
        if p.get('proposal_id') in proposal_ids or p.get('id') == 'proposal-current:' + item]
    ordered = self._proposal_events(db, item)
    self._same(pointers, [{'id': 'proposal-current:' + item, 'proposal_id': ordered[-1][1]['id']}] if ordered else [],
        'Mixed当前提案指针包含别名、未知或旧版本。')
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
        self._same([v for _, v in events(event)], related(kind), '原能力记录与实际事件全集不符。')
        for row in related(kind):
            if self.checkpoints._event(db, event, row)['seq'] > first._ready['observation']['events']['cutoff']:
                raise ValueError('回合首边尚未开放新增能力。')
    self.s.work_flows.require_stopped(db, item)
    if h['usage']['attempts'] > auth['max_attempts_total']:
        raise ValueError('ROUND全层原累计上限已增长。')
    # Exact post-ROUND activation and new saved/preparation namespaces. The
    # bounded parent's prefix cannot hide later, renamed or wrapped records.
    from .work_coordinator_handoff_post_round_contracts import KIND as PK, OPENED as PE
    post = self._parent_reader._post
    self._same(related(PK), [post], '新保存原开启能力存在孤立或遗漏。')
    self._same([v for _, v in events(PE)], [post], '新保存原开启事件存在孤立或遗漏。')
    expected_rows = [self.data.get(db, protocol, self._edge_value[field]) for protocol, field in
        ((sr.CHECKPOINT, 'checkpoint'), (sr.CANDIDATE, 'candidate'),
         (sr.APPLICATION, 'application'), (sr.REGISTRATION, 'registration'))]
    import json
    def marker(value):
        pending = [value]
        while pending:
            node = pending.pop()
            if isinstance(node, dict):
                identity = node.get('id')
                if (node.get('protocol') in sr.KIND_FOR or (isinstance(identity, str) and
                        any(identity.startswith(kind + ':') for kind in sr.KIND_FOR.values()))): return True
                pending.extend(node.values())
            elif isinstance(node, list): pending.extend(node)
        return False
    # The prior sections established the exact complete canonical sets for
    # these namespaces. Inspect all other kinds as well: changing outer labels
    # cannot hide a nested identity or an original capability reference.
    from .work_coordinator_handoff_post_round_contracts import CAPABILITY as PC
    from .work_coordinator_handoff_round_runtime import CAPABILITY as FC
    from .work_coordinator_handoff_mixed_runtime import CAPABILITY as MC
    from .work_coordinator_handoff_runtime import CAPABILITY as RC
    from .work_coordinator_handoff_adjustment_runtime import CAPABILITY as AC
    from .work_coordinator_handoff_records import DELIVERY, COMPLETION
    record_kinds = {**fr.KIND_FOR, **mr.KIND_FOR, **ar.KIND_FOR, **sr.KIND_FOR,
        PC: PK, FC: ft.KIND, MC: KIND, RC: 'work_coordinator_handoff_capability',
        AC: 'work_coordinator_handoff_adjustment_capability', DELIVERY: DELIVERY_KIND,
        COMPLETION: COMPLETION_KIND}
    record_events = {**fr.EVENTS, **mr.EVENTS, **ar.EVENTS, **sr.EVENTS,
        PC: PE, FC: ft.OPENED, MC: OPENED, RC: 'coordinator_runtime_opened',
        AC: 'coordinator_adjustment_opened', DELIVERY: 'coordinator_handoff_delivered',
        COMPLETION: 'coordinator_handoff_completed'}
    known_rows = {kind: related(kind) for kind in record_kinds.values()}
    known_events = {kind: [v for _, v in events(kind)] for kind in record_events.values()}
    def any_record_marker(value):
        pending = [value]
        while pending:
            node = pending.pop()
            if isinstance(node, dict):
                identity = node.get('id')
                if (node.get('protocol') in record_kinds or (isinstance(identity, str) and
                        any(identity.startswith(k + ':') for k in record_kinds.values()))): return True
                pending.extend(node.values())
            elif isinstance(node, list): pending.extend(node)
        return False
    carrier_kinds = {'teamwork_plan', 'work_unit_run', 'work_driver', 'work_driver_revision',
        'work_unit_authorization', 'work_unit_authorization_revision', 'work_proposal', PARENT_KIND}
    carrier_rows = {k: related(k) for k in carrier_kinds}
    carrier_events = {k: [v for _, v in events(k)] for k in
        ({r[0] for r in db.execute("SELECT DISTINCT kind FROM events WHERE kind LIKE 'work_driver_%'")} |
         {'cooperative_unit_reserved', 'coordinator_proposal_checked', PARENT_EVENT,
          fr.SAVE_EVENT, mr.SAVE_EVENT, sr.SAVE_EVENT, 'coordinator_handoff_adjustment_plan_saved',
          'cooperation_authorized'})}
    for outer_kind, raw in db.execute('SELECT kind,data FROM collaboration'):
        value = json.loads(raw)
        if not self._belongs(value, item) or not any_record_marker(value): continue
        if value in known_rows.get(outer_kind, []) or value in carrier_rows.get(outer_kind, []): continue
        raise ValueError('完整保存树存在改名或嵌套隐藏的原记录。')
    for row in db.execute('SELECT * FROM events'):
        value = json.loads(row['payload'])
        if not self._belongs(value, item) or not any_record_marker(value): continue
        if value in known_events.get(row['kind'], []) or value in carrier_events.get(row['kind'], []): continue
        raise ValueError('完整保存树存在改名或嵌套隐藏的原事件。')
    found_rows = []; found_events = []
    for row in db.execute('SELECT kind,data FROM collaboration'):
        value = json.loads(row['data'])
        if row['kind'] in {'teamwork_plan', 'work_driver', 'work_driver_revision',
                'work_unit_authorization', 'work_unit_authorization_revision'}:
            continue  # Their exact full sets and immutable versions were verified above.
        if (row['kind'] in sr.KIND_FOR.values() or marker(value)) and self._belongs(value, item):
            protocol = value.get('snapshot', {}).get('protocol')
            if protocol not in sr.KIND_FOR or row['kind'] != sr.KIND_FOR[protocol]:
                raise ValueError('新保存记录改名、嵌套或协议错绑。')
            found_rows.append(sr.validate_row(value, protocol))
    for row in db.execute('SELECT * FROM events ORDER BY seq'):
        value = json.loads(row['payload'])
        if ((row['kind'].startswith('work_driver_') and value in [{**v, 'no_dispatch': True} for v in values]) or
                (row['kind'] == sr.SAVE_EVENT and self.checkpoints.live._event(row) == self._edge_value['save_event'])):
            continue  # Exact real carrier events, already matched as complete sets.
        if (row['kind'] in sr.EVENTS.values() or marker(value)) and self._belongs(value, item):
            protocol = value.get('snapshot', {}).get('protocol')
            if (protocol not in sr.EVENTS or row['kind'] != sr.EVENTS[protocol] or
                    row['task_id'] is not None or row['attempt_id'] is not None):
                raise ValueError('新保存事件改名、嵌套、协议或任务归属错绑。')
            found_events.append(sr.validate_row(value, protocol))
    self._same(found_rows, expected_rows, '新保存准备及保存记录全集不闭合。')
    self._same(found_events, expected_rows, '新保存准备及保存事件全集不闭合。')
    return child_versions
