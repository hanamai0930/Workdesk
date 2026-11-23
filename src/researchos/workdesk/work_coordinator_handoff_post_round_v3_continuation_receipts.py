"""Per-run continuation receipt reconstruction; first V9 keeps its old codec."""
from copy import deepcopy
import json

from .store import digest
from .work_transitions import plan_ref
from .work_coordinator_handoff_records import versioned, fields
from .work_coordinator_handoff_adjustment_records import finite
from .work_coordinator_handoff_post_round_v3_continuation_context import request_from_run
from .work_coordinator_handoff_post_round_v3_command_scope import pending_request
from . import work_coordinator_handoff_post_round_v3_continuation_contracts as c

KIND = 'work_coordinator_handoff_post_round_declared_continuation_receipt_v3'
EVENT = 'coordinator_post_round_declared_continuation_receipt_saved_v3'
PROTOCOL = 'COORDINATOR_FILE_HANDOFF_V3_DECLARED_CONTINUATION_RECEIPT_V1'


def response_for(service, db, run, dispatch):
    task=service.store.task(run['task_id'],db)
    binding={'id':run['item_id'],'revision':dispatch['item_revision'],'dispatch_id':dispatch['id']}
    return {'task_id':run['task_id'],'spec_hash':task['spec_hash'],'launch_attempt':run['attempt_id'],
        'resume_mode':None,'note':'','state':'RESERVED',
        'resource_selection':run['input']['unit']['member']['reference'],'unit_run_id':run['id'],
        'cooperative':run['cooperative'],'work_item':binding,'work_items':[binding]}


def body_for(owner, plan, run, driver, capability, response):
    context=run['input'].get('handoff_coordination_context') or run['input']['handoff_unit_context']
    if context['protocol'] not in {c.COORDINATION,c.UNIT}:
        raise ValueError('V3续行回执不能重编码原首次V9回执。')
    return versioned({'protocol':PROTOCOL,'item_id':plan['item_id'],'plan':plan_ref(plan),
        'capability':owner.ref(capability),'run':deepcopy(run),'driver':deepcopy(driver),
        'request':request_from_run(owner,run),'receipt':deepcopy(response)})


def reconstruct(owner, db, plan, run, linked_driver, dispatch, capability):
    """A complete child owner must call once for every actual run and link.

    This selects a row by its real run identity. The enclosing owner compares
    the full row/event/command namespace against the union of these results.
    """
    service=owner.s; request=request_from_run(owner,run)
    rows=[v for v in owner._related(db,KIND,plan['item_id']) if v.get('snapshot',{}).get('run',{}).get('id')==run['id']]
    command=db.execute('SELECT * FROM commands WHERE id=?',(run['command_id'],)).fetchone()
    live=command is None and pending_request(service,db,run['command_id'],request)
    if not rows:
        if live: return {'rows':[],'commands':[],'carriers':[]}
        raise ValueError('V3续行缺少本次原命令的完整回执见证。')
    if len(rows)!=1 or dispatch is None:
        raise ValueError('V3每次续行须有唯一完整回执和真实dispatch。')
    response=response_for(service,db,run,dispatch)
    body=body_for(owner,plan,run,linked_driver,capability,response); row=rows[0]
    fields(row,{'id','version','snapshot','item_id','command_id','created'}); finite(row['created'])
    expected={'id':KIND+':'+body['version'],'version':body['version'],'snapshot':body,
        'item_id':plan['item_id'],'command_id':run['command_id'],'created':row['created']}
    event=owner.checkpoints._event(db,EVENT,row)
    if (row!=expected or service.collaboration.get(db,KIND,row['id'])!=row or
            linked_driver['last_unit_run']!=run['id'] or
            not dispatch['created']<=linked_driver['updated']<=row['created'] or
            event['seq']<=owner._driver_event(db,linked_driver) or
            (not live and (command is None or command['kind']!='work-item.unit-send' or
                command['payload_hash']!=digest(request) or json.loads(command['receipt'])!=response or
                command['created']<row['created']))):
        raise ValueError('V3续行原UUID/九字段hash/fullreceipt或当次真实link不符。')
    return {'rows':[row],'commands':[dict(command)] if command is not None else [],
        'carriers':[(KIND,row,event)]}
