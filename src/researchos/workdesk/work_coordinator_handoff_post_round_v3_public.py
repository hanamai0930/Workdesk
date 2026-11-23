"""Current V3 catalog projection; only the independent complete tree grants proof."""
from copy import deepcopy
import json
import time

from .store import digest
from .work_transitions import plan_ref
from .work_coordinator_handoff_records import PERMISSIONS

LINEAGE = 'COORDINATOR_FILE_HANDOFF_POST_ROUND_ADJUSTMENT_LINEAGE_V3'
CLOSED_REASON = '使用原声明单步操作；按准确材料阶段检查提案、派发和捕获。'


def responsibility(service, item, history):
    """Project only the independently verified Current; never grant runtime."""
    from . import work_coordinator_handoff_post_round_v3_responsibility_records as r
    from .work_coordinator_handoff_post_round_candidates import ref
    driver = history['driver']; auth = history['authorization']; usage = history['usage']
    scope = {'id': item['id'], 'revision': history['task']['revision'],
        'plan': plan_ref(history['child_plan']), 'current_driver': service.work_drivers.reference(driver),
        'authorization': service.work_units.auth_ref(auth), 'frontier': r.frontier(history),
        'saved_registration': ref(history['registration'])}
    actions = {}
    for action, allowed in [('resume', ('PAUSED',)), ('pause', ('RESERVED',)), ('release', ('PAUSED', 'RESERVED'))]:
        problems = []
        if driver['state'] not in allowed: problems.append('当前责任状态不支持此动作。')
        if action == 'resume':
            problems += [i['message'] for i in history['responsibility_conditions'] if i['code'] != 'V3_LIFECYCLE_CLOSED']
            if usage['unknown_requests']: problems.append('原累计存在待核对回执。')
            if usage['attempts'] >= auth['max_attempts_total']: problems.append('原累计尝试额度已用完。')
            if time.time() >= auth['dispatch_before']: problems.append('已超过原新增派发截止。')
        request = r.request_shape({**scope, 'action': action})
        actions[action] = {'kind': r.COMMAND, 'request': request, 'enabled': not problems, 'problems': problems}
    commands = {c['id']: c for c in history['responsibility']['commands']}
    entries = []
    for row in history['responsibility']['transitions']:
        command = commands.get(row['command_id'])
        entries.append({'transition': row, 'command': {'id': command['id'], 'kind': command['kind'],
            'payload': row['snapshot']['request']} if command else None,
            'receipt': json.loads(command['receipt']) if command else None})
    if history.get('successor_runtime_open'):
        for action in actions.values():
            action.update(enabled=False,problems=['首次后继已开启；本责任合同只覆盖原派发前责任。'])
    return {'protocol': 'COORDINATOR_FILE_HANDOFF_PUBLIC_RESPONSIBILITY_V3',
        'full_current_tree_verified': True, 'state': driver['state'], 'driver': driver,
        'original_driver': history['original_driver'], 'entries': entries, 'actions': actions,
        'successor_runtime_open': False, 'no_dispatch': True}


def is_v3(plan):
    # Routing/denial only. This predicate supplies no proof or permission.
    return bool(plan and plan.get('coordinator_file_handoff_post_round_lineage', {}).get('protocol') == LINEAGE)


