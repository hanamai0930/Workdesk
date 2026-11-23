"""Internal ordinary add/revise candidates on a complete fixed handoff path.

No commands, plan writes, authorization transfers or execution admission.
"""
from copy import deepcopy
import time

from .contracts import bounded_text
from .plans import PlanCommands
from .store import digest
from .work_file_handoff_ancestry import WorkFileHandoffAncestry
from .work_file_handoff_apply import APPLICATION_KIND
from .work_file_handoff_chain import MAX_DEPTH
from .work_file_handoff_text_reworks import _ReworkObservation
from .work_file_sources import resource_status
from .work_transitions import plan_ref, source_ref
from .work_unit_revisions import changes, allowed_members

PREVIEW = 'FIXED_FILE_HANDOFF_UNIT_REVISION_PREVIEW_V1'
CHECKPOINT = 'FIXED_FILE_HANDOFF_UNIT_REVISION_CHECKPOINT_V1'
CANDIDATE = 'FIXED_FILE_HANDOFF_UNIT_REVISION_SAVED_CANDIDATE_V1'
APPLICATION = 'FIXED_FILE_HANDOFF_UNIT_REVISION_APPLICATION_V1'
CONSUMPTION = 'FIXED_FILE_HANDOFF_UNIT_REVISION_CONSUMPTION_V1'
PATH = 'FIXED_FILE_HANDOFF_UNIT_REVISION_SAVED_PATH_V1'
INPUT = 'FIXED_FILE_HANDOFF_UNIT_REVISION_INPUT_V1'
FIELDS = {'id', 'revision', 'current_driver', 'plan', 'authorization', 'frontier', 'changes', 'reason'}
TRANSITION = 'EXPLICIT_FIXED_FILE_HANDOFF_UNIT_REVISION'
PAUSE_REASON = 'FILE_HANDOFF_UNIT_REVISED_EXPLICIT_RECHECK_REQUIRED'


def historical_keys(service, db, item_id, *, before=None):
    # A saved edge rechecks the historical set at its own save time. Later
    # descendants contain the added key and must not invalidate that old edge.
    return sorted({u['key'] for p in service.collaboration.all(db, 'teamwork_plan')
        if p['item_id'] == item_id and (before is None or p['created'] < before)
        for u in p['units']})


def ordinary(unit):
    return (unit['role'] != 'coordinate' and not unit['outputs'] and not unit['checks']
            and not unit['write_paths'] and unit['dependency_gate'] == 'result')


