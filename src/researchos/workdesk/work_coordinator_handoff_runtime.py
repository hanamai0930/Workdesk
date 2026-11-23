"""Versioned internal coordinator runtime; old frozen V1 inputs stay V1.

All historical views are reconstructed from persisted event boundaries. Only
the command path may attach current originals; a historical CURRENT is data.
"""
from copy import deepcopy
import json
import math
import time

from .store import canonical, digest
from .work_transitions import plan_ref
from .work_coordinator_handoff_roots import CONFIG_FIELDS, MARKER_FIELD
from .work_coordinator_handoff_context import WorkCoordinatorHandoffContext, TERMINAL
from .work_coordinator_handoff_ledger import WorkCoordinatorHandoffLedger
from .work_coordinator_handoff_records import (
    INTENT, DELIVERY, COMPLETION, UNIT_INPUT, DELIVERY_KIND, COMPLETION_KIND,
    PERMISSIONS, validate, versioned)

CAPABILITY_KIND = 'work_coordinator_handoff_capability'
CAPABILITY = 'COORDINATOR_FILE_HANDOFF_RUNTIME_CAPABILITY_V1'
PROTOCOL = 'COORDINATOR_FILE_HANDOFF_COORDINATION_INPUT_V2'
READ = 'COORDINATOR_FILE_HANDOFF_STAGE_HISTORY_V1'
COORD_PROMPT = '读取完整冻结协调输入，只返回候选的 dispatch_schema 或 pause_schema 对应严格 JSON；不写文件。\n输入快照 JSON：\n'
UNIT_PROMPT = '完成已声明合作单元，读取准确依赖与文件附件，遵守原要求和写入声明；提案仅为数据。\n输入快照 JSON：\n'


def enabled(service, db, plan):
    return bool(plan and any(r.get('item_id') == plan['item_id'] or
        r.get('snapshot', {}).get('root', {}).get('id') == plan['id']
        for r in service.collaboration.all(db, CAPABILITY_KIND)))


def reader(service, db, plan):
    from .work_coordinator_handoff_text_rework_runtime import present, TextRuntime
    if present(service, db, plan):
        return TextRuntime(service)
    if plan and plan.get('coordinator_file_handoff_post_round_lineage'):
        from .work_coordinator_handoff_post_round_v3_public import is_v3
        if is_v3(plan):
            from .work_coordinator_handoff_post_round_v3_runtime import present, WorkCoordinatorHandoffPostRoundV3Runtime
            if present(service, db, plan): return WorkCoordinatorHandoffPostRoundV3Runtime(service)
            from .work_coordinator_handoff_post_round_v3_responsibility_current import WorkCoordinatorHandoffPostRoundV3ResponsibilityCurrent
            return WorkCoordinatorHandoffPostRoundV3ResponsibilityCurrent(service)
        if plan['coordinator_file_handoff_post_round_lineage'].get('protocol') == 'COORDINATOR_FILE_HANDOFF_POST_ROUND_ADJUSTMENT_LINEAGE_V2':
            from .work_coordinator_handoff_post_round_v2_runtime import present, WorkCoordinatorHandoffPostRoundV2Runtime
            if present(service, db, plan): return WorkCoordinatorHandoffPostRoundV2Runtime(service)
            from .work_coordinator_handoff_post_round_v2_saved_tree import WorkCoordinatorHandoffPostRoundV2SavedTree
            return WorkCoordinatorHandoffPostRoundV2SavedTree(service)
        if plan['coordinator_file_handoff_post_round_lineage'].get('protocol') != 'COORDINATOR_FILE_HANDOFF_POST_ROUND_ADJUSTMENT_LINEAGE_V1':
            raise ValueError('未知POST保存谱系，不能降级为旧读取或运行合同。')
        from .work_coordinator_handoff_post_round_successor_runtime import present, WorkCoordinatorHandoffPostRoundSuccessorRuntime
        if present(service, db, plan): return WorkCoordinatorHandoffPostRoundSuccessorRuntime(service)
        from .work_coordinator_handoff_post_round_saved_tree import WorkCoordinatorHandoffPostRoundSavedTree
        return WorkCoordinatorHandoffPostRoundSavedTree(service)
    if plan and plan.get('coordinator_file_handoff_round_lineage'):
        from .work_coordinator_handoff_post_round_runtime import enabled as post_enabled, runtime as post_runtime
        if post_enabled(service, db, plan): return post_runtime(service, plan)
        if plan['coordinator_file_handoff_round_lineage'].get('protocol') == 'COORDINATOR_FILE_HANDOFF_MIXED_ROUND_LINEAGE_V1':
            from .work_coordinator_handoff_mixed_runtime import WorkCoordinatorHandoffMixedRuntime
            return WorkCoordinatorHandoffMixedRuntime(service)
        from .work_coordinator_handoff_round_runtime import WorkCoordinatorHandoffRoundRuntime
        return WorkCoordinatorHandoffRoundRuntime(service)
    if plan and plan.get('coordinator_file_handoff_lineage'):
        from .work_coordinator_handoff_adjustment_path import WorkCoordinatorHandoffAdjustmentPath
        return WorkCoordinatorHandoffAdjustmentPath(service)
    from .work_coordinator_handoff_adjustment_runtime import enabled as adjustment_enabled, WorkCoordinatorHandoffAdjustmentRuntime
    if adjustment_enabled(service, db, plan):
        return WorkCoordinatorHandoffAdjustmentRuntime(service)
    return WorkCoordinatorHandoffRuntime(service) if enabled(service, db, plan) else WorkCoordinatorHandoffContext(service)


