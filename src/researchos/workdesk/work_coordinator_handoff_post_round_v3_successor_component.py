"""Private historical component owned by the complete responsibility reader.

The original SavedComponent remains a unique-PAUSED boundary. This class
rebuilds that same original immutable save (including its full committed
receipt) and the actual responsibility subchain before exposing exact rows.
"""
import json
from copy import deepcopy
from .store import digest
from .work_coordinator_handoff_post_round_candidates import ref, LINEAGE_FIELD
from .work_coordinator_handoff_post_round_runtime import belongs
from .work_coordinator_handoff_post_round_v3_saved_component import WorkCoordinatorHandoffPostRoundV3SavedComponent
from .work_coordinator_handoff_post_round_v3_candidates import WorkCoordinatorHandoffPostRoundV3Candidates
from .work_coordinator_handoff_post_round_v3_responsibility_history import _mentions
from .work_coordinator_handoff_post_round_v3_successor_history import read_subchain
from . import work_coordinator_handoff_post_round_v3_successor_contracts as c
from .work_coordinator_handoff_post_round_v3_commands import RECEIPT_KIND, RECEIPT_PROTOCOL
from . import work_coordinator_handoff_post_round_v3_saved_records as r
from . import work_coordinator_handoff_post_round_v3_responsibility_records as rr
from . import work_coordinator_handoff_post_round_v3_path as p


