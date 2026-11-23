"""Exact private V2 unit-send scope; installed dispatch routing remains closed."""
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
import uuid

from .store import digest
from .work_coordinator_handoff_records import fields
from .work_coordinator_handoff_post_round_successor_contracts import REQUEST_FIELDS
from .work_coordinator_handoff_post_round_candidates import LINEAGE_FIELD
from .work_coordinator_handoff_post_round_v2_candidates import LINEAGE

COMMAND = 'work-item.unit-send'
SAVEPOINT = 'post_round_v2_successor_command'
_pending = ContextVar('exact_post_round_v2_successor_command', default=None)


def pending_request(service, db, identity, request):
    state = _pending.get()
    return bool(state is not None and state['service'] is service and state['db'] is db and
        db.in_transaction and state['identity'] == identity == service.command_identity and
        state['kind'] == service.command_kind == COMMAND and state['request'] == request and
        state['request_hash'] == digest(request) == service.command_payload_hash)


def pending(service, db, capability):
    return (pending_request(service, db, capability['command_id'], capability['snapshot']['request']) and
        digest(capability['snapshot']['request']) == capability['snapshot']['request_hash'])


@contextmanager
def command(service, db, kind, request):
    """Only an exact V2 child command can acquire a temporary private witness.

    Enter SAVEPOINT before setting ContextVar. Both entry failure and failures
    caught by an outer transaction leave no pending witness. Preserve the task
    snapshot transaction's exact UUID/directory-identity rollback semantics.
    This helper neither creates capabilities nor validates committed history.
    """
    if kind != COMMAND:
        yield; return
    fields(request, REQUEST_FIELDS)
    plan = service.teamwork.current(db, request['id'])
    if not plan or plan.get(LINEAGE_FIELD, {}).get('protocol') != LINEAGE:
        yield; return
    if (not db.in_transaction or service.command_kind != kind or
            service.command_payload_hash != digest(request) or _pending.get() is not None):
        raise ValueError('V2命令作用域须属于准确完整请求的唯一写事务。')
    uuid.UUID(service.command_identity)
    state = {'service': service, 'db': db, 'identity': service.command_identity,
        'kind': kind, 'request': deepcopy(request), 'request_hash': digest(request)}
    db.execute('SAVEPOINT ' + SAVEPOINT)
    token = _pending.set(state)
    try:
        from .workspace import task_snapshot_transaction
        with task_snapshot_transaction(service.locations.data / 'tasks'):
            yield
            db.execute('RELEASE ' + SAVEPOINT)
    except BaseException:
        db.execute('ROLLBACK TO ' + SAVEPOINT)
        db.execute('RELEASE ' + SAVEPOINT)
        raise
    finally:
        _pending.reset(token)
