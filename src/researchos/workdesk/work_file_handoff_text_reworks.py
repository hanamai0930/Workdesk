"""Typed, internal explicit text rework candidates for saved fixed handoff paths.

This module observes history and prepares a detached checkpoint. It does not
register commands, save a plan, transfer authority, or admit execution.
"""
from copy import deepcopy
import time

from .contracts import bounded_text
from .store import digest
from .work_file_handoff_ancestry import WorkFileHandoffAncestry
from .work_file_handoff_apply import APPLICATION_KIND
from .work_file_handoff_chain import MAX_DEPTH
from .work_file_sources import resource_status
from .work_transitions import plan_ref, source_ref


PREVIEW = 'FIXED_FILE_HANDOFF_TEXT_REWORK_PREVIEW_V1'
CHECKPOINT = 'FIXED_FILE_HANDOFF_TEXT_REWORK_CHECKPOINT_V1'
CANDIDATE = 'FIXED_FILE_HANDOFF_TEXT_REWORK_SAVED_CANDIDATE_V1'
APPLICATION = 'FIXED_FILE_HANDOFF_TEXT_REWORK_APPLICATION_V1'
CONSUMPTION = 'FIXED_FILE_HANDOFF_TEXT_REWORK_CONSUMPTION_V1'
PATH = 'FIXED_FILE_HANDOFF_REWORK_SAVED_PATH_V1'
INPUT = 'FIXED_FILE_HANDOFF_TEXT_REWORK_INPUT_V1'
FIELDS = {'id', 'revision', 'current_driver', 'plan', 'authorization', 'frontier', 'selections', 'reason'}


def selections(value):
    if (not isinstance(value, list) or not 1 <= len(value) <= 20 or
            any(not isinstance(v, dict) or set(v) != {'unit_key', 'source_run_id', 'source_version'} or
                any(not isinstance(v[k], str) or not v[k].strip() for k in v) for v in value) or
            len({v['unit_key'] for v in value}) != len(value)):
        raise ValueError('文字返工须选择1至20个唯一单元及准确原运行、完整证据版本。')


def project_sources(service, plan, request, rows, evidence, proof):
    """Rebuild sources and closure only after the entire historical path passed."""
    selections(request['selections'])
    reason = bounded_text(request['reason'], '明确文字返工依据', 2000)
    by_key = {u['key']: u for u in plan['units']}
    by_id = {r['id']: r for r in rows}
    retired, completed = proof['retired_source'], proof['completed_key']
    sources = {}
    for unit in plan['units']:
        key = unit['key']
        if key == retired:
            sources[key] = None
            continue
        local = [r for r in rows if r['plan_id'] == plan['id'] and r['unit_key'] == key]
        inherited = plan.get('result_lineage', {}).get(key)
        run = local[0] if local else by_id.get(inherited['run_id']) if inherited else None
        if len(local) > 1 or (inherited and not run):
            raise ValueError('文字返工来源缺失或同修订重复运行。')
        raw = evidence[run['id']] if run else None
        if run and (run['unit_key'] != key or run['input']['unit'] != unit or
                    (not local and source_ref(run, raw) != inherited)):
            raise ValueError('文字返工来源的原定义、运行或完整证据不一致。')
        sources[key] = ({'source_ref': source_ref(run, raw), 'original_definition': unit,
                         'input': run['input'], 'evidence': raw} if run else None)
    chosen = {v['unit_key']: v for v in request['selections']}
    for key, value in chosen.items():
        source = sources.get(key)
        if (key not in by_key or key in (retired, completed) or by_key[key]['role'] == 'coordinate' or
                source is None or value['source_run_id'] != source['source_ref']['run_id'] or
                value['source_version'] != source['evidence']['version']):
            raise ValueError('明确选择须绑定当前准确普通来源的原运行和完整证据版本。')
    missing = {k for k, v in sources.items() if v and not v['evidence']['result_available']}
    affected = set(chosen) | missing
    while True:
        more = {u['key'] for u in plan['units'] if set(u['depends']) & affected} - affected
        if not more:
            break
        affected.update(more)
    protected = WorkFileHandoffAncestry.protected_keys(plan['units'], [retired, completed])
    if affected & protected:
        raise ValueError('文字返工不能影响交接两端及其完整上游。')
    for key in affected:
        unit = by_key[key]
        if (unit['role'] == 'coordinate' or unit['outputs'] or unit['checks'] or unit['write_paths'] or
                unit['dependency_gate'] != 'result' or retired in unit['depends']):
            raise ValueError('文字返工影响须限于普通文字结果，不能读取退役前版：' + key)
    selected, entries = [], []
    for unit in plan['units']:
        key = unit['key']; source = sources[key]
        if key in chosen:
            selected.append({**chosen[key], **source})
        state = ('HISTORICAL_RETIRED' if key == retired else 'AFFECTED' if key in affected
                 else 'ELIGIBLE_RESULT' if source else 'NOT_RUN')
        if state == 'ELIGIBLE_RESULT':
            service.work_artifacts.require(source['evidence'],
                'checked' if unit['checks'] else 'files' if unit['outputs'] else 'result')
        causes = (['EXPLICIT_SELECTION'] if key in chosen else []) + (
            ['KNOWN_TERMINAL_WITHOUT_RESULT'] if key in missing else [])
        if key in affected and not causes:
            causes.append('AFFECTED_DEPENDENCY')
        entries.append({'key': key, 'original_definition': unit, 'candidate_definition': unit,
            'state': state, 'source': source['source_ref'] if state == 'ELIGIBLE_RESULT' else None,
            'prior_source': source if state == 'AFFECTED' else None, 'reasons': causes,
            'historical_source': proof['historical_source'] if key == retired else None})
    return deepcopy({'selections': selected, 'units': entries,
        'affected_units': [u['key'] for u in plan['units'] if u['key'] in affected],
        'eligible_results': [e['key'] for e in entries if e['state'] == 'ELIGIBLE_RESULT'],
        'protected_keys': sorted(protected), 'reason': reason})