def project_revision(service, plan, request, rows, evidence, proof, history):
    changes(request['changes'])
    reason = bounded_text(request['reason'], '明确普通文字修订依据', 2000)
    original = {u['key']: u for u in plan['units']}
    members = [plan['coordinator'], plan['integrator'], *(u['member'] for u in plan['units'])]
    frozen = {}
    for member in members:
        key = digest(member['reference'])
        if key in frozen and frozen[key] != member:
            raise ValueError('原方案相同资源引用的完整成员对象不一致。')
        frozen[key] = member
    replacements, additions, direct = {}, [], {}
    for change in request['changes']:
        name, basis, definition = change['unit_key'], change['basis'], change['definition']
        source = original.get(basis['unit_key'])
        if (not source or not ordinary(source) or basis['unit_revision'] != digest(source) or
                basis['work_requirements_version'] != digest(plan['work_requirements'])):
            raise ValueError('普通文字修订须引用当前准确原普通文字职责和任务要求。')
        if change['operation'] == 'add':
            if name in history or name in original:
                raise ValueError('新增单元不能复用本任务任意历史方案身份。')
        elif name != source['key']:
            raise ValueError('修订依据须是同key的准确原定义。')
        member = frozen.get(digest(definition['member']))
        if member is None or member['reference'] != definition['member']:
            raise ValueError('只能选择原方案已冻结的完整成员/连接/额度引用。')
        unit = {**source, 'key': name, 'member': member, 'depends': sorted(definition['depends']),
            **{k: bounded_text(definition[k], '局部' + k, limit)
               for k, limit in [('title',160), ('goal',4000), ('acceptance',4000)]}}
        if change['operation'] == 'revise':
            if {**source, 'depends': sorted(source['depends'])} == unit:
                raise ValueError('局部修订须实际改变定义，单纯排序不构成修订。')
            replacements[name] = unit
        else:
            additions.append(unit)
        direct[name] = change
    candidate = [replacements.get(u['key'], u) for u in plan['units']] + additions
    if len(candidate) > 20:
        raise ValueError('完整候选最多20项工作单元。')
    PlanCommands.validate_graph({u['key']:u['depends'] for u in candidate})
    retired, completed = proof['retired_source'], proof['completed_key']
    protected = (WorkFileHandoffAncestry.protected_keys(plan['units'], [retired,completed]) |
                 WorkFileHandoffAncestry.protected_keys(candidate, [retired,completed]))
    sources, by_id = {}, {r['id']: r for r in rows}
    for unit in plan['units']:
        name = unit['key']
        if name == retired:
            sources[name] = None
            continue
        local = [r for r in rows if r['plan_id'] == plan['id'] and r['unit_key'] == name]
        inherited = plan.get('result_lineage', {}).get(name)
        run = local[0] if local else by_id.get(inherited['run_id']) if inherited else None
        if len(local) > 1 or (inherited and not run):
            raise ValueError('普通文字修订来源缺失或同修订重复运行。')
        raw = evidence[run['id']] if run else None
        if run and (run['unit_key'] != name or run['input']['unit'] != unit or
                    (not local and source_ref(run, raw) != inherited)):
            raise ValueError('普通文字修订须保持准确原定义/运行/证据。')
        sources[name] = ({'source_ref':source_ref(run, raw), 'original_definition':unit,
                         'input':run['input'], 'evidence':raw} if run else None)
    missing = {k for k,v in sources.items() if v and not v['evidence']['result_available']}
    reasons = {u['key']: (['UNIT_ADDED'] if u['key'] not in original else
               ['DEFINITION_CHANGED'] if u['key'] in replacements else []) +
               (['KNOWN_TERMINAL_WITHOUT_RESULT'] if u['key'] in missing else []) for u in candidate}
    affected = set(direct) | missing
    while True:
        more = {u['key'] for u in candidate if
            set(u['depends'] + original.get(u['key'], {}).get('depends', [])) & affected} - affected
        if not more:
            break
        for name in more: reasons[name].append('AFFECTED_DEPENDENCY')
        affected.update(more)
    if affected & protected:
        raise ValueError('普通文字修订不能影响交接两端及原/新图完整上游。')
    entries = []
    for unit in candidate:
        name = unit['key']; source = sources.get(name); change = direct.get(name)
        if name in affected and (not ordinary(unit) or retired in unit['depends']):
            raise ValueError('新增/影响须限普通文字结果且不能读取退役前版：' + name)
        state = ('HISTORICAL_RETIRED' if name == retired else 'ADDED' if name not in original else
                 'AFFECTED' if name in affected else 'ELIGIBLE_RESULT' if source else 'NOT_RUN')
        if state == 'ELIGIBLE_RESULT':
            service.work_artifacts.require(source['evidence'],
                'checked' if unit['checks'] else 'files' if unit['outputs'] else 'result')
        entries.append({'key':name, 'operation':change['operation'] if change else None,
            'basis':change['basis'] if change else None, 'original_definition':original.get(name),
            'candidate_definition':unit, 'state':state,
            'source':source['source_ref'] if state == 'ELIGIBLE_RESULT' else None,
            'prior_source':source if state == 'AFFECTED' else None, 'reasons':reasons[name],
            'historical_source':proof['historical_source'] if name == retired else None})
    return deepcopy({'candidate_units':candidate, 'units':entries, 'reason':reason,
        'affected_units':[u['key'] for u in candidate if u['key'] in affected],
        'added_units':[u['key'] for u in additions], 'revised_units':list(replacements),
        'direct_changes':[u['key'] for u in candidate if u['key'] in direct],
        'eligible_results':[e['key'] for e in entries if e['state'] == 'ELIGIBLE_RESULT'],
        'protected_keys':sorted(protected)})


