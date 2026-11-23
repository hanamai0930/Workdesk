"""Private single-pass components owned by one complete V2 runtime read.

No component is cached across passes. The public readers retain their own
double reconstruction contracts; this adapter transfers their tail obligations
to the complete owner, which still reconstructs and audits twice.
"""
from copy import deepcopy

from .work_coordinator_handoff_post_round_v2_saved_tree import _SavedPreparation
from .work_coordinator_handoff_post_round_v2_candidates import _CheckpointParent
from .work_coordinator_handoff_adjustment_contracts import closed
from .work_read_observation import database_observation


class _Preparation(_SavedPreparation):
    def __init__(self, traversal):
        super().__init__(traversal.owner.s)
        self.traversal = traversal

    def _facts(self, db, plan, identity, cutoff):
        self.traversal.require(db)
        if plan not in self.traversal.plans:
            raise ValueError('私有父组件不属于本次准确祖先。')
        parent = _CheckpointParent(self.s, cutoff)
        value = parent._once_at(db, plan, identity)
        self._component = (parent, deepcopy(value))
        h = value['history']; proposal = value['proposal']
        return {'plan': plan, 'proposal': proposal, 'history': h,
            'preview': proposal['snapshot']['adjustment'], 'source_check_event': value['boundary_event'],
            'current_material_observation': value['current_material_observation'], 'parent_reader': parent}

    def read(self, db, identity):
        self.traversal.require(db)
        if len(self.traversal.passes) >= 2:
            raise ValueError('私有组件仅属于本次完整双遍。')
        value = self._once(db, identity)
        if value['child_plan'] != self.traversal.plan:
            raise ValueError('私有准备不属于本次准确子方案。')
        parent, component = self._component
        self.traversal.passes.append((parent, component, deepcopy(value)))
        return value


class _Traversal:
    def __init__(self, owner, db, plan):
        self.owner = owner; self.db = db; self.plan = deepcopy(plan)
        self.plans = owner._collect(db, plan)
        self.passes = []; self.active = False
        self.preparation = _Preparation(self)

    def require(self, db):
        if (not self.active or db is not self.db or not db.in_transaction or
                self.owner.s is not self.preparation.s or
                self.owner.preparation is not self.preparation):
            raise ValueError('私有组件须属于同service/db的当前完整读取。')

    def resources(self):
        # Include historical members absent from the final plan. This observes
        # external selection anew; database_fact must not cache this result.
        return [(p['id'], self.owner._resources(self.db, p)) for p in self.plans]

    def finish(self):
        self.require(self.db)
        if len(self.passes) != 2:
            raise ValueError('完整拥有者缺少两次独立私有组件。')
        def witness(group):
            _, parent, preparation = group
            parent = deepcopy(parent)
            parent['history']['usage'].pop('elapsed_observed_seconds', None)
            return parent, preparation
        if witness(self.passes[0]) != witness(self.passes[1]):
            raise ValueError('两遍私有父组件或准备完整见证发生变化。')
        parent, value, preparation = self.passes[1]
        # Preserve both removed public readers' exact end checks. The outer
        # reader separately rechecks live sources and every complete-tree layer.
        self.owner.checkpoints._recheck(self.db, {
            'frames': value['history']['frames'],
            'observation': {'history': value['history']},
            'current_material_observation': value['current_material_observation']})
        self.owner.checkpoints._recheck(self.db, preparation)
        for layer in parent._layers:
            self.owner.s.work_file_rework_applications.require_baseline(
                layer._ready['observation']['history']['material_observation'])


@closed
def read(owner, db, plan, complete):
    if not db.in_transaction:
        raise ValueError('V2完整组合读取须属于明确数据库事务。')
    if isinstance(owner.preparation, _Preparation):
        raise ValueError('V2完整拥有者不得重入同一遍历。')
    with database_observation(db):
        changes = db.total_changes
        traversal = _Traversal(owner, db, plan)
        resources = traversal.resources()
        original = owner.preparation
        owner.preparation = traversal.preparation
        traversal.active = True
        try:
            result = complete(db, plan)
            traversal.finish()
            if (db.total_changes != changes or owner._collect(db, plan) != traversal.plans or
                    traversal.resources() != resources):
                raise ValueError('完整组合读取末尾祖先、资源或数据库发生变化。')
            return result
        finally:
            traversal.active = False
            traversal.passes.clear()
            owner.preparation = original
