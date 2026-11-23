"""Pure saved-child serial rounds, exact dependencies and new-plan proposals."""
from copy import deepcopy

from .store import canonical, digest
from .work_artifacts import WorkArtifacts
from .work_transitions import source_ref
from .work_coordinator_handoff_records import fields, versioned
from .work_coordinator_handoff_roots import CONFIG_FIELDS
from .work_coordinator_handoff_text_rework_saved_records import ref
from .work_coordinator_handoff_text_rework_saved_values import driver_ref, auth_ref
from .work_coordinator_handoff_text_rework_review_proposals import SOURCE_FIELDS, REQUEST_FIELDS
from . import work_coordinator_handoff_text_rework_contracts as c

SEND = 'work-item.unit-send'
CHECK = 'work-item.proposal-check'
SEND_FIELDS = {'id', 'revision', 'current_driver', 'plan', 'authorization', 'frontier', 'unit', 'retry_of', 'proposal'}
DISPATCH_SCHEMA = {'schema_version': 1, 'action': 'dispatch', 'unit_key': 'declared ordinary key',
    'retry_of': None, 'reason': 'dispatch reason'}
PAUSE_SCHEMA = {'schema_version': 1, 'action': 'pause', 'reason': 'pause reason'}


def frontier(history):
    return digest([{'run_id': f['run']['id'], 'evidence': f['evidence']['version']} for f in history['frames']])


def base_request(result):
    h = result['history']
    return {'id': result['child_plan']['item_id'], 'revision': h['task']['revision'],
        'current_driver': driver_ref(result['driver']), 'plan': ref(result['child_plan']),
        'authorization': auth_ref(result['authorization']), 'frontier': frontier(h)}


def send_request(result, unit, proposal=None):
    return {**base_request(result), 'unit': {'key': unit['key'], 'revision': digest(unit)},
        'retry_of': None, 'proposal': ref(proposal) if proposal else None}


def available(result):
    """The save owner proved carries; only exact child exits can extend them."""
    plan = result['child_plan']; frames = result['history']['frames']; effective = {}
    for key, original in plan['result_lineage'].items():
        found = [f for f in frames if f['run']['unit_key'] == key and source_ref(f['run'], f['source_evidence']) == original]
        if len(found) != 1:
            raise ValueError('返工后继沿用须对应唯一已重建原来源：' + key)
        effective[key] = found[0]
    for f in frames:
        if f['run']['plan_id'] == plan['id'] and f['source_evidence']['state'] != 'RESERVED':
            effective[f['run']['unit_key']] = f
    return effective


def dependencies(result, unit, *, pending_coordinate=None):
    plan = result['child_plan']; effective = available(result); deps = []
    for key in unit['depends']:
        if key == pending_coordinate:
            continue
        if key == plan['file_handoffs'][0]['source_unit'] or key not in effective:
            raise ValueError('返工后继依赖尚无准确可消费来源：' + key)
        evidence = effective[key]['source_evidence']
        WorkArtifacts.require(None, evidence, unit['dependency_gate'])
        if not evidence['exact_identity'] or evidence['blockers']:
            raise ValueError('返工后继依赖身份或退出受阻：' + key)
        deps.append({'unit_key': key, **deepcopy(evidence)})
    return deps


def child_runs(result):
    return [f['run'] for f in result['history']['frames'] if f['run']['plan_id'] == result['child_plan']['id']]


def ordinary_target(result, unit, *, pending_coordinate=None):
    plan = result['child_plan']
    if (unit not in plan['units'] or unit['role'] == 'coordinate' or unit['outputs'] or unit['checks'] or
            unit['write_paths'] or unit['dependency_gate'] != 'result' or
            unit['key'] in plan['result_lineage'] or any(r['unit_key'] == unit['key'] for r in child_runs(result))):
        raise ValueError('返工后继只开放未沿用、尚未运行的原定义普通文字单元。')
    dependencies(result, unit, pending_coordinate=pending_coordinate)
    return {'key': unit['key'], 'revision': digest(unit)}


def require_proposal(result, unit, proposal):
    runs = child_runs(result); current = result['proposal']; h = result['history']
    if (proposal is None or current != proposal or proposal['snapshot']['state'] != 'VALID' or
            proposal['snapshot']['plan'] != ref(result['child_plan']) or
            any(r.get('proposal') == ref(proposal) for r in runs)):
        raise ValueError('每次普通派发须消费本新方案最新未消费真实dispatch提案。')
    b = proposal['snapshot']; decision = b['decision']
    coordinators = [r for r in runs if r['input']['unit']['role'] == 'coordinate']
    if (not coordinators or b['source']['run_id'] != coordinators[-1]['id'] or
            b['driver'] != driver_ref(result['driver']) or b['item_revision'] != h['task']['revision'] or
            b['authorization'] != auth_ref(result['authorization']) or b['frontier'] != frontier(h) or
            decision['action'] != 'dispatch' or decision['retry_of'] is not None or
            decision['unit_key'] != unit['key'] or b['target'] != ordinary_target(result, unit)):
        raise ValueError('返工后继提案来源、当前前沿或准确目标已变化。')
    return proposal


