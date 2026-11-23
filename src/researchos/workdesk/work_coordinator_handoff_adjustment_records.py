"""Closed adjustment data records. Shape validity grants no save/input permit.

The candidate reader separately rebuilds every referenced source from the DB,
events and actual bytes. Hash consistency is never source authority.
"""
from copy import deepcopy
import json
import math
import time

from .store import digest
from .work_coordinator_handoff_records import (
    PERMISSIONS, fields, reference, sha, text, observation, frontier, manifest,
    task_revision, validate_row as validate_delivery_row, DELIVERY_KIND, COMPLETION_KIND)
from .work_coordinator_handoff_roots import PLAN_FIELDS, UNIT_FIELDS
from .work_coordinator_handoff_adjustments import PREVIEW


PREFIX = 'COORDINATOR_FILE_HANDOFF_'
CHECKPOINT = PREFIX + 'ADJUSTMENT_CHECKPOINT_V1'
CANDIDATE = PREFIX + 'ADJUSTMENT_SAVED_CANDIDATE_V1'
APPLICATION = PREFIX + 'ADJUSTMENT_APPLICATION_V1'
CONSUMPTION = PREFIX + 'ADJUSTMENT_CONSUMPTION_V1'
SAVED_PATH = PREFIX + 'ADJUSTMENT_SAVED_PATH_V1'
LINEAGE = PREFIX + 'ADJUSTMENT_LINEAGE_V1'
SCOPE = 'IMMUTABLE_ADJUSTMENT_DATA; NOT_A_SAVE_OR_EXECUTION_PERMIT'
PATH_SCOPE = 'DERIVED_SAVED_PATH; NO_INPUT_OR_EXECUTION_PERMISSION'
PAUSE_REASON = 'COORDINATOR_HANDOFF_DEPENDENCIES_ADJUSTED_EXPLICIT_RECHECK_REQUIRED'
KINDS = {'work_coordinator_handoff_adjustment_' + name: protocol for name, protocol in (
    ('preview', PREVIEW), ('checkpoint', CHECKPOINT), ('candidate', CANDIDATE),
    ('application', APPLICATION), ('consumption', CONSUMPTION))}
KIND_FOR = {v: k for k, v in KINDS.items()}
EVENTS = {PREVIEW: 'coordinator_handoff_adjustment_preview_frozen',
          CHECKPOINT: 'coordinator_handoff_adjustment_checkpointed',
          CANDIDATE: 'coordinator_handoff_adjustment_candidate_prepared',
          APPLICATION: 'coordinator_handoff_adjustment_applied',
          CONSUMPTION: 'coordinator_handoff_adjustment_consumed'}
COMMON = {'protocol', 'item_id', 'root', 'parent_plan', 'capability', 'operation',
          *PERMISSIONS, 'scope', 'version'}
PREVIEW_FIELDS = {'protocol', 'root', 'parent_plan', 'capability', 'operation', *PERMISSIONS,
    'scope', 'version', 'item_revision', 'driver', 'authorization', 'source', 'frozen_context',
    'phase', 'prior_frontier', 'frontier', 'candidate_units', 'direct_changes', 'affected_keys',
    'protected_keys', 'qualifications', 'carry_sources', 'historical_coordinators',
    'resume_coordinate_keys', 'resume_blockers', 'material_observation', 'usage'}
EXTRA = {
    CHECKPOINT: {'preview', 'proposal', 'plan', 'history', 'usage',
                 'original_dispatch_before', 'authorization_from'},
    CANDIDATE: {'preview', 'checkpoint', 'proposal', 'child_seed', 'candidate_units',
                'carry_sources', 'historical_coordinators', 'transition', 'expected_pause_reason'},
    APPLICATION: {'candidate', 'checkpoint', 'child_plan', 'proposal', 'source', 'driver_before',
                  'driver_after', 'authorization_before', 'authorization_after', 'usage',
                  'pause_reason', 'save_event'},
    CONSUMPTION: {'proposal', 'application', 'from_plan', 'to_plan', 'source', 'command_id'}}
SOURCE_FIELDS = {'run_id', 'task_id', 'attempt_id', 'owner', 'session_id', 'turn_id',
                 'connection_fingerprint', 'result_digest', 'version'}
