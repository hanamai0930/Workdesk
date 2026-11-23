"""Actual child reservation/spec/dispatch/link and independent exit frames."""
from copy import deepcopy
from .store import canonical, digest
from .work_coordinator_handoff_historical_assets import _json
from .work_coordinator_handoff_text_rework_review_values import linked_driver, frozen_credential_revision
from . import work_coordinator_handoff_text_rework_contracts as c

class ChildExecution:
    def __init__(self, sources, ledger, timeline, barrier, send_owner=None):
        self.sources, self.ledger, self.timeline = sources, ledger, timeline
        self.db, self.barrier, self.send_owner = sources.db, barrier, send_owner

    def _reserved(self, run, task, attempt, plan, unit, reserved, owner):
        sources, db, ledger = self.sources, self.db, self.ledger
        if (task['state'] != 'RESERVED' or task['grant'] is not None or
                task['spec'].get('runtime_mode') != 'official' or task['spec'].get('work_mode') != 'cooperative' or
                attempt['result'] is not None or attempt['reconciliation'] is not None or
                attempt['state'] != 'RESERVED' or attempt['deadline'] != 0 or attempt['stop_requested'] != 0 or
                attempt['session_id'] != run['initial_session_id'] or attempt['turn_id'] is not None or
                attempt['baseline'] != {'runtime_mode': 'official'} or
                db.execute('SELECT COUNT(*) FROM attempts WHERE task_id=?', (task['id'],)).fetchone()[0] != 1):
            raise ValueError('审阅保留须为尚未运行的准确首次官方attempt。')
        c._uuid(run['initial_session_id'])
        for table in ('requests', 'native_requests', 'approvals'):
            if db.execute('SELECT 1 FROM ' + table + ' WHERE task_id=? OR attempt_id=?',
                    (task['id'], attempt['id'])).fetchone():
                raise ValueError('审阅RESERVED不能已有请求或审批。')
        if db.execute('SELECT 1 FROM tool_calls WHERE attempt_id=?', (attempt['id'],)).fetchone():
            raise ValueError('审阅RESERVED不能已有工具调用。')
        created = ledger.event('cooperative_unit_created', {'runtime_mode': 'official',
            'configuration': 'VENDOR_DEFAULTS', 'extra_task_limits': False}, owner, task=task['id'])
        events = list(db.execute("SELECT * FROM events WHERE kind='execution_reserved' AND task_id=? AND attempt_id=?",
            (task['id'], attempt['id'])))
        if len(events) != 1:
            raise ValueError('审阅须有唯一原官方保留事件。')
        body = _json(events[0]['payload'])
        c.sha(body.get('implementation_revision'))
        if self.send_owner is not None and body['implementation_revision'] != self.send_owner.service.implementation:
            raise ValueError('审阅保留不属于当前实际程序版本。')
        spec = task['spec']
        expected = {'owner': attempt['owner'], 'runtime_mode': 'official', 'human_note': '',
            'implementation_revision': body['implementation_revision'], 'resume_mode': None,
            'configuration_snapshot': {**{k: spec[k] for k in ('provider', 'model', 'effort', 'tier')},
                'access': spec.get('access', 'inherit'), 'configuration': 'VENDOR_DEFAULTS'},
            'credential_selection_revision': frozen_credential_revision(spec['execution_connection']),
            'execution_connection': spec['execution_connection'], 'extra_task_limits': False}
        if body != expected:
            raise ValueError('审阅原官方保留参数不符。')
        execution = ledger.event('execution_reserved', expected, owner, task=task['id'], attempt=attempt['id'])
        resource = ledger.event('execution_resource_frozen', spec['execution_resource'], owner,
            task=task['id'], attempt=attempt['id'])
        ledger.event_at(reserved['seq'], reserved['kind'], run, owner, task=task['id'], attempt=attempt['id'])
        if not self.barrier['seq'] < created['seq'] < execution['seq'] < resource['seq'] < reserved['seq']:
            raise ValueError('审阅原任务创建、保留、资源及run事件顺序不符。')
        return {'task': task, 'attempt': attempt, 'plan': plan, 'unit': unit, 'reserved': reserved,
            'exited': None, 'credential_revision': expected['credential_selection_revision']}


    def _dispatch(self, run, task, attempt, dispatch, proof):
        from .execution_constraints import snapshot
        if proof['exited'] is not None:
            expected = proof['dispatch']
        else:
            expected = {'id': 'dispatch:' + attempt['id'], 'item_id': run['item_id'],
                'item_revision': run['item_revision'] + 1, 'task_id': task['id'], 'attempt_id': attempt['id'],
                'session_id': run['initial_session_id'], 'command_id': run['command_id'], 'command_kind': 'work-item.unit-send',
                'spec_hash': task['spec_hash'], 'provider': task['spec']['provider'],
                'credential_revision': proof['credential_revision'],
                'execution_connection': task['spec']['execution_connection'],
                'execution_resource': task['spec']['execution_resource'], 'cooperative': run['cooperative'],
                'constraints': snapshot(task, attempt), 'responsibility': 'OFFICIAL_RUNTIME',
                'handoff_id': None, 'created': dispatch['created']}
        c.finite(dispatch['created'])
        if dispatch != expected:
            raise ValueError('返工dispatch的完整身份、命令类型及实际约束不符。')


    def _link(self, run, proof, previous, owner):
        timeline, sources, ledger, db = self.timeline, self.sources, self.ledger, self.db
        plan, dispatch, attempt, task = (proof[k] for k in ('plan', 'dispatch', 'attempt', 'task'))
        matches = [(event, value) for event, value in timeline.driver_versions(plan)
            if event['kind'] == 'work_driver_execution_linked' and value.get('last_unit_run') == run['id']]
        if len(matches) != 1:
            raise ValueError('审阅须有唯一真实driver执行绑定。')
        event, value = matches[0]
        expected = linked_driver(previous, run, dispatch['item_revision'], revision=value['revision'], updated=value['updated'])
        dispatch_event = sources._event(db, 'work_dispatch_bound', dispatch, None, task['id'], attempt['id'])
        if (timeline.driver_before(plan, proof['reserved']['seq']) != previous or
                timeline.driver_before(plan, event['seq']) != previous or value != expected or
                not proof['reserved']['seq'] < dispatch_event['seq'] < event['seq'] or
                proof['exited'] is not None and event['seq'] >= proof['exited']['seq'] or
                not previous['updated'] <= dispatch['created'] <= value['updated']):
            raise ValueError('审阅driver绑定须按真实原前驱及事件顺序推进。')
        before, after = timeline.business(run['item_revision']), timeline.business(dispatch['item_revision'])
        source = {k: task[k] for k in ('id', 'project_id', 'title', 'created', 'spec_hash')}
        source.update(state='RESERVED', updated=attempt['started'], cursor=proof['reserved']['seq'])
        source.update({k: task['spec'].get(k) for k in ('interaction', 'work_scene', 'work_mode', 'provider', 'model')})
        record = {'kind': 'task', 'record_id': task['id'],
            'source_revision': canonical(digest({'kind': 'task', 'source': source, 'display': {}}))}
        expected_after = {**before, 'revision': before['revision'] + 1, 'updated': after['updated'],
            'record_refs': sorted([*before['record_refs'], record], key=lambda r: (r['kind'], r['record_id']))}
        raw_record = db.execute("SELECT * FROM work_item_records WHERE item_id=? AND kind='task' AND record_id=?",
            (run['item_id'], task['id'])).fetchone()
        body = {'work_item_id': run['item_id'], 'revision': after['revision'], 'no_dispatch': True}
        linked = sources._event(db, 'work_item_linked', body)
        if (after != expected_after or raw_record is None or raw_record['source_revision'] != record['source_revision'] or
                not proof['reserved']['seq'] < linked['seq'] < dispatch_event['seq'] or
                not run['created'] <= raw_record['created'] <= after['updated'] <= dispatch['created']):
            raise ValueError('审阅业务绑定的前后完整修订和原记录摘要不符。')
        ledger.business_version(run['item_id'], before['revision'], before, 'business:' + str(before['revision']))
        ledger.business_version(run['item_id'], after['revision'], after, 'business:' + str(after['revision']))
        ledger.business_record(dict(raw_record), owner); ledger.event('work_item_linked', body, owner)
        ledger.row('work_driver_revision', {**value, 'id': 'driver-version:' + value['revision'], 'driver_id': value['id']}, owner)
        ledger.event_at(event['seq'], event['kind'], {**value, 'no_dispatch': True}, owner)
        return {'driver': value, 'event': event, 'before': before, 'after': after}


    def frame(self, run, proof):
        sources = self.sources
        if proof['exited'] is None:
            attempt = proof['attempt']
            raw = {'run_id': run['id'], 'task_id': proof['task']['id'], 'attempt_id': attempt['id'],
                'owner': attempt['owner'], 'session_id': attempt['session_id'], 'turn_id': attempt['turn_id'],
                'state': 'RESERVED', 'exact_identity': True, 'connection_fingerprint': run['connection_fingerprint'],
                'result_digest': digest({}), 'answer': '', 'result_available': False, 'check_status': 'NOT_RECORDED',
                'independent_review': 'NOT_PERFORMED', 'human_acceptance': 'NOT_RECORDED', 'blockers': []}
            raw['version'] = digest(raw)
        else:
            raw = sources.raw_evidence(run, proof)
        files = sources.files(run, proof, raw, run['input']['dependencies'])
        captures = sources.captures(run, proof, files)
        if captures or files['snapshots']:
            raise ValueError('返工审阅回合不允许登记文件产物或追加A/B捕获。')
        unit = proof['unit']
        status = {'state': 'MISSING' if unit['outputs'] else 'NOT_REQUIRED', 'reference': None,
            'problems': [], 'manifest': {}, 'check_status': 'NOT_RECORDED', 'checks': [],
            'required_outputs': unit['outputs'], 'required_checks': unit['checks'],
            'independent_review': 'NOT_PERFORMED', 'human_acceptance': 'NOT_RECORDED',
            'provenance': 'OBSERVED_AFTER_EXACT_TURN_NOT_PROOF_OF_AUTHORSHIP',
            'delivery': 'ORIGINAL_PROJECT_FILES_REVALIDATED_AT_DISPATCH'}
        full = {**raw, 'result_version': raw['version'], 'file_artifacts': status, 'check_status': status['check_status']}
        full['version'] = digest(full)
        frame = {'run': deepcopy(run), 'task_spec': proof['task']['spec'], 'workspace': proof['attempt']['workspace'],
            'evidence': raw, 'source_evidence': full, 'files': files}
        return frame
