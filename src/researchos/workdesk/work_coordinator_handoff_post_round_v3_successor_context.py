"""Complete live admission and pure reconstruction of V3 first input values."""
from copy import deepcopy
import time

from .store import digest
from .work_transitions import plan_ref
from .work_coordinator_handoff_records import versioned, PERMISSIONS
from .work_coordinator_handoff_adjustments import qualifications
from .work_coordinator_handoff_mixed_runtime import WorkCoordinatorHandoffMixedRuntime
from .work_coordinator_handoff_post_round_candidates import ref
from . import work_coordinator_handoff_post_round_v3_successor_contracts as c


def values(owner, db, original, frames, chain, unit, observed_at):
    """Values only. Every argument belongs to the caller's actual fresh proof."""
    plan = original['child_plan']; driver = chain['driver']; auth = original['authorization']
    phase = original['phase']; eligible = owner._eligible(plan, frames)
    dependencies = WorkCoordinatorHandoffMixedRuntime._admit(owner, plan, unit, frames, phase)
    return c.validate_observation(versioned({'protocol': c.OBSERVATION, 'scope': c.SCOPE,
        'root': original['root'], 'plan': plan_ref(plan), 'plan_snapshot': plan,
        'task': {'id': plan['item_id'], 'revision': driver['item_revision']}, 'driver': driver,
        'authorization': auth, 'saved_path': original['saved_path'],
        'responsibility_prefix': [ref(t) for t in chain['transitions']],
        'phase': phase, 'frontier': [owner.old.frontier_entry(f) for f in frames],
        'material_observation': original['material_observation'],
        'selected_unit': {'definition': unit, 'revision': digest(unit)},
        'qualifications': qualifications(plan, phase, eligible), 'dependencies': dependencies,
        'usage': owner.s.work_units.usage(db, [f['run'] for f in frames]),
        'observed_at': observed_at, **PERMISSIONS}))


def observe(service, db, plan, unit):
    from .work_coordinator_handoff_post_round_v3_responsibility_current import WorkCoordinatorHandoffPostRoundV3ResponsibilityCurrent as Current
    owner = Current(service); h = owner.read(db, plan)
    item = service.work_items.item(db, plan['item_id'])
    if (service.teamwork.current(db,item['id']) != plan or item['revision'] != h['driver']['item_revision'] or
            service.work_drivers.current(db,item['id']) != h['driver']):
        raise ValueError('V3首次准入当前任务、方案或责任已变化。')
    problems = [i['message'] for i in h['responsibility_conditions'] if i['code'] != 'V3_LIFECYCLE_CLOSED']
    if (problems or h['driver']['instance'] != service.instance or
            h['usage']['unknown_requests'] or service.work_drivers.execution_issues(db, item['id'], h['driver'])):
        raise ValueError('V3首次准入责任实例、资源、材料或已知退出受阻：' + '；'.join(problems))
    service.work_flows.require_stopped(db, item['id'])
    selected = service.resources.selection(db, unit['member']['reference']['member_id'])
    if selected['reference'] != unit['member']['reference']:
        raise ValueError('V3首次准入实际成员或连接已变化。')
    obs = values(owner, db, h, h['frames'], h['responsibility'], unit, time.time())
    for frame in h['current_frames']:
        if service.work_units.evidence(db,frame['run']) != frame['source_evidence']:
            raise ValueError('V3首次准入末尾当前来源已变化。')
    service.work_file_rework_applications.require_baseline(h['material_observation'])
    if time.time() >= h['authorization']['dispatch_before']:
        raise ValueError('V3首次完整准入结束已过原截止。')
    return deepcopy(obs)


def checked(service, db, request):
    from .work_coordinator_handoff_post_round_successor_contracts import REQUEST_FIELDS
    from .work_coordinator_handoff_records import fields
    fields(request, REQUEST_FIELDS)
    item=service.work_items.checked(db,request); service.work_items.project(db,item['project_id'])
    plan=service.teamwork.current(db,item['id'])
    unit=next((u for u in plan['units'] if request['unit']=={'key':u['key'],'revision':digest(u)}),None)
    if unit is None: raise ValueError('V3首次单元完整修订不符。')
    from .work_coordinator_handoff_post_round_v3_runtime import present,WorkCoordinatorHandoffPostRoundV3Runtime
    if present(service,db,plan):
        owner=WorkCoordinatorHandoffPostRoundV3Runtime(service); h=owner.read(db,plan); owner.admission(db,plan,h)
        owner._admit(plan,unit,h['frames'],h['phase'])
        from .work_coordinator_handoff_post_round_v3_continuation_contracts import request_for
        current=service.work_proposals.current(db,item['id'])
        expected=request_for({**h,'plan':plan_ref(plan),'selected_unit':{'definition':unit,'revision':digest(unit)},
            'proposal':service.work_proposals.reference(current) if unit['role']!='coordinate' else None})
        if request!=expected: raise ValueError('V3续行九字段请求不等于准确当前准入。')
        return item,h['driver'],plan
    obs=observe(service,db,plan,unit)
    if request != c.request_for(obs): raise ValueError('V3首次九字段请求不等于准确当前准入。')
    return item,obs['driver'],plan
