"""Single-pass original first-ROUND components inside a complete mixed walk.

The accepted standalone readers retain their independent two-read/current
gates. Here the complete mixed boundary repeats the whole traversal twice.
No complete proof or file bytes survive a traversal for reuse.
"""
from .work_coordinator_handoff_round_checkpoints import WorkCoordinatorHandoffRoundCheckpoints
from .work_coordinator_handoff_round_parent_history import WorkCoordinatorHandoffRoundParentHistory
from .work_coordinator_handoff_round_records import CANDIDATE, CHECKPOINT, KIND_FOR, EVENTS
from .work_coordinator_handoff_round_contracts import prepared


class FirstCheckpointReplay(WorkCoordinatorHandoffRoundCheckpoints):
    def read_candidate(self, db, reference):
        candidate = self.data.get(db, CANDIDATE, reference); body = candidate['snapshot']
        checkpoint = self.data.get(db, CHECKPOINT, body['checkpoint'])
        source = self._once(db, body['checkpoint']); child = prepared(checkpoint, candidate)
        related = [r for r in self.s.collaboration.all(db, KIND_FOR[CANDIDATE]) if
            r.get('snapshot', {}).get('source', {}).get('run_id') == body['source']['run_id']]
        event = self._event(db, EVENTS[CANDIDATE], candidate)
        if (related != [candidate] or event['seq'] <= source['checkpoint_event']['seq'] or
                candidate['created'] < checkpoint['created']):
            raise ValueError('Mixed首ROUND候选必须有准确唯一真实来源和事件。')
        return {**source, 'candidate': candidate, 'candidate_event': event,
            'child_plan': child, 'candidate_data_verified': True}


class FirstRoundReplay(WorkCoordinatorHandoffRoundParentHistory):
    def __init__(self, service):
        super().__init__(service)
        self.checkpoints = FirstCheckpointReplay(service)
