"""Closed explicit-round data. These records confer no save or input permission."""
from copy import deepcopy
import json
import time

from .store import digest
from .work_transitions import plan_ref
from .work_coordinator_handoff_roots import PLAN_FIELDS, MARKER_FIELD, CONFIG_FIELDS
from .work_coordinator_handoff_records import (
    fields, reference, text, sha, task_revision, observation, PERMISSIONS,
    validate_row as delivery_row, DELIVERY_KIND, COMPLETION_KIND)
from .work_coordinator_handoff_adjustment_records import (
    finite, hashed, permissions, units, source, carry, keys, strings, usage, member,
    driver_ref, auth_ref, original_driver, original_authorization, complete_frontier,
    validate_path as adjustment_path)
from .work_coordinator_handoff_adjustment_contracts import (
    closed, successor_plan as adjustment_plan,
    successor_driver as adjustment_driver, transferred_authorization as adjustment_auth)

PREFIX = 'COORDINATOR_FILE_HANDOFF_ROUND_'
PARENT = PREFIX + 'PARENT_OBSERVATION_V1'
CHECKPOINT = PREFIX + 'CHECKPOINT_V1'
CANDIDATE = PREFIX + 'SAVED_CANDIDATE_V1'
APPLICATION = PREFIX + 'APPLICATION_V1'
REGISTRATION = PREFIX + 'REGISTRATION_V1'
LINEAGE = PREFIX + 'LINEAGE_V1'
SAVED_PATH = PREFIX + 'SAVED_PATH_V1'
SCOPE = 'IMMUTABLE_EXPLICIT_ROUND_DATA; NO_SAVE_OR_EXECUTION_PERMISSION'
PARENT_SCOPE = 'CURRENT_PARENT_OBSERVATION; NO_SAVED_EDGE_OR_INPUT_PERMISSION'
OPERATION = 'EXPLICIT_COORDINATION_REVISION'
PAUSE_REASON = 'COORDINATOR_HANDOFF_ROUND_REVISED_EXPLICIT_RECHECK_REQUIRED'
SAVE_EVENT = 'coordinator_handoff_round_plan_saved'
KINDS = {'work_coordinator_handoff_round_' + name: protocol for name, protocol in (
    ('checkpoint', CHECKPOINT), ('candidate', CANDIDATE),
    ('application', APPLICATION), ('registration', REGISTRATION))}
KIND_FOR = {v: k for k, v in KINDS.items()}
EVENTS = {CHECKPOINT: 'coordinator_handoff_round_checkpointed',
          CANDIDATE: 'coordinator_handoff_round_candidate_prepared',
          APPLICATION: 'coordinator_handoff_round_applied',
          REGISTRATION: 'coordinator_handoff_round_registered'}
COMMON = {'protocol', 'item_id', 'root', 'parent_plan', 'parent_path', 'source',
          'selected_unit', 'reason', 'operation', *PERMISSIONS, 'scope', 'version'}
EXTRA = {
    CHECKPOINT: {'plan', 'observation'},
    CANDIDATE: {'checkpoint', 'child_seed', 'carry_sources', 'historical_coordinators',
                'coordination_revision', 'transition'},
    APPLICATION: {'checkpoint', 'candidate', 'child_plan', 'driver_before', 'driver_after',
                  'authorization_before', 'authorization_after', 'usage', 'save_event', 'pause_reason'},
    REGISTRATION: {'candidate', 'application', 'from_plan', 'to_plan', 'command_id'}}
HISTORY = {'protocol', 'root', 'marker', 'capability', 'adjustment_capability', 'task',
           'driver', 'authorization', 'phase', 'frontier', 'material_observation',
           'delivery', 'completion', 'units', *PERMISSIONS}
PARENT_FIELDS = {'protocol', 'item_id', 'root', 'plan', 'parent_kind', 'saved_path',
    'history', 'source', 'selected_unit', 'prior_check', 'carry_sources',
    'historical_coordinators', 'usage', 'original_dispatch_before', 'events',
    *PERMISSIONS, 'scope', 'version'}


def parent_ref(value):
    return {k: deepcopy(value[k]) for k in ('protocol', 'version', 'root', 'plan')}