SOURCE_REF_FIELDS = SOURCE_FIELDS | {'plan_id', 'plan_version', 'unit_revision', 'input_version',
                                   'result_version', 'artifact', 'manifest', 'check_status'}
USAGE_NUMBERS = {'model_requests', 'reserved_cny', 'tool_calls', 'elapsed_observed_seconds',
                 'api_known_input_tokens', 'api_known_output_tokens',
                 'api_requests_without_token_receipt', 'unknown_requests', 'attempts'}
USAGE_FIXED = {'account_charge': 'UNKNOWN', 'subscription_remaining': 'UNKNOWN',
    'native_model_requests': 'UNKNOWN', 'request_count_scope': 'LOCAL_REQUEST_LEDGER_ONLY_NOT_NATIVE_TOTAL',
    'scope': 'ALL_COOPERATIVE_ATTEMPTS_FOR_BUSINESS_ITEM'}
HISTORY_FIELDS = {'protocol', 'root', 'marker', 'capability', 'adjustment_capability', 'task',
    'driver', 'authorization', 'phase', 'frontier', 'material_observation', 'delivery',
    'completion', 'units', *PERMISSIONS}


def finite(value):
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        raise ValueError('调整记录的数值或时间无效。')


def hashed(value):
    sha(value['version'])
    if value['version'] != digest({k: v for k, v in value.items() if k != 'version'}):
        raise ValueError('调整记录完整内容摘要不一致。')


def permissions(value):
    if any(value[k] is not v for k, v in PERMISSIONS.items()):
        raise ValueError('调整数据记录不能授予保存、输入或执行许可。')


def keys(value):
    if (not isinstance(value, list) or len(value) > 20 or
            any(not isinstance(k, str) or not k for k in value) or value != sorted(set(value))):
        raise ValueError('调整keys须为排序唯一的原声明身份。')


def strings(value):
    if not isinstance(value, list) or any(not isinstance(v, str) for v in value):
        raise ValueError('调整理由须为字符串列表。')


def driver_ref(value):
    fields(value, {'id', 'generation', 'revision'})
    for v in value.values(): text(v)


def auth_ref(value):
    fields(value, {'id', 'revision'})
    for v in value.values(): text(v)


def source(value, full=False):
    fields(value, SOURCE_REF_FIELDS if full else SOURCE_FIELDS)
    for key in ('run_id', 'task_id', 'attempt_id', 'owner'):
        text(value[key])
    for key in ('session_id', 'turn_id'):
        if value[key] is not None: text(value[key])
    for key in ('connection_fingerprint', 'result_digest', 'version'):
        sha(value[key])
    if full:
        text(value['plan_id'])
        for key in ('plan_version', 'unit_revision', 'input_version', 'result_version'): sha(value[key])
        if value['artifact'] is not None: reference(value['artifact'])
        manifest(value['manifest']); text(value['check_status'])


def usage(value):
    fields(value, USAGE_NUMBERS | USAGE_FIXED.keys())
    for key in USAGE_NUMBERS: finite(value[key])
    if type(value['attempts']) is not int or any(value[k] != v for k, v in USAGE_FIXED.items()):
        raise ValueError('调整累计须为原完整WorkUnits.usage，不补推订阅消耗。')


def units(value):
    if not isinstance(value, list) or not 1 <= len(value) <= 20:
        raise ValueError('调整须保留完整原声明单元。')
    seen = set()
    for unit in value:
        fields(unit, UNIT_FIELDS); text(unit['key'])
        if unit['key'] in seen: raise ValueError('调整单元身份重复。')
        seen.add(unit['key'])
        strings(unit['depends'])
        if len(set(unit['depends'])) != len(unit['depends']): raise ValueError('调整依赖重复。')
        member(unit['member'])
        for key in ('title', 'goal', 'acceptance'):
            limit = 160 if key == 'title' else 4000
            if not isinstance(unit[key], str) or not unit[key].strip() or len(unit[key]) > limit:
                raise ValueError('调整须保留原单元文本字段范围。')
        for key in ('role', 'dependency_gate'): text(unit[key])
        for key in ('write_paths', 'outputs'): strings(unit[key])
        checks(unit['checks'])
    from .plans import PlanCommands
    PlanCommands.validate_graph({u['key']: u['depends'] for u in value})


