"""Independent FILE saved-path history. Public observations never grant dispatch.

Only persisted, fully rebuilt FILE edges establish historical replacements.
Legacy validators and current evidence remain separate and unchanged.
"""
from copy import deepcopy

from .store import digest
from .work_file_handoff_unit_revision_path import WorkFileHandoffUnitRevisionPath
from .work_file_handoff_file_reworks import (
    PREVIEW, CHECKPOINT, CANDIDATE, APPLICATION, CONSUMPTION, PATH, INPUT, FIELDS, project_sources)
from .work_file_handoff_apply import APPLICATION_KIND, CANDIDATE_KIND
from .work_file_sources import resource_status
from .work_transitions import plan_ref, source_ref
from .work_file_reworks import original_files
from . import work_file_sources


TRANSITION = 'EXPLICIT_FIXED_FILE_HANDOFF_FILE_REWORK'
PAUSE_REASON = 'FILE_HANDOFF_FILE_REWORKED_EXPLICIT_RECHECK_REQUIRED'


def input_context(service, db, plan, unit):
    if plan.get('file_handoff_lineage', {}).get('protocol') != APPLICATION:
        return None, None
    path = WorkFileHandoffFileReworkPath(service)
    parent = service.collaboration.get(db, 'teamwork_plan', plan['previous_plan_reference']['id'])
    edge = path._saved_edge(db, parent, plan)
    context, baseline = path.run_context(unit, edge)[1], path.unit_baseline(unit, edge)
    if (unit['outputs'] or unit['checks'] or unit['write_paths']) and baseline is None:
        raise ValueError('当前FILE仅开放本次应用受影响的准确文件职责；其它文件须另行明确。')
    return context, baseline


