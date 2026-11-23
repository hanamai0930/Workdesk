"""One ordered saved ancestry; no automatic V7 input upgrade or runtime authority."""
from copy import deepcopy
from .work_coordinator_handoff_records import fields, reference, observation, PERMISSIONS
from .work_coordinator_handoff_adjustment_records import hashed, permissions, complete_frontier
from .work_coordinator_handoff_adjustment_contracts import closed
from .work_coordinator_handoff_post_round_successor_contracts import bounded
from .work_coordinator_handoff_post_round_saved_records import successor_driver, transferred_authorization

PATH = 'COORDINATOR_FILE_HANDOFF_POST_ROUND_SAVED_PATH_V2'
SCOPE = 'ORDERED_SAVED_V2; NO_RUNTIME_INPUT_PERMISSION'


def plan_kind(plan):
    from . import work_coordinator_handoff_post_round_candidates as p
    from . import work_coordinator_handoff_post_round_v2_candidates as v
    from . import work_coordinator_handoff_round_records as f
    from . import work_coordinator_handoff_mixed_records as m
    from . import work_coordinator_handoff_adjustment_records as a
    if plan.get(p.LINEAGE_FIELD):
        protocol = plan[p.LINEAGE_FIELD]['protocol']
        if protocol == v.LINEAGE: return 'POST_V2'
        if protocol == p.LINEAGE: return 'POST_V1'
        raise ValueError('未知POST保存边类型。')
    if plan.get('coordinator_file_handoff_round_lineage'):
        protocol = plan['coordinator_file_handoff_round_lineage']['protocol']
        if protocol == f.LINEAGE: return 'FIRST_ROUND'
        if protocol == m.LINEAGE: return 'MIXED_ROUND'
        raise ValueError('未知ROUND边类型。')
    if plan.get('coordinator_file_handoff_lineage'):
        if plan['coordinator_file_handoff_lineage']['protocol'] != a.LINEAGE: raise ValueError('未知调整边类型。')
        return 'ADJUSTMENT'
    return 'ROOT'


@closed
def validate_path(value):
    from . import work_coordinator_handoff_adjustment_records as a
    from . import work_coordinator_handoff_round_records as f
    from . import work_coordinator_handoff_mixed_records as m
    from . import work_coordinator_handoff_post_round_saved_records as p
    from . import work_coordinator_handoff_post_round_v2_saved_records as v
    from .work_coordinator_handoff_round_records import event_ref
    fields(value, {'protocol', 'version', 'root', 'plan', 'ancestors', 'history_frontier',
        'driver', 'authorization', 'material_observation', 'scope', *PERMISSIONS})
    hashed(value); permissions(value); bounded(value)
    if value['protocol'] != PATH or value['scope'] != SCOPE: raise ValueError('须使用独立有序V2路径。')
    reference(value['root']); reference(value['plan'])
    complete_frontier(value['history_frontier']); observation(value['material_observation'])
    driver = successor_driver(value['driver']); auth = transferred_authorization(value['authorization'])
    entries = value['ancestors']
    if not isinstance(entries, list) or not 4 <= len(entries) <= auth['max_attempts_total'] + 1:
        raise ValueError('V2路径深度超出原授权。')
    seen = set(); last_seq = -1
    modules = {'ADJUSTMENT': a, 'FIRST_ROUND': f, 'MIXED_ROUND': m, 'POST_V1': p, 'POST_V2': v}
    kinds = [entry['kind'] for entry in entries]
    if (kinds[0] != 'ROOT' or kinds[-2:] != ['POST_V1', 'POST_V2'] or
            kinds.count('FIRST_ROUND') != 1 or kinds.count('POST_V1') != 1 or kinds.count('POST_V2') != 1 or
            any(k not in {'ROOT', *modules} for k in kinds)):
        raise ValueError('V2当前仅接受原调整/ROUND/首POST/V7父后的实际再次保存。')
    expected_order = ['ROOT', 'ADJUSTMENT', 'FIRST_ROUND', 'MIXED_ROUND', 'POST_V1', 'POST_V2']
    if [expected_order.index(k) for k in kinds] != sorted(expected_order.index(k) for k in kinds):
        raise ValueError('V2保存边实际类型顺序无效。')
    for i, entry in enumerate(entries):
        fields(entry, {'plan', 'kind', 'edge', 'checkpoint_cutoff'}); reference(entry['plan'])
        if entry['plan']['id'] in seen: raise ValueError('V2路径形成循环。')
        seen.add(entry['plan']['id'])
        if i == 0:
            if entry['plan'] != value['root'] or entry['edge'] is not None or entry['checkpoint_cutoff'] is not None:
                raise ValueError('V2根祖先身份不符。')
            continue
        module = modules[entry['kind']]; edge = entry['edge']; cutoff = entry['checkpoint_cutoff']
        fields(edge, {'parent_plan', 'child_plan', 'checkpoint', 'candidate', 'application',
            'consumption' if module is a else 'registration', 'save_event'} |
            (set() if module is a else {'registration_event'}))
        if (edge['parent_plan'] != entries[i-1]['plan'] or edge['child_plan'] != entry['plan'] or
                type(cutoff) is not int or cutoff <= last_seq): raise ValueError('V2边父子引用或实际截止顺序不符。')
        for key, protocol in (('checkpoint', module.CHECKPOINT), ('candidate', module.CANDIDATE),
                ('application', module.APPLICATION), ('consumption' if module is a else 'registration',
                    module.CONSUMPTION if module is a else module.REGISTRATION)):
            reference(edge[key])
            if edge[key]['id'] != module.KIND_FOR[protocol] + ':' + edge[key]['version']:
                raise ValueError('V2路径各边记录须使用真实类型的准确引用。')
        event_ref(edge['save_event'], 'coordinator_handoff_adjustment_plan_saved' if module is a else module.SAVE_EVENT)
        last_seq = edge['save_event']['seq']
        if not cutoff < last_seq: raise ValueError('V2检查点截止须早于实际保存。')
        if module is not a:
            event_ref(edge['registration_event'], module.EVENTS[module.REGISTRATION])
            if last_seq >= edge['registration_event']['seq']: raise ValueError('V2登记顺序无效。')
            last_seq = edge['registration_event']['seq']
    if (entries[-1]['plan'] != value['plan'] or driver['plan_id'] != value['plan']['id'] or
            auth['plan_id'] != value['plan']['id'] or driver['plan_version'] != value['plan']['version'] or
            auth['plan_version'] != value['plan']['version'] or driver['generation'] != auth['driver_generation'] or
            auth['transfer_registration'] != entries[-1]['edge']['candidate'] or
            driver['transition_registration'] != entries[-1]['edge']['candidate'] or
            driver['previous_plan_reference'] != entries[-2]['plan']):
        raise ValueError('V2路径末尾方案、责任及原授权迁移不符。')
    return deepcopy(value)
