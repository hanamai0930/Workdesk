"""Public text actions come from actual complete owners, including refusals."""
from copy import deepcopy

from .store import digest
from .work_coordinator_handoff_text_rework_parent import TextReworkParent
from .work_coordinator_handoff_text_rework_current import TextCurrent
from .work_coordinator_handoff_text_rework_review_replay import ReviewReplay
from .work_coordinator_handoff_text_rework_review_live import require_live
from .work_coordinator_handoff_text_rework_review_proposals import request_for as review_check_request
from .work_coordinator_handoff_text_rework_saved_values import request_for as apply_request, driver_ref, auth_ref
from .work_coordinator_handoff_text_rework_saved_records import ref
from .work_coordinator_handoff_text_rework_runtime import child, capability
from . import work_coordinator_handoff_text_rework_child_values as v
from . import work_coordinator_handoff_text_rework_contracts as c


def action(kind, request, problems, label):
    return {'kind': kind, 'request': deepcopy(request), 'enabled': request is not None and not problems,
        'problems': list(problems), 'label': label}


def entry(service, db, plan):
    result = {'state': 'NOT_OPEN', 'actions': {}, 'problems': []}
    try:
        parent = TextReworkParent.current(service, db, plan)
        if parent.parent_type != 'POST_V3':
            raise ValueError('本批只开放已运行POST_V3 B_COMPLETED父叶子的文字返工。')
        from .work_coordinator_handoff_text_rework_values import _coordinate_blockers
        h = parent.history
        for unit in plan['units']:
            if unit['role'] != 'coordinate':
                continue
            problems = _coordinate_blockers(plan, unit, set(), {f['run']['unit_key']: f for f in h['current_frames']})
            if h['usage']['attempts'] + 3 > h['authorization']['max_attempts_total']:
                problems.append('原累计不足以完成审阅、新协调及一个普通运行。')
            request = {'id': plan['item_id'], 'revision': h['task']['revision'], 'plan': ref(plan),
                'current_driver': driver_ref(h['driver']), 'authorization': auth_ref(h['authorization']),
                'frontier': v.frontier(h), 'unit': {'key': unit['key'], 'revision': digest(unit)},
                'retry_of': None, 'proposal': None, 'capability': None}
            result['actions']['review:' + unit['key']] = action(c.SEND, request, problems, '返工提案回合：' + unit['title'])
    except (ValueError, OSError) as exc:
        result['problems'].append(str(exc))
    return result


def _read(service, db, plan):
    if child(plan):
        return TextCurrent(service).read(db, plan)
    cap = capability(service, db, plan)
    result = ReviewReplay(db, service.locations.data, ref(cap)).read()
    excluded = {result['run']['attempt_id']} if not result['exited'] else set()
    require_live(service, db, cap, result['driver'], excluding=excluded)
    return result


def proposal_projection(result, *, saved):
    row = result['proposal']; consumed = None
    h = result['history']
    if row and saved:
        consumed = next((r['id'] for r in v.child_runs(result) if r.get('proposal') == ref(row)), None)
        if row == result['review']['proposal']:
            consumed = result['application']['id']
    impact = row['snapshot'].get('text_rework') if row else None
    return {'record': deepcopy(row), 'reference': ref(row) if row else None,
        'state': 'CONSUMED' if consumed else row['snapshot']['state'] if row else 'NONE',
        'problems': deepcopy(row['snapshot']['problems']) if row else [], 'consumed_by': consumed,
        'transition': None, 'can_apply': False, 'can_apply_file_rework': False, 'can_apply_unit_revision': False,
        'can_apply_rework': bool(not saved and h and row and row['snapshot']['state'] == 'COORDINATOR_TEXT_REWORK_PREVIEW' and impact and not impact['blockers'])}


def proposal_view(service, db, item, plan):
    return proposal_projection(_read(service, db, plan), saved=child(plan))