class WorkCoordinatorHandoffRuntime:
    def __init__(self, service):
        self.s = service
        self.old = WorkCoordinatorHandoffContext(service)
        self.sources = WorkCoordinatorHandoffLedger(service)
        self.records = self.sources.records

    def activate(self, db, plan):
        """Internal only, once; validates all previously reachable V1 history."""
        if enabled(self.s, db, plan):
            raise ValueError('该原根已有运行能力身份，不能重复激活。')
        history = self.sources.read(db, plan)
        body = versioned({'protocol': CAPABILITY, 'root': plan_ref(plan), 'marker': plan[MARKER_FIELD],
            'configuration_version': digest({k: plan[k] for k in CONFIG_FIELDS}),
            'activation_frontier': history['frontier'], 'coordination_protocol': PROTOCOL,
            'unit_protocol': UNIT_INPUT, 'allowed_actions': ['dispatch', 'pause'], **PERMISSIONS,
            'scope': 'INTERNAL_ORIGINAL_ROOT_SERIAL_FIRST_RUNS; SIX_REVISION_ENTRIES_NOT_OPEN'})
        row = {'id': CAPABILITY_KIND + ':' + body['version'], 'version': body['version'],
               'snapshot': body, 'item_id': plan['item_id'], 'created': time.time(),
               'command_id': self.s.command_identity}
        if db.execute('SELECT 1 FROM collaboration WHERE id=?', (row['id'],)).fetchone():
            raise ValueError('运行能力内容身份已占用。')
        self.s.collaboration.put(db, CAPABILITY_KIND, row)
        self.s.store.event(None, None, 'coordinator_runtime_opened', row, db)
        self.capability(db, plan)
        return deepcopy(row)

    def capability(self, db, plan):
        rows = [r for r in self.s.collaboration.all(db, CAPABILITY_KIND)
                if r.get('item_id') == plan['item_id'] or r.get('snapshot', {}).get('root', {}).get('id') == plan['id']]
        if len(rows) != 1:
            raise ValueError('新根缺少唯一准确能力身份。')
        row = rows[0]; body = row['snapshot']
        expected = versioned({'protocol': CAPABILITY, 'root': plan_ref(plan), 'marker': plan[MARKER_FIELD],
            'configuration_version': digest({k: plan[k] for k in CONFIG_FIELDS}),
            'activation_frontier': body['activation_frontier'], 'coordination_protocol': PROTOCOL,
            'unit_protocol': UNIT_INPUT, 'allowed_actions': ['dispatch', 'pause'], **PERMISSIONS,
            'scope': 'INTERNAL_ORIGINAL_ROOT_SERIAL_FIRST_RUNS; SIX_REVISION_ENTRIES_NOT_OPEN'})
        if (set(row) != {'id', 'version', 'snapshot', 'item_id', 'created', 'command_id'} or body != expected or
                row['id'] != CAPABILITY_KIND + ':' + body['version'] or row['version'] != body['version'] or
                row['item_id'] != plan['item_id'] or type(row['created']) not in (int, float) or
                not math.isfinite(row['created']) or row['created'] < plan['created'] or
                not isinstance(row['command_id'], str) or not row['command_id']):
            raise ValueError('运行能力的完整原根、字段或配置不一致。')
        seq = self._event(db, 'coordinator_runtime_opened', row)
        return deepcopy(row), seq

    @staticmethod
    def ref(row):
        return {k: row[k] for k in ('id', 'version')}

    def _event(self, db, kind, payload):
        rows = [r['seq'] for r in db.execute('SELECT seq,payload FROM events WHERE kind=? ORDER BY seq', (kind,))
                if json.loads(r['payload']) == payload]
        if len(rows) != 1:
            raise ValueError('不可变记录缺少唯一准确实际事件：' + kind)
        return rows[0]

    def _driver_event(self, db, driver):
        events = [r['seq'] for r in db.execute("SELECT seq,payload FROM events WHERE kind LIKE 'work_driver_%'")
                  if json.loads(r['payload']) == {**driver, 'no_dispatch': True}]
        if len(events) != 1:
            raise ValueError('原责任版本缺少唯一实际责任事件。')
        return events[0]

    def _records(self, db, plan):
        result = {}
        for kind, event in ((DELIVERY_KIND, 'coordinator_handoff_delivered'),
                            (COMPLETION_KIND, 'coordinator_handoff_completed')):
            values = []
            for row in self.s.collaboration.all(db, kind):
                body = row.get('snapshot', {})
                if (row.get('item_id') != plan['item_id'] and body.get('item_id') != plan['item_id'] and
                        body.get('root', {}).get('id') != plan['id']):
                    continue
                saved = self.records.get(db, kind, self.records.reference(row))
                if body['root'] != plan_ref(plan) or body['marker'] != plan[MARKER_FIELD]:
                    raise ValueError('阶段记录属于其它根。')
                values.append((saved, self._event(db, event, saved)))
            if len(values) > 1:
                raise ValueError('原根只允许唯一首次交付与完成。')
            result[kind] = values
        return result

    def _phase(self, plan, frames, records):
        relation = plan['file_handoffs'][0]
        states = {f['run']['unit_key']: f for f in frames}
        a, b = states.get(relation['source_unit']), states.get(relation['target_unit'])
        if b:
            if b['evidence']['state'] not in TERMINAL:
                return 'B_IN_FLIGHT'
            if b['evidence']['blockers'] or b['evidence']['state'] != 'COMPLETED':
                return 'BLOCKED'
            if records[COMPLETION_KIND]:
                return 'B_COMPLETED'
            return 'B_EXITED_UNCAPTURED'
        if a and a['evidence']['state'] in TERMINAL - {'COMPLETED'}:
            return 'BLOCKED'
        return 'A_CAPTURED' if a and a['source_evidence']['file_artifacts']['state'] == 'CURRENT' else 'BEFORE_A'

    def _dependencies(self, plan, unit, frames, phase, pending=None):
        by_key = {f['run']['unit_key']: f for f in frames}
        relation = plan['file_handoffs'][0]
        values = []
        for key in unit['depends']:
            if key == pending:
                continue
            if key == relation['source_unit'] and phase in {'B_IN_FLIGHT', 'B_EXITED_UNCAPTURED', 'B_COMPLETED'}:
                raise ValueError('显式依赖原A已交付；历史退役来源不能用作当前附件。')
            if key == relation['target_unit'] and phase != 'B_COMPLETED':
                raise ValueError('后继B尚未准确捕获完成。')
            frame = by_key.get(key)
            if not frame or frame['evidence']['blockers']:
                raise ValueError('声明依赖没有准确已退出来源：' + key)
            full = frame['source_evidence']
            self.s.work_artifacts.require(full, unit['dependency_gate'])
            values.append({'unit_key': key, **full})
        return values

    def _admit(self, plan, unit, frames, phase, pending=None):
        if unit not in plan['units'] or any(f['run']['unit_key'] == unit['key'] for f in frames):
            raise ValueError('只允许原声明单元首次运行，不能重派或改定义。')
        if phase in {'B_IN_FLIGHT', 'B_EXITED_UNCAPTURED', 'BLOCKED'}:
            raise ValueError('当前交付阶段阻止新增运行：' + phase)
        if any(f['evidence']['state'] not in TERMINAL or f['evidence']['blockers'] for f in frames):
            raise ValueError('完整前沿存在活动或未知退出状态。')
        if unit['key'] == plan['file_handoffs'][0]['target_unit'] and phase != 'A_CAPTURED':
            raise ValueError('B须以准确捕获的A作为交付来源。')
        return self._dependencies(plan, unit, frames, phase, pending)

    def _forecast(self, plan, context, frames, unit):
        forecast = self.old.forecast(plan, context, unit)
        forecast.update(allowed_actions=['dispatch', 'pause'],
            dispatch_schema={'schema_version': 1, 'action': 'dispatch', 'unit_key': 'candidate key',
                             'retry_of': None, 'reason': 'selection reason'},
            scope='DECLARED_FIRST_ORDINARY_RUNS; SIX_REVISION_ENTRIES_NOT_OPEN')
        for candidate in forecast['candidates']:
            target = candidate['unit']
            for field, pending in (('ready', None), ('ready_after_proposal', unit['key'])):
                try:
                    self._admit(plan, target, frames, context['phase'], pending)
                    candidate[field] = True
                    if field == 'ready': candidate['blocked_reason'] = ''
                except ValueError as exc:
                    candidate[field] = False
                    if field == 'ready': candidate['blocked_reason'] = str(exc)
            found = next((f for f in frames if f['run']['unit_key'] == target['key']), None)
            candidate['latest_result'] = found['evidence'] if found else None
        return forecast

    def _input(self, plan, unit, task, driver, auth, frames, observation, records, cap, proposal=None):
        phase = self._phase(plan, frames, records)
        dependencies = self._admit(plan, unit, frames, phase)
        value = {'item_id': plan['item_id'], 'item_revision': task['revision'],
            'plan_id': plan['id'], 'plan_version': plan['version'], 'work_requirements': plan['work_requirements'],
            'unit': unit, 'dependencies': dependencies,
            'check_evidence': [{'unit_key': d['unit_key'], 'artifact': d['file_artifacts']['reference'],
                                'check_status': d['check_status']} for d in dependencies],
            'file_artifacts': [{'unit_key': d['unit_key'], **d['file_artifacts']} for d in dependencies
                               if d['file_artifacts']['state'] == 'CURRENT']}
        frontier = [self.old.frontier_entry(f) for f in frames]
        if unit['role'] == 'coordinate':
            context = self.old._context(plan, task, driver, auth, frames, observation)
            relation = plan['file_handoffs'][0]
            for entry in context['units']:
                key = entry['definition']['key']
                entry['qualification'] = ('HISTORICAL_RETIRED' if phase == 'B_COMPLETED' and key == relation['source_unit']
                    else 'CURRENT_COMPLETED' if phase == 'B_COMPLETED' and key == relation['target_unit'] else
                    'PROTECTED_HISTORY' if phase in {'B_IN_FLIGHT', 'B_EXITED_UNCAPTURED'} and key == relation['source_unit']
                    else 'RECORDED' if entry['source_run'] else 'NOT_RUN')
            context.update(protocol=PROTOCOL, capability=self.ref(cap), phase=phase,
                           prior_frontier=frontier, allowed_actions=['dispatch', 'pause'],
                           scope='FROZEN_STAGE_HISTORY; NO_MATERIAL_OR_EXECUTION_PERMISSION')
            context = versioned({k: v for k, v in context.items() if k != 'version'})
            value.update(handoff_coordination_context=context,
                         coordination_context=self._forecast(plan, context, frames, unit))
        else:
            if proposal is None:
                raise ValueError('普通单元缺少准确V2提案。')
            context = versioned({'protocol': UNIT_INPUT, 'root': plan_ref(plan), 'marker': plan[MARKER_FIELD],
                'task': task, 'driver': driver, 'authorization': auth, 'unit_revision': digest(unit),
                'proposal': proposal, 'phase': phase, 'prior_frontier': frontier,
                'material_observation': observation, **PERMISSIONS,
                'scope': 'FROZEN_FIRST_ORDINARY_INPUT; SOURCE_IS_EXACT_V2_COORDINATOR'})
            validate(context, UNIT_INPUT)
            value.update(handoff_unit_context=context, coordination_proposal=proposal)
        return value

    @staticmethod
    def prompt(frozen):
        return (COORD_PROMPT if frozen['unit']['role'] == 'coordinate' else UNIT_PROMPT) + canonical(frozen)

    @staticmethod
    def materials(frozen):
        expected = {}
        for files in frozen['file_artifacts']:
            for name, value in files['manifest'].items():
                if name in expected and expected[name] != value:
                    raise ValueError('准确依赖文件版本冲突。')
                expected[name] = value
        return expected

    def _proposal(self, db, plan, unit, frames, proposal, task, driver, auth, consumer=None):
        if not isinstance(proposal, dict) or set(proposal) != {'reference', 'source', 'decision'}:
            raise ValueError('普通运行缺少完整准确消费提案。')
        row = self.s.collaboration.get(db, 'work_proposal', proposal['reference']['id'])
        body = row['snapshot']
        if (set(row) != {'id', 'version', 'snapshot', 'item_id', 'created', 'command_id'} or
                self.s.work_proposals.reference(row) != proposal['reference'] or row['version'] != digest(body) or
                row['item_id'] != plan['item_id'] or body['state'] != 'VALID' or
                {k: body[k] for k in ('source', 'decision')} != {k: proposal[k] for k in ('source', 'decision')} or
                body['item_revision'] != task['revision'] or body['driver'] != self.s.work_drivers.reference(driver) or
                body['authorization'] != self.s.work_units.auth_ref(auth) or
                body['plan'] != {k: plan[k] for k in ('id', 'version')} or
                body['target'] != {'key': unit['key'], 'revision': digest(unit)} or
                body['decision']['unit_key'] != unit['key'] or body['decision']['retry_of'] is not None or
                body['frontier'] != digest([{'run_id': f['run']['id'], 'evidence': f['evidence']['version']} for f in frames])):
            raise ValueError('普通运行提案脱离原任务、前沿、责任、授权或目标。')
        sources = [f for f in frames if f['run']['input']['unit']['role'] == 'coordinate']
        source = sources[-1] if sources else None
        if not source or source['run']['id'] != proposal['source']['run_id']:
            raise ValueError('提案不是本回合前最新准确coordinate。')
        frozen = source['run']['input']
        full = source['source_evidence']
        if (not self._source_protocol(db, plan, source['run'], consumer) or
                body['handoff_context'] != frozen['coordination_context']['handoff_context'] or
                proposal['source'] != {k: full[k] for k in proposal['source']} or not full['result_available']):
            raise ValueError('普通提案须来自真实V2准确退出来源。')
        from .work_proposals import parse
        candidate = next(c for c in frozen['coordination_context']['candidates'] if c['unit']['key'] == unit['key'])
        if (parse(full['answer']) != proposal['decision'] or body['answer_digest'] != digest(full['answer']) or
                not candidate['ready_after_proposal'] or candidate['unit'] != unit):
            raise ValueError('普通目标不是原冻结候选与真实提案回复。')
        checked = self._event(db, 'coordinator_proposal_checked', row)
        boundary = self.sources._reservation_event(db, consumer) if consumer else None
        latest = [json.loads(r['payload']) for r in db.execute(
            "SELECT seq,payload FROM events WHERE kind='coordinator_proposal_checked' ORDER BY seq")
            if (boundary is None or r['seq'] < boundary) and json.loads(r['payload']).get('item_id') == plan['item_id']]
        if not latest or latest[-1] != row or (boundary is not None and checked >= boundary):
            raise ValueError('普通消费须为实际保留前最新唯一准确VALID提案。')
        return row

    def _source_protocol(self, db, plan, source, consumer):
        return source['input'].get('handoff_coordination_context', {}).get('protocol') == PROTOCOL

    def _run_protocol(self, db, plan, run, context, boundary):
        return context['protocol'] == (PROTOCOL if run['input']['unit']['role'] == 'coordinate' else UNIT_INPUT)

    def _historical_input(self, db, run, *args):
        return self._input(*args)

    def _intent(self, plan, frozen, frames):
        unit = frozen['unit']; context = frozen['handoff_unit_context']
        relation = plan['file_handoffs'][0]
        source = next(f for f in frames if f['run']['unit_key'] == relation['source_unit'])
        full = source['source_evidence']; baseline = context['material_observation']
        self.s.work_artifacts.require(full, 'checked' if source['run']['input']['unit']['checks'] or
            unit['dependency_gate'] == 'checked' else 'files')
        if baseline['missing'] or baseline['manifest'] != full['file_artifacts']['manifest']:
            raise ValueError('B写前基线不等于准确A全部当前字节。')
        body = versioned({'protocol': INTENT, 'item_id': plan['item_id'],
            'item_revision': context['task']['revision'], 'root': plan_ref(plan), 'marker': plan[MARKER_FIELD],
            'relation': relation, 'driver': context['driver'], 'authorization': context['authorization'],
            'source_run': source['run']['id'], 'source_evidence': full, 'target_revision': digest(unit),
            'protected_history': context['prior_frontier'], 'baseline': baseline,
            'input_template_version': digest(frozen), 'frontier': context['prior_frontier'],
            'scope': 'EXACT_FIRST_B_INTENT; PROTECTED_ALL_PRIOR_HISTORY'})
        return validate(body, INTENT)

    def _read(self, db, plan):
        changes = db.total_changes
        item, root = self.old._root(db, plan)
        cap, activated = self.capability(db, plan)
        driver = self.s.work_drivers.current(db, item['id'])
        auth = self.s.work_units.authorization(db, driver)
        revisions = [r for r in self.s.collaboration.all(db, 'work_driver_revision') if r.get('item_id') == item['id']]
        if driver:
            if self.old._driver(db, plan, self.s.work_drivers.reference(driver)) != driver:
                raise ValueError('当前责任不是原不可变版本。')
            for saved in revisions:
                if saved['generation'] != driver['generation']:
                    raise ValueError('原根generation发生变化。')
                original_driver = self.old._driver(db, plan, {'id': saved['driver_id'], 'generation': saved['generation'], 'revision': saved['revision']})
                self._driver_event(db, original_driver)
        elif revisions:
            raise ValueError('原根责任历史缺失。')
        auths = [r for r in self.s.collaboration.all(db, 'work_unit_authorization_revision') if r.get('item_id') == item['id']]
        if auth:
            if (self.old._auth(db, plan, self.s.work_units.auth_ref(auth)) != auth or len(auths) != 1 or
                    auth['driver_generation'] != driver['generation']):
                raise ValueError('原根累计授权版本不一致。')
            self._event(db, 'cooperation_authorized', auth)
        elif auths:
            raise ValueError('原累计授权缺失。')
        rows = self.s.work_units.rows(db, item['id']); records = self._records(db, plan)
        if rows and (not driver or not auth):
            raise ValueError('真实运行缺少原责任与授权。')
        frames, revision, seen = self._reconstruct_runs(
            db, plan, item, root, cap, activated, driver, auth, revisions, rows, records)
        if revision != item['revision'] or (driver and driver['item_revision'] != revision):
            raise ValueError('当前任务修订脱离完整运行绑定历史。')
        self._match_records(db, plan, frames, records)
        observation = self.s.work_file_rework_applications.observe(root, plan['file_handoffs'][0]['paths'])
        phase = self._phase(plan, frames, records)
        current_frames = deepcopy(frames)
        for frame in current_frames:
            frame['source_evidence'] = self.s.work_units.evidence(db, frame['run'])
        current_by_key = {f['run']['unit_key']: f for f in current_frames}
        expected_current = (plan['file_handoffs'][0]['target_unit'] if phase == 'B_COMPLETED' else
                            plan['file_handoffs'][0]['source_unit'] if phase == 'A_CAPTURED' else None)
        if expected_current and current_by_key[expected_current]['source_evidence']['file_artifacts']['state'] != 'CURRENT':
            phase = 'BLOCKED'
        if db.total_changes != changes or self.s.teamwork.current(db, item['id']) != plan:
            raise ValueError('阶段读取期间数据库或current变化。')
        return deepcopy({'protocol': READ, 'root': plan_ref(plan), 'marker': plan[MARKER_FIELD],
            'capability': self.ref(cap), 'task': {'id': item['id'], 'revision': revision}, 'driver': driver,
            'authorization': auth, 'frames': frames, 'current_frames': current_frames, 'phase': phase,
            'frontier': [self.old.frontier_entry(f) for f in frames], 'material_observation': observation,
            'delivery': [r for r, _ in records[DELIVERY_KIND]], 'completion': [r for r, _ in records[COMPLETION_KIND]],
            'units': [{'definition': u, 'qualification': 'HISTORICAL_RETIRED' if phase == 'B_COMPLETED' and
                u['key'] == plan['file_handoffs'][0]['source_unit'] else 'CURRENT_COMPLETED' if phase == 'B_COMPLETED' and
                u['key'] == plan['file_handoffs'][0]['target_unit'] else 'RECORDED' if u['key'] in seen else 'NOT_RUN',
                **PERMISSIONS} for u in plan['units']], **PERMISSIONS})

    def _reconstruct_runs(self, db, plan, item, root, cap, activated, driver, auth, revisions, rows, records):
        """Rebuild supplied historical runs; caller must validate its whole scope.

        This private method grants no current-plan or saved-edge authority.
        The original _read still checks unique current root/auth before calling.
        """
        frames, seen, revision, before_activation = [], set(), plan['item_revision'], []
        for run in rows:
            unit = next((u for u in plan['units'] if u['key'] == run['unit_key']), None)
            task = self.s.store.task(run['task_id'], db); attempt = self.s.store.attempt(run['attempt_id'], db)
            raw = self.s.work_units.base_evidence(db, run)
            old_driver = self.old._driver(db, plan, {'id': driver['id'], 'generation': run['generation'], 'revision': run['driver_revision']})
            old_auth = self.old._auth(db, plan, run['authorization'])
            boundary = self.sources._reservation_event(db, run)
            if self._driver_event(db, old_driver) >= boundary or self._event(db, 'cooperation_authorized', old_auth) >= boundary:
                raise ValueError('原责任或授权晚于实际保留边界。')
            prior = [self.sources._frame_before(db, f['run'], run) for f in frames]
            earlier_records = {k: [(r, seq) for r, seq in v if seq < boundary] for k, v in records.items()}
            if (unit is None or unit['key'] in seen or run['retry_of'] is not None or run['plan_id'] != plan['id'] or
                    run['plan_version'] != plan['version'] or run['item_id'] != item['id'] or run['item_revision'] != revision or
                    run['ordinal_total'] != len(frames) + 1 or run['ordinal_total'] > auth['max_attempts_total'] or
                    run['created'] >= auth['dispatch_before'] or old_auth != auth or old_driver['state'] != 'RESERVED' or
                    old_driver['item_revision'] != revision or old_driver['generation'] != auth['driver_generation'] or
                    run['driver_id'] != driver['id'] or run['unit_revision'] != digest(unit) or
                    run['input_version'] != digest(run['input']) or not raw['exact_identity'] or
                    run['scheduling'] != 'EXPLICIT_SERIAL_STEP' or run['flow'] is not None or
                    attempt['workspace'] != root or run['task_spec_version'] != digest(task['spec'])):
                raise ValueError('阶段运行原run/spec/input/责任/授权/累计身份不一致。')
            frozen = run['input']; context = frozen.get('handoff_coordination_context') if unit['role'] == 'coordinate' else frozen.get('handoff_unit_context')
            if not context:
                raise ValueError('阶段运行缺少原协议冻结输入。')
            observation = context['material_observation']
            from .work_coordinator_handoff_records import observation as validate_observation
            validate_observation(observation)
            if observation['root'] != root or observation['paths'] != sorted(plan['file_handoffs'][0]['paths']):
                raise ValueError('冻结观察不是原目录全部关系文件。')
            self.s.work_file_rework_applications.require_baseline(observation)
            original_task = {'id': item['id'], 'revision': revision}
            legacy = context['protocol'] == 'COORDINATOR_FILE_HANDOFF_COORDINATION_INPUT_V1'
            if legacy:
                if boundary > activated:
                    raise ValueError('新能力激活后不能新增旧V1运行。')
                self.old._coordinator(unit)
                old_prior = [{**f, 'evidence': f['source_evidence']} for f in prior]
                expected = self.old._input(plan, unit, self.old._context(plan, original_task, old_driver, old_auth, old_prior, observation))
                from .work_coordinator_handoff_context import PROMPT
                prompt = PROMPT + canonical(expected)
            else:
                if boundary <= activated or not self._run_protocol(db, plan, run, context, boundary):
                    raise ValueError('阶段运行协议或激活边界不符。')
                proposal = frozen.get('coordination_proposal')
                if unit['role'] != 'coordinate':
                    row = self._proposal(db, plan, unit, prior, proposal, original_task, old_driver, old_auth, run)
                    consumed = [r for r in rows if r.get('proposal') == self.ref(row)]
                    if consumed != [run]:
                        raise ValueError('普通提案没有唯一准确消费。')
                expected = self._historical_input(db, run, plan, unit, original_task, old_driver, old_auth, prior, observation, earlier_records, cap, proposal)
                if unit['key'] == plan['file_handoffs'][0]['target_unit']:
                    expected['handoff_delivery_intent'] = self._intent(plan, expected, prior)
                prompt = self.prompt(expected)
            if frozen != expected:
                raise ValueError('冻结输入不等于按实际边界完整原历史重建。')
            spec = task['spec']; selected = self.s.resources.selection(db, unit['member']['reference']['member_id'])
            binding = {k: selected['connection'][k] for k in ('id', 'provider', 'credential_reference')}
            meta = {'item_id': item['id'], 'source_item_revision': revision, 'driver_id': old_driver['id'],
                'generation': old_driver['generation'], 'driver_revision': old_driver['revision'], 'plan_id': plan['id'],
                'plan_version': plan['version'], 'unit_key': unit['key'], 'unit_revision': digest(unit),
                'input_version': digest(frozen), 'authorization': self.s.work_units.auth_ref(old_auth)}
            if frozen.get('coordination_proposal'):
                meta['proposal'] = frozen['coordination_proposal']['reference']
            from .native_permissions import choice
            resource = spec.get('execution_resource')
            resource_events = list(db.execute(
                "SELECT seq,payload FROM events WHERE task_id=? AND attempt_id=? AND kind='execution_resource_frozen'",
                (run['task_id'], run['attempt_id'])))
            if (not isinstance(resource, dict) or resource['reference'] != unit['member']['reference'] or
                    {k: resource['connection'][k] for k in binding} != binding or
                    any(resource['member'][k] != unit['member'][k] for k in ('name', 'role', 'provider', 'model', 'effort', 'tier')) or
                    resource['quota_source']['id'] != unit['member']['reference']['quota_id'] or
                    resource['quota_source']['revision'] != unit['member']['reference']['quota_revision'] or
                    len(resource_events) != 1 or json.loads(resource_events[0]['payload']) != resource or
                    resource_events[0]['seq'] >= boundary):
                raise ValueError('原实际资源冻结、准确成员/连接/额度引用或事件不一致。')
            from .attachments import normalize, freeze
            attachments = normalize([{'file': n, 'route': 'native'} for n in self.materials(frozen)])
            freeze(attachments, self.s.locations.data / 'tasks' / run['task_id'] / 'input', self.materials(frozen))
            for attachment in attachments:
                attachment['delivery'] = 'NATIVE_IMAGE_INPUT' if attachment['kind'] == 'image' and spec['provider'] != 'qwen' else 'NATIVE_FILE_TOOLS'
            if (spec['goal'] != prompt or spec['materials'] != self.materials(frozen) or
                    spec['attachments'] != attachments or
                    spec['writable'] or spec['checks'] or spec['work_mode'] != 'cooperative' or
                    spec['native'] != {'configuration': 'VENDOR_DEFAULTS'} or spec['access'] != choice(None) or
                    spec['execution_connection'] != binding or any(spec[k] != unit['member'][k] for k in ('provider', 'model', 'effort', 'tier')) or
                    run['cooperative'] != meta or any(run.get(k) != v for k, v in meta.items())):
                raise ValueError('原实际spec、附件、原生权限或完整cooperative绑定不符。')
            links = list({digest(l): l for r in revisions for l in r.get('revision_lineage', []) if l['unit_run_id'] == run['id']}.values())
            if links:
                if len(links) != 1 or links[0]['from'] != revision:
                    raise ValueError('原任务绑定历史不一致。')
                revision = links[0]['to']
            elif not (attempt['state'] == 'RESERVED' and run['command_id'] == self.s.command_identity):
                raise ValueError('运行缺少准确任务绑定历史。')
            files, current = self.sources._saved_files(db, run)
            if raw['state'] == 'COMPLETED':
                exits = [r['seq'] for r in db.execute(
                    "SELECT seq,payload FROM events WHERE task_id=? AND attempt_id=? AND kind='attempt_result_saved'",
                    (run['task_id'], run['attempt_id'])) if json.loads(r['payload']) == attempt['result']]
                captures = self.sources._capture_events(db, run, files)
                if len(exits) != 1 or exits[0] <= boundary or any(e['seq'] <= exits[0] for e in captures):
                    raise ValueError('准确完成来源缺少保留后的唯一实际退出，或捕获早于退出。')
            source = self.sources._historical_evidence(db, run, self.s.work_artifacts.reference(current))
            if unit['role'] == 'coordinate' and files['snapshots']:
                raise ValueError('coordinate不能伪造文件产物。')
            frame = {'run': run, 'task_spec': spec, 'workspace': root, 'evidence': raw,
                     'source_evidence': source['full'], 'files': files}
            frames.append(frame); seen.add(unit['key'])
            if boundary < activated:
                before_activation.append(self.old.frontier_entry(frame))
        if before_activation != cap['snapshot']['activation_frontier']:
            raise ValueError('能力激活前沿不等于准确原V1历史。')
        return frames, revision, seen

    def _match_records(self, db, plan, frames, records):
        targets = [f for f in frames if f['run']['unit_key'] == plan['file_handoffs'][0]['target_unit']]
        deliveries = records[DELIVERY_KIND]; completions = records[COMPLETION_KIND]
        if bool(targets) != bool(deliveries) or (completions and not deliveries):
            raise ValueError('真实B与唯一交付/完成来源不一致。')
        if not targets: return
        frame = targets[0]; run = frame['run']; delivered, seq = deliveries[0]
        expected = versioned({'protocol': DELIVERY, 'item_id': plan['item_id'], 'root': plan_ref(plan),
            'marker': plan[MARKER_FIELD], 'intent': run['input']['handoff_delivery_intent'],
            'run_id': run['id'], 'run_version': digest(run), 'input_version': run['input_version'],
            'task_spec_version': run['task_spec_version'], 'materials': frame['task_spec']['materials']})
        if delivered['snapshot'] != expected or seq <= self.sources._reservation_event(db, run):
            raise ValueError('正式交付不等于真实B输入、spec与原意图。')
        if completions:
            completed, cseq = completions[0]
            expected = versioned({'protocol': COMPLETION, 'item_id': plan['item_id'], 'root': plan_ref(plan),
                'marker': plan[MARKER_FIELD], 'delivery': self.ref(delivered), 'run_id': run['id'],
                'run_version': digest(run), 'input_version': run['input_version'],
                'evidence': frame['source_evidence'], 'files_version': digest(frame['files'])})
            captures = self.sources._capture_events(db, run, frame['files'])
            if (completed['snapshot'] != expected or not captures or cseq <= captures[-1]['seq'] or
                    frame['source_evidence']['check_status'] == 'FAIL'):
                raise ValueError('正式完成不等于准确真实捕获的B来源。')

    def read(self, db, plan):
        try:
            return self._read(db, plan)
        except (KeyError, TypeError, AttributeError, LookupError, StopIteration) as exc:
            raise ValueError('完整阶段运行字段或来源无效。') from exc

    def target(self, db, item, plan, unit, pending=None):
        history = self.read(db, plan)
        self._admit(plan, unit, history['current_frames'], history['phase'], pending)
        return {'key': unit['key'], 'revision': digest(unit)}

    def input_for(self, db, item, plan, unit, rows):
        history = self.read(db, plan)
        if rows != self.s.work_units.rows(db, item['id']) or history['task'] != {'id': item['id'], 'revision': item['revision']}:
            raise ValueError('当前完整前沿或任务已变化。')
        auth, driver = history['authorization'], history['driver']
        if (not auth or not driver or driver['state'] != 'RESERVED' or
                len(rows) >= auth['max_attempts_total'] or time.time() >= auth['dispatch_before']):
            raise ValueError('准确原责任或累计授权不足。')
        self._admit(plan, unit, history['current_frames'], history['phase'])
        proposal = None
        if unit['role'] != 'coordinate':
            row = self.s.work_proposals.current(db, item['id'])
            if not row or any(r.get('proposal') == self.ref(row) for r in rows):
                raise ValueError('最新提案缺失或已消费。')
            proposal = {'reference': self.ref(row), **{k: row['snapshot'][k] for k in ('source', 'decision')}}
            self._proposal(db, plan, unit, history['current_frames'], proposal, history['task'], driver, auth)
        cap, _ = self.capability(db, plan)
        records = self._records(db, plan)
        frozen = self._input(plan, unit, history['task'], driver, auth, history['current_frames'],
                             history['material_observation'], records, cap, proposal)
        if unit['key'] == plan['file_handoffs'][0]['target_unit']:
            frozen['handoff_delivery_intent'] = self._intent(plan, frozen, history['current_frames'])
        return deepcopy(frozen)

    def recheck(self, db, plan, frozen):
        item = self.s.work_items.item(db, plan['item_id'])
        if self.input_for(db, item, plan, frozen['unit'], self.s.work_units.rows(db, item['id'])) != frozen:
            raise ValueError('准备后完整前沿或当前材料晚变。')

    def deliver(self, db, plan, run):
        intent = run['input'].get('handoff_delivery_intent')
        if not intent: return None
        snapshot = versioned({'protocol': DELIVERY, 'item_id': plan['item_id'], 'root': plan_ref(plan),
            'marker': plan[MARKER_FIELD], 'intent': intent, 'run_id': run['id'], 'run_version': digest(run),
            'input_version': run['input_version'], 'task_spec_version': run['task_spec_version'],
            'materials': self.s.store.task(run['task_id'], db)['spec']['materials']})
        row = self.records.put(db, DELIVERY_KIND, snapshot)
        self.s.store.event(run['task_id'], run['attempt_id'], 'coordinator_handoff_delivered', row, db)
        return row

    def reservation(self, db, plan, run):
        if (self.s.work_units.reservation(db, run['attempt_id']) != run or
                self.s.store.attempt(run['attempt_id'], db)['state'] != 'RESERVED' or run['command_id'] != self.s.command_identity):
            raise ValueError('最终核对只允许当前命令的准确持久RESERVED。')
        history = self.read(db, plan)
        if not history['frames'] or history['frames'][-1]['run'] != run:
            raise ValueError('当前保留不是最后完整前沿。')
        context = run['input'].get('handoff_unit_context') or run['input']['handoff_coordination_context']
        if (history['material_observation'] != context['material_observation'] or
                history['driver']['state'] != 'RESERVED' or history['authorization'] != context['authorization']):
            raise ValueError('绑定后原材料、原责任或累计授权晚变。')
        if time.time() >= history['authorization']['dispatch_before']:
            raise ValueError('最终绑定时原新增派发截止已到期。')
        if (run.get('proposal') and self.s.work_proposals.reference(
                self.s.work_proposals.current(db, plan['item_id'])) != run['proposal']):
            raise ValueError('绑定后准确消费提案指针晚变。')
        for dependency in run['input']['dependencies']:
            frame = next(f for f in history['current_frames'] if f['run']['id'] == dependency['run_id'])
            if dependency != {'unit_key': dependency['unit_key'], **frame['source_evidence']}:
                raise ValueError('绑定后准确依赖的当前材料晚变。')

    def guard_capture(self, db, plan, run):
        history = self.read(db, plan)
        relation = plan['file_handoffs'][0]
        if run['unit_key'] == relation['source_unit'] and history['delivery']:
            raise ValueError('A已受保护交付，不能再捕获。')
        if run['unit_key'] == relation['target_unit'] and history['completion']:
            raise ValueError('B已准确完成，首次原根不再捕获。')
        return history

    def complete(self, db, plan, run):
        if run['unit_key'] != plan['file_handoffs'][0]['target_unit']: return None
        files, current = self.sources._saved_files(db, run)
        evidence = self.s.work_units.evidence(db, run)
        if evidence['check_status'] == 'FAIL': return None
        self.s.work_artifacts.require(evidence, 'checked' if run['input']['unit']['checks'] else 'files')
        deliveries = self._records(db, plan)[DELIVERY_KIND]
        if len(deliveries) != 1: raise ValueError('B捕获缺少唯一交付。')
        snapshot = versioned({'protocol': COMPLETION, 'item_id': plan['item_id'], 'root': plan_ref(plan),
            'marker': plan[MARKER_FIELD], 'delivery': self.ref(deliveries[0][0]), 'run_id': run['id'],
            'run_version': digest(run), 'input_version': run['input_version'], 'evidence': evidence,
            'files_version': digest(files)})
        row = self.records.put(db, COMPLETION_KIND, snapshot)
        self.s.store.event(run['task_id'], run['attempt_id'], 'coordinator_handoff_completed', row, db)
        return row

    def finish_capture(self, db, plan, run):
        result = self.complete(db, plan, run)
        history = self.read(db, plan)
        frame = next(f for f in history['current_frames'] if f['run']['id'] == run['id'])
        if history['phase'] == 'BLOCKED' or frame['source_evidence']['file_artifacts']['state'] != 'CURRENT':
            raise ValueError('捕获最终事件后当前材料晚变。')
        return result
