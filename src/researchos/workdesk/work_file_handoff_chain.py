"""Internal V2 saved-path verification. No command, save or dispatch permission.

Only verify(current_plan) is an entry point. Historical membership is derived
from persisted parents and applications, never supplied by a caller. V1 public
verification and original-plan audit retain their separate current contracts.
"""
from copy import deepcopy

from .contracts import bounded_text
from .plans import PlanCommands
from .store import digest
from .work_file_handoff_ancestry import PROTOCOL as AUDIT1, WorkFileHandoffAncestry
from .work_file_handoff_apply import (
    APPLICATION as APPLICATION1, CANDIDATE as CANDIDATE1, CONSUMPTION as CONSUMPTION1,
    APPLICATION_KIND, CANDIDATE_KIND, CONSUMPTION_KIND, REMOVED_CONTEXT)
from .work_file_handoff_revisions import PROTOCOL as PREVIEW1
from .work_file_sources import resource_status
from .work_transitions import plan_ref, source_ref
from .work_unit_revisions import changes


PROTOCOL = 'FIXED_FILE_HANDOFF_SAVED_PATH_V2'
AUDIT = 'FIXED_FILE_HANDOFF_PATH_CHECKPOINT_V2'
PREVIEW = 'FIXED_FILE_HANDOFF_REVISION_CANDIDATE_V2'
CANDIDATE = 'FIXED_FILE_HANDOFF_SAVED_CANDIDATE_V2'
APPLICATION = 'FIXED_FILE_HANDOFF_APPLICATION_V2'
CONSUMPTION = 'FIXED_FILE_HANDOFF_CANDIDATE_CONSUMPTION_V2'
MAX_DEPTH = 32


