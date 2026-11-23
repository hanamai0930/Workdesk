"""Independent ROUND checkpoint observation values from the actual prefix.

The original owner must first admit all run inputs, checks, captures and A/B
records. No stored checkpoint observation is an input to this reconstruction.
Saved application/registration and command proofs remain separate owners.
"""
from copy import deepcopy

from .store import digest
from .work_transitions import plan_ref, source_ref
from .work_coordinator_handoff_records import PERMISSIONS, versioned
from .work_coordinator_handoff_adjustments import _require_result
from .work_coordinator_handoff_post_round_candidates import ref
from .work_coordinator_handoff_historical_parent import event_value
from .work_coordinator_handoff_historical_assets import _json
from .work_coordinator_handoff_context import WorkCoordinatorHandoffContext
from types import SimpleNamespace


class HistoricalRound:
    def __init__(self, sources, timeline, ledger, replay):
        if (timeline.sources is not sources or ledger.sources is not sources or
                replay.sources is not sources or not sources.db.in_transaction):
            raise ValueError('ROUND观察须属于本次准确来源、重放及读取事务。')
        self.sources, self.timeline, self.ledger, self.replay = sources, timeline, ledger, replay
        self.db = sources.db

    def saved(self, values, child):
        """Rebuild an actual ROUND save, including its original full receipt.

        The enclosing owner admits the boundary's drivers, capabilities, input
        and A/B records. The observation itself is generated here, never supplied
        from a checkpoint or from the saved command's receipt.
        """
        from .work_coordinator_handoff_historical_ancestor_values import HistoricalAncestorValues
        parent = values.plan
        if (child not in self.timeline.plans or self.timeline.plans.index(child) == 0 or
                self.timeline.plans[self.timeline.plans.index(child) - 1] != parent):
            raise ValueError('ROUND保存只能消费本次时间线准确相邻父子。')
        mixed = child['coordinator_file_handoff_round_lineage']['protocol'] == 'COORDINATOR_FILE_HANDOFF_MIXED_ROUND_LINEAGE_V1'
        if mixed:
            from . import work_coordinator_handoff_mixed_records as r
            from . import work_coordinator_handoff_mixed_contracts as contracts
            from . import work_coordinator_handoff_mixed_apply as apply
            command_kind, child_kind = 'internal.mixed.save', 'MIXED_ROUND'
        else:
            from . import work_coordinator_handoff_round_records as r
            from . import work_coordinator_handoff_round_contracts as contracts
            from . import work_coordinator_handoff_round_apply as apply
            command_kind, child_kind = 'internal.round.save', 'FIRST_ROUND'
        def get(protocol, identity):
            row = self.sources.get(self.db, r.KIND_FOR[protocol], identity['id'])
            r.validate_row(r.KIND_FOR[protocol], row)
            if ref(row) != identity:
                raise ValueError('ROUND完整typed引用与准确数据库版本不符。')
            return row
        candidate = get(r.CANDIDATE, child['coordinator_file_handoff_round_lineage']['candidate'])
        checkpoint = get(r.CHECKPOINT, candidate['snapshot']['checkpoint'])
        cp, body = checkpoint['snapshot'], candidate['snapshot']
        cutoff = values.boundary - 1
        observation = self.observation(values, child_kind=child_kind, cutoff=cutoff)
        reason = cp['reason']
        common = {'item_id': parent['item_id'], 'root': observation['root'],
            'parent_plan': plan_ref(parent), 'parent_path': r.parent_ref(observation),
            'source': observation['source'], 'selected_unit': observation['selected_unit'],
            'reason': reason, 'operation': r.OPERATION, 'scope': r.SCOPE, **PERMISSIONS}
        expected_cp = versioned({**common, 'protocol': r.CHECKPOINT, 'plan': parent,
            'observation': observation})
        answer = next(f for f in values.frames if f['run']['id'] == observation['source']['run_id'])['source_evidence']['answer']
        expected_candidate = versioned({**common, 'protocol': r.CANDIDATE,
            'checkpoint': ref(checkpoint), 'child_seed': body['child_seed'],
            'carry_sources': observation['carry_sources'],
            'historical_coordinators': observation['historical_coordinators'],
            'coordination_revision': {'id': body['coordination_revision']['id'],
                'item_id': parent['item_id'], 'unit_key': observation['selected_unit']['definition']['key'],
                'source': observation['source'], 'reason': reason, 'prior_check': observation['prior_check'],
                'answer': answer if len(answer) <= 32000 else None,
                'answer_chars': len(answer), 'answer_digest': digest(answer)},
            'transition': {'kind': r.OPERATION, 'source': observation['source'], 'reason': reason,
                'usage_at_transition': observation['usage'],
                'authorization_from': {k: observation['history']['authorization'][k] for k in ('id', 'revision')},
                'command_id': candidate['command_id'], 'no_dispatch': True}})
        if (cp != expected_cp or body != expected_candidate or
                checkpoint['command_id'] != candidate['command_id'] or contracts.prepared(checkpoint, candidate) != child):
            raise ValueError('ROUND准备须等于独立完整父观察及原候选算法。')
        # A source can register only one candidate in this original codec.
        related = [row for row in self.sources.all(self.db, r.KIND_FOR[r.CANDIDATE])
            if row.get('snapshot', {}).get('source', {}).get('run_id') == observation['source']['run_id']]
        if related != [candidate]:
            raise ValueError('ROUND原协调来源须有唯一准确候选。')
        def one(protocol, key):
            found = [row for row in self.sources.all(self.db, r.KIND_FOR[protocol])
                if row.get('snapshot', {}).get(key) == plan_ref(child)]
            if len(found) != 1:
                raise ValueError('ROUND保存须有唯一准确应用与登记。')
            return get(protocol, ref(found[0]))
        app, reg = one(r.APPLICATION, 'child_plan'), one(r.REGISTRATION, 'to_plan')
        service = SimpleNamespace(collaboration=self.sources,
            work_drivers=SimpleNamespace(reference=lambda v: {k: v[k] for k in ('id', 'generation', 'revision')}),
            work_units=SimpleNamespace(auth_ref=lambda v: {k: v[k] for k in ('id', 'revision')}))
        old = WorkCoordinatorHandoffContext(service)
        driver = old._driver(self.db, child, app['snapshot']['driver_after'])
        auth = old._auth(self.db, child, app['snapshot']['authorization_after'])
        h = observation['history']
        contracts.adjacent(checkpoint, candidate, child, h['authorization'], auth, h['driver'], driver)
        de, first = self.timeline.driver_versions(child)[0]
        driver_event = 'work_driver_coordinator_handoff_' + ('mixed_round_revised' if mixed else 'round_revised')
        if first != driver or de['kind'] != driver_event:
            raise ValueError('ROUND最初责任须为原保存转移的准确暂停版本。')
        payload = apply.save_payload(service, candidate, child, driver, auth)
        events = {'checkpoint_event': self.sources._event(self.db, r.EVENTS[r.CHECKPOINT], checkpoint),
            'candidate_event': self.sources._event(self.db, r.EVENTS[r.CANDIDATE], candidate),
            'driver_event': de, 'save_event': self.sources._event(self.db, r.SAVE_EVENT, payload),
            'application_event': self.sources._event(self.db, r.EVENTS[r.APPLICATION], app),
            'registration_event': self.sources._event(self.db, r.EVENTS[r.REGISTRATION], reg)}
        ev = {key: event_value(value) for key, value in events.items()}
        seqs = [e['seq'] for e in events.values()]
        if (apply.bodies(service, checkpoint, candidate, child, h['driver'], driver,
                h['authorization'], auth, ev['save_event']) != (app['snapshot'], reg['snapshot']) or
                seqs != sorted(set(seqs)) or seqs[0] != cutoff + 1 or
                any(row['command_id'] != candidate['command_id'] for row in (app, reg)) or
                not checkpoint['created'] <= candidate['created'] <= auth['transferred_at'] <=
                    driver['updated'] <= app['created'] <= reg['created'] < auth['dispatch_before'] or
                any(row['created'] > events[name]['created'] for name, row in
                    (('checkpoint_event', checkpoint), ('candidate_event', candidate),
                     ('application_event', app), ('registration_event', reg)))):
            raise ValueError('ROUND保存原四元组、转移责任、事件及时间未闭合。')
        edge = {'parent_plan': plan_ref(parent), 'child_plan': plan_ref(child),
            'checkpoint': ref(checkpoint), 'candidate': ref(candidate), 'application': ref(app),
            'registration': ref(reg), 'save_event': ev['save_event'],
            'registration_event': ev['registration_event']}
        ready = {'edge': edge, 'observation': observation}
        if mixed:
            # No child run exists inside the original save. Generate that
            # boundary from the admitted parent prefix and initial transferred
            # responsibility; retain the original Mixed read flags verbatim.
            child_values = HistoricalAncestorValues(self.sources, self.timeline, child,
                events['registration_event']['seq'], stage=child_kind, frames=values.frames,
                records=values.records, material=values.material, authorization=auth,
                capabilities={**values._caps, 'round': None}, saved=ready)
            receipt = child_values.history()
            for key in ('post_round_capability', 'successor_capability'):
                receipt.pop(key, None)
            from .work_coordinator_handoff_mixed_runtime import SCOPE
            receipt.update(full_mixed_path_verified=True, successor_layers_verified=True,
                verified_scope=SCOPE, public_apply=False)
            path = receipt['saved_path']
        else:
            from .work_coordinator_handoff_round_path import validate_path, PATH_SCOPE
            path = validate_path(versioned({'protocol': r.SAVED_PATH, 'root': observation['root'],
                'plan': plan_ref(child), 'parent_kind': observation['parent_kind'],
                'parent_path': observation['saved_path'],
                'edge': {key: value for key, value in edge.items() if key != 'registration_event'},
                'history_frontier': h['frontier'], 'material_observation': values.material,
                'usage': observation['usage'], 'original_dispatch_before': observation['original_dispatch_before'],
                'scope': PATH_SCOPE, **PERMISSIONS}))
            receipt = {'root': observation['root'], 'plan': child, 'saved_path': path,
                'candidate': candidate, 'checkpoint': checkpoint, 'application': app,
                'registration': reg, 'driver': driver, 'authorization': auth, 'frames': values.frames,
                'history': h, 'usage': observation['usage'], 'current_material_observation': values.material,
                'saved_edge_verified': True, 'successor_layers_verified': False,
                'verified_scope': PATH_SCOPE, 'public_apply': False, **PERMISSIONS}
        request = {'plan': parent['id'] if mixed else plan_ref(parent), 'reason': reason}
        command = self.db.execute('SELECT kind FROM commands WHERE id=?', (candidate['command_id'],)).fetchone()
        if command is not None and command['kind'] == 'work-item.coordination-revise':
            # The public writer invokes the same atomic save, then returns its
            # original compact receipt. It is a distinct command codec.
            command_kind = command['kind']
            request = {'id': parent['item_id'], 'revision': h['task']['revision'],
                'current_driver': self.s_driver_ref(h['driver']), 'plan': plan_ref(parent),
                'authorization': {k: h['authorization'][k] for k in ('id', 'revision')},
                'frontier': digest([{'run_id': f['run']['id'], 'evidence': f['evidence']['version']} for f in values.frames]),
                'run_id': observation['source']['run_id'], 'result_version': observation['source']['version'], 'reason': reason}
            receipt = {'coordination_revision': child['coordination_revision'], 'plan': plan_ref(child),
                'driver': self.s_driver_ref(driver), 'authorization': {k: auth[k] for k in ('id', 'revision')},
                'registration': path['edge']['registration'], 'no_dispatch': True, 'automatically_resumed': False}
        owner = 'saved:' + candidate['id']
        self.ledger.command(candidate['command_id'], command_kind, request, receipt, owner,
            not_before=max(reg['created'], events['registration_event']['created']))
        for protocol, row in ((r.CHECKPOINT, checkpoint), (r.CANDIDATE, candidate),
                (r.APPLICATION, app), (r.REGISTRATION, reg)):
            self.ledger.row(r.KIND_FOR[protocol], row, owner)
            self.ledger.event(r.EVENTS[protocol], row, owner)
        self.ledger.row('teamwork_plan', child, owner)
        self.ledger.row('work_driver_revision', {**driver, 'id': 'driver-version:' + driver['revision'],
            'driver_id': driver['id']}, owner)
        self.ledger.row('work_unit_authorization_revision', {**auth, 'id': 'unit-auth-version:' + auth['revision']}, owner)
        self.ledger.event(driver_event, {**driver, 'no_dispatch': True}, owner)
        self.ledger.event(r.SAVE_EVENT, payload, owner)
        return {'child_plan': deepcopy(child), 'edge': edge, 'cutoff': cutoff,
            'checkpoint': checkpoint, 'candidate': candidate, 'application': app, 'registration': reg,
            'driver': driver, 'authorization': auth, 'saved_path': path, 'observation': observation,
            'frames': deepcopy(values.frames), 'events': ev, 'receipt': deepcopy(receipt)}

    @staticmethod
    def s_driver_ref(driver):
        return {k: driver[k] for k in ('id', 'generation', 'revision')}

    def checks(self, cutoff):
        checks, rows = [], {}
        for raw in self.db.execute("SELECT * FROM events WHERE kind='coordinator_proposal_checked' AND seq<=? ORDER BY seq", (cutoff,)):
            value = _json(raw['payload'])
            if value.get('item_id') != self.timeline.item_id:
                continue
            saved = self.ledger.rows.get(('work_proposal', value['id']))
            event = self.ledger.events.get(raw['seq'])
            if (saved is None or saved[0] != value or event is None or
                    event[0] != (raw['kind'], value, raw['task_id'], raw['attempt_id']) or value['id'] in rows):
                raise ValueError('ROUND历史检查须由原提案owner完整独立重建后消费。')
            checks.append({'reference': ref(value), 'state': value['snapshot']['state'],
                'event': {'seq': raw['seq'], 'kind': raw['kind'], 'payload_digest': digest(value)}})
            rows[value['id']] = value
        return checks, rows

    def observation(self, values, *, child_kind, cutoff):
        plan = values.plan
        if (values.sources is not self.sources or values.timeline is not self.timeline or
                values.boundary != cutoff + 1 or values.frames != self.replay.frames_before(cutoff + 1)):
            raise ValueError('ROUND观察须消费原检查点前完整准确重放。')
        if plan == self.timeline.root or values.stage == 'ADJUSTMENT':
            if child_kind != 'FIRST_ROUND':
                raise ValueError('原根和调整父只能形成首条明确ROUND边。')
            from . import work_coordinator_handoff_round_records as codec
            protocol, scope, history_fields = codec.PARENT, codec.PARENT_SCOPE, codec.HISTORY
            parent_kind = 'ROOT' if plan == self.timeline.root else 'ADJUSTMENT'
        elif values.stage == 'FIRST_ROUND':
            if child_kind != 'MIXED_ROUND':
                raise ValueError('首ROUND后须使用实际Mixed保存codec。')
            from . import work_coordinator_handoff_round_parent_checkpoint as codec
            protocol, scope, history_fields = codec.PARENT, codec.SCOPE, codec.HISTORY_FIELDS
            parent_kind = 'ROUND'
        elif values.stage == 'MIXED_ROUND':
            if child_kind != 'MIXED_ROUND':
                raise ValueError('Mixed父的明确回合须保持实际Mixedcodec。')
            from . import work_coordinator_handoff_mixed_parent as codec
            protocol, scope, history_fields = codec.PARENT, codec.SCOPE, codec.HISTORY_FIELDS
            parent_kind = 'ROUND'
        else:
            raise ValueError('ROUND观察不能解释POST或未知父codec。')
        h = values.history()
        candidates = [f for f in h['current_frames'] if f['run']['plan_id'] == plan['id'] and
            f['run']['generation'] == h['driver']['generation'] and f['run']['input']['unit']['role'] == 'coordinate']
        if not candidates:
            raise ValueError('ROUND观察缺少原方案当前generation的准确协调来源。')
        selected = candidates[-1]; run, full = selected['run'], selected['source_evidence']
        unit = self.replay.inputs[run['id']]['unit']
        if (h['driver']['state'] != 'RESERVED' or full['state'] != 'COMPLETED' or
                not full['exact_identity'] or not full['result_available'] or full['blockers'] or
                not isinstance(full['answer'], str) or not full['answer'] or
                unit['member']['reference'] != plan['coordinator']['reference']):
            raise ValueError('ROUND原来源须有准确责任、原协调定义和完整已知退出。')
        checks, rows = self.checks(cutoff)
        prior, prior_event = None, None
        if checks:
            latest = rows[checks[-1]['reference']['id']]
            body = latest['snapshot']
            if body['source']['run_id'] == run['id']:
                if body['source']['version'] != full['version'] or body['plan'] != plan_ref(plan):
                    raise ValueError('ROUND同版本原检查来源不一致。')
                prior = {'reference': ref(latest), 'state': body['state'], 'problems': body['problems']}
                prior_event = checks[-1]['event']
        carry = {}
        current = {f['run']['unit_key']: f for f in h['current_frames']}
        retired = plan['file_handoffs'][0]['source_unit'] if h['phase'] == 'B_COMPLETED' else None
        for ordinary in plan['units']:
            key = ordinary['key']
            if ordinary['role'] == 'coordinate' or key == retired or key not in current:
                continue
            frame = current[key]; evidence = frame['source_evidence']
            if frame['run']['input']['unit'] != ordinary or evidence['state'] != 'COMPLETED' or not evidence['exact_identity']:
                raise ValueError('ROUND沿用成果须保持准确原普通定义。')
            _require_result(evidence, ordinary)
            carry[key] = source_ref(frame['run'], evidence)
        historical = set(plan.get('historical_coordinators', [])) | {
            f['run']['unit_key'] for f in h['frames'] if f['run']['input']['unit']['role'] == 'coordinate'}
        historical.discard(unit['key'])
        events = []
        for frame in h['frames']:
            original = frame['run']; proof = self.sources.exited(original)
            captures = []
            for raw in self.db.execute("SELECT * FROM events WHERE task_id=? AND attempt_id=? AND kind='cooperative_files_checked' AND seq<=? ORDER BY seq",
                    (original['task_id'], original['attempt_id'], cutoff)):
                body = _json(raw['payload']); admitted = self.ledger.events.get(raw['seq'])
                if admitted is None or admitted[0] != (raw['kind'], body, raw['task_id'], raw['attempt_id']):
                    raise ValueError('ROUND不能吞掉未按原fullreceipt重建的捕获事件。')
                captures.append({'seq': raw['seq'], 'kind': raw['kind'], 'payload_digest': digest(body)})
            events.append({'run_id': original['id'], 'reservation': event_value(proof['reserved']),
                'result': event_value(proof['exited']), 'captures': captures})
        return codec.validate_parent(versioned({'protocol': protocol, 'item_id': plan['item_id'],
            'root': h['root'], 'plan': plan_ref(plan), 'parent_kind': parent_kind,
            'saved_path': deepcopy(h.get('saved_path')), 'history': {key: deepcopy(h[key]) for key in history_fields},
            'source': source_ref(run, full), 'selected_unit': {'definition': unit, 'revision': digest(unit)},
            'prior_check': prior, 'carry_sources': carry, 'historical_coordinators': sorted(historical),
            'usage': h['usage'], 'original_dispatch_before': h['authorization']['dispatch_before'],
            'events': {'cutoff': cutoff, 'runs': events, 'checks': checks, 'prior_check': prior_event},
            'scope': scope, **PERMISSIONS}))