def member(value):
    fields(value, {'reference', 'name', 'role', 'provider', 'model', 'effort', 'tier'})
    fields(value['reference'], {'member_id', 'member_revision', 'connection_id',
        'connection_revision', 'quota_id', 'quota_revision'})
    for key, v in value['reference'].items():
        if key == 'quota_revision' and type(v) is int: finite(v)
        elif key == 'member_revision': task_revision(v)
        elif key == 'connection_revision': sha(v)
        else: text(v)


def checks(value):
    if not isinstance(value, list): raise ValueError('原检查须为完整声明列表。')
    for check in value:
        fields(check, {'file', 'kind', 'text'} if check.get('kind') == 'utf8_contains' else {'file', 'kind'})
        text(check['file'])
        if check['kind'] not in {'nonempty', 'utf8_contains', 'json_parse'}: raise ValueError('检查种类未登记。')
        if 'text' in check and (not isinstance(check['text'], str) or not 1 <= len(check['text']) <= 4000):
            raise ValueError('原文本检查须保留1至4000字范围。')


def evidence(value, full=False):
    names = SOURCE_FIELDS | {'state', 'exact_identity', 'answer', 'result_available',
        'check_status', 'independent_review', 'human_acceptance', 'blockers'}
    fields(value, names | {'result_version', 'file_artifacts'} if full else names)
    source({k: value[k] for k in SOURCE_FIELDS})
    if full:
        sha(value['result_version'])
        files = value['file_artifacts']
        fields(files, {'state', 'reference', 'problems', 'manifest', 'check_status', 'checks',
            'required_outputs', 'required_checks', 'independent_review', 'human_acceptance', 'provenance', 'delivery'})
        if files['reference'] is not None: reference(files['reference'])
        manifest(files['manifest']); strings(files['problems']); strings(files['required_outputs']); checks(files['required_checks'])
        observations(files['checks'])
    else: hashed(value)


def observations(value):
    if not isinstance(value, list): raise ValueError('原检查观察须为完整列表。')
    for entry in value:
        fields(entry, {'definition', 'passed'}); checks([entry['definition']])
        if type(entry['passed']) is not bool: raise ValueError('检查结果须为准确布尔值。')


def complete_frontier(value):
    frontier(value)
    for entry in value:
        evidence(entry['evidence'])
        files = entry['files']
        fields(files, {'run_id', 'input_version', 'actual_input_manifest', 'frozen_dependency_manifest',
            'input_state', 'input_problems', 'snapshots', 'output_state', 'scope'})
        manifest(files['actual_input_manifest']); manifest(files['frozen_dependency_manifest']); strings(files['input_problems'])
        if not isinstance(files['snapshots'], list): raise ValueError('须保留全部原快照列表。')
        for saved in files['snapshots']:
            fields(saved, {'reference', 'snapshot', 'state', 'problems'}); reference(saved['reference']); strings(saved['problems'])
            snapshot = saved['snapshot']
            fields(snapshot, {'run_id', 'result_version', 'identity', 'plan_id', 'plan_version', 'unit_revision',
                'manifest', 'observations', 'check_status'})
            fields(snapshot['identity'], {'task_id', 'attempt_id', 'owner', 'session_id', 'turn_id', 'connection_fingerprint'})
            manifest(snapshot['manifest']); observations(snapshot['observations'])
            if digest(snapshot) != saved['reference']['version']: raise ValueError('全部原快照内容摘要不一致。')


def original_driver(value):
    base = {'id', 'item_id', 'item_revision', 'generation', 'kind', 'plan_id', 'plan_version', 'state',
        'reason', 'instance', 'previous_generation', 'task_ids', 'created', 'scope', 'revision', 'updated'}
    optional = {'last_unit_run', 'revision_lineage', 'released'}
    if not isinstance(value, dict) or not base <= set(value) or set(value) - base - optional:
        raise ValueError('写前责任完整字段不符。')
    driver_ref({k: value[k] for k in ('id', 'generation', 'revision')})
    if 'revision_lineage' in value:
        if not isinstance(value['revision_lineage'], list): raise ValueError('责任绑定历史须为完整列表。')
        for link in value['revision_lineage']:
            fields(link, {'from', 'to', 'unit_run_id'}); task_revision(link['from']); task_revision(link['to']); text(link['unit_run_id'])


