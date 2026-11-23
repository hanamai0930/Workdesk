"""Bounded daily leader organization over the existing native unit transactions."""
import json
import time

from .contracts import bounded_text
from .store import canonical, digest, uid

PROTOCOL = 'WORKDESK_LEADER_DAILY_DECISION_V1'
COORDINATE = 'leader_coordinate'
KIND = 'work_leader_organization'


def unique_fields(pairs):
    fields = {}
    for key, value in pairs:
        if key in fields:
            raise ValueError('负责人决策包含重复字段。')
        fields[key] = value
    return fields


def organization(service, db, plan, *, trail=frozenset()):
    if not plan or plan['mode'] not in {'leader', 'host_experts'}:
        return None
    prefix = 'host-experts' if plan['mode'] == 'host_experts' else 'leader'
    kind = 'work_host_experts_organization' if plan['mode'] == 'host_experts' else KIND
    stored = db.execute('SELECT data FROM collaboration WHERE kind=? AND id=?',
                        (kind, prefix + '-organization:' + plan['id'])).fetchone()
    if not stored:
        return None
    row = json.loads(stored[0])
    body = {k: v for k, v in row.items() if k != 'version'}
    if digest(body) != row['version'] or row['plan'] != {k: plan[k] for k in ('id', 'version')}:
        raise ValueError('负责人日常组织的摘要或方案来源已变化。')
    if plan['id'] in trail:
        raise ValueError('负责人组织沿袭出现循环。')
    if row.get('parent'):
        parent = row['parent']
        old_plan = service.collaboration.get(db, 'teamwork_plan', parent['plan']['id'])
        old = organization(service, db, old_plan, trail=trail | {plan['id']})
        consumed = service.collaboration.get(db, 'work_proposal_consumption', parent['consumption_id'])
        if (not old or old['version'] != parent['version'] or old['plan'] != parent['plan']
                or consumed['from_plan'] != parent['plan'] or consumed['to_plan'] != row['plan']
                or consumed['proposal'] != parent['proposal'] or consumed['command_id'] != row['command_id']
                or plan['transition']['proposal'] != parent['proposal']
                or plan['previous_plan_reference'] != parent['plan']
                or row.get('decision_protocol',PROTOCOL) != old.get('decision_protocol',PROTOCOL)
                or row.get('member_access', {}) != old.get('member_access', {})
                or any(row[k] != old[k] for k in ('preparation_id','evidence','access'))):
            raise ValueError('负责人子方案脱离准确父组织或真实修订消费。')
    else:
        preparation_kind = 'work_host_experts_preparation' if plan['mode'] == 'host_experts' else 'work_leader_preparation'
        preparation = service.collaboration.get(db, preparation_kind, row['preparation_id'])
        if (preparation['state'] != 'APPLIED' or preparation.get('plan') != row['plan']
                or preparation.get('evidence') != row['evidence'] or preparation.get('access', 'workspace') != row['access']
                or preparation.get('member_access', {}) != row.get('member_access', {})):
            raise ValueError('负责人日常组织的准备来源已变化。')
    return row


def foundation(service, db, plan):
    return bool(plan and (plan['mode'] == 'fixed' or organization(service, db, plan)))


def strategy(service, plan):
    return service.host_experts if plan['mode'] == 'host_experts' else service.leader


def revisions_enabled(row):
    return row.get('decision_protocol') in {'WORKDESK_LEADER_DAILY_DECISION_V2',
                                           'WORKDESK_HOST_EXPERTS_DAILY_DECISION_V1'}


