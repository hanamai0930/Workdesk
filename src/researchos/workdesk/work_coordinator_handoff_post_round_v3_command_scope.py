"""Exact private V3 unit-send scope; installed dispatch routing remains closed."""
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
import uuid
import json

from .store import canonical, digest
from .work_coordinator_handoff_records import fields
from .work_coordinator_handoff_post_round_successor_contracts import REQUEST_FIELDS
from .work_coordinator_handoff_post_round_candidates import LINEAGE_FIELD
from .work_coordinator_handoff_post_round_v3_candidates import LINEAGE

COMMAND = 'work-item.unit-send'
SAVEPOINT = 'post_round_v3_successor_command'
_pending = ContextVar('exact_post_round_v3_successor_command', default=None)


def pending_request(service, db, identity, request, kind=COMMAND):
    state = _pending.get()
    return bool(state is not None and state['service'] is service and state['db'] is db and
        type(state['owner']) is _CommandOwner and state['owner'].state is state and state['owner'].active and
        db.in_transaction and state['identity'] == identity == service.command_identity and
        state['kind'] == service.command_kind == kind and state['request'] == request and
        state['request_hash'] == digest(request) == service.command_payload_hash and
        state['instance'] == service.instance and
        db.execute('SELECT 1 FROM commands WHERE id=?',(identity,)).fetchone() is None)


class _CommandOwner:
    def __init__(self, state):
        self.state = state
        self.active = True


def bind_capability(service, db, application, capability):
    from .work_coordinator_handoff_post_round_v3_runtime import WorkCoordinatorHandoffPostRoundV3Runtime
    state = _pending.get()
    if (not pending_request(service,db,capability['command_id'],capability['snapshot']['request']) or
            type(application) is not WorkCoordinatorHandoffPostRoundV3Runtime or application.s is not service or
            state.get('capability') is not None):
        raise ValueError('V3首次能力须由当前唯一完整运行拥有者写入。')
    from .work_coordinator_handoff_post_round_v3_successor_contracts import KIND, OPENED
    rows = list(db.execute('SELECT * FROM events WHERE kind=? AND payload=?',(OPENED,
        canonical(capability))))
    if (service.collaboration.get(db,KIND,capability['id']) != capability or len(rows)!=1 or
            rows[0]['task_id'] is not None or rows[0]['attempt_id'] is not None):
        raise ValueError('V3首次能力拥有者缺少准确已写记录与事件。')
    state.update(application=application,capability=deepcopy(capability),opened_seq=rows[0]['seq'])


def pending(service, db, capability):
    if not pending_request(service,db,capability['command_id'],capability['snapshot']['request']): return False
    state = _pending.get()
    from .work_coordinator_handoff_post_round_v3_runtime import WorkCoordinatorHandoffPostRoundV3Runtime
    from .work_coordinator_handoff_post_round_v3_successor_contracts import KIND, OPENED
    event = db.execute('SELECT * FROM events WHERE seq=?',(state.get('opened_seq'),)).fetchone()
    return bool(type(state.get('application')) is WorkCoordinatorHandoffPostRoundV3Runtime and
        state['application'].s is service and state.get('capability')==capability and
        service.collaboration.get(db,KIND,capability['id'])==capability and event is not None and
        event['kind']==OPENED and json.loads(event['payload'])==capability and
        event['task_id'] is None and event['attempt_id'] is None and
        digest(capability['snapshot']['request'])==capability['snapshot']['request_hash'])


def bind_continuation(service,db,application,capability):
    from .work_coordinator_handoff_post_round_v3_runtime import WorkCoordinatorHandoffPostRoundV3Runtime
    from . import work_coordinator_handoff_post_round_v3_continuation_contracts as c
    state=_pending.get(); request=capability['snapshot']['observation']['request']
    c.validate_capability(capability)
    if (not pending_request(service,db,capability['command_id'],request) or
            type(application) is not WorkCoordinatorHandoffPostRoundV3Runtime or application.s is not service or
            state.get('continuation') is not None):
        raise ValueError('V3续行开启须属于本次准确运行拥有者。')
    events=list(db.execute('SELECT * FROM events WHERE kind=? AND payload=?',(c.OPENED,canonical(capability))))
    if (service.collaboration.get(db,c.KIND,capability['id'])!=capability or len(events)!=1 or
            events[0]['task_id'] is not None or events[0]['attempt_id'] is not None):
        raise ValueError('V3续行开启缺少本命令已写能力与事件。')
    state.update(continuation_owner=application,continuation=deepcopy(capability),continuation_seq=events[0]['seq'])


