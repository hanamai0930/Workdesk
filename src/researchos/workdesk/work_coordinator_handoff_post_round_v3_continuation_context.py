"""Per-step V3 values built from the caller's actual reconstructed frontier.

No routing or activation lives here. Historical first V9 remains owned by the
unchanged first-coordinate builder. Complete runtime owners supply every value
and independently close the referenced save/capability/receipt namespace.
"""
from copy import deepcopy

from .store import digest
from .work_transitions import plan_ref
from .work_coordinator_handoff_records import versioned, PERMISSIONS
from .work_coordinator_handoff_adjustments import qualifications
from .work_coordinator_handoff_mixed_runtime import WorkCoordinatorHandoffMixedRuntime
from .work_coordinator_handoff_post_round_candidates import ref
from . import work_coordinator_handoff_post_round_v3_continuation_contracts as c


def observation(owner, db, original, frames, chain, driver, records, unit, material,
        first_capability, first_receipt, proposal, request, observed_at):
    plan=original['child_plan']; phase=owner._phase(plan,owner._eligible(plan,frames),records)
    eligible=owner._eligible(plan,frames)
    value={'protocol':c.OBSERVATION,'scope':c.SCOPE,'root':plan_ref(owner._root_plan),
        'plan':plan_ref(plan),'plan_snapshot':plan,
        'task':{'id':plan['item_id'],'revision':driver['item_revision']},
        'driver':driver,'authorization':original['authorization'],
        'saved_registration':ref(original['registration']),
        'saved_path_reference':c.path_reference(original['saved_path']),
        'responsibility_prefix':[ref(t) for t in chain['transitions']],
        'first_capability':ref(first_capability),'first_receipt':ref(first_receipt),
        'phase':phase,'frontier':[owner.old.frontier_entry(f) for f in frames],
        'material_observation':material,'selected_unit':{'definition':unit,'revision':digest(unit)},
        'qualifications':qualifications(plan,phase,eligible),
        'dependencies':WorkCoordinatorHandoffMixedRuntime._admit(owner,plan,unit,frames,phase),
        'usage':owner.s.work_units.usage(db,[f['run'] for f in frames]),
        'proposal':proposal,'observed_at':observed_at,'request':request,'request_hash':digest(request), **PERMISSIONS}
    return c.validate_observation(versioned(value))


def path(owner, db, original, frames, chain, driver, records, material,
        first_capability, continuation_capability):
    plan=original['child_plan']; phase=owner._phase(plan,owner._eligible(plan,frames),records)
    boundary=owner._driver_event(db,driver)
    history=[{'event':owner.checkpoints.live._event(db.execute('SELECT * FROM events WHERE seq=?',(seq,)).fetchone()),
        'driver':value} for seq,value,_ in owner._versions if seq<=boundary]
    return c.validate_path(versioned({'protocol':c.PATH,'scope':c.SCOPE,
        'root':plan_ref(owner._root_plan),'plan':plan_ref(plan),
        'saved_registration':ref(original['registration']),
        'saved_path_reference':c.path_reference(original['saved_path']),
        'responsibility_prefix':[ref(t) for t in chain['transitions']],
        'first_capability':ref(first_capability),'continuation_capability':ref(continuation_capability),
        'driver_history':history,'driver':driver,'authorization':original['authorization'],
        'history_frontier':[owner.old.frontier_entry(f) for f in frames],
        'current_qualifications':qualifications(plan,phase,owner._eligible(plan,frames)),
        'material_observation':material,'phase':phase, **PERMISSIONS}))


