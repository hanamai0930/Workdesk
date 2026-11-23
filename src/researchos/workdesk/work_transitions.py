"""Atomic dependency-plan changes and original-run result references. No dispatch."""
import json
import time

from .store import digest, uid


def plan_ref(plan):
    return {k: plan[k] for k in ('id', 'version')}


def source_ref(run, evidence):
    return {**{k: run[k] for k in ('plan_id', 'plan_version', 'unit_revision', 'input_version')},
            **{k: evidence[k] for k in ('run_id', 'task_id', 'attempt_id', 'owner', 'session_id', 'turn_id',
                                       'connection_fingerprint', 'result_digest', 'result_version', 'version')},
            'artifact': evidence['file_artifacts']['reference'], 'manifest': evidence['file_artifacts']['manifest'],
            'check_status': evidence['check_status']}


class WorkTransitions:
    def __init__(self, service):
        self.s = service

    def handlers(self):
        return {'work-item.proposal-apply': self.apply}

    def consumption(self, db, proposal):
        from .work_coordinator_handoff_adjustments import proposal_state
        if proposal_state(proposal['snapshot']):
            from .work_coordinator_handoff_adjustment_records import CONSUMPTION, KIND_FOR, validate_row
            matches = [r for r in self.s.collaboration.all(db, KIND_FOR[CONSUMPTION])
                       if r.get('snapshot', {}).get('proposal') == self.s.work_proposals.reference(proposal)]
            if len(matches) > 1:
                raise ValueError('同提案存在重复调整消费。')
            if matches:
                return validate_row(KIND_FOR[CONSUMPTION], matches[0])['snapshot']
        row = db.execute("SELECT data FROM collaboration WHERE kind='work_proposal_consumption' AND id=?",
                         ('proposal-consumption:' + proposal['id'],)).fetchone()
        return json.loads(row[0]) if row else None

    def validate_run(self, db, plan, unit, run, rows, trail, cache=None, *, consumer_plan=None, _read=None):
        self.s.work_file_handoff_applications.require_sources(db, plan, _read=_read)
        self.s.work_file_handoff_applications.require_local_identity(db, plan, run)
        cache = {} if cache is None else cache
        trail = trail | {(plan['id'], unit['key'])}
        if (digest({k: v for k, v in plan.items() if k != 'version'}) != plan['version'] or
                run['item_id'] != plan['item_id'] or run['plan_id'] != plan['id'] or
                run['plan_version'] != plan['version'] or run['unit_key'] != unit['key'] or
                run['unit_revision'] != digest(unit) or run['input']['unit'] != unit or
                run['input_version'] != digest(run['input'])):
            raise ValueError('原成果的方案、单元或冻结输入版本不一致')
        frozen = run['input']['dependencies']
        consumer_plan = consumer_plan or plan
        self.s.work_rework_applications.require_sources(db, plan, rows, consumer_plan=consumer_plan)
        self.s.work_unit_revision_applications.require_sources(db, plan, rows, consumer_plan=consumer_plan)
        context = self.s.work_file_rework_applications.input_context(db, plan, unit, rows)
        if run['input'].get('file_rework_context') != context:
            raise ValueError('原成果的冻结文件返工来源与原方案不一致')
        if run['input'].get('write_before_baseline'):
            self.s.work_file_rework_applications.require_baseline(run['input']['write_before_baseline'])
        rework = self.s.work_rework_applications.input_context(plan, unit)
        if run['input'].get('rework_context') != rework:
            raise ValueError('原成果的冻结返工来源与原方案不一致')
        if run['input'].get('unit_revision_context') != self.s.work_unit_revision_applications.input_context(plan, unit):
            raise ValueError('原成果的冻结局部修订来源与原方案不一致')
        if len(frozen) != len(unit['depends']) or {d['unit_key'] for d in frozen} != set(unit['depends']):
            raise ValueError('原成果的冻结前置集合不一致')
        for dependency in frozen:
            historical = self.s.work_file_handoff_ledger.dependency_history(db, run, dependency, rows)
            if historical:
                self.s.work_artifacts.require(historical['evidence'], unit.get('dependency_gate', 'result'))
                continue
            resolved = self.resolve(db, plan, dependency['unit_key'], rows, trail=trail, cache=cache,
                                    consumer_plan=consumer_plan, _read=_read)
            evidence = resolved['evidence']
            if resolved['problems'] or not evidence or evidence['version'] != dependency['version']:
                raise ValueError('原成果的冻结前置结果或文件证据已变化：' + dependency['unit_key'])
            self.s.work_artifacts.require(evidence, unit.get('dependency_gate', 'result'))

    def resolve(self, db, plan, key, rows, *, trail=frozenset(), cache=None, consumer_plan=None, _read=None):
        """Return the effective original run; an invalid carry is never NOT_RUN."""
        if plan.get('coordinator_file_handoff_round_lineage'):
            from .work_coordinator_handoff_runtime import reader
            return reader(self.s, db, plan).resolve(db, plan, key, rows)
        if plan.get('coordinator_file_handoff_lineage'):
            from .work_coordinator_handoff_adjustment_path import WorkCoordinatorHandoffAdjustmentPath
            return WorkCoordinatorHandoffAdjustmentPath(self.s).resolve(db,plan,key,rows)
        if _read is None and plan.get('file_handoff_lineage'):
            _read = self.s.work_file_handoff_applications.read_projection(db, plan)
        marker = (plan['id'], key)
        consumer_plan = consumer_plan or plan
        if marker in trail:
            raise ValueError('成果沿用引用形成循环')
        cache = {} if cache is None else cache
        if marker in cache and not plan.get('file_handoff_lineage'):
            return cache[marker]
        trail = trail | {marker}
        unit = next((u for u in plan['units'] if u['key'] == key), None)
        if not unit:
            raise ValueError('成果引用单元不存在')
        handoff_proof, handoff_problems = None, []
        if plan.get('file_handoff_lineage'):
            try:
                handoff_proof = self.s.work_file_handoff_applications.require_sources(db, plan, _read=_read)
            except ValueError as exc:
                handoff_problems.append(str(exc))
            retired = plan['file_handoffs'][0]['source_unit']
            if key == retired:
                source = (handoff_proof or plan['file_handoff_lineage']['proof'])['historical_source']
                run = next((r for r in rows if r['id'] == source['run_id']), None)
                evidence = self._read_evidence(db, run, _read) if run else None
                if evidence:
                    evidence = {**evidence, 'state': 'HISTORICAL_RETIRED', 'result_available': False,
                        'file_artifacts': {**evidence['file_artifacts'], 'state': 'HISTORICAL_RETIRED'}}
                    evidence['version'] = digest(evidence)
                return {'run': run, 'evidence': evidence, 'inherited': True, 'source': None,
                    'consumer_plan': plan_ref(plan), 'state': 'HISTORICAL_RETIRED',
                    'problems': [*handoff_problems, '历史前版已交付并退役，不能重新派发、捕获或作为新输入。']}
        local = [r for r in rows if r['plan_id'] == plan['id'] and r['unit_key'] == key]
        entry = plan.get('result_lineage', {}).get(key)
        run = local[-1] if local else next((r for r in rows if entry and r['id'] == entry['run_id']), None)
        inherited = bool(entry and not local)
        problems = handoff_problems
        try:
            if run:
                self.s.work_file_handoff_applications.require_local_identity(db, plan, run)
            evidence = self._read_evidence(db, run, _read) if run else None
        except ValueError as exc:
            evidence = self._invalid_evidence(db, run, str(exc))
            problems.append(str(exc))
        # Existing within-plan readers may inspect an outdated result to propose
        # an adjustment. A carried result and its entire original dependency
        # chain must pass the stronger frozen-input check on every use.
        if local and len(trail) > 1:
            try:
                self.validate_run(db, plan, unit, run, rows, trail, cache, consumer_plan=consumer_plan, _read=_read)
            except ValueError as exc:
                problems.append(str(exc))
        if inherited:
            try:
                if not run or unit['role'] == 'coordinate':
                    raise ValueError('沿用原运行不存在或为历史协调安排')
                original = self.s.collaboration.get(db, 'teamwork_plan', run['plan_id'])
                old_unit = next(u for u in original['units'] if u['key'] == key)
                if unit != old_unit or source_ref(run, evidence) != entry:
                    raise ValueError('沿用原成果、定义或文件检查版本已变化')
                history = [r for r in rows if r['plan_id'] == original['id'] and r['unit_key'] == key]
                if not history or history[-1]['id'] != run['id']:
                    raise ValueError('沿用来源不再是原方案的最新准确运行')
                gate = 'checked' if unit.get('checks') else 'files' if unit.get('outputs') else 'result'
                self.s.work_artifacts.require(evidence, gate)
                if _read is None or not handoff_proof:
                    self.validate_run(db, original, old_unit, run, rows, trail, cache, consumer_plan=consumer_plan)
            except (ValueError, StopIteration) as exc:
                problems.append(str(exc) or '沿用原单元不存在')
        if evidence and problems:
            evidence = {**evidence, 'result_available': False,
                        'version': digest({'source_version': evidence['version'], 'lineage_problems': problems})}
        result = {'run': run, 'evidence': evidence, 'inherited': inherited, 'problems': problems,
                  'source': entry if inherited else None, 'consumer_plan': plan_ref(plan)}
        cache[marker] = result
        return result

    def _read_evidence(self, db, run, observation):
        if observation is not None and run['id'] in observation.evidence:
            from copy import deepcopy
            observation.require(self.s.work_file_handoff_applications, db, observation.plan)
            return deepcopy(observation.evidence[run['id']])
        if observation is not None:
            try:
                self.s.work_file_handoff_applications.require_local_identity(db, observation.plan, run)
            except ValueError as exc:
                return self._invalid_evidence(db, run, str(exc))
        return self.s.work_units.evidence(db, run)

    def _invalid_evidence(self, db, run, problem):
        evidence = {**self.s.work_units.base_evidence(db, run), 'result_available': False,
                    'check_status': 'NOT_RECORDED', 'file_artifacts': {
                        'state': 'UNAVAILABLE', 'reference': None, 'manifest': {}, 'problems': [problem]}}
        evidence['version'] = digest(evidence)
        return evidence

    def apply(self, db, p):
        if set(p) != {'id', 'revision', 'current_driver', 'plan', 'authorization', 'frontier', 'proposal'}:
            raise ValueError('应用调整只接受当前准确任务、责任、方案、授权、前沿和提案引用。')
        item, driver, plan = self.s.work_units.checked(db, p)
        self.s.work_flows.require_stopped(db, item['id'])
        proposals = self.s.work_proposals
        row = proposals.current(db, item['id'])
        if (not row or p['proposal'] != proposals.reference(row) or self.consumption(db, row) or
                row['snapshot']['state'] != 'ADJUSTMENT_READY'):
            raise ValueError('须使用最新、未消费的有效依赖调整预览。')
        issues = proposals.live_issues(db, item, row)
        if issues:
            raise ValueError('调整预览已失效：' + '；'.join(issues))
        auth = self.s.work_units.authorization(db, driver)
        if (not auth or p['authorization'] != self.s.work_units.auth_ref(auth) or
                auth['driver_generation'] != driver['generation'] or auth['plan_id'] != plan['id'] or
                auth['plan_version'] != plan['version'] or p['frontier'] != self.s.work_units.frontier(db, item['id'])):
            raise ValueError('调整授权归属或准确执行前沿已变化。')
        if plan.get('coordinator_file_handoff_root'):
            from .work_coordinator_handoff_adjustment_apply import WorkCoordinatorHandoffAdjustmentApplications
            from .work_coordinator_handoff_adjustment_candidates import WorkCoordinatorHandoffAdjustmentCandidates
            candidates = WorkCoordinatorHandoffAdjustmentCandidates(self.s)
            ready = candidates.prepare(db, proposals.reference(row))
            edge = WorkCoordinatorHandoffAdjustmentApplications(self.s).apply(db, candidates.records.ref(ready['candidate']))
            return {'transition': edge['consumption']['snapshot'], 'plan': edge['application']['snapshot']['child_plan'],
                    'driver': self.s.work_drivers.reference(edge['driver']),
                    'authorization': self.s.work_units.auth_ref(edge['authorization']),
                    'application': candidates.records.ref(edge['application']),
                    'no_dispatch': True, 'automatically_resumed': False}
        auth = self.s.work_units.authorization(db, driver)
        if (not auth or p['authorization'] != self.s.work_units.auth_ref(auth) or
                auth['driver_generation'] != driver['generation'] or auth['plan_id'] != plan['id'] or
                auth['plan_version'] != plan['version'] or p['frontier'] != self.s.work_units.frontier(db, item['id'])):
            raise ValueError('调整授权归属或准确执行前沿已变化。')
        rows = self.s.work_units.rows(db, item['id'])
        for unit in plan['units']:
            resolved = self.resolve(db, plan, unit['key'], rows)
            e = resolved['evidence']
            if resolved['problems']:
                raise ValueError('沿用来源已失效：' + '；'.join(resolved['problems']))
            if e and unit.get('outputs'):
                self.s.work_artifacts.require(e, 'checked' if unit.get('checks') else 'files')
        # live_issues recomputes the candidate, impact, frozen source and usage.
        adjustment = row['snapshot']['adjustment']
        transition = {'proposal': proposals.reference(row), 'source': row['snapshot']['source'],
                      'impact': adjustment['units'], 'usage_at_transition': adjustment['usage'],
                      'authorization_from': self.s.work_units.auth_ref(auth),
                      'command_id': self.s.command_identity, 'no_dispatch': True}
        new, updated, transferred = self.save_plan(db, item, driver, plan, auth,
            adjustment['candidate_units'],
            {u['key']: u['source'] for u in adjustment['units'] if u['state'] == 'ELIGIBLE_RESULT'}, transition)
        consumption = {'id': 'proposal-consumption:' + row['id'], 'proposal': proposals.reference(row),
                       'item_id': item['id'], 'kind': 'PLAN_TRANSITION', 'from_plan': plan_ref(plan),
                       'to_plan': plan_ref(new), 'driver': self.s.work_drivers.reference(updated),
                       'authorization': self.s.work_units.auth_ref(transferred), 'created': new['created'],
                       'command_id': self.s.command_identity, 'no_dispatch': True}
        self.s.collaboration.put(db, 'work_proposal_consumption', consumption)
        self.s.store.event(None, None, 'cooperation_plan_adjusted', consumption, db)
        return {'transition': consumption, 'plan': plan_ref(new), 'driver': self.s.work_drivers.reference(updated),
                'authorization': self.s.work_units.auth_ref(transferred), 'no_dispatch': True,
                'automatically_resumed': False}

    def save_plan(self, db, item, driver, plan, auth, units, lineage, transition, *, extra=None,
                  reason='PLAN_ADJUSTED_EXPLICIT_RECHECK_REQUIRED', handoff_candidate=None):
        """Called only after command-specific checks, within the Service transaction."""
        if handoff_candidate is not None:
            from .work_coordinator_handoff_text_rework_contracts import CANDIDATE as TEXT_CANDIDATE, KINDS as TEXT_KINDS
            text_row = db.execute('SELECT kind FROM collaboration WHERE id=?', (handoff_candidate.get('id'),)).fetchone()
            if text_row and text_row['kind'] == TEXT_KINDS[TEXT_CANDIDATE]:
                from .work_coordinator_handoff_text_rework_apply import save_plan
                return save_plan(self.s, db, handoff_candidate, item, driver, plan, auth, units, lineage,
                    transition, extra, reason)
            # Independent coordinator candidates must be dispatched before the
            # older fixed candidate reader, which intentionally rejects them.
            from .work_coordinator_handoff_adjustment_records import CANDIDATE, KIND_FOR
            row = db.execute('SELECT kind FROM collaboration WHERE id=?', (handoff_candidate.get('id'),)).fetchone()
            if row and row['kind'] == KIND_FOR[CANDIDATE]:
                from .work_coordinator_handoff_adjustment_apply import WorkCoordinatorHandoffAdjustmentApplications
                return WorkCoordinatorHandoffAdjustmentApplications(self.s).save(db, handoff_candidate,
                    item, driver, plan, auth, units, lineage, transition, extra, reason)
        if plan.get('file_handoffs') or (extra or {}).get('file_handoffs') or (extra or {}).get('file_handoff_lineage'):
            if handoff_candidate is None:
                raise ValueError('同路径交接方案的跨修订来源尚未开放，不能复制或更改关系。')
            from .work_file_handoff_apply import WorkFileHandoffRevisionApplications, CANDIDATE as CANDIDATE1
            from .work_file_handoff_chain_apply import WorkFileHandoffChainApplications
            from .work_file_handoff_chain import CANDIDATE as CANDIDATE2
            from .work_file_handoff_text_rework_apply import WorkFileHandoffTextReworkApplications, PAUSE_REASON
            from .work_file_handoff_text_reworks import CANDIDATE as REWORK_CANDIDATE
            from .work_file_handoff_unit_revision_apply import WorkFileHandoffUnitRevisionApplications
            from .work_file_handoff_unit_revisions import CANDIDATE as UNIT_CANDIDATE, PAUSE_REASON as UNIT_PAUSE
            from .work_file_handoff_file_rework_apply import WorkFileHandoffFileReworkApplications, PAUSE_REASON as FILE_PAUSE
            from .work_file_handoff_file_reworks import CANDIDATE as FILE_CANDIDATE
            row = self.s.work_file_handoff_ledger.get(db, 'work_file_handoff_revision_candidate', handoff_candidate)
            protocols = {CANDIDATE1: (WorkFileHandoffRevisionApplications, 'FILE_HANDOFF_REVISED_EXPLICIT_RECHECK_REQUIRED'),
                CANDIDATE2: (WorkFileHandoffChainApplications, 'FILE_HANDOFF_REVISED_EXPLICIT_RECHECK_REQUIRED'),
                REWORK_CANDIDATE: (WorkFileHandoffTextReworkApplications, PAUSE_REASON),
                UNIT_CANDIDATE: (WorkFileHandoffUnitRevisionApplications, UNIT_PAUSE),
                FILE_CANDIDATE: (WorkFileHandoffFileReworkApplications, FILE_PAUSE)}
            protocol = row['snapshot'].get('protocol')
            if protocol not in protocols:
                raise ValueError('交接保存候选协议不受支持。')
            application_type, expected_reason = protocols[protocol]
            applications = application_type(self.s)
            applications.require_save(db, handoff_candidate,
                item, driver, plan, auth, units, lineage, transition, extra)
            if reason != expected_reason:
                raise ValueError('交接修订须按准确原因暂停责任。')
        elif handoff_candidate is not None:
            raise ValueError('普通方案不能使用交接修订证明。')
        now = time.time()
        new = {**plan, 'id': 'teamwork:' + uid(), 'item_revision': item['revision'],
               'units': units, 'previous_plan': plan['id'], 'previous_plan_reference': plan_ref(plan),
               'created': now, 'state': 'PREPARED', 'result_lineage': lineage, 'transition': transition}
        # A later dependency adjustment can deliberately reopen historical coordinator keys.
        for key in ('coordination_revision', 'historical_coordinators', 'diagnostic_revision', 'rework_lineage',
                    'unit_revision_lineage', 'file_rework_lineage'):
            new.pop(key, None)
        new.update(extra or {})
        new.pop('version', None)
        new['version'] = digest(new)
        self.s.collaboration.put(db, 'teamwork_plan', new)
        self.s.collaboration.put(db, 'teamwork_current', {'id': 'teamwork-current:' + item['id'], 'plan_id': new['id']})
        transferred = {**auth, 'revision': uid(), 'plan_id': new['id'], 'plan_version': new['version'],
                       'previous_authorization': self.s.work_units.auth_ref(auth), 'transferred_at': now,
                       'transfer_proposal': transition.get('proposal'), 'usage_at_transfer': transition['usage_at_transition'],
                       'command_id': self.s.command_identity}
        self.s.collaboration.put(db, 'work_unit_authorization', transferred)
        self.s.collaboration.put(db, 'work_unit_authorization_revision',
                                 {**transferred, 'id': 'unit-auth-version:' + transferred['revision']})
        updated = self.s.work_drivers.save(db, {**driver, 'plan_id': new['id'], 'plan_version': new['version'],
            'state': 'PAUSED', 'reason': reason,
            'previous_plan_reference': plan_ref(plan), 'transition_proposal': transition.get('proposal')}, 'plan_adjusted')
        from .work_leader import organization
        if organization(self.s, db, plan):
            self.s.leader_revisions.adopt(db, plan, new, transition)
        return new, updated, transferred