def selected(value):
    fields(value, {'definition', 'revision'}); sha(value['revision'])
    unit = deepcopy(value['definition']); strings(unit['depends'])
    if len(unit['depends']) != len(set(unit['depends'])): raise ValueError('协调单元依赖不能重复。')
    unit['depends'] = []; units([unit])
    if value['definition']['role'] != 'coordinate' or value['revision'] != digest(value['definition']):
        raise ValueError('明确新回合须保持原声明coordinate完整定义。')


def prior_check(value):
    if value is None: return
    fields(value, {'reference', 'state', 'problems'}); reference(value['reference'])
    text(value['state']); strings(value['problems'])


def event_ref(value, kind=None):
    fields(value, {'seq', 'kind', 'payload_digest'})
    if type(value['seq']) is not int or value['seq'] < 1:
        raise ValueError('回合历史须为准确实际事件序号。')
    text(value['kind']); sha(value['payload_digest'])
    if kind is not None and value['kind'] != kind:
        raise ValueError('回合历史实际事件类型不符。')


def record_ref(value, protocol):
    reference(value)
    if value['id'] != KIND_FOR[protocol] + ':' + value['version']:
        raise ValueError('独立回合引用须为准确kind与内容身份。')


@closed
def validate_parent(value):
    fields(value, PARENT_FIELDS); hashed(value); permissions(value)
    if value['protocol'] != PARENT or value['scope'] != PARENT_SCOPE:
        raise ValueError('父观察须为独立只读精确协议。')
    for name in ('root', 'plan'): reference(value[name])
    text(value['item_id']); source(value['source'], full=True); selected(value['selected_unit'])
    prior_check(value['prior_check']); carry(value['carry_sources']); keys(value['historical_coordinators'])
    usage(value['usage']); finite(value['original_dispatch_before'])
    h = value['history']; fields(h, HISTORY); permissions(h)
    if h['protocol'] != 'COORDINATOR_FILE_HANDOFF_STAGE_HISTORY_V1':
        raise ValueError('父观察须保留真实原阶段历史。')
    for name in ('root', 'capability'): reference(h[name])
    if h['adjustment_capability'] is not None: reference(h['adjustment_capability'])
    marker = h['marker']
    fields(marker, {'protocol', 'plan_id', 'item_id', 'item_revision', 'project_id',
        'project_root', 'work_requirements_version', 'configuration_version', 'version'}); hashed(marker)
    for name in ('plan_id', 'item_id', 'project_id', 'project_root'): text(marker[name])
    task_revision(marker['item_revision'])
    for name in ('work_requirements_version', 'configuration_version'): sha(marker[name])
    if (marker['protocol'] != 'COORDINATOR_FILE_HANDOFF_ROOT_V1' or
            marker['plan_id'] != value['root']['id'] or marker['item_id'] != value['item_id']):
        raise ValueError('父观察须保留准确原根marker。')
    fields(h['task'], {'id', 'revision'}); text(h['task']['id']); task_revision(h['task']['revision'])
    if value['parent_kind'] == 'ROOT':
        if value['plan'] != value['root'] or value['saved_path'] is not None:
            raise ValueError('原根父观察不能附带保存边。')
        original_driver(h['driver']); original_authorization(h['authorization'])
    elif value['parent_kind'] == 'ADJUSTMENT':
        adjustment_driver(h['driver']); adjustment_auth(h['authorization'])
        path = adjustment_path(value['saved_path'])
        if (path['root'] != value['root'] or path['plan'] != value['plan'] or
                path['capability'] != h['adjustment_capability'] or
                path['history_frontier'] != h['frontier'] or
                path['material_observation'] != h['material_observation']):
            raise ValueError('父观察须完整保留准确调整路径。')
    else:
        raise ValueError('本版父观察尚未开放round保存路径。')
    complete_frontier(h['frontier']); observation(h['material_observation'])
    if h['phase'] not in {'BEFORE_A', 'A_CAPTURED', 'B_COMPLETED'}:
        raise ValueError('父观察当前阶段不能登记新回合。')
    for name, kind in (('delivery', DELIVERY_KIND), ('completion', COMPLETION_KIND)):
        if not isinstance(h[name], list) or len(h[name]) > 1:
            raise ValueError('父观察原正式交付/完成必须唯一。')
        for row in h[name]: delivery_row(kind, row)
    if not isinstance(h['units'], list) or not h['units']: raise ValueError('父观察须保留全部资格。')
    for entry in h['units']:
        fields(entry, {'definition', 'qualification', *PERMISSIONS}); permissions(entry)
        text(entry['qualification'])
    # Whole-plan graph validation preserves dependencies between these definitions.
    units([u['definition'] for u in h['units']])
    unit = value['selected_unit']['definition']; src = value['source']
    if (h['root'] != value['root'] or h['task']['id'] != value['item_id'] or
            h['driver']['item_revision'] != h['task']['revision'] or
            h['driver']['generation'] != h['authorization']['driver_generation'] or
            h['driver']['state'] != 'RESERVED' or
            value['original_dispatch_before'] != h['authorization']['dispatch_before'] or
            src['plan_id'] != value['plan']['id'] or src['plan_version'] != value['plan']['version'] or
            src['unit_revision'] != value['selected_unit']['revision'] or
            unit not in [u['definition'] for u in h['units']] or unit['key'] in value['historical_coordinators'] or
            any(h[n]['plan_id'] != value['plan']['id'] or h[n]['plan_version'] != value['plan']['version'] or
                h[n]['item_id'] != value['item_id'] for n in ('driver', 'authorization'))):
        raise ValueError('父观察原根、当前来源、完整定义或原授权身份不符。')
    events = value['events']; fields(events, {'cutoff', 'runs', 'checks', 'prior_check'})
    if type(events['cutoff']) is not int or events['cutoff'] < 1 or not isinstance(events['runs'], list):
        raise ValueError('父观察须冻结实际全历史事件截止。')
    if [r['run_id'] for r in events['runs']] != [r['run_id'] for r in h['frontier']]:
        raise ValueError('父观察事件须覆盖全部有序旧运行。')
    seen = set()
    for entry in events['runs']:
        fields(entry, {'run_id', 'reservation', 'result', 'captures'}); text(entry['run_id'])
        event_ref(entry['reservation'], 'cooperative_unit_reserved'); event_ref(entry['result'], 'attempt_result_saved')
        if not isinstance(entry['captures'], list): raise ValueError('父观察须保留全部快照事件。')
        for event in entry['captures']: event_ref(event, 'cooperative_files_checked')
        if entry['reservation']['seq'] >= entry['result']['seq']:
            raise ValueError('准确结果必须在实际派发后。')
        for event in [entry['reservation'], entry['result'], *entry['captures']]:
            if event['seq'] > events['cutoff'] or event['seq'] in seen:
                raise ValueError('父观察事件重复或超出实际截止。')
            seen.add(event['seq'])
    if src['run_id'] not in [f['run_id'] for f in h['frontier']]:
        raise ValueError('父观察来源不在完整原前沿。')
    if not isinstance(events['checks'], list): raise ValueError('父观察须保留全部旧提案检查。')
    check_ids = set(); previous = 0
    for check in events['checks']:
        fields(check, {'reference', 'state', 'event'}); reference(check['reference']); text(check['state'])
        event_ref(check['event'], 'coordinator_proposal_checked')
        if (check['reference']['id'] in check_ids or not previous < check['event']['seq'] <= events['cutoff'] or
                check['event']['seq'] in seen):
            raise ValueError('父观察全部旧检查须为准确唯一有序实际历史。')
        check_ids.add(check['reference']['id']); previous = check['event']['seq']; seen.add(previous)
    if (value['prior_check'] is None) != (events['prior_check'] is None):
        raise ValueError('父观察同版本检查须保留准确实际事件。')
    if events['prior_check'] is not None:
        event_ref(events['prior_check'], 'coordinator_proposal_checked')
        if not events['checks'] or events['checks'][-1] != {
                'reference': value['prior_check']['reference'], 'state': value['prior_check']['state'],
                'event': events['prior_check']}:
            raise ValueError('同版本旧检查须为完整实际检查历史的末项。')
    return deepcopy(value)


