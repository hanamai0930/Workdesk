"""Exact coordinator replies become bounded proposals, then pass normal dispatch."""
import json
import time

from .contracts import bounded_text
from .work_leader import organization, strategy, PROTOCOL as LEADER_PROTOCOL
from .store import digest, uid
from . import work_adjustments, work_reworks, work_unit_revisions, work_file_reworks
from .work_rework_diagnostics import diagnostic_unit, file_diagnostic_unit


RETRY_STATES = {'FAILED_NO_SEND', 'FAILED', 'INTERRUPTED', 'STOPPED_RUNTIME'}


def parse(text):
    if not isinstance(text, str) or len(text) > 32000:
        raise ValueError('提案 JSON 最多32000字。')

    def pairs(items):
        value = {}
        for key, item in items:
            if key in value:
                raise ValueError('提案 JSON 字段重复。')
            value[key] = item
        return value

    def constant(_):
        raise ValueError('提案 JSON 不接受非有限值。')

    try:
        value = json.loads(text, object_pairs_hook=pairs, parse_constant=constant)
    except (ValueError, RecursionError):
        raise ValueError('提案须为单一严格 JSON 对象，不能含重复字段、非有限值或正文围栏。') from None
    if not isinstance(value, dict) or type(value.get('schema_version')) is not int or value['schema_version'] != 1:
        raise ValueError('提案协议版本须为整数1。')
    action = value.get('action')
    fields = {'schema_version', 'action', 'reason'}
    if action == 'dispatch':
        fields |= {'unit_key', 'retry_of'}
    elif action == 'adjust_dependencies':
        fields.add('changes')
    elif action in ('rework_units', 'file_rework_units'):
        fields.add('changes')
    elif action == 'revise_units':
        fields.add('changes')
    elif action != 'pause':
        raise ValueError('提案仅支持派发、依赖调整/文字或文件返工/局部修订预览或建议暂停。')
    if set(value) != fields:
        raise ValueError('提案字段不符；不能附带命令、资源、目标或授权更改。')
    value['reason'] = bounded_text(value.get('reason'), '提案依据', 2000)
    if action == 'adjust_dependencies':
        work_adjustments.changes(value['changes'])
    if action == 'rework_units':
        work_reworks.changes(value['changes'])
    if action == 'file_rework_units':
        work_file_reworks.changes(value['changes'])
    if action == 'revise_units':
        work_unit_revisions.changes(value['changes'])
    if action == 'dispatch' and (not isinstance(value['unit_key'], str) or
            (value['retry_of'] is not None and not isinstance(value['retry_of'], str))):
        raise ValueError('提案单元或重试引用无效。')
    return value


