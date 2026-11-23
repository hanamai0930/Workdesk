"""Read-only proposal exclusion across the stored consumption protocols.

Old fixed revisions share one consumption kind and cannot legitimately coexist
with a coordinator root on the same task. Their verifier is not a source gate
for this protocol. Unrelated tasks remain outside this exclusion scope.
"""
import json

from .work_coordinator_handoff_records import reference
from .work_coordinator_handoff_adjustment_records import CONSUMPTION, KIND_FOR


GENERIC = 'work_proposal_consumption'
FIXED = 'work_file_handoff_revision_consumption'
KINDS = (GENERIC, FIXED, KIND_FOR[CONSUMPTION])


def require_unconsumed(service, db, proposal, item_id, *, allowed=None):
    reference(proposal)
    identity = proposal['id']
    def matches(value):
        return isinstance(value,dict) and value.get('id') == identity

    for kind in KINDS:
        for raw in db.execute('SELECT id,data FROM collaboration WHERE kind=?',(kind,)):
            row = json.loads(raw['data'])
            body = row.get('snapshot',row)
            if not isinstance(body,dict):
                raise ValueError('消费记录完整字段损坏，不能核对提案互斥。')
            related = (row.get('item_id') == item_id or body.get('item_id') == item_id)
            conflict = (matches(row.get('proposal')) or matches(body.get('proposal')) or
                        raw['id'] == 'proposal-consumption:'+identity or
                        (kind == FIXED and related))
            if not conflict:
                continue
            if kind == KIND_FOR[CONSUMPTION] and allowed is not None and row == allowed:
                continue
            raise ValueError('准确提案已有其它消费或同任务旧交接消费，不能重复保存。')
    # A dispatch is also a consumption. Compare the identity even when a
    # corrupt version was attached, and inspect the actual frozen input too.
    for run in service.collaboration.all(db,'work_unit_run'):
        frozen = run.get('input',{}).get('coordination_proposal') or {}
        if matches(run.get('proposal')) or matches(frozen.get('reference')):
            raise ValueError('准确提案已有真实运行消费，不能用于调整保存。')
