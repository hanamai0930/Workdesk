"""One explicit POST_V3 extension of an independently validated V8 parent path.

These are adjacent values, not evidence of durable records or a full namespace.
The saved-tree owner must fetch and canonically rebuild every supplied value.
"""
from copy import deepcopy

from .store import digest
from .work_transitions import plan_ref
from .work_coordinator_handoff_records import fields, reference, PERMISSIONS, versioned, observation
from .work_coordinator_handoff_adjustment_records import hashed, permissions, complete_frontier
from .work_coordinator_handoff_adjustment_contracts import closed
from .work_coordinator_handoff_post_round_successor_contracts import bounded
from .work_coordinator_handoff_post_round_candidates import ref
from .work_coordinator_handoff_round_records import event_ref
from . import work_coordinator_handoff_post_round_v3_saved_records as r

PATH = 'COORDINATOR_FILE_HANDOFF_POST_ROUND_SAVED_PATH_V3'
SCOPE = 'ORDERED_SAVED_V3; PAUSED_TRANSFER; NO_RUNTIME_INPUT_PERMISSION'
EVENT_FIELDS = {'checkpoint_event': r.EVENTS[r.CHECKPOINT], 'candidate_event': r.EVENTS[r.CANDIDATE],
    'driver_event': r.DRIVER_EVENT, 'save_event': r.SAVE_EVENT,
    'application_event': r.EVENTS[r.APPLICATION], 'registration_event': r.EVENTS[r.REGISTRATION]}


@closed
def plan_kind(plan):
    from .work_coordinator_handoff_post_round_candidates import LINEAGE_FIELD
    from .work_coordinator_handoff_post_round_v3_candidates import LINEAGE
    if plan.get(LINEAGE_FIELD, {}).get('protocol') == LINEAGE: return 'POST_V3'
    from .work_coordinator_handoff_post_round_v2_path import plan_kind as parent_kind
    return parent_kind(plan)


