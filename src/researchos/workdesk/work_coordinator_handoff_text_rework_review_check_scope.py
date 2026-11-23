"""Exact pre-save proposal-check scope, distinct from review send and child IO."""
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
import time

from .store import digest, uid
from .work_coordinator_handoff_historical_assets import _uuid
from .work_coordinator_handoff_records import fields
from .work_coordinator_handoff_text_rework_parent import require_service_db
from .work_coordinator_handoff_text_rework_review_replay import ReviewReplay
from .work_coordinator_handoff_text_rework_review_live import require_live
from . import work_coordinator_handoff_text_rework_review_proposals as p
from . import work_coordinator_handoff_text_rework_contracts as c

_pending = ContextVar('exact_coordinator_text_rework_review_check', default=None)
SAVEPOINT = 'coordinator_text_rework_review_check'


class _ReviewCheck:
    def __init__(self, service, db, request):
        require_service_db(service, db); fields(request, p.REQUEST_FIELDS)
        if service.command_kind != p.COMMAND or service.command_payload_hash != digest(request):
            raise ValueError('返工检查须属于本Service准确九字段真实命令。')
        self.service, self.db, self.request = service, db, deepcopy(request)
        self.identity, self.instance = _uuid(service.command_identity), service.instance
        self.start_tail = db.execute('SELECT COALESCE(MAX(seq),0) FROM events').fetchone()[0]
        self.start_changes, self.active = db.total_changes, True
        self.capability = self.witness = self._response = None
        self._identity_digest = digest((self.identity, self.instance, self.request, self.start_tail, self.start_changes))

    def require(self):
        require_service_db(self.service, self.db)
        if (not self.active or _pending.get() is not self or self.service.instance != self.instance or
                self.service.command_identity != self.identity or self.service.command_kind != p.COMMAND or
                self.service.command_payload_hash != digest(self.request) or
                self._identity_digest != digest((self.identity, self.instance, self.request, self.start_tail, self.start_changes)) or
                self.db.execute('SELECT 1 FROM commands WHERE id=?', (self.identity,)).fetchone()):
            raise ValueError('返工保存前检查不属于当前唯一未提交真实命令。')
        return self

    def before(self):
        self.require()
        if self.capability is not None or self.db.total_changes != self.start_changes:
            raise ValueError('返工检查须在本命令首写之前重建准确父审阅。')
        run = self.service.work_units.reservation(self.db, self.request['run_id'].removeprefix('unit-run:'))
        context = (run or {}).get('input', {}).get('handoff_coordination_context', {})
        if context.get('protocol') != c.REVIEW_INPUT or run['id'] != self.request['run_id']:
            raise ValueError('返工检查只接受实际冻结的新REWORK_REVIEW输入。')
        replay = ReviewReplay(self.db, self.service.locations.data, context['capability'])
        result = replay.read()
        if not result['exited'] or result['run'] != run:
            raise ValueError('返工检查须先核实准确审阅退出。')
        self.capability = deepcopy(replay.capability)
        require_live(self.service, self.db, self.capability, result['driver'])
        self._before_digest = digest((result['history'], result['proposal'], run))
        return result

    def bind(self, witness):
        self.require(); p.validate(witness)
        if self.witness is not None or self.capability is None or witness['command_id'] != self.identity:
            raise ValueError('返工检查完整见证须由本命令绑定一次。')
        if witness['snapshot']['request'] != self.request:
            raise ValueError('返工检查见证原请求变化。')
        self.witness = deepcopy(witness)

    def require_witness(self, replay, witness, seq):
        self.require()
        if (type(replay) is not ReviewReplay or replay.sources.db.connection is not self.db or
                replay.capability != self.capability or witness != self.witness or
                witness['snapshot']['request'] != self.request or
                witness['snapshot']['capability'] != {k: self.capability[k] for k in ('id', 'version')} or
                witness['snapshot']['checked_event']['seq'] != self.start_tail + 1 or seq != self.start_tail + 2):
            raise ValueError('返工pending检查只能消费本命令准确已写完整见证。')

    def finish(self, response):
        self.require()
        if self.witness is None or self._response is not None:
            raise ValueError('返工检查缺少本命令唯一完整见证。')
        ref = {k: self.capability[k] for k in ('id', 'version')}
        result = ReviewReplay(self.db, self.service.locations.data, ref).read(proposal_owner=self)
        if (self.witness['snapshot']['response'] != response or
                result['proposal'] != self.witness['snapshot']['proposal'] or
                digest((result['history'], self._previous, result['run'])) != self._before_digest):
            raise ValueError('返工检查完整响应或原准确历史在保存中变化。')
        require_live(self.service, self.db, self.capability, result['driver'])
        self._response, self._response_value = response, deepcopy(response)
        self._finished_changes = self.db.total_changes
        return response

    def require_response(self):
        self.require()
        if (self._response is None or self._response != self._response_value or
                self.db.total_changes != self._finished_changes):
            raise ValueError('返工检查缺少最终完整响应或提交前发生写入。')
        result = ReviewReplay(self.db, self.service.locations.data,
            {k: self.capability[k] for k in ('id', 'version')}).read(proposal_owner=self)
        if result['proposal'] != self.witness['snapshot']['proposal']:
            raise ValueError('返工检查提交前实际指针不等于新完整提案。')
        require_live(self.service, self.db, self.capability, result['driver'])


