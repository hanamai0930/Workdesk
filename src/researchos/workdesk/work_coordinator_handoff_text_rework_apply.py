"""Dedicated same-definition atomic save; no generic extra/callback permission."""
from contextvars import ContextVar
from copy import deepcopy
import time

from .store import digest, uid
from .work_coordinator_handoff_records import versioned
from .work_coordinator_handoff_historical_assets import _uuid
from .work_coordinator_handoff_text_rework_parent import require_service_db
from .work_coordinator_handoff_text_rework_review_replay import ReviewReplay
from .work_coordinator_handoff_text_rework_review_live import require_live
from .work_coordinator_handoff_text_rework_saved_records import ref, validate, request_shape, SAVE_EVENT, DRIVER_EVENT
from . import work_coordinator_handoff_text_rework_saved_values as v
from . import work_coordinator_handoff_text_rework_contracts as c

_pending = ContextVar('exact_coordinator_text_rework_save', default=None)
SAVEPOINT = 'coordinator_text_rework_atomic_save'


class _TextSave:
    def __init__(self, service, db, request):
        require_service_db(service, db); request_shape(request)
        if service.command_kind != c.APPLY or service.command_payload_hash != digest(request) or _pending.get() is not None:
            raise ValueError('返工保存须属于本Service准确原七字段的唯一真实事务。')
        self.service, self.db, self.request = service, db, deepcopy(request)
        self.identity, self.instance = _uuid(service.command_identity), service.instance
        self.start_tail = db.execute('SELECT COALESCE(MAX(seq),0) FROM events').fetchone()[0]
        self.start_changes, self.active = db.total_changes, True
        self.candidate = self.receipt = self.response = None
        self._identity_digest = digest((self.identity, self.instance, self.request, self.start_tail, self.start_changes))

    def require(self):
        require_service_db(self.service, self.db)
        if (not self.active or _pending.get() is not self or self.service.command_identity != self.identity or
                self.service.command_kind != c.APPLY or self.service.command_payload_hash != digest(self.request) or
                self.service.instance != self.instance or self._identity_digest != digest((self.identity, self.instance,
                    self.request, self.start_tail, self.start_changes)) or
                self.db.execute('SELECT 1 FROM commands WHERE id=?', (self.identity,)).fetchone()):
            raise ValueError('返工保存pending不属于本命令准确唯一owner。')
        return self

    def require_edge(self, sources, candidate, event):
        self.require()
        if (sources.db.connection is not self.db or self.candidate != candidate or
                candidate['command_id'] != self.identity or candidate['snapshot']['request'] != self.request or
                event['seq'] != self.start_tail + 1):
            raise ValueError('返工保存边须为本命令真实首个candidate事件。')

    def require_receipt(self, history, receipt, response):
        from .work_coordinator_handoff_text_rework_saved_history import TextSavedHistory
        self.require()
        if (type(history) is not TextSavedHistory or history.sources.db.connection is not self.db or
                history.edge.candidate != self.candidate or receipt != self.receipt or response != self.response or
                receipt['snapshot']['request'] != self.request or receipt['command_id'] != self.identity):
            raise ValueError('返工保存须消费本命令实际已写的最终完整回执。')

    def _put(self, protocol, body):
        row = {'id': c.KINDS[protocol] + ':' + body['version'], 'version': body['version'], 'snapshot': body,
            'item_id': self.request['id'], 'created': time.time(), 'command_id': self.identity}
        (c.validate_receipt(row) if protocol == c.RECEIPT else validate(row, protocol))
        if self.db.execute('SELECT 1 FROM collaboration WHERE id=?', (row['id'],)).fetchone():
            raise ValueError('返工保存不能覆盖任何既有准确载体。')
        self.service.collaboration.put(self.db, c.KINDS[protocol], row)
        self.service.store.event(None, None, c.EVENTS[protocol], row, self.db)
        return row

    def _event(self, kind, payload):
        from .work_coordinator_handoff_historical_assets import _json
        rows = [r for r in self.db.execute('SELECT * FROM events WHERE kind=?', (kind,))
            if _json(r['payload']) == payload and r['task_id'] is None and r['attempt_id'] is None]
        if len(rows) != 1:
            raise ValueError('返工保存缺少唯一准确无task/attempt归属事件。')
        return {'seq': rows[0]['seq'], 'kind': kind, 'payload_digest': digest(payload)}

    def run(self):
        self.require(); s, db = self.service, self.db
        item = s.work_items.checked(db, self.request)
        proposal = s.work_proposals.current(db, item['id'])
        context = (proposal or {}).get('snapshot', {}).get('handoff_context', {})
        if context.get('protocol') != c.REVIEW_INPUT:
            raise ValueError('返工保存必须来自本次真实新审阅输入的准确提案。')
        replay = ReviewReplay(db, s.locations.data, context['capability']); original = replay.read()
        h = original['history']; cap = replay.capability
        if (h is None or original['proposal'] != proposal or self.request != v.request_for(h, cap['snapshot']['observation']['parent_plan_snapshot'], proposal) or
                s.work_transitions.consumption(db, proposal)):
            raise ValueError('返工保存原七字段、最新提案或未消费状态已变化。')
        require_live(s, db, cap, h['driver'])
        check = original['sources']._event(original['sources'].db, 'coordinator_proposal_checked', proposal,
            None, original['run']['task_id'], original['run']['attempt_id'])
        seed = {'id': 'teamwork:' + uid(), 'created': time.time()}
        body = v.candidate_body(cap, h, proposal, {k: check[k] for k in ('seq', 'kind', 'payload_digest')}, self.request,
            seed, {**v.driver_ref(h['driver']), 'revision': uid()},
            {**v.auth_ref(h['authorization']), 'revision': uid()}, time.time(), self.identity)
        child = body['child_plan']
        if db.total_changes != self.start_changes or db.execute('SELECT COALESCE(MAX(seq),0) FROM events').fetchone()[0] != self.start_tail:
            raise ValueError('返工候选首写前实际SQL已变化。')
        for key in (child['id'], 'driver-version:' + body['driver_after']['revision'], 'unit-auth-version:' + body['authorization_after']['revision']):
            if db.execute('SELECT 1 FROM collaboration WHERE id=?', (key,)).fetchone():
                raise ValueError('返工保存新身份已经存在。')
        self.candidate = self._put(c.CANDIDATE, body)
        parent = cap['snapshot']['observation']['parent_plan_snapshot']
        saved_child, driver, auth = s.work_transitions.save_plan(db, item, h['driver'], parent, h['authorization'],
            child['units'], child['result_lineage'], child['transition'], reason=c.PAUSE_REASON, handoff_candidate=ref(self.candidate))
        if saved_child != child:
            raise ValueError('返工专用save_plan返回child不等于准确候选。')
        consumed = v.consumption(self.candidate, driver, auth, time.time())
        if db.execute('SELECT 1 FROM collaboration WHERE id=?', (consumed['id'],)).fetchone():
            raise ValueError('返工提案已经消费。')
        s.collaboration.put(db, 'work_proposal_consumption', consumed)
        application = self._put(c.APPLICATION, v.application_body(self.candidate, driver, auth, consumed))
        events = [self._event(c.EVENTS[c.CANDIDATE], self.candidate),
            self._event(DRIVER_EVENT, {**driver, 'no_dispatch': True}),
            self._event(SAVE_EVENT, v.save_payload(self.candidate, driver, auth)),
            self._event(c.EVENTS[c.APPLICATION], application)]
        registration = self._put(c.REGISTRATION, v.registration_body(self.candidate, application, events))
        self.response = v.response_for(self.candidate, application, registration, driver, auth)
        self.receipt = self._put(c.RECEIPT, versioned({'protocol': c.RECEIPT, 'operation': 'SAVE',
            'request': self.request, 'request_hash': digest(self.request), 'capability': ref(cap),
            'registration': ref(registration), 'run': None, 'driver': v.driver_ref(driver), 'receipt': self.response, **c.PERMISSIONS}))
        self.verify()
        return self.response

    def verify(self):
        self.require()
        from .work_coordinator_handoff_text_rework_saved_history import TextSavedHistory
        from .work_coordinator_handoff_text_rework_current import require_saved_live
        result = TextSavedHistory(self.db, self.service.locations.data, ref(self.candidate), save_owner=self).read()
        if result['response'] != self.response:
            raise ValueError('返工保存最终完整回执不等于真实全集重建。')
        require_saved_live(self.service, self.db, result)
        return result


