"""Chronological saved-child owner; shares real prefix sources and full ledger."""
from copy import deepcopy

from .store import digest, Store
from .work_coordinator_handoff_records import fields
from .work_coordinator_handoff_historical_assets import _json
from .work_coordinator_handoff_historical_timeline import HistoricalTimeline
from .work_coordinator_handoff_text_rework_saved_history import TextSavedHistory
from .work_coordinator_handoff_text_rework_saved_records import ref
from .work_coordinator_handoff_text_rework_review_replay import RUN_FIELDS
from .work_coordinator_handoff_text_rework_review_values import response_for
from .work_coordinator_handoff_text_rework_child_execution import ChildExecution
from . import work_coordinator_handoff_text_rework_child_values as v
from . import work_coordinator_handoff_text_rework_contracts as c


class TextChildHistory(TextSavedHistory):
    def __init__(self, db, asset_root, candidate, *, save_owner=None, responsibility_owner=None,
            send_owner=None, check_owner=None, _sources=None, _ledger=None):
        super().__init__(db, asset_root, candidate, save_owner=save_owner, responsibility_owner=responsibility_owner, _sources=_sources, _ledger=_ledger)
        self.send_owner, self.check_owner = send_owner, check_owner

    def read(self, *, before_binding=False, before_receipt=False):
        result = self._reconstruct_children(before_binding=before_binding, before_receipt=before_receipt)
        self.close(result)
        return result

    def _reconstruct_children(self, *, before_binding=False, before_receipt=False):
        result = self._reconstruct()
        self.result = result
        h = result['history'] = deepcopy(result['history'])
        h.update(driver=deepcopy(result['driver']), authorization=deepcopy(result['authorization']),
            task={'id': result['child_plan']['item_id'], 'revision': result['driver']['item_revision']})
        result.update(responsibility_receipts=[], send_receipts=[], proposal_receipts=[])
        plan = result['child_plan']; timeline = HistoricalTimeline(self.sources, self.ledger, ref(plan))
        boundary = self.sources._event(self.db, c.EVENTS[c.RECEIPT], result['receipt'])
        operations = []; used_receipts = set()
        receipts = []
        for row in self.sources.all(self.db, c.KINDS[c.RECEIPT]):
            if row.get('item_id') != plan['item_id']:
                continue
            row = c.validate_receipt(row)
            if row['snapshot']['registration'] == ref(result['registration']) and row['snapshot']['operation'] in {'SEND', 'PROPOSAL_CHECK', 'DRIVER_CHANGE'}:
                event = self.sources._event(self.db, c.EVENTS[c.RECEIPT], row)
                receipts.append((event, row))
                if row['snapshot']['operation'] == 'DRIVER_CHANGE':
                    revision = row['snapshot']['driver']['revision']
                    versions = [(e, d) for e, d in timeline.driver_versions(plan) if d['revision'] == revision]
                    if len(versions) != 1:
                        raise ValueError('后继责任回执缺少准确唯一真实修订。')
                    operations.append((versions[0][0], 'DRIVER_CHANGE', row))
        for run in self.sources.all(self.db, 'work_unit_run'):
            if run.get('plan_id') == plan['id']:
                actual = self.sources.run(run['id'])
                operations.append((actual[-1], 'SEND', actual))
        for row in self.sources.all(self.db, 'work_proposal'):
            if row.get('item_id') == plan['item_id'] and row.get('snapshot', {}).get('plan') == ref(plan):
                source = row['snapshot']['source']
                event = self.sources._event(self.db, 'coordinator_proposal_checked', row, None, source['task_id'], source['attempt_id'])
                operations.append((event, 'PROPOSAL_CHECK', row))
        operations.sort(key=lambda entry: entry[0]['seq'])
        self.operations = operations
        for index, (event, operation, value) in enumerate(operations):
            if any(f['source_evidence']['state'] == 'RESERVED' for f in h['frames']):
                raise ValueError('后继存在尚未退出的真实保留，不开放后续步骤。')
            if event['seq'] <= boundary['seq'] or event['created'] < boundary['created']:
                raise ValueError('返工后继每步须在原保存及前次完整命令/已知退出之后。')
            if operation == 'DRIVER_CHANGE':
                from .work_coordinator_handoff_text_rework_responsibility import replay_change
                boundary = replay_change(self, result, value, boundary, self.responsibility_owner)
                used_receipts.add(value['id'])
                h['driver'] = deepcopy(result['driver'])
            else:
                found = [(e, r) for e, r in receipts if r['command_id'] ==
                    (value[0]['command_id'] if operation == 'SEND' else value['command_id'])]
                partial = operation == 'SEND' and self.send_owner is not None and value[0]['command_id'] == self.send_owner.identity
                if partial and (before_binding or before_receipt):
                    if found or index != len(operations) - 1:
                        raise ValueError('后继中间保留只接受本次末尾且尚无完整receipt。')
                    self.send_owner.require(self.sources)
                    receipt = None
                else:
                    if len(found) != 1 or found[0][1]['snapshot']['operation'] != operation:
                        raise ValueError('每次后继运行/提案检查须有唯一准确operation完整回执。')
                    receipt = found[0][1]; used_receipts.add(receipt['id'])
                if operation == 'SEND':
                    boundary = self._send(result, timeline, value, event, boundary, receipt,
                        before_binding=partial and before_binding, before_receipt=partial and before_receipt)
                else:
                    boundary = self._check(result, value, event, receipt)
        if used_receipts != {r['id'] for _, r in receipts}:
            raise ValueError('返工后继存在未由真实语义步骤消费的完整回执。')
        h['current_frames'] = list(v.available(result).values())
        return result

    def _receipt(self, result, row, operation, request, run, response, previous_event, pending):
        body = v.receipt_body(result, operation, request, run, response)
        event = self.sources._event(self.db, c.EVENTS[c.RECEIPT], row)
        if (row['snapshot'] != body or row['command_id'] !=
                (run['command_id'] if operation == 'SEND' else response['proposal']['command_id']) or
                not previous_event['seq'] < event['seq'] or
                not previous_event['created'] <= row['created'] <= event['created']):
            raise ValueError('后继完整receipt不等于原请求、准确run、登记及完整实际返回对象。')
        owner = 'text-child-send:' + run['id'] if operation == 'SEND' else 'text-child-check:' + response['proposal']['id']
        self.ledger.row(c.KINDS[c.RECEIPT], row, owner)
        self.ledger.event_at(event['seq'], event['kind'], row, owner)
        command = self.db.execute('SELECT * FROM commands WHERE id=?', (row['command_id'],)).fetchone()
        if command is None:
            from .work_coordinator_handoff_text_rework_child_scope import _ChildSend, _ChildCheck
            expected_type = _ChildSend if operation == 'SEND' else _ChildCheck
            if type(pending) is not expected_type:
                raise ValueError('后继缺少原完整命令或当前准确typed命令owner。')
            pending.require_receipt(self, row, event, response)
        else:
            if pending is not None and pending.identity == row['command_id']:
                raise ValueError('已提交后继命令不能复用pending owner。')
            self.ledger.command(row['command_id'], v.SEND if operation == 'SEND' else v.CHECK,
                request, response, owner, not_before=event['created'])
        return event

    def _send(self, result, timeline, actual, event, barrier, receipt, *, before_binding, before_receipt):
        run, task, attempt, plan, unit, reserved = actual
        fields(run, RUN_FIELDS | ({'proposal'} if unit['role'] != 'coordinate' else set()))
        owner = 'text-child-send:' + run['id']; h = result['history']
        proposal = result['proposal'] if unit['role'] != 'coordinate' else None
        request = v.send_request(result, unit, proposal)
        fields(request, v.SEND_FIELDS)
        if receipt is not None and receipt['snapshot']['request'] != request:
            raise ValueError('后继SEND原请求未绑定当时完整前沿及最新准确提案。')
        if receipt is None and (self.send_owner is None or self.send_owner.request != request):
            raise ValueError('后继pending保留须属于本命令原准确请求。')
        frozen = v.input_value(result, unit, proposal)
        if task['created'] < barrier['created'] or task['created'] >= result['authorization']['dispatch_before']:
            raise ValueError('后继保留须在前次退出之后且原最初截止之前。')
        self.sources.verify_input(run, frozen, v.prompt(frozen), result['driver'], result['authorization'], h['usage']['attempts'] + 1)
        self.ledger.row('work_unit_run', run, owner)
        execution = ChildExecution(self.sources, self.ledger, timeline, barrier, self.send_owner if receipt is None else None)
        if attempt['state'] == 'RESERVED':
            proof = execution._reserved(run, task, attempt, plan, unit, reserved, owner)
        else:
            if before_binding or before_receipt:
                raise ValueError('未提交后继运行不能已有实际退出。')
            proof = self.sources.exited(run); self.ledger.execution(proof, owner)
        if before_binding:
            if self.db.execute('SELECT 1 FROM collaboration WHERE id=?', ('dispatch:' + attempt['id'],)).fetchone():
                raise ValueError('后继准入前不应已有业务dispatch。')
            result['pending_run'] = run; result['pending_input'] = frozen
            return reserved
        dispatch = self.sources.get(self.db, 'work_dispatch', 'dispatch:' + attempt['id'])
        execution._dispatch(run, task, attempt, dispatch, proof)
        self.ledger.row('work_dispatch', dispatch, owner)
        self.ledger.event('work_dispatch_bound', dispatch, owner, task=task['id'], attempt=attempt['id'])
        proof['dispatch'] = dispatch
        linked = execution._link(run, proof, result['driver'], owner)
        result['driver'] = linked['driver']; h['driver'] = deepcopy(result['driver'])
        h['task'] = {'id': plan['item_id'], 'revision': result['driver']['item_revision']}
        response = response_for(run, task['spec_hash'], dispatch)
        result['last_send_response'] = response
        if before_receipt:
            last = linked['event']
        else:
            last = self._receipt(result, receipt, 'SEND', request, run, response, linked['event'], self.send_owner)
            result['send_receipts'].append(receipt)
        frame = execution.frame(run, proof)
        h['frames'].append(frame)
        if proof['exited'] is not None:
            if not last['seq'] < proof['exited']['seq'] or last['created'] > proof['exited']['created']:
                raise ValueError('后继退出须在真实完整SEND命令回执之后。')
            last = proof['exited']
        self._advance(result)
        return last

    def _advance(self, result):
        h = result['history']; values = result['review']['parent']['values']
        h['frontier'] = [values.old.frontier_entry(f) for f in h['frames']]
        usages = [Store.usage(None, attempt=f['run']['attempt_id'], db=self.db) for f in h['frames']]
        numeric = ('model_requests', 'reserved_cny', 'tool_calls', 'elapsed_observed_seconds',
            'api_known_input_tokens', 'api_known_output_tokens', 'api_requests_without_token_receipt', 'unknown_requests')
        totals = {k: round(sum(u[k] for u in usages), 3) for k in numeric}
        for value in totals.values():
            c.finite(value)
            if value < 0:
                raise ValueError('后继实际累计不能为负。')
        h['usage'] = {**h['usage'], **totals, 'attempts': len(h['frames'])}

    def _check(self, result, proposal, event, receipt):
        c.fields(proposal, {'id', 'version', 'snapshot', 'item_id', 'created', 'command_id'})
        c._uuid(proposal['id'].removeprefix('work-proposal:')); c._uuid(proposal['command_id']); c.finite(proposal['created'])
        run_id = proposal['snapshot']['source']['run_id']
        frames = [f for f in result['history']['frames'] if f['run']['id'] == run_id]
        if len(frames) != 1 or receipt is None:
            raise ValueError('后继提案须有真实已重建的本方案协调退出及完整回执。')
        frame = frames[0]; run = frame['run']; request = receipt['snapshot']['request']
        expected = v.check_snapshot(result, run, frame, request, proposal['created'])
        if (proposal['snapshot'] != expected or proposal['version'] != digest(expected) or
                not proposal['id'].startswith('work-proposal:') or proposal['item_id'] != run['item_id'] or
                not proposal['created'] <= event['created']):
            raise ValueError('后继提案不等于独立严格回复、完整原输入及真实检查边界。')
        owner = 'text-child-check:' + proposal['id']
        self.ledger.row('work_proposal', proposal, owner)
        self.ledger.event_at(event['seq'], event['kind'], proposal, owner, task=run['task_id'], attempt=run['attempt_id'])
        response = {'proposal': proposal, 'no_dispatch': True}
        last = self._receipt(result, receipt, 'PROPOSAL_CHECK', request, run, response, event, self.check_owner)
        result['proposal'] = proposal; result['proposal_receipts'].append(receipt)
        return last
