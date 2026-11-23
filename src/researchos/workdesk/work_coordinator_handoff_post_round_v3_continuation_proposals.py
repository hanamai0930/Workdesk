"""Strict V3 proposal values and per-check full receipt reconstruction.

The active runtime must reconstruct the caller's exact check boundary first.
This module neither routes generic capture nor grants pending Store authority.
"""
from copy import deepcopy
import json

from .store import digest
from .work_transitions import plan_ref
from .work_proposals import parse
from .work_coordinator_handoff_records import fields, versioned, PERMISSIONS, reference
from .work_coordinator_handoff_adjustment_records import finite, hashed, permissions
from .work_coordinator_handoff_adjustment_contracts import closed
from .work_coordinator_handoff_post_round_successor_contracts import bounded
from .work_coordinator_handoff_post_round_v3_responsibility_records import _uuid
from . import work_coordinator_handoff_post_round_v3_successor_contracts as first
from . import work_coordinator_handoff_post_round_v3_continuation_contracts as continuation

COMMAND = 'work-item.proposal-check'
KIND = 'work_coordinator_handoff_post_round_proposal_receipt_v3'
EVENT = 'coordinator_post_round_proposal_receipt_saved_v3'
PROTOCOL = 'COORDINATOR_FILE_HANDOFF_V3_PROPOSAL_RECEIPT_V1'
REQUEST_FIELDS = {'id','revision','current_driver','plan','authorization','frontier','run_id','result_version','proposal'}
SOURCE_FIELDS = ('run_id','task_id','attempt_id','owner','session_id','turn_id',
    'connection_fingerprint','result_digest','version')


def request_for(owner, history, source, previous):
    return {'id':history['task']['id'],'revision':history['task']['revision'],
        'current_driver':owner.s.work_drivers.reference(history['driver']),
        'plan':plan_ref(history['child_plan']),
        'authorization':owner.s.work_units.auth_ref(history['authorization']),
        'frontier':digest([{'run_id':f['run']['id'],'evidence':f['evidence']['version']} for f in history['frames']]),
        'run_id':source['run']['id'],'result_version':source['source_evidence']['version'],
        'proposal':owner.s.work_proposals.reference(previous)}


@closed
def snapshot(owner, history, request, previous):
    """Rebuild a standard proposal from the actual boundary and strict answer."""
    fields(request,REQUEST_FIELDS)
    plan=history['child_plan']; driver=history['driver']; frames=history['frames']
    sources=[f for f in frames if f['run']['plan_id']==plan['id'] and
        f['run']['generation']==driver['generation'] and f['run']['input']['unit']['role']=='coordinate']
    if not sources: raise ValueError('V3提案检查缺少本方案真实协调退出。')
    source=sources[-1]; run=source['run']; full=source['source_evidence']; frozen=run['input']
    context=frozen['handoff_coordination_context']
    if (request!=request_for(owner,history,source,previous) or
            context['protocol'] not in {first.PROTOCOL,continuation.COORDINATION} or
            not full['result_available'] or not full['exact_identity'] or full['state']!='COMPLETED' or full['blockers'] or
            driver['state']!='RESERVED' or driver['generation']!=run['generation'] or
            history['usage']['unknown_requests'] or history['usage']['attempts']>=history['authorization']['max_attempts_total'] or
            run['input']['unit']['member']['reference']!=plan['coordinator']['reference']):
        raise ValueError('V3提案须绑定本方案最新准确协调、当前责任和完整检查边界。')
    current={f['run']['id']:f for f in owner._eligible(plan,frames)}
    for dependency in frozen['dependencies']:
        actual=current.get(dependency['run_id'])
        if actual is None or dependency!={'unit_key':dependency['unit_key'],**actual['source_evidence']}:
            raise ValueError('V3提案原冻结依赖在检查时已变化。')
    decision=parse(full['answer']); target=None
    if decision['action'] not in frozen['coordination_context']['allowed_actions'] or decision['action'] not in continuation.ACTIONS:
        raise ValueError('V3本批提案只支持原冻结dispatch/pause。')
    state='PAUSE_SUGGESTED'
    if decision['action']=='dispatch':
        candidate=next((c for c in frozen['coordination_context']['candidates']
            if c['unit']['key']==decision['unit_key']),None)
        unit=next((u for u in plan['units'] if u['key']==decision['unit_key']),None)
        if (unit is None or unit['role']=='coordinate' or not candidate or candidate['unit']!=unit or
                not candidate['ready_after_proposal'] or candidate['retry_of'] is not None or decision['retry_of'] is not None):
            raise ValueError('V3提案目标须为原冻结候选中的声明普通单元首次。')
        owner._admit(plan,unit,frames,history['phase'])
        target={'key':unit['key'],'revision':digest(unit)}; state='VALID'
    return {'item_revision':history['task']['revision'],'driver':owner.s.work_drivers.reference(driver),
        'plan':plan_ref(plan),'authorization':owner.s.work_units.auth_ref(history['authorization']),
        'frontier':request['frontier'],'state':state,'decision':decision,'target':target,'problems':[],
        'source':{k:full[k] for k in SOURCE_FIELDS},'answer_digest':digest(full['answer']),
        'answer':full['answer'],'handoff_context':frozen['coordination_context']['handoff_context']}