def view(service, db, item, plan):
    from .work_coordinator_handoff_post_round_v3_responsibility_current import WorkCoordinatorHandoffPostRoundV3ResponsibilityCurrent as Current
    from .work_coordinator_handoff_post_round_v3_saved_component import COMMAND, receipt
    problems = []
    try:
        from .work_coordinator_handoff_post_round_v3_runtime import present, WorkCoordinatorHandoffPostRoundV3Runtime
        history = (WorkCoordinatorHandoffPostRoundV3Runtime(service) if present(service,db,plan) else Current(service)).read(db,plan)
    except (ValueError, OSError) as exc:
        history = None
        problems.append(str(exc))
    driver = service.work_drivers.current(db, item['id'])
    auth = service.work_units.authorization(db, driver)
    rows = service.work_units.rows(db, item['id'])
    flow = service.work_flows.view(db, item)
    frames = {f['run']['unit_key']: f for f in history['current_frames']} if history else {}
    qualifications = {u['definition']['key']: u['qualification'] for u in history['units']} if history else {}
    proposal = service.work_proposals.current(db, item['id'])
    proposal_view = proposal_projection(service,db,item,plan,history,proposal,problems)
    successor = successor_view(service,db,item,plan,history,problems)
    units = []
    for unit in plan['units']:
        frame = frames.get(unit['key'])
        run = frame['run'] if frame else None
        action = successor['actions'].get(unit['key'], {})
        enabled = action.get('enabled',False)
        reason = '；'.join(action.get('problems',problems)) or CLOSED_REASON
        units.append({'key': unit['key'], 'revision': digest(unit),
            'title': unit['title'], 'member_name': unit['member']['name'], 'role': unit['role'],
            'qualification': qualifications.get(unit['key'], 'UNVERIFIED'),
            'ready': enabled, 'dispatch_ready': enabled, 'can_send': enabled, 'can_retry': False,
            'can_check':bool(history and run and unit['role']!='coordinate' and unit['outputs'] and
                history['driver']['state']=='RESERVED' and history['driver']['instance']==service.instance and
                frame['evidence']['result_available'] and frame['evidence']['exact_identity'] and
                frame['evidence']['state']=='COMPLETED' and not frame['evidence']['blockers'] and not frame['files']['snapshots'] and
                not (unit['key']==plan['file_handoffs'][0]['source_unit'] and history['delivery']) and
                not (unit['key']==plan['file_handoffs'][0]['target_unit'] and history['completion'])),
            'can_check_proposal':bool(successor['proposal_action'].get('enabled') and
                successor['proposal_action']['request']['run_id']==(run['id'] if run else None)),
            'capture_result_version':frame['evidence']['version'] if frame else None,
            'blocked_reason': '' if enabled else reason,
            'dispatch_blocked_reason': '' if enabled else reason,
            'dependency_gate': unit['dependency_gate'], 'frozen_history': None,
            'lineage': {'inherited': bool(history and unit['key'] in plan.get('result_lineage', {})),
                'source': plan.get('result_lineage', {}).get(unit['key']) if history else None,
                'consumer_plan': plan_ref(plan), 'problems': list(problems)},
            'latest_run_id': run['id'] if run else None, 'result': frame['source_evidence'] if frame else None})
    root = service.collaboration.get(db, 'teamwork_plan', plan['coordinator_file_handoff_root']['plan_id'])
    saved = None
    if history:
        app = history['application']
        saved = {'saved_path': history['saved_path'], 'application': app, 'registration': history['registration'],
            'command': {'id': app['command_id'], 'kind': COMMAND, 'payload': app['snapshot']['request']},
            'receipt': receipt({**history, 'driver': history['original_driver']})}
    stage = {'protocol': 'COORDINATOR_FILE_HANDOFF_PUBLIC_SAVED_STAGE_V3', 'saved_v3': True,
        'root': history['root'] if history else plan_ref(root) if root else None, 'plan': plan_ref(plan),
        'phase': history['phase'] if history else 'BLOCKED', 'problems': problems,
        'historical_integrity': 'VERIFIED' if history else 'BLOCKED',
        'full_saved_tree_verified': bool(history), 'saved_edge_verified': bool(history),
        'successor_runtime_open': bool(history and history.get('successor_runtime_open')), 'post_round_adjustment': 'NOT_OPEN', 'v3_saved': saved,
        'v3_successor': successor,
        'full_current_tree_verified': bool(history),
        'v3_responsibility': responsibility(service, item, history) if history else None,
        'delivery': history['delivery'] if history else [], 'completion': history['completion'] if history else [],
        **PERMISSIONS}
    from .work_coordinator_handoff_text_rework_public import entry as text_entry
    text_rework = text_entry(service, db, plan) if history and history['phase'] == 'B_COMPLETED' else None
    all_frames = {f['run']['id']: f for f in history['frames']} if history else {}
    return deepcopy({'supported': True, 'single_step_supported': True, 'continuous_supported': False,
        'scheduling': 'V3_ORIGINAL_DECLARED_SERIAL', 'closed_reason': CLOSED_REASON, 'automatic_dispatch': False,
        'flow': {**flow, 'can_start': False}, 'authorization': auth,
        'authorization_reference': service.work_units.auth_ref(auth), 'can_authorize': False,
        'frontier': service.work_units.frontier(db, item['id']), 'units': units,
        'usage': service.work_units.usage(db, rows), 'proposal': proposal_view, 'text_rework': text_rework,
        'coordination_revision': {'can_revise': False, 'record': plan.get('coordination_revision'), 'problems': [CLOSED_REASON]},
        'rework_diagnostic': None, 'file_rework_diagnostic': None,
        'runs': [{**r, 'result': all_frames[r['id']]['source_evidence'] if r['id'] in all_frames else
            service.work_units.base_evidence(db, r)} for r in rows], 'handoff_stage': stage, 'no_dispatch': True})


