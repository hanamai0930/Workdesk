"""Serial handoff transactions and exact frozen-consumer history proofs.

Current evidence and new input permissions remain separate from history.
Delivery and completion share the dispatch and artifact transactions.
"""
import json
import time

from .store import digest
from .work_file_handoff_config import definition
from .work_file_reworks import original_files
from .work_file_sources import resource_status
from .work_transitions import plan_ref
from .work_unit_revisions import require_history


INTENT = 'SERIAL_FILE_HANDOFF_INTENT_V1'
DELIVERY = 'SERIAL_FILE_HANDOFF_DELIVERY_V1'
COMPLETION = 'SERIAL_FILE_HANDOFF_COMPLETION_V1'


class WorkFileHandoffLedger:
    def __init__(self, service):
        self.s = service

    @staticmethod
    def reference(row):
        return {k: row[k] for k in ('id', 'version')}

    def dispatch_intent(self, db, scope, plan, unit, rows):
        if not plan.get('file_handoffs'):
            return None
        if plan.get('file_handoff_lineage'):
            item = self.s.work_items.item(db, plan['item_id'])
            self.s.work_file_handoff_applications.admit(db, item, plan, unit)
            return None
        relations = definition(plan['file_handoffs'], plan['units'], plan['max_parallel'])
        relation = relations[0]
        self.relation(plan, relation['target_unit'])
        if plan['mode'] != 'fixed':
            raise ValueError('同路径事务首批仅支持固定流程，提案模式接线尚未开放。')
        if unit['key'] == relation['source_unit'] and any(r['plan_id'] == plan['id'] and
                r['unit_key'] == relation['target_unit'] for r in rows):
            raise ValueError('准确前版已交付后继，不能把后继字节作为原单元重试来源。')
        return self.prepare(db, scope, unit['key']) if unit['key'] == relation['target_unit'] else None

    def guard_capture(self, db, run, rows):
        plan = self.s.teamwork.current(db, run['item_id'])
        if plan and plan.get('file_handoff_lineage'):
            from .work_file_handoff_chain_runtime import uses_file_rework
            if uses_file_rework(self.s.work_file_handoff_applications, db, plan):
                from .work_file_handoff_file_rework_path import WorkFileHandoffFileReworkPath
                proof = WorkFileHandoffFileReworkPath(self.s).guard_capture(db, plan, run)
            else:
                proof = self.s.work_file_handoff_applications.require_sources(db, plan)
            if run['unit_key'] in {proof['retired_source'], proof['completed_key']}:
                raise ValueError('交接修订的历史前版及完成后继不能重新捕获。')
        for consumer in rows:
            intent = consumer['input'].get('file_handoff_intent')
            if intent and intent['source_run'] == run['id']:
                raise ValueError('准确前版已交付，不能将后继当前文件重新捕获到原运行。')

    def delivery_for(self, db, run):
        rows = [json.loads(r[0]) for r in db.execute(
            "SELECT data FROM collaboration WHERE kind='work_file_handoff_delivery' "
            "AND json_extract(data,'$.snapshot.run_id')=?", (run['id'],))]
        if len(rows) != 1:
            raise ValueError('交接后继缺少唯一准确正式交付记录。')
        return self.get(db, 'work_file_handoff_delivery', self.reference(rows[0]))

    def completion_for(self, db, run):
        artifact = self.s.work_artifacts.current(db, run['id'])
        reference = self.s.work_artifacts.reference(artifact) if artifact else None
        rows = [json.loads(r[0]) for r in db.execute(
            "SELECT data FROM collaboration WHERE kind='work_file_handoff_completion' "
            "AND json_extract(data,'$.snapshot.run_id')=?", (run['id'],))]
        selected = [r for r in rows if r.get('snapshot', {}).get('evidence', {}).get('file_artifacts', {}).get('reference') == reference]
        if not reference or len(selected) != 1:
            raise ValueError('当前准确产物缺少唯一正式交接完成记录。')
        return self.get(db, 'work_file_handoff_completion', self.reference(selected[0]))

    def dependency_history(self, db, consumer, dependency, rows):
        """Validate only the bound frozen dependency, never a new attachment."""
        intent = consumer['input'].get('file_handoff_intent')
        if not intent or dependency['unit_key'] != intent['relation']['source_unit']:
            return None
        expected = {k: v for k, v in dependency.items() if k != 'unit_key'}
        if expected != intent['source_evidence']:
            raise ValueError('交接冻结前置完整正文与正式意图不一致。')
        sources = [r for r in rows if r['id'] == intent['source_run']]
        if len(sources) != 1:
            raise ValueError('交接冻结前置缺少唯一准确原运行。')
        completion = self.completion_for(db, consumer)
        proof = self.match(db, self.reference(completion), consumer, sources[0], expected['version'])
        return {'evidence': expected, 'proof': proof}

    def frontier_history(self, db, source, expected_version, rows):
        consumers = [r for r in rows if r['input'].get('file_handoff_intent', {}).get('source_run') == source['id']]
        if not consumers:
            return None
        if len(consumers) != 1:
            raise ValueError('原前沿存在多个交接消费者，不能选择任意完成记录。')
        consumer = consumers[0]
        completion = self.completion_for(db, consumer)
        return self.match(db, self.reference(completion), consumer, source, expected_version)

    def require_current_input(self, source, rows):
        # A formal transfer retires the old input identity even when bytes are
        # unchanged. Frozen consumer history is validated on a separate path.
        if any(r['input'].get('file_handoff_intent', {}).get('source_run') == source['id'] for r in rows):
            raise ValueError('前版已交付同路径后继，新输入须依赖后继准确产物。')

    @staticmethod
    def versioned(body):
        return {**body, 'version': digest(body)}

    @staticmethod
    def require_version(value, protocol):
        if (not isinstance(value, dict) or value.get('protocol') != protocol or
                value.get('version') != digest({k: v for k, v in value.items() if k != 'version'})):
            raise ValueError('交接记录协议或完整摘要不一致。')

    def put(self, db, kind, body):
        version = digest(body)
        row = {'id': kind + ':' + version, 'version': version, 'snapshot': body,
               'item_id': body['item_id'], 'created': time.time(), 'command_id': self.s.command_identity}
        prior = db.execute('SELECT data FROM collaboration WHERE kind=? AND id=?', (kind, row['id'])).fetchone()
        if prior:
            saved = json.loads(prior[0])
            if (saved.get('snapshot') != body or saved.get('version') != version or
                    saved.get('id') != row['id'] or saved.get('item_id') != body['item_id']):
                raise ValueError('已有交接记录不一致，不能覆盖。')
            return saved
        self.s.collaboration.put(db, kind, row)
        return row

    def get(self, db, kind, reference):
        if not isinstance(reference, dict) or set(reference) != {'id', 'version'}:
            raise ValueError('须引用准确不可变交接记录。')
        row = self.s.collaboration.get(db, kind, reference['id'])
        if (self.reference(row) != reference or row['id'] != kind + ':' + row['version'] or
                digest(row['snapshot']) != row['version'] or row['item_id'] != row['snapshot']['item_id']):
            raise ValueError('交接记录完整摘要或归属已变化。')
        return row

    def relation(self, plan, key):
        if digest({k: v for k, v in plan.items() if k != 'version'}) != plan['version']:
            raise ValueError('交接方案完整摘要不一致。')
        relations = definition(plan.get('file_handoffs', []), plan['units'], plan['max_parallel'])
        if relations != plan.get('file_handoffs', []):
            raise ValueError('交接关系须以准确规范形式保存。')
        if (not relations or relations[0]['target_unit'] != key or
                any(plan.get(k) for k in ('result_lineage', 'previous_plan_reference', 'file_rework_lineage',
                                         'unit_revision_lineage', 'rework_lineage', 'diagnostic_revision'))):
            raise ValueError('本记录基础只支持未修订方案内的准确单一交接后继。')
        return relations[0]

    def history(self, db, rows):
        require_history(self.s, db, rows)
        values = []
        for run in rows:
            files = original_files(self.s, db, run)
            if files['input_problems'] or files['output_state'] == 'DAMAGED':
                raise ValueError('交接的原实际输入或全部旧文件快照损坏。')
            values.append({'run_id': run['id'], 'run_version': digest(run),
                           'task_spec_version': digest(self.s.store.task(run['task_id'], db)['spec']),
                           'workspace': self.s.store.attempt(run['attempt_id'], db)['workspace'],
                           'base_version': self.s.work_units.base_evidence(db, run)['version'],
                           'files_version': digest(files)})
        return values

    def _prepare(self, db, scope, key, rows, *, preserve):
        item, driver, plan = self.s.work_units.checked(db, scope)
        self.s.work_flows.require_stopped(db, item['id'])
        relation = self.relation(plan, key)
        if any(r['plan_id'] != plan['id'] for r in rows):
            raise ValueError('首批交接不能将旧方案运行重绑为当前方案配置。')
        target = next(u for u in plan['units'] if u['key'] == key)
        if any(r['unit_key'] == key for r in rows):
            raise ValueError('首批交接后继须尚未运行。')
        source = next((r for r in reversed(rows) if r['unit_key'] == relation['source_unit']), None)
        if not source:
            raise ValueError('交接准确前版尚未运行。')
        source_unit = self.s.work_artifacts.unit(db, source)
        self.s.work_transitions.validate_run(db, plan, source_unit, source, rows, frozenset())
        evidence = self.s.work_units.evidence(db, source)
        self.s.work_artifacts.require(evidence, 'checked' if source_unit['checks'] or
                                      target['dependency_gate'] == 'checked' else 'files')
        protected = self.history(db, rows)
        if any(not r['valid'] for r in resource_status(self.s, db, [plan])):
            raise ValueError('交接原成员、连接或额度来源已变化。')
        auth = self.s.work_units.authorization(db, driver)
        if (not auth or auth['plan_id'] != plan['id'] or auth['plan_version'] != plan['version'] or
                auth['driver_generation'] != driver['generation'] or len(rows) >= auth['max_attempts_total'] or
                time.time() >= auth['dispatch_before']):
            raise ValueError('交接原累计授权不足或新增派发截止已到期。')
        frozen = self.s.work_units.input_for(db, item, plan, target, rows)
        root = self.s.work_file_rework_applications.root(db, item)
        baseline = self.s.work_file_rework_applications.observe(root, relation['paths'])
        if (baseline['missing'] or baseline['manifest'] != evidence['file_artifacts']['manifest'] or
                self.s.store.attempt(source['attempt_id'], db)['workspace'] != root):
            raise ValueError('交接写前基线与准确前版当前原目录字节不一致。')
        if preserve:
            self.s.work_file_rework_applications.preserve(baseline)
        return self.versioned({'protocol': INTENT, 'item_id': item['id'], 'item_revision': item['revision'],
            'plan': plan_ref(plan), 'driver': driver, 'authorization': auth, 'relation': relation,
            'target_revision': digest(target), 'source_run': source['id'], 'source_evidence': evidence,
            'protected_history': protected, 'baseline': baseline, 'input_template_version': digest(frozen),
            'frontier': digest([{'run_id': r['id'], 'evidence': self.s.work_units.evidence(db, r)['version']}
                                for r in rows]),
            'scope': 'EXACT_CONSUMER_INTENT; NO_COMPLETION_OR_CURRENT_EVIDENCE_EXCEPTION'})

    def prepare(self, db, scope, key):
        # Use a new observation context even when called inside an outer cached
        # view. The final pre-dispatch recheck must also use this fresh path.
        with self.s.work_unit_revision_applications.validation_snapshot(db):
            return self._prepare(db, scope, key, self.s.work_units.rows(db, scope['id']), preserve=True)

    @staticmethod
    def scope(intent):
        driver = intent['driver']
        return {'id': intent['item_id'], 'revision': intent['item_revision'], 'plan': intent['plan'],
                'current_driver': {k: driver[k] for k in ('id', 'generation', 'revision')}}

    def recheck(self, db, intent):
        self.require_version(intent, INTENT)
        with self.s.work_unit_revision_applications.validation_snapshot(db):
            current = self._prepare(db, self.scope(intent), intent['relation']['target_unit'],
                                    self.s.work_units.rows(db, intent['item_id']), preserve=False)
        if current != intent:
            raise ValueError('交接写前最终独立观察已变化；未派发。')
        self.s.work_file_rework_applications.require_baseline(intent['baseline'])

    def target(self, db, intent, run):
        self.require_version(intent, INTENT)
        plan = self.s.collaboration.get(db, 'teamwork_plan', intent['plan']['id'])
        relation = self.relation(plan, run['unit_key'])
        target = next(u for u in plan['units'] if u['key'] == run['unit_key'])
        task = self.s.store.task(run['task_id'], db)
        if (plan_ref(plan) != intent['plan'] or relation != intent['relation'] or
                run['item_id'] != intent['item_id'] or run['item_revision'] != intent['item_revision'] or
                run['plan_id'] != plan['id'] or run['plan_version'] != plan['version'] or
                run['unit_revision'] != intent['target_revision'] or digest(target) != run['unit_revision'] or
                run['input']['unit'] != target or run['input_version'] != digest(run['input']) or
                run['input'].get('file_handoff_intent') != intent or run['retry_of'] is not None or
                run['generation'] != intent['driver']['generation'] or
                run['authorization'] != self.s.work_units.auth_ref(intent['authorization']) or
                task['spec'].get('cooperative') != run['cooperative'] or
                self.s.store.attempt(run['attempt_id'], db)['workspace'] != intent['baseline']['root'] or
                any(run['cooperative'].get(k) != run[k] for k in
                    ('item_id', 'plan_id', 'plan_version', 'unit_key', 'unit_revision', 'input_version',
                     'generation', 'authorization')) or not self.s.work_units.base_evidence(db, run)['exact_identity']):
            raise ValueError('准确交接后继运行、冻结输入或原授权身份不一致。')
        template = {k: v for k, v in run['input'].items() if k not in ('file_handoff_intent', 'write_before_baseline')}
        if digest(template) != intent['input_template_version'] or run['input'].get('write_before_baseline') != intent['baseline']:
            raise ValueError('交接冻结依赖或写前基线不一致。')
        dependencies = [d for d in run['input']['dependencies'] if d['unit_key'] == relation['source_unit']]
        if len(dependencies) != 1 or {k: v for k, v in dependencies[0].items() if k != 'unit_key'} != intent['source_evidence']:
            raise ValueError('交接实际消费的完整前版证据不一致。')
        self.s.work_file_rework_applications.require_baseline(intent['baseline'])
        files = original_files(self.s, db, run)
        if files['input_problems'] or files['output_state'] == 'DAMAGED':
            raise ValueError('交接后继实际输入或全部保存快照损坏。')
        for name, expected in intent['baseline']['manifest'].items():
            if files['actual_input_manifest'].get(name) != expected:
                raise ValueError('实际交付材料不是交接前版准确字节。')
        return plan, files

    def deliver(self, db, intent, run):
        # Must run after official_work.send captures original-project materials,
        # in the same transaction as the new run. Never rewrite frozen input.
        self.target(db, intent, run)
        rows = self.s.work_units.rows(db, intent['item_id'])
        if not rows or rows[-1] != run:
            raise ValueError('交接派发事务缺少准确新运行或存在追加运行。')
        with self.s.work_unit_revision_applications.validation_snapshot(db):
            current = self._prepare(db, self.scope(intent), run['unit_key'], rows[:-1], preserve=False)
        if current != intent:
            raise ValueError('交接派发材料捕获后来源或完整前沿已变化。')
        return self.put(db, 'work_file_handoff_delivery', {'protocol': DELIVERY, 'item_id': run['item_id'],
            'intent': intent, 'run_id': run['id'], 'run_version': digest(run), 'input_version': run['input_version'],
            'task_spec_version': digest(self.s.store.task(run['task_id'], db)['spec'])})

    def verify_delivery(self, db, reference, run):
        row = self.get(db, 'work_file_handoff_delivery', reference)
        value = row['snapshot']
        if (value['protocol'] != DELIVERY or value['run_id'] != run['id'] or
                value['run_version'] != digest(run) or value['input_version'] != run['input_version'] or
                value['task_spec_version'] != digest(self.s.store.task(run['task_id'], db)['spec'])):
            raise ValueError('正式交付记录已脱离准确消费者运行。')
        intent = value['intent']
        plan, files = self.target(db, intent, run)
        rows = self.s.work_units.rows(db, run['item_id'])
        protected = intent['protected_history']
        prefix = rows[:len(protected)]
        if (len(rows) <= len(protected) or rows[len(protected)] != run or
                self.history(db, prefix) != protected):
            raise ValueError('交接保护的原运行、输入或全部旧快照已变化。')
        source = next((r for r in prefix if r['id'] == intent['source_run']), None)
        if not source:
            raise ValueError('正式交接记录的准确前版不存在。')
        # Rebuild the entire old evidence from exact saved bytes and current raw
        # result; only the original-project status may now differ. No resolve()
        # call here: completion validation must not recursively resolve itself.
        raw = self.s.work_units.base_evidence(db, source)
        artifacts = self.s.work_artifacts.status(db, source, raw)
        expected = intent['source_evidence']
        saved = original_files(self.s, db, source)
        selected = next((s for s in saved['snapshots'] if s['reference'] == expected['file_artifacts']['reference']), None)
        if (not selected or self.s.work_artifacts.reference(self.s.work_artifacts.current(db, source['id'])) != selected['reference']):
            raise ValueError('交接前版准确产物指针或保存版本已变化。')
        restored_status = {**artifacts, 'state': 'CURRENT', 'problems': [],
                           'check_status': selected['snapshot']['check_status']}
        rebuilt = {**raw, 'result_version': raw['version'], 'file_artifacts': restored_status,
                   'check_status': restored_status['check_status']}
        rebuilt['version'] = digest(rebuilt)
        if rebuilt != expected:
            raise ValueError('正式交接前版完整旧证据无法准确重建。')
        frontier = [{'run_id': r['id'], 'evidence': expected['version'] if r['id'] == source['id'] else
                     self.s.work_units.evidence(db, r)['version']} for r in prefix]
        if digest(frontier) != intent['frontier']:
            raise ValueError('交接前沿中未绑定的其它当前文件或证据已变化。')
        return row, plan, files

    def complete(self, db, delivery, run):
        row, plan, files = self.verify_delivery(db, delivery, run)
        evidence = self.s.work_units.evidence(db, run)
        unit = self.s.work_artifacts.unit(db, run)
        self.s.work_artifacts.require(evidence, 'checked' if unit['checks'] else 'files')
        if not evidence['result_available']:
            raise ValueError('交接后继尚未准确完成退出。')
        return self.put(db, 'work_file_handoff_completion', {'protocol': COMPLETION, 'item_id': run['item_id'],
            'delivery': self.reference(row), 'run_id': run['id'], 'run_version': digest(run),
            'input_version': run['input_version'], 'evidence': evidence, 'files_version': digest(files)})

    def match(self, db, completion, consumer_run, source_run, expected_version):
        """Historical-only exact consumer proof; never returned as current input."""
        row = self.get(db, 'work_file_handoff_completion', completion)
        value = row['snapshot']
        delivery, plan, files = self.verify_delivery(db, value['delivery'], consumer_run)
        intent = delivery['snapshot']['intent']
        evidence = self.s.work_units.evidence(db, consumer_run)
        # Appending a newer artifact must invalidate the previous completion as
        # the current proof, even if older saved bytes remain intact.
        if (value['protocol'] != COMPLETION or value['run_id'] != consumer_run['id'] or
                value['run_version'] != digest(consumer_run) or value['input_version'] != consumer_run['input_version'] or
                value['evidence'] != evidence or value['files_version'] != digest(files) or
                source_run['id'] != intent['source_run'] or
                digest(source_run) != next(h['run_version'] for h in intent['protected_history']
                                           if h['run_id'] == intent['source_run']) or
                expected_version != intent['source_evidence']['version']):
            raise ValueError('完成记录未准确绑定当前新产物、消费者与完整旧证据。')
        unit = self.s.work_artifacts.unit(db, consumer_run)
        self.s.work_artifacts.require(evidence, 'checked' if unit['checks'] else 'files')
        return {'kind': 'FORMAL_EXACT_CONSUMER_HISTORY', 'version': expected_version,
                'source_run': source_run['id'], 'consumer_run': consumer_run['id'],
                'delivery': self.reference(delivery), 'completion': self.reference(row),
                'current_input_permission': False}