def input_value(owner, plan, unit, task, driver, auth, frames, material, records, saved_path, proposal=None):
    """Historical and live callers share one exact pure input algorithm."""
    c.validate_path(saved_path)
    phase=owner._phase(plan,owner._eligible(plan,frames),records)
    if (phase!=saved_path['phase'] or material!=saved_path['material_observation'] or
            driver!=saved_path['driver'] or auth!=saved_path['authorization'] or
            task!={'id':plan['item_id'],'revision':driver['item_revision']} or
            saved_path['plan']!=plan_ref(plan) or
            saved_path['history_frontier']!=[owner.old.frontier_entry(f) for f in frames]):
        raise ValueError('V3本次输入的完整边界与已重建动态路径不符。')
    deps=WorkCoordinatorHandoffMixedRuntime._admit(owner,plan,unit,frames,phase)
    value={'item_id':plan['item_id'],'item_revision':task['revision'],'plan_id':plan['id'],
        'plan_version':plan['version'],'work_requirements':plan['work_requirements'],'unit':unit,
        'dependencies':deps,'check_evidence':[{'unit_key':d['unit_key'],
            'artifact':d['file_artifacts']['reference'],'check_status':d['check_status']} for d in deps],
        'file_artifacts':[{'unit_key':d['unit_key'],**d['file_artifacts']} for d in deps if d['file_artifacts']['state']=='CURRENT']}
    context={'protocol':c.COORDINATION if unit['role']=='coordinate' else c.UNIT,'scope':c.SCOPE,
        'root':saved_path['root'],'marker':plan['coordinator_file_handoff_root'],'plan':plan_ref(plan),
        'task':task,'driver':driver,'authorization':auth,'phase':phase,'saved_path':saved_path,
        'prior_frontier':saved_path['history_frontier'],'material_observation':material, **PERMISSIONS}
    if unit['role']=='coordinate':
        if proposal is not None: raise ValueError('V3原声明协调续行不能消费普通提案。')
        old=owner.old._context(plan,task,driver,auth,owner._eligible(plan,frames),material)
        context.update(selected_unit={'definition':unit,'revision':digest(unit)},
            work_requirements=plan['work_requirements'],configuration=old['configuration'],
            units=[{**entry,'qualification':q['qualification']}
                for entry,q in zip(old['units'],saved_path['current_qualifications'])],allowed_actions=c.ACTIONS)
        context=c.validate_context(versioned(context)); forecast=owner._forecast(plan,context,frames,unit)
        forecast.update(scope=c.SCOPE,allowed_actions=c.ACTIONS,previous_round=deepcopy(plan['coordination_revision']))
        value.update(handoff_coordination_context=context,coordination_context=forecast)
    else:
        if proposal is None: raise ValueError('V3普通续行须消费本前沿最新准确VALID提案。')
        context.update(unit_revision=digest(unit),proposal=proposal)
        value.update(handoff_unit_context=c.validate_context(versioned(context)),coordination_proposal=proposal)
    return deepcopy(value)


def intent(owner, plan, frozen, frames):
    context=frozen['handoff_unit_context']; relation=plan['file_handoffs'][0]
    source=next(f for f in owner._eligible(plan,frames) if f['run']['unit_key']==relation['source_unit'])
    full=source['source_evidence']; baseline=context['material_observation']
    owner.s.work_artifacts.require(full,'checked' if source['run']['input']['unit']['checks'] or
        frozen['unit']['dependency_gate']=='checked' else 'files')
    return c.validate_intent(versioned({'protocol':c.INTENT,'scope':c.SCOPE,
        'item_id':plan['item_id'],'item_revision':context['task']['revision'],
        'root':context['root'],'marker':plan['coordinator_file_handoff_root'],'plan':plan_ref(plan),
        'saved_path':context['saved_path'],'relation':relation,'driver':context['driver'],
        'authorization':context['authorization'],'source_run':source['run']['id'],'source_evidence':full,
        'target_revision':digest(frozen['unit']),'protected_history':context['prior_frontier'],
        'baseline':baseline,'input_template_version':digest(frozen),'frontier':context['prior_frontier']}))


def request_from_run(owner, run):
    """Every step reconstructs its own original nine fields, including V9."""
    frozen=run['input']; context=frozen.get('handoff_coordination_context') or frozen['handoff_unit_context']
    return {'id':run['item_id'],'revision':context['task']['revision'],
        'plan':{'id':run['plan_id'],'version':run['plan_version']},
        'current_driver':{k:context['driver'][k] for k in ('id','generation','revision')},
        'authorization':{k:context['authorization'][k] for k in ('id','revision')},
        'frontier':digest([{'run_id':f['run_id'],'evidence':f['evidence']['version']} for f in context['prior_frontier']]),
        'unit':{'key':run['unit_key'],'revision':run['unit_revision']},'retry_of':None,
        'proposal':frozen.get('coordination_proposal',{}).get('reference')}
