"""Independent explicit-round adjacent contracts. No saved-edge authority."""
from copy import deepcopy
from pathlib import Path

from .store import digest
from .work_transitions import plan_ref
from .work_coordinator_handoff_roots import PLAN_FIELDS, CONFIG_FIELDS, MARKER_FIELD, ROOT
from .work_coordinator_handoff_records import fields, reference, text, sha, task_revision, versioned
from .work_coordinator_handoff_adjustment_records import (
    original_driver, original_authorization, finite, hashed, units, carry, keys, member, strings,
    auth_ref, usage)
from .work_coordinator_handoff_adjustment_contracts import (
    closed, AUTH_EXTRA as OLD_AUTH_EXTRA, DRIVER_EXTRA as OLD_DRIVER_EXTRA)
from .work_coordinator_handoff_mixed_records import (
    LINEAGE, CHECKPOINT, CANDIDATE, OPERATION, PAUSE_REASON, parent_ref, record_ref,
    validate_row, KIND_FOR)

PLAN_EXTRA = {'previous_plan_reference', 'result_lineage', 'historical_coordinators',
              'transition', 'coordination_revision', 'coordinator_file_handoff_round_lineage'}
AUTH_EXTRA = {'previous_authorization', 'transferred_at', 'transfer_registration', 'usage_at_transfer'}
DRIVER_EXTRA = {'previous_plan_reference', 'transition_registration'}


@closed
def successor_plan(value):
    fields(value, PLAN_FIELDS | PLAN_EXTRA); hashed(value)
    reference(value['previous_plan_reference']); task_revision(value['item_revision'])
    for name in ('id', 'item_id', 'project_id'): text(value[name])
    finite(value['created']); units(value['units']); carry(value['result_lineage'])
    for name in ('coordinator', 'integrator'): member(value[name])
    fields(value['work_requirements'], {'title', 'goal', 'constraints_text', 'acceptance'})
    if any(not isinstance(v, str) for v in value['work_requirements'].values()):
        raise ValueError('新回合须保留完整原要求。')
    marker = value[MARKER_FIELD]
    fields(marker, {'protocol', 'plan_id', 'item_id', 'item_revision', 'project_id', 'project_root',
                   'work_requirements_version', 'configuration_version', 'version'}); hashed(marker)
    task_revision(marker['item_revision'])
    for name in ('plan_id', 'item_id', 'project_id', 'project_root'): text(marker[name])
    for name in ('work_requirements_version', 'configuration_version'): sha(marker[name])
    if (marker['protocol'] != ROOT or str(Path(marker['project_root']).absolute()) != marker['project_root'] or
            marker['item_id'] != value['item_id'] or marker['project_id'] != value['project_id'] or
            marker['work_requirements_version'] != digest(value['work_requirements']) or
            value['mode'] not in {'leader', 'host_experts'} or type(value['max_parallel']) is not int or
            value['max_parallel'] != 1):
        raise ValueError('新回合须保留准确原根与模式配置。')
    if value['hard_cash_limit_cny'] is not None:
        finite(value['hard_cash_limit_cny'])
        if value['hard_cash_limit_cny'] > 1000000: raise ValueError('新回合原硬费用上限无效。')
    from .work_file_handoff_config import definition
    if not value['file_handoffs'] or definition(value['file_handoffs'], value['units'], 1) != value['file_handoffs']:
        raise ValueError('新回合须保留完整规范关系。')
    keys(value['historical_coordinators'])
    link = value['coordinator_file_handoff_round_lineage']
    fields(link, {'protocol', 'root', 'parent_plan', 'parent_path', 'candidate'})
    for name in ('root', 'parent_plan'): reference(link[name])
    record_ref(link['candidate'], CANDIDATE)
    fields(link['parent_path'], {'protocol', 'version', 'root', 'plan'})
    from .work_coordinator_handoff_mixed_records import PARENT
    sha(link['parent_path']['version'])
    if (link['protocol'] != LINEAGE or link['parent_path']['protocol'] not in (PARENT, 'COORDINATOR_FILE_HANDOFF_ROUND_PARENT_OBSERVATION_V2') or
            link['parent_path']['root'] != link['root'] or link['parent_path']['plan'] != link['parent_plan'] or
            link['parent_plan'] != value['previous_plan_reference'] or
            value['previous_plan'] != link['parent_plan']['id'] or value['state'] != 'PREPARED' or
            value['id'] in {link['root']['id'], link['parent_plan']['id']} or
            not value['id'].startswith('teamwork:') or marker['plan_id'] != link['root']['id']):
        raise ValueError('新回合独立lineage须绑定准确根、父观察与候选。')
    # These nested shapes are exactly the independent candidate fields. Adjacent
    # verification below binds them to the actual checkpoint/candidate records.
    from .work_coordinator_handoff_mixed_records import prior_check
    r = value['coordination_revision']
    fields(r, {'id', 'item_id', 'unit_key', 'source', 'reason', 'prior_check', 'answer', 'answer_chars', 'answer_digest'})
    text(r['id']); text(r['reason']); prior_check(r['prior_check']); sha(r['answer_digest'])
    if not r['reason'].strip() or len(r['reason']) > 2000: raise ValueError('新回合原因须为非空且最多2000字。')
    from .work_coordinator_handoff_adjustment_records import source
    source(r['source'], full=True)
    selected = next((u for u in value['units'] if u['key'] == r['unit_key']), None)
    if (not r['id'].startswith('coordination-revision:') or r['item_id'] != value['item_id'] or
            selected is None or selected['role'] != 'coordinate' or r['unit_key'] in value['historical_coordinators'] or
            r['source']['plan_id'] != link['parent_plan']['id'] or
            r['source']['plan_version'] != link['parent_plan']['version'] or
            r['source']['unit_revision'] != digest(selected) or
            type(r['answer_chars']) is not int or r['answer_chars'] < 1):
        raise ValueError('新回合身份须保持原声明key及完整源回合。')
    if r['answer_chars'] <= 32000:
        if not isinstance(r['answer'], str) or len(r['answer']) != r['answer_chars'] or digest(r['answer']) != r['answer_digest']:
            raise ValueError('新回合须保存准确原回复。')
    elif r['answer'] is not None: raise ValueError('长回复须明确只保存摘要与字数。')
    t = value['transition']
    fields(t, {'kind', 'source', 'reason', 'usage_at_transition', 'authorization_from', 'command_id', 'no_dispatch'})
    usage(t['usage_at_transition']); auth_ref(t['authorization_from']); text(t['command_id'])
    if (t['kind'] != OPERATION or t['source'] != r['source'] or t['reason'] != r['reason'] or t['no_dispatch'] is not True):
        raise ValueError('新回合transition须为明确登记且不派发。')
    if set(value['result_lineage']) - {u['key'] for u in value['units'] if u['role'] != 'coordinate'}:
        raise ValueError('新回合不能将旧协调回复作为普通沿用成果。')
    return deepcopy(value)