def original_authorization(value):
    fields(value, {'id', 'revision', 'item_id', 'driver_generation', 'plan_id', 'plan_version',
        'max_attempts_total', 'dispatch_before', 'created', 'already_used', 'scope',
        'deadline_enforcement', 'cash_enforcement', 'native_permissions', 'command_id'})
    auth_ref({k: value[k] for k in ('id', 'revision')}); finite(value['dispatch_before'])


def carry(value):
    if not isinstance(value, dict) or len(value) > 20: raise ValueError('调整沿用映射无效。')
    for key, entry in value.items(): text(key); source(entry, full=True)


def qualifications(value):
    if not isinstance(value, list) or len(value) > 20: raise ValueError('调整资格列表无效。')
    seen = set()
    for entry in value:
        fields(entry, {'key', 'qualification', 'source'}); text(entry['key']); text(entry['qualification'])
        if entry['key'] in seen: raise ValueError('调整资格重复。')
        seen.add(entry['key'])
        if entry['source'] is not None:
            # Exact base_evidence identity; the reader checks every value.
            evidence(entry['source'])


def validate_preview(value):
    fields(value, PREVIEW_FIELDS); hashed(value); permissions(value)
    if value['protocol'] != PREVIEW or value['operation'] != 'ADJUSTMENT' or value['scope'] != 'PREVIEW_ONLY; SAVED_EDGE_NOT_OPEN':
        raise ValueError('调整预览须为原V3准确无许可数据。')
    for name in ('root', 'parent_plan', 'capability'): reference(value[name])
    driver_ref(value['driver']); auth_ref(value['authorization']); task_revision(value['item_revision'])
    source(value['source']); fields(value['frozen_context'], {'protocol', 'version', 'root'})
    reference(value['frozen_context']['root']); sha(value['frozen_context']['version'])
    if value['frozen_context']['protocol'] != PREFIX + 'COORDINATION_INPUT_V3':
        raise ValueError('调整预览不能升级旧协议来源。')
    for name in ('direct_changes', 'affected_keys', 'protected_keys', 'historical_coordinators', 'resume_coordinate_keys'): keys(value[name])
    if not value['direct_changes'] or not value['resume_coordinate_keys']: raise ValueError('调整缺少真实变化或接续路径。')
    if value['phase'] not in {'BEFORE_A', 'A_CAPTURED', 'B_COMPLETED'}: raise ValueError('调整阶段不可用。')
    complete_frontier(value['prior_frontier']); sha(value['frontier']); units(value['candidate_units'])
    carry(value['carry_sources']); qualifications(value['qualifications']); usage(value['usage'])
    observation(value['material_observation'])
    if not isinstance(value['resume_blockers'], dict): raise ValueError('接续阻塞须逐key记录。')
    for key, reason in value['resume_blockers'].items(): text(key); text(reason)
    return deepcopy(value)


def save_event(value):
    fields(value, {'seq', 'kind', 'payload_digest'})
    if type(value['seq']) is not int or value['seq'] < 1 or value['kind'] != 'coordinator_handoff_adjustment_plan_saved':
        raise ValueError('保存边须引用准确正式保存事件。')
    sha(value['payload_digest'])


def history(value, *, successor=False):
    fields(value, HISTORY_FIELDS); permissions(value)
    for key in ('root', 'capability', 'adjustment_capability'): reference(value[key])
    fields(value['task'], {'id', 'revision'}); text(value['task']['id']); task_revision(value['task']['revision'])
    if (value['protocol'] != PREFIX + 'STAGE_HISTORY_V1' or
            not isinstance(value['driver'], dict) or not isinstance(value['authorization'], dict)):
        raise ValueError('写前须保存完整原阶段读取身份。')
    if successor:
        from .work_coordinator_handoff_adjustment_contracts import successor_driver, transferred_authorization
        successor_driver(value['driver']); transferred_authorization(value['authorization'])
    else:
        original_driver(value['driver']); original_authorization(value['authorization'])
    complete_frontier(value['frontier']); observation(value['material_observation'])
    if value['phase'] not in {'BEFORE_A', 'A_CAPTURED', 'B_COMPLETED'}: raise ValueError('写前阶段不可保存。')
    for name, kind in (('delivery', DELIVERY_KIND), ('completion', COMPLETION_KIND)):
        if not isinstance(value[name], list) or len(value[name]) > 1: raise ValueError('原交付与完成须唯一。')
        for row in value[name]: validate_delivery_row(kind, row)
    if not isinstance(value['units'], list): raise ValueError('写前须保存全部资格。')
    for entry in value['units']:
        fields(entry, {'definition', 'qualification', *PERMISSIONS}); permissions(entry)
        fields(entry['definition'], UNIT_FIELDS); text(entry['qualification'])


