"""Internal fixed-path file redo candidates; no save, input or runtime grant.

The first scope requires current captured files, outside the root delivery
prefix. Proposed historical matching is diagnostic, never a formal replacement.
"""
from copy import deepcopy
import time

from .contracts import bounded_text
from .store import digest
from .work_file_handoff_ancestry import WorkFileHandoffAncestry
from .work_file_handoff_apply import APPLICATION_KIND
from .work_file_handoff_chain import MAX_DEPTH
from .work_file_handoff_text_reworks import _ReworkObservation, selections
from .work_file_rework_applications import scope
from .work_file_reworks import original_files, file_work
from . import work_file_sources
from .work_transitions import plan_ref, source_ref


PREVIEW = 'FIXED_FILE_HANDOFF_FILE_REWORK_PREVIEW_V1'
CHECKPOINT = 'FIXED_FILE_HANDOFF_FILE_REWORK_CHECKPOINT_V1'
CANDIDATE = 'FIXED_FILE_HANDOFF_FILE_REWORK_SAVED_CANDIDATE_V1'
APPLICATION = 'FIXED_FILE_HANDOFF_FILE_REWORK_APPLICATION_V1'
CONSUMPTION = 'FIXED_FILE_HANDOFF_FILE_REWORK_CONSUMPTION_V1'
PATH = 'FIXED_FILE_HANDOFF_FILE_REWORK_SAVED_PATH_V1'
INPUT = 'FIXED_FILE_HANDOFF_FILE_REWORK_INPUT_V1'
FIELDS = {'id', 'revision', 'current_driver', 'plan', 'authorization', 'frontier', 'selections', 'reason'}


def project_sources(service, db, plan, request, rows, evidence, proof):
    try:
        selections(request['selections'])
    except ValueError as exc:
        raise ValueError(str(exc).replace('文字返工', '文件返工')) from None
    reason = bounded_text(request['reason'], '明确文件返工依据', 2000)
    by_key = {u['key']: u for u in plan['units']}
    by_id = {r['id']: r for r in rows}
    retired, completed = proof['retired_source'], proof['completed_key']
    sources = {}
    for unit in plan['units']:
        key = unit['key']
        local = [r for r in rows if r['plan_id'] == plan['id'] and r['unit_key'] == key]
        entry = plan.get('result_lineage', {}).get(key)
        run = local[0] if local else by_id.get(entry['run_id']) if entry else None
        if len(local) > 1 or (entry and not run):
            raise ValueError('文件返工来源缺失或同修订重复运行。')
        raw = evidence[run['id']] if run else None
        if run and (run['unit_key'] != key or run['input']['unit'] != unit or
                    (not local and source_ref(run, raw) != entry)):
            raise ValueError('文件返工须保持准确原定义、来源和完整证据。')
        sources[key] = (None if key == retired or not run else
            {'source_ref': source_ref(run, raw), 'original_definition': unit,
             'input': run['input'], 'evidence': raw})
    chosen = {v['unit_key']: v for v in request['selections']}
    for key, value in chosen.items():
        source = sources.get(key)
        if (key not in by_key or key in (retired, completed) or by_key[key]['role'] == 'coordinate' or
                not source or value['source_run_id'] != source['source_ref']['run_id'] or
                value['source_version'] != source['evidence']['version']):
            raise ValueError('文件返工选择须绑定准确普通来源、原运行及完整证据版本。')
    if not any(by_key[k]['outputs'] for k in chosen):
        raise ValueError('文件返工至少须选择一项已有文件产物；纯文字使用文字返工入口。')
    missing = {k for k, v in sources.items() if v and not v['evidence']['result_available']}
    affected = set(chosen) | missing
    while True:
        more = {u['key'] for u in plan['units'] if set(u['depends']) & affected} - affected
        if not more:
            break
        affected.update(more)
    protected = WorkFileHandoffAncestry.protected_keys(plan['units'], [retired, completed])
    if protected & affected:
        raise ValueError('文件返工不能影响交接两端及其完整上游。')
    target_id = proof['completed_target']['source']['run_id']
    target_index = next(i for i, r in enumerate(rows) if r['id'] == target_id)
    targets = scope(plan, affected)
    for key in affected:
        unit, source = by_key[key], sources[key]
        if unit['role'] == 'coordinate' or retired in unit['depends']:
            raise ValueError('文件返工不能包含协调职责或读取退役前版。')
        if file_work(unit) and (unit['checks'] or unit['write_paths']) and not unit['outputs']:
            raise ValueError('文件返工不开放仅检查或仅写入职责。')
        if source and (unit['outputs'] or unit['checks'] or unit['write_paths']):
            run = by_id[source['source_ref']['run_id']]
            if rows.index(run) <= target_index:
                raise ValueError('首批文件返工仅支持根B完成后运行的普通文件，根交付前沿不开放替代。')
            service.work_artifacts.require(source['evidence'],
                'checked' if unit['checks'] else 'files' if unit['outputs'] else unit['dependency_gate'])
    entries, selected = [], []
    for unit in plan['units']:
        key, source = unit['key'], sources[unit['key']]
        state = ('HISTORICAL_RETIRED' if key == retired else 'AFFECTED' if key in affected
                 else 'ELIGIBLE_RESULT' if source else 'NOT_RUN')
        if state == 'ELIGIBLE_RESULT':
            service.work_artifacts.require(source['evidence'],
                'checked' if unit['checks'] else 'files' if unit['outputs'] else 'result')
        if key in chosen:
            selected.append({**chosen[key], **source})
        causes = (['EXPLICIT_SELECTION'] if key in chosen else []) + (
            ['KNOWN_TERMINAL_WITHOUT_RESULT'] if key in missing else [])
        if key in affected and not causes:
            causes.append('AFFECTED_DEPENDENCY')
        entries.append({'key': key, 'original_definition': unit, 'candidate_definition': unit,
            'state': state, 'source': source['source_ref'] if state == 'ELIGIBLE_RESULT' else None,
            'prior_source': source if state == 'AFFECTED' else None, 'reasons': causes,
            'historical_source': proof['historical_source'] if key == retired else None})
    return deepcopy({'selections': selected, 'units': entries, 'write_scope': targets,
        'affected_units': [u['key'] for u in plan['units'] if u['key'] in affected],
        'eligible_results': [e['key'] for e in entries if e['state'] == 'ELIGIBLE_RESULT'],
        'protected_keys': sorted(protected), 'reason': reason})


