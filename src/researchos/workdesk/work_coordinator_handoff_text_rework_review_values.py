"""Exact review reservation/binding values shared by writer and replay."""
from copy import deepcopy

from .store import digest
from .work_coordinator_handoff_records import versioned
from . import work_coordinator_handoff_text_rework_contracts as c


def frozen_credential_revision(connection):
    """The original writer's pure connection digest, with no credential read."""
    from .connection_context import ConnectionContext
    ref = connection['credential_reference']
    return ConnectionContext(None, connection['provider'], ref['reference'], ref.get('file_revision')).credential_revision


def metadata(capability):
    cap = c.validate_capability(capability)
    from .work_coordinator_handoff_text_rework_values import review_input
    obs = cap['snapshot']['observation']; plan = obs['parent_plan_snapshot']
    driver = obs['driver']; unit = next(u for u in plan['units']
        if {'key': u['key'], 'revision': digest(u)} == obs['review_coordinate'])
    return {'item_id': cap['item_id'], 'source_item_revision': obs['task']['revision'],
        'driver_id': driver['id'], 'generation': driver['generation'], 'driver_revision': driver['revision'],
        'plan_id': plan['id'], 'plan_version': plan['version'], 'unit_key': unit['key'],
        'unit_revision': digest(unit), 'input_version': digest(review_input(cap)),
        'authorization': {k: obs['authorization'][k] for k in ('id', 'revision')}}


def linked_driver(previous, run, item_revision, *, revision, updated):
    if item_revision != run['item_revision'] + 1:
        raise ValueError('审阅业务绑定须准确推进一次修订。')
    return {**deepcopy(previous), 'item_revision': item_revision,
        'task_ids': sorted(set(previous['task_ids']) | {run['task_id']}), 'last_unit_run': run['id'],
        'reason': 'OWNED_EXECUTION_LINKED', 'revision_lineage': [*previous.get('revision_lineage', []),
            {'from': run['item_revision'], 'to': item_revision, 'unit_run_id': run['id']}][-100:],
        'revision': revision, 'updated': updated}


def response_for(run, spec_hash, dispatch):
    binding = {'id': run['item_id'], 'revision': dispatch['item_revision'], 'dispatch_id': dispatch['id']}
    return {'task_id': run['task_id'], 'spec_hash': spec_hash, 'launch_attempt': run['attempt_id'],
        'resume_mode': None, 'note': '', 'state': 'RESERVED',
        'resource_selection': run['input']['unit']['member']['reference'],
        'unit_run_id': run['id'], 'cooperative': run['cooperative'],
        'work_item': binding, 'work_items': [binding]}


def receipt_body(capability, run, driver, response):
    cap = c.validate_capability(capability)
    return versioned({'protocol': c.RECEIPT, 'operation': 'REVIEW_SEND',
        'request': cap['snapshot']['request'], 'request_hash': cap['snapshot']['request_hash'],
        'capability': {k: cap[k] for k in ('id', 'version')}, 'registration': None,
        'run': {'id': run['id'], 'version': digest(run)},
        'driver': {k: driver[k] for k in ('id', 'generation', 'revision')},
        'receipt': deepcopy(response), **c.PERMISSIONS})
