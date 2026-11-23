"""Independent original ancestry through the actual first V3 saved boundary.

This owner generates ancestor inputs before comparing them with stored runs.
It replays real SQL events in order and reads only frozen baseline/output bytes.
The later running catalog owns V3 prefix/runtime replay and final namespace
closure; this module supplies no current execution or attachment permission.
"""
from copy import deepcopy

from .store import digest
from .work_transitions import plan_ref
from .work_coordinator_handoff_records import DELIVERY_KIND, COMPLETION_KIND
from .work_coordinator_handoff_post_round_candidates import ref
from .work_coordinator_handoff_post_round_v3_path import plan_kind
from .work_coordinator_handoff_historical_assets import _json
from .work_coordinator_handoff_historical_replay import HistoricalReplay
from .work_coordinator_handoff_historical_ab import HistoricalAB
from .work_coordinator_handoff_historical_round import HistoricalRound
from .work_coordinator_handoff_historical_saved import HistoricalSaved
from .work_coordinator_handoff_historical_proposals import HistoricalProposals
from .work_coordinator_handoff_historical_capabilities import HistoricalCapabilities
from .work_coordinator_handoff_historical_responsibility import HistoricalResponsibility
from .work_coordinator_handoff_historical_ancestor_values import HistoricalAncestorValues