class WorkFileHandoffChain:
    def __init__(self, service):
        self.s = service

    @property
    def ledger(self):
        return self.s.work_file_handoff_ledger

    def verify(self, db, plan):
        # A new observation on EVERY invocation, including under an outer read.
        with self.s.work_unit_revision_applications.validation_snapshot(db):
            try:
                return self._verify(db, plan)
            except (KeyError, TypeError, AttributeError, StopIteration, IndexError) as exc:
                raise ValueError('交接祖先链的完整记录形状、引用或历史不一致。') from exc

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
                if protocol != (APPLICATION1 if index == 1 else APPLICATION):
                    raise ValueError('交接祖先边协议或层次不一致。')
        return path

    def _saved_edge(self, db, parent, plan):
        """Check one edge already reached from the current persisted path."""
        record = plan['file_handoff_lineage']
        v1 = record['protocol'] == APPLICATION1
        candidate = self.ledger.get(db, CANDIDATE_KIND, record['candidate'])
        preview = candidate['snapshot']['preview']; audit = record['proof']
        self.ledger.require_version(preview, PREVIEW1 if v1 else PREVIEW)
        self.ledger.require_version(audit, AUDIT1 if v1 else AUDIT)
        if (candidate['snapshot']['protocol'] != (CANDIDATE1 if v1 else CANDIDATE) or
                candidate['item_id'] != plan['item_id'] or
                preview['version'] != record['candidate_version'] or audit != preview['ancestry_audit'] or
                preview['base_plan'] != record['base_plan'] or plan_ref(parent) != record['base_plan'] or
                preview['authorization'] != record['original_authorization'] or
                preview['driver'] != record['original_driver'] or preview['reason'] != record['reason'] or
                preview['execution_blockers'] or audit['state'] != 'COMPLETED_VALID' or not audit['carry_candidate']):
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
        transition = {'kind': 'EXPLICIT_FIXED_FILE_HANDOFF_REVISION', 'candidate': record['candidate'],
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
            'state': 'PAUSED', 'reason': 'FILE_HANDOFF_REVISED_EXPLICIT_RECHECK_REQUIRED',
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
        expected_consumption = {'protocol': CONSUMPTION1 if v1 else CONSUMPTION, 'item_id': plan['item_id'],
            'candidate': record['candidate'], 'application': self.ledger.reference(application),
            'from_plan': plan_ref(parent), 'to_plan': plan_ref(plan),
            'driver': self.s.work_drivers.reference(paused), 'authorization': self.s.work_units.auth_ref(auth),
            'command_id': record['command_id'], 'no_dispatch': True}
        if consumed['snapshot'] != expected_consumption:
            raise ValueError('祖先消费的原/新方案、应用、责任或授权不一致。')
        return {'application': self.ledger.reference(application), 'candidate': record['candidate'],
                'preview': preview, 'audit': audit, 'authorization': auth, 'paused_driver': paused}

    def _candidate(self, parent, child, preview, audit):
        """Rebuild allowed edits and old/new graph closure from the exact basis."""
        request = preview['request']; changes(request['changes'])
        if (request['plan'] != plan_ref(parent) or request['revision'] != preview['item_revision'] or
                request['current_driver'] != preview['driver'] or
                request['authorization'] != self.s.work_units.auth_ref(preview['authorization']) or
                request['frontier'] != preview['frontier'] or request['id'] != parent['item_id'] or
                request['reason'] != preview['reason']):
            raise ValueError('祖先候选准确请求与原方案责任依据不一致。')
        old = {u['key']: u for u in parent['units']}; replacements = {}
        for change in request['changes']:
            name = change['unit_key']; basis = change['basis']; proposed = change['definition']; unit = old[name]
            if (change['operation'] != 'revise' or unit['role'] == 'coordinate' or
                    basis['unit_key'] != name or basis['unit_revision'] != digest(unit) or
                    basis['work_requirements_version'] != digest(parent['work_requirements']) or
                    proposed['member'] != unit['member']['reference']):
                raise ValueError('祖先候选原职责、准确成员或完整依据不一致。')
            replacements[name] = {**unit, 'depends': sorted(proposed['depends']),
                **{k: bounded_text(proposed[k], k, limit) for k, limit in
                   [('title', 160), ('goal', 4000), ('acceptance', 4000)]}}
            if replacements[name] == {**unit, 'depends': sorted(unit['depends'])}:
                raise ValueError('祖先候选没有准确局部改动。')
        units = [replacements.get(u['key'], u) for u in parent['units']]
        PlanCommands.validate_graph({u['key']: u['depends'] for u in units})
        affected = set(replacements)
        while True:
            more = {u['key'] for u in units if set(u['depends'] + old[u['key']]['depends']) & affected} - affected
            if not more: break
            affected.update(more)
        endpoints = [audit['relation'][k] for k in ('source_unit', 'target_unit')]
        protected = (WorkFileHandoffAncestry.protected_keys(parent['units'], endpoints) |
                     WorkFileHandoffAncestry.protected_keys(units, endpoints))
        if (protected & affected or units != child['units'] or
                preview['direct_changes'] != [u['key'] for u in units if u['key'] in replacements] or
                preview['affected_units'] != [u['key'] for u in units if u['key'] in affected] or
                audit['candidate_units'] != units or audit['affected_units'] != sorted(affected) or
                audit['protected_keys'] != sorted(protected)):
            raise ValueError('祖先候选完整图、旧新影响闭包或保护上游不一致。')
        for u in units:
            if u['key'] in affected and (u['outputs'] or u['checks'] or u['write_paths'] or
                    u['dependency_gate'] != 'result' or endpoints[0] in u['depends']):
                raise ValueError('祖先候选只能细化保护闭包外文字工作，不能输入退役A。')
        return affected

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
            affected = self._candidate(parent, child, preview, audit)
            prior_ids = {p['id'] for p in path[:index+1]}
            prior = [r for r in rows if r['plan_id'] in prior_ids]
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
        return deepcopy({'protocol':PROTOCOL, 'consumer_plan':plan_ref(plan), 'plans':[plan_ref(p) for p in path],
            'edges':[e['application'] for e in edges], 'historical_source':root_audit['historical_source'],
            'completed_target':root_audit['completed_target'], 'frontier':[{'run_id':r['id'],'evidence':evidence[r['id']]} for r in terminal],
            'retired_source':relation['source_unit'], 'completed_key':relation['target_unit'],
            'save_permission':False, 'current_input_permission':False, 'execution_permission':False, 'no_dispatch':True})

    def _terminal_rows(self, db, plan, rows):
        return rows  # The public verifier always requires every row's terminal history.

    def _local(self, db, plan, run, edge, root):
        self.s.work_file_handoff_applications.require_local_identity(db, plan, run)
        from .native_permissions import choice
        task = self.s.store.task(run['task_id'],db); spec = task['spec']
        unit = next(u for u in plan['units'] if u['key']==run['unit_key'])
        frozen = run['input']
        driver = self.s.collaboration.get(db, 'work_driver_revision', 'driver-version:' + run['driver_revision'])
        cooperative = {k:run[k] for k in ('item_id','driver_id','generation','driver_revision','plan_id',
            'plan_version','unit_key','unit_revision','input_version','authorization')}
        cooperative['source_item_revision'] = run['item_revision']
        if (set(frozen) != {'item_id','item_revision','plan_id','plan_version','work_requirements','unit',
                           'dependencies','check_evidence','file_artifacts'} or
                run['cooperative'] != cooperative or frozen['item_revision'] != run['item_revision'] or
                run['driver_id'] != edge['paused_driver']['id'] or
                driver['driver_id'] != run['driver_id'] or driver['generation'] != run['generation'] or
                driver['revision'] != run['driver_revision'] or driver['state'] != 'RESERVED' or
                driver['item_id'] != run['item_id'] or driver['item_revision'] != run['item_revision'] or
                driver['plan_id'] != plan['id'] or driver['plan_version'] != plan['version'] or
                run['flow'] is not None or run['scheduling'] != 'EXPLICIT_SERIAL_STEP' or
                any(frozen[k] != run[k] for k in ('item_id','plan_id','plan_version')) or
                run['unit_key'] in {r[k] for r in plan['file_handoffs'] for k in ('source_unit','target_unit')} or
                unit['role']=='coordinate' or unit['outputs'] or unit['checks'] or unit['write_paths'] or
                run['retry_of'] is not None or run['authorization'] != self.s.work_units.auth_ref(edge['authorization']) or
                run['generation'] != edge['paused_driver']['generation'] or
                task['project_id'] != plan['project_id'] or spec.get('runtime_mode') != 'official' or
                spec.get('work_mode') != 'cooperative' or spec.get('access') != choice(None) or
                spec.get('native') != {'configuration':'VENDOR_DEFAULTS'} or spec.get('budget') != {} or
                spec.get('writable') != [] or spec.get('checks') != [] or
                self.s.store.attempt(run['attempt_id'],db)['workspace'] != root or
                any(run['input'].get(k) for k in ('file_handoff_intent','rework_context','unit_revision_context',
                    'file_rework_context','write_before_baseline','coordination_context'))):
            raise ValueError('祖先文字运行的完整权限、原累计授权、目录或身份不一致。')

    def _resolve(self, db, plan, key, rows, plans, evidence, root, trail):
        if (plan['id'],key) in trail:
            raise ValueError('祖先成果来源循环。')
        unit = next(u for u in plan['units'] if u['key']==key)
        local = [r for r in rows if r['plan_id']==plan['id'] and r['unit_key']==key]
        if len(local)>1:
            raise ValueError('祖先路径不接受同修订重复运行。')
        entry = plan.get('result_lineage',{}).get(key)
        run = local[0] if local else next((r for r in rows if entry and r['id']==entry['run_id']),None)
        if plan['id']!=root['id'] and key==root['file_handoffs'][0]['source_unit']:
            return None, None
        if not run:
            if entry: raise ValueError('祖先沿用缺少准确原运行。')
            return None,None
        raw = evidence[run['id']]
        if not local:
            if (run['plan_id'] not in plans or list(plans).index(run['plan_id']) >= list(plans).index(plan['id']) or
                    next(u for u in plans[run['plan_id']]['units'] if u['key']==key) != unit or
                    source_ref(run,raw) != entry):
                raise ValueError('祖先成果不能重绑同key或非路径原运行。')
        if key!=root['file_handoffs'][0]['source_unit']:
            self._run(db, plans[run['plan_id']], run, rows, plans, evidence, root, trail)
            self.s.work_artifacts.require(raw, 'checked' if unit['checks'] else 'files' if unit['outputs'] else 'result')
        return run,raw

    def _run(self, db, plan, run, rows, plans, evidence, root, trail):
        marker = (plan['id'],run['unit_key'])
        if marker in trail: raise ValueError('祖先运行依赖循环。')
        if plan['id']==root['id']:
            unit = next(u for u in plan['units'] if u['key']==run['unit_key'])
            self.s.work_transitions.validate_run(db,plan,unit,run,rows,frozenset(),consumer_plan=root)
            return
        unit = run['input']['unit']; frozen = run['input']['dependencies']
        if len(frozen)!=len(unit['depends']) or {d['unit_key'] for d in frozen}!=set(unit['depends']):
            raise ValueError('祖先运行冻结前置集合不一致。')
        for dep in frozen:
            original,raw = self._resolve(db,plan,dep['unit_key'],rows,plans,evidence,root,trail|{marker})
            if not original or raw != {k:v for k,v in dep.items() if k not in ('unit_key','lineage')}:
                raise ValueError('祖先运行冻结的完整依赖证据已变化。')
            expected_lineage = {'source':plan.get('result_lineage',{}).get(dep['unit_key']), 'consumer_plan':plan_ref(plan)}
            if dep.get('lineage') != (expected_lineage if original['plan_id']!=plan['id'] else None):
                raise ValueError('祖先冻结依赖的来源/消费者方案不一致。')
            self.s.work_artifacts.require(raw,unit['dependency_gate'])
        if (run['input']['check_evidence'] != [{'unit_key':d['unit_key'],'artifact':d['file_artifacts']['reference'],
                'check_status':d['check_status']} for d in frozen] or
                run['input']['file_artifacts'] != [{'unit_key':d['unit_key'],**d['file_artifacts']} for d in frozen
                    if d['file_artifacts']['state']=='CURRENT']):
            raise ValueError('祖先运行冻结文件清单或检查引用不一致。')