class WorkFileHandoffFileReworks:
    def __init__(self, service):
        self.s = service

    def preview(self, db, request):
        with self.s.work_unit_revision_applications.validation_snapshot(db):
            if not isinstance(request, dict) or set(request) != FIELDS:
                raise ValueError('文件返工只接受准确任务、责任、方案、授权、前沿、来源和依据。')
            item, driver, plan = self.s.work_units.checked(db, request)
            self.s.work_flows.require_stopped(db, item['id'])
            observation = _ReworkObservation(self.s, db, plan)
            proof, ledger = observation.proof, self.s.work_file_handoff_ledger
            if len(proof['edges']) >= MAX_DEPTH:
                raise ValueError('保存路径最多32条边。')
            last = ledger.get(db, APPLICATION_KIND, proof['edges'][-1])['snapshot']
            auth = self.s.work_units.authorization(db, driver)
            frontier = self.s.work_units.frontier(db, item['id'], _read=observation)
            if (auth != last['authorization'] or request['authorization'] != self.s.work_units.auth_ref(auth) or
                    auth['plan_id'] != plan['id'] or auth['plan_version'] != plan['version'] or
                    auth['item_id'] != item['id'] or auth['driver_generation'] != driver['generation'] or
                    driver['id'] != last['paused_driver']['id'] or
                    driver['generation'] != last['paused_driver']['generation'] or request['frontier'] != frontier):
                raise ValueError('文件返工须保持最后准确转移授权、同generation责任及完整前沿。')
            rows = self.s.work_units.rows(db, item['id'])
            projection = project_sources(self.s, db, plan, request, rows, observation.evidence, proof)
            root = ledger.get(db, APPLICATION_KIND, proof['edges'][0])['snapshot']['lineage']['proof']
            resources = work_file_sources.resource_status(self.s, db, observation.paths)
            if any(not r['valid'] for r in resources):
                raise ValueError('文件返工原路径成员、连接或额度来源已变化。')
            files = [original_files(self.s, db, r) for r in rows]
            if any(f['input_problems'] or f['output_state'] == 'DAMAGED' for f in files):
                raise ValueError('文件返工原实际输入或全部旧快照损坏。')
            bindings = work_file_sources.replacement_candidates(self.s, db, plan, rows,
                set(projection['affected_units']), files)
            names = sorted({n for paths in projection['write_scope'].values() for n in paths})
            baseline = self.s.work_file_rework_applications.observe(root['project_root'], names)
            if baseline['missing']:
                raise ValueError('首批明确文件重做要求当前声明文件完整。')
            for entry in projection['units']:
                if entry['key'] not in projection['write_scope'] or not entry['prior_source']:
                    continue
                manifest = entry['prior_source']['evidence']['file_artifacts']['manifest']
                if any(baseline['manifest'].get(n) != manifest.get(n) for n in projection['write_scope'][entry['key']]):
                    raise ValueError('写前观察与准确当前文件证据不一致。')
            file_audit = {'original_files': files,
                'current_project_observations': work_file_sources.current_observations(self.s, db, rows),
                'replacement_candidates': bindings, 'write_scope': projection['write_scope'],
                'write_before_observation': baseline}
            audit = {'protocol': CHECKPOINT, 'base_plan': plan_ref(plan), 'item_id': item['id'],
                'item_revision': item['revision'], 'project_root': root['project_root'], 'relation': root['relation'],
                'plans': proof['plans'], 'parent_application': proof['edges'][-1], 'root_application': proof['edges'][0],
                **{k: root[k] for k in ('historical_source', 'completed_target', 'delivery', 'completion')},
                'resources': resources, 'protected_keys': projection['protected_keys'],
                'protected_history': ledger.history(db, rows), 'frontier': proof['frontier'],
                'candidate_units': plan['units'], 'selections': projection['selections'],
                'affected_units': projection['affected_units'], 'file_audit': file_audit}
            audit['version'] = digest(audit)
            blockers = []
            if len(rows) >= auth['max_attempts_total']:
                blockers.append('原累计授权次数不足；文件返工不增加授权')
            if time.time() >= auth['dispatch_before']:
                blockers.append('原新增派发截止已到期；文件返工不延长截止')
            _, issues = self.s.work_drivers.readiness(db, item, driver)
            blockers.extend(i['message'] for i in issues)
            body = {'protocol': PREVIEW, 'request': request, 'base_plan': plan_ref(plan),
                'item_revision': item['revision'], 'driver': self.s.work_drivers.reference(driver),
                'authorization': auth, 'frontier': frontier, 'reason': projection['reason'],
                'candidate_units': plan['units'], **{k: projection[k] for k in
                    ('selections', 'units', 'affected_units', 'eligible_results', 'write_scope')},
                'ancestry_audit': audit, 'completed_target': proof['completed_key'], 'retired_source': proof['retired_source'],
                'usage': self.s.work_units.usage(db, rows), 'execution_blockers': blockers,
                'save_permission': False, 'current_input_permission': False, 'execution_permission': False,
                'scope': 'INTERNAL_FIXED_CURRENT_FILE_REDO; NO_SAVE_OR_DISPATCH',
                'independent_review': 'NOT_PERFORMED', 'human_acceptance': 'NOT_RECORDED'}
            observation.require(self.s.work_file_handoff_applications, db, plan)
            return deepcopy({**body, 'version': digest(body)})

    def recheck(self, db, expected):
        self.s.work_file_handoff_ledger.require_version(expected, PREVIEW)
        current = self.preview(db, expected['request'])
        if current != expected:
            raise ValueError('文件返工候选的完整历史、当前材料、基线或操作资格已变化。')
        return current

    def match_proposed_history(self, db, preview, run_id):
        """A proposed diagnostic only. Does not validate or authorize a saved edge."""
        ledger = self.s.work_file_handoff_ledger
        ledger.require_version(preview, PREVIEW)
        audit = preview['ancestry_audit']; ledger.require_version(audit, CHECKPOINT)
        plan = self.s.collaboration.get(db, 'teamwork_plan', preview['base_plan']['id'])
        if plan_ref(plan) != preview['base_plan'] or audit['base_plan'] != preview['base_plan']:
            raise ValueError('历史候选原方案版本不一致。')
        rows = self.s.work_units.rows(db, plan['item_id'])
        if ledger.history(db, rows) != audit['protected_history']:
            raise ValueError('历史候选的全部原运行、实际输入或全部旧快照已变化。')
        files = [original_files(self.s, db, r) for r in rows]
        saved = audit['file_audit']
        bindings = work_file_sources.replacement_candidates(self.s, db, plan, rows,
            set(preview['affected_units']), files)
        if files != saved['original_files'] or bindings != saved['replacement_candidates']:
            raise ValueError('历史候选的准确文件替代绑定或完整保存版本已变化。')
        run = next((r for r in rows if r['id'] == run_id), None)
        binding = next((b for b in bindings if b['run_id'] == run_id), None)
        expected = next((e['evidence'] for e in audit['frontier'] if e['run_id'] == run_id), None)
        if not run or not binding or not expected:
            raise ValueError('历史候选只核对本次准确受影响文件来源。')
        # evidence() hashes the complete body with the original base version.
        if (digest({**expected, 'version': expected['result_version']}) != expected['version'] or
                self.s.work_artifacts.reference(self.s.work_artifacts.current(db, run_id)) !=
                expected['file_artifacts']['reference']):
            raise ValueError('历史候选的完整旧证据正文或准确产物指针已变化。')
        match = work_file_sources.historical_match(self.s, db, run, expected['version'], binding,
            next(f for f in files if f['run_id'] == run_id))
        return deepcopy({'state': 'PROPOSED_HISTORICAL_MATCH_ONLY', 'run_id': run_id, 'match': match,
            'save_permission': False, 'current_input_permission': False, 'execution_permission': False})