@closed
def transferred_authorization(value):
    if not isinstance(value, dict) or not AUTH_EXTRA <= set(value): raise ValueError('新回合授权缺少登记依据。')
    original_authorization({k: v for k, v in value.items() if k not in AUTH_EXTRA})
    for name in ('id', 'revision', 'item_id', 'driver_generation', 'plan_id', 'command_id'): text(value[name])
    sha(value['plan_version']); finite(value['created']); finite(value['transferred_at'])
    auth_ref(value['previous_authorization']); record_ref(value['transfer_registration'], CANDIDATE)
    usage(value['usage_at_transfer'])
    if (type(value['max_attempts_total']) is not int or not 1 <= value['max_attempts_total'] <= 1000 or
            type(value['already_used']) is not int or value['already_used'] != 0 or
            value['id'] != 'unit-auth:' + value['driver_generation'] or value['dispatch_before'] <= value['created'] or
            value['scope'] != 'ALL_COOPERATIVE_ATTEMPTS_FOR_BUSINESS_ITEM' or
            value['deadline_enforcement'] != 'NEW_DISPATCH_ONLY' or value['cash_enforcement'] != 'UNSUPPORTED' or
            value['native_permissions'] != 'VENDOR_DEFAULTS' or value['id'] != value['previous_authorization']['id'] or
            value['revision'] == value['previous_authorization']['revision'] or value['transferred_at'] < value['created']):
        raise ValueError('新回合授权须保留原累计、generation、最初截止和原生权限。')
    return deepcopy(value)


@closed
def successor_driver(value):
    if not isinstance(value, dict) or not DRIVER_EXTRA <= set(value): raise ValueError('新回合责任缺少登记依据。')
    original_driver({k: v for k, v in value.items() if k not in DRIVER_EXTRA})
    for name in ('id', 'item_id', 'generation', 'revision', 'kind', 'plan_id', 'state', 'reason', 'instance', 'scope'): text(value[name])
    task_revision(value['item_revision']); sha(value['plan_version']); finite(value['created']); finite(value['updated'])
    strings(value['task_ids']); reference(value['previous_plan_reference']); record_ref(value['transition_registration'], CANDIDATE)
    if (value['kind'] != 'COOPERATIVE' or value['previous_generation'] is not None or
            value['updated'] < value['created'] or len(set(value['task_ids'])) != len(value['task_ids']) or
            value['state'] not in {'PAUSED', 'RESERVED', 'RELEASED'}):
        raise ValueError('新回合责任须保留原generation和真实任务历史。')
    if 'last_unit_run' in value: text(value['last_unit_run'])
    if 'released' in value: finite(value['released'])
    return deepcopy(value)