def proposal_projection(service,db,item,plan,history,row,problems):
    result={'record':row,'reference':service.work_proposals.reference(row),'state':'STALE' if row else 'NONE',
        'problems':list(problems),'consumed_by':None,'transition':None,'can_apply':False,
        'can_apply_rework':False,'can_apply_file_rework':False,'can_apply_unit_revision':False}
    if not history or not row: return result
    consumed=next((f['run']['id'] for f in history['frames'] if f['run'].get('proposal')==service.work_proposals.reference(row)),None)
    if row['snapshot']['plan']!=plan_ref(plan):
        result.update(state='CONSUMED',consumed_by=history['application']['id'],transition=history['application']['snapshot'])
    elif consumed: result.update(state='CONSUMED',consumed_by=consumed)
    elif row['snapshot']['state']=='PAUSE_SUGGESTED':
        b=row['snapshot']; latest=next((f for f in reversed(history['frames'])
            if f['run']['plan_id']==plan['id'] and f['run']['input']['unit']['role']=='coordinate'),None)
        if (b['item_revision']==history['task']['revision'] and b['driver']==service.work_drivers.reference(history['driver']) and
                b['authorization']==service.work_units.auth_ref(history['authorization']) and
                b['frontier']==digest([{'run_id':f['run']['id'],'evidence':f['evidence']['version']} for f in history['frames']]) and
                latest and b['source']['run_id']==latest['run']['id']): result['state']='PAUSE_SUGGESTED'
    else:
        from .work_coordinator_handoff_post_round_v3_runtime import WorkCoordinatorHandoffPostRoundV3Runtime
        owner=WorkCoordinatorHandoffPostRoundV3Runtime(service)
        try:
            unit=next(u for u in plan['units'] if u['key']==row['snapshot']['target']['key'])
            owner._proposal(db,plan,unit,history['frames'],{'reference':service.work_proposals.reference(row),
                **{k:row['snapshot'][k] for k in ('source','decision')}},history['task'],history['driver'],history['authorization'])
            result['state']='VALID'
        except ValueError as exc: result['problems'].append(str(exc))
    return result