@closed
def validate(value, protocol):
    if protocol not in EXTRA: raise ValueError('独立回合记录协议未登记。')
    fields(value, COMMON | EXTRA[protocol]); hashed(value); permissions(value)
    if value['protocol'] != protocol or value['scope'] != SCOPE or value['operation'] != OPERATION:
        raise ValueError('明确回合记录协议、操作或范围不符。')
    for name in ('root', 'parent_plan'): reference(value[name])
    text(value['item_id']); text(value['reason']); source(value['source'], full=True); selected(value['selected_unit'])
    path = value['parent_path']; fields(path, {'protocol', 'version', 'root', 'plan'}); sha(path['version'])
    if path['protocol'] != PARENT or path['root'] != value['root'] or path['plan'] != value['parent_plan']:
        raise ValueError('明确回合须引用准确父观察身份。')
    src = value['source']
    if (src['plan_id'] != value['parent_plan']['id'] or src['plan_version'] != value['parent_plan']['version'] or
            src['unit_revision'] != value['selected_unit']['revision']):
        raise ValueError('明确回合必须属于当前父方案的准确源回合。')
    if protocol == CHECKPOINT:
        parent = value['plan']; obs = validate_parent(value['observation'])
        if obs['parent_kind'] == 'ADJUSTMENT': adjustment_plan(parent)
        else:
            fields(parent, PLAN_FIELDS); hashed(parent); finite(parent['created']); task_revision(parent['item_revision'])
            fields(parent['work_requirements'], {'title', 'goal', 'constraints_text', 'acceptance'})
            if any(not isinstance(v, str) for v in parent['work_requirements'].values()):
                raise ValueError('父方案原要求须为完整文本。')
            for name in ('coordinator', 'integrator'): member(parent[name])
            from .work_file_handoff_config import definition
            if (parent['previous_plan'] is not None or parent['state'] != 'PREPARED' or
                    parent['mode'] not in {'leader', 'host_experts'} or type(parent['max_parallel']) is not int or
                    parent['max_parallel'] != 1 or not parent['id'].startswith('teamwork:') or
                    parent['project_id'] != obs['history']['marker']['project_id'] or
                    digest(parent['work_requirements']) != obs['history']['marker']['work_requirements_version'] or
                    digest({k: parent[k] for k in CONFIG_FIELDS}) != obs['history']['marker']['configuration_version'] or
                    not parent['file_handoffs'] or
                    definition(parent['file_handoffs'], parent['units'], parent['max_parallel']) != parent['file_handoffs']):
                raise ValueError('原根父方案须保留准确完整配置。')
            if parent['hard_cash_limit_cny'] is not None: finite(parent['hard_cash_limit_cny'])
        if (plan_ref(parent) != value['parent_plan'] or parent['item_id'] != value['item_id'] or
                parent[MARKER_FIELD] != obs['history']['marker'] or
                parent['units'] != [u['definition'] for u in obs['history']['units']] or
                parent_ref(obs) != path or obs['source'] != src or obs['selected_unit'] != value['selected_unit']):
            raise ValueError('回合检查点须绑定完整准确父方案与父观察。')
    elif protocol == CANDIDATE:
        record_ref(value['checkpoint'], CHECKPOINT); carry(value['carry_sources']); keys(value['historical_coordinators'])
        seed = value['child_seed']; fields(seed, {'id', 'created'}); text(seed['id']); finite(seed['created'])
        if not seed['id'].startswith('teamwork:') or seed['id'] in {value['root']['id'], value['parent_plan']['id']}:
            raise ValueError('新回合须预分配新plan身份。')
        revision = value['coordination_revision']
        fields(revision, {'id', 'item_id', 'unit_key', 'source', 'reason', 'prior_check',
            'answer', 'answer_chars', 'answer_digest'})
        text(revision['id']); prior_check(revision['prior_check']); sha(revision['answer_digest'])
        if (not revision['id'].startswith('coordination-revision:') or revision['source'] != src or
                revision['reason'] != value['reason'] or revision['item_id'] != value['item_id'] or
                revision['unit_key'] != value['selected_unit']['definition']['key'] or
                type(revision['answer_chars']) is not int or revision['answer_chars'] < 1):
            raise ValueError('候选须保留准确源回合与明确登记依据。')
        answer = revision['answer']
        if revision['answer_chars'] <= 32000:
            if not isinstance(answer, str) or len(answer) != revision['answer_chars'] or digest(answer) != revision['answer_digest']:
                raise ValueError('源回复正文须为准确原文字。')
        elif answer is not None: raise ValueError('长回复只保留准确摘要与字数。')
        transition = value['transition']
        fields(transition, {'kind', 'source', 'reason', 'usage_at_transition', 'authorization_from', 'command_id', 'no_dispatch'})
        usage(transition['usage_at_transition']); auth_ref(transition['authorization_from']); text(transition['command_id'])
        if (transition['kind'] != OPERATION or transition['source'] != src or transition['reason'] != value['reason'] or
                transition['no_dispatch'] is not True or revision['unit_key'] in value['historical_coordinators']):
            raise ValueError('明确登记不消费旧提案或隐含派发。')
    elif protocol == APPLICATION:
        record_ref(value['checkpoint'], CHECKPOINT); record_ref(value['candidate'], CANDIDATE)
        reference(value['child_plan'])
        for name in ('driver_before', 'driver_after'): driver_ref(value[name])
        for name in ('authorization_before', 'authorization_after'): auth_ref(value[name])
        usage(value['usage']); event_ref(value['save_event'], SAVE_EVENT)
        if (value['pause_reason'] != PAUSE_REASON or value['child_plan']['id'] in {value['parent_plan']['id'],value['root']['id']} or
                any(value['driver_before'][k] != value['driver_after'][k] for k in ('id', 'generation')) or
                value['driver_before']['revision'] == value['driver_after']['revision'] or
                value['authorization_before']['id'] != value['authorization_after']['id'] or
                value['authorization_before']['revision'] == value['authorization_after']['revision']):
            raise ValueError('回合保存须保持generation、原授权id并明确暂停。')
    else:
        record_ref(value['candidate'], CANDIDATE); record_ref(value['application'], APPLICATION)
        for name in ('from_plan', 'to_plan'): reference(value[name])
        text(value['command_id'])
        if value['from_plan'] != value['parent_plan'] or value['to_plan']['id'] in {value['from_plan']['id'],value['root']['id']}:
            raise ValueError('明确登记须为准确新方案身份。')
    return deepcopy(value)


