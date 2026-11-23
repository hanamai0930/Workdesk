"""Internal read-only revision-candidate audit. No save or execution permission.

Only a current, original fixed plan is accepted. A completed consumer may be
proved as a future carry candidate; the retired producer is historical only.
This module is deliberately not registered on Service or any command path.
"""
from copy import deepcopy
import json

from .store import digest
from .work_file_handoff_config import definition
from .work_file_sources import resource_status
from .work_transitions import plan_ref, source_ref


PROTOCOL = 'SERIAL_FILE_HANDOFF_ANCESTRY_AUDIT_V1'


class WorkFileHandoffAncestry:
    def __init__(self, service):
        self.s = service

    @staticmethod
    def protected_keys(units, endpoints):
        by_key = {u['key']: u for u in units}
        protected, pending = set(), list(endpoints)
        while pending:
            key = pending.pop()
            if key not in protected:
                protected.add(key)
                pending.extend(by_key[key]['depends'])
        return protected

    def audit(self, db, plan, candidate_units, affected):
        # An outer cached view cannot hide same-length file changes on recheck.
        with self.s.work_unit_revision_applications.validation_snapshot(db):
            return self._audit(db, plan, candidate_units, affected)

    def _audit(self, db, plan, candidate_units, affected):
        ledger = self.s.work_file_handoff_ledger
        if (self.s.teamwork.current(db, plan['item_id']) != plan or
                self.s.collaboration.get(db, 'teamwork_plan', plan['id']) != plan or plan['mode'] != 'fixed'):
            raise ValueError('沿袭核对须使用当前准确固定流程原方案。')
        item = self.s.work_items.item(db, plan['item_id'])
        # Linking execution records legitimately advances the business revision.
        # Bind its current value in the audit; compare requirements separately.
        if (item['project_id'] != plan['project_id'] or
                {k: item[k] for k in ('title', 'goal', 'constraints_text', 'acceptance')} != plan['work_requirements']):
            raise ValueError('业务任务准确版本、原要求或项目归属已变化。')
        root = self.s.work_file_rework_applications.root(db, item)
        relations = definition(plan.get('file_handoffs', []), plan['units'], plan['max_parallel'])
        if len(relations) != 1:
            raise ValueError('沿袭核对须有一项明确交接关系。')
        relation = ledger.relation(plan, relations[0]['target_unit'])
        if definition(plan['file_handoffs'], candidate_units, plan['max_parallel']) != [relation]:
            raise ValueError('候选交接关系不一致。')
        if (not isinstance(affected, list) or any(not isinstance(k, str) for k in affected) or
                len(set(affected)) != len(affected) or not set(affected) <= {u['key'] for u in candidate_units}):
            raise ValueError('沿袭核对须提供准确候选受影响单元集合。')
        endpoints = [relation['source_unit'], relation['target_unit']]
        protected = self.protected_keys(plan['units'], endpoints) | self.protected_keys(candidate_units, endpoints)
        old = {u['key']: u for u in plan['units']}
        new = {u['key']: u for u in candidate_units}
        if (protected & set(affected) or any(old.get(k) != new.get(k) for k in protected)):
            raise ValueError('交接两端及其完整上游定义或依赖受影响，首批不能沿袭。')
        rows = self.s.work_units.rows(db, plan['item_id'])
        if any(r['plan_id'] != plan['id'] for r in rows):
            raise ValueError('内部基础只核对无祖先原方案，不能将旧运行重绑为修订许可。')
        history = ledger.history(db, rows)
        if any(h['workspace'] != root for h in history):
            raise ValueError('当前项目目录与原实际执行目录不一致。')
        resources = resource_status(self.s, db, [plan])
        if any(not r['valid'] for r in resources):
            raise ValueError('交接原成员、连接或额度来源已变化。')
        sources = [r for r in rows if r['unit_key'] == relation['source_unit']]
        targets = [r for r in rows if r['unit_key'] == relation['target_unit']]
        if len(sources) > 1 or len(targets) > 1:
            raise ValueError('首批来源核对不接受重复端点运行或同修订重试。')
        source = sources[0] if sources else None
        target = targets[0] if targets else None
        # Bind the complete history, including unrelated units and all snapshots.
        # Failed/unknown frozen dependencies must not become valid through carry.
        for run in rows:
            if run is target:
                continue  # completion supplies the exact historical dependency proof.
            self.s.work_transitions.validate_run(db, plan, old[run['unit_key']], run, rows, frozenset())
            if run is not source:
                evidence = self.s.work_units.evidence(db, run)
                unit = old[run['unit_key']]
                self.s.work_artifacts.require(evidence, 'checked' if unit['checks'] else
                                              'files' if unit['outputs'] else 'result')
        snapshot = {'protocol': PROTOCOL, 'item_id': plan['item_id'], 'item_revision': item['revision'],
                    'project_root': root, 'base_plan': plan_ref(plan),
                    'relation': relation, 'candidate_units': candidate_units, 'affected_units': sorted(affected),
                    'protected_keys': sorted(protected), 'protected_history': history, 'resources': resources,
                    'frontier': [{'run_id': r['id'], 'evidence': self.s.work_units.evidence(db, r)} for r in rows],
                    'state': 'NOT_DELIVERED', 'completed_target': None, 'historical_source': None,
                    'delivery': None, 'completion': None, 'carry_candidate': False,
                    'current_input_permission': False, 'execution_permission': False,
                    'scope': 'INTERNAL_ORIGINAL_PLAN_CANDIDATE_AUDIT; NO_REVISION_OR_DISPATCH_GRANT'}
        if not target:
            if source:
                evidence = self.s.work_units.evidence(db, source)
                unit = old[source['unit_key']]
                self.s.work_artifacts.require(evidence, 'checked' if unit['checks'] else 'files')
        else:
            if not source:
                raise ValueError('交接后继缺少准确原前版运行。')
            delivery = ledger.delivery_for(db, target)
            ledger.verify_delivery(db, ledger.reference(delivery), target)
            snapshot.update(state='DELIVERED_UNFINISHED', delivery=ledger.reference(delivery))
            completions = [json.loads(r[0]) for r in db.execute(
                "SELECT data FROM collaboration WHERE kind='work_file_handoff_completion' "
                "AND json_extract(data,'$.snapshot.run_id')=?", (target['id'],))]
            if completions:
                completion = ledger.completion_for(db, target)
                intent = delivery['snapshot']['intent']
                proof = ledger.match(db, ledger.reference(completion), target, source,
                                     intent['source_evidence']['version'])
                self.s.work_transitions.validate_run(db, plan, old[target['unit_key']], target, rows, frozenset())
                evidence = self.s.work_units.evidence(db, target)
                snapshot.update(state='COMPLETED_VALID', carry_candidate=True,
                    completion=proof['completion'],
                    historical_source={'run_id': source['id'], 'evidence': intent['source_evidence'],
                                       'current_input_permission': False},
                    completed_target={'source': source_ref(target, evidence), 'evidence': evidence})
        # Detach caller-owned candidate definitions and source dictionaries.
        return deepcopy({**snapshot, 'version': digest(snapshot)})

    def recheck(self, db, expected):
        if (not isinstance(expected, dict) or expected.get('protocol') != PROTOCOL or
                expected.get('version') != digest({k: v for k, v in expected.items() if k != 'version'})):
            raise ValueError('沿袭核对快照完整版本不一致。')
        plan = self.s.collaboration.get(db, 'teamwork_plan', expected['base_plan']['id'])
        current = self.audit(db, plan, expected['candidate_units'], expected['affected_units'])
        if current != expected:
            raise ValueError('沿袭核对业务任务、来源、当前文件或完整前沿已变化。')
        return current
