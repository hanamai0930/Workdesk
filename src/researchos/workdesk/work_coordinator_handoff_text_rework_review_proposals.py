"""Pre-save review proposal values and complete check-response witness.

The typed PROPOSAL row owns the standard proposal, its exact request and full
response. It precedes registration; successor PROPOSAL_CHECK receipts retain
their separate saved-plan meaning.
"""
from copy import deepcopy

from .store import digest
from .work_transitions import plan_ref
from .work_coordinator_handoff_records import fields, versioned
from .work_coordinator_handoff_historical_assets import _uuid
from .work_coordinator_handoff_text_rework_values import preview
from . import work_coordinator_handoff_text_rework_contracts as c

COMMAND = 'work-item.proposal-check'
REQUEST_FIELDS = {'id', 'revision', 'current_driver', 'plan', 'authorization', 'frontier',
    'run_id', 'result_version', 'proposal'}
SOURCE_FIELDS = ('run_id', 'task_id', 'attempt_id', 'owner', 'session_id', 'turn_id',
    'connection_fingerprint', 'result_digest', 'version')
BODY_FIELDS = {'protocol', 'version', 'capability', 'request', 'request_hash', 'previous_proposal',
    'proposal', 'checked_event', 'preview', 'material_observation', 'response', *c.PERMISSIONS}


def request_for(history, run, frame, previous):
    return {'id': history['task']['id'], 'revision': history['task']['revision'],
        'current_driver': {k: history['driver'][k] for k in ('id', 'generation', 'revision')},
        'plan': {'id': run['plan_id'], 'version': run['plan_version']},
        'authorization': {k: history['authorization'][k] for k in ('id', 'revision')},
        'frontier': digest([{'run_id': f['run']['id'], 'evidence': f['evidence']['version']} for f in history['frames']]),
        'run_id': run['id'], 'result_version': frame['source_evidence']['version'],
        'proposal': {k: previous[k] for k in ('id', 'version')} if previous else None}


def snapshot(capability, history, run, frame, exit_event, request, previous, observed_at):
    fields(request, REQUEST_FIELDS); c.finite(observed_at)
    full = frame['source_evidence']; obs = capability['snapshot']['observation']
    if (request != request_for(history, run, frame, previous) or not full['exact_identity'] or
            full['blockers'] or full['state'] != 'COMPLETED' or not full['result_available'] or
            history['driver']['state'] != 'RESERVED' or history['driver']['generation'] != run['generation'] or
            history['usage']['unknown_requests'] or observed_at >= history['authorization']['dispatch_before']):
        raise ValueError('返工提案检查须绑定准确审阅退出、当前责任和原授权。')
    decision, impact, problems, state = None, None, [], 'INVALID'
    try:
        decision = c.parse_reply(full['answer'])
        if decision['action'] == 'rework_units':
            impact = preview(obs['parent_plan_snapshot'], history, capability, run, exit_event, decision,
                observed_at=observed_at)
            state = 'COORDINATOR_TEXT_REWORK_PREVIEW'
        else:
            state = 'PAUSE_SUGGESTED'
    except ValueError as exc:
        problems.append(str(exc))
    return {'item_revision': history['task']['revision'], 'driver': request['current_driver'],
        'plan': request['plan'], 'authorization': request['authorization'], 'frontier': request['frontier'],
        'state': state, 'decision': decision, 'target': None, 'problems': problems,
        'source': {k: full[k] for k in SOURCE_FIELDS}, 'answer_digest': digest(full['answer']),
        'answer': full['answer'] if len(full['answer']) <= 32000 else None,
        'text_rework': impact, 'handoff_context': run['input']['handoff_coordination_context']}


def witness_body(capability, request, previous, proposal, checked_event):
    return versioned({'protocol': c.PROPOSAL, 'capability': {k: capability[k] for k in ('id', 'version')},
        'request': deepcopy(request), 'request_hash': digest(request),
        'previous_proposal': {k: previous[k] for k in ('id', 'version')} if previous else None,
        'proposal': deepcopy(proposal), 'checked_event': deepcopy(checked_event),
        'preview': deepcopy(proposal['snapshot']['text_rework']),
        'material_observation': deepcopy(capability['snapshot']['observation']['material_observation']),
        'response': {'proposal': deepcopy(proposal), 'no_dispatch': True}, **c.PERMISSIONS})


@c.closed
def validate(row):
    fields(row, {'id', 'version', 'snapshot', 'item_id', 'created', 'command_id'})
    body = row['snapshot']; fields(body, BODY_FIELDS); c.hashed(body); c.permissions(body)
    fields(body['request'], REQUEST_FIELDS); c.reference(body['capability']); c.event_ref(body['checked_event'])
    c.finite(row['created']); _uuid(row['command_id'])
    proposal = body['proposal']; fields(proposal, {'id', 'version', 'snapshot', 'item_id', 'created', 'command_id'})
    c.finite(proposal['created']); _uuid(proposal['command_id'])
    _uuid(proposal['id'].removeprefix('work-proposal:'))
    if (body['protocol'] != c.PROPOSAL or row['version'] != body['version'] or
            row['id'] != c.KINDS[c.PROPOSAL] + ':' + body['version'] or
            not proposal['id'].startswith('work-proposal:') or proposal['version'] != digest(proposal['snapshot']) or
            proposal['command_id'] != row['command_id'] or proposal['item_id'] != row['item_id'] or
            body['request']['id'] != row['item_id'] or body['request_hash'] != digest(body['request']) or
            body['checked_event']['kind'] != 'coordinator_proposal_checked' or
            body['preview'] != proposal['snapshot']['text_rework'] or
            body['response'] != {'proposal': proposal, 'no_dispatch': True}):
        raise ValueError('返工保存前提案须有准确原请求、标准proposal及完整原响应。')
    if body['previous_proposal'] is not None:
        c.reference(body['previous_proposal'])
    if body['preview'] is not None:
        c.validate_preview(body['preview'])
    return deepcopy(row)