@contextmanager
def review_check_command(service, db, request):
    if _pending.get() is not None:
        raise ValueError('返工检查命令不能嵌套。')
    owner = _ReviewCheck(service, db, request)
    db.execute('SAVEPOINT ' + SAVEPOINT); token = _pending.set(owner)
    try:
        yield owner
        owner.require_response()
        db.execute('RELEASE ' + SAVEPOINT)
    except BaseException:
        db.execute('ROLLBACK TO ' + SAVEPOINT); db.execute('RELEASE ' + SAVEPOINT)
        raise
    finally:
        owner.active = False; _pending.reset(token)


@c.closed
def capture_review(service, db, request):
    owner = _pending.get()
    if type(owner) is not _ReviewCheck or owner.service is not service or owner.db is not db or request != owner.request:
        raise ValueError('返工capture须属于当前准确保存前检查scope。')
    result = owner.before(); history = result['history']; proof = result['proof']
    previous = result['proposal']; owner._previous = deepcopy(previous)
    exit_ref = {k: proof['exited'][k] for k in ('seq', 'kind', 'payload_digest')}
    now = time.time()
    body = p.snapshot(owner.capability, history, result['run'], history['frames'][-1], exit_ref, request, previous, now)
    proposal = {'id': 'work-proposal:' + uid(), 'item_id': request['id'], 'snapshot': body,
        'version': digest(body), 'created': now, 'command_id': owner.identity}
    service.collaboration.put(db, 'work_proposal', proposal)
    service.collaboration.put(db, 'work_proposal_current', {'id': 'proposal-current:' + request['id'], 'proposal_id': proposal['id']})
    source = body['source']
    service.store.event(source['task_id'], source['attempt_id'], 'coordinator_proposal_checked', proposal, db)
    # Store.event's return is not a boundary contract; locate the actual row.
    raw = db.execute('SELECT * FROM events WHERE kind=? AND task_id=? AND attempt_id=? ORDER BY seq DESC LIMIT 1',
        ('coordinator_proposal_checked', source['task_id'], source['attempt_id'])).fetchone()
    from .work_coordinator_handoff_historical_assets import _json
    if raw is None or _json(raw['payload']) != proposal:
        raise ValueError('返工检查未产生准确原归属proposal事件。')
    checked = {'seq': raw['seq'], 'kind': raw['kind'], 'payload_digest': digest(proposal)}
    witness_body = p.witness_body(owner.capability, request, previous, proposal, checked)
    witness = {'id': c.KINDS[c.PROPOSAL] + ':' + witness_body['version'], 'version': witness_body['version'],
        'snapshot': witness_body, 'item_id': request['id'], 'created': time.time(), 'command_id': owner.identity}
    p.validate(witness)
    if db.execute('SELECT 1 FROM collaboration WHERE id=?', (witness['id'],)).fetchone():
        raise ValueError('返工检查不能覆盖既有完整见证。')
    service.collaboration.put(db, c.KINDS[c.PROPOSAL], witness)
    service.store.event(None, None, c.EVENTS[c.PROPOSAL], witness, db)
    owner.bind(witness)
    return owner.finish({'proposal': proposal, 'no_dispatch': True})