@closed
def validate_path(value, checkpoint, candidate):
    fields(value, {'protocol', 'version', 'root', 'plan', 'ancestors', 'history_frontier',
        'driver', 'authorization', 'material_observation', 'scope', *PERMISSIONS})
    hashed(value); permissions(value); bounded(value)
    r.preparation_row(checkpoint, r.CHECKPOINT); r.preparation_row(candidate, r.CANDIDATE)
    reference(value['root']); reference(value['plan'])
    complete_frontier(value['history_frontier']); observation(value['material_observation'])
    cp = checkpoint['snapshot']; b = candidate['snapshot']; h = cp['history']
    from .work_coordinator_handoff_post_round_v2_run_contracts import validate_path as parent_path
    parent = parent_path(h['saved_path'])
    child = b['child_plan']
    r.adjacent(checkpoint, candidate, child, h['driver'], value['driver'], h['authorization'], value['authorization'])
    auth = value['authorization']; entries = value['ancestors']
    if (value['protocol'] != PATH or value['scope'] != SCOPE or value['root'] != b['root'] or
            value['plan'] != plan_ref(child) or parent['plan'] != b['parent_plan'] or
            parent['root'] != b['root'] or parent['driver'] != h['driver'] or parent['authorization'] != h['authorization'] or
            value['history_frontier'] != h['frontier'] or value['history_frontier'] != parent['history_frontier'] or
            value['material_observation'] != h['material_observation'] or
            value['material_observation'] != parent['material_observation'] or
            len(value['history_frontier']) != auth['usage_at_transfer']['attempts'] or
            len(value['history_frontier']) > auth['max_attempts_total'] or
            not isinstance(entries, list) or len(entries) != len(parent['ancestors']) + 1 or
            len(entries) > auth['max_attempts_total'] + 1 or entries[:-1] != parent['ancestors']):
        raise ValueError('V3有序路径须精确延长真实V8父、全部累计前沿和原材料。')
    entry = entries[-1]; fields(entry, {'plan', 'kind', 'edge', 'checkpoint_cutoff'})
    if (entry['kind'] != 'POST_V3' or entry['plan'] != value['plan'] or
            entry['plan']['id'] in {e['plan']['id'] for e in entries[:-1]} or
            type(entry['checkpoint_cutoff']) is not int or entry['checkpoint_cutoff'] != cp['cutoff']):
        raise ValueError('V3末边类型、准确child、循环或检查点截止不符。')
    edge = entry['edge']
    fields(edge, {'parent_plan', 'child_plan', 'checkpoint', 'candidate', 'application', 'registration', *EVENT_FIELDS})
    if (edge['parent_plan'] != parent['plan'] or edge['child_plan'] != value['plan'] or
            edge['checkpoint'] != ref(checkpoint) or edge['candidate'] != ref(candidate)):
        raise ValueError('V3末边未绑定准确检查点、候选及相邻父子。')
    for key, protocol in (('checkpoint', r.CHECKPOINT), ('candidate', r.CANDIDATE),
            ('application', r.APPLICATION), ('registration', r.REGISTRATION)):
        r.record_ref(edge[key], protocol)
    for key, kind in EVENT_FIELDS.items(): event_ref(edge[key], kind)
    seqs = [edge[k]['seq'] for k in EVENT_FIELDS]
    if (len(set(seqs)) != len(seqs) or seqs != sorted(seqs) or
            seqs[0] != cp['cutoff'] + 1 or seqs[1] != seqs[0] + 1 or
            parent['ancestors'][-1]['edge']['registration_event']['seq'] >= cp['cutoff'] or
            cp['source_check_event']['seq'] > cp['cutoff'] or
            edge['checkpoint_event']['payload_digest'] != digest(checkpoint) or
            edge['candidate_event']['payload_digest'] != digest(candidate) or
            edge['driver_event']['payload_digest'] != digest({**value['driver'], 'no_dispatch': True}) or
            edge['save_event']['payload_digest'] != digest(r.save_payload(candidate, child, value['driver'], auth))):
        raise ValueError('V3检查点、原检查、迁移及保存事件须完整准确有序。')
    return deepcopy(value)


@closed
def extend_path(checkpoint, candidate, driver, auth, application, registration, events):
    """Construct a value draft; callers still owe all actual DB/event audits."""
    app = r.validate_row(application, r.APPLICATION); reg = r.validate_row(registration, r.REGISTRATION)
    fields(events, set(EVENT_FIELDS))
    cp = checkpoint['snapshot']; b = candidate['snapshot']; h = cp['history']; child = b['child_plan']
    expected = r.bodies(checkpoint, candidate, child, h['driver'], driver, h['authorization'], auth,
        events['save_event'], cp['request'])
    if (expected != (app['snapshot'], reg['snapshot']) or
            any(v['command_id'] != candidate['command_id'] for v in (app, reg)) or
            not driver['updated'] <= app['created'] <= reg['created'] < auth['dispatch_before'] or
            events['application_event']['payload_digest'] != digest(app) or
            events['registration_event']['payload_digest'] != digest(reg)):
        raise ValueError('V3应用登记、完整命令或原期限未准确重建。')
    edge = {'parent_plan': b['parent_plan'], 'child_plan': plan_ref(child), 'checkpoint': ref(checkpoint),
        'candidate': ref(candidate), 'application': ref(app), 'registration': ref(reg), **deepcopy(events)}
    return validate_path(versioned({'protocol': PATH, 'root': b['root'], 'plan': plan_ref(child),
        'ancestors': deepcopy(h['saved_path']['ancestors']) + [{'plan': plan_ref(child), 'kind': 'POST_V3',
            'edge': edge, 'checkpoint_cutoff': cp['cutoff']}], 'history_frontier': deepcopy(h['frontier']),
        'driver': deepcopy(driver), 'authorization': deepcopy(auth),
        'material_observation': deepcopy(h['material_observation']), 'scope': SCOPE, **PERMISSIONS}), checkpoint, candidate)