def admit_unit(result, unit, proposal):
    plan = result['child_plan']; h = result['history']; runs = child_runs(result)
    if (result['driver']['state'] != 'RESERVED' or h['usage']['unknown_requests'] or
            h['usage']['attempts'] >= result['authorization']['max_attempts_total'] or
            any(f['source_evidence']['state'] == 'RESERVED' for f in h['frames'])):
        raise ValueError('返工后继须明确恢复，全部已知退出且原累计仍可用。')
    if unit['role'] == 'coordinate':
        if {'key': unit['key'], 'revision': digest(unit)} != plan[c.LINEAGE_FIELD]['next_coordinate'] or proposal is not None:
            raise ValueError('返工后继只重新开放指定的准确协调key。')
        previous = [r for r in runs if r['unit_key'] == unit['key']]
        if previous:
            current = result['proposal']
            if (current is None or current['snapshot']['source']['run_id'] != previous[-1]['id'] or
                    current['snapshot']['state'] != 'VALID' or
                    not any(r.get('proposal') == ref(current) for r in runs)):
                raise ValueError('后续显式协调轮次须先消费上一轮准确dispatch提案并完成普通运行。')
        dependencies(result, unit)
    else:
        ordinary_target(result, unit); require_proposal(result, unit, proposal)


def candidates(result, coordinate):
    values = []
    for unit in result['child_plan']['units']:
        if unit['role'] == 'coordinate':
            continue
        problems = []
        try:
            ordinary_target(result, unit, pending_coordinate=coordinate['key'])
        except ValueError as exc:
            problems.append(str(exc))
        values.append({'unit': {'key': unit['key'], 'revision': digest(unit)},
            'retry_of': None, 'ready_after_proposal': not problems, 'problems': problems})
    return values


def path_value(result):
    h = result['history']
    return versioned({'protocol': c.PATH, 'root': result['capability']['snapshot']['root'],
        'parent_plan': result['capability']['snapshot']['parent_plan'], 'plan': ref(result['child_plan']),
        'parent_saved_path': h.get('saved_path'), 'review_path': h['review_path'],
        'capability': ref(result['capability']), 'registration': ref(result['registration']),
        'task': h['task'], 'driver': result['driver'], 'authorization': result['authorization'],
        'phase': 'B_COMPLETED', 'frontier': h['frontier'], 'material_observation': h['material_observation'],
        'usage': h['usage'], **c.PERMISSIONS})


def input_value(result, unit, proposal=None):
    admit_unit(result, unit, proposal)
    plan = result['child_plan']; h = result['history']; deps = dependencies(result, unit)
    coordinate = unit['role'] == 'coordinate'
    context = versioned({'protocol': c.COORDINATION_INPUT if coordinate else c.UNIT_INPUT,
        'root': result['capability']['snapshot']['root'], 'plan': ref(plan),
        'registration': ref(result['registration']), 'capability': ref(result['capability']),
        'saved_path': path_value(result), 'task': h['task'], 'driver': result['driver'],
        'authorization': result['authorization'], 'phase': 'B_COMPLETED',
        'prior_frontier': h['frontier'], 'material_observation': h['material_observation'],
        'selected_unit': deepcopy(unit), 'configuration': {k: plan[k] for k in CONFIG_FIELDS},
        'work_requirements': plan['work_requirements'], 'units': plan['units'],
        'rework_sources': plan[c.LINEAGE_FIELD]['ordinary_sources'],
        'next_coordinate': plan[c.LINEAGE_FIELD]['next_coordinate'],
        'round': 1 + sum(r['input']['unit']['role'] == 'coordinate' for r in child_runs(result)),
        'allowed_actions': c.POST_ACTIONS if coordinate else [], **c.PERMISSIONS})
    frozen = {'item_id': plan['item_id'], 'item_revision': h['task']['revision'],
        'plan_id': plan['id'], 'plan_version': plan['version'], 'work_requirements': plan['work_requirements'],
        'unit': deepcopy(unit), 'dependencies': deps,
        'check_evidence': [{'unit_key': d['unit_key'], 'artifact': d['file_artifacts']['reference'],
            'check_status': d['check_status']} for d in deps],
        'file_artifacts': [{'unit_key': d['unit_key'], **d['file_artifacts']} for d in deps if d['file_artifacts']['state'] == 'CURRENT'],
        'handoff_coordination_context' if coordinate else 'handoff_unit_context': context}
    if coordinate:
        frozen['coordination_context'] = {'allowed_actions': c.POST_ACTIONS, 'dispatch_schema': DISPATCH_SCHEMA,
            'pause_schema': PAUSE_SCHEMA, 'candidates': candidates(result, unit)}
    if proposal is not None:
        frozen['coordination_proposal'] = {'reference': ref(proposal),
            **{k: deepcopy(proposal['snapshot'][k]) for k in ('source', 'decision')}}
    c.bounded(frozen)
    if len(prompt(frozen)) > 160000:
        raise ValueError('返工后继完整提示超过160000字，未截断。')
    return frozen


