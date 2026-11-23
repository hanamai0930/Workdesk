"""Read DeepSeek workflow and job facts from the exact recorded SDK session."""

import json


_WORKFLOW_EVENTS = (
    'tool-workflow/run-start', 'tool-workflow/agent-start',
    'tool-workflow/agent-end', 'tool-workflow/run-end',
)
_JOB_EVENTS = ('job/registered', 'job/settled')
_EVENT_LIMIT = 2_000
_RECORD_LIMIT = 100
_MEMBER_LIMIT = 32
_NO_TERMINAL = 'TERMINAL_NOT_OBSERVED'


def _has_identity(value):
    return isinstance(value, str) and 0 < len(value) <= 256


def _display_text(value):
    return value[:256] if isinstance(value, str) else None


def _record(identity, kind):
    return {'id': identity, 'kind': kind, 'status': _NO_TERMINAL,
            'recorded_start': False, 'live_control_available': False}


def _observe_workflow(run, event_type, data):
    if event_type == 'tool-workflow/run-start':
        run.update(name=_display_text(data.get('name')), recorded_start=True)
    elif event_type == 'tool-workflow/run-end':
        reason = data.get('stopReason')
        if reason in ('completed', 'cancelled', 'error'):
            run['status'] = reason
    else:
        seq = data.get('seq')
        if type(seq) is not int or seq < 1:
            return
        if event_type == 'tool-workflow/agent-start':
            child = data.get('childId')
            if _has_identity(child):
                member = run['members'].setdefault(seq, {'seq': seq, 'status': _NO_TERMINAL})
                member.update(session_id=child, label=_display_text(data.get('label')),
                              phase=_display_text(data.get('phase')))
        else:
            outcome = data.get('outcome')
            if outcome in ('completed', 'failed', 'cancelled'):
                member = run['members'].setdefault(seq, {'seq': seq})
                member['status'] = outcome


def _observe_job(job, event_type, data):
    job.update(job_kind=_display_text(data.get('kind')), name=_display_text(data.get('label')))
    if type(data.get('startedAt')) is int:
        job['started_at'] = data['startedAt']
    if event_type == 'job/registered':
        job['recorded_start'] = True
        return
    if data.get('status') in ('completed', 'killed', 'failed') and type(data.get('finishedAt')) is int:
        job.update(status=data['status'], finished_at=data['finishedAt'])
        if isinstance(data.get('producerReported'), bool):
            job['producer_reported'] = data['producerReported']


def history(_store, db, task, attempt):
    """Project one session's saved evidence, including earlier attempts on that session."""
    session_id = attempt.get('session_id')
    if (task['spec']['provider'] != 'deepseek' or attempt['task_id'] != task['id']
            or not _has_identity(session_id)):
        raise ValueError('查询须绑定 DeepSeek 任务的准确原生会话。')

    event_types = _WORKFLOW_EVENTS + _JOB_EVENTS
    placeholders = ','.join('?' for _ in event_types)
    rows = db.execute(f"""
        SELECT e.seq, e.attempt_id, e.payload
        FROM events e JOIN attempts a ON a.id=e.attempt_id AND a.task_id=e.task_id
        WHERE e.task_id=? AND a.session_id=? AND e.kind='model_event'
          AND json_extract(e.payload, '$.method')='session.event'
          AND json_extract(e.payload, '$.payload.sessionId')=?
          AND json_extract(e.payload, '$.payload.event.type') IN ({placeholders})
        ORDER BY e.seq DESC LIMIT ?
    """, (task['id'], session_id, session_id, *event_types, _EVENT_LIMIT + 1)).fetchall()

    records = {}
    truncated = len(rows) > _EVENT_LIMIT
    for row in reversed(rows[:_EVENT_LIMIT]):
        event = json.loads(row['payload'])['payload']['event']
        event_type = event['type']
        data = event.get('data')
        if not isinstance(data, dict):
            continue
        is_workflow = event_type in _WORKFLOW_EVENTS
        identity = data.get('runId' if is_workflow else 'jobId')
        if not _has_identity(identity) or (not is_workflow and data.get('owner') != session_id):
            continue
        kind = 'workflow' if is_workflow else 'background_job'
        record = records.setdefault((kind, identity), _record(identity, kind))
        record.update(last_event_seq=row['seq'], last_attempt_id=row['attempt_id'])
        if is_workflow:
            record.setdefault('members', {})
            _observe_workflow(record, event_type, data)
        else:
            _observe_job(record, event_type, data)

    data = sorted(records.values(), key=lambda row: row['last_event_seq'], reverse=True)
    truncated = truncated or len(data) > _RECORD_LIMIT
    data = data[:_RECORD_LIMIT]
    for record in data:
        if record['kind'] == 'workflow':
            members = record['members']
            record['members_truncated'] = len(members) > _MEMBER_LIMIT
            record['members'] = [members[seq] for seq in sorted(members)[:_MEMBER_LIMIT]]
    return {'data': data, 'session_id': session_id, 'source': 'RECORDED_NATIVE_SESSION_EVENTS',
            'historical': True, 'truncated': truncated}
