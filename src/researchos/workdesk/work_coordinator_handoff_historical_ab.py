"""Original A/B records and complete per-capture command reconstruction.

Inputs are private independently rebuilt values owned by the enclosing catalog.
The reader never treats a run's stored input or a record's own evidence as its
proof. Actual complete row/event sets remain the CanonicalLedger's obligation.
"""
from copy import deepcopy

from .store import digest
from .work_transitions import plan_ref
from .work_coordinator_handoff_records import (
    DELIVERY_KIND, COMPLETION_KIND, DELIVERY, COMPLETION, validate_row, versioned)


class HistoricalAB:
    def __init__(self, sources, timeline, ledger, proven_inputs):
        if timeline.sources is not sources or ledger.sources is not sources:
            raise ValueError('历史A/B须属于同一次独立来源和目录。')
        self.sources, self.timeline, self.ledger = sources, timeline, ledger
        self.db, self.root = sources.db, timeline.root
        self._inputs = proven_inputs
        self._records = {DELIVERY_KIND: [], COMPLETION_KIND: []}
        self._proven = set()
        for kind, event_kind in ((DELIVERY_KIND, 'coordinator_handoff_delivered'),
                (COMPLETION_KIND, 'coordinator_handoff_completed')):
            for row in sources.all(self.db, kind):
                body = row.get('snapshot', {})
                if (row.get('item_id') != timeline.item_id and body.get('item_id') != timeline.item_id and
                        body.get('root', {}).get('id') != self.root['id']):
                    continue
                validate_row(kind, row)
                if (body['root'] != plan_ref(self.root) or body['marker'] != self.root['coordinator_file_handoff_root'] or
                        row['item_id'] != timeline.item_id):
                    raise ValueError('历史A/B记录不属于准确原根及业务任务。')
                run = sources.get(self.db, 'work_unit_run', body['run_id'])
                event = sources._event(self.db, event_kind, row, None, run['task_id'], run['attempt_id'])
                if row['created'] > event['created']:
                    raise ValueError('历史A/B记录创建晚于其实际事件。')
                self._records[kind].append((row, event))
            if len(self._records[kind]) > 1:
                raise ValueError('原根只允许唯一正式B交付及唯一成功完成。')

    def _input(self, run):
        value = self._inputs.get(run['id'])
        if value is None or value != run['input']:
            raise ValueError('历史A/B只能消费本目录已独立重建的原完整输入。')
        return deepcopy(value)

    def _delivery(self, row, event):
        run = self.sources.get(self.db, 'work_unit_run', row['snapshot']['run_id'])
        frozen = self._input(run)
        intent = frozen.get('handoff_delivery_intent')
        proof = self.sources.exited(run)
        if intent is None or run['unit_key'] != self.root['file_handoffs'][0]['target_unit']:
            raise ValueError('原正式交付须属于准确B及独立冻结意图。')
        expected = versioned({'protocol': DELIVERY, 'item_id': run['item_id'], 'root': plan_ref(self.root),
            'marker': self.root['coordinator_file_handoff_root'], 'intent': intent,
            'run_id': run['id'], 'run_version': digest(run), 'input_version': digest(frozen),
            'task_spec_version': digest(proof['task']['spec']), 'materials': proof['task']['spec']['materials']})
        dispatch = self.sources._event(self.db, 'work_dispatch_bound', proof['dispatch'],
            None, run['task_id'], run['attempt_id'])
        if (row['snapshot'] != expected or row['command_id'] != run['command_id'] or
                not proof['reserved']['seq'] < event['seq'] < dispatch['seq'] or
                not run['created'] <= row['created'] <= event['created']):
            raise ValueError('历史正式交付不等于原run/input/spec/materials及真实事务顺序。')
        return run

    def _completion(self, row, event):
        body = row['snapshot']
        run = self.sources.get(self.db, 'work_unit_run', body['run_id'])
        frozen = self._input(run)
        deliveries = self._records[DELIVERY_KIND]
        if (len(deliveries) != 1 or deliveries[0][0]['id'] not in self._proven or
                deliveries[0][0]['snapshot']['run_id'] != run['id'] or
                deliveries[0][1]['seq'] >= event['seq']):
            raise ValueError('历史完成须消费此前独立重建的唯一准确交付。')
        frame = self.sources.frame_at(run, event['seq'], frozen['dependencies'])
        full = frame['source_evidence']
        files = full['file_artifacts']
        if (full['state'] != 'COMPLETED' or full['blockers'] or not full['result_available'] or
                files['state'] != 'CURRENT' or files['check_status'] == 'FAIL' or
                frozen['unit']['checks'] and files['check_status'] != 'PASS'):
            raise ValueError('历史完成缺少原准确已退出结果及成功冻结产物。')
        expected = versioned({'protocol': COMPLETION, 'item_id': run['item_id'], 'root': plan_ref(self.root),
            'marker': self.root['coordinator_file_handoff_root'],
            'delivery': {k: deliveries[0][0][k] for k in ('id', 'version')},
            'run_id': run['id'], 'run_version': digest(run), 'input_version': digest(frozen),
            'evidence': full, 'files_version': digest(frame['files'])})
        captures = self.sources.captures(run, self.sources.exited(run), frame['files'])
        prior = [capture for capture in captures if capture['seq'] < event['seq']]
        if not prior or prior[-1]['artifact'] != files['reference'] or row['snapshot'] != expected:
            raise ValueError('历史完成完整值不等于准确原捕获及冻结字节重建。')
        capture = prior[-1]
        command = self.capture(run, capture)
        if (row['command_id'] != command['id'] or not capture['created'] <= row['created'] <= command['created'] or
                event['created'] > command['created']):
            raise ValueError('历史完成须属于最后成功捕获的原已提交完整命令。')
        return run

    def prove_before(self, boundary):
        """Advance only records earlier than the next real historical boundary."""
        ordered = sorted(((event['seq'], kind, row, event) for kind, entries in self._records.items()
            for row, event in entries if event['seq'] < boundary), key=lambda entry: entry[0])
        for seq, kind, row, event in ordered:
            if row['id'] in self._proven:
                continue
            run = self._delivery(row, event) if kind == DELIVERY_KIND else self._completion(row, event)
            owner = 'run:' + run['id']
            self.ledger.row(kind, row, owner)
            self.ledger.event_at(seq, event['kind'], row, owner, task=run['task_id'], attempt=run['attempt_id'])
            self._proven.add(row['id'])
        return {kind: [(deepcopy(row), event['seq']) for row, event in entries if event['seq'] < boundary]
            for kind, entries in self._records.items()}

    def capture(self, run, capture):
        """Rebuild original seven-field request at this precise occurrence."""
        frozen = self._input(run)
        proof = self.sources.exited(run)
        raw = self.sources.raw_evidence(run, proof)
        files = self.sources.files(run, proof, raw, frozen['dependencies'])
        all_captures = self.sources.captures(run, proof, files)
        matches = [value for value in all_captures if value['seq'] == capture['seq']]
        if matches != [capture]:
            raise ValueError('历史捕获只能消费本run准确事件发生项。')
        prior = [value for value in all_captures if value['seq'] < capture['seq']]
        driver = self.timeline.driver_before(proof['plan'], capture['seq'])
        item = self.timeline.business_before(capture['seq'])
        if driver['state'] != 'RESERVED' or item['revision'] != driver['item_revision']:
            raise ValueError('历史捕获须为原当前业务修订和明确持有责任。')
        request = {'id': run['item_id'], 'revision': item['revision'],
            'current_driver': {k: driver[k] for k in ('id', 'generation', 'revision')},
            'plan': plan_ref(proof['plan']), 'run_id': run['id'], 'result_version': raw['version'],
            'artifact': prior[-1]['artifact'] if prior else None}
        return self.ledger.capture_command(run, capture, request, 'run:' + run['id'])

    def captures(self, run):
        frozen = self._input(run)
        proof = self.sources.exited(run)
        files = self.sources.files(run, proof, self.sources.raw_evidence(run, proof), frozen['dependencies'])
        captures = self.sources.captures(run, proof, files)
        for capture in captures:
            self.capture(run, capture)
        owner = 'run:' + run['id']
        for saved in files['snapshots']:
            self.ledger.row('work_unit_artifact',
                self.sources.get(self.db, 'work_unit_artifact', saved['reference']['id']), owner)
        if captures:
            self.ledger.row('work_unit_artifact_current', {'id': 'unit-artifact-current:' + run['id'],
                'artifact_id': captures[-1]['artifact']['id']}, owner)
        return captures
