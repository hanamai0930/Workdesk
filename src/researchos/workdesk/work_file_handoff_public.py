"""Public explicit fixed revisions, routed only by persisted plan/candidate identity."""
import time

from copy import deepcopy

from .store import digest
from .work_file_handoff_apply import CANDIDATE, CANDIDATE_KIND
from .work_file_handoff_chain import CANDIDATE as CHAIN_CANDIDATE
from .work_file_handoff_chain_apply import WorkFileHandoffChainApplications
from .work_file_handoff_ancestry import WorkFileHandoffAncestry
from .work_file_handoff_text_reworks import CANDIDATE as TEXT_CANDIDATE
from .work_file_handoff_text_rework_apply import WorkFileHandoffTextReworkApplications
from .work_file_handoff_unit_revisions import (
    CANDIDATE as UNIT_CANDIDATE, historical_keys, ordinary)
from .work_file_handoff_unit_revision_apply import WorkFileHandoffUnitRevisionApplications
from .work_file_handoff_file_reworks import CANDIDATE as FILE_CANDIDATE
from .work_file_handoff_file_rework_apply import WorkFileHandoffFileReworkApplications
from .work_file_handoff_file_rework_public import file_scope
from .work_file_handoff_diagnostics import read_sources
from .work_transitions import plan_ref


