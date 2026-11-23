"""Explicit diagnostic coordinator revisions; failures remain original evidence."""
import time

from .contracts import bounded_text
from .store import digest, uid
from .work_adjustments import prior_frontier
from .work_reworks import context, diagnostics, TERMINAL
from .work_transitions import plan_ref, source_ref


PROTOCOL = 'TEXT_REWORK_DIAGNOSTIC_REVISION_V1'
FILE_PROTOCOL = 'FILE_REWORK_DIAGNOSTIC_REVISION_V1'


def diagnostic_unit(plan, unit):
    record = plan.get('diagnostic_revision')
    return bool(record and record['protocol'] in {PROTOCOL, FILE_PROTOCOL} and unit['role'] == 'coordinate'
                and record['unit_key'] == unit['key'])


def file_diagnostic_unit(plan, unit):
    return diagnostic_unit(plan, unit) and plan['diagnostic_revision']['protocol'] == FILE_PROTOCOL


class WorkReworkDiagnostics:
    protocol = PROTOCOL
    file_scope = False
    label = '文字'
    command = 'work-item.rework-diagnose'
    record_kind = 'work_rework_diagnostic_revision'
    event = 'text_diagnostic_round_registered'
    def __init__(self, service):
        self.s = service

    def handlers(self):
        return {self.command: self.revise}

    def protection(self, db, rows):
        return {}

    def candidate(self, db, item, driver, plan, rows):
        if not plan or plan['mode'] == 'fixed':
            raise ValueError('只有负责人／主持人模式可登记' + self.label + '诊断回合。')
        self.s.work_units.checked(db, {'id': item['id'], 'revision': item['revision'],
            'current_driver': self.s.work_drivers.reference(driver), 'plan': plan_ref(plan)})
        self.s.work_flows.require_stopped(db, item['id'])
        auth = self.s.work_units.authorization(db, driver)
        if (not auth or auth['plan_id'] != plan['id'] or auth['plan_version'] != plan['version'] or
                auth['driver_generation'] != driver['generation']):
            raise ValueError(self.label + '诊断须使用当前方案和责任的准确授权。')
        if len(rows) >= auth['max_attempts_total'] or time.time() >= auth['dispatch_before']:
            raise ValueError('原累计次数或派发截止已耗尽，不能登记诊断回合。')
        for run in rows:
            raw = self.s.work_units.evidence(db, run)
            original = self.s.collaboration.get(db, 'teamwork_plan', run['plan_id'])
            unit = next((u for u in original['units'] if u['key'] == run['unit_key']), None)
            if (not raw['exact_identity'] or raw['blockers'] or raw['state'] not in TERMINAL or not unit or
                    digest({k: v for k, v in original.items() if k != 'version'}) != original['version'] or
                    run['plan_version'] != original['version'] or run['input']['unit'] != unit or
                    run['unit_revision'] != digest(unit)):
                raise ValueError('原运行身份、终态或退出证据不足；未知不登记新回合。')
        observed = diagnostics(self.s, db, plan, rows)
        affected = {d['key'] for d in observed if d['source'] and
                    (not d['result_available'] or d['lineage_problems'] or d['frozen_input_problems'])}
        for unit in plan['units']:
            d = next((d for d in observed if d['key'] == unit['key']), None)
            if d and d['source'] and unit['outputs']:
                run = next(r for r in rows if r['id'] == d['source']['run_id'])
                try:
                    self.s.work_artifacts.require(self.s.work_units.evidence(db, run),
                                                  'checked' if unit['checks'] else 'files')
                except ValueError:
                    affected.add(unit['key'])
        if not affected:
            raise ValueError('尚无需要' + self.label + '返工诊断的失败或失效普通来源。')
        while True:
            more = {u['key'] for u in plan['units'] if set(u['depends']) & affected} - affected
            if not more:
                break
            affected.update(more)
        affected_files = [unit for unit in plan['units'] if unit['key'] in affected and unit['role'] != 'coordinate' and (
                    unit['outputs'] or unit['checks'] or unit['write_paths'] or unit['dependency_gate'] != 'result')]
        if self.file_scope and not affected_files:
            raise ValueError('纯文字不足请使用文字诊断入口；文件诊断须涉及原文件或写入工作。')
        if not self.file_scope:
            for unit in affected_files:
                raise ValueError('受影响的文件或写入工作须另行处理：' + unit['key'])
        if any(not d['exact_identity'] for d in observed):
            raise ValueError('普通诊断来源身份不一致。')
        coordinators = [u for u in plan['units'] if u['role'] == 'coordinate']
        sources = [r for r in rows if r['plan_id'] == plan['id'] and r['input']['unit']['role'] == 'coordinate']
        chosen = next((u for u in coordinators if sources and u['key'] == sources[-1]['unit_key']), coordinators[0])
        snapshot = {'protocol': self.protocol, 'base_plan': plan_ref(plan), 'unit_key': chosen['key'],
                    'diagnostics': observed, 'prior_frontier': prior_frontier(self.s, db, rows),
                    'affected_units': [u['key'] for u in plan['units'] if u['key'] in affected],
                    'source': source_ref(sources[-1], self.s.work_units.evidence(db, sources[-1])) if sources else None,
                    **self.protection(db, rows)}
        return snapshot, auth

    def view(self, db, item, driver, plan, rows):
        record = plan.get('diagnostic_revision') if plan else None
        if record and record['protocol'] != self.protocol:
            record = None
        try:
            snapshot, _ = self.candidate(db, item, driver, plan, rows)
            return {'can_register': True, 'snapshot': snapshot, 'version': digest(snapshot),
                    'record': record, 'problems': []}
        except ValueError as exc:
            return {'can_register': False, 'snapshot': None, 'version': None,
                    'record': record, 'problems': [str(exc)]}

    def revise(self, db, p):
        if set(p) != {'id', 'revision', 'current_driver', 'plan', 'authorization', 'frontier',
                      'diagnostic_version', 'reason'}:
            raise ValueError('诊断登记只接受准确任务、责任、方案、授权、前沿、诊断版本和依据。')
        reason = bounded_text(p['reason'], '诊断回合依据', 2000)
        item, driver, plan = self.s.work_units.checked(db, p)
        rows = self.s.work_units.rows(db, item['id'])
        snapshot, auth = self.candidate(db, item, driver, plan, rows)
        if (p['authorization'] != self.s.work_units.auth_ref(auth) or
                p['frontier'] != self.s.work_units.frontier(db, item['id']) or
                p['diagnostic_version'] != digest(snapshot)):
            raise ValueError('诊断登记的授权、准确前沿或诊断来源版本已变化。')
        record = {**snapshot, 'id': 'rework-diagnostic:' + uid(), 'reason': reason,
                  'command_id': self.s.command_identity, 'no_dispatch': True}
        lineage = {d['key']: d['source'] for d in snapshot['diagnostics'] if d['source']}
        history = set(plan.get('historical_coordinators', [])) | {
            r['unit_key'] for r in rows if r['plan_id'] == plan['id'] and r['input']['unit']['role'] == 'coordinate'}
        history.discard(snapshot['unit_key'])
        transition = {'kind': 'EXPLICIT_FILE_DIAGNOSTIC_REVISION' if self.file_scope else 'EXPLICIT_TEXT_DIAGNOSTIC_REVISION', 'source': snapshot['source'],
                      'diagnostic_revision': record['id'], 'reason': reason,
                      'usage_at_transition': self.s.work_units.usage(db, rows),
                      'authorization_from': self.s.work_units.auth_ref(auth),
                      'command_id': self.s.command_identity, 'no_dispatch': True}
        new, updated, transferred = self.s.work_transitions.save_plan(db, item, driver, plan, auth,
            plan['units'], lineage, transition,
            extra={'diagnostic_revision': record, 'historical_coordinators': sorted(history)},
            reason='FILE_DIAGNOSTIC_EXPLICIT_RECHECK_REQUIRED' if self.file_scope else 'TEXT_DIAGNOSTIC_EXPLICIT_RECHECK_REQUIRED')
        saved = {**record, 'to_plan': plan_ref(new), 'driver': self.s.work_drivers.reference(updated),
                 'authorization': self.s.work_units.auth_ref(transferred), 'created': new['created']}
        self.s.collaboration.put(db, self.record_kind, saved)
        self.s.store.event(None, None, self.event, saved, db)
        return {'diagnostic_revision': saved, 'plan': plan_ref(new),
                'driver': self.s.work_drivers.reference(updated), 'authorization': self.s.work_units.auth_ref(transferred),
                'no_dispatch': True, 'automatically_resumed': False}

    def require_frontier(self, db, plan, unit, rows):
        record = plan['diagnostic_revision']
        originals = [r for r in rows if not (r['plan_id'] == plan['id'] and r['unit_key'] == unit['key'])]
        if record['prior_frontier'] != prior_frontier(self.s, db, originals):
            raise ValueError('登记时的原诊断执行/文件前沿已变化，须重新明确登记。')
        for name, value in self.protection(db, originals).items():
            if record.get(name) != value:
                raise ValueError('登记时的原文件运行、输入或全部旧快照已变化，须重新明确登记。')

    def validate_source(self, db, plan, unit, rows, run):
        if not diagnostic_unit(plan, unit) or plan['diagnostic_revision']['protocol'] != self.protocol:
            return
        value = run['input']
        frozen = value.get('coordination_context', {})
        if (value['dependencies'] or value['file_artifacts'] or value['check_evidence'] or
                frozen.get('diagnostic_round') != plan['diagnostic_revision'] or
                frozen.get('rework_context') != context(self.s, db, plan, rows) or
                frozen.get('adjustment_prior_frontier') != prior_frontier(self.s, db, [r for r in rows if r['id'] != run['id']])):
            raise ValueError('诊断回合的准确输入或原前沿已变化；旧回复不能重绑新来源。')
        self.require_frontier(db, plan, unit, rows)
