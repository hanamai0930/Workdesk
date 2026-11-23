"""Closed parent observation for arbitrary authorized mixed ROUND depth."""
from copy import deepcopy
from .store import digest
from .work_coordinator_handoff_records import fields, reference, text, sha, task_revision, observation, PERMISSIONS, DELIVERY_KIND, COMPLETION_KIND, validate_row as delivery_row
from .work_coordinator_handoff_adjustment_records import hashed, permissions, source, carry, keys, usage, finite, complete_frontier, units
from .work_coordinator_handoff_adjustment_contracts import closed
from .work_coordinator_handoff_round_records import PARENT_FIELDS, HISTORY, selected, prior_check, event_ref
from .work_coordinator_handoff_mixed_records import PARENT
SCOPE = 'COMPLETE_AUTHORIZED_MIXED_ROUND_PARENT; NO_IMPLICIT_SAVE_OR_DISPATCH'
HISTORY_FIELDS = HISTORY | {'round_capability'}

@closed
def validate_parent(value):
    from .work_coordinator_handoff_mixed_runtime import validate_path
    fields(value, PARENT_FIELDS); hashed(value); permissions(value)
    if value['protocol'] != PARENT or value['scope'] != SCOPE or value['parent_kind'] != 'ROUND':
        raise ValueError('V2父观察仅为真实已运行首ROUND的独立历史协议。')
    for k in ('root', 'plan'): reference(value[k])
    text(value['item_id']); source(value['source'], full=True); selected(value['selected_unit'])
    prior_check(value['prior_check']); carry(value['carry_sources']); keys(value['historical_coordinators'])
    usage(value['usage']); finite(value['original_dispatch_before'])
    path = validate_path(value['saved_path']); h = value['history']; fields(h, HISTORY_FIELDS); permissions(h)
    if h['protocol'] != 'COORDINATOR_FILE_HANDOFF_STAGE_HISTORY_V1': raise ValueError('父历史阶段协议不符。')
    if (path['root'] != value['root'] or path['plan'] != value['plan'] or
            h['root'] != path['root'] or h['capability'] != path['runtime_capability'] or
            h['adjustment_capability'] != path['adjustment_capability'] or
            h['round_capability'] is None or h['round_capability'] != path['round_capability'] or
            h['driver'] != path['driver'] or h['authorization'] != path['authorization'] or
            h['frontier'] != path['history_frontier'] or h['material_observation'] != path['material_observation'] or
            h['task'] != {'id': value['item_id'], 'revision': h['driver']['item_revision']} or
            h['driver']['state'] != 'RESERVED' or h['phase'] not in {'BEFORE_A', 'A_CAPTURED', 'B_COMPLETED'} or
            h['authorization']['dispatch_before'] != value['original_dispatch_before']):
        raise ValueError('ROUND父观察须逐值绑定原路径、全部前沿、责任授权和材料。')
    complete_frontier(h['frontier']); observation(h['material_observation'])
    marker = h['marker']; fields(marker, {'protocol','plan_id','item_id','item_revision','project_id',
        'project_root','work_requirements_version','configuration_version','version'}); hashed(marker)
    task_revision(marker['item_revision'])
    for k in ('plan_id', 'item_id', 'project_id', 'project_root'): text(marker[k])
    for k in ('work_requirements_version', 'configuration_version'): sha(marker[k])
    if (marker['plan_id'] != path['root']['id'] or marker['item_id'] != value['item_id'] or
            marker['protocol'] != 'COORDINATOR_FILE_HANDOFF_ROOT_V1' or
            h['material_observation']['root'] != marker['project_root']):
        raise ValueError('ROUND父观察原根marker不符。')
    for name, kind in (('delivery', DELIVERY_KIND), ('completion', COMPLETION_KIND)):
        if not isinstance(h[name], list) or len(h[name]) > 1: raise ValueError('正式交付完成必须唯一。')
        for row in h[name]: delivery_row(kind, row)
    if not isinstance(h['units'], list) or len(h['units']) != len(path['current_qualifications']):
        raise ValueError('ROUND父须保留全部原定义和当前资格。')
    for u, q in zip(h['units'], path['current_qualifications']):
        fields(u, {'definition', 'qualification', *PERMISSIONS}); permissions(u)
        if u['definition']['key'] != q['key'] or u['qualification'] != q['qualification']:
            raise ValueError('ROUND父资格必须属于准确当前身份。')
    units([u['definition'] for u in h['units']])
    src = value['source']; unit = value['selected_unit']['definition']
    if (src['plan_id'] != value['plan']['id'] or src['plan_version'] != value['plan']['version'] or
            src['unit_revision'] != digest(unit) or unit not in [u['definition'] for u in h['units']] or
            unit['key'] in value['historical_coordinators'] or
            not any(f['run_id'] == src['run_id'] and f['input_version'] == src['input_version'] for f in h['frontier'])):
        raise ValueError('ROUND父来源须为完整前沿内准确当前plan的新回合。')
    ev = value['events']; fields(ev, {'cutoff', 'runs', 'checks', 'prior_check'})
    if type(ev['cutoff']) is not int or ev['cutoff'] < 1 or not isinstance(ev['runs'], list):
        raise ValueError('ROUND父须绑定真实全局事件截止。')
    if [r['run_id'] for r in ev['runs']] != [r['run_id'] for r in h['frontier']]:
        raise ValueError('ROUND父事件须覆盖全部有序历史运行。')
    seen = set()
    for r in ev['runs']:
        fields(r, {'run_id','reservation','result','captures'}); text(r['run_id'])
        event_ref(r['reservation'], 'cooperative_unit_reserved'); event_ref(r['result'], 'attempt_result_saved')
        if not isinstance(r['captures'], list) or r['reservation']['seq'] >= r['result']['seq']:
            raise ValueError('ROUND父运行必须实际退出。')
        for e in r['captures']:
            event_ref(e, 'cooperative_files_checked')
            if e['seq'] <= r['result']['seq']: raise ValueError('ROUND父快照必须晚于实际退出。')
        for e in [r['reservation'], r['result'], *r['captures']]:
            if e['seq'] > ev['cutoff'] or e['seq'] in seen: raise ValueError('ROUND父事件重复或超出截止。')
            seen.add(e['seq'])
    if not isinstance(ev['checks'], list): raise ValueError('ROUND父须保留完整旧检查历史。')
    previous = 0; ids = set()
    for c in ev['checks']:
        fields(c, {'reference','state','event'}); reference(c['reference']); text(c['state'])
        event_ref(c['event'], 'coordinator_proposal_checked'); seq = c['event']['seq']
        if not previous < seq <= ev['cutoff'] or seq in seen or c['reference']['id'] in ids:
            raise ValueError('ROUND父旧检查须唯一有序。')
        previous = seq; seen.add(seq); ids.add(c['reference']['id'])
    if (value['prior_check'] is None) != (ev['prior_check'] is None): raise ValueError('ROUND父同版本检查事件不符。')
    if ev['prior_check'] is not None:
        event_ref(ev['prior_check'], 'coordinator_proposal_checked')
        if not ev['checks'] or ev['checks'][-1] != {'reference': value['prior_check']['reference'],
                'state': value['prior_check']['state'], 'event': ev['prior_check']}:
            raise ValueError('ROUND父同版本检查必须为完整历史末项。')
    return deepcopy(value)