@closed
def prepared(checkpoint, candidate):
    validate_row(KIND_FOR[CHECKPOINT], checkpoint); validate_row(KIND_FOR[CANDIDATE], candidate)
    cp, body = checkpoint['snapshot'], candidate['snapshot']; obs = cp['observation']
    if not cp['reason'].strip() or len(cp['reason']) > 2000: raise ValueError('新回合原因须为非空且最多2000字。')
    for name in ('item_id', 'root', 'parent_plan', 'parent_path', 'source', 'selected_unit', 'reason', 'operation'):
        if body[name] != cp[name]: raise ValueError('新回合候选与检查点相邻身份不符。')
    ref = {k: candidate[k] for k in ('id', 'version')}
    src = next(f for f in obs['history']['frontier'] if f['run_id'] == obs['source']['run_id'])['evidence']
    answer = src['answer']; revision = body['coordination_revision']
    if (body['checkpoint'] != {k: checkpoint[k] for k in ('id', 'version')} or
            body['parent_path'] != parent_ref(obs) or body['carry_sources'] != obs['carry_sources'] or
            body['historical_coordinators'] != obs['historical_coordinators'] or
            revision['prior_check'] != obs['prior_check'] or revision['answer_digest'] != digest(answer) or
            revision['answer_chars'] != len(answer) or revision['answer'] != (answer if len(answer) <= 32000 else None) or
            body['transition']['usage_at_transition'] != obs['usage'] or
            body['transition']['authorization_from'] != {k: obs['history']['authorization'][k] for k in ('id', 'revision')} or
            checkpoint['command_id'] != candidate['command_id'] or body['transition']['command_id'] != candidate['command_id'] or
            not checkpoint['created'] <= body['child_seed']['created'] <= candidate['created'] < obs['original_dispatch_before']):
        raise ValueError('新回合候选必须由完整真实检查点逐值重建。')
    parent = cp['plan']
    child = {k: deepcopy(parent[k]) for k in PLAN_FIELDS if k != 'version'}
    child.update(body['child_seed'], item_revision=obs['history']['task']['revision'],
        previous_plan=parent['id'], previous_plan_reference=plan_ref(parent),
        result_lineage=deepcopy(body['carry_sources']), historical_coordinators=deepcopy(body['historical_coordinators']),
        transition=deepcopy(body['transition']), coordination_revision=deepcopy(revision),
        coordinator_file_handoff_round_lineage={'protocol': LINEAGE, 'root': body['root'],
            'parent_plan': body['parent_plan'], 'parent_path': body['parent_path'], 'candidate': ref})
    return successor_plan(versioned(child))


@closed
def adjacent(checkpoint, candidate, child, auth_before, auth_after, driver_before, driver_after):
    """Adjacent values only; actual events/full saved path are still mandatory."""
    expected = prepared(checkpoint, candidate); transferred_authorization(auth_after); successor_driver(driver_after)
    obs = checkpoint['snapshot']['observation']; parent = checkpoint['snapshot']['plan']; body = candidate['snapshot']
    ref = {k: candidate[k] for k in ('id', 'version')}
    if (child != expected or auth_before != obs['history']['authorization'] or driver_before != obs['history']['driver'] or
            any(child[k] != parent[k] for k in CONFIG_FIELDS) or child[MARKER_FIELD] != parent[MARKER_FIELD]):
        raise ValueError('新回合须保持完整原定义、marker与检查点原责任授权。')
    changed_auth = {'revision', 'plan_id', 'plan_version', 'command_id'} | AUTH_EXTRA | OLD_AUTH_EXTRA
    if ({k: v for k, v in auth_after.items() if k not in changed_auth} !=
            {k: v for k, v in auth_before.items() if k not in changed_auth} or
            auth_after['previous_authorization'] != {k: auth_before[k] for k in ('id', 'revision')} or
            auth_after['transfer_registration'] != ref or auth_after['usage_at_transfer'] != obs['usage'] or
            auth_after['command_id'] != candidate['command_id']):
        raise ValueError('新回合转移不得重置累计、延长最初截止或变更登记来源。')
    changed_driver = {'revision', 'updated', 'plan_id', 'plan_version', 'state', 'reason'} | DRIVER_EXTRA | OLD_DRIVER_EXTRA
    if ({k: v for k, v in driver_after.items() if k not in changed_driver} !=
            {k: v for k, v in driver_before.items() if k not in changed_driver} or
            driver_after['revision'] == driver_before['revision'] or driver_after['state'] != 'PAUSED' or
            driver_after['reason'] != PAUSE_REASON or driver_after['previous_plan_reference'] != plan_ref(parent) or
            driver_after['transition_registration'] != ref or
            not child['created'] <= candidate['created'] <= auth_after['transferred_at'] <= driver_after['updated'] < auth_after['dispatch_before']):
        raise ValueError('新回合登记须保持原任务绑定历史并明确暂停。')
    for value in (auth_after, driver_after):
        if value['plan_id'] != child['id'] or value['plan_version'] != child['version']:
            raise ValueError('新回合责任及授权须指向准确新方案。')
    return deepcopy({'plan': child, 'authorization': auth_after, 'driver': driver_after,
                     'saved_edge_verified': False, 'public_apply': False, 'no_dispatch': True})
