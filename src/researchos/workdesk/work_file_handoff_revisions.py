"""Internal fixed-mode local candidates; no public command, save or execution grant.

The original completed handoff is audited independently of ordinary current
evidence: its retired producer must never be diagnosed as work to re-execute.
"""
from copy import deepcopy
import time

from .contracts import bounded_text
from .plans import PlanCommands
from .store import digest
from .work_file_handoff_ancestry import WorkFileHandoffAncestry
from .work_transitions import plan_ref, source_ref
from .work_unit_revisions import allowed_members, changes


PROTOCOL = 'FIXED_FILE_HANDOFF_REVISION_CANDIDATE_V1'
FIELDS = {'id', 'revision', 'current_driver', 'plan', 'authorization', 'frontier', 'changes', 'reason'}


class WorkFileHandoffRevisions:
    protocol = PROTOCOL

    def __init__(self, service):
        self.s = service

    def observation(self, db, plan):
        return None

    def audit(self, db, plan, candidate, affected, observation):
        return WorkFileHandoffAncestry(self.s).audit(db, plan, candidate, sorted(affected))

    def result(self, db, plan, key, rows, evidence, observation):
        local = [r for r in rows if r['unit_key'] == key]
        run = local[-1] if local else None
        return run, evidence[run['id']] if run else None

    def preview(self, db, request):
        # A fresh observation is required even under an outer projection cache.
        with self.s.work_unit_revision_applications.validation_snapshot(db):
            return self._preview(db, request)

    def _preview(self, db, p):
        if not isinstance(p, dict) or set(p) != FIELDS:
            raise ValueError('固定交接候选只接受准确任务、责任、方案、授权、前沿、局部改动和依据。')
        item, driver, plan = self.s.work_units.checked(db, p)
        self.s.work_flows.require_stopped(db, item['id'])
        if plan['mode'] != 'fixed' or not plan.get('file_handoffs'):
            raise ValueError('候选入口仅接受原固定流程的明确文件交接。')
        observation = self.observation(db, plan)
        rows = self.s.work_units.rows(db, item['id'])
        auth = self.s.work_units.authorization(db, driver)
        if (not auth or p['authorization'] != self.s.work_units.auth_ref(auth) or
                auth['item_id'] != item['id'] or auth['driver_generation'] != driver['generation'] or
                auth['plan_id'] != plan['id'] or auth['plan_version'] != plan['version'] or
                p['frontier'] != self.s.work_units.frontier(db, item['id'], _read=observation)):
            raise ValueError('固定交接候选的准确授权归属或完整前沿已变化。')
        reason = bounded_text(p['reason'], '明确局部修订依据', 2000)
        changes(p['changes'])
        original = {u['key']: u for u in plan['units']}
        members = {digest(m['reference']): m for m in
                   [plan['coordinator'], plan['integrator'], *(u['member'] for u in plan['units'])]}
        replacements, bases = {}, {}
        for change in p['changes']:
            name, basis, proposed = change['unit_key'], change['basis'], change['definition']
            source = original.get(name)
            if (change['operation'] != 'revise' or not source or source['role'] == 'coordinate' or
                    basis['unit_key'] != name or basis['unit_revision'] != digest(source) or
                    basis['work_requirements_version'] != digest(plan['work_requirements'])):
                raise ValueError('首批固定候选仅细化准确原普通单元，须引用其原职责和任务要求版本。')
            if (proposed['member'] not in allowed_members(plan) or
                    proposed['member'] != source['member']['reference']):
                raise ValueError('固定候选须保留原方案准确成员，并保持原单元成员、连接和额度来源。')
            # Reuse the original complete selection; do not open a second reader
            # while the caller holds its read/write transaction. Audit checks it live.
            unit = {**source, 'member': members[digest(proposed['member'])],
                    'depends': sorted(proposed['depends']),
                    **{k: bounded_text(proposed[k], '局部' + k, limit)
                       for k, limit in [('title', 160), ('goal', 4000), ('acceptance', 4000)]}}
            if {**source, 'depends': sorted(source['depends'])} == unit:
                raise ValueError('固定候选须实际改变允许的局部定义。')
            replacements[name], bases[name] = unit, basis
        candidate = [replacements.get(u['key'], u) for u in plan['units']]
        PlanCommands.validate_graph({u['key']: u['depends'] for u in candidate})
        by_key = {u['key']: u for u in candidate}
        for unit in candidate:
            for key in unit['depends']:
                if unit['dependency_gate'] in {'files', 'checked'} and not by_key[key]['outputs']:
                    raise ValueError('候选文件依赖须保留前置产物。')
                if unit['dependency_gate'] == 'checked' and not by_key[key]['checks']:
                    raise ValueError('候选检查依赖须保留前置检查。')
        affected = set(replacements)
        while True:
            more = {u['key'] for u in candidate if
                    set(u['depends'] + original[u['key']]['depends']) & affected} - affected
            if not more:
                break
            affected.update(more)
        audit = self.audit(db, plan, candidate, affected, observation)
        if audit['state'] != 'COMPLETED_VALID' or not audit['carry_candidate']:
            raise ValueError('首批固定候选须有已正式完成且当前仍有效的后继文件成果。')
        retired, completed = (audit['relation'][k] for k in ('source_unit', 'target_unit'))
        # The projection must use the evidence actually audited, rather than
        # silently substituting a newer, unchecked observation during assembly.
        evidence_by_run = {entry['run_id']: entry['evidence'] for entry in audit['frontier']}
        target_run = next(r for r in rows if r['unit_key'] == completed)
        if source_ref(target_run, evidence_by_run[target_run['id']]) != audit['completed_target']['source']:
            raise ValueError('沿袭核对内部前沿与准确后继来源不一致。')
        for name in affected:
            unit = by_key[name]
            if unit['outputs'] or unit['checks'] or unit['write_paths'] or unit['dependency_gate'] != 'result':
                raise ValueError('首批固定候选只允许闭包外的文字工作受影响：' + name)
            if retired in unit['depends']:
                raise ValueError('受影响工作不能新增或沿用退役前版作为新输入：' + name)
        entries = []
        for unit in candidate:
            name = unit['key']
            run, evidence = self.result(db, plan, name, rows, evidence_by_run, observation)
            state = ('HISTORICAL_RETIRED' if name == retired else 'AFFECTED' if name in affected
                     else 'ELIGIBLE_RESULT' if run else 'NOT_RUN')
            entries.append({'key': name, 'state': state, 'basis': bases.get(name),
                            'original_definition': original[name], 'candidate_definition': unit,
                            'old_depends': original[name]['depends'], 'candidate_depends': unit['depends'],
                            'source': source_ref(run, evidence) if run and state == 'ELIGIBLE_RESULT' else None,
                            'historical_source': audit['historical_source'] if name == retired else None,
                            'new_input_blockers': ['RETIRED_DEPENDENCY'] if retired in unit['depends'] else [],
                            'reasons': ['DEFINITION_CHANGED'] if name in replacements else
                                       ['AFFECTED_DEPENDENCY'] if name in affected else []})
        blockers = []
        if len(rows) >= auth['max_attempts_total']:
            blockers.append('原累计授权次数不足；候选不增加授权')
        if time.time() >= auth['dispatch_before']:
            blockers.append('原新增派发截止已到期；候选不延长截止')
        body = {'protocol': self.protocol, 'request': p, 'base_plan': plan_ref(plan),
                'item_revision': item['revision'], 'driver': self.s.work_drivers.reference(driver),
                'authorization': auth, 'frontier': p['frontier'], 'reason': reason,
                'candidate_units': candidate, 'units': entries, 'ancestry_audit': audit,
                'direct_changes': [u['key'] for u in candidate if u['key'] in replacements],
                'affected_units': [u['key'] for u in candidate if u['key'] in affected],
                'eligible_results': [e['key'] for e in entries if e['state'] == 'ELIGIBLE_RESULT'],
                'completed_target': completed, 'retired_source': retired,
                'usage': self.s.work_units.usage(db, rows), 'execution_blockers': blockers,
                'save_permission': False, 'current_input_permission': False, 'execution_permission': False,
                'scope': 'INTERNAL_EXPLICIT_FIXED_CANDIDATE; ORIGINAL_DUTY_AND_RESOURCES; NO_SAVE_OR_DISPATCH',
                'independent_review': 'NOT_PERFORMED', 'human_acceptance': 'NOT_RECORDED'}
        return deepcopy({**body, 'version': digest(body)})

    def recheck(self, db, expected):
        if (not isinstance(expected, dict) or expected.get('protocol') != self.protocol or
                expected.get('version') != digest({k: v for k, v in expected.items() if k != 'version'})):
            raise ValueError('固定交接候选完整版本不一致。')
        current = self.preview(db, expected['request'])
        if current != expected:
            raise ValueError('固定交接候选的来源、资源、授权、期限或完整前沿已变化。')
        return current