class WorkFileHandoffPublic:
    def __init__(self, service):
        self.s = service
        self.apps = service.work_file_handoff_applications

    def candidate_apps(self, db, reference):
        row = self.apps.ledger.get(db, CANDIDATE_KIND, reference)
        protocol = row['snapshot'].get('protocol')
        if protocol == CANDIDATE:
            return self.apps
        if protocol == CHAIN_CANDIDATE:
            return WorkFileHandoffChainApplications(self.s)
        raise ValueError('明确交接候选协议不受支持。')

    def check(self, db, request):
        if not isinstance(request, dict):
            raise ValueError('明确交接候选须使用准确请求。')
        plan = self.s.teamwork.current(db, request.get('id'))
        apps = WorkFileHandoffChainApplications(self.s) if plan and plan.get('file_handoff_lineage') else self.apps
        return apps.check(db, request)

    def apply(self, db, request):
        if not isinstance(request, dict) or set(request) != {
                'id','revision','current_driver','plan','authorization','frontier','candidate'}:
            raise ValueError('交接应用只接受准确任务、责任、方案、授权、前沿和候选引用。')
        return self.candidate_apps(db, request['candidate']).apply(db, request)

    def text_check(self, db, request):
        return WorkFileHandoffTextReworkApplications(self.s).check(db, request)

    def text_apply(self, db, request):
        if not isinstance(request, dict) or set(request) != {
                'id','revision','current_driver','plan','authorization','frontier','candidate'}:
            raise ValueError('文字返工应用只接受准确任务、责任、方案、授权、前沿和候选引用。')
        row = self.apps.ledger.get(db, CANDIDATE_KIND, request['candidate'])
        if row['snapshot'].get('protocol') != TEXT_CANDIDATE:
            raise ValueError('文字返工专用入口只接受准确文字返工候选。')
        return WorkFileHandoffTextReworkApplications(self.s).apply(db, request)

    def unit_check(self, db, request):
        return WorkFileHandoffUnitRevisionApplications(self.s).check(db, request)

    def unit_apply(self, db, request):
        if not isinstance(request, dict) or set(request) != {
                'id','revision','current_driver','plan','authorization','frontier','candidate'}:
            raise ValueError('普通文字修订应用只接受准确任务、责任、方案、授权、前沿和候选引用。')
        row = self.apps.ledger.get(db, CANDIDATE_KIND, request['candidate'])
        if row['snapshot'].get('protocol') != UNIT_CANDIDATE:
            raise ValueError('普通文字新增与修订专用入口只接受准确普通文字候选。')
        return WorkFileHandoffUnitRevisionApplications(self.s).apply(db, request)

    def file_check(self, db, request):
        return WorkFileHandoffFileReworkApplications(self.s).check(db, request)

    def file_apply(self, db, request):
        if not isinstance(request, dict) or set(request) != {
                'id','revision','current_driver','plan','authorization','frontier','candidate'}:
            raise ValueError('文件返工应用只接受准确任务、责任、方案、授权、前沿和候选引用。')
        row = self.apps.ledger.get(db, CANDIDATE_KIND, request['candidate'])
        if row['snapshot'].get('protocol') != FILE_CANDIDATE:
            raise ValueError('文件返工专用入口只接受准确文件返工候选。')
        return WorkFileHandoffFileReworkApplications(self.s).apply(db, request)

    @staticmethod
    def candidate_type(row):
        if row is None:
            return None
        protocol = row['snapshot'].get('protocol')
        if protocol in (CANDIDATE, CHAIN_CANDIDATE):
            return 'DEFINITION_REVISION'
        if protocol == FILE_CANDIDATE:
            return 'FILE_REWORK'
        if protocol == UNIT_CANDIDATE:
            return 'UNIT_REVISION'
        return 'TEXT_REWORK' if protocol == TEXT_CANDIDATE else 'UNKNOWN'

    def detail_apps(self, db, reference):
        row = self.apps.ledger.get(db, CANDIDATE_KIND, reference)
        if row['snapshot'].get('protocol') == TEXT_CANDIDATE:
            return WorkFileHandoffTextReworkApplications(self.s)
        if row['snapshot'].get('protocol') == UNIT_CANDIDATE:
            return WorkFileHandoffUnitRevisionApplications(self.s)
        if row['snapshot'].get('protocol') == FILE_CANDIDATE:
            return WorkFileHandoffFileReworkApplications(self.s)
        return self.candidate_apps(db, reference)

    def view(self, db, item):
        plan = self.s.teamwork.current(db, item['id'])
        if not plan or not plan.get('file_handoffs'):
            return None
        if not plan.get('file_handoff_lineage'):
            value = self.apps._original_view(db, item)
            value.update(chain_depth=0, consumer_plan=plan_ref(plan), source_plans=[], path_problems=[],
                candidate_type=self.candidate_type(value.get('record')),
                text_rework={'can_check':False, 'sources':None, 'problems':[]},
                unit_revision={'can_check':False, 'sources':None, 'problems':[]},
                file_rework={'can_check':False, 'sources':None, 'problems':[]})
            return value
        driver = self.s.work_drivers.current(db, item['id'])
        auth = self.s.work_units.authorization(db, driver)
        protected = WorkFileHandoffAncestry.protected_keys(plan['units'],
            [r[k] for r in plan['file_handoffs'] for k in ('source_unit','target_unit')])
        editable = [u for u in plan['units'] if u['key'] not in protected and u['role'] != 'coordinate' and
                    not (u['outputs'] or u['checks'] or u['write_paths']) and u['dependency_gate'] == 'result']
        value = {'state':'NOT_CHECKED','record':None,'reference':None,'problems':[],
            'candidate_type':None, 'text_rework':{'can_check':False,'sources':None,'problems':[]},
            'unit_revision':{'can_check':False,'sources':None,'problems':[]},
            'file_rework':{'can_check':False,'sources':None,'problems':[]},
            'can_check':False,'can_apply':False,'protected_keys':sorted(protected),
            'work_requirements_version':digest(plan['work_requirements']),
            'edit_units':[{**{k:u[k] for k in ('key','title','goal','acceptance','depends')},
                'revision':digest(u),'member':u['member']['reference']} for u in editable],
            'chain_depth':None,'consumer_plan':plan_ref(plan),'source_plans':[], 'path_problems':[], 'no_dispatch':True}
        try:
            observation = WorkFileHandoffChainApplications(self.s).candidates.observation(db, plan)
            proof = observation.require(self.apps, db, plan)
            _, issues = self.s.work_drivers.readiness(db, item, driver)
            value.update(chain_depth=len(proof['edges']), source_plans=proof['plans'][:-1],
                can_check=bool(editable and driver and driver['state']=='RESERVED' and auth and not issues and
                    driver['item_revision']==item['revision'] and driver['plan_id']==plan['id'] and
                    driver['plan_version']==plan['version'] and len(self.s.work_units.rows(db,item['id'])) < auth['max_attempts_total'] and
                    time.time() < auth['dispatch_before'] and
                    (self.s.work_flows.current(db,item['id']) or {}).get('state')!='RUNNING'))
            operational = bool(driver and driver['state']=='RESERVED' and auth and not issues and
                driver['item_revision']==item['revision'] and driver['plan_id']==plan['id'] and
                driver['plan_version']==plan['version'] and len(self.s.work_units.rows(db,item['id'])) < auth['max_attempts_total'] and
                time.time() < auth['dispatch_before'] and
                (self.s.work_flows.current(db,item['id']) or {}).get('state')!='RUNNING')
            sources = read_sources(self.s, db, plan, _read=observation)
            selectable = {u['key'] for u in editable}
            for source in sources['units']:
                source['selectable'] = bool(source['key'] in selectable and source['source'] and
                    source['definition']['dependency_gate'] == 'result' and
                    source['qualification'] in ('CURRENT_RESULT','KNOWN_TERMINAL_WITHOUT_RESULT'))
            value['file_rework'] = file_scope(self.s, db, plan, item, sources, observation, operational)
            value['text_rework'].update(sources=sources,
                can_check=bool(value['can_check'] and any(u['selectable'] for u in sources['units'])))
            # The material scope excludes reader protocol/path_version: a current
            # candidate can select a richer reader without changing any material.
            bases = [{**deepcopy(u), 'revision':digest(u)} for u in plan['units']
                     if ordinary(u) and u['key'] not in protected]
            members = list({digest(m['reference']):deepcopy(m) for m in
                [plan['coordinator'],plan['integrator'],*(u['member'] for u in plan['units'])]}.values())
            scope = {'sources':deepcopy(sources['units']), 'members':members,
                'historical_keys':historical_keys(self.s,db,item['id']),
                'work_requirements_version':digest(plan['work_requirements'])}
            value['unit_revision'].update(**scope, basis_units=bases,
                scope_version=digest(scope), can_check=bool(value['can_check'] and bases))
        except ValueError as exc:
            value['path_problems'] = [str(exc)]
            value['problems'].append(str(exc))
            value['text_rework']['problems'] = [str(exc)]
            value['unit_revision']['problems'] = [str(exc)]
            value['file_rework']['problems'] = [str(exc)]
        try:
            row = self.apps.current(db, item['id'])
            if row:
                value.update(record=row, reference=self.apps.ledger.reference(row),
                    candidate_type=self.candidate_type(row))
                apps = self.detail_apps(db, value['reference'])
                if self.apps.consumptions(db,value['reference']):
                    value['state'] = 'CONSUMED'  # History does not block the next explicit check.
                else:
                    preview = apps.candidates.recheck(db,row['snapshot']['preview'])
                    ready = ({'TEXT_REWORK':value['text_rework']['can_check'],
                              'UNIT_REVISION':value['unit_revision']['can_check'],
                              'FILE_REWORK':value['file_rework']['can_check']}.get(
                                  value['candidate_type'], value['can_check']))
                    value.update(state='VALID',can_apply=bool(ready and not preview['execution_blockers']),
                        problems=[*value['problems'],*preview['execution_blockers']])
        except ValueError as exc:
            value.update(state='STALE',problems=[*value['problems'],str(exc)])
        return value