class _SuccessorComponent(WorkCoordinatorHandoffPostRoundV3SavedComponent):
    def __init__(self, service, *, _application=None):
        super().__init__(service)
        self._application = _application
        self._reconstruction = None

    def _require_reconstruction(self, db, candidate):
        state = self._reconstruction
        if (state is None or state['db'] is not db or not db.in_transaction or
                state['changes'] != db.total_changes or state['candidate'] != ref(candidate)):
            raise ValueError('V3 child reconstruction must belong to this unchanged component invocation.')
        return state

    def _once(self, db, identity):
        # The parent's two complete passes each call _canonical_carriers. Each
        # call rebuilds the entire child namespace once before the parent audit.
        # Keep their ordinary public read, including all comparisons and tails.
        if self._reconstruction is not None or not db.in_transaction:
            raise ValueError('V3 child reconstruction requires one active transactional invocation.')
        self._reconstruction = {'db': db, 'changes': db.total_changes,
            'candidate': deepcopy(identity), 'passes': 0}
        try:
            candidate = self._get(db, r.CANDIDATE, identity); b = candidate['snapshot']
            checkpoint = self._get(db, r.CHECKPOINT, b['checkpoint']); body = checkpoint['snapshot']
            event = self._event(db, r.EVENTS[r.CHECKPOINT], checkpoint)
            ce = self._event(db, r.EVENTS[r.CANDIDATE], candidate)
            facts = self._facts(db, body['plan'], b['proposal'], body['cutoff'], (checkpoint, candidate))
            h = facts['history']; self._parent_reader = facts['parent_reader']
            self._parent_reader.checkpoints._guard(db, b['child_plan'])
            state = self._require_reconstruction(db, candidate)
            if (state['passes'] != 2 or body['request'] != self._request(h, facts['plan'], facts['proposal']) or
                    body != self._checkpoint(facts, body['request'], body['cutoff']) or
                    b != self._candidate(facts, checkpoint, b['child_seed']) or event['seq'] != body['cutoff'] + 1 or
                    ce['seq'] != event['seq'] + 1 or candidate['command_id'] != checkpoint['command_id'] or
                    not facts['proposal']['created'] <= checkpoint['created'] <= b['child_seed']['created'] <= candidate['created'] < h['authorization']['dispatch_before']):
                raise ValueError('V3 child reconstruction lost its exact preparation boundary or two parent audits.')
            # These values were produced by this invocation's second complete
            # semantic reconstruction, never by a previous Current or caller.
            original = self._chain['extension']['original']
            saved = {k: original[k] for k in ('application', 'registration', 'child_plan',
                'driver', 'authorization', 'saved_path', 'events')}
            return {**saved, 'checkpoint': checkpoint, 'candidate': candidate,
                'checkpoint_event': event, 'candidate_event': ce, 'frames': h['frames'],
                'observation': {'history': body['history']},
                'current_material_observation': facts['current_material_observation'],
                'candidate_data_verified': True, 'bounded_parent_components_verified': True,
                'actual_saved_values_verified': True, 'typed_saved_namespace_verified': True,
                'preparation_namespace_verified': False, 'full_saved_tree_verified': False,
                'saved_edge_verified': False, 'successor_runtime_open': False, 'public_apply': False,
                **r.PERMISSIONS}
        finally:
            self._reconstruction = None

    def _namespace(self, db, facts, checkpoint, candidate):
        value = self._saved(db, facts, checkpoint, candidate)
        # Frozen HISTORY_FIELDS deliberately omits frames and usage. Resolve
        # its independently compared frontier to real immutable runs and the
        # actual request ledger on every invocation, including the final hook.
        runs = [self.s.collaboration.get(db, 'work_unit_run', f['run_id'])
            for f in facts['history']['frontier']]
        original = {**value, 'frontier': facts['history']['frontier'],
            'usage': self.s.work_units.usage(db, runs)}
        chain = read_subchain(self.s, db, original, facts)
        # This proof belongs to this fresh historical reconstruction only.
        # No caller-supplied original, carrier rows or digest grants authority.
        self._chain = chain
        extension = chain['extension']; self._extension = extension
        carriers = self._carriers(checkpoint, candidate, value)
        carriers = [(k, chain['driver'] if k == 'work_driver' else v, e) for k, v, e in carriers]
        carriers += [('work_driver_revision', {**v, 'id': 'driver-version:' + v['revision'],
            'driver_id': v['id']}, None) for v in chain['versions'][1:]]
        carriers += [(rr.KIND, v, None) for v in chain['transitions']]
        carriers += extension['carriers']
        carriers += [(k,v,None) for k,v in extension['extra_rows']]
        carriers += [('work_dispatch',v,None) for v in extension['dispatches']]
        identities = {v for row in (checkpoint, candidate, value['application'], value['registration'],
                value['child_plan'], value['driver'], value['authorization'])
            for k, v in row.items() if k in ('id', 'version', 'revision', 'generation')}
        identities.update(v for row in chain['transitions'] for v in ref(row).values())
        identities.update(v['revision'] for v in chain['versions'])
        identities.update(v for _,row,_ in extension['carriers'] if 'version' in row for v in ref(row).values())
        identities.update(facts['history']['root'].values())
        identities.update(candidate['snapshot']['parent_plan'].values())
        protocols = {c.CAPABILITY,c.OBSERVATION,c.PROTOCOL,c.PATH,RECEIPT_PROTOCOL,*r.KIND_FOR, rr.TRANSITION, p.PATH, candidate['snapshot']['child_plan'][LINEAGE_FIELD]['protocol']}
        from . import work_coordinator_handoff_post_round_v3_continuation_contracts as cc
        from . import work_coordinator_handoff_post_round_v3_continuation_receipts as cr
        from . import work_coordinator_handoff_post_round_v3_continuation_proposals as cp
        protocols.update({cc.CAPABILITY,cc.OBSERVATION,cc.PATH,cc.COORDINATION,cc.UNIT,cc.INTENT,cr.PROTOCOL,cp.PROTOCOL})
        # Attribution includes bare typed refs, even under unknown outer kinds.
        # Only V3 markers define this component's scope. Parent namespaces are
        # still checked by the actual bounded V8 owner, not accepted here.
        typed = {x for row in (checkpoint, candidate, value['application'], value['registration'])
            for x in ref(row).values()}
        def v3_marked(value):
            pending = [value]
            while pending:
                node = pending.pop()
                if isinstance(node, dict):
                    if node.get('protocol') in protocols: return True
                    if any(isinstance(node.get(k), str) and (node[k] in typed or
                            any(node[k].startswith(kind + ':') for kind in [*r.KIND_FOR.values(),rr.KIND,c.KIND,RECEIPT_KIND,cc.KIND,cr.KIND,cp.KIND]))
                            for k in ('id', 'version')): return True
                    pending.extend(node.values())
                elif isinstance(node, list): pending.extend(node)
            return False
        expected_rows = {(kind, row['id']): row for kind, row, _ in carriers}
        expected_events = {(e['kind'], e['seq']): row for _, row, e in carriers if e is not None}
        attributed = {(e['kind'],e['seq']):(e.get('task_id'),e.get('attempt_id')) for _,_,e in carriers if e is not None}
        expected_events.update({(e['kind'], e['seq']): e['payload'] for e in chain['events']})
        expected_events[(r.DRIVER_EVENT, value['events']['driver_event']['seq'])] = {**value['driver'], 'no_dispatch': True}
        expected_events[(r.SAVE_EVENT, value['events']['save_event']['seq'])] = r.save_payload(
            candidate, value['child_plan'], value['driver'], value['authorization'])
        for row in db.execute('SELECT * FROM collaboration'):
            body = json.loads(row['data'])
            if ((row['kind'] in r.KIND_FOR.values() or v3_marked(body)) and
                    belongs(body, candidate['item_id'], identities) and
                    expected_rows.get((row['kind'], row['id'])) != body):
                raise ValueError('V3后继实际记录全集存在未重建载体：'+row['kind']+':'+row['id'])
        for row in db.execute('SELECT * FROM events'):
            body = json.loads(row['payload'])
            if ((row['kind'] in {*r.EVENTS.values(), r.SAVE_EVENT, r.DRIVER_EVENT} or v3_marked(body)) and
                    belongs(body, candidate['item_id'], identities) and
                    (expected_events.get((row['kind'], row['seq'])) != body or
                     (row['task_id'],row['attempt_id']) != attributed.get((row['kind'],row['seq']),(None,None)))):
                raise ValueError('V3实际事件全集存在重复、改名、嵌套或错误归属。')
        # Original save and responsibility receipts must have their exact
        # original UUIDs, kinds, payload hashes and full values. Aliases fail.
        original_command = dict(db.execute('SELECT * FROM commands WHERE id=?',
            (candidate['command_id'],)).fetchone())
        commands = {c['id']: c for c in [original_command, *chain['commands']]}
        for command in db.execute('SELECT * FROM commands'):
            body = json.loads(command['receipt'])
            if _mentions(body, typed | {v for t in chain['transitions'] for v in ref(t).values()}, candidate['item_id']):
                if commands.get(command['id']) != dict(command):
                    raise ValueError('V3完整current存在未重建、别名或嵌套命令回执。')
        # Every child runtime carrier must equal this invocation's semantic reconstruction.
        for kind, expected in (('work_driver_revision', [{**v, 'id': 'driver-version:' + v['revision'], 'driver_id': v['id']} for v in chain['versions']]),
                ('work_unit_authorization_revision', [expected_rows[('work_unit_authorization_revision', 'unit-auth-version:' + value['authorization']['revision'])]]),
                ('work_unit_run', extension['runs'])):
            actual = [v for v in self.s.collaboration.all(db, kind) if v.get('plan_id') == value['child_plan']['id']]
            if sorted(map(digest, actual)) != sorted(map(digest, expected)):
                raise ValueError('V3后继完整revision、authorization或唯一首运行集合不符。')
        return value

    def _canonical_carriers(self, db, facts, checkpoint, candidate):
        state = self._require_reconstruction(db, candidate)
        if state['passes'] >= 2:
            raise ValueError('V3 child reconstruction exceeded its two parent audits.')
        # Validate both preparation envelopes and invoke our complete namespace
        # exactly once. SavedComponent's wrapper would invoke it a second time.
        WorkCoordinatorHandoffPostRoundV3Candidates._canonical_carriers(
            self, db, facts, checkpoint, candidate)
        chain = self._chain
        saved = chain['extension']['original']
        carriers = tuple((k, v, e) for k, v, e in self._carriers(checkpoint, candidate, saved)
            if e is not None)
        transitions = {v['id']: v for v in chain['transitions']}
        extra = []
        for e in chain['events']:
            if e['kind'] == rr.EVENT:
                extra.append((rr.KIND, transitions[e['payload']['id']], e))
        self._require_reconstruction(db, candidate)
        state['passes'] += 1
        return carriers + tuple(extra) + tuple(self._extension['carriers'])
