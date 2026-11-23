"""Independent actual REWORK_REVIEW suffix, including reserved command tails.

The original parent owner registers its immutable prefix in the same ledger.
This owner reconstructs the new input before accepting any new execution row.
Saved children and proposal-check tails have separate later owners.
"""
from copy import deepcopy

from .store import canonical, digest
from .work_coordinator_handoff_records import fields
from .work_coordinator_handoff_historical_assets import HistoricalSources, FrozenAssets, _json
from .work_coordinator_handoff_historical_namespace import CanonicalLedger
from .work_coordinator_handoff_text_rework_history import parent_at_edge
from .work_coordinator_handoff_text_rework_values import review_input, review_prompt
from .work_coordinator_handoff_text_rework_review_values import linked_driver, response_for, receipt_body, frozen_credential_revision
from . import work_coordinator_handoff_text_rework_contracts as c


RUN_FIELDS = {'id', 'item_id', 'source_item_revision', 'driver_id', 'generation', 'driver_revision',
    'plan_id', 'plan_version', 'unit_key', 'unit_revision', 'input_version', 'authorization',
    'cooperative', 'item_revision', 'task_id', 'attempt_id', 'owner', 'initial_session_id',
    'connection_fingerprint', 'input', 'retry_of', 'ordinal_total', 'created', 'command_id',
    'scheduling', 'flow', 'task_spec_version'}