@closed
def validate_row(kind, row):
    if kind not in KINDS: raise ValueError('独立回合kind未登记。')
    fields(row, {'id', 'version', 'snapshot', 'item_id', 'created', 'command_id'})
    body = validate(row['snapshot'], KINDS[kind]); finite(row['created']); text(row['command_id'])
    if row['id'] != kind + ':' + body['version'] or row['version'] != body['version'] or row['item_id'] != body['item_id']:
        raise ValueError('独立回合外壳身份或归属不符。')
    if KINDS[kind] == REGISTRATION and row['command_id'] != body['command_id']:
        raise ValueError('明确登记必须属于准确外壳command。')
    return deepcopy(row)


class WorkCoordinatorHandoffRoundRecords:
    """Immutable storage, without commands or plan/driver/authorization mutations."""
    def __init__(self, service): self.s = service

    @staticmethod
    def ref(row): return {k: row[k] for k in ('id', 'version')}

    def get(self, db, protocol, ref):
        reference(ref)
        if protocol not in KIND_FOR: raise ValueError('独立回合协议未登记。')
        raw = db.execute('SELECT kind,data FROM collaboration WHERE id=?', (ref['id'],)).fetchone()
        if not raw or raw['kind'] != KIND_FOR[protocol]: raise ValueError('独立回合记录缺失或kind不符。')
        row = validate_row(raw['kind'], json.loads(raw['data']))
        if self.ref(row) != ref: raise ValueError('独立回合引用版本不符。')
        return row

    def put(self, db, protocol, snapshot):
        if protocol not in KIND_FOR: raise ValueError('独立回合协议未登记。')
        kind = KIND_FOR[protocol]; validate(snapshot, protocol)
        row = {'id': kind + ':' + snapshot['version'], 'version': snapshot['version'],
            'snapshot': deepcopy(snapshot), 'item_id': snapshot['item_id'], 'created': time.time(),
            'command_id': self.s.command_identity}
        validate_row(kind, row)
        prior = db.execute('SELECT kind,data FROM collaboration WHERE id=?', (row['id'],)).fetchone()
        if prior:
            if prior['kind'] != kind: raise ValueError('独立回合全局身份已被其它kind占用。')
            old = validate_row(kind, json.loads(prior['data']))
            if old['snapshot'] != snapshot or old['command_id'] != row['command_id']:
                raise ValueError('独立回合不可变记录不得覆盖或跨command复用。')
            return old
        self.s.collaboration.put(db, kind, row)
        self.s.store.event(None, None, EVENTS[protocol], row, db)
        return self.get(db, protocol, self.ref(row))