def transition(value):
    fields(value, {'proposal', 'source', 'impact', 'usage_at_transition', 'authorization_from', 'command_id', 'no_dispatch'})
    reference(value['proposal']); source(value['source']); auth_ref(value['authorization_from'])
    usage(value['usage_at_transition']); text(value['command_id'])
    fields(value['impact'], {'direct_changes', 'affected_keys', 'protected_keys'})
    for v in value['impact'].values(): keys(v)
    if value['no_dispatch'] is not True: raise ValueError('候选不得隐含派发。')


def _validate(value, protocol):
    if protocol == PREVIEW: return validate_preview(value)
    if protocol not in EXTRA: raise ValueError('独立调整记录协议未登记。')
    fields(value, COMMON | EXTRA[protocol]); hashed(value); permissions(value)
    for name in ('root', 'parent_plan', 'capability'): reference(value[name])
    text(value['item_id'])
    if value['protocol'] != protocol or value['operation'] != 'ADJUSTMENT' or value['scope'] != SCOPE:
        raise ValueError('独立调整记录协议、范围或操作不一致。')
    reference(value['proposal'])
    if protocol == CHECKPOINT:
        validate_preview(value['preview'])
        successor = value['parent_plan'] != value['root']
        if successor:
            from .work_coordinator_handoff_adjustment_contracts import successor_plan
            successor_plan(value['plan'])
            if value['plan']['coordinator_file_handoff_lineage']['root'] != value['root']:
                raise ValueError('后继检查点须保留同一准确原根。')
        else:
            fields(value['plan'], PLAN_FIELDS); hashed(value['plan'])
        history(value['history'], successor=successor); usage(value['usage']); finite(value['original_dispatch_before']); auth_ref(value['authorization_from'])
        if (value['parent_plan'] != {k: value['plan'][k] for k in ('id', 'version')} or
                value['history']['root'] != value['root'] or value['plan']['item_id'] != value['item_id'] or
                value['preview']['parent_plan'] != value['parent_plan'] or
                value['preview']['capability'] != value['capability'] or
                value['history']['adjustment_capability'] != value['capability'] or
                value['preview']['root'] != value['root'] or
                value['history']['marker'] != value['plan']['coordinator_file_handoff_root'] or
                value['preview']['item_revision'] != value['history']['task']['revision'] or
                value['history']['driver']['item_revision'] != value['history']['task']['revision'] or
                value['history']['task']['id'] != value['item_id'] or
                value['history']['phase'] != value['preview']['phase'] or
                value['history']['material_observation'] != value['preview']['material_observation'] or
                [u['definition'] for u in value['history']['units']] != value['plan']['units'] or
                value['history']['authorization']['driver_generation'] != value['history']['driver']['generation'] or
                any(value['history'][name]['plan_id'] != value['plan']['id'] or
                    value['history'][name]['plan_version'] != value['plan']['version'] for name in ('driver','authorization')) or
                value['preview']['driver'] != {k:value['history']['driver'][k] for k in ('id','generation','revision')} or
                value['preview']['authorization'] != value['authorization_from'] or
                value['usage'] != value['preview']['usage'] or
                value['original_dispatch_before'] != value['history']['authorization']['dispatch_before'] or
                value['authorization_from'] != {k: value['history']['authorization'][k] for k in ('id', 'revision')}):
            raise ValueError('检查点须绑定完整原方案、原累计授权与实际预览。')
    elif protocol == CANDIDATE:
        reference(value['preview']); reference(value['checkpoint']); fields(value['child_seed'], {'id', 'created'})
        text(value['child_seed']['id']); finite(value['child_seed']['created'])
        if not value['child_seed']['id'].startswith('teamwork:') or value['child_seed']['id'] == value['parent_plan']['id']:
            raise ValueError('后继seed须预分配全新身份且不含未来version。')
        units(value['candidate_units']); carry(value['carry_sources']); keys(value['historical_coordinators']); transition(value['transition'])
        if value['expected_pause_reason'] != PAUSE_REASON or value['proposal'] != value['transition']['proposal']:
            raise ValueError('候选的提案或准确暂停原因不一致。')
    elif protocol == APPLICATION:
        for name in ('candidate', 'checkpoint', 'child_plan'): reference(value[name])
        source(value['source']); save_event(value['save_event']); usage(value['usage'])
        for name in ('driver_before', 'driver_after'): driver_ref(value[name])
        for name in ('authorization_before', 'authorization_after'): auth_ref(value[name])
        if (value['pause_reason'] != PAUSE_REASON or value['child_plan'] == value['parent_plan'] or
                any(value['driver_before'][k] != value['driver_after'][k] for k in ('id', 'generation')) or
                value['driver_before']['revision'] == value['driver_after']['revision'] or
                value['authorization_before']['id'] != value['authorization_after']['id'] or
                value['authorization_before']['revision'] == value['authorization_after']['revision']):
            raise ValueError('应用须沿用原责任generation、同id原授权新revision及准确暂停原因。')
    else:
        for name in ('application', 'from_plan', 'to_plan'): reference(value[name])
        source(value['source']); text(value['command_id'])
        if value['from_plan'] != value['parent_plan'] or value['from_plan'] == value['to_plan']:
            raise ValueError('消费须为准确跨方案保存。')
    return deepcopy(value)