class ReviewReplay:
    def __init__(self, db, asset_root, capability, *, command_owner=None, _sources=None, _ledger=None):
        self.sources = HistoricalSources(db, FrozenAssets(asset_root)) if _sources is None else _sources
        self.ledger = CanonicalLedger(self.sources) if _ledger is None else _ledger
        raw = db.connection if hasattr(db, 'connection') else db
        from pathlib import Path
        if (type(self.sources) is not HistoricalSources or type(self.ledger) is not CanonicalLedger or
                self.ledger.sources is not self.sources or self.sources.db.connection is not raw or
                Path(asset_root).absolute() != self.sources.assets.root):
            raise ValueError('审阅前缀须共享同次真实SQL及冻结资产账本。')
        self.parent = parent_at_edge(db, asset_root, capability, self.ledger, command_owner=command_owner)
        self.edge = self.parent['edge']; self.capability = self.edge.capability
        self.db = self.sources.db; self.command_owner = command_owner

    def read(self, *, before_binding=False, before_receipt=False, proposal_owner=None):
        return self._read(before_binding=before_binding, before_receipt=before_receipt, proposal_owner=proposal_owner)

    def read_before_save(self, edge):
        from .work_coordinator_handoff_text_rework_saved_records import _SaveEdge
        if type(edge) is not _SaveEdge:
            raise ValueError('审阅保存前缀须由真实准确新candidate边界选定。')
        edge.require(self.sources)
        if edge.candidate['snapshot']['capability'] != {k: self.capability[k] for k in ('id', 'version')}:
            raise ValueError('新保存边不属于当前审阅typed能力。')
        return self._read(save_edge=edge)

    def replay_for_catalog(self, catalog):
        from .work_coordinator_handoff_text_rework_assets import TextHistoricalCatalog
        if (type(catalog) is not TextHistoricalCatalog or catalog.sources is not self.sources or catalog.ledger is not self.ledger):
            raise ValueError('审阅资产后缀须由真实同源完整目录消费。')
        catalog.require_snapshot()
        return self._read(catalog_owner=catalog)

    def _read(self, *, before_binding=False, before_receipt=False, proposal_owner=None, save_edge=None, catalog_owner=None):
        """Intermediate stages require the exact actual live command owner."""
        sources, ledger, edge = self.sources, self.ledger, self.edge
        cap = self.capability; obs = cap['snapshot']['observation']
        if before_binding or before_receipt:
            if self.command_owner is None:
                raise ValueError('返工中间保留只在当前真实未提交命令内可读。')
            self.command_owner.require(sources)
        runs = [r for r in sources.all(self.db, 'work_unit_run') if r.get('command_id') == cap['command_id']]
        if len(runs) != 1:
            raise ValueError('文字返工能力须有唯一准确真实保留。')
        run, task, attempt, plan, unit, reserved = sources.run(runs[0]['id'])
        fields(run, RUN_FIELDS)
        frozen = review_input(cap)
        if (run['command_id'] != cap['command_id'] or plan != edge.parent_plan or
                {'key': unit['key'], 'revision': digest(unit)} != obs['review_coordinate'] or
                run['input'] != frozen or not edge.opened['seq'] < reserved['seq'] or
                not edge.opened['created'] <= task['created'] <= run['created']):
            raise ValueError('返工实际保留未绑定本边独立输入及原准确协调定义。')
        sources.verify_input(run, frozen, review_prompt(frozen), obs['driver'],
            obs['authorization'], obs['usage']['attempts'] + 1)
        actual_runs = [r for r in sources.all(self.db, 'work_unit_run') if r.get('item_id') == plan['item_id']]
        if save_edge is not None:
            actual_runs = [r for r in actual_runs if sources.run(r['id'])[-1]['seq'] < save_edge.boundary]
        owned = {r['id'] for r in self.parent['owner'].replay.runs.values()} | {run['id']}
        if {r['id'] for r in actual_runs if r.get('item_id') == plan['item_id']} != owned:
            raise ValueError('返工实际保留全集须等于完整原前缀及唯一新审阅运行。')
        owner = 'text-review-run:' + run['id']
        ledger.row('work_unit_run', run, owner)
        exited = attempt['state'] != 'RESERVED'
        if exited:
            if before_binding or before_receipt:
                raise ValueError('未提交审阅保留不能已有运行退出。')
            proof = sources.text_review_exited(run, edge)
            ledger.execution(proof, owner)
        else:
            proof = self._reserved(run, task, attempt, plan, unit, reserved, owner)
        if before_binding:
            if self.db.execute('SELECT 1 FROM collaboration WHERE id=?', ('dispatch:' + attempt['id'],)).fetchone():
                raise ValueError('审阅准入前不应已有dispatch绑定。')
            link = None; response = None
            driver = obs['driver']
        else:
            dispatch = sources.get(self.db, 'work_dispatch', 'dispatch:' + attempt['id'])
            self._dispatch(run, task, attempt, dispatch, proof)
            ledger.row('work_dispatch', dispatch, owner)
            dispatch_event = ledger.event('work_dispatch_bound', dispatch, owner,
                task=task['id'], attempt=attempt['id'])
            if not reserved['seq'] < dispatch_event['seq']:
                raise ValueError('返工dispatch须在真实保留之后。')
            proof['dispatch'] = dispatch
            link = self._link(run, proof, obs['driver'], owner)
            driver = link['driver']; response = response_for(run, task['spec_hash'], dispatch)
            self._receipt(run, driver, response, link, owner, before_receipt)
        history = self._history(run, proof, driver) if exited else None
        previous = self.parent['owner'].proposals.previous(edge.boundary)
        proposal = self._proposal_checks(history, run, proof, previous, proposal_owner,
            boundary=save_edge.boundary if save_edge is not None else None) if exited else previous
        if save_edge is None:
            self._pointers(plan, driver, obs['authorization'], proposal)
            if catalog_owner is None:
                ledger.close(protocol_prefixes=('COORDINATOR_FILE_HANDOFF_',))
        else:
            save_edge.require(sources)
            if not exited or proof['exited']['seq'] >= save_edge.boundary:
                raise ValueError('返工保存之前审阅运行须已准确退出。')
        sources.assets.recheck(); edge.require(sources, plan)
        return {'parent': self.parent, 'run': run, 'input': frozen, 'proof': proof,
            'link': link, 'driver': driver, 'response': response, 'exited': exited,
            'history': history, 'proposal': proposal, 'sources': sources, 'ledger': ledger}

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
        if self.command_owner is not None and body['implementation_revision'] != self.command_owner.service.implementation:
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
        if not self.edge.opened['seq'] < created['seq'] < execution['seq'] < resource['seq'] < reserved['seq']:
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
                'session_id': run['initial_session_id'], 'command_id': run['command_id'], 'command_kind': c.SEND,
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
        timeline, sources, ledger, db = self.parent['owner'].timeline, self.sources, self.ledger, self.db
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

    def _receipt(self, run, driver, response, link, owner, before_receipt):
        rows = [r for r in self.sources.all(self.db, c.KINDS[c.RECEIPT])
            if r.get('command_id') == run['command_id']]
        if before_receipt:
            if rows:
                raise ValueError('返工回执写前不应已有见证。')
            return
        if len(rows) != 1:
            raise ValueError('返工每次真实审阅须有唯一完整回执。')
        row = c.validate_receipt(rows[0]); body = receipt_body(self.capability, run, driver, response)
        if row != {'id': c.KINDS[c.RECEIPT] + ':' + body['version'], 'version': body['version'],
                'snapshot': body, 'item_id': run['item_id'], 'command_id': run['command_id'], 'created': row['created']}:
            raise ValueError('审阅完整回执必须等于独立重建的业务绑定后完整返回值。')
        event = self.ledger.event(c.EVENTS[c.RECEIPT], row, owner)
        if not link['event']['seq'] < event['seq'] or not driver['updated'] <= row['created'] <= event['created']:
            raise ValueError('审阅完整回执须晚于真实业务及driver绑定。')
        self.ledger.row(c.KINDS[c.RECEIPT], row, owner)
        if self.command_owner is None:
            self.ledger.command(run['command_id'], c.SEND, self.capability['snapshot']['request'], response,
                owner, not_before=event['created'])
        else:
            self.command_owner.require(self.sources)

    def _history(self, run, proof, driver):
        sources = self.sources
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
        history = deepcopy(self.parent['history'])
        history.update(task={'id': run['item_id'], 'revision': driver['item_revision']}, driver=deepcopy(driver),
            frames=[*history['frames'], frame])
        # REWORK_REVIEW is an additional typed round, not a replacement of the
        # original parent sources used by ordinary dependencies and previews.
        values = self.parent['values']
        history['frontier'] = [values.old.frontier_entry(f) for f in history['frames']]
        from .store import Store
        usages = [Store.usage(None, attempt=f['run']['attempt_id'], db=self.db) for f in history['frames']]
        numeric = ('model_requests', 'reserved_cny', 'tool_calls', 'elapsed_observed_seconds',
            'api_known_input_tokens', 'api_known_output_tokens', 'api_requests_without_token_receipt', 'unknown_requests')
        usage = {k: round(sum(value[k] for value in usages), 3) for k in numeric}
        for value in usage.values():
            c.finite(value)
            if value < 0:
                raise ValueError('返工实际累计不能为负。')
        history['usage'] = {**self.parent['history']['usage'], **usage, 'attempts': len(history['frames'])}
        from .work_coordinator_handoff_records import versioned
        history['review_path'] = versioned({'protocol': c.PATH,
            'root': self.capability['snapshot']['root'], 'parent_plan': self.capability['snapshot']['parent_plan'],
            'parent_saved_path': self.parent['history'].get('saved_path'),
            'capability': {k: self.capability[k] for k in ('id', 'version')},
            'task': history['task'], 'driver': history['driver'], 'authorization': history['authorization'],
            'phase': history['phase'], 'frontier': history['frontier'],
            'material_observation': history['material_observation'], 'usage': history['usage'],
            'review_run': {'id': run['id'], 'version': digest(run)}, **c.PERMISSIONS})
        return history

    def _proposal_checks(self, history, run, proof, previous, pending, *, boundary=None):
        from . import work_coordinator_handoff_text_rework_review_proposals as p
        rows = []
        for raw in self.db.execute('SELECT * FROM events WHERE kind=? ORDER BY seq', (c.EVENTS[c.PROPOSAL],)):
            if boundary is not None and raw['seq'] >= boundary:
                break
            value = _json(raw['payload'])
            if value.get('item_id') == run['item_id']:
                rows.append((raw, p.validate(value)))
        for raw, row in rows:
            body = row['snapshot']; proposal = body['proposal']
            checked = self.sources._event(self.db, 'coordinator_proposal_checked', proposal,
                raw['seq'], run['task_id'], run['attempt_id'])
            event_ref = {k: checked[k] for k in ('seq', 'kind', 'payload_digest')}
            exit_ref = {k: proof['exited'][k] for k in ('seq', 'kind', 'payload_digest')}
            expected = p.snapshot(self.capability, history, run, history['frames'][-1], exit_ref,
                body['request'], previous, proposal['created'])
            expected_body = p.witness_body(self.capability, body['request'], previous, proposal, event_ref)
            if (body != expected_body or proposal['snapshot'] != expected or
                    self.sources.get(self.db, 'work_proposal', proposal['id']) != proposal or
                    self.sources.get(self.db, c.KINDS[c.PROPOSAL], row['id']) != row or
                    not proof['exited']['seq'] < checked['seq'] < raw['seq'] or
                    not proof['exited']['created'] <= proposal['created'] <= checked['created'] <= row['created'] <= raw['at'] or
                    raw['task_id'] is not None or raw['attempt_id'] is not None):
                raise ValueError('返工提案原请求、严格回复、完整预览、响应或真实事件顺序不符。')
            owner = 'text-review-check:' + row['id']
            self.ledger.row('work_proposal', proposal, owner)
            self.ledger.event_at(checked['seq'], checked['kind'], proposal, owner,
                task=run['task_id'], attempt=run['attempt_id'])
            self.ledger.row(c.KINDS[c.PROPOSAL], row, owner)
            self.ledger.event_at(raw['seq'], raw['kind'], row, owner)
            command = self.db.execute('SELECT * FROM commands WHERE id=?', (row['command_id'],)).fetchone()
            if command is None:
                from .work_coordinator_handoff_text_rework_review_check_scope import _ReviewCheck
                if type(pending) is not _ReviewCheck:
                    raise ValueError('返工保存前检查缺少原完整命令或准确本次检查owner。')
                pending.require_witness(self, row, raw['seq'])
            else:
                if pending is not None and pending.identity == row['command_id']:
                    raise ValueError('已提交提案不能复用pending检查owner。')
                self.ledger.command(row['command_id'], p.COMMAND, body['request'], body['response'], owner,
                    not_before=raw['at'])
            previous = proposal
        return previous

    def _pointers(self, plan, driver, auth, proposal):
        ledger, item = self.ledger, plan['item_id']; owner = 'current:' + item
        ledger.row('teamwork_current', {'id': 'teamwork-current:' + item, 'plan_id': plan['id']}, owner)
        ledger.row('work_driver', driver, owner)
        ledger.row('work_unit_authorization', auth, owner)
        pointer_id = 'proposal-current:' + item
        ledger.identify(pointer_id)
        if proposal is not None:
            ledger.row('work_proposal_current', {'id': pointer_id, 'proposal_id': proposal['id']}, owner)
        elif self.db.execute('SELECT 1 FROM collaboration WHERE id=?', (pointer_id,)).fetchone():
            raise ValueError('审阅没有历史提案时实际proposal-current必须不存在。')
