"""Read-only mixed saved-path verifier for explicit fixed text rework.

Legacy edges retain their original V1/V2 validation methods. Only a typed new
edge accepts unchanged definitions and exact known-terminal affected sources.
"""
from copy import deepcopy

from .store import digest
from .work_file_handoff_apply import (APPLICATION as APPLICATION1, APPLICATION_KIND,
    CANDIDATE_KIND, CONSUMPTION_KIND, REMOVED_CONTEXT)
from .work_file_handoff_chain import WorkFileHandoffChain, MAX_DEPTH, APPLICATION as APPLICATION2
from .work_file_handoff_text_reworks import (
    PREVIEW, CHECKPOINT, CANDIDATE, APPLICATION, CONSUMPTION, PATH, INPUT, FIELDS, project_sources)
from .work_file_sources import resource_status
from .work_transitions import plan_ref, source_ref


def _edge_context(unit, edge):
    preview = edge['preview']
    if unit['key'] not in preview['affected_units']:
        return None
    entry = next(e for e in preview['units'] if e['key'] == unit['key'])
    return deepcopy({'protocol': INPUT, 'application': edge['application'],
        'from_plan': preview['base_plan'], 'unit_key': unit['key'],
        'prior_source': entry['prior_source'], 'selections': preview['selections']})


def input_context(service, db, plan, unit):
    if plan.get('file_handoff_lineage', {}).get('protocol') != APPLICATION:
        return None
    from .work_file_handoff_chain_runtime import saved_path
    path = saved_path(service, db, plan)
    parent = service.collaboration.get(db, 'teamwork_plan', plan['previous_plan_reference']['id'])
    return _edge_context(unit, path._saved_edge(db, parent, plan))