def validate(value, protocol):
    try: return _validate(value, protocol)
    except (KeyError, TypeError, AttributeError, RecursionError, OverflowError) as exc:
        raise ValueError('独立调整记录嵌套字段无效。') from exc


def validate_path(value):
    try:
        fields(value, {'protocol', 'root', 'plan', 'capability', 'edges', 'history_frontier',
            'current_qualifications', 'material_observation', 'problems', *PERMISSIONS, 'scope', 'version'})
        hashed(value); permissions(value)
        for name in ('root', 'plan', 'capability'): reference(value[name])
        if value['protocol'] != SAVED_PATH or value['scope'] != PATH_SCOPE or not isinstance(value['edges'], list) or not 1 <= len(value['edges']) <= 1000:
            raise ValueError('非空保存路径须为独立有序派生数据。')
        previous = value['root']; seen = {previous['id']}
        for edge in value['edges']:
            fields(edge, {'parent_plan', 'child_plan', 'checkpoint', 'candidate', 'application', 'consumption', 'save_event'})
            for name in ('parent_plan', 'child_plan', 'checkpoint', 'candidate', 'application', 'consumption'): reference(edge[name])
            save_event(edge['save_event'])
            if edge['parent_plan'] != previous or edge['child_plan']['id'] in seen:
                raise ValueError('保存路径须从原根连续到当前且不得重复方案。')
            seen.add(edge['child_plan']['id']); previous = edge['child_plan']
        if previous != value['plan']: raise ValueError('保存路径没有到达准确当前方案。')
        complete_frontier(value['history_frontier']); qualifications(value['current_qualifications'])
        observation(value['material_observation']); strings(value['problems'])
        return deepcopy(value)
    except (KeyError, TypeError, AttributeError, RecursionError, OverflowError) as exc:
        raise ValueError('保存路径嵌套字段无效。') from exc


def validate_row(kind, row):
    if kind not in KINDS: raise ValueError('独立调整kind未登记。')
    fields(row, {'id', 'version', 'snapshot', 'item_id', 'created', 'command_id'})
    body = validate(row['snapshot'], KINDS[kind]); finite(row['created']); text(row['command_id']); text(row['item_id'])
    if (row['id'] != kind + ':' + body['version'] or row['version'] != body['version'] or
            (KINDS[kind] != PREVIEW and row['item_id'] != body['item_id'])):
        raise ValueError('独立调整记录外壳、内容身份或归属不一致。')
    return deepcopy(row)