class WorkProposals:
    def __init__(self, service):
        self.s = service

    def handlers(self):
        return {'work-item.proposal-check': self.capture}

    @staticmethod
    def reference(row):
        return {k: row[k] for k in ('id', 'version')} if row else None

    def current(self, db, item_id):
        r = db.execute("SELECT data FROM collaboration WHERE kind='work_proposal_current' AND id=?",
                       ('proposal-current:' + item_id,)).fetchone()
        return self.s.collaboration.get(db, 'work_proposal', json.loads(r[0])['proposal_id']) if r else None

    def source(self, db, item, driver, plan, rows, run_id):
        sources = [r for r in rows if r['plan_id'] == plan['id'] and r['generation'] == driver['generation'] and
                   r['input']['unit']['role'] == 'coordinate']
        if not sources or sources[-1]['id'] != run_id:
            raise ValueError('须核对本责任与方案的最新准确提案回合。')
        run = sources[-1]
        if plan.get('coordinator_file_handoff_root'):
            from .work_coordinator_handoff_runtime import reader
            reader(self.s, db, plan).read(db, plan)
        unit = next((u for u in plan['units'] if u['key'] == run['unit_key']), None)
        if (not unit or digest(unit) != run['unit_revision'] or unit['member']['reference'] != plan['coordinator']['reference'] or
                plan['mode'] == 'fixed'):
            raise ValueError('提案来源不是当前负责人／主持人的声明单元。')
        evidence = self.s.work_units.evidence(db, run)
        if not evidence['result_available'] or not evidence['exact_identity']:
            raise ValueError('准确提案结果尚不可用或退出证据不足。')
        self.s.work_rework_diagnostics.validate_source(db, plan, unit, rows, run)
        self.s.work_file_rework_diagnostics.validate_source(db, plan, unit, rows, run)
        for dependency in run['input']['dependencies']:
            resolved = self.s.work_units.resolve(db, plan, dependency['unit_key'], rows)
            if resolved['problems'] or not resolved['evidence'] or resolved['evidence']['version'] != dependency['version']:
                raise ValueError('提案回合的前置准确结果或文件证据已变化；不能将旧回复重新绑定到新输入。')
        return run, evidence

    def target(self, db, item, plan, rows, decision, *, pending_source=None):
        if plan.get('coordinator_file_handoff_root'):
            from .work_coordinator_handoff_runtime import enabled, reader
            if not enabled(self.s, db, plan):
                raise ValueError('新关系根的普通交付运行尚未开放。')
            unit = next((u for u in plan['units'] if u['key'] == decision['unit_key']), None)
            if not unit or unit['role'] == 'coordinate' or decision['retry_of'] is not None:
                raise ValueError('新根只允许原声明普通单元首次运行。')
            return reader(self.s, db, plan).target(db, item, plan, unit, pending_source)
        unit = next((u for u in plan['units'] if u['key'] == decision['unit_key']), None)
        if not unit or unit['role'] == 'coordinate':
            raise ValueError('提案只能选择方案中已声明的普通工作单元。')
        resolved = self.s.work_units.resolve(db, plan, unit['key'], rows)
        if resolved['inherited']:
            raise ValueError('该普通单元已有原成果沿用引用，不能重复派发。')
        history = [r for r in rows if r['plan_id'] == plan['id'] and r['unit_key'] == unit['key']]
        if history:
            e = self.s.work_units.evidence(db, history[-1])
            if decision['retry_of'] != history[-1]['id'] or e['state'] not in RETRY_STATES or e['blockers']:
                raise ValueError('只有最新已知失败／停止可明确重试；已完成或未知单元不能重发。')
        elif decision['retry_of'] is not None:
            raise ValueError('提案重试来源不存在。')
        dependency_unit = {**unit, 'depends': [k for k in unit['depends'] if k != pending_source]}
        self.s.work_units.input_for(db, item, plan, dependency_unit, rows)
        return {'key': unit['key'], 'revision': digest(unit)}

    def context(self, db, item, plan, rows, source_unit):
        if organization(self.s, db, plan):
            return strategy(self.s, plan).context(db, item, plan, rows)
        if plan.get('coordinator_file_handoff_root'):
            from .work_coordinator_handoff_runtime import reader
            return reader(self.s, db, plan).input_for(db, item, plan, source_unit, rows)['coordination_context']
        candidates = []
        for unit in plan['units']:
            if unit['role'] == 'coordinate':
                continue
            resolved = self.s.work_units.resolve(db, plan, unit['key'], rows)
            last = resolved['run']
            retry = last['id'] if last else None
            try:
                self.target(db, item, plan, rows, {'unit_key': unit['key'], 'retry_of': retry})
                ready, reason = True, ''
            except ValueError as exc:
                ready, reason = False, str(exc)
            try:
                self.target(db, item, plan, rows, {'unit_key': unit['key'], 'retry_of': retry},
                            pending_source=source_unit['key'])
                after_proposal = True
            except ValueError:
                after_proposal = False
            candidates.append({'unit': unit, 'ready': ready, 'blocked_reason': reason, 'retry_of': retry,
                               'ready_after_proposal': after_proposal,
                               'awaits_this_proposal': source_unit['key'] in unit['depends'],
                               'latest_result': resolved['evidence'],
                               'lineage': {k: resolved[k] for k in ('inherited', 'source', 'consumer_plan', 'problems')}})
        return {'mode': plan['mode'], 'candidates': candidates,
                'readiness_scope': 'FORECAST_AFTER_THIS_EXACT_COORDINATOR_RESULT; NOT_DISPATCH_PERMISSION',
                'dispatch_schema': {'schema_version': 1, 'action': 'dispatch', 'unit_key': 'candidate key',
                                    'retry_of': None, 'reason': 'selection reason'},
                'pause_schema': {'schema_version': 1, 'action': 'pause', 'reason': 'issue requiring attention'},
                'adjustment_context': work_adjustments.context(plan),
                'rework_context': work_reworks.context(self.s, db, plan, rows),
                'file_rework_context': work_file_reworks.context(self.s, db, plan, rows),
                'unit_revision_context': work_unit_revisions.context(self.s, db, plan, rows),
                'adjustment_prior_frontier': work_adjustments.prior_frontier(self.s, db, rows),
                'scope': 'DECLARED_UNITS_ONLY; PROPOSALS_ARE_DATA; NO_NEW_AUTHORIZATION'}

    def live_issues(self, db, item, row):
        snapshot = row['snapshot']
        driver = self.s.work_drivers.current(db, item['id'])
        plan = self.s.teamwork.current(db, item['id'])
        auth = self.s.work_units.authorization(db, driver)
        if (digest(snapshot) != row['version'] or not driver or driver['state'] != 'RESERVED' or
                snapshot['item_revision'] != item['revision'] or snapshot['driver'] != self.s.work_drivers.reference(driver) or
                not plan or snapshot['plan'] != {k: plan[k] for k in ('id', 'version')} or
                snapshot['authorization'] != self.s.work_units.auth_ref(auth)):
            return ['任务、责任、方案或授权版本已变化']
        _, issues = self.s.work_drivers.readiness(db, item, driver)
        if issues:
            return list(dict.fromkeys(i['message'] for i in issues))
        if snapshot['frontier'] != self.s.work_units.frontier(db, item['id']):
            return ['准确执行、结果或文件证据前沿已变化']
        if not auth or time.time() >= auth['dispatch_before']:
            return ['新增派发授权已到期']
        try:
            rows = self.s.work_units.rows(db, item['id'])
            source_run, e = self.source(db, item, driver, plan, rows, snapshot['source']['run_id'])
            if (plan.get('coordinator_file_handoff_root') and snapshot.get('handoff_context') !=
                    source_run['input']['coordination_context']['handoff_context']):
                raise ValueError('提案的准确新根冻结context引用已变化。')
            if organization(self.s, db, plan):
                strategy(self.s, plan).validate_source(db, item, plan, rows, source_run)
            if e['version'] != snapshot['source']['version']:
                raise ValueError('提案源结果版本已变化。')
            if snapshot['state'] == 'VALID' and self.target(db, item, plan, rows, snapshot['decision']) != snapshot['target']:
                raise ValueError('提案目标版本已变化。')
            if snapshot['state'] == 'ADJUSTMENT_READY' and not plan.get('coordinator_file_handoff_root'):
                run, _ = self.source(db, item, driver, plan, rows, snapshot['source']['run_id'])
                current = work_adjustments.preview(self.s, db, item, driver, plan, rows, run, snapshot['decision'])
                if current != snapshot['adjustment']:
                    raise ValueError('依赖调整的成果、用量或影响范围已变化。')
            if plan.get('coordinator_file_handoff_root') and snapshot['state'] == 'POST_ROUND_ADJUSTMENT_PREVIEW':
                protocol = source_run['input']['handoff_coordination_context']['protocol']
                if protocol == 'COORDINATOR_FILE_HANDOFF_COORDINATION_INPUT_V8':
                    from .work_coordinator_handoff_post_round_v2_preview import preview as post_preview
                elif protocol == 'COORDINATOR_FILE_HANDOFF_COORDINATION_INPUT_V7':
                    from .work_coordinator_handoff_post_round_successor_preview import preview as post_preview
                elif protocol == 'COORDINATOR_FILE_HANDOFF_COORDINATION_INPUT_V6':
                    from .work_coordinator_handoff_post_round_preview import preview as post_preview
                else:
                    raise ValueError('ROUND后调整提案来源协议未开放。')
                if post_preview(self.s,db,item,driver,plan,rows,source_run,snapshot['decision']) != snapshot['adjustment']:
                    raise ValueError('ROUND后调整预览的准确来源或影响已变化。')
            if plan.get('coordinator_file_handoff_root') and snapshot['state'] in {'ADJUSTMENT_PREVIEW', 'ADJUSTMENT_READY'}:
                from .work_coordinator_handoff_adjustments import preview
                current = preview(self.s, db, item, driver, plan, rows, source_run, snapshot['decision'])
                if current != snapshot['adjustment']:
                    raise ValueError('新根调整预览的完整来源、当前资格或影响已变化。')
            if snapshot['state'] == 'REWORK_PREVIEW':
                run, _ = self.source(db, item, driver, plan, rows, snapshot['source']['run_id'])
                current = work_reworks.preview(self.s, db, item, driver, plan, rows, run, snapshot['decision'])
                if current != snapshot['rework']:
                    raise ValueError('文字返工的诊断、用量或影响范围已变化。')
            if snapshot['state'] == 'UNIT_REVISION_PREVIEW':
                run, _ = self.source(db, item, driver, plan, rows, snapshot['source']['run_id'])
                current = work_unit_revisions.preview(self.s, db, item, driver, plan, rows, run, snapshot['decision'])
                if current != snapshot['unit_revision']:
                    raise ValueError('普通单元局部修订的来源、资源或影响范围已变化。')
            if snapshot['state'] == 'FILE_REWORK_PREVIEW':
                run, _ = self.source(db, item, driver, plan, rows, snapshot['source']['run_id'])
                current = work_file_reworks.preview(self.s, db, item, driver, plan, rows, run, snapshot['decision'])
                if current != snapshot['file_rework']:
                    raise ValueError('文件返工的原快照、输入、用量或影响范围已变化。')
        except ValueError as exc:
            return [str(exc)]
        return []

    def view(self, db, item):
        from .work_coordinator_handoff_text_rework_runtime import present
        plan = self.s.teamwork.current(db, item['id'])
        if present(self.s, db, plan):
            from .work_coordinator_handoff_text_rework_public import proposal_view
            return proposal_view(self.s, db, item, plan)
        row = self.current(db, item['id'])
        if not row:
            return {'record': None, 'reference': None, 'state': 'NONE', 'problems': []}
        consumed = next((r for r in self.s.work_units.rows(db, item['id']) if r.get('proposal') == self.reference(row)), None)
        transition = self.s.work_transitions.consumption(db, row)
        issues = [] if consumed or transition else self.live_issues(db, item, row)
        flow = self.s.work_flows.current(db,item['id']) if organization(self.s,db,plan) else None
        manual = not (flow and flow['state'] in {'RUNNING','SCHEDULED'})
        return {'record': row, 'reference': self.reference(row),
                'state': 'CONSUMED' if consumed or transition else 'STALE' if issues else row['snapshot']['state'],
                'problems': issues, 'consumed_by': consumed['id'] if consumed else None,
                'transition': transition,
                'can_apply_file_rework': bool(manual and not consumed and not transition and not issues and
                    row['snapshot']['state'] == 'FILE_REWORK_PREVIEW' and
                    not row['snapshot']['file_rework']['execution_blockers']),
                'can_apply': bool(manual and not consumed and not transition and not issues and row['snapshot']['state'] == 'ADJUSTMENT_READY'),
                'can_apply_rework': bool(manual and not consumed and not transition and not issues and
                    row['snapshot']['state'] == 'REWORK_PREVIEW' and not row['snapshot']['rework']['execution_blockers']),
                'can_apply_unit_revision': bool(manual and not consumed and not transition and not issues and
                    row['snapshot']['state'] == 'UNIT_REVISION_PREVIEW' and
                    not row['snapshot']['unit_revision']['execution_blockers'])}

    def capture(self, db, p):
        if organization(self.s, db, self.s.teamwork.current(db, p.get("id"))):
            return strategy(self.s, self.s.teamwork.current(db, p.get("id"))).capture(db, p)
        from .work_coordinator_handoff_text_rework_runtime import child
        from . import work_coordinator_handoff_text_rework_contracts as text
        if child(self.s.teamwork.current(db, p.get('id'))):
            from .work_coordinator_handoff_text_rework_child_commands import capture_child
            return capture_child(self.s, db, p)
        row = db.execute("SELECT data FROM collaboration WHERE kind='work_unit_run' AND id=?", (p.get('run_id'),)).fetchone()
        if row and json.loads(row[0]).get('input', {}).get('handoff_coordination_context', {}).get('protocol') == text.REVIEW_INPUT:
            from .work_coordinator_handoff_text_rework_review_check_scope import capture_review
            return capture_review(self.s, db, p)
        from .work_coordinator_handoff_post_round_v3_public import is_v3
        if is_v3(self.s.teamwork.current(db,p.get('id'))):
            from .work_coordinator_handoff_post_round_v3_capture import Capture
            return Capture(self.s).capture(db,p)
        if set(p) - {'id', 'revision', 'current_driver', 'plan', 'authorization', 'frontier', 'run_id', 'result_version', 'proposal'}:
            raise ValueError('提案检查字段不支持。')
        item, driver, plan = self.s.work_units.checked(db, p)
        self.s.work_flows.require_stopped(db, item['id'])
        if plan['mode'] == 'fixed':
            raise ValueError('固定流程使用已声明依赖，不接受负责人／主持人提案。')
        auth = self.s.work_units.authorization(db, driver)
        if not auth or p.get('authorization') != self.s.work_units.auth_ref(auth):
            raise ValueError('提案须绑定当前明确派发授权。')
        if p.get('frontier') != self.s.work_units.frontier(db, item['id']):
            raise ValueError('提案检查的准确执行前沿已变化。')
        if p.get('proposal') != self.reference(self.current(db, item['id'])):
            raise ValueError('已保存提案版本已变化，请刷新。')
        rows = self.s.work_units.rows(db, item['id'])
        run, e = self.source(db, item, driver, plan, rows, p.get('run_id'))
        handoff_history = None
        if plan.get('coordinator_file_handoff_root'):
            from .work_coordinator_handoff_runtime import reader
            handoff_history = reader(self.s, db, plan).read(db, plan)
        if p.get('result_version') != e['version']:
            raise ValueError('提案源结果版本已变化。')
        state, decision, target, problems, adjustment, rework, unit_revision = 'INVALID', None, None, [], None, None, None
        file_rework = None
        try:
            decision = parse(e['answer'])
            unit = next(u for u in plan['units'] if u['key'] == run['unit_key'])
            if plan.get('coordinator_file_handoff_root') and decision['action'] not in run['input']['coordination_context']['allowed_actions']:
                raise ValueError('此新根冻结回合只开放准确暂停建议，普通交付及修订入口待实施。')
            if diagnostic_unit(plan, unit):
                action = 'file_rework_units' if file_diagnostic_unit(plan, unit) else 'rework_units'
                if decision['action'] not in {action, 'pause'}:
                    raise ValueError(('文件' if file_diagnostic_unit(plan, unit) else '文字') +
                                     '诊断回合只接受准确返工预览或暂停建议。')
            if decision['action'] == 'dispatch':
                candidate = next((c for c in run['input']['coordination_context']['candidates']
                                  if c['unit']['key'] == decision['unit_key']), None)
                if (not candidate or not candidate['ready_after_proposal'] or
                        candidate['retry_of'] != decision['retry_of']):
                    raise ValueError('提案只能选择本次准确输入中声明可在提案完成后准入的候选。')
                target = self.target(db, item, plan, rows, decision)
                state = 'VALID'
            elif decision['action'] == 'adjust_dependencies':
                if plan.get('coordinator_file_handoff_root'):
                    from .work_coordinator_handoff_post_round_contracts import PROTOCOL as POST_PROTOCOL
                    from .work_coordinator_handoff_post_round_successor_contracts import PROTOCOL as SUCCESSOR_PROTOCOL
                    from .work_coordinator_handoff_post_round_v2_successor_contracts import PROTOCOL as V2_PROTOCOL
                    if run['input']['handoff_coordination_context']['protocol'] == V2_PROTOCOL:
                        from .work_coordinator_handoff_post_round_v2_preview import preview
                        next_state = 'POST_ROUND_ADJUSTMENT_PREVIEW'
                    elif run['input']['handoff_coordination_context']['protocol'] == SUCCESSOR_PROTOCOL:
                        from .work_coordinator_handoff_post_round_successor_preview import preview
                        next_state = 'POST_ROUND_ADJUSTMENT_PREVIEW'
                    elif run['input']['handoff_coordination_context']['protocol'] == POST_PROTOCOL:
                        from .work_coordinator_handoff_post_round_preview import preview
                        next_state = 'POST_ROUND_ADJUSTMENT_PREVIEW'
                    else:
                        from .work_coordinator_handoff_adjustments import preview
                        next_state = 'ADJUSTMENT_READY'
                    adjustment = preview(self.s, db, item, driver, plan, rows, run, decision)
                    state = next_state
                else:
                    adjustment = work_adjustments.preview(self.s, db, item, driver, plan, rows, run, decision)
                    state = 'ADJUSTMENT_READY'
            elif decision['action'] == 'rework_units':
                rework = work_reworks.preview(self.s, db, item, driver, plan, rows, run, decision)
                state = 'REWORK_PREVIEW'
            elif decision['action'] == 'revise_units':
                unit_revision = work_unit_revisions.preview(self.s, db, item, driver, plan, rows, run, decision)
                state = 'UNIT_REVISION_PREVIEW'
            elif decision['action'] == 'file_rework_units':
                file_rework = work_file_reworks.preview(self.s, db, item, driver, plan, rows, run, decision)
                state = 'FILE_REWORK_PREVIEW'
            else:
                state = 'PAUSE_SUGGESTED'
        except ValueError as exc:
            problems.append(str(exc))
        snapshot = {'item_revision': item['revision'], 'driver': self.s.work_drivers.reference(driver),
                    'plan': {k: plan[k] for k in ('id', 'version')}, 'authorization': self.s.work_units.auth_ref(auth),
                    'frontier': p['frontier'], 'state': state, 'decision': decision, 'target': target, 'problems': problems,
                    'source': {k: e[k] for k in ('run_id', 'task_id', 'attempt_id', 'owner', 'session_id', 'turn_id',
                                                'connection_fingerprint', 'result_digest', 'version')},
                    'answer_digest': digest(e['answer']), 'answer': e['answer'] if len(e['answer']) <= 32000 else None}
        if adjustment:
            snapshot['adjustment'] = adjustment
        if plan.get('coordinator_file_handoff_root'):
            snapshot['handoff_context'] = run['input']['coordination_context']['handoff_context']
        if rework:
            snapshot['rework'] = rework
        if unit_revision:
            snapshot['unit_revision'] = unit_revision
        if file_rework:
            snapshot['file_rework'] = file_rework
        row = {'id': 'work-proposal:' + uid(), 'item_id': item['id'], 'snapshot': snapshot, 'version': digest(snapshot),
               'created': time.time(), 'command_id': self.s.command_identity}
        self.s.collaboration.put(db, 'work_proposal', row)
        self.s.collaboration.put(db, 'work_proposal_current', {'id': 'proposal-current:' + item['id'], 'proposal_id': row['id']})
        self.s.store.event(run['task_id'], run['attempt_id'], 'coordinator_proposal_checked', row, db)
        if handoff_history is not None and (reader(self.s, db, plan).read(db, plan) != handoff_history or
                                            self.current(db, item['id']) != row):
            raise ValueError('提案保存最终历史、当前材料或准确指针已变化。')
        return {'proposal': row, 'no_dispatch': True}

    def require(self, db, item, plan, unit, p):
        if plan['mode'] == 'fixed' or unit['role'] == 'coordinate':
            if p.get('proposal') is not None:
                raise ValueError('此单元不接受提案派发引用。')
            return None
        from .work_coordinator_handoff_post_round_v3_public import is_v3
        if is_v3(plan):
            from .work_coordinator_handoff_post_round_v3_runtime import WorkCoordinatorHandoffPostRoundV3Runtime
            owner=WorkCoordinatorHandoffPostRoundV3Runtime(self.s); h=owner.read(db,plan)
            row=self.current(db,item['id'])
            if not row or p.get('proposal')!=self.reference(row) or any(f['run'].get('proposal')==self.reference(row) for f in h['frames']):
                raise ValueError('V3须引用当前尚未消费的准确新提案。')
            owner._proposal(db,plan,unit,h['frames'],{'reference':self.reference(row),
                **{k:row['snapshot'][k] for k in ('source','decision')}},h['task'],h['driver'],h['authorization'])
            return row
        if organization(self.s, db, plan) and p.get('check_rework'):
            row = self.current(db, item['id'])
            if not row or row['snapshot']['target'] != {'key': unit['key'], 'revision': digest(unit)}:
                raise ValueError('检查返工的原负责人决策已变化。')
            return row
        view = self.view(db, item)
        row = view['record']
        if not row or p.get('proposal') != view['reference'] or view['state'] != 'VALID':
            raise ValueError('须先检查并引用当前有效的负责人／主持人提案。')
        snapshot = row['snapshot']
        if snapshot['target'] != {'key': unit['key'], 'revision': digest(unit)} or p.get('retry_of') != snapshot['decision']['retry_of']:
            raise ValueError('派发单元或明确重试引用不符合准确提案。')
        return row