class WorkLeader:
    coordinate = COORDINATE
    prefix, kind = 'leader', KIND
    decision_protocol = 'WORKDESK_LEADER_DAILY_DECISION_V2'
    def __init__(self, service):
        self.s = service

    def register(self, db, plan, preparation, evidence):
        row = {'id': self.prefix + '-organization:' + plan['id'], 'plan': {k: plan[k] for k in ('id', 'version')},
               'preparation_id': preparation['id'], 'evidence': evidence,
               'access': preparation.get('access', 'workspace'), 'command_id': self.s.command_identity,
               'decision_protocol': self.decision_protocol}
        if preparation.get('member_access'):
            row['member_access'] = preparation['member_access']
        row['version'] = digest(row)
        self.s.collaboration.put(db, self.kind, row)

    def round(self, db, plan, rows):
        history = [r for r in rows if r['plan_id'] == plan['id'] and r['unit_key'] == self.coordinate]
        return {'protocol': organization(self.s, db, plan).get('decision_protocol', PROTOCOL), 'index': len(history) + 1,
                'previous_run_id': history[-1]['id'] if history else None,
                'organization_version': organization(self.s, db, plan)['version']}

    def context(self, db, item, plan, rows):
        candidates = []
        for unit in plan['units']:
            if unit['role'] == 'coordinate':
                continue
            resolved = self.s.work_units.resolve(db, plan, unit['key'], rows)
            latest = resolved['run']
            retry = latest['id'] if latest else None
            try:
                self.s.work_proposals.target(db, item, plan, rows, {'unit_key': unit['key'], 'retry_of': retry})
                ready, reason = True, ''
            except ValueError as exc:
                ready, reason = False, str(exc)
            candidates.append({'unit': unit, 'ready_after_proposal': ready, 'retry_of': retry,
                               'blocked_reason': reason, 'latest_result': resolved['evidence']})
        auth = self.s.work_units.authorization(db, self.s.work_drivers.current(db, item['id']))
        ordinary = [u for u in plan['units'] if u['role'] != 'coordinate']
        upstream = {k for u in ordinary for k in u['depends']}
        finals = [u['key'] for u in ordinary if u['key'] not in upstream]
        protocol = organization(self.s, db, plan).get('decision_protocol', PROTOCOL)
        result = {'protocol': protocol, 'round': self.round(db, plan, rows), 'candidates': candidates,
                'finals': finals, 'remaining_attempts': auth['max_attempts_total'] - len(rows) - 1,
                'budget_accounting': {
                    'phase': 'AFTER_CURRENT_COORDINATOR_ALREADY_COUNTED',
                    'next_ordinary_unit_cost': 1, 'next_coordinator_cost': 1,
                    'deliver_in_this_round_cost': 0,
                    'minimum_for_one_next_ordinary_and_followup': 2,
                    'rule': 'Planning 2*N+1 includes this completed coordinator. Use remaining_attempts for future calls only; organization budgets are not product acceptance conditions.'},
                'dispatch_before': auth['dispatch_before'],
                'schemas': {
                    'dispatch': {'protocol': protocol, 'action': 'dispatch', 'unit_key': 'ready candidate key',
                                 'retry_of': None, 'reason': 'selection reason'},
                    'pause': {'protocol': protocol, 'action': 'pause', 'reason': 'issue requiring attention'},
                    'deliver': {'protocol': protocol, 'action': 'deliver', 'unit_keys': finals,
                                'reason': 'all declared work and final results verified'}},
                'scope': 'PROPOSALS_ARE_DATA; DECLARED_UNITS_ONLY; ORIGINAL_BUDGET_AND_DEADLINE'}
        if plan.get('pdca'):
            result['pdca_rounds'] = plan['pdca']['rounds']
        elif revisions_enabled(organization(self.s, db, plan)):
            result.update(self.s.leader_revisions.context(db, plan, rows))
            result['schemas'].update(self.s.leader_revisions.schemas())
        return result

    def validate_source(self, db, item, plan, rows, run):
        context = run['input'].get('coordination_context', {})
        if context.get('protocol') != organization(self.s, db, plan).get('decision_protocol', PROTOCOL) or run['input'].get('leader_round') != context.get('round'):
            raise ValueError('负责人协调回合的冻结协议或轮次不符。')
        if context['round']['organization_version'] != organization(self.s, db, plan)['version']:
            raise ValueError('负责人组织来源已变化。')
        for candidate in context['candidates']:
            current = self.s.work_units.resolve(db, plan, candidate['unit']['key'], rows)
            unit = next(u for u in plan['units'] if u['key'] == candidate['unit']['key'])
            old = candidate['latest_result']
            new = current['evidence']
            if (unit != candidate['unit'] or current['problems']
                    or (old['version'] if old else None) != (new['version'] if new else None)):
                raise ValueError('负责人协调时读取的准确单元、结果或文件已变化。')

    @staticmethod
    def parse(answer, protocol=PROTOCOL):
        try:
            value = json.loads(answer, object_pairs_hook=unique_fields,
                               parse_constant=lambda _: (_ for _ in ()).throw(ValueError('非有限值')))
        except (ValueError, TypeError, RecursionError):
            raise ValueError('负责人须返回单一严格决策JSON。') from None
        if not isinstance(value, dict) or value.get('protocol') != protocol:
            raise ValueError('负责人决策协议不符。')
        action = value.get('action')
        fields = {'protocol', 'action', 'reason'}
        if action == 'dispatch':
            fields |= {'unit_key', 'retry_of'}
            if (not isinstance(value.get('unit_key'), str)
                    or value.get('retry_of') is not None and not isinstance(value['retry_of'], str)):
                raise ValueError('负责人派发引用无效。')
        elif action == 'deliver':
            fields.add('unit_keys')
            keys = value.get('unit_keys')
            if (not isinstance(keys, list) or not keys or len(keys) > 19
                    or any(not isinstance(k, str) for k in keys) or len(set(keys)) != len(keys)):
                raise ValueError('负责人交付引用无效。')
        elif protocol == 'WORKDESK_LEADER_DAILY_DECISION_V2' and action in ('adjust_dependencies','rework_units','revise_units','file_rework_units'):
            fields.add('changes')
            from .work_proposals import parse
            parse(canonical({k:v for k,v in value.items() if k != 'protocol'} | {'schema_version':1}))
        elif action != 'pause':
            raise ValueError('本版负责人决策仅支持dispatch、pause和deliver。')
        if set(value) != fields:
            raise ValueError('负责人决策字段不符。')
        value['reason'] = bounded_text(value.get('reason'), '负责人决策依据', 2000)
        return value

    def capture(self, db, p):
        fields = {'id', 'revision', 'current_driver', 'plan', 'authorization', 'frontier',
                  'run_id', 'result_version', 'proposal', 'flow'}
        if set(p) - fields:
            raise ValueError('负责人提案检查字段不支持。')
        item, driver, plan = self.s.work_units.checked(db, p)
        organization(self.s, db, plan)
        self.s.work_flows.guard_proposal(db, item, p)
        auth = self.s.work_units.authorization(db, driver)
        proposals = self.s.work_proposals
        if (p.get('authorization') != self.s.work_units.auth_ref(auth)
                or p.get('frontier') != self.s.work_units.frontier(db, item['id'])
                or p.get('proposal') != proposals.reference(proposals.current(db, item['id']))):
            raise ValueError('负责人授权、前沿或提案版本已变化。')
        rows = self.s.work_units.rows(db, item['id'])
        run, evidence = proposals.source(db, item, driver, plan, rows, p.get('run_id'))
        if p.get('result_version') != evidence['version']:
            raise ValueError('负责人源结果版本已变化。')
        decision, target, problems, state = None, None, [], 'INVALID'
        extra = {}
        try:
            self.validate_source(db, item, plan, rows, run)
            decision = self.parse(evidence['answer'], run['input']['coordination_context']['protocol'])
            context = run['input']['coordination_context']
            if 'pdca_rounds' in context and decision['action'] not in {'dispatch', 'deliver', 'pause'}:
                raise ValueError('PDCA 已固定阶段、顺序和成员；如需调整请暂停。')
            self.validate_decision(context, decision)
            if decision['action'] == 'dispatch':
                candidate = next((c for c in context['candidates'] if c['unit']['key'] == decision['unit_key']), None)
                if (not candidate or not candidate['ready_after_proposal']
                        or candidate['retry_of'] != decision['retry_of']):
                    raise ValueError('负责人只能选择本轮冻结的就绪候选。')
                target = proposals.target(db, item, plan, rows, decision)
                state = 'VALID'
            elif decision['action'] == 'deliver':
                if set(decision['unit_keys']) != set(context['finals']):
                    raise ValueError('负责人须交付本轮准确整合成果。')
                self.delivery(db, plan, rows)
                state = 'DELIVERY_READY'
            elif decision['action'] != 'pause':
                state, field, preview = self.s.leader_revisions.preview(db, item, driver, plan, rows, run, decision)
                extra[field] = preview
            else:
                state = 'PAUSE_SUGGESTED'
        except ValueError as exc:
            problems.append(str(exc))
        snapshot = {'protocol': run['input']['coordination_context']['protocol'], **extra, 'item_revision': item['revision'],
                    'driver': self.s.work_drivers.reference(driver),
                    'plan': {k: plan[k] for k in ('id', 'version')},
                    'authorization': self.s.work_units.auth_ref(auth), 'frontier': p['frontier'],
                    'state': state, 'decision': decision, 'target': target, 'problems': problems,
                    'source': {k:evidence[k] for k in ('run_id','task_id','attempt_id','owner','session_id','turn_id','connection_fingerprint','result_digest','version')},
                    'answer_digest': digest(evidence['answer']), 'answer': evidence['answer']}
        row = {'id': 'work-proposal:' + uid(), 'item_id': item['id'], 'snapshot': snapshot,
               'version': digest(snapshot), 'created': time.time(), 'command_id': self.s.command_identity}
        self.s.collaboration.put(db, 'work_proposal', row)
        self.s.collaboration.put(db, 'work_proposal_current',
                                 {'id': 'proposal-current:' + item['id'], 'proposal_id': row['id']})
        self.s.store.event(run['task_id'], run['attempt_id'], 'work_proposal_captured', row, db)
        return {'proposal': row, 'no_dispatch': True}

    def validate_decision(self, context, decision):
        """Organization policies may require additional evidence before dispatch."""

    def delivery(self, db, plan, rows):
        entries = []
        for unit in plan['units']:
            resolved = self.s.work_units.resolve(db, plan, unit['key'], rows)
            run, e = resolved['run'], resolved['evidence']
            if not run or resolved['problems']:
                raise ValueError('负责人交付尚缺准确声明工作：' + unit['title'])
            self.s.work_artifacts.require(e, 'checked' if unit['checks'] else 'files' if unit['outputs'] else 'result')
            entries.append(self.s.work_flows.delivered_unit(unit, run, e['result_version'], e['file_artifacts']))
        return entries

    def next_round(self, db, plan, auth, runs):
        blocked = self.s.work_flows.dispatch_block(auth, runs)
        if blocked:
            return 'BLOCKED', blocked, None
        unit = next(u for u in plan['units'] if u['key'] == self.coordinate)
        return 'READY', '开展下一轮真实负责人协调', {
            'unit': {'key': unit['key'], 'revision': digest(unit)}, 'leader_round': self.round(db, plan, runs)}

    def codex_quota_denied(self, db, run, flow, evidence):
        if (evidence['state'] != 'FAILED' or not evidence['exact_identity'] or evidence['blockers']
                or (run.get('flow') or {}).get('generation') != flow['generation']):
            return False
        if self.s.store.task(run['task_id'], db)['spec']['provider'] != 'codex':
            return False
        return bool(db.execute("""
            SELECT 1 FROM events
            WHERE task_id=? AND attempt_id=? AND kind='native_event'
              AND json_extract(payload,'$.method')='turn/completed'
              AND json_extract(payload,'$.params.threadId')=?
              AND json_extract(payload,'$.params.turn.id')=?
              AND json_extract(payload,'$.params.turn.status')='failed'
              AND json_extract(payload,'$.params.turn.error.codexErrorInfo')='usageLimitExceeded'
            LIMIT 1
            """, (run['task_id'], run['attempt_id'], evidence['session_id'], evidence['turn_id'])).fetchone())

    def decision(self, db, item, row, plan, auth, runs):
        current = [r for r in runs if r['plan_id'] == plan['id']]
        revisions = revisions_enabled(organization(self.s, db, plan))
        for unit in plan['units']:
            if unit['role'] == 'coordinate':
                continue
            history = [r for r in current if r['unit_key'] == unit['key']]
            if not history:
                continue
            run = history[-1]
            e = self.s.work_units.evidence(db, run)
            if not e['result_available']:
                if revisions and self.codex_quota_denied(db, run, row, e):
                    return 'BLOCKED', f"单元 {unit['title']} 的 Codex 连接额度已达上限，请在额度恢复后明确启动。", None
                if revisions and e['exact_identity'] and e['state'] in {'FAILED_NO_SEND','FAILED','INTERRUPTED','STOPPED_RUNTIME'} and not e['blockers']:
                    continue
                return 'BLOCKED', '负责人执行单元尚无准确可用结果，保留原终态并明确处理', None
            if unit['outputs']:
                files = e['file_artifacts']
                if files['state'] == 'MISSING':
                    return 'CAPTURE_READY', '保存负责人执行成果及检查', {
                        'run_id': run['id'], 'result_version': e['result_version'], 'artifact': None}
                if auth.get('auto_rework_checks') and files['check_status'] == 'FAIL':
                    blocked = self.s.work_flows.dispatch_block(auth, runs)
                    if blocked:
                        return 'BLOCKED', blocked, None
                    try:
                        context = self.s.work_flows.check_rework(db, plan, unit, run, e, runs, auth)
                    except ValueError as exc:
                        if revisions and files['state']=='CURRENT' and any(r['plan_id']==plan['id'] and r['unit_key']==unit['key'] and r['input'].get('check_rework') for r in runs):
                            continue
                        return 'BLOCKED', str(exc), None
                    return 'REWORK_READY', '按原授权返工一次', {
                        'unit': {'key': unit['key'], 'revision': digest(unit)}, 'retry_of': run['id'],
                        'check_rework': digest(context), 'context': context}
                try:
                    self.s.work_artifacts.require(e, 'checked' if unit['checks'] else 'files')
                except ValueError as exc:
                    if revisions and files['state']=='CURRENT' and files['check_status']=='FAIL':
                        continue
                    if revisions and files['state'] == 'STALE':
                        paths = '、'.join(files['required_outputs'])
                        problems = '；'.join(files['problems'])
                        return 'BLOCKED', f"单元 {unit['title']} 的登记产物失效（{paths}）：{problems}", None
                    return 'BLOCKED', str(exc), None
        coordinates = [r for r in current if r['unit_key'] == self.coordinate]
        proposals = self.s.work_proposals
        if coordinates:
            run = coordinates[-1]
            e = self.s.work_units.evidence(db, run)
            explicit_recheck = bool(revisions and row.get('generation') and run.get('flow')
                                    and row['generation'] != run['flow']['generation'])
            if not e['result_available']:
                if revisions and self.codex_quota_denied(db, run, row, e):
                    return 'BLOCKED', '负责人协调的 Codex 连接额度已达上限，请在额度恢复后明确启动。', None
                if explicit_recheck and e['exact_identity'] and e['state'] in {'FAILED_NO_SEND','FAILED','INTERRUPTED','STOPPED_RUNTIME'} and not e['blockers']:
                    return self.next_round(db, plan, auth, runs)
                return 'BLOCKED', '负责人协调结果或退出尚不可用', None
            proposal = proposals.current(db, item['id'])
            if not proposal or proposal['snapshot']['source']['run_id'] != run['id']:
                return 'PROPOSAL_READY', '核对本轮负责人决策', {
                    'run_id': run['id'], 'result_version': e['version'], 'proposal': proposals.reference(proposal)}
            consumed = any(r.get('proposal') == proposals.reference(proposal) for r in current)
            if not consumed:
                issues = proposals.live_issues(db, item, proposal)
                state = proposal['snapshot']['state']
                if state == 'DELIVERY_READY':
                    issues = [i for i in issues if i != '新增派发授权已到期']
                if issues:
                    snapshot = proposal['snapshot']
                    if (explicit_recheck and digest(snapshot)==proposal['version']
                            and snapshot['plan']=={k:plan[k] for k in ('id','version')}
                            and snapshot['item_revision']==item['revision']
                            and snapshot['frontier']==self.s.work_units.frontier(db,item['id'])
                            and snapshot['source']['version']==e['version']):
                        try:
                            self.validate_source(db,item,plan,runs,run)
                        except ValueError:
                            pass
                        else:
                            return self.next_round(db,plan,auth,runs)
                    return 'BLOCKED', '；'.join(issues), None
                if state == 'DELIVERY_READY':
                    return 'RESULTS_AVAILABLE', '负责人已核对整合交付；Human使用验收另列', self.delivery(db, plan, runs)
                if state in {'ADJUSTMENT_READY','REWORK_PREVIEW','UNIT_REVISION_PREVIEW','FILE_REWORK_PREVIEW'}:
                    return 'REVISION_READY', '应用准确负责人修订并按原授权继续', {'proposal':proposals.reference(proposal)}
                if state != 'VALID':
                    if explicit_recheck:
                        return self.next_round(db,plan,auth,runs)
                    return 'BLOCKED', '；'.join(proposal['snapshot']['problems']) or proposal['snapshot']['decision']['reason'], None
                blocked = self.s.work_flows.dispatch_block(auth, runs)
                if blocked:
                    return 'BLOCKED', blocked, None
                return 'READY', '按准确负责人决策执行', {
                    'unit': proposal['snapshot']['target'], 'proposal': proposals.reference(proposal),
                    'retry_of': proposal['snapshot']['decision']['retry_of']}
        return self.next_round(db,plan,auth,runs)
