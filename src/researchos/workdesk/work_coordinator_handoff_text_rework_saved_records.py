"""Closed save carriers and a real typed candidate boundary, never caller seq."""
from copy import deepcopy

from .store import digest
from .work_coordinator_handoff_records import fields, reference
from .work_coordinator_handoff_historical_assets import HistoricalSources, _uuid, _json
from . import work_coordinator_handoff_text_rework_contracts as c

SAVE_EVENT = 'coordinator_text_rework_saved_v1'
DRIVER_EVENT = 'work_driver_plan_adjusted'
CANDIDATE_FIELDS = {'protocol', 'version', 'root', 'parent_plan', 'capability', 'proposal',
    'proposal_check_event', 'preview', 'request', 'request_hash', 'parent_history', 'child_seed',
    'child_plan', 'driver_before', 'driver_after', 'authorization_before', 'authorization_after',
    'lineage', 'prepared_at', *c.PERMISSIONS}
APPLICATION_FIELDS = {'protocol', 'version', 'candidate', 'request', 'request_hash', 'parent_plan',
    'child_plan', 'proposal', 'driver_before', 'driver_after', 'authorization_before',
    'authorization_after', 'consumption', *c.PERMISSIONS}
REGISTRATION_FIELDS = {'protocol', 'version', 'root', 'parent_plan', 'child_plan', 'capability',
    'candidate', 'application', 'proposal', 'next_coordinate', 'source_frontier', 'events', *c.PERMISSIONS}


def ref(value):
    return {k: value[k] for k in ('id', 'version')}


@c.closed
def request_shape(request):
    fields(request, c.APPLY_FIELDS); c.text(request['id']); c.task_revision(request['revision'])
    c.sha(request['frontier']); reference(request['plan']); reference(request['proposal'])
    from .work_coordinator_handoff_adjustment_records import driver_ref, auth_ref
    driver_ref(request['current_driver']); auth_ref(request['authorization'])
    return deepcopy(request)


@c.closed
def validate(row, protocol):
    fields(row, {'id', 'version', 'snapshot', 'item_id', 'created', 'command_id'})
    c.finite(row['created']); _uuid(row['command_id'])
    body = row['snapshot']; c.hashed(body); c.permissions(body)
    schema = {c.CANDIDATE: CANDIDATE_FIELDS, c.APPLICATION: APPLICATION_FIELDS, c.REGISTRATION: REGISTRATION_FIELDS}
    fields(body, schema[protocol])
    if row['id'] != c.KINDS[protocol] + ':' + body['version'] or row['version'] != body['version'] or body['protocol'] != protocol:
        raise ValueError('文字返工保存载体须保持准确协议及内容身份。')
    for name in ('root', 'parent_plan', 'child_plan', 'capability', 'proposal', 'candidate', 'application'):
        if name in body and not (protocol == c.CANDIDATE and name == 'child_plan'):
            reference(body[name])
    if protocol in {c.CANDIDATE, c.APPLICATION}:
        request_shape(body['request'])
        from .work_coordinator_handoff_adjustment_records import driver_ref, auth_ref
        for name in ('driver_before', 'driver_after'): driver_ref(body[name])
        for name in ('authorization_before', 'authorization_after'): auth_ref(body[name])
        if (body['request_hash'] != digest(body['request']) or body['request']['id'] != row['item_id'] or
                body['request']['plan'] != body['parent_plan'] or body['request']['proposal'] != body['proposal'] or
                body['request']['current_driver'] != body['driver_before'] or body['request']['authorization'] != body['authorization_before']):
            raise ValueError('文字返工保存须绑定准确原七字段与原责任授权。')
    if protocol == c.CANDIDATE:
        fields(body['child_seed'], {'id', 'created'}); c.finite(body['child_seed']['created']); c.finite(body['prepared_at'])
        c.hashed(body['child_plan']); c.validate_preview(body['preview']); c.event_ref(body['proposal_check_event'])
        if body['lineage'] != body['child_plan'][c.LINEAGE_FIELD] or body['preview']['blockers']:
            raise ValueError('文字返工候选须为同定义可应用完整预览。')
    if protocol == c.REGISTRATION:
        c.coordinate_ref(body['next_coordinate'])
        if not isinstance(body['events'], list) or len(body['events']) != 4:
            raise ValueError('返工登记须精确引用此前四个实际保存事件。')
        for event in body['events']: c.event_ref(event)
    return deepcopy(row)


class _SaveEdge:
    def __init__(self, sources, candidate_ref, *, command_owner=None):
        if type(sources) is not HistoricalSources:
            raise ValueError('保存边须使用真实SQL来源。')
        sources.unchanged(); reference(candidate_ref)
        candidate = validate(sources.get(sources.db, c.KINDS[c.CANDIDATE], candidate_ref['id']), c.CANDIDATE)
        if ref(candidate) != candidate_ref:
            raise ValueError('保存candidate实际版本变化。')
        event = sources._event(sources.db, c.EVENTS[c.CANDIDATE], candidate)
        command = sources.db.execute('SELECT * FROM commands WHERE id=?', (candidate['command_id'],)).fetchone()
        if command is None:
            from .work_coordinator_handoff_text_rework_apply import _TextSave
            if type(command_owner) is not _TextSave:
                raise ValueError('保存边缺少完整原命令或当前准确保存owner。')
            command_owner.require_edge(sources, candidate, event)
        elif (command_owner is not None or command['kind'] != c.APPLY or
                command['payload_hash'] != candidate['snapshot']['request_hash']):
            raise ValueError('保存边原命令类型或请求摘要不符。')
        self.sources, self.candidate, self.event, self.command_owner = sources, candidate, event, command_owner
        self.command = dict(command) if command is not None else None
        self._fingerprint = digest((candidate, event, self.command))

    @property
    def boundary(self):
        return self.event['seq']

    def require(self, sources):
        if sources is not self.sources or self._fingerprint != digest((self.candidate, self.event, self.command)):
            raise ValueError('保存边只能属于本次同源完整历史重建。')
        sources.unchanged()
        if sources.get(sources.db, c.KINDS[c.CANDIDATE], self.candidate['id']) != self.candidate:
            raise ValueError('保存边实际candidate已变化。')
        if self.command_owner is not None:
            self.command_owner.require_edge(sources, self.candidate, self.event)
        return self.boundary