def receipt_body(request, identity, previous, proposal, checked_event, material):
    """Keep the standard proposal wrapper unchanged; witness is independent."""
    return versioned({'protocol':PROTOCOL,'item_id':proposal['item_id'],
        'request':deepcopy(request),'command_id':identity,'command_kind':COMMAND,'request_hash':digest(request),
        'previous_proposal':deepcopy(previous),'proposal':deepcopy(proposal),
        'checked_event':deepcopy(checked_event),'material_observation':deepcopy(material),
        'response':{'proposal':deepcopy(proposal),'no_dispatch':True}, **PERMISSIONS})


@closed
def validate_receipt(row):
    fields(row,{'id','version','snapshot','item_id','command_id','created'})
    body=row['snapshot']; fields(body,{'protocol','version','item_id','request','command_id','command_kind',
        'request_hash','previous_proposal','proposal','checked_event','material_observation','response',*PERMISSIONS})
    hashed(body); permissions(body); bounded(body); finite(row['created']); _uuid(row['command_id'])
    from .work_coordinator_handoff_records import observation
    observation(body['material_observation'])
    fields(body['request'],REQUEST_FIELDS); proposal=body['proposal']
    fields(proposal,{'id','version','snapshot','item_id','created','command_id'})
    finite(proposal['created']); reference({'id':proposal['id'],'version':proposal['version']})
    from .work_coordinator_handoff_round_records import event_ref
    event_ref(body['checked_event'],'coordinator_proposal_checked')
    if body['previous_proposal'] is not None: reference(body['previous_proposal'])
    if (body['protocol']!=PROTOCOL or body['command_kind']!=COMMAND or row['id']!=KIND+':'+body['version'] or
            row['version']!=body['version'] or row['item_id']!=body['item_id'] or
            body['command_id']!=row['command_id'] or proposal['command_id']!=row['command_id'] or
            proposal['item_id']!=row['item_id'] or proposal['version']!=digest(proposal['snapshot']) or
            body['request_hash']!=digest(body['request']) or body['request']['id']!=row['item_id'] or
            body['response']!={'proposal':proposal,'no_dispatch':True} or
            not proposal['created']<=row['created'] or body['checked_event']['kind']!='coordinator_proposal_checked'):
        raise ValueError('V3提案完整回执须绑定原UUID/kind/hash、真实proposal和完整response。')
    return deepcopy(row)


def reconstruct(owner, db, history, row, previous, *, pending=False):
    """Prove a committed check at its independently rebuilt historical boundary.

    Pending capture must use a dedicated actual-row/event witness, then invoke
    the same semantic reconstruction before Store commits. It cannot call this
    committed-only helper with a fabricated command row.
    """
    validate_receipt(row); body=row['snapshot']; proposal=body['proposal']
    expected=snapshot(owner,history,body['request'],previous)
    event=owner.checkpoints._event(db,'coordinator_proposal_checked',proposal,None,
        expected['source']['task_id'],expected['source']['attempt_id'])
    raw_event=db.execute('SELECT * FROM events WHERE seq=?',(event['seq'],)).fetchone()
    actual_event=owner.checkpoints.live._event(raw_event)
    expected_body=receipt_body(body['request'],row['command_id'],owner.s.work_proposals.reference(previous),proposal,actual_event,history['material_observation'])
    command=db.execute('SELECT * FROM commands WHERE id=?',(row['command_id'],)).fetchone()
    witness_event=owner.checkpoints._event(db,EVENT,row)
    if (proposal['snapshot']!=expected or body!=expected_body or
            owner.s.collaboration.get(db,'work_proposal',proposal['id'])!=proposal or
            owner.s.collaboration.get(db,KIND,row['id'])!=row or
            (not pending and (command is None or command['kind']!=COMMAND or command['payload_hash']!=digest(body['request']) or
                json.loads(command['receipt'])!=body['response'] or command['created']<row['created'])) or
            not event['seq']<witness_event['seq'] or
            not proposal['created']<=raw_event['at']<=row['created']<history['authorization']['dispatch_before']):
        raise ValueError('V3真实提案检查、前指针、完整命令回执或事件边界不符。')
    attributed={**event,'task_id':expected['source']['task_id'],'attempt_id':expected['source']['attempt_id']}
    return {'proposal':deepcopy(proposal),'commands':[dict(command)] if command is not None else [],
        'carriers':[('work_proposal',proposal,attributed),(KIND,row,witness_event)]}
