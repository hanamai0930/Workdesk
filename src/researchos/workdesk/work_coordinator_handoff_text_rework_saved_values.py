"""Pure candidate, same-generation transfer and saved command values."""
from copy import deepcopy

from .store import digest
from .work_transitions import plan_ref
from .work_coordinator_handoff_records import versioned
from .work_coordinator_handoff_text_rework_values import child_value
from .work_coordinator_handoff_text_rework_saved_records import ref, validate
from . import work_coordinator_handoff_text_rework_contracts as c

AUTH_BASE = {'id', 'revision', 'item_id', 'driver_generation', 'plan_id', 'plan_version',
    'max_attempts_total', 'dispatch_before', 'created', 'already_used', 'scope',
    'deadline_enforcement', 'cash_enforcement', 'native_permissions', 'command_id'}
DRIVER_BASE = {'id', 'item_id', 'item_revision', 'generation', 'kind', 'plan_id', 'plan_version',
    'state', 'reason', 'instance', 'previous_generation', 'task_ids', 'created', 'scope', 'revision', 'updated'}


def driver_ref(value):
    return {k: value[k] for k in ('id', 'generation', 'revision')}


def auth_ref(value):
    return {k: value[k] for k in ('id', 'revision')}


def request_for(history, plan, proposal):
    return {'id': history['task']['id'], 'revision': history['task']['revision'],
        'current_driver': driver_ref(history['driver']), 'plan': plan_ref(plan),
        'authorization': auth_ref(history['authorization']),
        'frontier': digest([{'run_id': f['run']['id'], 'evidence': f['evidence']['version']} for f in history['frames']]),
        'proposal': ref(proposal)}


def candidate_body(capability, history, proposal, check_event, request, seed, after_driver, after_auth, prepared_at, command):
    obs = capability['snapshot']['observation']; plan = obs['parent_plan_snapshot']
    impact = proposal['snapshot']['text_rework']
    if (proposal['snapshot']['state'] != 'COORDINATOR_TEXT_REWORK_PREVIEW' or
            impact is None or impact['blockers'] or request != request_for(history, plan, proposal) or
            history['usage']['unknown_requests'] or history['usage']['attempts'] + 2 > history['authorization']['max_attempts_total']):
        raise ValueError('返工保存须使用当前完整、未阻塞的新准确提案。')
    child = child_value(plan, history, capability, ref(proposal), impact, seed, command)
    from .work_coordinator_handoff_adjustment_records import driver_ref as check_driver, auth_ref as check_auth
    check_driver(after_driver); check_auth(after_auth); c.finite(prepared_at)
    if (after_driver['id'] != history['driver']['id'] or after_driver['generation'] != history['driver']['generation'] or
            after_driver['revision'] == history['driver']['revision'] or after_auth['id'] != history['authorization']['id'] or
            after_auth['revision'] == history['authorization']['revision'] or
            not seed['created'] <= prepared_at < history['authorization']['dispatch_before']):
        raise ValueError('返工候选须冻结同generation的新责任及原授权新修订。')
    return versioned({'protocol': c.CANDIDATE, 'root': capability['snapshot']['root'],
        'parent_plan': plan_ref(plan), 'capability': ref(capability), 'proposal': ref(proposal),
        'proposal_check_event': check_event, 'preview': impact, 'request': deepcopy(request),
        'request_hash': digest(request), 'parent_history': deepcopy(history), 'child_seed': deepcopy(seed),
        'child_plan': child, 'driver_before': driver_ref(history['driver']), 'driver_after': after_driver,
        'authorization_before': auth_ref(history['authorization']), 'authorization_after': after_auth,
        'lineage': child[c.LINEAGE_FIELD], 'prepared_at': prepared_at, **c.PERMISSIONS})


def transferred(candidate, *, transferred_at, driver_updated):
    validate(candidate, c.CANDIDATE); body = candidate['snapshot']; h = body['parent_history']; child = body['child_plan']
    c.finite(transferred_at); c.finite(driver_updated)
    if not candidate['created'] <= transferred_at <= driver_updated < h['authorization']['dispatch_before']:
        raise ValueError('返工转移须在实际候选之后、原最初截止之前。')
    auth = {k: deepcopy(h['authorization'][k]) for k in AUTH_BASE}
    auth.update(revision=body['authorization_after']['revision'], plan_id=child['id'], plan_version=child['version'],
        previous_authorization=body['authorization_before'], transferred_at=transferred_at,
        text_rework_capability=body['capability'], usage_at_transfer=deepcopy(h['usage']), command_id=candidate['command_id'])
    driver = {k: deepcopy(h['driver'][k]) for k in DRIVER_BASE}
    for name in ('last_unit_run', 'revision_lineage'):
        if name in h['driver']: driver[name] = deepcopy(h['driver'][name])
    driver.update(revision=body['driver_after']['revision'], updated=driver_updated,
        plan_id=child['id'], plan_version=child['version'], state='PAUSED', reason=c.PAUSE_REASON,
        previous_plan_reference=body['parent_plan'], text_rework_capability=body['capability'],
        text_rework_proposal=body['proposal'])
    return driver, auth


def save_payload(candidate, driver, authorization):
    return {'candidate': ref(candidate), 'parent_plan': candidate['snapshot']['parent_plan'],
        'child_plan': deepcopy(candidate['snapshot']['child_plan']), 'driver': deepcopy(driver),
        'authorization': deepcopy(authorization), 'no_dispatch': True}


def consumption(candidate, driver, auth, created):
    body = candidate['snapshot']; c.finite(created)
    return {'id': 'proposal-consumption:' + body['proposal']['id'], 'proposal': body['proposal'],
        'item_id': candidate['item_id'], 'kind': 'COORDINATOR_TEXT_REWORK', 'from_plan': body['parent_plan'],
        'to_plan': plan_ref(body['child_plan']), 'driver': driver_ref(driver), 'authorization': auth_ref(auth),
        'created': created, 'command_id': candidate['command_id'], 'no_dispatch': True}


def application_body(candidate, driver, auth, consumed):
    b = candidate['snapshot']
    return versioned({'protocol': c.APPLICATION, 'candidate': ref(candidate), 'request': b['request'],
        'request_hash': b['request_hash'], 'parent_plan': b['parent_plan'], 'child_plan': plan_ref(b['child_plan']),
        'proposal': b['proposal'], 'driver_before': b['driver_before'], 'driver_after': driver_ref(driver),
        'authorization_before': b['authorization_before'], 'authorization_after': auth_ref(auth),
        'consumption': consumed, **c.PERMISSIONS})


def registration_body(candidate, application, events):
    b = candidate['snapshot']
    return versioned({'protocol': c.REGISTRATION, 'root': b['root'], 'parent_plan': b['parent_plan'],
        'child_plan': plan_ref(b['child_plan']), 'capability': b['capability'], 'candidate': ref(candidate),
        'application': ref(application), 'proposal': b['proposal'], 'next_coordinate': b['preview']['next_coordinate'],
        'source_frontier': b['parent_history']['frontier'], 'events': events, **c.PERMISSIONS})


def response_for(candidate, application, registration, driver, auth):
    return {'transition': application['snapshot']['consumption'], 'plan': plan_ref(candidate['snapshot']['child_plan']),
        'driver': driver_ref(driver), 'authorization': auth_ref(auth), 'application': ref(application),
        'registration': ref(registration), 'no_dispatch': True, 'automatically_resumed': False}
