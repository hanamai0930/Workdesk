"""Exact V8 proof carriers for a bounded component, not a saved-tree audit."""
import json

from .work_coordinator_handoff_records import DELIVERY_KIND, COMPLETION_KIND
from .work_coordinator_handoff_post_round_v2_run_contracts import CAPABILITY, KIND, OPENED, PROTOCOL, UNIT, PATH
from .work_coordinator_handoff_post_round_v2_intent import PROTOCOL as INTENT
from .work_coordinator_handoff_post_round_v2_commands import RECEIPT_PROTOCOL, RECEIPT_KIND, RECEIPT_EVENT


def audit(reader, db, plan, h, *, _owned=()):
    """Every related V8 proof must have an exact already-rebuilt carrier.

    Current pointers and unrelated future events do not determine a prefix.
    Future saved edges containing V8 proofs require their own typed owning
    audit; this component deliberately grants no full saved-namespace proof.
    """
    protocols = {CAPABILITY, RECEIPT_PROTOCOL, PROTOCOL, UNIT, PATH, INTENT}
    identity = set(reader.ref(reader._successor).values())

    def marked(value):
        pending = [value]
        while pending:
            node = pending.pop()
            if isinstance(node, dict):
                if (node.get('protocol') in protocols or
                        any(isinstance(node.get(k), str) and (node[k] in identity or
                            node[k].startswith((KIND + ':', RECEIPT_KIND + ':')))
                            for k in ('id', 'version'))): return True
                pending.extend(node.values())
            elif isinstance(node, list): pending.extend(node)
        return False

    item = plan['item_id']
    proposals = [v for _, v in reader._proposal_events(db, item)]
    receipts = reader._related(db, RECEIPT_KIND, item)
    # Attribute even a bare nested reference, without an item_id or full body.
    # Both canonical sets were independently validated before entering audit.
    for row in receipts: identity.update(reader.ref(row).values())
    reader._identity_ids.update(identity)
    known_rows = {KIND: [reader._successor], RECEIPT_KIND: receipts,
        'work_unit_run': [f['run'] for f in h['frames']], 'work_proposal': proposals,
        DELIVERY_KIND: h['delivery'], COMPLETION_KIND: h['completion']}
    known_events = {OPENED: [reader._successor], RECEIPT_EVENT: receipts,
        'cooperative_unit_reserved': known_rows['work_unit_run'], CHECKED: proposals,
        'coordinator_handoff_delivered': h['delivery'], 'coordinator_handoff_completed': h['completion']}
    for row in db.execute('SELECT id,kind,data FROM collaboration'):
        value = json.loads(row['data'])
        if not reader._belongs(value, item) or not marked(value): continue
        if any(row['id'] == v['id'] and row['kind'] == k and value == v for k, v, _ in _owned): continue
        if row['id'] != value.get('id') or value not in known_rows.get(row['kind'], []):
            raise ValueError('V8截止组件存在改名、嵌套或未重建的协议载体。')
    for row in db.execute('SELECT * FROM events'):
        value = json.loads(row['payload'])
        if not reader._belongs(value, item) or not marked(value): continue
        if any(row['seq'] == e['seq'] and row['kind'] == e['kind'] and value == v and
                row['task_id'] == e.get('task_id') and row['attempt_id'] == e.get('attempt_id') for _, v, e in _owned): continue
        if row['seq'] >= reader._cutoff or value not in known_events.get(row['kind'], []):
            raise ValueError('V8截止组件存在迟到、改名或嵌套的协议事件。')


CHECKED = 'coordinator_proposal_checked'