def successor_view(service,db,item,plan,history,problems):
    from .work_coordinator_handoff_post_round_v3_successor_contracts import request_for as first_request
    from .work_coordinator_handoff_post_round_v3_continuation_contracts import request_for
    from .work_coordinator_handoff_post_round_v3_continuation_proposals import request_for as proposal_request
    from .work_coordinator_handoff_post_round_v3_runtime import WorkCoordinatorHandoffPostRoundV3Runtime
    opened=bool(history and history.get('successor_runtime_open'))
    actions={}; driver=history['driver'] if history else None
    current=service.work_proposals.current(db,item['id'])
    owner=WorkCoordinatorHandoffPostRoundV3Runtime(service)
    admission=list(problems)
    if history:
        try: owner.admission(db,plan,history)
        except ValueError as exc: admission.append(str(exc))
    else: admission.append('完整当前来源尚未核对。')
    for unit in plan['units']:
        if unit['key'] in plan['historical_coordinators']: continue
        reasons=list(admission); request=None
        if not opened and unit['role']!='coordinate': reasons.append('先派发原声明的首次协调。')
        if history:
            try:
                owner._admit(plan,unit,history['frames'],history['phase'])
                if unit['role']!='coordinate':
                    if not current: raise ValueError('须先检查当前准确协调提案。')
                    if any(f['run'].get('proposal')==service.work_proposals.reference(current) for f in history['frames']):
                        raise ValueError('当前提案已经消费。')
                    owner._proposal(db,plan,unit,history['frames'],{'reference':service.work_proposals.reference(current),
                        **{k:current['snapshot'][k] for k in ('source','decision')}},history['task'],driver,history['authorization'])
            except ValueError as exc: reasons.append(str(exc))
            data={'task':history['task'],'plan':plan_ref(plan),'driver':driver,'authorization':history['authorization'],
                'frontier':history['frontier'],'selected_unit':{'definition':unit,'revision':digest(unit)},
                'proposal':service.work_proposals.reference(current) if unit['role']!='coordinate' else None}
            request=(request_for if opened else first_request)(data)
        actions[unit['key']]={'kind':'work-item.unit-send','request':request,'enabled':not reasons,'problems':reasons}
    pa={'kind':'work-item.proposal-check','request':None,'enabled':False,'problems':list(admission)}
    if opened:
        sources=[f for f in history['frames'] if f['run']['plan_id']==plan['id'] and f['run']['input']['unit']['role']=='coordinate']
        if sources and sources[-1]['source_evidence']['result_available']:
            pa['request']=proposal_request(owner,history,sources[-1],current)
            if current and current['snapshot']['source']['run_id']==sources[-1]['run']['id']:
                pa['problems'].append('本次协调结果已检查；使用原请求核对回执。')
            pa['enabled']=not pa['problems']
        else: pa['problems'].append('最新协调尚无准确退出结果。')
    entries=[]
    if opened:
        for row in history['successor_receipts']+history['continuation_receipts']:
            entries.append({'command':{'id':row['command_id'],'kind':'work-item.unit-send','payload':row['snapshot']['request']},
                'receipt':row['snapshot']['receipt']})
        for row in history['proposal_receipts']:
            entries.append({'command':{'id':row['command_id'],'kind':'work-item.proposal-check','payload':row['snapshot']['request']},
                'receipt':row['snapshot']['response']})
    observed=[]
    for run in service.work_units.rows(db,item['id']):
        if run['plan_id']!=plan['id']: continue
        evidence=service.work_units.base_evidence(db,run)
        observed.append({k:evidence[k] for k in ('run_id','task_id','attempt_id','state','exact_identity','result_available')})
    return {'protocol':'COORDINATOR_FILE_HANDOFF_PUBLIC_DECLARED_CONTINUATION_V3',
        'state':'OPENED' if opened else 'READY' if any(a['enabled'] for a in actions.values()) else 'BLOCKED',
        'full_current_tree_verified':bool(history),'actions':actions,'proposal_action':pa,'entries':entries,
        'first_coordinate_only':not opened,'ordinary_dispatch_open':opened,'automatic_dispatch':False,
        'execution_observation':{'observation_only':True,'runs':observed,'no_automatic_resend':True,'full_current_tree_verified':bool(history)}}
