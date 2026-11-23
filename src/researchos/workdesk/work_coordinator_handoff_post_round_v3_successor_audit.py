"""Complete responsibility namespace audit; default saved audit stays frozen."""
import json
from .store import digest
from .work_coordinator_handoff_records import DELIVERY_KIND, COMPLETION_KIND
from .work_coordinator_handoff_adjustment_path import WorkCoordinatorHandoffAdjustmentPath
from .work_coordinator_handoff_post_round_v3_saved_records import successor_driver
from .work_coordinator_handoff_post_round_v3_saved_records import REGISTRATION, EVENTS
from . import work_coordinator_handoff_post_round_v3_saved_records as v3
from .work_coordinator_handoff_post_round_v3_saved_component import SavedRecords as V3Records
from . import work_coordinator_handoff_post_round_v2_saved_records as vr
from . import work_coordinator_handoff_post_round_saved_records as sr
from . import work_coordinator_handoff_mixed_records as mr
from .work_coordinator_handoff_mixed_runtime import KIND, OPENED

def audit(self, db, plan, h):
    """Account for every plan/run/auth/driver and saved record in this scope."""
    from . import work_coordinator_handoff_post_round_v3_responsibility_records as rr
    chain = self._responsibility; extension = chain['extension']
    ready = self._ready; cp = ready['checkpoint']; obs = ready['observation']
    old = WorkCoordinatorHandoffAdjustmentPath(self.s)
    layers = self._layers + [self]; first = layers[0]
    mixed_layers = [l for l in layers if l._ready['candidate']['snapshot']['protocol'] == mr.CANDIDATE]
    post_layers = [l for l in layers if l._ready['candidate']['snapshot']['protocol'] == sr.CANDIDATE]
    v2_layers = [l for l in layers if l._ready['candidate']['snapshot']['protocol'] == vr.CANDIDATE]
    v3_layers = [self]
    canonical_rows = {}; canonical_events = {}
    def bind(kind, event, values):
        canonical_rows[kind] = list(values)
        canonical_events[event] = list(values)
    def storage(module):
        if module is v3: return V3Records(self.s)
        if module is fr: return module.WorkCoordinatorHandoffRoundRecords(self.s)
        if module is mr: return module.WorkCoordinatorHandoffMixedRecords(self.s)
        return module.SavedRecords(self.s)
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
    for transition in chain['transitions']:
        self._identity_ids.update(self.ref(transition).values())
    registration = self.data.get(db, REGISTRATION, self._edge_value['registration'])
    cutoffs = {p['id']: first._ready['observation']['events']['cutoff'] for p in first._parents}
    for before, later in zip(layers, layers[1:]):
        cutoffs[before._edge_value['child_plan']['id']] = later._ready['checkpoint']['snapshot'].get('cutoff', later._ready['checkpoint_event']['seq'] - 1)
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
            if value == after: successor_driver(value)
            child_versions.append((seq, value, row))
        elif seq > cutoffs[p['id']]:
            raise ValueError('冻结原父后责任历史仍有变化。')
        values.append(value)
    child_versions.sort(key=lambda v: v[0])
    registered = self.checkpoints._event(db, EVENTS[REGISTRATION], registration)['seq']
    if not child_versions or child_versions[0][1] != after:
        raise ValueError('回合责任缺少准确首次保存暂停版本。')
    # The separate semantic owner proved every dedicated transition, exact
    # full command receipt and ordered event. Generic driver changes stay out.
    if child_versions != self._versions:
        raise ValueError('V3完整current责任版本不等于准确重建的专属链。')
    latest = child_versions[-1][1]
    if (self.s.teamwork.current(db, item) != plan or
            self.s.work_drivers.current(db, item) != latest or
            self.s.work_units.authorization(db, latest) != auth or
            self.s.work_items.item(db, item)['revision'] != latest['item_revision'] or
            related('work_driver') != [latest] or related('work_unit_authorization') != [auth]):
        raise ValueError('回合责任当前指针、任务、责任或原授权不符。')
    self._same([v for _, v in events('work_driver_coordinator_handoff_mixed_round_revised')],
        [{**layer._versions[0][1], 'no_dispatch': True} for layer in mixed_layers], 'Mixed新责任保存事件不闭合。')
    self._same([v for _, v in events(sr.DRIVER_EVENT)],
        [{**l._versions[0][1], 'no_dispatch': True} for l in post_layers], '首POST保存责任事件不闭合。')
    self._same([v for _, v in events(vr.DRIVER_EVENT)],
        [{**l._versions[0][1], 'no_dispatch': True} for l in v2_layers], 'V2保存责任事件不闭合。')
    self._same([v for _, v in events(v3.DRIVER_EVENT)],
        [{**l._versions[0][1], 'no_dispatch': True} for l in v3_layers], 'V3保存责任事件不闭合。')
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
    for module, group in ((fr, [first]), (mr, mixed_layers), (sr, post_layers), (vr, v2_layers), (v3, v3_layers)):
        for protocol, field in ((module.CHECKPOINT, 'checkpoint'), (module.CANDIDATE, 'candidate'),
                (module.APPLICATION, 'application'), (module.REGISTRATION, 'registration')):
            expected = []
            for layer in group:
                data = storage(module)
                expected.append(data.get(db, protocol, layer._edge_value[field]))
            bind(module.KIND_FOR[protocol], module.EVENTS[protocol], expected)
            self._same(related(module.KIND_FOR[protocol]), expected, 'Mixed实际保存记录存在未知层。')
            self._same([v for _, v in events(module.EVENTS[protocol])], expected, 'Mixed记录与事件不闭合。')
        self._same([self.checkpoints.live._event(e) for e, _ in events(module.SAVE_EVENT)],
            [layer._edge_value['save_event'] for layer in group], 'Mixed存在未核保存事件。')
        canonical_events[module.SAVE_EVENT] = [json.loads(db.execute('SELECT payload FROM events WHERE seq=?', (layer._edge_value['save_event']['seq'],)).fetchone()[0]) for layer in group]
    self._same([v for _, v in events('work_driver_coordinator_handoff_round_revised')],
        [{**first._versions[0][1], 'no_dispatch': True}], 'Mixed首ROUND原责任事件不闭合。')
    for kind, event, group in ((ft.KIND, ft.OPENED, [first]), (KIND, OPENED, mixed_layers)):
        expected = [layer._round for layer in group if layer._round is not None]
        bind(kind, event, expected)
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
    bind(PARENT_KIND, PARENT_EVENT, parent_rows)
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
        bind(ar.KIND_FOR[protocol], ar.EVENTS[protocol], rows)
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
    post = post_layers[0]._parent_reader._post
    self._same(related(PK), [post], '新保存原开启能力存在孤立或遗漏。')
    self._same([v for _, v in events(PE)], [post], '新保存原开启事件存在孤立或遗漏。')
    from .work_coordinator_handoff_post_round_successor_contracts import KIND as SK, OPENED as SE
    from .work_coordinator_handoff_post_round_successor_commands import RECEIPT_KIND, RECEIPT_EVENT
    expected_caps = [l._successor for l in post_layers]
    self._same(related(SK), expected_caps, 'V2全树后继能力存在未重建层。')
    self._same([v for _, v in events(SE)], expected_caps, 'V2全树后继开启事件不闭合。')
    expected_receipts = []
    for layer in post_layers:
        from .work_coordinator_handoff_post_round_successor_commands import require_receipt
        parent = self.s.collaboration.get(db, 'teamwork_plan', layer._edge_value['child_plan']['id'])
        require_receipt(layer, db, parent, frames)
        expected_receipts.extend(layer._related(db, RECEIPT_KIND, item))
    self._same(related(RECEIPT_KIND), expected_receipts, 'V2全树每次派发完整回执有遗漏或孤立。')
    self._same([v for _, v in events(RECEIPT_EVENT)], expected_receipts, 'V2全树回执事件有遗漏或孤立。')
    from .work_coordinator_handoff_post_round_v2_run_contracts import KIND as VK, OPENED as VE
    from .work_coordinator_handoff_post_round_v2_commands import RECEIPT_KIND as VR, RECEIPT_EVENT as VRE, require_receipt as require_v2_receipt
    expected_v2_caps = [l._successor for l in v2_layers]
    self._same(related(VK), expected_v2_caps, 'V2实际能力全集存在孤立或遗漏。')
    self._same([v for _, v in events(VE)], expected_v2_caps, 'V2实际开启事件全集不符。')
    expected_v2_receipts = []
    for layer in v2_layers:
        child = self.s.collaboration.get(db, 'teamwork_plan', layer._edge_value['child_plan']['id'])
        require_v2_receipt(layer, db, child, frames)
        expected_v2_receipts.extend(layer._related(db, VR, item))
    self._same(related(VR), expected_v2_receipts, 'V2实际每次完整回执存在孤立或遗漏。')
    self._same([v for _, v in events(VRE)], expected_v2_receipts, 'V2实际完整回执事件全集不符。')
    consumed = [l._ready['candidate']['snapshot']['proposal']['id'] for l in post_layers + v2_layers + v3_layers]
    consumed.extend(f['run']['proposal']['id'] for f in frames if f['run'].get('proposal'))
    if len(consumed) != len(set(consumed)): raise ValueError('V2保存提案不能重复消费。')
    # Exact registry comes from the complete semantic reconstructions above.
    # Fetching an arbitrary related row never grants carrier permission.
    bind(PK, PE, [post])
    bind(SK, SE, expected_caps); bind(RECEIPT_KIND, RECEIPT_EVENT, expected_receipts)
    bind(VK, VE, expected_v2_caps); bind(VR, VRE, expected_v2_receipts)
    for kind, event, name in (('work_coordinator_handoff_capability', 'coordinator_runtime_opened', 'capability'),
            ('work_coordinator_handoff_adjustment_capability', 'coordinator_adjustment_opened', 'adjustment_capability')):
        identity = h[name]
        expected = [self.s.collaboration.get(db, kind, identity['id'])] if identity else []
        self._same(related(kind), expected, 'V3原能力完整集合不符。')
        bind(kind, event, expected)
    for kind, event, name in ((DELIVERY_KIND, 'coordinator_handoff_delivered', 'delivery'),
            (COMPLETION_KIND, 'coordinator_handoff_completed', 'completion')):
        bind(kind, event, h[name])
    canonical_rows.update({'teamwork_plan': plans,
        'teamwork_current': [{'id': 'teamwork-current:' + item, 'plan_id': plan['id']}],
        'work_unit_run': [f['run'] for f in frames], 'work_driver': [latest],
        'work_driver_revision': [{**v, 'id': 'driver-version:' + v['revision'], 'driver_id': v['id']} for v in values],
        'work_unit_authorization': [auth],
        'work_unit_authorization_revision': [{**v, 'id': 'unit-auth-version:' + v['revision']} for v in old_auths + [auth]],
        'work_proposal': [v for _, v in ordered],
        'work_proposal_current': [{'id': 'proposal-current:' + item, 'proposal_id': ordered[-1][1]['id']}] if ordered else [],
        'work_unit_artifact': [self.s.collaboration.get(db, 'work_unit_artifact', a['id']) for a in artifacts],
        'work_unit_artifact_current': [{'id': 'unit-artifact-current:' + f['run']['id'],
            'artifact_id': f['files']['snapshots'][-1]['reference']['id']} for f in frames if f['files']['snapshots']]})
    from .execution_constraints import snapshot
    from .work_coordinator_handoff_adjustment_records import finite
    dispatches = []
    for frame in [f for f in frames if f['run']['plan_id'] != plan['id']]:
        run = frame['run']; task = self.s.store.task(run['task_id'], db)
        attempt = self.s.store.attempt(run['attempt_id'], db)
        value = self.s.collaboration.get(db, 'work_dispatch', 'dispatch:' + run['attempt_id'])
        command = db.execute('SELECT * FROM commands WHERE id=?', (run['command_id'],)).fetchone()
        if not value or command is None or command['kind'] != 'work-item.unit-send':
            raise ValueError('V3原运行缺少准确已提交派发绑定。')
        command_receipt = json.loads(command['receipt']); binding = command_receipt.get('work_item', {})
        finite(value['created'])
        from .work_coordinator_handoff_post_round_v3_execution_events import dispatch_session
        reserved_session = dispatch_session(self.s, db, self.checkpoints, frame, value)
        expected = {'id': 'dispatch:' + run['attempt_id'], 'item_id': item, 'item_revision': binding.get('revision'),
            'task_id': run['task_id'], 'attempt_id': run['attempt_id'], 'session_id': reserved_session,
            'command_id': run['command_id'], 'command_kind': 'work-item.unit-send', 'spec_hash': task['spec_hash'],
            'provider': task['spec']['provider'], 'credential_revision': value['credential_revision'],
            'execution_connection': task['spec'].get('execution_connection'),
            'execution_resource': task['spec'].get('execution_resource'), 'cooperative': run['cooperative'],
            'constraints': snapshot(task, attempt), 'responsibility': 'OFFICIAL_RUNTIME' if task['spec'].get('runtime_mode') == 'official' else 'WORKDESK_EXECUTION',
            'handoff_id': None, 'created': value['created']}
        if (value != expected or binding != {'id': item, 'revision': value['item_revision'], 'dispatch_id': value['id']} or
                command_receipt.get('unit_run_id') != run['id'] or command_receipt.get('launch_attempt') != run['attempt_id'] or
                not run['created'] <= value['created'] <= command['created'] or not isinstance(value['credential_revision'], str)):
            raise ValueError('V3原运行派发绑定、原回执或冻结配置不符：' + ','.join(
                k for k in set(value) | set(expected) if value.get(k) != expected.get(k)))
        self.checkpoints._event(db, 'work_dispatch_bound', value, None, run['task_id'], run['attempt_id'])
        dispatches.append(expected)
    bind('work_dispatch', 'work_dispatch_bound', dispatches + extension['dispatches'])
    root_plan = plans[0]
    canonical_events['teamwork_plan_prepared'] = [{'work_item_id': item,
        'item_revision': root_plan['item_revision'], 'plan_id': root_plan['id'],
        'version': root_plan['version'], 'mode': root_plan['mode'], 'no_dispatch': True}]
    # Driver event identities were independently matched against immutable
    # versions above; payload equality alone cannot select a different event.
    for value in values:
        seq = old._driver_event(db, value)
        row = db.execute('SELECT * FROM events WHERE seq=?', (seq,)).fetchone()
        canonical_events.setdefault(row['kind'], []).append({**value, 'no_dispatch': True})
    canonical_events.update({'cooperative_unit_reserved': [f['run'] for f in frames],
        'coordinator_proposal_checked': [v for _, v in ordered], 'cooperation_authorized': [old_auths[0]],
        'coordinator_handoff_adjustment_plan_saved': [json.loads(db.execute('SELECT payload FROM events WHERE seq=?',
            (e['save_event']['seq'],)).fetchone()[0]) for e in old_edges]})
    bind(rr.KIND, rr.EVENT, chain['transitions'])
    from .work_coordinator_handoff_post_round_v3_successor_contracts import KIND as CK, OPENED as CE
    from .work_coordinator_handoff_post_round_v3_commands import RECEIPT_KIND as CR, RECEIPT_EVENT as CRE
    bind(CK, CE, [extension['capability']]); bind(CR, CRE, extension['receipt_rows'])
    from . import work_coordinator_handoff_post_round_v3_continuation_contracts as cc
    from . import work_coordinator_handoff_post_round_v3_continuation_receipts as cr
    from . import work_coordinator_handoff_post_round_v3_continuation_proposals as cp
    bind(cc.KIND,cc.OPENED,[extension['continuation']] if extension['continuation'] else [])
    bind(cr.KIND,cr.EVENT,extension['continuation_receipts'])
    bind(cp.KIND,cp.EVENT,extension['proposal_receipts'])
    # Every typed record, including a renamed/nested bare reference, must be
    # exactly one canonical row under its real database kind and primary key.
    for row in db.execute('SELECT * FROM collaboration'):
        value = json.loads(row['data'])
        if not self._belongs(value, item): continue
        if row['id'] != value.get('id') or value not in canonical_rows.get(row['kind'], []):
            raise ValueError('V3完整树存在未知载体、错误数据库键或未重建记录：' + row['kind'])
    for kind, expected in canonical_rows.items():
        self._same(related(kind), expected, 'V3准确记录全集不闭合：' + kind)
    for kind, expected in canonical_events.items():
        self._same([v for _, v in events(kind)], expected, 'V3准确事件全集不闭合：' + kind)
    # These original official task events carry no item/plan in their body.
    # Reconstruct their exact semantics from already-audited runs, tasks,
    # attempts, dispatch bindings and the complete terminal result. They are
    # not a kind allowlist: missing, duplicate, altered or late events fail.
    from .work_coordinator_handoff_post_round_v3_execution_events import execution_events
    task_events = {}
    for frame in [f for f in frames if f['run']['plan_id'] != plan['id']]:
        dispatch = next(v for v in dispatches if v['attempt_id'] == frame['run']['attempt_id'])
        task_events.update(execution_events(self.s, db, self.checkpoints, frame, dispatch))
    task_events.update(extension['task_events'])
    # All workflow proof carriers and attributed original task events must
    # match the exact registry; unrelated payloads cannot hide attribution.
    unknown = []
    for row in db.execute('SELECT * FROM events'):
        value = json.loads(row['payload'])
        if not (self._belongs(value, item) or row['task_id'] in self._task_ids or
                row['attempt_id'] in self._attempt_ids): continue
        if task_events.get((row['kind'], row['seq'])) == (value, row['task_id'], row['attempt_id']): continue
        if row['kind'] in {'attempt_result_saved', 'cooperative_files_checked'}: continue
        if value not in canonical_events.get(row['kind'], []):
            unknown.append(row['kind'])
    if unknown:
        raise ValueError('V3完整树存在改名、嵌套或未重建事件种类：' + ','.join(sorted(set(unknown))))
    return child_versions
