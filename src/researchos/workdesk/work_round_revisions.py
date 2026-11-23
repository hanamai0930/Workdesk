"""Explicit next coordinator execution revision, retaining exact ordinary results."""
import time

from .contracts import bounded_text
from .store import digest, uid
from .work_transitions import plan_ref, source_ref


class WorkRoundRevisions:
    def __init__(self, service):
        self.s = service

    def handlers(self):
        return {'work-item.coordination-revise': self.revise}

    def candidate(self, db, item, driver, plan, rows):
        if not plan or plan['mode'] == 'fixed':
            raise ValueError('只有负责人／主持人模式可登记下一提案回合。')
        self.s.work_units.checked(db, {'id': item['id'], 'revision': item['revision'],
            'current_driver': self.s.work_drivers.reference(driver), 'plan': plan_ref(plan)})
        self.s.work_flows.require_stopped(db, item['id'])
        auth = self.s.work_units.authorization(db, driver)
        if (not auth or auth['plan_id'] != plan['id'] or auth['plan_version'] != plan['version'] or
                auth['driver_generation'] != driver['generation']):
            raise ValueError('须使用当前方案和责任的准确派发授权。')
        if len(rows) >= auth['max_attempts_total'] or time.time() >= auth['dispatch_before']:
            raise ValueError('原累计次数或派发截止已耗尽，不能登记新提案回合。')
        sources = [r for r in rows if r['plan_id'] == plan['id'] and r['generation'] == driver['generation']
                   and r['input']['unit']['role'] == 'coordinate']
        if not sources:
            raise ValueError('本方案尚无可修订的准确提案回合。')
        run, evidence = self.s.work_proposals.source(db, item, driver, plan, rows, sources[-1]['id'])
        source_unit = next(u for u in plan['units'] if u['key'] == run['unit_key'])
        self.s.work_transitions.validate_run(db, plan, source_unit, run, rows,
                                            frozenset({(plan['id'], source_unit['key'])}))
        lineage = {}
        for unit in plan['units']:
            if unit['role'] == 'coordinate':
                continue
            resolved = self.s.work_units.resolve(db, plan, unit['key'], rows)
            old, result = resolved['run'], resolved['evidence']
            if resolved['problems']:
                raise ValueError('普通成果沿用已失效，须先明确返工：' + unit['key'])
            if not old:
                continue
            if not result['exact_identity'] or not result['result_available']:
                raise ValueError('普通工作尚无准确完成结果，须先明确返工：' + unit['key'])
            self.s.work_artifacts.require(result, 'checked' if unit['checks'] else 'files' if unit['outputs'] else 'result')
            original = self.s.collaboration.get(db, 'teamwork_plan', old['plan_id'])
            original_unit = next(u for u in original['units'] if u['key'] == unit['key'])
            if original_unit != unit:
                raise ValueError('普通成果的原单元定义已变化：' + unit['key'])
            self.s.work_transitions.validate_run(db, original, original_unit, old, rows,
                                                frozenset({(original['id'], unit['key'])}), consumer_plan=plan)
            lineage[unit['key']] = source_ref(old, result)
        return run, evidence, auth, lineage

    def view(self, db, item, driver, plan, rows):
        if plan and plan.get('coordinator_file_handoff_root'):
            from .work_coordinator_handoff_round_public import view
            return view(self.s, db, item, driver, plan)
        try:
            run, evidence, _, _ = self.candidate(db, item, driver, plan, rows)
            return {'can_revise': True, 'source': source_ref(run, evidence), 'unit_key': run['unit_key'],
                    'record': plan.get('coordination_revision'), 'problems': []}
        except ValueError as exc:
            return {'can_revise': False, 'source': None, 'unit_key': None,
                    'record': plan.get('coordination_revision') if plan else None, 'problems': [str(exc)]}

    def revise(self, db, p):
        if set(p) != {'id', 'revision', 'current_driver', 'plan', 'authorization', 'frontier',
                      'run_id', 'result_version', 'reason'}:
            raise ValueError('新提案回合只接受准确任务、责任、方案、授权、前沿、源回合与登记依据。')
        reason = bounded_text(p['reason'], '新提案回合依据', 2000)
        item, driver, plan = self.s.work_units.checked(db, p)
        rows = self.s.work_units.rows(db, item['id'])
        if plan.get('coordinator_file_handoff_root'):
            from .work_coordinator_handoff_round_public import revise
            return revise(self.s, db, p, item, driver, plan, reason)
        run, evidence, auth, lineage = self.candidate(db, item, driver, plan, rows)
        if p['authorization'] != self.s.work_units.auth_ref(auth) or p['frontier'] != self.s.work_units.frontier(db, item['id']):
            raise ValueError('新提案回合的授权或准确执行前沿已变化。')
        if p['run_id'] != run['id'] or p['result_version'] != evidence['version']:
            raise ValueError('须引用本方案最新准确协调回合及结果版本。')
        source = source_ref(run, evidence)
        checked = self.s.work_proposals.current(db, item['id'])
        prior_check = ({'reference': self.s.work_proposals.reference(checked),
                        'state': checked['snapshot']['state'], 'problems': checked['snapshot']['problems']}
                       if checked and digest(checked['snapshot']) == checked['version'] and
                       checked['snapshot']['source']['run_id'] == run['id'] and
                       checked['snapshot']['source']['version'] == evidence['version'] else None)
        revision = {'id': 'coordination-revision:' + uid(), 'item_id': item['id'], 'unit_key': run['unit_key'],
                    'source': source, 'reason': reason, 'prior_check': prior_check,
                    'answer': evidence['answer'] if len(evidence['answer']) <= 32000 else None,
                    'answer_chars': len(evidence['answer']), 'answer_digest': digest(evidence['answer'])}
        history = set(plan.get('historical_coordinators', [])) | {
            r['unit_key'] for r in rows if r['plan_id'] == plan['id'] and r['input']['unit']['role'] == 'coordinate'}
        history.discard(run['unit_key'])
        transition = {'kind': 'EXPLICIT_COORDINATION_REVISION', 'source': source, 'reason': reason,
                      'usage_at_transition': self.s.work_units.usage(db, rows),
                      'authorization_from': self.s.work_units.auth_ref(auth),
                      'command_id': self.s.command_identity, 'no_dispatch': True}
        new, updated, transferred = self.s.work_transitions.save_plan(db, item, driver, plan, auth,
            plan['units'], lineage, transition,
            extra={'coordination_revision': revision, 'historical_coordinators': sorted(history)},
            reason='COORDINATION_REVISED_EXPLICIT_RECHECK_REQUIRED')
        record = {**revision, 'from_plan': plan_ref(plan), 'to_plan': plan_ref(new),
                  'driver': self.s.work_drivers.reference(updated),
                  'authorization': self.s.work_units.auth_ref(transferred),
                  'created': new['created'], 'command_id': self.s.command_identity, 'no_dispatch': True}
        self.s.collaboration.put(db, 'work_coordination_revision', record)
        self.s.store.event(run['task_id'], run['attempt_id'], 'coordinator_round_revised', record, db)
        return {'coordination_revision': record, 'plan': plan_ref(new),
                'driver': self.s.work_drivers.reference(updated), 'authorization': self.s.work_units.auth_ref(transferred),
                'no_dispatch': True, 'automatically_resumed': False}