class _ReworkObservation:
    def __init__(self, service, db, plan):
        from .work_file_handoff_chain_runtime import saved_path
        self.s, self.db, self.plan = service, db, deepcopy(plan)
        chain = saved_path(service, db, plan)
        self.proof = chain.verify(db, plan)
        self.paths = chain._path(db, plan)
        self.changes = db.total_changes
        self.evidence = {e['run_id']: e['evidence'] for e in self.proof['frontier']}

    def require(self, apps, db, plan):
        if (apps is not self.s.work_file_handoff_applications or db is not self.db or
                db.total_changes != self.changes or self.plan != plan or
                self.s.teamwork.current(db, plan['item_id']) != plan):
            raise ValueError('返工路径观察已脱离本次准确Service、事务或完整方案。')
        return deepcopy(self.proof)


class WorkFileHandoffTextReworks:
    protocol = PREVIEW

    def __init__(self, service):
        self.s = service

    def preview(self, db, request):
        with self.s.work_unit_revision_applications.validation_snapshot(db):
            if not isinstance(request, dict) or set(request) != FIELDS:
                raise ValueError('文字返工只接受准确任务、责任、方案、授权、前沿、来源选择和依据。')
            item, driver, plan = self.s.work_units.checked(db, request)
            self.s.work_flows.require_stopped(db, item['id'])
            observation = _ReworkObservation(self.s, db, plan)
            proof = observation.proof
            ledger = self.s.work_file_handoff_ledger
            if len(proof['edges']) >= MAX_DEPTH:
                raise ValueError('保存路径最多32条边，不能再增加返工边。')
            last = ledger.get(db, APPLICATION_KIND, proof['edges'][-1])['snapshot']
            auth = self.s.work_units.authorization(db, driver)
            frontier = self.s.work_units.frontier(db, item['id'], _read=observation)
            if (auth != last['authorization'] or request['authorization'] != self.s.work_units.auth_ref(auth) or
                    auth['plan_id'] != plan['id'] or auth['plan_version'] != plan['version'] or
                    auth['item_id'] != item['id'] or auth['driver_generation'] != driver['generation'] or
                    driver['id'] != last['paused_driver']['id'] or
                    driver['generation'] != last['paused_driver']['generation'] or request['frontier'] != frontier):
                raise ValueError('文字返工须保持最后准确转移授权、同generation责任与完整前沿。')
            rows = self.s.work_units.rows(db, item['id'])
            projection = project_sources(self.s, plan, request, rows, observation.evidence, proof)
            root = ledger.get(db, APPLICATION_KIND, proof['edges'][0])['snapshot']['lineage']['proof']
            resources = resource_status(self.s, db, observation.paths)
            if any(not r['valid'] for r in resources):
                raise ValueError('返工原路径成员、连接或额度来源已变化。')
            audit = {'protocol': CHECKPOINT, 'base_plan': plan_ref(plan), 'item_id': item['id'],
                'item_revision': item['revision'], 'project_root': root['project_root'], 'relation': root['relation'],
                'plans': proof['plans'], 'parent_application': proof['edges'][-1], 'root_application': proof['edges'][0],
                **{k: root[k] for k in ('historical_source', 'completed_target', 'delivery', 'completion')},
                'resources': resources, 'protected_keys': projection['protected_keys'],
                'protected_history': ledger.history(db, rows), 'frontier': proof['frontier'],
                'candidate_units': plan['units'], 'selections': projection['selections'],
                'affected_units': projection['affected_units']}
            audit['version'] = digest(audit)
            blockers = []
            if len(rows) >= auth['max_attempts_total']:
                blockers.append('原累计授权次数不足；返工不增加授权')
            if time.time() >= auth['dispatch_before']:
                blockers.append('原新增派发截止已到期；返工不延长截止')
            body = {'protocol': PREVIEW, 'request': request, 'base_plan': plan_ref(plan),
                'item_revision': item['revision'], 'driver': self.s.work_drivers.reference(driver),
                'authorization': auth, 'frontier': frontier, 'reason': projection['reason'],
                'candidate_units': plan['units'], **{k: projection[k] for k in
                    ('selections', 'units', 'affected_units', 'eligible_results')},
                'ancestry_audit': audit, 'completed_target': proof['completed_key'], 'retired_source': proof['retired_source'],
                'usage': self.s.work_units.usage(db, rows), 'execution_blockers': blockers,
                'save_permission': False, 'current_input_permission': False, 'execution_permission': False,
                'scope': 'INTERNAL_EXPLICIT_FIXED_TEXT_REWORK; NO_SAVE_OR_DISPATCH',
                'independent_review': 'NOT_PERFORMED', 'human_acceptance': 'NOT_RECORDED'}
            observation.require(self.s.work_file_handoff_applications, db, plan)
            return deepcopy({**body, 'version': digest(body)})

    def recheck(self, db, expected):
        if (not isinstance(expected, dict) or expected.get('protocol') != PREVIEW or
                expected.get('version') != digest({k: v for k, v in expected.items() if k != 'version'})):
            raise ValueError('文字返工候选完整版本不一致。')
        current = self.preview(db, expected['request'])
        if current != expected:
            raise ValueError('文字返工候选的完整来源、影响、历史或操作资格已变化。')
        return current