class HistoricalOriginal:
    def __init__(self, sources, timeline, ledger):
        if timeline.sources is not sources or ledger.sources is not sources or not sources.db.in_transaction:
            raise ValueError('原历史owner须属于本次真实SQL快照及准确账本。')
        self.sources, self.timeline, self.ledger, self.db = sources, timeline, ledger, sources.db
        self.replay = HistoricalReplay(sources, timeline, ledger)
        self.ab = HistoricalAB(sources, timeline, ledger, self.replay.inputs)
        self.proposals = HistoricalProposals(sources, timeline, ledger, self.replay)
        self.round = HistoricalRound(sources, timeline, ledger, self.replay)
        self.saved = HistoricalSaved(sources, timeline, ledger)
        self.capabilities = HistoricalCapabilities(sources, timeline, ledger, self.replay)
        self.responsibility = HistoricalResponsibility(sources, timeline, ledger, self.replay)
        self.edges, self.caps, self.auths = {}, {}, {}
        self.adjustment_edges = []
        self._check_facts = {}
        self._captures = []
        self._global_caps = {'runtime': None, 'adjustment': None}
        self._checkpoint_children = self._children()

    def _children(self):
        """Select actual original checkpoint identities through typed lineage."""
        from . import work_coordinator_handoff_adjustment_records as adjustment
        from . import work_coordinator_handoff_round_records as first
        from . import work_coordinator_handoff_mixed_records as mixed
        from . import work_coordinator_handoff_post_round_saved_records as v1
        from . import work_coordinator_handoff_post_round_v2_saved_records as v2
        from . import work_coordinator_handoff_post_round_v3_saved_records as v3
        codecs = {'ADJUSTMENT': adjustment, 'FIRST_ROUND': first, 'MIXED_ROUND': mixed,
            'POST_V1': v1, 'POST_V2': v2, 'POST_V3': v3}
        result = {}
        for child in self.timeline.plans[1:]:
            kind = plan_kind(child); codec = codecs.get(kind)
            if codec is None:
                raise ValueError('原祖先包含未核实保存codec。')
            field = ('coordinator_file_handoff_lineage' if kind == 'ADJUSTMENT' else
                'coordinator_file_handoff_round_lineage' if kind in {'FIRST_ROUND', 'MIXED_ROUND'} else
                'coordinator_file_handoff_post_round_lineage')
            link = child[field]
            if kind in {'POST_V1', 'POST_V2', 'POST_V3'}:
                identity = link['checkpoint']
            else:
                candidate = self.sources.get(self.db, codec.KIND_FOR[codec.CANDIDATE], link['candidate']['id'])
                if ref(candidate) != link['candidate']:
                    raise ValueError('原保存candidate引用与实际准确版本不符。')
                identity = candidate['snapshot']['checkpoint']
            checkpoint = self.sources.get(self.db, codec.KIND_FOR[codec.CHECKPOINT], identity['id'])
            if ref(checkpoint) != identity:
                raise ValueError('原保存checkpoint引用与实际准确版本不符。')
            event = self.sources._event(self.db, codec.EVENTS[codec.CHECKPOINT], checkpoint)
            if event['seq'] in result:
                raise ValueError('不同原保存边不能共用同一实际checkpoint事件。')
            result[event['seq']] = (child, kind, codec, checkpoint)
        return result

    def _stage(self, plan):
        kind = plan_kind(plan)
        caps = {**self._global_caps, 'round': None, 'post': None, 'successor': None,
            **self.caps.get(plan['id'], {})}
        if kind == 'ROOT':
            stage = 'ROOT_ADJUSTMENT' if caps['adjustment'] is not None else 'ROOT_V2' if caps['runtime'] is not None else 'ROOT_V1'
        elif kind in {'FIRST_ROUND', 'MIXED_ROUND'}:
            stage = 'POST_ROUND' if caps['post'] is not None else kind
        else:
            stage = kind
        return stage, caps

    def _material(self, plan, boundary, *, recorded=None, _text_edge=None):
        """Read only the baseline actually named at this event boundary.

        Captures preserve outputs without writing the project. Consequently an
        output manifest is never a substitute for a project-baseline recording.
        The input's named baseline is an observational leaf, not an input seed.
        """
        root = self.timeline.root['coordinator_file_handoff_root']['project_root']
        names = sorted(plan['file_handoffs'][0]['paths'])
        raw = self.db.execute('SELECT * FROM events WHERE seq=?', (boundary,)).fetchone()
        if raw is None:
            raise ValueError('原材料观察缺少准确实际事件边界。')
        body = _json(raw['payload']); snapshot = body.get('snapshot', {})
        if _text_edge is not None:
            from .work_coordinator_handoff_text_rework_parent import _TypedEdge
            if (type(_text_edge) is not _TypedEdge or _text_edge.require(self.sources, plan) != boundary or
                    recorded != _text_edge.capability['snapshot']['observation']['material_observation']):
                raise ValueError('新父材料只能来自本次实际已核typed边。')
            if recorded['root'] != root or recorded['paths'] != names:
                raise ValueError('新父材料须保持原完整声明路径。')
            self.sources.assets.baseline(recorded)
            return deepcopy(recorded)
        contexts = [body.get('input', {}).get(key, {}) for key in
            ('handoff_coordination_context', 'handoff_unit_context')]
        contexts += [snapshot.get('history', {}), snapshot.get('observation', {}),
            snapshot.get('observation', {}).get('history', {}), snapshot.get('adjustment', {})]
        claims = [context['material_observation'] for context in contexts if 'material_observation' in context]
        if claims:
            if any(value != claims[0] for value in claims) or recorded is not None and recorded != claims[0]:
                raise ValueError('原材料参数必须等于此实际事件唯一命名的完整baseline。')
            recorded = claims[0]
        elif recorded is not None:
            raise ValueError('不能由helper参数为原边界补造未记录的baseline。')
        else:
            # These original events do not observe material. Their algorithms
            # use only the existing frontier/phase/selection; carry an already
            # proved value without claiming a new project observation.
            from .work_coordinator_handoff_round_runtime import OPENED as first_opened
            from .work_coordinator_handoff_mixed_runtime import OPENED as mixed_opened
            if raw['kind'] not in {'coordinator_adjustment_opened', 'coordinator_proposal_checked', first_opened, mixed_opened}:
                raise ValueError('该原边界缺少自己的准确材料观察。')
            previous = [(self.sources.run(identity)[-1]['seq'], frozen) for identity, frozen in self.replay.inputs.items()
                if self.sources.run(identity)[-1]['seq'] < boundary]
            if previous:
                frozen = max(previous, key=lambda value: value[0])[1]
            else:
                # Before the first run, only that run's explicitly named
                # initial baseline is available. No input/dependency is seeded.
                runs = [r for r in self.sources.all(self.db, 'work_unit_run') if r.get('item_id') == plan['item_id']]
                runs.sort(key=lambda r: self.sources.run(r['id'])[-1]['seq'])
                if not runs or plan != self.timeline.root:
                    raise ValueError('原历史缺少可命名的初始冻结baseline。')
                frozen = runs[0]['input']
            context = frozen.get('handoff_coordination_context') or frozen.get('handoff_unit_context')
            recorded = context['material_observation']
        if recorded['root'] != root or recorded['paths'] != names:
            raise ValueError('历史材料观察必须保持原根及完整声明路径。')
        self.sources.assets.baseline(recorded)
        return deepcopy(recorded)

    def values(self, plan, boundary, *, material=None, stage=None, _text_edge=None):
        actual_stage, caps = self._stage(plan)
        auth = self.auths[plan['id']]
        if plan == self.timeline.root:
            auth_event = self.sources._event(self.db, 'cooperation_authorized', auth)
        else:
            saved_edge = self.edges[plan['id']]
            events = saved_edge.get('original', saved_edge).get('events')
            if events is None:
                # Adjustment stores its exact consumption event in the ledger;
                # its saved command already owns the original transferred auth.
                consumption = saved_edge['consumption']
                from .work_coordinator_handoff_adjustment_records import EVENTS, CONSUMPTION
                auth_event = self.sources._event(self.db, EVENTS[CONSUMPTION], consumption)
            else:
                auth_event = events['registration_event']
        if auth_event['seq'] >= boundary:
            raise ValueError('原值边界不能使用尚未实际授权或保存转移的未来授权。')
        frames = self.replay.frames_before(boundary)
        records = self.ab.prove_before(boundary)
        saved = self.edges.get(plan['id'])
        ready = saved if saved and actual_stage in {'FIRST_ROUND', 'MIXED_ROUND', 'POST_ROUND', 'POST_V1', 'POST_V2'} else None
        return HistoricalAncestorValues(self.sources, self.timeline, plan, boundary,
            stage=stage or actual_stage, frames=frames, records=records,
            material=self._material(plan, boundary, recorded=material, _text_edge=_text_edge), authorization=auth,
            capabilities=caps, saved=ready, adjustment_edges=self.adjustment_edges)

    def _save(self, seq, child, kind, codec, checkpoint):
        parent = self.timeline.plans[self.timeline.plans.index(child) - 1]
        cp = checkpoint['snapshot']
        material = (cp['observation']['history']['material_observation'] if 'observation' in cp else
            cp['history']['material_observation'])
        values = self.values(parent, seq, material=material)
        if kind in {'FIRST_ROUND', 'MIXED_ROUND'}:
            saved = self.round.saved(values, child)
        else:
            proposal = self.proposals.previous(seq)
            if proposal is None or proposal['id'] not in self._check_facts:
                raise ValueError('原保存边缺少完整独立原提案检查。')
            if kind == 'ADJUSTMENT':
                saved = self.saved.adjustment(values, child, proposal, ancestor_edges=self.adjustment_edges)
                self.adjustment_edges.append(saved['edge'])
            else:
                if proposal['snapshot']['plan'] != plan_ref(parent):
                    raise ValueError('POST保存只能消费准确原父方案提案。')
                facts = {**self._check_facts[proposal['id']],
                    'history': values.history(bounded_parent=kind in {'POST_V2', 'POST_V3'})}
                candidate_rows = [row for row in self.sources.all(self.db, codec.KIND_FOR[codec.CANDIDATE])
                    if row.get('snapshot', {}).get('checkpoint') == ref(checkpoint)]
                if len(candidate_rows) != 1:
                    raise ValueError('POST原checkpoint须有唯一准确candidate。')
                saved = self.saved.post(kind, checkpoint, candidate_rows[0], facts,
                    proved_ancestor_edges=self.edges)
                # Generic history fields omit these values; reconstruct them
                # explicitly for the first V3 owner rather than reading them
                # back from the checkpoint's own history.
                saved['original'].update(root=plan_ref(self.timeline.root),
                    phase=values._phase(parent, values.frames, values.records),
                    frames=deepcopy(values.frames), material_observation=deepcopy(values.material))
        self.edges[child['id']] = saved
        initial = saved.get('original', saved)
        self.auths[child['id']] = initial['authorization']
        self.responsibility.saved_start(saved)
        _, parent_caps = self._stage(parent)
        self.caps[child['id']] = ({name: parent_caps[name] for name in ('round', 'post')}
            if kind in {'POST_V1', 'POST_V2', 'POST_V3'} else {})
        return saved

    def read_original(self):
        """Rebuild every actual ancestor up to this V3's saved registration."""
        return self._read_prefix()

    def read_before_text_edge(self, edge):
        """Private non-V3 parent prefix; the enclosing new owner closes all SQL.

        The actual new capability determines the boundary. No integer cutoff,
        stored successful Current or filtered database can select a prefix.
        V3 runtime replay has its own semantic owner after read_original().
        """
        from .work_coordinator_handoff_text_rework_parent import _TypedEdge
        if type(edge) is not _TypedEdge or plan_kind(self.timeline.plans[-1]) == 'POST_V3':
            raise ValueError('文字返工原祖先前缀须为准确已核新边及非V3父。')
        edge.require(self.sources, self.timeline.plans[-1])
        return self._read_prefix(edge)

    def _read_prefix(self, edge=None):
        from . import work_coordinator_handoff_runtime as root
        from . import work_coordinator_handoff_adjustment_runtime as adjustment
        from . import work_coordinator_handoff_round_runtime as first
        from . import work_coordinator_handoff_mixed_runtime as mixed
        from . import work_coordinator_handoff_post_round_contracts as post
        from . import work_coordinator_handoff_post_round_successor_contracts as v1
        from . import work_coordinator_handoff_post_round_v2_run_contracts as v2
        leaf = self.timeline.plans[-1]
        boundary = edge.require(self.sources, leaf) if edge is not None else None
        if edge is None and plan_kind(leaf) != 'POST_V3':
            raise ValueError('此原历史owner只收敛到准确V3实际保存边。')
        self.responsibility.root()
        self.auths[self.timeline.root['id']] = self.responsibility.authorize()
        opened = {'coordinator_runtime_opened': ('runtime', root.CAPABILITY_KIND),
            'coordinator_adjustment_opened': ('adjustment', adjustment.KIND),
            first.OPENED: ('round', first.KIND), mixed.OPENED: ('round', mixed.KIND),
            post.OPENED: ('post', post.KIND), v1.OPENED: ('successor', v1.KIND), v2.OPENED: ('successor', v2.KIND)}
        ancestor_ids = {p['id'] for p in (self.timeline.plans if edge is not None else self.timeline.plans[:-1])}
        for raw in self.db.execute('SELECT * FROM events ORDER BY seq'):
            seq, kind, body = raw['seq'], raw['kind'], _json(raw['payload'])
            if boundary is not None and seq >= boundary:
                break
            if seq in self._checkpoint_children:
                child, child_kind, codec, checkpoint = self._checkpoint_children[seq]
                saved = self._save(seq, child, child_kind, codec, checkpoint)
                if child == leaf and edge is None:
                    self.responsibility.finalize_commands()
                    self.sources.assets.recheck()
                    return deepcopy(saved['original'])
            elif kind in opened and body.get('item_id') == self.timeline.item_id:
                name, carrier_kind = opened[kind]
                if self.sources.get(self.db, carrier_kind, body['id']) != body:
                    raise ValueError('开启事件须等于准确原typed能力行。')
                if name == 'runtime':
                    self._global_caps[name] = self.capabilities.root(body)
                    continue
                if name == 'adjustment':
                    plan = self.timeline.root; material = None
                else:
                    identity = body['snapshot'].get('plan') or body['snapshot']['observation']['plan']
                    plan = self.timeline.get_ref('teamwork_plan', identity)
                    material = body['snapshot'].get('observation', {}).get('material_observation')
                if plan['id'] not in ancestor_ids:
                    raise ValueError('原保存之前不能出现V3或未知方案的开启。')
                values = self.values(plan, seq, material=material)
                if name == 'adjustment':
                    self._global_caps[name] = self.capabilities.adjustment(values, body)
                elif name == 'round':
                    self.caps.setdefault(plan['id'], {})[name] = self.capabilities.round(values, body, self.edges[plan['id']])
                else:
                    self.caps.setdefault(plan['id'], {})[name] = self.capabilities.post(values, body,
                        saved=self.edges.get(plan['id']))
            elif kind == 'cooperative_unit_reserved' and body.get('item_id') == self.timeline.item_id:
                plan = self.timeline.get_ref('teamwork_plan', {'id': body['plan_id'], 'version': body['plan_version']})
                if plan['id'] not in ancestor_ids:
                    raise ValueError('原保存之前出现非祖先运行。')
                values = self.values(plan, seq)
                proposal = self.proposals.consume(values, body)
                deliveries = [r for r, _ in self.ab._records[DELIVERY_KIND] if r['snapshot']['run_id'] == body['id']]
                if len(deliveries) > 1:
                    raise ValueError('原B保留只能对应唯一正式交付。')
                self.replay.advance(body, values, proposal=proposal, delivery=deliveries[0] if deliveries else None)
            elif kind == 'coordinator_proposal_checked' and body.get('item_id') == self.timeline.item_id:
                plan = self.timeline.get_ref('teamwork_plan', body['snapshot']['plan'])
                material = body['snapshot'].get('adjustment', {}).get('material_observation')
                self._check_facts[body['id']] = self.proposals.check(self.values(plan, seq, material=material), body)
            elif kind == 'cooperative_files_checked' and raw['task_id'] in self.ledger.tasks:
                runs = [r for r in self.replay.runs.values() if r['task_id'] == raw['task_id'] and r['attempt_id'] == raw['attempt_id']]
                if len(runs) != 1:
                    raise ValueError('原捕获须有唯一已重建运行。')
                run = runs[0]; proof = self.sources.exited(run)
                files = self.sources.files(run, proof, self.sources.raw_evidence(run, proof), self.replay.inputs[run['id']]['dependencies'])
                captures = [c for c in self.sources.captures(run, proof, files) if c['seq'] == seq]
                if len(captures) != 1:
                    raise ValueError('原捕获须定位准确实际发生项。')
                self.ab.capture(run, captures[0]); self._captures.append({**captures[0], 'run': run})
                self.ledger.row('work_unit_artifact', self.sources.get(self.db, 'work_unit_artifact', body['artifact']['id']), 'run:' + run['id'])
            elif kind.startswith('work_driver_') and body.get('item_id') == self.timeline.item_id:
                plan = next((p for p in self.timeline.plans if p['id'] == body['plan_id']), None)
                if plan is None:
                    raise ValueError('原责任事件引用未重建方案。')
                driver = {k: v for k, v in body.items() if k != 'no_dispatch'}
                event, actual = next((e, d) for e, d in self.timeline.driver_versions(plan) if e['seq'] == seq)
                if actual != driver:
                    raise ValueError('原责任事件与准确完整版本不符。')
                if seq not in self.ledger.events:
                    self.responsibility.transition(plan, event, driver)
        if edge is not None:
            self.responsibility.finalize_commands()
            for run in self.replay.runs.values():
                if self.sources.exited(run)['exited']['seq'] >= boundary:
                    raise ValueError('返工边之前的原尝试须已准确退出。')
                self.ab.captures(run)
                # Later data remains in the same actual database. An old task
                # cannot acquire a new execution/capture event behind the edge.
                if self.db.execute('SELECT 1 FROM events WHERE seq>=? AND (task_id=? OR attempt_id=?)',
                        (boundary, run['task_id'], run['attempt_id'])).fetchone():
                    raise ValueError('返工边之后出现旧任务的迟到事件。')
            values = self.values(leaf, boundary,
                material=edge.capability['snapshot']['observation']['material_observation'], _text_edge=edge)
            history = values.history()
            self.sources.assets.recheck()
            edge.require(self.sources, leaf)
            return {'history': history, 'values': values, 'owner': self}
        raise ValueError('原历史缺少准确V3保存边界。')
