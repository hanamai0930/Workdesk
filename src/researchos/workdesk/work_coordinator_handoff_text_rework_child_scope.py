"""Exact typed child SEND/CHECK command identities and final SQL/live guards."""
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy

from .store import digest
from .work_coordinator_handoff_text_rework_parent import require_service_db
from .work_coordinator_handoff_text_rework_saved_records import ref
from .work_coordinator_handoff_text_rework_child_history import TextChildHistory
from . import work_coordinator_handoff_text_rework_child_values as v
from . import work_coordinator_handoff_text_rework_contracts as c

_pending_send = ContextVar('exact_text_rework_child_send', default=None)
_pending_check = ContextVar('exact_text_rework_child_check', default=None)


class _ChildCommand:
    def __init__(self, service, db, request):
        require_service_db(service, db); c.fields(request, self.fields)
        if (service.command_kind != self.kind or service.command_payload_hash != digest(request) or
                _pending_send.get() is not None or _pending_check.get() is not None):
            raise ValueError('后继命令须属于唯一真实typed原请求及准确字段。')
        self.service, self.db, self.request = service, db, deepcopy(request)
        self.identity, self.instance = c._uuid(service.command_identity), service.instance
        self.tail = db.execute('SELECT COALESCE(MAX(seq),0) FROM events').fetchone()[0]
        self.active, self.candidate, self.witness = True, None, None
        self.response = self.response_value = None
        self.fingerprint = digest((self.identity, self.instance, self.request, self.tail))

    def require(self, sources=None):
        require_service_db(self.service, self.db)
        if (not self.active or self.context.get() is not self or self.service.command_identity != self.identity or
                self.service.instance != self.instance or self.service.command_kind != self.kind or
                self.service.command_payload_hash != digest(self.request) or
                self.fingerprint != digest((self.identity, self.instance, self.request, self.tail)) or
                self.db.execute('SELECT 1 FROM commands WHERE id=?', (self.identity,)).fetchone() or
                sources is not None and sources.db.connection is not self.db):
            raise ValueError('后继pending只接受本Service实际事务及唯一未提交准确命令。')
        return self

    def before(self):
        from .work_coordinator_handoff_text_rework_current import TextCurrent, require_saved_live
        self.require()
        result = TextCurrent(self.service).read(self.db, self.service.teamwork.current(self.db, self.request['id']))
        require_saved_live(self.service, self.db, result)
        candidate = ref(result['candidate'])
        if self.candidate is not None and self.candidate != candidate:
            raise ValueError('后继命令不能更换准确保存边。')
        self.candidate = candidate
        return result

    def replay(self, **stages):
        self.require()
        if self.candidate is None:
            raise ValueError('后继命令缺少原current已核准确保存边。')
        return TextChildHistory(self.db, self.service.locations.data, self.candidate,
            **{self.owner_argument: self}).read(**stages)

    def require_receipt(self, history, row, event, response):
        self.require(history.sources)
        if (ref(history.edge.candidate) != self.candidate or row != self.witness or row['command_id'] != self.identity or
                row['snapshot']['request'] != self.request or row['snapshot']['receipt'] != response or
                row['snapshot']['operation'] != self.operation or event['seq'] <= self.tail):
            raise ValueError('后继pending只消费本命令真实准确完整回执。')

    def bind_response(self, response):
        self.require()
        if self.response is not None or self.witness is None or self.witness['snapshot']['receipt'] != response:
            raise ValueError('后继最终响应只能从本命令实际完整receipt绑定一次。')
        checked = self.replay()
        self.require_result(checked, response)
        self.response, self.response_value = response, deepcopy(response)
        self.finished_changes = self.db.total_changes
        self.finished_tail = self.db.execute('SELECT COALESCE(MAX(seq),0) FROM events').fetchone()[0]

    def require_response(self):
        from .work_coordinator_handoff_text_rework_current import require_saved_live
        self.require()
        if (self.response is None or self.response != self.response_value or self.db.total_changes != self.finished_changes or
                self.db.execute('SELECT COALESCE(MAX(seq),0) FROM events').fetchone()[0] != self.finished_tail):
            raise ValueError('后继提交前最终响应或真实SQL尾变化。')
        checked = self.replay(); self.require_result(checked, self.response_value)
        require_saved_live(self.service, self.db, checked)


class _ChildSend(_ChildCommand):
    kind, operation, fields = v.SEND, 'SEND', v.SEND_FIELDS
    context, owner_argument = _pending_send, 'send_owner'

    def require_result(self, result, response):
        if (result.get('last_send_response') != response or not result['send_receipts'] or
                result['send_receipts'][-1] != self.witness):
            raise ValueError('后继SEND完整独立重建不等于实际最后运行响应。')


class _ChildCheck(_ChildCommand):
    kind, operation, fields = v.CHECK, 'PROPOSAL_CHECK', v.REQUEST_FIELDS
    context, owner_argument = _pending_check, 'check_owner'

    def require_result(self, result, response):
        if (response != {'proposal': result['proposal'], 'no_dispatch': True} or not result['proposal_receipts'] or
                result['proposal_receipts'][-1] != self.witness):
            raise ValueError('后继CHECK完整独立重建不等于实际最后提案响应。')


@contextmanager
def child_command(service, db, kind, request):
    if kind not in {v.SEND, v.CHECK}:
        raise ValueError('后继scope只属于准确unit-send或proposal-check。')
    owner = (_ChildSend if kind == v.SEND else _ChildCheck)(service, db, request)
    savepoint = 'coordinator_text_rework_child_send' if kind == v.SEND else 'coordinator_text_rework_child_check'
    db.execute('SAVEPOINT ' + savepoint); token = owner.context.set(owner)
    try:
        from .workspace import task_snapshot_transaction
        with task_snapshot_transaction(service.locations.data / 'tasks'):
            yield owner
            owner.require_response()
            db.execute('RELEASE ' + savepoint)
    except BaseException:
        db.execute('ROLLBACK TO ' + savepoint); db.execute('RELEASE ' + savepoint)
        raise
    finally:
        owner.active = False; owner.context.reset(token)
