"""Reconstruct real proposal-event boundaries without mutable pointer views."""
from copy import deepcopy
import json

from .store import digest
from .work_coordinator_handoff_records import DELIVERY_KIND, COMPLETION_KIND
from . import work_coordinator_handoff_post_round_v3_continuation_proposals as proposals


def frame_at(owner, db, run, boundary):
    if owner.sources._reservation_event(db,run)>=boundary:
        raise ValueError('V3历史来源须先于准确事件边界。')
    attempt=owner.s.store.attempt(run['attempt_id'],db)
    result=owner.checkpoints._event(db,'attempt_result_saved',attempt['result'],boundary-1,run['task_id'],run['attempt_id'])
    files,_=owner.sources._saved_files(db,run)
    captures=[v for v in owner.sources._capture_events(db,run,files) if v['seq']<boundary]
    selected=captures[-1]['artifact'] if captures else None
    source=owner.sources._historical_evidence(db,run,selected)
    if source['raw']['blockers'] or not source['raw']['result_available']:
        raise ValueError('V3事件前沿须有完整准确已知退出。')
    ids={v['artifact']['id'] for v in captures}
    files['snapshots']=[v for v in files['snapshots'] if v['reference']['id'] in ids]
    files['output_state']='INTACT' if files['snapshots'] else 'NOT_RECORDED'
    return {'run':run,'task_spec':owner.s.store.task(run['task_id'],db)['spec'],
        'workspace':attempt['workspace'],'evidence':source['raw'],'source_evidence':source['full'],'files':files}


def history_at(owner,db,original,frames,versions,boundary,material):
    prior=[frame_at(owner,db,f['run'],boundary) for f in frames
        if owner.sources._reservation_event(db,f['run'])<boundary]
    driver=next(v for seq,v,_ in reversed(versions) if seq<boundary)
    from .work_coordinator_handoff_adjustment_sources import WorkCoordinatorHandoffAdjustmentSources
    records=WorkCoordinatorHandoffAdjustmentSources(owner.s)._records_before(db,owner._root_plan,boundary)
    phase=owner._phase(original['child_plan'],owner._eligible(original['child_plan'],prior),records)
    owner.s.work_file_rework_applications.require_baseline(material)
    return {**original,'task':{'id':original['child_plan']['item_id'],'revision':driver['item_revision']},
        'driver':driver,'frames':prior,'phase':phase,'material_observation':material,
        'usage':owner.s.work_units.usage(db,[f['run'] for f in prior])}


def proposal_checks(owner,db,original,frames,versions):
    plan=original['child_plan']; item=plan['item_id']
    ordered=owner._proposal_events(db,item)
    witnesses=owner._related(db,proposals.KIND,item)
    rows=[]; commands=[]; carriers=[]; previous=None
    from .work_coordinator_handoff_post_round_v3_command_scope import pending_proposal
    for boundary,row in ordered:
        if row['snapshot']['plan']!=owner.ref(plan):
            previous=row; continue
        found=[w for w in witnesses if w['snapshot']['proposal']==row]
        if len(found)!=1: raise ValueError('V3每条真实提案检查须有唯一完整回执。')
        witness=found[0]; body=witness['snapshot']
        h=history_at(owner,db,original,frames,versions,boundary,body['material_observation'])
        proof=proposals.reconstruct(owner,db,h,witness,previous,
            pending=pending_proposal(owner.s,db,witness))
        rows.append(row); commands.extend(proof['commands']); carriers.extend(proof['carriers'])
        previous=row
    owner._same(witnesses,[v for k,v,_ in carriers if k==proposals.KIND], 'V3提案回执记录全集不符。')
    owner._same([v for _,v in owner._events(db,proposals.EVENT,item)],witnesses,'V3提案回执事件全集不符。')
    return rows,commands,carriers
