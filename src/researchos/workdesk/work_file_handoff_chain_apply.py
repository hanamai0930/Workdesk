"""Internal V2 candidate/atomic save, without public handlers or runtime grants.

Candidate frontiers use WorkUnits.frontier with an explicitly verified current
path observation. Historical verification never reads the candidate pointer.
"""
from copy import deepcopy

from .store import digest
from .work_file_handoff_ancestry import WorkFileHandoffAncestry
from .work_file_handoff_apply import WorkFileHandoffRevisionApplications, APPLICATION_KIND
from .work_file_handoff_chain import (
    WorkFileHandoffChain, MAX_DEPTH, AUDIT, PREVIEW, CANDIDATE, APPLICATION, CONSUMPTION)
from .work_file_handoff_config import definition
from .work_file_handoff_revisions import WorkFileHandoffRevisions
from .work_file_sources import resource_status


class _PathObservation:
    """One current-plan read, bound to service, connection and write count."""
    def __init__(self, service, db, plan):
        self.s, self.db, self.plan = service, db, deepcopy(plan)
        from .work_file_handoff_chain_runtime import saved_path
        chain = saved_path(service, db, plan)
        self.proof = chain.verify(db, plan)
        self.paths = chain._path(db, plan)
        self.changes = db.total_changes
        self.evidence = {e['run_id']: e['evidence'] for e in self.proof['frontier']}

    def require(self, apps, db, plan):
        if (apps is not self.s.work_file_handoff_applications or db is not self.db or
                self.changes != db.total_changes or self.plan != plan or
                self.s.teamwork.current(db, plan['item_id']) != plan):
            raise ValueError('祖先路径观察已脱离本次Service、事务或当前完整方案。')
        return deepcopy(self.proof)


class WorkFileHandoffChainCandidates(WorkFileHandoffRevisions):
    protocol = PREVIEW

    def observation(self, db, plan):
        observation = _PathObservation(self.s, db, plan)
        proof = observation.proof
        if len(proof['edges']) >= MAX_DEPTH:
            raise ValueError('交接祖先链最多32层，不能再保存修订。')
        app = self.s.work_file_handoff_ledger.get(db, APPLICATION_KIND, proof['edges'][-1])['snapshot']
        driver = self.s.work_drivers.current(db, plan['item_id'])
        if (self.s.work_units.authorization(db, driver) != app['authorization'] or
                driver['id'] != app['paused_driver']['id'] or
                driver['generation'] != app['paused_driver']['generation']):
            raise ValueError('多级交接须保持最后一边完整转移的原授权及同generation责任，不能重授或扩大。')
        return observation

    def audit(self, db, plan, candidate, affected, observation):
        proof = observation.require(self.s.work_file_handoff_applications, db, plan)
        ledger = self.s.work_file_handoff_ledger
        paths = observation.paths
        first = ledger.get(db, APPLICATION_KIND, proof['edges'][0])['snapshot']['lineage']['proof']
        relation = first['relation']
        if definition(plan['file_handoffs'], candidate, plan['max_parallel']) != [relation]:
            raise ValueError('多级候选须保留原交接关系。')
        endpoints = [relation[k] for k in ('source_unit', 'target_unit')]
        protected = (WorkFileHandoffAncestry.protected_keys(plan['units'], endpoints) |
                     WorkFileHandoffAncestry.protected_keys(candidate, endpoints))
        old, new = ({u['key']:u for u in units} for units in (plan['units'], candidate))
        if protected & affected or any(old[k] != new[k] for k in protected):
            raise ValueError('交接两端及完整原/新上游闭包不能受影响。')
        rows = self.s.work_units.rows(db, plan['item_id'])
        resources = resource_status(self.s, db, paths)
        if any(not r['valid'] for r in resources):
            raise ValueError('原路径成员、连接或额度来源已变化。')
        item = self.s.work_items.item(db, plan['item_id'])
        value = {**first, 'protocol': AUDIT, 'base_plan': proof['consumer_plan'],
            'item_revision': item['revision'], 'candidate_units': candidate,
            'affected_units': sorted(affected), 'protected_keys': sorted(protected),
            'protected_history': ledger.history(db, rows), 'resources': resources,
            'frontier': proof['frontier'], 'plans': proof['plans'],
            'parent_application': proof['edges'][-1], 'root_application': proof['edges'][0]}
        value.pop('version')
        return deepcopy({**value, 'version': digest(value)})

    def result(self, db, plan, key, rows, evidence, observation):
        if key == observation.proof['retired_source']:
            return None, None
        resolved = self.s.work_units.resolve(db, plan, key, rows, _read=observation)
        if resolved['problems']:
            raise ValueError('多级候选准确原成果已失效：' + '；'.join(resolved['problems']))
        return resolved['run'], resolved['evidence']


class WorkFileHandoffChainApplications(WorkFileHandoffRevisionApplications):
    candidate_protocol = CANDIDATE
    application_protocol = APPLICATION
    consumption_protocol = CONSUMPTION

    def __init__(self, service):
        super().__init__(service)
        self.candidates = WorkFileHandoffChainCandidates(service)

    def handlers(self):
        return {}  # d3.3 owns the public entry; existing V1 handlers stay V1.

    def check(self, db, request):
        receipt = super().check(db, request)
        self.candidates.recheck(db, receipt['record']['snapshot']['preview'])
        return receipt

    def apply(self, db, request):
        receipt = super().apply(db, request)
        self.verify(db, self.s.teamwork.current(db, request['id']))
        return receipt

    def contract(self, db, reference):
        preview, lineage, transition, extra = super().contract(db, reference)
        extra['file_handoff_lineage'].update({k:preview['ancestry_audit'][k]
            for k in ('parent_application', 'root_application')})
        return preview, lineage, transition, extra

    def verify(self, db, plan):
        from .work_file_handoff_chain_runtime import saved_path
        return saved_path(self.s, db, plan).verify(db, plan)