class WorkFileHandoffFileReworkPath(WorkFileHandoffUnitRevisionPath):
    path_protocol = PATH

    def explicit_protocols(self):
        return (*super().explicit_protocols(), APPLICATION)

    def edge_contract(self, protocol):
        if protocol == APPLICATION:
            return PREVIEW, CHECKPOINT, CANDIDATE, APPLICATION, CONSUMPTION, TRANSITION, PAUSE_REASON
        return super().edge_contract(protocol)

    def verify(self, db, plan):
        # Reusing this reader never reuses a previous observation or approvals.
        with self.s.work_unit_revision_applications.validation_snapshot(db):
            fresh = type(self)(self.s)
            try:
                return fresh._verify(db, plan)
            except (KeyError, TypeError, AttributeError, StopIteration, IndexError) as exc:
                raise ValueError('FILE保存路径完整记录、绑定或历史形状不一致。') from exc

    def _saved_edge(self, db, parent, plan):
        edge = super()._saved_edge(db, parent, plan)
        if plan['file_handoff_lineage']['protocol'] != APPLICATION:
            return edge
        candidate = self.ledger.get(db, CANDIDATE_KIND, edge['candidate'])
        application = self.ledger.get(db, APPLICATION_KIND, edge['application'])['snapshot']
        baseline = edge['audit']['file_audit']['write_before_observation']
        if (candidate['snapshot'] != {'protocol': CANDIDATE, 'item_id': plan['item_id'],
                                      'preview': edge['preview']} or
                set(application) != {'protocol', 'item_id', 'lineage', 'to_plan', 'authorization',
                    'paused_driver', 'created', 'command_id', 'no_dispatch', 'write_before_baseline'} or
                application['write_before_baseline'] != baseline):
            raise ValueError('FILE边不可变候选、应用完整字段或准确基线不一致。')
        self.s.work_file_rework_applications.require_baseline(baseline)
        return edge

    def run_context(self, unit, edge):
        if edge['preview']['protocol'] != PREVIEW:
            return super().run_context(unit, edge)
        preview = edge['preview']
        context = None
        if unit['key'] in preview['affected_units']:
            entry = next(e for e in preview['units'] if e['key'] == unit['key'])
            context = deepcopy({'protocol': INPUT, 'application': edge['application'],
                'from_plan': preview['base_plan'], 'unit_key': unit['key'],
                'prior_source': entry['prior_source'],
                'write_scope': preview['write_scope'].get(unit['key'], []),
                'baseline': {'version': preview['ancestry_audit']['file_audit']['write_before_observation']['version']}})
        return 'handoff_file_rework_context', context

    def unit_baseline(self, unit, edge):
        if edge['preview']['protocol'] != PREVIEW or unit['key'] not in edge['preview']['write_scope']:
            return None
        original = edge['audit']['file_audit']['write_before_observation']
        names = edge['preview']['write_scope'][unit['key']]
        value = {**deepcopy(original), 'paths': names,
                 'manifest': {n: original['manifest'][n] for n in names}, 'missing': []}
        value.pop('version')
        return {**value, 'version': digest(value)}

    def _local(self, db, plan, run, edge, root):
        if plan['file_handoff_lineage']['protocol'] != APPLICATION:
            return super()._local(db, plan, run, edge, root)
        self.s.work_file_handoff_applications.require_local_identity(db, plan, run)
        from .native_permissions import choice
        task = self.s.store.task(run['task_id'], db); spec = task['spec']
        unit = next(u for u in plan['units'] if u['key'] == run['unit_key'])
        frozen = run['input']; context_key, context = self.run_context(unit, edge)
        baseline = self.unit_baseline(unit, edge)
        keys = {'item_id','item_revision','plan_id','plan_version','work_requirements','unit',
                'dependencies','check_evidence','file_artifacts'}
        if context is not None:
            keys.add(context_key)
        if baseline is not None:
            keys.add('write_before_baseline')
            self.s.work_file_rework_applications.require_baseline(baseline)
        driver = self.s.collaboration.get(db, 'work_driver_revision', 'driver-version:' + run['driver_revision'])
        cooperative = {k:run[k] for k in ('item_id','driver_id','generation','driver_revision','plan_id',
            'plan_version','unit_key','unit_revision','input_version','authorization')}
        cooperative['source_item_revision'] = run['item_revision']
        selected = self.s.resources.selection(db, unit['member']['reference']['member_id'])
        if (set(frozen) != keys or frozen.get(context_key) != context or
                frozen.get('write_before_baseline') != baseline or
                (bool(unit['outputs'] or unit['checks'] or unit['write_paths']) and baseline is None) or
                (baseline is not None and (sorted(unit['outputs']) != baseline['paths'] or
                    sorted(unit['write_paths']) != baseline['paths'])) or
                run['cooperative'] != cooperative or frozen['item_revision'] != run['item_revision'] or
                run['driver_id'] != edge['paused_driver']['id'] or
                driver['driver_id'] != run['driver_id'] or driver['generation'] != run['generation'] or
                driver['revision'] != run['driver_revision'] or driver['state'] != 'RESERVED' or
                driver['item_id'] != run['item_id'] or driver['item_revision'] != run['item_revision'] or
                driver['plan_id'] != plan['id'] or driver['plan_version'] != plan['version'] or
                run['flow'] is not None or run['scheduling'] != 'EXPLICIT_SERIAL_STEP' or
                any(frozen[k] != run[k] for k in ('item_id','plan_id','plan_version')) or
                run['unit_key'] in {r[k] for r in plan['file_handoffs'] for k in ('source_unit','target_unit')} or
                unit['role'] == 'coordinate' or run['retry_of'] is not None or
                run['authorization'] != self.s.work_units.auth_ref(edge['authorization']) or
                run['generation'] != edge['paused_driver']['generation'] or
                selected['reference'] != unit['member']['reference'] or
                any(spec.get(k) != selected['member'][k] for k in ('provider','model','effort','tier')) or
                spec.get('execution_connection') != {k:selected['connection'][k]
                    for k in ('id','provider','credential_reference')} or
                task['project_id'] != plan['project_id'] or spec.get('runtime_mode') != 'official' or
                spec.get('work_mode') != 'cooperative' or spec.get('access') != choice(None) or
                spec.get('native') != {'configuration':'VENDOR_DEFAULTS'} or spec.get('budget') != {} or
                spec.get('writable') != [] or spec.get('checks') != [] or
                digest(spec['execution_connection']) != run['connection_fingerprint'] or
                self.s.store.attempt(run['attempt_id'], db)['workspace'] != root):
            raise ValueError('FILE运行的完整上下文、逐单元基线、原声明、成员权限或授权身份不一致。')

    def guard_capture(self, db, plan, run):
        # Verify all saved history, including this terminal run's frozen inputs.
        # Its own new file snapshot is deliberately not a prerequisite.
        proof = self.verify(db, plan)
        if (run['plan_id'] != plan['id'] or
                run['unit_key'] in {proof['retired_source'], proof['completed_key']} or
                not self.s.work_units.base_evidence(db, run)['result_available']):
            raise ValueError('FILE捕获须为当前准确成功退出的新运行。')
        return proof

    def finish_capture(self, db, plan, run, artifact):
        self.guard_capture(db, plan, run)
        evidence = self.s.work_units.evidence(db, run)
        files = evidence['file_artifacts']
        if (files['state'] != 'CURRENT' or files['reference'] != self.s.work_artifacts.reference(artifact) or
                files['manifest'] != artifact['snapshot']['manifest'] or
                files['check_status'] != artifact['snapshot']['check_status']):
            raise ValueError('FILE捕获最终事件后准确产物、检查或当前文件已变化；未登记。')

    def _prepare_files(self, db, path, edges, rows):
        """Check persisted identities and exact bindings before historical use."""
        approved = []
        self._file_edges = []
        for index, (parent, child, edge) in enumerate(zip(path, path[1:], edges)):
            if child['file_handoff_lineage']['protocol'] != APPLICATION:
                continue
            prior = [r for r in rows if r['plan_id'] in {p['id'] for p in path[:index+1]}]
            audit = edge['audit']; preview = edge['preview']; saved = audit['file_audit']
            files = [original_files(self.s, db, r) for r in prior]
            bindings = work_file_sources.replacement_candidates(self.s, db, parent, prior,
                set(preview['affected_units']), files)
            if (audit['protected_history'] != self.ledger.history(db, prior) or
                    saved['original_files'] != files or saved['replacement_candidates'] != bindings or
                    [e['run_id'] for e in audit['frontier']] != [r['id'] for r in prior]):
                raise ValueError('FILE边全部旧运行、输入、快照或替代绑定已变化。')
            prefix = {r['id'] for r in prior}
            target_id = edges[0]['audit']['completed_target']['source']['run_id']
            target_index = next(i for i, r in enumerate(prior) if r['id'] == target_id)
            for binding in bindings:
                run = next(r for r in prior if r['id'] == binding['run_id'])
                if prior.index(run) <= target_index:
                    raise ValueError('FILE首批全部替代来源须在根B运行之后。')
                expected = next(e['evidence'] for e in audit['frontier'] if e['run_id'] == run['id'])
                self._historical(db, run, expected, binding, next(f for f in files if f['run_id'] == run['id']))
                approved.append({'binding': binding, 'files': next(f for f in files if f['run_id'] == run['id']),
                                 'prefix': prefix, 'index': index, 'application': edge['application']})
            self._file_edges.append((index, saved))
        self._approved = approved
        # No binding becomes usable until each FILE candidate is fully rebuilt.
        for index, (parent, child, edge) in enumerate(zip(path, path[1:], edges)):
            if child['file_handoff_lineage']['protocol'] == APPLICATION:
                prior = [r for r in rows if r['plan_id'] in {p['id'] for p in path[:index+1]}]
                frozen = {e['run_id']: e['evidence'] for e in edge['audit']['frontier']}
                self._rework_candidate(db, path[:index+1], edges[:index], parent, child,
                    edge['preview'], edge['audit'], prior, frozen, edges[0]['audit'])

    def _historical(self, db, run, expected, binding, files):
        if (digest({**expected, 'version': expected['result_version']}) != expected['version'] or
                self.s.work_artifacts.reference(self.s.work_artifacts.current(db, run['id'])) !=
                expected['file_artifacts']['reference']):
            raise ValueError('FILE完整旧证据正文或准确产物current指针已变化。')
        return work_file_sources.historical_match(self.s, db, run, expected['version'], binding, files)

    def _match_evidence(self, db, run, expected, *, edge_index=None, consumer=None):
        live = self.s.work_units.evidence(db, run)
        if live == expected:
            return {'kind': 'CURRENT_UNCHANGED', 'version': live['version']}
        matches = [a for a in self._approved if a['binding']['run_id'] == run['id'] and
                   (edge_index is None or a['index'] >= edge_index) and
                   (consumer is None or consumer['id'] in a['prefix'])]
        if not matches:
            raise ValueError('旧证据变化没有更晚正式FILE边的准确替代关系。')
        return self._historical(db, run, expected, matches[0]['binding'], matches[0]['files'])

    def _edge_evidence(self, db, rows, edge, index):
        frontier = edge['audit']['frontier']
        if [e['run_id'] for e in frontier] != [r['id'] for r in rows]:
            raise ValueError('旧边完整运行前沿及顺序不一致。')
        values = {}
        for run, entry in zip(rows, frontier):
            self._match_evidence(db, run, entry['evidence'], edge_index=index)
            values[run['id']] = entry['evidence']
        return values

    def _rework_candidate(self, db, paths, edges, parent, child, preview, audit, rows, evidence, root):
        if child['file_handoff_lineage']['protocol'] != APPLICATION:
            return super()._rework_candidate(db, paths, edges, parent, child, preview, audit, rows, evidence, root)
        request = preview['request']
        raw = [{'run_id': r['id'], 'evidence': evidence[r['id']]['version']} for r in rows]
        lineage = []
        for key, reference in parent.get('result_lineage', {}).items():
            local = [r for r in rows if r['plan_id'] == parent['id'] and r['unit_key'] == key]
            run_id = local[0]['id'] if local else reference['run_id']
            lineage.append({'key': key, 'source': None if local else reference, 'problems': [],
                            'evidence': evidence[run_id]['version']})
        frontier = digest({'runs': raw, 'plan': plan_ref(parent), 'lineage': lineage}) if lineage else digest(raw)
        if (set(request) != FIELDS or request['id'] != parent['item_id'] or
                request['revision'] != preview['item_revision'] or request['plan'] != plan_ref(parent) or
                request['current_driver'] != preview['driver'] or
                request['authorization'] != self.s.work_units.auth_ref(preview['authorization']) or
                request['frontier'] != frontier or preview['frontier'] != frontier or child['units'] != parent['units']):
            raise ValueError('FILE边须绑定封闭原请求、全部原定义和准确前沿。')
        proof = {'retired_source': root['relation']['source_unit'], 'completed_key': root['relation']['target_unit'],
                 **{k: root[k] for k in ('historical_source', 'completed_target')}}
        projection = project_sources(self.s, db, parent, request, rows, evidence, proof)
        saved = audit['file_audit']; baseline = saved['write_before_observation']
        names = sorted({n for paths in projection['write_scope'].values() for n in paths})
        manifest = {}
        for entry in projection['units']:
            if entry['key'] in projection['write_scope']:
                # NOT_RUN downstream has no old evidence to reconstruct. Its
                # declared bytes are protected solely by the immutable baseline.
                old = (entry['prior_source']['evidence']['file_artifacts']['manifest']
                       if entry['prior_source'] else baseline['manifest'])
                manifest.update({n: old[n] for n in projection['write_scope'][entry['key']]})
        expected_baseline = {'root': root['project_root'], 'paths': names, 'manifest': manifest, 'missing': [],
            'scope': 'EXACT_DECLARED_PATHS_ONLY; NOT_DIRECTORY_BACKUP_OR_OS_ISOLATION'}
        expected_baseline['version'] = digest(expected_baseline)
        if baseline != expected_baseline or set(manifest) != set(names):
            raise ValueError('FILE历史写前基线须匹配完整准确来源文件。')
        self.s.work_file_rework_applications.require_baseline(baseline)
        observations = work_file_sources.current_observations(self.s, db, rows)
        for observation in observations:
            for name in observation['outputs']:
                historical = next((f['write_before_observation'] for i, f in self._file_edges
                                   if i >= len(edges) and name in f['write_before_observation']['paths']), None)
                if historical:
                    observed = {name: historical['manifest'][name]} if name in historical['manifest'] else {}
                    observation['outputs'][name] = {'state': 'OBSERVED' if observed else 'MISSING', 'manifest': observed}
        file_audit = {'original_files': [original_files(self.s, db, r) for r in rows],
            'current_project_observations': observations,
            'replacement_candidates': work_file_sources.replacement_candidates(self.s, db, parent, rows,
                set(projection['affected_units']), [original_files(self.s, db, r) for r in rows]),
            'write_scope': projection['write_scope'], 'write_before_observation': baseline}
        expected_audit = {'protocol': CHECKPOINT, 'base_plan': plan_ref(parent), 'item_id': parent['item_id'],
            'item_revision': preview['item_revision'], 'project_root': root['project_root'], 'relation': root['relation'],
            'plans': [plan_ref(p) for p in paths], 'parent_application': edges[-1]['application'],
            'root_application': edges[0]['application'],
            **{k: root[k] for k in ('historical_source', 'completed_target', 'delivery', 'completion')},
            'resources': resource_status(self.s, db, paths), 'protected_keys': projection['protected_keys'],
            'protected_history': self.ledger.history(db, rows),
            'frontier': [{'run_id': r['id'], 'evidence': evidence[r['id']]} for r in rows],
            'candidate_units': parent['units'], 'selections': projection['selections'],
            'affected_units': projection['affected_units'], 'file_audit': file_audit}
        expected_audit['version'] = digest(expected_audit)
        expected = {'protocol': PREVIEW, 'request': request, 'base_plan': plan_ref(parent),
            'item_revision': preview['item_revision'], 'driver': preview['driver'],
            'authorization': preview['authorization'], 'frontier': frontier, 'reason': projection['reason'],
            'candidate_units': parent['units'], **{k: projection[k] for k in
                ('selections', 'units', 'affected_units', 'eligible_results', 'write_scope')},
            'ancestry_audit': expected_audit, 'completed_target': proof['completed_key'], 'retired_source': proof['retired_source'],
            'usage': self.s.work_units.usage(db, rows), 'execution_blockers': [], 'save_permission': False,
            'current_input_permission': False, 'execution_permission': False,
            'scope': 'INTERNAL_FIXED_CURRENT_FILE_REDO; NO_SAVE_OR_DISPATCH',
            'independent_review': 'NOT_PERFORMED', 'human_acceptance': 'NOT_RECORDED'}
        expected['version'] = digest(expected)
        if audit != expected_audit or preview != expected:
            raise ValueError('FILE边完整选择、影响、文件观察、来源或历史检查点不一致。')
        return set(projection['affected_units'])

    def _run(self, db, plan, run, rows, plans, evidence, root, trail):
        marker = (plan['id'], run['unit_key'])
        if marker in trail:
            raise ValueError('FILE历史运行依赖循环。')
        unit = next(u for u in plan['units'] if u['key'] == run['unit_key'])
        if plan['id'] == root['id']:
            if run['unit_key'] in {root['file_handoffs'][0]['source_unit'], root['file_handoffs'][0]['target_unit']}:
                return super()._run(db, plan, run, rows, plans, evidence, root, trail)
            by_id = {r['id']: r for r in rows}
            pending = [d['run_id'] for d in run['input']['dependencies']]
            seen = set()
            while pending:
                identity = pending.pop()
                if identity in seen:
                    continue
                seen.add(identity)
                if identity in by_id:
                    pending.extend(d['run_id'] for d in by_id[identity]['input']['dependencies'])
            if not seen.intersection(a['binding']['run_id'] for a in self._approved):
                return super()._run(db, plan, run, rows, plans, evidence, root, trail)
            self._root_identity(db, plan, unit, run, rows)
        frozen = run['input']['dependencies']
        if len(frozen) != len(unit['depends']) or {d['unit_key'] for d in frozen} != set(unit['depends']):
            raise ValueError('FILE历史冻结前置集合不一致。')
        for dep in frozen:
            raw = {k: v for k, v in dep.items() if k not in ('unit_key', 'lineage')}
            local = [r for r in rows if r['plan_id'] == plan['id'] and r['unit_key'] == dep['unit_key']]
            entry = plan.get('result_lineage', {}).get(dep['unit_key'])
            original = local[0] if len(local) == 1 else next((r for r in rows if entry and r['id'] == entry['run_id']), None)
            if (not original or len(local) > 1 or rows.index(original) >= rows.index(run) or
                    original['unit_key'] == root['file_handoffs'][0]['source_unit']):
                raise ValueError('FILE历史冻结依赖不是准确较早普通来源。')
            if not local and (original['plan_id'] not in plans or
                    list(plans).index(original['plan_id']) >= list(plans).index(plan['id']) or
                    next(u for u in plans[original['plan_id']]['units'] if u['key'] == dep['unit_key']) !=
                    next(u for u in plan['units'] if u['key'] == dep['unit_key']) or source_ref(original, raw) != entry):
                raise ValueError('FILE历史依赖沿袭或原定义不一致。')
            self._match_evidence(db, original, raw, consumer=run)
            expected_lineage = {'source': entry, 'consumer_plan': plan_ref(plan)} if original['plan_id'] != plan['id'] else None
            if dep.get('lineage') != expected_lineage:
                raise ValueError('FILE历史冻结依赖消费者或lineage不一致。')
            self._run(db, plans[original['plan_id']], original, rows, plans, evidence, root, trail | {marker})
            self.s.work_artifacts.require(raw, unit['dependency_gate'])
        if (run['input']['check_evidence'] != [{'unit_key': d['unit_key'], 'artifact': d['file_artifacts']['reference'],
                'check_status': d['check_status']} for d in frozen] or
                run['input']['file_artifacts'] != [{'unit_key': d['unit_key'], **d['file_artifacts']} for d in frozen
                    if d['file_artifacts']['state'] == 'CURRENT'] or original_files(self.s, db, run)['input_problems']):
            raise ValueError('FILE历史冻结文件、实际输入或检查引用不一致。')

    def _root_identity(self, db, plan, unit, run, rows):
        task = self.s.store.task(run['task_id'], db)
        if (run['item_id'] != plan['item_id'] or run['plan_id'] != plan['id'] or
                run['plan_version'] != plan['version'] or run['unit_revision'] != digest(unit) or
                run['input']['unit'] != unit or run['input_version'] != digest(run['input']) or
                run['input']['work_requirements'] != plan['work_requirements'] or
                # Original root rows predate the new-local spec field. Their
                # complete spec hash is also frozen in every edge's history.
                ('task_spec_version' in run and run['task_spec_version'] != digest(task['spec'])) or
                task['spec_hash'] != digest(task['spec'])):
            raise ValueError('FILE根普通运行完整spec、单元或输入身份不一致。')
        self.s.work_rework_applications.require_sources(db, plan, rows, consumer_plan=plan)
        self.s.work_unit_revision_applications.require_sources(db, plan, rows, consumer_plan=plan)
        if (run['input'].get('file_rework_context') != self.s.work_file_rework_applications.input_context(db, plan, unit, rows) or
                run['input'].get('rework_context') != self.s.work_rework_applications.input_context(plan, unit) or
                run['input'].get('unit_revision_context') != self.s.work_unit_revision_applications.input_context(plan, unit)):
            raise ValueError('FILE根普通运行原上下文不一致。')
        if run['input'].get('write_before_baseline'):
            self.s.work_file_rework_applications.require_baseline(run['input']['write_before_baseline'])

    def _verify(self, db, plan):
        path = self._path(db, plan)
        edges = [self._saved_edge(db, p, c) for p, c in zip(path, path[1:])]
        if len({e['application']['id'] for e in edges}) != len(edges):
            raise ValueError('祖先路径重复应用边。')
        root = path[0]; root_audit = edges[0]['audit']; relation = root_audit['relation']
        if self.ledger.relation(root, relation['target_unit']) != relation:
            raise ValueError('祖先根交接关系不一致。')
        item = self.s.work_items.item(db, plan['item_id'])
        project_root = self.s.work_file_rework_applications.root(db, item)
        if (item['project_id'] != root['project_id'] or
                {k: item[k] for k in ('title', 'goal', 'constraints_text', 'acceptance')} != root['work_requirements'] or
                project_root != root_audit['project_root']):
            raise ValueError('祖先链原任务要求、项目或实际目录已变化。')
        rows = self.s.work_units.rows(db, plan['item_id'])
        by_plan = {p['id']: p for p in path}
        if (any(r['plan_id'] not in by_plan for r in rows) or len({r['id'] for r in rows}) != len(rows) or
                len({(r['plan_id'],r['unit_key']) for r in rows}) != len(rows)):
            raise ValueError('存在非当前祖先路径运行或重复运行。')
        # Complete shape/spec/permission checks precede file-history inspection.
        for r in rows:
            if r['plan_id'] != root['id']:
                self._local(db, by_plan[r['plan_id']], r, edges[path.index(by_plan[r['plan_id']])-1], project_root)
        terminal = self._terminal_rows(db, plan, rows)
        history = self.ledger.history(db, terminal)
        history_by_run = {h['run_id']:h for h in history}
        if any(h['workspace'] != project_root for h in history):
            raise ValueError('祖先运行实际目录不一致。')
        evidence = {r['id']: self.s.work_units.evidence(db, r) for r in terminal}
        self._prepare_files(db, path, edges, rows)
        historical_checks = []
        for index, (parent, child, edge) in enumerate(zip(path, path[1:], edges)):
            audit, preview = edge['audit'], edge['preview']
            prior_ids = {p['id'] for p in path[:index+1]}
            prior = [r for r in rows if r['plan_id'] in prior_ids]
            edge_evidence = self._edge_evidence(db, prior, edge, index)
            historical_checks.append({'application': edge['application'], 'state': 'MATCHED',
                'frontier': [{'run_id': r['id'], 'version': edge_evidence[r['id']]['version']} for r in prior]})
            is_rework = self.edge_contract(child['file_handoff_lineage']['protocol']) is not None
            if is_rework:
                affected = self._rework_candidate(db, path[:index+1], edges[:index], parent, child,
                    preview, audit, prior, edge_evidence, root_audit)
            else:
                affected = self._candidate(parent, child, preview, audit)
            if (audit['item_id'] != item['id'] or audit['base_plan'] != plan_ref(parent) or
                    audit['item_revision'] != preview['item_revision'] or
                    audit['project_root'] != project_root or audit['relation'] != relation or
                    audit['protected_history'] != [history_by_run[r['id']] for r in prior] or
                    audit['frontier'] != [{'run_id':r['id'], 'evidence':edge_evidence[r['id']]} for r in prior] or
                    audit['resources'] != resource_status(self.s, db, path[:index+1]) or
                    any(not r['valid'] for r in audit['resources']) or
                    self.s.work_units.usage(db, prior) != preview['usage']):
                raise ValueError('祖先层全部旧运行、顺序、输入、快照、前沿、资源或累计已变化。')
            if (len(prior) >= preview['authorization']['max_attempts_total'] or
                    child['created'] >= preview['authorization']['dispatch_before']):
                raise ValueError('祖先边保存当时原累计上限或新增派发截止不足。')
            if index:
                links = {'plans': [plan_ref(p) for p in path[:index+1]],
                         'parent_application': edges[index-1]['application'], 'root_application': edges[0]['application']}
                if any(audit.get(k) != v for k, v in links.items()) or any(
                        child['file_handoff_lineage'].get(k) != links[k] for k in ('parent_application','root_application')):
                    raise ValueError('祖先层跳层、父应用或根交接引用不一致。')
                previous_auth = edges[index-1]['authorization']
                previous_driver = edges[index-1]['paused_driver']
                if (preview['authorization'] != previous_auth or
                        preview['driver']['id'] != previous_driver['id'] or
                        preview['driver']['generation'] != previous_driver['generation']):
                    raise ValueError('祖先层不能重授或扩大原累计授权。')
            for k in ('historical_source','completed_target','delivery','completion'):
                if audit[k] != root_audit[k]:
                    raise ValueError('祖先层退役A或根B证明被改绑。')
            if is_rework:
                continue  # The new edge's complete projection was rebuilt above.
            entries = []
            for unit in child['units']:
                run, raw = self._resolve(db, parent, unit['key'], prior, by_plan, edge_evidence, root, frozenset())
                state = ('HISTORICAL_RETIRED' if unit['key'] == relation['source_unit'] else
                         'AFFECTED' if unit['key'] in affected else 'ELIGIBLE_RESULT' if run else 'NOT_RUN')
                entries.append((unit['key'],state,source_ref(run,raw) if state == 'ELIGIBLE_RESULT' else None))
            if ([(e['key'],e['state'],e['source']) for e in preview['units']] != entries or
                    preview['eligible_results'] != [k for k,st,_ in entries if st=='ELIGIBLE_RESULT'] or
                    preview['retired_source'] != relation['source_unit'] or preview['completed_target'] != relation['target_unit']):
                raise ValueError('祖先层准确成果资格、原运行或退役身份不一致。')
        for run in rows:
            self._run(db, by_plan[run['plan_id']], run, rows, by_plan, evidence, root, frozenset())
        source = next(r for r in rows if r['id'] == root_audit['historical_source']['run_id'])
        target = next(r for r in rows if r['id'] == root_audit['completed_target']['source']['run_id'])
        proof = self.ledger.match(db, root_audit['completion'], target, source, root_audit['historical_source']['evidence']['version'])
        delivery = self.ledger.get(db, 'work_file_handoff_delivery', proof['delivery'])
        historical = {'run_id':source['id'], 'evidence':delivery['snapshot']['intent']['source_evidence'],
                      'current_input_permission':False}
        completed = {'source':source_ref(target,evidence[target['id']]), 'evidence':evidence[target['id']]}
        if (source['plan_id'] != root['id'] or target['plan_id'] != root['id'] or
                proof['delivery'] != root_audit['delivery'] or
                root_audit['historical_source'] != historical or root_audit['completed_target'] != completed):
            raise ValueError('祖先根B不是原方案唯一准确交付及完成。')
        return deepcopy({'protocol':self.path_protocol, 'consumer_plan':plan_ref(plan), 'plans':[plan_ref(p) for p in path],
            'edges':[e['application'] for e in edges], 'historical_checks':historical_checks, 'historical_source':root_audit['historical_source'],
            'completed_target':root_audit['completed_target'], 'frontier':[{'run_id':r['id'],'evidence':evidence[r['id']]} for r in terminal],
            'retired_source':relation['source_unit'], 'completed_key':relation['target_unit'],
            'save_permission':False, 'current_input_permission':False, 'execution_permission':False, 'no_dispatch':True})
