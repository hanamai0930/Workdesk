"""Private historical component owned by the complete responsibility reader.

The original SavedComponent remains a unique-PAUSED boundary. This class
rebuilds that same original immutable save (including its full committed
receipt) and the actual responsibility subchain before exposing exact rows.
"""
import json
from .store import digest
from .work_coordinator_handoff_post_round_candidates import ref, LINEAGE_FIELD
from .work_coordinator_handoff_post_round_runtime import belongs
from .work_coordinator_handoff_post_round_v3_saved_component import WorkCoordinatorHandoffPostRoundV3SavedComponent
from .work_coordinator_handoff_post_round_v3_responsibility_history import read_subchain, _mentions
from . import work_coordinator_handoff_post_round_v3_saved_records as r
from . import work_coordinator_handoff_post_round_v3_responsibility_records as rr
from . import work_coordinator_handoff_post_round_v3_path as p


class _CurrentComponent(WorkCoordinatorHandoffPostRoundV3SavedComponent):
    def __init__(self, service, *, _application=None):
        super().__init__(service)
        self._application = _application

    def _namespace(self, db, facts, checkpoint, candidate):
        value = self._saved(db, facts, checkpoint, candidate)
        # Frozen HISTORY_FIELDS deliberately omits frames and usage. Resolve
        # its independently compared frontier to real immutable runs and the
        # actual request ledger on every invocation, including the final hook.
        runs = [self.s.collaboration.get(db, 'work_unit_run', f['run_id'])
            for f in facts['history']['frontier']]
        original = {**value, 'frontier': facts['history']['frontier'],
            'usage': self.s.work_units.usage(db, runs)}
        chain = read_subchain(self.s, db, original, self._application)
        # This proof belongs to this fresh historical reconstruction only.
        # No caller-supplied original, carrier rows or digest grants authority.
        self._chain = chain
        carriers = self._carriers(checkpoint, candidate, value)
        carriers = [(k, chain['driver'] if k == 'work_driver' else v, e) for k, v, e in carriers]
        carriers += [('work_driver_revision', {**v, 'id': 'driver-version:' + v['revision'],
            'driver_id': v['id']}, None) for v in chain['versions'][1:]]
        carriers += [(rr.KIND, v, None) for v in chain['transitions']]
        identities = {v for row in (checkpoint, candidate, value['application'], value['registration'],
                value['child_plan'], value['driver'], value['authorization'])
            for k, v in row.items() if k in ('id', 'version', 'revision', 'generation')}
        identities.update(v for row in chain['transitions'] for v in ref(row).values())
        identities.update(v['revision'] for v in chain['versions'])
        identities.update(facts['history']['root'].values())
        identities.update(candidate['snapshot']['parent_plan'].values())
        protocols = {*r.KIND_FOR, rr.TRANSITION, p.PATH, candidate['snapshot']['child_plan'][LINEAGE_FIELD]['protocol']}
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
                            any(node[k].startswith(kind + ':') for kind in [*r.KIND_FOR.values(), rr.KIND]))
                            for k in ('id', 'version')): return True
                    pending.extend(node.values())
                elif isinstance(node, list): pending.extend(node)
            return False
        expected_rows = {(kind, row['id']): row for kind, row, _ in carriers}
        expected_events = {(e['kind'], e['seq']): row for _, row, e in carriers if e is not None}
        expected_events.update({(e['kind'], e['seq']): e['payload'] for e in chain['events']})
        expected_events[(r.DRIVER_EVENT, value['events']['driver_event']['seq'])] = {**value['driver'], 'no_dispatch': True}
        expected_events[(r.SAVE_EVENT, value['events']['save_event']['seq'])] = r.save_payload(
            candidate, value['child_plan'], value['driver'], value['authorization'])
        for row in db.execute('SELECT * FROM collaboration'):
            body = json.loads(row['data'])
            if ((row['kind'] in r.KIND_FOR.values() or v3_marked(body)) and
                    belongs(body, candidate['item_id'], identities) and
                    expected_rows.get((row['kind'], row['id'])) != body):
                raise ValueError('V3实际记录全集存在改名、嵌套、错误键或未知边。')
        for row in db.execute('SELECT * FROM events'):
            body = json.loads(row['payload'])
            if ((row['kind'] in {*r.EVENTS.values(), r.SAVE_EVENT, r.DRIVER_EVENT} or v3_marked(body)) and
                    belongs(body, candidate['item_id'], identities) and
                    (expected_events.get((row['kind'], row['seq'])) != body or
                     row['task_id'] is not None or row['attempt_id'] is not None)):
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
        # No child runtime is covered by this contract.
        for kind, expected in (('work_driver_revision', [{**v, 'id': 'driver-version:' + v['revision'], 'driver_id': v['id']} for v in chain['versions']]),
                ('work_unit_authorization_revision', [expected_rows[('work_unit_authorization_revision', 'unit-auth-version:' + value['authorization']['revision'])]]),
                ('work_unit_run', [])):
            actual = [v for v in self.s.collaboration.all(db, kind) if v.get('plan_id') == value['child_plan']['id']]
            if sorted(map(digest, actual)) != sorted(map(digest, expected)):
                raise ValueError('V3完整current仅接受准确专属责任链且无后继运行。')
        return value

    def _canonical_carriers(self, db, facts, checkpoint, candidate):
        # Super independently rebuilds both original preparation envelopes,
        # original save/full receipt and our exact child namespace first.
        carriers = super()._canonical_carriers(db, facts, checkpoint, candidate)
        chain = self._chain
        transitions = {v['id']: v for v in chain['transitions']}
        extra = []
        for e in chain['events']:
            if e['kind'] == rr.EVENT:
                extra.append((rr.KIND, transitions[e['payload']['id']], e))
        return carriers + tuple(extra)