def prompt(frozen):
    context = frozen.get('handoff_coordination_context') or frozen.get('handoff_unit_context')
    if not context or context['protocol'] not in {c.COORDINATION_INPUT, c.UNIT_INPUT}:
        raise ValueError('返工后继提示须使用准确新typed输入。')
    prefix = ('读取完整冻结协调输入，只返回dispatch_schema或pause_schema对应的严格JSON；不写文件。\n输入快照 JSON：\n'
        if context['protocol'] == c.COORDINATION_INPUT else
        '完成完整冻结输入中的原文字工作单元，返回文字结果；不写文件。\n输入快照 JSON：\n')
    return prefix + canonical(frozen)


def check_snapshot(result, run, frame, request, observed_at):
    fields(request, REQUEST_FIELDS); c.finite(observed_at)
    h = result['history']; full = frame['source_evidence']; runs = child_runs(result)
    coordinates = [r for r in runs if r['input']['unit']['role'] == 'coordinate']
    expected = {**base_request(result), 'run_id': run['id'], 'result_version': full['version'],
        'proposal': ref(result['proposal']) if result['proposal'] else None}
    already_consumed = bool(result['proposal'] and result['proposal']['snapshot']['source']['run_id'] == run['id'] and
        any(r.get('proposal') == ref(result['proposal']) for r in runs))
    if (request != expected or not coordinates or coordinates[-1] != run or
            run['plan_id'] != result['child_plan']['id'] or not full['result_available'] or
            not full['exact_identity'] or full['blockers'] or full['state'] != 'COMPLETED' or
            result['driver']['state'] != 'RESERVED' or h['usage']['unknown_requests'] or
            observed_at >= result['authorization']['dispatch_before'] or already_consumed):
        raise ValueError('返工新提案检查须绑定本方案最新未消费协调退出及当前完整前沿。')
    decision, target, problems, state = None, None, [], 'INVALID'
    try:
        from .work_proposals import parse
        decision = parse(full['answer'])
        if decision['action'] not in c.POST_ACTIONS:
            raise ValueError('返工新协调只允许dispatch或pause。')
        if decision['action'] == 'dispatch':
            unit = next((u for u in result['child_plan']['units'] if u['key'] == decision['unit_key']), None)
            forecast = next((v for v in run['input']['coordination_context']['candidates'] if v['unit']['key'] == decision['unit_key']), None)
            if unit is None or decision['retry_of'] is not None or not forecast or not forecast['ready_after_proposal']:
                raise ValueError('返工dispatch须选择本次完整输入内可准入的首次普通候选。')
            target = ordinary_target(result, unit); state = 'VALID'
        else:
            state = 'PAUSE_SUGGESTED'
    except ValueError as exc:
        problems.append(str(exc))
    return {'item_revision': h['task']['revision'], 'driver': request['current_driver'], 'plan': request['plan'],
        'authorization': request['authorization'], 'frontier': request['frontier'], 'state': state,
        'decision': decision, 'target': target, 'problems': problems,
        'source': {k: full[k] for k in SOURCE_FIELDS}, 'answer_digest': digest(full['answer']),
        'answer': full['answer'] if len(full['answer']) <= 32000 else None,
        'handoff_context': run['input']['handoff_coordination_context']}


def receipt_body(result, operation, request, run, response, driver=None):
    return versioned({'protocol': c.RECEIPT, 'operation': operation, 'request': deepcopy(request),
        'request_hash': digest(request), 'capability': ref(result['capability']),
        'registration': ref(result['registration']), 'run': {'id': run['id'], 'version': digest(run)},
        'driver': driver_ref(driver or result['driver']), 'receipt': deepcopy(response), **c.PERMISSIONS})
