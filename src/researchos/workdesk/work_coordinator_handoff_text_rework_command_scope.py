"""Exact live REVIEW_SEND identity; no synthetic committed command or receipt.

This private scope binds a real pre-write ParentCurrent to the actual first
readiness/opened events. The complete command writer must independently replay
its suffix and install its final receipt guard before this scope may commit.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy

from .store import digest
from .work_coordinator_handoff_historical_assets import _uuid
from . import work_coordinator_handoff_text_rework_contracts as c


_pending = ContextVar('exact_coordinator_text_rework_review_send', default=None)
SAVEPOINT = 'coordinator_text_rework_review_send'


class _ReviewCommand:
    def __init__(self, service, db, request):
        from .work_coordinator_handoff_text_rework_parent import require_service_db
        require_service_db(service, db)
        if (service.command_kind != c.SEND or service.command_payload_hash != digest(request)):
            raise ValueError('返工pending须属于真实Service的准确原请求写事务。')
        self.service, self.db = service, db
        self.identity = _uuid(service.command_identity)
        self.instance, self.request = service.instance, c.review_request(request)
        self.start_tail = db.execute('SELECT COALESCE(MAX(seq),0) FROM events').fetchone()[0]
        self.start_changes, self.active = db.total_changes, True
        self.parent = self.capability = None
        self._response = self._response_value = None
        self._identity_digest = digest((self.identity, self.instance, self.request, self.start_tail, self.start_changes))

    def require(self, sources=None):
        from .work_coordinator_handoff_text_rework_parent import require_service_db
        require_service_db(self.service, self.db)
        raw = self.db
        if (not self.active or _pending.get() is not self or not raw.in_transaction or
                self.service.command_identity != self.identity or self.service.command_kind != c.SEND or
                self.service.command_payload_hash != digest(self.request) or self.service.instance != self.instance or
                self._identity_digest != digest((self.identity, self.instance, self.request, self.start_tail, self.start_changes)) or
                raw.execute('SELECT 1 FROM commands WHERE id=?', (self.identity,)).fetchone() is not None or
                sources is not None and sources.db.connection is not raw):
            raise ValueError('返工pending不属于本次唯一真实未提交命令。')
        return self

    def bind_parent(self, parent):
        from .work_coordinator_handoff_text_rework_parent import ParentCurrent
        self.require()
        if (self.parent is not None or type(parent) is not ParentCurrent or parent.s is not self.service or
                parent.db is not self.db or parent.tail != self.start_tail or parent.changes != self.start_changes):
            raise ValueError('返工pending只能绑定本命令首写前的真实完整父current。')
        parent.require()
        if (self.request['plan'] != {'id': parent.plan['id'], 'version': parent.plan['version']} or
                self.request['id'] != parent.item['id'] or self.request['revision'] != parent.item['revision']):
            raise ValueError('返工pending父方案或业务修订不等于原请求。')
        self.parent = parent
        self._parent_digest = c.memory_digest((parent.plan, parent.history, parent.plans, parent.root, parent.parent_type))

    def bind_capability(self, sources, capability):
        self.require(sources)
        c.validate_capability(capability)
        if self.parent is None or self.capability is not None:
            raise ValueError('返工能力只能绑定一次已证明的原父current。')
        self._parent_unchanged()
        b = capability['snapshot']; obs = b['observation']
        if (capability['command_id'] != self.identity or b['request'] != self.request or
                b['parent_tail'] != self.start_tail or b['root'] != self.parent.root or
                b['parent_type'] != self.parent.parent_type or b['observation'] != self.parent.observation(
                    self.request['unit'], obs['readiness_observation'], obs['observed_at']) or
                sources.get(sources.db, c.KINDS[c.CAPABILITY], capability['id']) != capability):
            raise ValueError('返工能力须等于本命令真实父观察与准确已写载体。')
        self.capability = deepcopy(capability)
        self._capability_digest = digest(capability)

    def _parent_unchanged(self):
        p = self.parent
        if p is None or c.memory_digest((p.plan, p.history, p.plans, p.root, p.parent_type)) != self._parent_digest:
            raise ValueError('返工pending绑定的原父完整事实已变化。')

    def require_edge(self, sources, capability, ready_event, opened):
        self.require(sources); self._parent_unchanged()
        if (self.capability != capability or digest(capability) != self._capability_digest or
                capability['command_id'] != self.identity or capability['snapshot']['request'] != self.request or
                ready_event['seq'] != self.start_tail + 1 or opened['seq'] != self.start_tail + 2):
            raise ValueError('返工pending边界须为原命令首个真实readiness及紧随能力事件。')
        return self

    def bind_response(self, response):
        """Bind only the independently rebuilt final REVIEW_SEND response."""
        self.require()
        if self._response is not None or self.capability is None:
            raise ValueError('审阅完整响应只能由本命令末尾writer绑定一次。')
        from .work_coordinator_handoff_text_rework_review_replay import ReviewReplay
        ref = {k: self.capability[k] for k in ('id', 'version')}
        result = ReviewReplay(self.db, self.service.locations.data, ref, command_owner=self).read()
        if result['response'] != response:
            raise ValueError('审阅末尾响应不等于独立完整后缀。')
        self._response, self._response_value = response, deepcopy(response)
        self._response_changes = self.db.total_changes
        self._response_tail = self.db.execute('SELECT COALESCE(MAX(seq),0) FROM events').fetchone()[0]

    def require_response(self):
        self.require()
        if (self._response is None or self._response != self._response_value or
                self.db.total_changes != self._response_changes or
                self.db.execute('SELECT COALESCE(MAX(seq),0) FROM events').fetchone()[0] != self._response_tail):
            raise ValueError('审阅命令缺少准确最终响应或完整尾在提交前变化。')
        from .work_coordinator_handoff_text_rework_review_replay import ReviewReplay
        from .work_coordinator_handoff_text_rework_review_live import require_live
        ref = {k: self.capability[k] for k in ('id', 'version')}
        result = ReviewReplay(self.db, self.service.locations.data, ref, command_owner=self).read()
        if result['response'] != self._response_value:
            raise ValueError('审阅提交前完整后缀不等于最终响应。')
        require_live(self.service, self.db, self.capability, result['driver'],
            excluding={result['run']['attempt_id']})


def pending_review(owner, sources, capability, ready_event, opened):
    if type(owner) is not _ReviewCommand:
        raise ValueError('返工历史缺少准确已提交命令或当前唯一命令owner。')
    return owner.require_edge(sources, capability, ready_event, opened)


@contextmanager
def review_command(service, db, request):
    """Only REVIEW_SEND; no allowlist for arbitrary pending commands."""
    if _pending.get() is not None:
        raise ValueError('返工命令不能嵌套。')
    owner = _ReviewCommand(service, db, request)
    db.execute('SAVEPOINT ' + SAVEPOINT)
    token = _pending.set(owner)
    try:
        from .workspace import task_snapshot_transaction
        with task_snapshot_transaction(service.locations.data / 'tasks'):
            yield owner
            owner.require_response()
            db.execute('RELEASE ' + SAVEPOINT)
    except BaseException:
        db.execute('ROLLBACK TO ' + SAVEPOINT)
        db.execute('RELEASE ' + SAVEPOINT)
        raise
    finally:
        owner.active = False
        _pending.reset(token)