def pending_continuation(service,db,capability):
    from . import work_coordinator_handoff_post_round_v3_continuation_contracts as c
    from .work_coordinator_handoff_post_round_v3_runtime import WorkCoordinatorHandoffPostRoundV3Runtime
    request=capability['snapshot']['observation']['request']
    if not pending_request(service,db,capability['command_id'],request): return False
    state=_pending.get(); event=db.execute('SELECT * FROM events WHERE seq=?',(state.get('continuation_seq'),)).fetchone()
    return bool(type(state.get('continuation_owner')) is WorkCoordinatorHandoffPostRoundV3Runtime and
        state['continuation_owner'].s is service and state.get('continuation')==capability and
        service.collaboration.get(db,c.KIND,capability['id'])==capability and event is not None and
        event['kind']==c.OPENED and json.loads(event['payload'])==capability and
        event['task_id'] is None and event['attempt_id'] is None)


def bind_proposal(service,db,application,witness):
    from .work_coordinator_handoff_post_round_v3_capture import Capture
    from . import work_coordinator_handoff_post_round_v3_continuation_proposals as p
    p.validate_receipt(witness); b=witness['snapshot']; state=_pending.get()
    if (not pending_request(service,db,witness['command_id'],b['request'],p.COMMAND) or
            type(application) is not Capture or application.s is not service or state.get('proposal') is not None):
        raise ValueError('V3提案pending须由本命令准确capture拥有者建立。')
    checked=db.execute('SELECT * FROM events WHERE seq=?',(b['checked_event']['seq'],)).fetchone()
    events=list(db.execute('SELECT * FROM events WHERE kind=? AND payload=?',(p.EVENT,canonical(witness))))
    source=b['proposal']['snapshot']['source']
    if (service.collaboration.get(db,p.KIND,witness['id'])!=witness or
            service.collaboration.get(db,'work_proposal',b['proposal']['id'])!=b['proposal'] or checked is None or
            checked['kind']!='coordinator_proposal_checked' or json.loads(checked['payload'])!=b['proposal'] or
            checked['task_id']!=source['task_id'] or checked['attempt_id']!=source['attempt_id'] or
            len(events)!=1 or events[0]['seq']<=checked['seq'] or events[0]['task_id'] is not None or events[0]['attempt_id'] is not None):
        raise ValueError('V3提案pending须绑定准确已写proposal、原归属checked和完整回执事件。')
    state.update(proposal_owner=application,proposal=deepcopy(witness),proposal_seq=events[0]['seq'])


def pending_proposal(service,db,witness):
    from .work_coordinator_handoff_post_round_v3_capture import Capture
    from . import work_coordinator_handoff_post_round_v3_continuation_proposals as p
    b=witness['snapshot']
    if not pending_request(service,db,witness['command_id'],b['request'],p.COMMAND): return False
    state=_pending.get(); event=db.execute('SELECT * FROM events WHERE seq=?',(state.get('proposal_seq'),)).fetchone()
    return bool(type(state.get('proposal_owner')) is Capture and state['proposal_owner'].s is service and
        state.get('proposal')==witness and service.collaboration.get(db,p.KIND,witness['id'])==witness and
        service.collaboration.get(db,'work_proposal',b['proposal']['id'])==b['proposal'] and event is not None and
        event['kind']==p.EVENT and json.loads(event['payload'])==witness and
        event['task_id'] is None and event['attempt_id'] is None)


@contextmanager
def command(service, db, kind, request):
    """Only an exact V3 child command can acquire a temporary private witness.

    Enter SAVEPOINT before setting ContextVar. Both entry failure and failures
    caught by an outer transaction leave no pending witness. Preserve the task
    snapshot transaction's exact UUID/directory-identity rollback semantics.
    This helper neither creates capabilities nor validates committed history.
    """
    if kind not in {COMMAND,'work-item.proposal-check'}:
        yield; return
    from .work_coordinator_handoff_post_round_v3_continuation_proposals import REQUEST_FIELDS as PROPOSAL_FIELDS
    fields(request, REQUEST_FIELDS if kind==COMMAND else PROPOSAL_FIELDS)
    plan = service.teamwork.current(db, request['id'])
    if not plan or plan.get(LINEAGE_FIELD, {}).get('protocol') != LINEAGE:
        yield; return
    if (not db.in_transaction or service.command_kind != kind or
            service.command_payload_hash != digest(request) or _pending.get() is not None):
        raise ValueError('V3命令作用域须属于准确完整请求的唯一写事务。')
    uuid.UUID(service.command_identity)
    state = {'service': service, 'db': db, 'identity': service.command_identity,
        'kind': kind, 'request': deepcopy(request), 'request_hash': digest(request),
        'instance': service.instance}
    state['owner'] = _CommandOwner(state)
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
        state['owner'].active = False
        _pending.reset(token)