class WorkFileHandoffReworkPath(WorkFileHandoffChain):
    path_protocol = PATH

    def explicit_protocols(self):
        return (APPLICATION,)

    def edge_contract(self, protocol):
        if protocol == APPLICATION:
            return (PREVIEW, CHECKPOINT, CANDIDATE, APPLICATION, CONSUMPTION,
                    'EXPLICIT_FIXED_FILE_HANDOFF_TEXT_REWORK',
                    'FILE_HANDOFF_TEXT_REWORKED_EXPLICIT_RECHECK_REQUIRED')
        return None

    def _path(self, db, plan):
        if self.s.teamwork.current(db, plan['item_id']) != plan:
            raise ValueError('祖先链入口只接受数据库当前准确方案。')
        reverse, seen = [], set()
        while True:
            if (plan['id'] in seen or
                    digest({k: v for k, v in plan.items() if k != 'version'}) != plan['version'] or
                    self.s.collaboration.get(db, 'teamwork_plan', plan['id']) != plan):
                raise ValueError('祖先链循环、重复方案或完整摘要不一致。')
            seen.add(plan['id']); reverse.append(plan)
            previous = plan.get('previous_plan_reference')
            if not previous:
                # Ordinary saves retain an informational previous_plan id.
                # Only previous_plan_reference establishes this protocol's edge.
                if plan.get('file_handoff_lineage'):
                    raise ValueError('交接祖先根缺失父边。')
                break
            if len(reverse) > MAX_DEPTH:
                raise ValueError('交接祖先链超过32层。')
            if set(previous) != {'id', 'version'} or plan.get('previous_plan') != previous['id']:
                raise ValueError('交接祖先父边引用不完整。')
            parent = self.s.collaboration.get(db, 'teamwork_plan', previous['id'])
            if plan_ref(parent) != previous or parent['created'] > plan['created']:
                raise ValueError('交接祖先父边版本或顺序不一致。')
            plan = parent
        path = list(reversed(reverse))
        if len(path) < 2:
            raise ValueError('祖先链入口须有已保存交接修订。')
        root = path[0]
        relation = self.ledger.relation(root, root['file_handoffs'][0]['target_unit'])
        for index, child in enumerate(path):
            if (child['mode'] != 'fixed' or child['item_id'] != root['item_id'] or
                    child['project_id'] != root['project_id'] or
                    child['work_requirements'] != root['work_requirements'] or
                    child['file_handoffs'] != [relation]):
                raise ValueError('交接祖先路径跨任务、项目、要求、模式或关系。')
            if index:
                protocol = child['file_handoff_lineage']['protocol']
                allowed = {APPLICATION1} if index == 1 else {APPLICATION2, *self.explicit_protocols()}
                if protocol not in allowed:
                    raise ValueError('交接祖先边协议或层次不一致。')
        return path

    def _saved_edge(self, db, parent, plan):
        """Check one edge already reached from the current persisted path."""
        record = plan['file_handoff_lineage']
        contract = self.edge_contract(record['protocol'])
        if contract is None:
            return super()._saved_edge(db, parent, plan)
        preview_protocol, checkpoint, candidate_protocol, application_protocol, consumption, transition_kind, pause_reason = contract
        candidate = self.ledger.get(db, CANDIDATE_KIND, record['candidate'])
        preview = candidate['snapshot']['preview']; audit = record['proof']
        self.ledger.require_version(preview, preview_protocol)
        self.ledger.require_version(audit, checkpoint)
        expected_record = {'id': 'file-handoff-revision:' + candidate['version'], 'protocol': application_protocol,
            'candidate': self.ledger.reference(candidate), 'base_plan': preview['base_plan'],
            'candidate_version': preview['version'], 'proof': preview['ancestry_audit'],
            'original_authorization': preview['authorization'], 'original_driver': preview['driver'],
            'reason': preview['reason'], 'command_id': record['command_id'], 'no_dispatch': True,
            **{k: audit[k] for k in ('parent_application', 'root_application')}}
        if record != expected_record:
            raise ValueError('返工应用边完整字段、候选身份或no_dispatch不一致。')
        if (candidate['snapshot']['protocol'] != candidate_protocol or
                candidate['item_id'] != plan['item_id'] or
                preview['version'] != record['candidate_version'] or audit != preview['ancestry_audit'] or
                preview['base_plan'] != record['base_plan'] or plan_ref(parent) != record['base_plan'] or
                preview['authorization'] != record['original_authorization'] or
                preview['driver'] != record['original_driver'] or preview['reason'] != record['reason'] or
                preview['execution_blockers']):
            raise ValueError('祖先边未绑定准确候选、原方案及完成证明。')
        # Current candidate is intentionally NOT a historical identity.
        saved = [r for r in self.s.collaboration.all(db, APPLICATION_KIND)
                 if r['snapshot'].get('lineage', {}).get('id') == record['id'] or
                 r['snapshot'].get('to_plan', {}).get('id') == plan['id']]
        if len(saved) != 1:
            raise ValueError('祖先方案须有唯一父边和不可变应用记录。')
        application = self.ledger.get(db, APPLICATION_KIND, self.ledger.reference(saved[0]))
        value = application['snapshot']
        if (value['protocol'] != record['protocol'] or value['lineage'] != record or
                value['item_id'] != plan['item_id'] or value['to_plan'] != plan_ref(plan) or
                value['created'] != plan['created'] or value['command_id'] != record['command_id'] or
                value['no_dispatch'] is not True):
            raise ValueError('祖先应用与最终方案、嵌入证明不一致。')
        transition = {'kind': transition_kind, 'candidate': record['candidate'],
                      'file_handoff_revision': record['id'], 'usage_at_transition': preview['usage'],
                      'authorization_from': self.s.work_units.auth_ref(preview['authorization']),
                      'command_id': record['command_id'], 'no_dispatch': True}
        lineage = {e['key']: e['source'] for e in preview['units'] if e['state'] == 'ELIGIBLE_RESULT'}
        expected = {**parent, 'id': plan['id'], 'item_revision': preview['item_revision'],
                    'units': preview['candidate_units'], 'previous_plan': parent['id'],
                    'previous_plan_reference': plan_ref(parent), 'created': plan['created'],
                    'state': 'PREPARED', 'result_lineage': lineage, 'transition': transition}
        for key in (*REMOVED_CONTEXT, 'version'):
            expected.pop(key, None)
        expected['file_handoff_lineage'] = record; expected['version'] = digest(expected)
        if plan != expected or preview['retired_source'] in lineage:
            raise ValueError('祖先边完整方案或成果沿袭不一致。')
        auth, paused = value['authorization'], value['paused_driver']
        expected_auth = {**preview['authorization'], 'revision': auth['revision'],
            'plan_id': plan['id'], 'plan_version': plan['version'],
            'previous_authorization': self.s.work_units.auth_ref(preview['authorization']),
            'transferred_at': plan['created'], 'transfer_proposal': None,
            'usage_at_transfer': preview['usage'], 'command_id': record['command_id']}
        old_auth = self.s.collaboration.get(db, 'work_unit_authorization_revision',
                            'unit-auth-version:' + preview['authorization']['revision'])
        new_auth = self.s.collaboration.get(db, 'work_unit_authorization_revision',
                            'unit-auth-version:' + auth['revision'])
        old_driver = self.s.collaboration.get(db, 'work_driver_revision',
                            'driver-version:' + preview['driver']['revision'])
        paused_history = self.s.collaboration.get(db, 'work_driver_revision', 'driver-version:' + paused['revision'])
        expected_paused = {**old_driver, 'id': preview['driver']['id'], 'revision': paused['revision'],
            'updated': paused['updated'], 'plan_id': plan['id'], 'plan_version': plan['version'],
            'state': 'PAUSED', 'reason': pause_reason,
            'previous_plan_reference': plan_ref(parent), 'transition_proposal': None}
        expected_paused.pop('driver_id', None)
        if (auth != expected_auth or old_auth != {**preview['authorization'], 'id': old_auth['id']} or
                new_auth != {**auth, 'id': new_auth['id']} or paused != expected_paused or
                self.s.work_drivers.reference({**old_driver, 'id': old_driver['driver_id']}) != preview['driver'] or
                paused_history != {**paused, 'id': paused_history['id'], 'driver_id': paused['id']} or
                auth['revision'] == preview['authorization']['revision'] or
                paused['revision'] == preview['driver']['revision'] or
                auth['plan_id'] != plan['id'] or
                preview['authorization']['plan_id'] != parent['id'] or
                preview['authorization']['plan_version'] != parent['version'] or
                old_driver['plan_id'] != parent['id'] or old_driver['plan_version'] != parent['version'] or
                old_driver['state'] != 'RESERVED' or old_driver['item_revision'] != preview['item_revision'] or
                old_driver['generation'] != auth['driver_generation'] or auth['item_id'] != plan['item_id']):
            raise ValueError('祖先边原授权转移及同generation暂停历史不一致。')
        consumed = [r for r in self.s.collaboration.all(db, CONSUMPTION_KIND)
                    if r['snapshot'].get('candidate') == record['candidate'] or
                    r['snapshot'].get('application') == self.ledger.reference(application)]
        if len(consumed) != 1:
            raise ValueError('祖先候选须且仅能消费一次。')
        consumed = self.ledger.get(db, CONSUMPTION_KIND, self.ledger.reference(consumed[0]))
        expected_consumption = {'protocol': consumption, 'item_id': plan['item_id'],
            'candidate': record['candidate'], 'application': self.ledger.reference(application),
            'from_plan': plan_ref(parent), 'to_plan': plan_ref(plan),
            'driver': self.s.work_drivers.reference(paused), 'authorization': self.s.work_units.auth_ref(auth),
            'command_id': record['command_id'], 'no_dispatch': True}
        if consumed['snapshot'] != expected_consumption:
            raise ValueError('祖先消费的原/新方案、应用、责任或授权不一致。')
        links = {k: audit[k] for k in ('parent_application', 'root_application')}
        if any(record.get(k) != v for k, v in links.items()):
            raise ValueError('返工边父应用或根应用引用不一致。')
        return {'application': self.ledger.reference(application), 'candidate': record['candidate'],
                'preview': preview, 'audit': audit, 'authorization': auth, 'paused_driver': paused}

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
        for index, (parent, child, edge) in enumerate(zip(path, path[1:], edges)):
            audit, preview = edge['audit'], edge['preview']
            prior_ids = {p['id'] for p in path[:index+1]}
            prior = [r for r in rows if r['plan_id'] in prior_ids]
            is_rework = self.edge_contract(child['file_handoff_lineage']['protocol']) is not None
            if is_rework:
                affected = self._rework_candidate(db, path[:index+1], edges[:index], parent, child,
                    preview, audit, prior, evidence, root_audit)
            else:
                affected = self._candidate(parent, child, preview, audit)
            if (audit['item_id'] != item['id'] or audit['base_plan'] != plan_ref(parent) or
                    audit['item_revision'] != preview['item_revision'] or
                    audit['project_root'] != project_root or audit['relation'] != relation or
                    audit['protected_history'] != [history_by_run[r['id']] for r in prior] or
                    audit['frontier'] != [{'run_id':r['id'], 'evidence':evidence[r['id']]} for r in prior] or
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
                run, raw = self._resolve(db, parent, unit['key'], prior, by_plan, evidence, root, frozenset())
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
            'edges':[e['application'] for e in edges], 'historical_source':root_audit['historical_source'],
            'completed_target':root_audit['completed_target'], 'frontier':[{'run_id':r['id'],'evidence':evidence[r['id']]} for r in terminal],
            'retired_source':relation['source_unit'], 'completed_key':relation['target_unit'],
            'save_permission':False, 'current_input_permission':False, 'execution_permission':False, 'no_dispatch':True})

    def run_context(self, unit, edge):
        return 'handoff_text_rework_context', _edge_context(unit, edge)

    def _local(self, db, plan, run, edge, root):
        if self.edge_contract(plan['file_handoff_lineage']['protocol']) is None:
            return super()._local(db, plan, run, edge, root)
        self.s.work_file_handoff_applications.require_local_identity(db, plan, run)
        from .native_permissions import choice
        task = self.s.store.task(run['task_id'], db); spec = task['spec']
        unit = next(u for u in plan['units'] if u['key'] == run['unit_key'])
        frozen = run['input']; context_key, context = self.run_context(unit, edge)
        keys = {'item_id','item_revision','plan_id','plan_version','work_requirements','unit',
                'dependencies','check_evidence','file_artifacts'}
        if context is not None:
            keys.add(context_key)
        driver = self.s.collaboration.get(db, 'work_driver_revision', 'driver-version:' + run['driver_revision'])
        cooperative = {k:run[k] for k in ('item_id','driver_id','generation','driver_revision','plan_id',
            'plan_version','unit_key','unit_revision','input_version','authorization')}
        cooperative['source_item_revision'] = run['item_revision']
        if (set(frozen) != keys or frozen.get(context_key) != context or
                run['cooperative'] != cooperative or frozen['item_revision'] != run['item_revision'] or
                run['driver_id'] != edge['paused_driver']['id'] or
                driver['driver_id'] != run['driver_id'] or driver['generation'] != run['generation'] or
                driver['revision'] != run['driver_revision'] or driver['state'] != 'RESERVED' or
                driver['item_id'] != run['item_id'] or driver['item_revision'] != run['item_revision'] or
                driver['plan_id'] != plan['id'] or driver['plan_version'] != plan['version'] or
                run['flow'] is not None or run['scheduling'] != 'EXPLICIT_SERIAL_STEP' or
                any(frozen[k] != run[k] for k in ('item_id','plan_id','plan_version')) or
                run['unit_key'] in {r[k] for r in plan['file_handoffs'] for k in ('source_unit','target_unit')} or
                unit['role'] == 'coordinate' or unit['outputs'] or unit['checks'] or unit['write_paths'] or
                run['retry_of'] is not None or run['authorization'] != self.s.work_units.auth_ref(edge['authorization']) or
                run['generation'] != edge['paused_driver']['generation'] or
                task['project_id'] != plan['project_id'] or spec.get('runtime_mode') != 'official' or
                spec.get('work_mode') != 'cooperative' or spec.get('access') != choice(None) or
                spec.get('native') != {'configuration':'VENDOR_DEFAULTS'} or spec.get('budget') != {} or
                spec.get('writable') != [] or spec.get('checks') != [] or
                self.s.store.attempt(run['attempt_id'], db)['workspace'] != root):
            raise ValueError('返工文字运行的完整上下文、权限、原累计授权、目录或身份不一致。')

    def _rework_candidate(self, db, paths, edges, parent, child, preview, audit, rows, evidence, root):
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
                request['plan'] != plan_ref(parent) or request['revision'] != preview['item_revision'] or
                request['current_driver'] != preview['driver'] or
                request['authorization'] != self.s.work_units.auth_ref(preview['authorization']) or
                request['frontier'] != preview['frontier'] or request['frontier'] != frontier or
                child['units'] != parent['units']):
            raise ValueError('返工边须绑定准确原请求并保留全部定义。')
        proof = {'retired_source': root['relation']['source_unit'],
                 'completed_key': root['relation']['target_unit'], 'historical_source': root['historical_source']}
        projection = project_sources(self.s, parent, request, rows, evidence, proof)
        expected_audit = {'protocol': CHECKPOINT, 'base_plan': plan_ref(parent), 'item_id': parent['item_id'],
            'item_revision': preview['item_revision'], 'project_root': root['project_root'], 'relation': root['relation'],
            'plans': [plan_ref(p) for p in paths], 'parent_application': edges[-1]['application'],
            'root_application': edges[0]['application'],
            **{k: root[k] for k in ('historical_source', 'completed_target', 'delivery', 'completion')},
            'resources': resource_status(self.s, db, paths), 'protected_keys': projection['protected_keys'],
            'protected_history': self.ledger.history(db, rows),
            'frontier': [{'run_id': r['id'], 'evidence': evidence[r['id']]} for r in rows],
            'candidate_units': parent['units'], 'selections': projection['selections'],
            'affected_units': projection['affected_units']}
        expected_audit['version'] = digest(expected_audit)
        expected = {'protocol': PREVIEW, 'request': request, 'base_plan': plan_ref(parent),
            'item_revision': preview['item_revision'], 'driver': preview['driver'],
            'authorization': preview['authorization'], 'frontier': request['frontier'], 'reason': projection['reason'],
            'candidate_units': parent['units'], **{k: projection[k] for k in
                ('selections', 'units', 'affected_units', 'eligible_results')},
            'ancestry_audit': expected_audit, 'completed_target': proof['completed_key'],
            'retired_source': proof['retired_source'], 'usage': self.s.work_units.usage(db, rows),
            'execution_blockers': [], 'save_permission': False, 'current_input_permission': False,
            'execution_permission': False, 'scope': 'INTERNAL_EXPLICIT_FIXED_TEXT_REWORK; NO_SAVE_OR_DISPATCH',
            'independent_review': 'NOT_PERFORMED', 'human_acceptance': 'NOT_RECORDED'}
        expected['version'] = digest(expected)
        if audit != expected_audit or preview != expected:
            raise ValueError('返工边完整选择、影响、原输入/证据、沿用来源或检查点已变化。')
        return set(projection['affected_units'])