@c.closed
def apply(service, db, request):
    owner = _TextSave(service, db, request)
    db.execute('SAVEPOINT ' + SAVEPOINT); token = _pending.set(owner)
    try:
        response = owner.run()
        owner.verify()
        db.execute('RELEASE ' + SAVEPOINT)
        return response
    except BaseException:
        db.execute('ROLLBACK TO ' + SAVEPOINT); db.execute('RELEASE ' + SAVEPOINT)
        raise
    finally:
        owner.active = False; _pending.reset(token)


@c.closed
def save_plan(service, db, candidate_ref, item, driver, plan, auth, units, lineage, transition, extra, reason):
    owner = _pending.get()
    if type(owner) is not _TextSave or owner.service is not service or owner.db is not db:
        raise ValueError('返工candidate不能通过通用save_plan绕过准确原保存命令。')
    owner.require(); candidate = validate(service.collaboration.get(db, c.KINDS[c.CANDIDATE], candidate_ref['id']), c.CANDIDATE)
    b = candidate['snapshot']; child = b['child_plan']; h = b['parent_history']
    if (ref(candidate) != candidate_ref or candidate != owner.candidate or item != service.work_items.item(db, b['request']['id']) or
            driver != h['driver'] or auth != h['authorization'] or plan != service.teamwork.current(db, item['id']) or
            b['parent_plan'] != {'id': plan['id'], 'version': plan['version']} or units != child['units'] or
            lineage != child['result_lineage'] or transition != child['transition'] or extra is not None or reason != c.PAUSE_REASON or
            service.work_drivers.current(db, item['id']) != driver or service.work_units.authorization(db, driver) != auth):
        raise ValueError('返工专用save_plan参数、当前来源或完整候选不符。')
    after, transferred = v.transferred(candidate, transferred_at=time.time(), driver_updated=time.time())
    service.collaboration.put(db, 'teamwork_plan', child)
    service.collaboration.put(db, 'teamwork_current', {'id': 'teamwork-current:' + item['id'], 'plan_id': child['id']})
    service.collaboration.put(db, 'work_unit_authorization', transferred)
    service.collaboration.put(db, 'work_unit_authorization_revision', {**transferred, 'id': 'unit-auth-version:' + transferred['revision']})
    service.collaboration.put(db, 'work_driver', after)
    service.collaboration.put(db, 'work_driver_revision', {**after, 'id': 'driver-version:' + after['revision'], 'driver_id': after['id']})
    service.store.event(None, None, DRIVER_EVENT, {**after, 'no_dispatch': True}, db)
    service.store.event(None, None, SAVE_EVENT, v.save_payload(candidate, after, transferred), db)
    return child, after, transferred