class WorkCoordinatorHandoffAdjustmentRecords:
    """Immutable storage only; no handler and no execution/save authority."""
    def __init__(self, service): self.s = service

    @staticmethod
    def ref(row): return {k: row[k] for k in ('id', 'version')}

    def get(self, db, protocol, ref):
        reference(ref)
        kind = KIND_FOR[protocol]
        raw = db.execute('SELECT kind,data FROM collaboration WHERE id=?', (ref['id'],)).fetchone()
        if not raw or raw['kind'] != kind: raise ValueError('独立调整记录缺失或kind不符。')
        row = validate_row(kind, json.loads(raw['data']))
        if self.ref(row) != ref: raise ValueError('独立调整引用版本已变化。')
        return row

    def put(self, db, protocol, snapshot, item_id):
        kind = KIND_FOR[protocol]; validate(snapshot, protocol)
        row = {'id': kind + ':' + snapshot['version'], 'version': snapshot['version'],
            'snapshot': deepcopy(snapshot), 'item_id': item_id, 'created': time.time(), 'command_id': self.s.command_identity}
        validate_row(kind, row)
        prior = db.execute('SELECT kind,data FROM collaboration WHERE id=?', (row['id'],)).fetchone()
        if prior:
            if prior['kind'] != kind: raise ValueError('独立调整内容id被其它kind占用，不能覆盖。')
            saved = validate_row(kind, json.loads(prior['data']))
            if saved['snapshot'] != snapshot or saved['item_id'] != item_id:
                raise ValueError('独立调整不可变内容不一致，不能覆盖。')
            return saved
        self.s.collaboration.put(db, kind, row)
        return deepcopy(row)


def required_records(db, file_baseline):
    """Backup asset graph check; does not treat records as saved-edge proof."""
    rows = {}
    for kind in KINDS:
        for raw in db.execute('SELECT data FROM collaboration WHERE kind=?', (kind,)):
            row = validate_row(kind, json.loads(raw[0])); rows[row['id']] = row
    def get(ref, protocol):
        value = rows.get(ref['id'])
        if (not value or value['id'] != KIND_FOR[protocol]+':'+ref['version'] or
                {k:value[k] for k in ('id','version')} != ref):
            raise ValueError('调整资产缺少准确同协议引用。')
        return value
    for row in rows.values():
        body = row['snapshot']; protocol = body['protocol']
        if protocol == PREVIEW:
            file_baseline(body['material_observation'])
        elif protocol == CHECKPOINT:
            file_baseline(body['history']['material_observation'])
            if body['preview']['material_observation'] != body['history']['material_observation']:
                raise ValueError('检查点资产的预览与原材料观察不同。')
        elif protocol == CANDIDATE:
            checkpoint = get(body['checkpoint'], CHECKPOINT); preview = get(body['preview'], PREVIEW)
            if (checkpoint['snapshot']['preview'] != preview['snapshot'] or
                    checkpoint['snapshot']['proposal'] != body['proposal'] or
                    row['item_id'] != checkpoint['item_id'] or row['item_id'] != preview['item_id'] or
                    any(body[k] != checkpoint['snapshot'][k] for k in ('root','parent_plan','capability')) or
                    any(body[k] != preview['snapshot'][k] for k in
                        ('candidate_units','carry_sources','historical_coordinators'))):
                raise ValueError('候选资产与准确检查点或预览来源不一致。')
        elif protocol == APPLICATION:
            candidate = get(body['candidate'], CANDIDATE)
            if (candidate['snapshot']['checkpoint'] != body['checkpoint'] or
                    candidate['snapshot']['proposal'] != body['proposal'] or
                    candidate['item_id'] != row['item_id']):
                raise ValueError('应用资产缺少准确候选或原提案。')
        else:
            application = get(body['application'], APPLICATION)
            if (application['item_id'] != row['item_id'] or application['snapshot']['proposal'] != body['proposal'] or
                    application['snapshot']['child_plan'] != body['to_plan'] or
                    application['snapshot']['parent_plan'] != body['from_plan']):
                raise ValueError('消费资产缺少准确保存应用。')
