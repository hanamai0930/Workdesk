"""Live read-only admission for the actual second saved post-round successor."""
from copy import deepcopy
import time

from .store import digest
from .work_transitions import plan_ref
from .work_read_observation import database_observation
from .work_coordinator_handoff_records import versioned, PERMISSIONS
from .work_coordinator_handoff_adjustment_contracts import closed
from .work_coordinator_handoff_adjustments import qualifications, protected
from .work_coordinator_handoff_roots import CONFIG_FIELDS
from .work_coordinator_handoff_post_round_v2_saved_tree import WorkCoordinatorHandoffPostRoundV2SavedTree
from .work_coordinator_handoff_post_round_v2_successor_contracts import (
    OBSERVATION, OBS_SCOPE, validate_observation, capability_draft, validate_draft)


class WorkCoordinatorHandoffPostRoundV2SuccessorContext:
    def __init__(self, service): self.s = service

    @closed
    def observe(self, db, plan, coordinate_key):
        if not db.in_transaction: raise ValueError('后继准入须属于明确同一读取事务。')
        with database_observation(db):
            before = db.total_changes
            runtime = WorkCoordinatorHandoffPostRoundV2SavedTree(self.s)
            resources = runtime._resources(db, plan)
            h = runtime.read(db, plan)
            item = self.s.work_items.item(db, plan['item_id'])
            self.s.work_units.checked(db, {'id': item['id'], 'revision': item['revision'],
                'current_driver': self.s.work_drivers.reference(h['driver']), 'plan': plan_ref(plan)})
            self.s.work_flows.require_stopped(db, item['id'])
            unit = next((u for u in plan['units'] if u['key'] == coordinate_key), None)
            if unit is None or unit['role'] != 'coordinate': raise ValueError('后继只选择原声明coordinate。')
            dependencies = runtime._admit(plan, unit, h['frames'], h['phase'])
            selected = self.s.resources.selection(db, unit['member']['reference']['member_id'])
            if selected['reference'] != unit['member']['reference']: raise ValueError('后继成员、连接或额度来源已变化。')
            obs = validate_observation(versioned({'protocol': OBSERVATION, 'root': h['root'],
                'plan': plan_ref(plan), 'plan_snapshot': plan, 'task': h['task'], 'driver': h['driver'],
                'authorization': h['authorization'], 'saved_path': h['saved_path'],
                'phase': h['phase'], 'frontier': h['frontier'], 'material_observation': h['material_observation'],
                'selected_unit': {'definition': unit, 'revision': digest(unit)},
                'qualifications': qualifications(plan, h['phase'], h['current_frames']),
                'dependencies': dependencies, 'usage': h['usage'],
                'protected_keys': sorted(protected(plan['units'], plan['file_handoffs'][0])),
                'configuration_version': digest({k: plan[k] for k in CONFIG_FIELDS}),
                'observed_at': time.time(), 'scope': OBS_SCOPE, **PERMISSIONS}))
            draft = validate_draft(capability_draft(obs), obs)
            # Rebuild all original layers again after composing the contracts;
            # stored hashes and a read transaction do not freeze external files.
            final_runtime = WorkCoordinatorHandoffPostRoundV2SavedTree(self.s)
            again = final_runtime.read(db, plan)
            final_runtime.checkpoints._recheck(db, {'frames': again['frames'],
                'observation': final_runtime._ready['observation'],
                'current_material_observation': again['material_observation']})
            for layer in final_runtime._layers:
                self.s.work_file_rework_applications.require_baseline(layer._ready['observation']['history']['material_observation'])
            for frame in again['current_frames']:
                if self.s.work_units.evidence(db, frame['run']) != frame['source_evidence']:
                    raise ValueError('后继准入末尾当前沿用来源发生变化。')
            comparable = lambda v: {**v, 'usage': {k: x for k, x in v['usage'].items() if k != 'elapsed_observed_seconds'}}
            stable_selection = lambda v: {k: x for k, x in v.items() if k != 'selected_at'}
            if (comparable(h) != comparable(again) or before != db.total_changes or
                    self.s.teamwork.current(db, item['id']) != plan or
                    final_runtime._resources(db, plan) != resources or
                    stable_selection(self.s.resources.selection(db, unit['member']['reference']['member_id'])) != stable_selection(selected)):
                raise ValueError('后继准入末尾完整历史、当前方案、数据库或实际资源发生变化。')
            if not obs['driver']['updated'] <= time.time() < obs['authorization']['dispatch_before']:
                raise ValueError('V2后继观察完成前最初授权期限已到。')
            return deepcopy({'observation': obs, 'capability_draft': draft})