def build_preview(service, db, plan, request, driver, auth, frontier, paths, proof, rows, evidence, history):
    projection = project_revision(service, plan, request, rows, evidence, proof, history)
    ledger = service.work_file_handoff_ledger
    root = ledger.get(db, APPLICATION_KIND, proof['edges'][0])['snapshot']['lineage']['proof']
    resources = resource_status(service, db, paths)
    if any(not r['valid'] for r in resources):
        raise ValueError('普通文字修订原路径成员、连接或额度来源已变化。')
    audit = {'protocol':CHECKPOINT, 'base_plan':plan_ref(plan), 'item_id':plan['item_id'],
        'item_revision':request['revision'], 'project_root':root['project_root'], 'relation':root['relation'],
        'plans':proof['plans'], 'parent_application':proof['edges'][-1], 'root_application':proof['edges'][0],
        **{k:root[k] for k in ('historical_source','completed_target','delivery','completion')},
        'resources':resources, 'protected_keys':projection['protected_keys'], 'historical_keys':history,
        'allowed_members':allowed_members(plan), 'protected_history':ledger.history(db, rows),
        'frontier':proof['frontier'], 'request':request,
        **{k:projection[k] for k in ('candidate_units','affected_units')}}
    audit['version'] = digest(audit)
    # These blockers describe the observation time. A saved edge instead checks
    # its own creation time and cumulative history in the complete path verifier.
    body = {'protocol':PREVIEW, 'request':request, 'base_plan':plan_ref(plan),
        'item_revision':request['revision'], 'driver':driver, 'authorization':auth, 'frontier':frontier,
        **{k:projection[k] for k in ('reason','candidate_units','units','affected_units',
                                   'added_units','revised_units','direct_changes','eligible_results')},
        'ancestry_audit':audit, 'completed_target':proof['completed_key'], 'retired_source':proof['retired_source'],
        'usage':service.work_units.usage(db, rows), 'execution_blockers':[],
        'save_permission':False, 'current_input_permission':False, 'execution_permission':False,
        'scope':'INTERNAL_EXPLICIT_FIXED_UNIT_REVISION; NO_SAVE_OR_DISPATCH',
        'independent_review':'NOT_PERFORMED', 'human_acceptance':'NOT_RECORDED'}
    return body


class _UnitRevisionObservation(_ReworkObservation):
    def __init__(self, service, db, plan):
        from .work_file_handoff_unit_revision_path import WorkFileHandoffUnitRevisionPath
        self.s, self.db, self.plan = service, db, deepcopy(plan)
        chain = WorkFileHandoffUnitRevisionPath(service)
        self.proof = chain.verify(db, plan); self.paths = chain._path(db, plan)
        self.changes = db.total_changes
        self.evidence = {e['run_id']:e['evidence'] for e in self.proof['frontier']}


class WorkFileHandoffUnitRevisions:
    protocol = PREVIEW

    def __init__(self, service): self.s = service

    def preview(self, db, request):
        with self.s.work_unit_revision_applications.validation_snapshot(db):
            if not isinstance(request, dict) or set(request) != FIELDS:
                raise ValueError('普通文字修订只接受准确任务、责任、方案、授权、前沿、改动和依据。')
            item, driver, plan = self.s.work_units.checked(db, request)
            self.s.work_flows.require_stopped(db, item['id'])
            observation = _UnitRevisionObservation(self.s, db, plan); proof = observation.proof
            if len(proof['edges']) >= MAX_DEPTH:
                raise ValueError('保存路径最多32条边，不能再增加修订边。')
            last = self.s.work_file_handoff_ledger.get(db, APPLICATION_KIND, proof['edges'][-1])['snapshot']
            auth = self.s.work_units.authorization(db, driver)
            frontier = self.s.work_units.frontier(db, item['id'], _read=observation)
            if (auth != last['authorization'] or request['authorization'] != self.s.work_units.auth_ref(auth) or
                    auth['plan_id'] != plan['id'] or auth['plan_version'] != plan['version'] or
                    auth['item_id'] != item['id'] or auth['driver_generation'] != driver['generation'] or
                    driver['id'] != last['paused_driver']['id'] or
                    driver['generation'] != last['paused_driver']['generation'] or request['frontier'] != frontier):
                raise ValueError('普通文字修订须保持最后转移授权、同generation责任与完整前沿。')
            rows = self.s.work_units.rows(db, item['id'])
            body = build_preview(self.s, db, plan, request, self.s.work_drivers.reference(driver), auth, frontier,
                observation.paths, proof, rows, observation.evidence, historical_keys(self.s, db, item['id']))
            if len(rows) >= auth['max_attempts_total']:
                body['execution_blockers'].append('原累计授权次数不足；普通文字修订不增加授权')
            if time.time() >= auth['dispatch_before']:
                body['execution_blockers'].append('原新增派发截止已到期；普通文字修订不延长截止')
            observation.require(self.s.work_file_handoff_applications, db, plan)
            return deepcopy({**body, 'version':digest(body)})

    def recheck(self, db, expected):
        if (not isinstance(expected, dict) or expected.get('protocol') != PREVIEW or
                expected.get('version') != digest({k:v for k,v in expected.items() if k != 'version'})):
            raise ValueError('普通文字修订候选完整版本不一致。')
        current = self.preview(db, expected['request'])
        if current != expected:
            raise ValueError('普通文字修订的完整来源、定义、影响、历史或操作资格已变化。')
        return current