def view(service, db, item, plan):
    saved = child(plan); problems = []; result = None; actions = {}; h = None
    try:
        result = _read(service, db, plan); h = result['history']
        if saved:
            from .work_coordinator_handoff_text_rework_responsibility import request_for
            driver = result['driver']
            for name in ('resume', 'pause', 'release'):
                reasons = []
                try:
                    allowed = {'pause': {'RESERVED'}, 'resume': {'PAUSED'}, 'release': {'RESERVED', 'PAUSED'}}
                    if driver['state'] not in allowed[name]:
                        raise ValueError('当前责任状态不支持此动作。')
                    if any(f['source_evidence']['state'] == 'RESERVED' for f in h['frames']):
                        raise ValueError('当前仍有实际保留，先核对退出。')
                except ValueError as exc:
                    reasons.append(str(exc))
                actions[name] = action(c.DRIVER_CHANGE, request_for(result, driver, name), reasons,
                    {'resume': '明确恢复', 'pause': '暂停责任', 'release': '释放责任'}[name])
            for unit in plan['units']:
                proposal = result['proposal'] if unit['role'] != 'coordinate' else None
                reasons = []
                try:
                    v.input_value(result, unit, proposal)
                except ValueError as exc:
                    reasons.append(str(exc))
                actions['send:' + unit['key']] = action(v.SEND, v.send_request(result, unit, proposal), reasons,
                    ('新协调：' if unit['role'] == 'coordinate' else '派发：') + unit['title'])
            coordinates = [f for f in h['frames'] if f['run']['plan_id'] == plan['id'] and f['run']['input']['unit']['role'] == 'coordinate']
            if coordinates:
                frame = coordinates[-1]; run = frame['run']; proposal = result['proposal']
                request = {**v.base_request(result), 'run_id': run['id'], 'result_version': frame['source_evidence']['version'],
                    'proposal': ref(proposal) if proposal else None}
                reasons = []
                try:
                    import time
                    v.check_snapshot(result, run, frame, request, time.time())
                except ValueError as exc:
                    reasons.append(str(exc))
                actions['check'] = action(v.CHECK, request, reasons, '检查新协调提案')
        elif result['exited']:
            frame = h['frames'][-1]; proposal = result['proposal']; cap = result['parent']['edge'].capability
            request = review_check_request(h, result['run'], frame, proposal)
            reasons = []
            try:
                import time
                from .work_coordinator_handoff_text_rework_review_proposals import snapshot
                exit_event = {k: result['proof']['exited'][k] for k in ('seq', 'kind', 'payload_digest')}
                snapshot(cap, h, result['run'], frame, exit_event, request, proposal, time.time())
            except ValueError as exc:
                reasons.append(str(exc))
            actions['check'] = action(v.CHECK, request, reasons, '检查返工提案')
            projected = proposal_projection(result, saved=False)
            actions['apply'] = action(c.APPLY, apply_request(h, plan, proposal) if proposal else None,
                [] if projected['can_apply_rework'] else ['须先取得无阻塞的准确文字返工预览。'], '应用并暂停')
        else:
            problems.append('返工提案回合已保留，等待准确已知退出。')
    except (ValueError, OSError) as exc:
        problems.append(str(exc))
    driver = service.work_drivers.current(db, item['id']); auth = service.work_units.authorization(db, driver)
    rows = service.work_units.rows(db, item['id']); frames = {f['run']['id']: f for f in h['frames']} if h else {}
    proposal = proposal_projection(result, saved=saved) if result else {'record': None, 'reference': None, 'state': 'STALE', 'problems': problems}
    text = {'state': 'SAVED' if saved and result else 'REVIEW_EXITED' if result and result['exited'] else 'REVIEW_RESERVED' if result else 'BLOCKED',
        'actions': actions, 'problems': problems, 'registration': ref(result['registration']) if result and saved else None,
        'preview': (proposal.get('record') or {}).get('snapshot', {}).get('text_rework')}
    return {'supported': True, 'single_step_supported': True, 'continuous_supported': False,
        'scheduling': 'EXPLICIT_TEXT_REWORK_SERIAL', 'automatic_dispatch': False,
        'closed_reason': '按文字返工操作链逐步推进。', 'flow': {**service.work_flows.view(db, item), 'can_start': False},
        'authorization': auth, 'authorization_reference': auth_ref(auth) if auth else None, 'can_authorize': False,
        'frontier': v.frontier(h) if h else None, 'usage': service.work_units.usage(db, rows), 'proposal': proposal,
        'text_rework': text, 'units': [], 'coordination_revision': {'can_revise': False, 'record': None, 'problems': []},
        'rework_diagnostic': None, 'file_rework_diagnostic': None,
        'runs': [{**r, 'result': deepcopy(frames[r['id']]['source_evidence']) if r['id'] in frames else service.work_units.base_evidence(db, r)} for r in rows],
        'handoff_stage': {'protocol': c.CURRENT, 'phase': 'B_COMPLETED' if h else 'BLOCKED',
            'historical_integrity': 'VERIFIED' if result else 'BLOCKED', 'problems': problems,
            'delivery': deepcopy(h['delivery']) if h else [], 'completion': deepcopy(h['completion']) if h else [], **c.PERMISSIONS},
        'no_dispatch': True}


def driver_view(service, db, item, plan):
    value = view(service, db, item, plan); driver = service.work_drivers.current(db, item['id'])
    return {'record': driver, 'reference': driver_ref(driver) if driver else None,
        'held': bool(driver and driver['state'] in {'RESERVED', 'PAUSED'}), 'stale': bool(value['text_rework']['problems']),
        'issues': [{'code': 'TEXT_REWORK', 'message': p} for p in value['text_rework']['problems']],
        'can_acquire': False, 'can_resume': False, 'can_pause': False, 'can_release': False,
        'no_dispatch': True, 'dispatch_supported': True, 'single_step_supported': True, 'continuous_supported': False}
