"""Exact POST saved-edge reconstruction over admitted ancestor history.

The independent ancestry owner must supply the original complete history and
previously proved edges. This component owns the actual four typed carriers,
initial transferred driver/auth, save events, seven-field request and committed
full receipt. It never invokes a runtime reader or the PAUSED-only namespace.
"""
from copy import deepcopy
from types import SimpleNamespace

from .store import digest
from .work_transitions import plan_ref
from .work_coordinator_handoff_context import WorkCoordinatorHandoffContext
from .work_coordinator_handoff_records import versioned, PERMISSIONS
from .work_coordinator_handoff_post_round_candidates import ref
from .work_coordinator_handoff_historical_parent import event_value


class HistoricalSaved:
    def __init__(self, sources, timeline, ledger):
        if timeline.sources is not sources or ledger.sources is not sources or not sources.db.in_transaction:
            raise ValueError('保存边须属于本次准确历史来源和事务。')
        self.sources, self.timeline, self.ledger, self.db = sources, timeline, ledger, sources.db
        self.s = SimpleNamespace(collaboration=sources,
            work_drivers=SimpleNamespace(reference=lambda value: {k: value[k] for k in ('id', 'generation', 'revision')}),
            work_units=SimpleNamespace(auth_ref=lambda value: {k: value[k] for k in ('id', 'revision')}),
            work_proposals=SimpleNamespace(reference=ref))

    def adjustment(self, values, child, proposal, *, ancestor_edges):
        """Actual original dependency-adjustment save and its full response."""
        from . import work_coordinator_handoff_adjustment_records as r
        from .work_coordinator_handoff_adjustment_contracts import adjacent
        from .work_coordinator_handoff_adjustments import rebuild_preview, proposal_state
        from .work_coordinator_handoff_adjustment_candidates import WorkCoordinatorHandoffAdjustmentCandidates as Values
        from .work_coordinator_handoff_adjustment_apply import WorkCoordinatorHandoffAdjustmentApplications as Apply, SAVE_EVENT
        from .work_coordinator_handoff_historical_ancestor_values import HistoricalAncestorValues
        parent = values.plan
        if (child not in self.timeline.plans or self.timeline.plans.index(child) == 0 or
                self.timeline.plans[self.timeline.plans.index(child) - 1] != parent or
                not proposal_state(proposal['snapshot'])):
            raise ValueError('原调整保存须属于准确相邻父子及已证明调整提案。')
        admitted = self.ledger.rows.get(('work_proposal', proposal['id']))
        if admitted is None or admitted[0] != proposal:
            raise ValueError('原调整保存不能接受未独立重建的提案。')
        def get(protocol, identity):
            row = self.sources.get(self.db, r.KIND_FOR[protocol], identity['id'])
            r.validate_row(r.KIND_FOR[protocol], row)
            if ref(row) != identity:
                raise ValueError('原调整记录须为实际准确typed引用。')
            return row
        candidate = get(r.CANDIDATE, child['coordinator_file_handoff_lineage']['candidate'])
        body = candidate['snapshot']; cp = get(r.CHECKPOINT, body['checkpoint'])
        preview_row = get(r.PREVIEW, body['preview'])
        h = values.history()
        source = next(f for f in values.frames if f['run']['id'] == proposal['snapshot']['source']['run_id'])
        preview = rebuild_preview(parent, h, source['run'], proposal['snapshot']['decision'], observed_at=proposal['created'])
        facts = {'item': self.timeline.business_before(values.boundary), 'plan': parent,
            'history': h, 'proposal': proposal, 'preview': preview}
        codec = Values(self.s)
        if (proposal['snapshot']['adjustment'] != preview or preview_row['snapshot'] != preview or
                cp['snapshot'] != codec._checkpoint(facts) or
                body != codec._candidate(facts, cp, preview_row, body['child_seed'], candidate['command_id']) or
                any(row['command_id'] != candidate['command_id'] for row in (cp, preview_row))):
            raise ValueError('原调整准备须逐项等于独立完整历史和原预览算法。')
        related = [row for row in self.sources.all(self.db, r.KIND_FOR[r.CANDIDATE])
            if row.get('snapshot', {}).get('proposal') == ref(proposal)]
        if related != [candidate]:
            raise ValueError('每个原调整提案须唯一准备及消费。')
        def one(protocol, key):
            found = [row for row in self.sources.all(self.db, r.KIND_FOR[protocol])
                if row.get('snapshot', {}).get(key) == plan_ref(child)]
            if len(found) != 1:
                raise ValueError('原调整边须有唯一完整应用和消费。')
            return get(protocol, ref(found[0]))
        app, consumption = one(r.APPLICATION, 'child_plan'), one(r.CONSUMPTION, 'to_plan')
        context = WorkCoordinatorHandoffContext(self.s)
        driver = context._driver(self.db, child, app['snapshot']['driver_after'])
        auth = context._auth(self.db, child, app['snapshot']['authorization_after'])
        adjacent(parent, child, candidate, h['authorization'], auth, h['driver'], driver)
        driver_event, first = self.timeline.driver_versions(child)[0]
        if first != driver or driver_event['kind'] != 'work_driver_coordinator_handoff_adjusted':
            raise ValueError('原调整责任须起于准确暂停转移。')
        payload = {'root': body['root'], 'parent_plan': plan_ref(parent), 'child_plan': plan_ref(child),
            'candidate': ref(candidate), 'driver': self.s.work_drivers.reference(driver),
            'authorization': self.s.work_units.auth_ref(auth), 'command_id': candidate['command_id'], 'no_dispatch': True}
        events = {'preview_event': self.sources._event(self.db, r.EVENTS[r.PREVIEW], preview_row),
            'checkpoint_event': self.sources._event(self.db, r.EVENTS[r.CHECKPOINT], cp),
            'candidate_event': self.sources._event(self.db, r.EVENTS[r.CANDIDATE], candidate),
            'driver_event': driver_event, 'save_event': self.sources._event(self.db, SAVE_EVENT, payload),
            'application_event': self.sources._event(self.db, r.EVENTS[r.APPLICATION], app),
            'consumption_event': self.sources._event(self.db, r.EVENTS[r.CONSUMPTION], consumption)}
        ev = {key: event_value(value) for key, value in events.items()}
        body_owner = SimpleNamespace(s=self.s, records=SimpleNamespace(ref=ref), _common=Apply._common)
        expected_bodies = Apply._bodies(body_owner, candidate, cp, child, h['driver'], driver,
            h['authorization'], auth, ev['save_event'])
        seqs = [e['seq'] for e in events.values()]
        if (expected_bodies != (app['snapshot'], consumption['snapshot']) or seqs != sorted(set(seqs)) or
                values.boundary != events['checkpoint_event']['seq'] or
                any(row['command_id'] != candidate['command_id'] for row in (app, consumption)) or
                not proposal['created'] <= preview_row['created'] <= cp['created'] <= body['child_seed']['created'] <=
                    candidate['created'] <= auth['transferred_at'] <= driver['updated'] <= app['created'] <=
                    consumption['created'] < auth['dispatch_before']):
            raise ValueError('原调整保存四元组、责任、原命令及实际事件未闭合。')
        consumers = [row for row in self.sources.all(self.db, r.KIND_FOR[r.CONSUMPTION])
            if row.get('snapshot', {}).get('proposal') == ref(proposal)]
        if consumers != [consumption] or any(run.get('proposal') == ref(proposal)
                for run in self.sources.all(self.db, 'work_unit_run')):
            raise ValueError('原调整提案不能重复保存或兼作普通派发消费。')
        edge = {'parent_plan': plan_ref(parent), 'child_plan': plan_ref(child),
            'checkpoint': ref(cp), 'candidate': ref(candidate), 'application': ref(app),
            'consumption': ref(consumption), 'save_event': ev['save_event']}
        if ancestor_edges:
            child_values = HistoricalAncestorValues(self.sources, self.timeline, child,
                events['consumption_event']['seq'], stage='ADJUSTMENT', frames=values.frames, records=values.records,
                material=values.material, authorization=auth, capabilities=values._caps,
                adjustment_edges=[*ancestor_edges, edge])
            receipt = child_values.history()
            for name in ('round_capability', 'post_round_capability', 'successor_capability'):
                receipt.pop(name, None)
            receipt.update(history={k: receipt[k] for k in r.HISTORY_FIELDS},
                current_material_observation=values.material, saved_edge_verified=True,
                successor_layers_verified=True, verified_scope='SAVED_SUCCESSOR_LAYERS', public_apply=False,
                application=app, consumption=consumption, candidate=candidate)
        else:
            receipt = {'root': h['root'], 'plan': child, 'candidate': candidate, 'application': app,
                'consumption': consumption, 'history': {k: h[k] for k in r.HISTORY_FIELDS},
                'frames': values.frames, 'driver': driver, 'authorization': auth, 'usage': h['usage'],
                'current_material_observation': values.material, 'saved_edge_verified': True,
                'verified_scope': 'FIRST_SAVED_EDGE_WITHOUT_SUCCESSOR_RUNS',
                'successor_layers_verified': False, 'public_apply': False, **PERMISSIONS}
        owner = 'saved:' + candidate['id']
        self.ledger.command(candidate['command_id'], 'internal-coordinator-adjustment',
            {'proposal': ref(proposal)}, receipt, owner,
            not_before=max(consumption['created'], events['consumption_event']['created']))
        for protocol, row in ((r.PREVIEW, preview_row), (r.CHECKPOINT, cp), (r.CANDIDATE, candidate),
                (r.APPLICATION, app), (r.CONSUMPTION, consumption)):
            self.ledger.row(r.KIND_FOR[protocol], row, owner)
            self.ledger.event(r.EVENTS[protocol], row, owner)
        self.ledger.row('teamwork_plan', child, owner)
        self.ledger.row('work_driver_revision', {**driver, 'id': 'driver-version:' + driver['revision'], 'driver_id': driver['id']}, owner)
        self.ledger.row('work_unit_authorization_revision', {**auth, 'id': 'unit-auth-version:' + auth['revision']}, owner)
        self.ledger.event(driver_event['kind'], {**driver, 'no_dispatch': True}, owner)
        self.ledger.event(SAVE_EVENT, payload, owner)
        return {'child_plan': child, 'edge': edge, 'cutoff': values.boundary - 1, 'candidate': candidate,
            'checkpoint': cp, 'application': app, 'consumption': consumption, 'driver': driver,
            'authorization': auth, 'frames': deepcopy(values.frames), 'receipt': receipt}

    def post(self, kind, checkpoint, candidate, facts, *, proved_ancestor_edges):
        """facts and all previous edges must already be independently proved."""
        if kind == 'POST_V1':
            from . import work_coordinator_handoff_post_round_saved_records as r
            from .work_coordinator_handoff_post_round_candidates import WorkCoordinatorHandoffPostRoundCandidates as Values
            command_kind = 'internal.post-round.save'
        elif kind == 'POST_V2':
            from . import work_coordinator_handoff_post_round_v2_saved_records as r
            from .work_coordinator_handoff_post_round_v2_candidates import WorkCoordinatorHandoffPostRoundV2Candidates as Values
            command_kind = 'internal.post-round.v2.save'
        elif kind == 'POST_V3':
            from . import work_coordinator_handoff_post_round_v3_saved_records as r
            from .work_coordinator_handoff_post_round_v3_candidates import WorkCoordinatorHandoffPostRoundV3Candidates as Values
            command_kind = 'internal.post-round.v3.save'
        else:
            raise ValueError('保存边须使用实际POST代际codec。')
        r.preparation_row(checkpoint, r.CHECKPOINT); r.preparation_row(candidate, r.CANDIDATE)
        h, parent, proposal = facts['history'], facts['plan'], facts['proposal']
        child = candidate['snapshot']['child_plan']; cp = checkpoint['snapshot']
        if child not in self.timeline.plans or parent != self.timeline.plans[self.timeline.plans.index(child) - 1]:
            raise ValueError('保存边必须是原时间线中的准确相邻父子。')
        request = {'id': child['item_id'], 'revision': h['task']['revision'],
            'current_driver': self.s.work_drivers.reference(h['driver']), 'plan': plan_ref(parent),
            'authorization': self.s.work_units.auth_ref(h['authorization']),
            'frontier': digest([{'run_id': f['run']['id'], 'evidence': f['evidence']['version']} for f in h['frames']]),
            'proposal': ref(proposal)}
        values = Values(self.s)
        expected_cp = values._checkpoint(facts, cp['cutoff']) if kind == 'POST_V1' else values._checkpoint(facts, request, cp['cutoff'])
        if (cp != expected_cp or candidate['snapshot'] != values._candidate(facts, checkpoint, candidate['snapshot']['child_seed']) or
                checkpoint['command_id'] != candidate['command_id'] or ref(checkpoint) != candidate['snapshot']['checkpoint']):
            raise ValueError('保存准备、原完整历史或候选不能由自洽快照代替独立重建。')
        def one(protocol, key):
            rows = [row for row in self.sources.all(self.db, r.KIND_FOR[protocol])
                if row.get('snapshot', {}).get(key) == plan_ref(child)]
            if len(rows) != 1:
                raise ValueError('每个实际保存边须有唯一完整应用和登记。')
            return r.validate_row(rows[0], protocol)
        app, reg = one(r.APPLICATION, 'child_plan'), one(r.REGISTRATION, 'to_plan')
        old = WorkCoordinatorHandoffContext(self.s)
        driver = old._driver(self.db, child, app['snapshot']['driver_after'])
        auth = old._auth(self.db, child, app['snapshot']['authorization_after'])
        r.adjacent(checkpoint, candidate, child, h['driver'], driver, h['authorization'], auth)
        first_event, first_driver = self.timeline.driver_versions(child)[0]
        driver_kind = {'POST_V1': 'work_driver_coordinator_post_round_adjustment_saved',
            'POST_V2': 'work_driver_coordinator_post_round_adjustment_saved_v2',
            'POST_V3': 'work_driver_coordinator_post_round_adjustment_saved_v3'}[kind]
        if first_driver != driver or first_event['kind'] != driver_kind:
            raise ValueError('保存后的责任必须以准确暂停转移版本起始。')
        payload = (r.save_payload(candidate, child, driver, auth) if kind == 'POST_V3'
            else r.save_payload(self.s, candidate, child, driver, auth))
        events = {'checkpoint_event': self.sources._event(self.db, r.EVENTS[r.CHECKPOINT], checkpoint),
            'candidate_event': self.sources._event(self.db, r.EVENTS[r.CANDIDATE], candidate),
            'driver_event': first_event,
            'save_event': self.sources._event(self.db, r.SAVE_EVENT, payload),
            'application_event': self.sources._event(self.db, r.EVENTS[r.APPLICATION], app),
            'registration_event': self.sources._event(self.db, r.EVENTS[r.REGISTRATION], reg)}
        original_events = {key: event_value(value) for key, value in events.items()}
        args = (checkpoint, candidate, child, h['driver'], driver, h['authorization'], auth,
            original_events['save_event'], request)
        expected_bodies = r.bodies(*args) if kind == 'POST_V3' else r.bodies(self.s, *args)
        seqs = [event['seq'] for event in events.values()]
        row_events = ((checkpoint, events['checkpoint_event']), (candidate, events['candidate_event']),
            (app, events['application_event']), (reg, events['registration_event']))
        if (expected_bodies != (app['snapshot'], reg['snapshot']) or app['snapshot']['request'] != request or
                any(row['command_id'] != candidate['command_id'] for row in (app, reg)) or
                seqs != sorted(set(seqs)) or seqs[:2] != [cp['cutoff'] + 1, cp['cutoff'] + 2] or
                any(row['created'] > event['created'] for row, event in row_events) or
                not driver['updated'] <= app['created'] <= reg['created'] < auth['dispatch_before']):
            raise ValueError('保存四元组、原七字段、全部事件或最初截止未闭合。')
        edge = {'parent_plan': plan_ref(parent), 'child_plan': plan_ref(child),
            'checkpoint': ref(checkpoint), 'candidate': ref(candidate), 'application': ref(app),
            'registration': ref(reg), 'save_event': original_events['save_event'],
            'registration_event': original_events['registration_event']}
        if kind == 'POST_V3':
            from .work_coordinator_handoff_post_round_v3_path import extend_path
            path = extend_path(checkpoint, candidate, driver, auth, app, reg, original_events)
        elif kind == 'POST_V1':
            path = versioned({'protocol': 'COORDINATOR_FILE_HANDOFF_POST_ROUND_SAVED_PATH_V1',
                'root': h['root'], 'plan': plan_ref(child), 'parent_path': h['saved_path'], 'edge': edge,
                'history_frontier': h['frontier'], 'driver': driver, 'authorization': auth,
                'material_observation': h['material_observation'], **PERMISSIONS})
        else:
            from .work_coordinator_handoff_post_round_v2_path import PATH, SCOPE, plan_kind, validate_path
            entries = []
            for plan in self.timeline.plans[:self.timeline.plans.index(child)]:
                if plan == self.timeline.root:
                    previous_edge, cutoff = None, None
                else:
                    proved = proved_ancestor_edges.get(plan['id'])
                    if proved is None or proved['child_plan'] != plan:
                        raise ValueError('有序保存路径缺少独立重建的祖先边。')
                    previous_edge, cutoff = proved['edge'], proved['cutoff']
                entries.append({'plan': plan_ref(plan), 'kind': plan_kind(plan),
                    'edge': deepcopy(previous_edge), 'checkpoint_cutoff': cutoff})
            entries.append({'plan': plan_ref(child), 'kind': kind, 'edge': edge, 'checkpoint_cutoff': cp['cutoff']})
            path = validate_path(versioned({'protocol': PATH, 'root': h['root'], 'plan': plan_ref(child),
                'ancestors': entries, 'history_frontier': h['frontier'], 'driver': driver,
                'authorization': auth, 'material_observation': h['material_observation'],
                'scope': SCOPE, **PERMISSIONS}))
        value = {'application': app, 'registration': reg, 'child_plan': child, 'driver': driver,
            'authorization': auth, 'saved_path': path, 'events': original_events,
            'frontier': deepcopy(h['frontier']), 'usage': deepcopy(h['usage'])}
        receipt = {key: value[key] for key in ('application', 'registration', 'child_plan', 'driver', 'authorization')}
        receipt.update(no_dispatch=True, automatically_resumed=False)
        owner = 'saved:' + candidate['id']
        self.ledger.command(candidate['command_id'], command_kind, request, receipt, owner,
            not_before=max(reg['created'], events['registration_event']['created']))
        for protocol, row in ((r.CHECKPOINT, checkpoint), (r.CANDIDATE, candidate),
                (r.APPLICATION, app), (r.REGISTRATION, reg)):
            self.ledger.row(r.KIND_FOR[protocol], row, owner)
            self.ledger.event(r.EVENTS[protocol], row, owner)
        self.ledger.row('teamwork_plan', child, owner)
        self.ledger.row('work_driver_revision', {**driver, 'id': 'driver-version:' + driver['revision'], 'driver_id': driver['id']}, owner)
        self.ledger.row('work_unit_authorization_revision', {**auth, 'id': 'unit-auth-version:' + auth['revision']}, owner)
        self.ledger.event(first_event['kind'], {**driver, 'no_dispatch': True}, owner)
        self.ledger.event(r.SAVE_EVENT, payload, owner)
        return {'original': deepcopy(value), 'edge': deepcopy(edge), 'cutoff': cp['cutoff'],
            'child_plan': deepcopy(child), 'checkpoint': deepcopy(checkpoint), 'candidate': deepcopy(candidate),
            'observation': {'history': deepcopy(h), 'saved_path': deepcopy(path)}, 'frames': deepcopy(h['frames'])}
