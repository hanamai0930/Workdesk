"""Event-bounded components for a frozen, actually executed post-ROUND parent.

These components never assert the current pointer or a complete saved tree.
The eventual successor reader must independently audit its whole namespace.
"""
from copy import deepcopy
import json

from .store import digest
from .work_coordinator_handoff_adjustment_contracts import closed
from .work_coordinator_handoff_mixed_replay import FirstRoundReplay
from .work_coordinator_handoff_mixed_runtime import WorkCoordinatorHandoffMixedRuntime
from .work_coordinator_handoff_round_runtime import WorkCoordinatorHandoffRoundRuntime
from .work_coordinator_handoff_post_round_runtime import PostRoundRuntime
from .work_coordinator_handoff_post_round_contracts import SCOPE


class _BoundedPost:
    def _proposal_events(self, db, item_id, boundary=None):
        limit = min(boundary, self._cutoff) if boundary is not None else self._cutoff
        pairs = super()._proposal_events(db, item_id, limit)
        for seq, row in pairs:
            event = db.execute('SELECT * FROM events WHERE seq=?', (seq,)).fetchone()
            source = row['snapshot']['source']
            if (event is None or event['kind'] != 'coordinator_proposal_checked' or
                    json.loads(event['payload']) != row or
                    event['task_id'] != source['task_id'] or event['attempt_id'] != source['attempt_id']):
                raise ValueError('截止提案检查事件须绑定准确原来源任务和尝试。')
        return pairs

    def _related(self, db, kind, item):
        rows = super()._related(db, kind, item)
        if kind == 'work_proposal':
            identities = {row['id'] for _, row in self._proposal_events(db, item)}
            rows = [row for row in rows if row['id'] in identities]
        return rows

    def reconstruct(self, db, plan, boundary, material):
        from .work_coordinator_handoff_post_round_candidates import CHECKPOINT_EVENT, CHECKPOINT, validate_row
        if not db.in_transaction:
            raise ValueError('父历史组件须属于明确数据库事务。')
        event = db.execute('SELECT * FROM events WHERE seq=?', (boundary['seq'],)).fetchone()
        if (event is None or event['kind'] != CHECKPOINT_EVENT or
                boundary != self.checkpoints.live._event(event) or
                event['task_id'] is not None or event['attempt_id'] is not None):
            raise ValueError('父截止须为新调整真实检查点事件。')
        row = json.loads(event['payload'])
        validate_row(row, CHECKPOINT)
        if (row['snapshot']['plan'] != plan or row['snapshot']['history']['material_observation'] != material or
                row['snapshot']['cutoff'] + 1 != boundary['seq']):
            raise ValueError('父截止须绑定准确完整原方案与材料。')
        self._cutoff = boundary['seq']
        self._load_post(db, plan)
        if self._post_opened >= boundary['seq']:
            raise ValueError('父历史新能力晚于检查点。')
        h, actual = super()._walk(db, plan, boundary, material, audit_namespace=False)
        h['post_round_capability'] = self.ref(self._post)
        h['saved_path'] = self._new_path(plan, h['frames'], material, h['phase'], h['driver'])
        h['verified_scope'] = SCOPE
        self._audit_post(db, plan, h)
        # The low-level walker has checked the actual task/attempt, original
        # frozen inputs, every exit and capture, driver/auth and old saved edges.
        # Proposals are also recomputed against their actual historical events.
        self._check_proposals(db, plan, h)
        return deepcopy(h), deepcopy(actual)


class _First(_BoundedPost, PostRoundRuntime, FirstRoundReplay):
    legacy = WorkCoordinatorHandoffRoundRuntime
    from .work_coordinator_handoff_round_runtime import OPENED as round_opened


class _Mixed(_BoundedPost, PostRoundRuntime, WorkCoordinatorHandoffMixedRuntime):
    legacy = WorkCoordinatorHandoffMixedRuntime
    from .work_coordinator_handoff_mixed_runtime import OPENED as round_opened


@closed
def reconstruct(service, db, plan, boundary, material):
    """One full component traversal; callers repeat it and audit the live tree."""
    mixed = plan['coordinator_file_handoff_round_lineage']['protocol'] == 'COORDINATOR_FILE_HANDOFF_MIXED_ROUND_LINEAGE_V1'
    helper = (_Mixed if mixed else _First)(service)
    history, actual = helper.reconstruct(db, plan, boundary, material)
    return helper, history, actual
