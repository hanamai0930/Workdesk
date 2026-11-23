"""Closed successor shapes and adjacent-value checks; no source/save authority."""
from copy import deepcopy
from functools import wraps
from pathlib import Path

from .store import digest
from .work_transitions import plan_ref
from .work_coordinator_handoff_roots import PLAN_FIELDS, CONFIG_FIELDS, MARKER_FIELD, ROOT
from .work_coordinator_handoff_records import fields, reference, text, task_revision, sha
from .work_coordinator_handoff_adjustment_records import (
    LINEAGE, PAUSE_REASON, original_driver, original_authorization, hashed,
    finite, units, carry, keys, transition, auth_ref, usage, member, strings)


PLAN_EXTRA = {'previous_plan_reference', 'result_lineage', 'historical_coordinators',
              'transition', 'coordinator_file_handoff_lineage'}
AUTH_EXTRA = {'previous_authorization', 'transferred_at', 'transfer_proposal', 'usage_at_transfer'}
DRIVER_EXTRA = {'previous_plan_reference', 'transition_proposal'}


def closed(function):
    @wraps(function)
    def checked(*args):
        try: return function(*args)
        except (KeyError, TypeError, AttributeError, LookupError, OverflowError, RecursionError) as exc:
            raise ValueError('后继完整字段或嵌套格式不符。') from exc
    return checked


def auth_reference(value): return {k:value[k] for k in ('id', 'revision')}


@closed
def successor_plan(value):
    fields(value, PLAN_FIELDS | PLAN_EXTRA); hashed(value)
    reference(value['previous_plan_reference']); task_revision(value['item_revision'])
    for name in ('id', 'item_id', 'project_id'): text(value[name])
    finite(value['created']); units(value['units']); carry(value['result_lineage'])
    for name in ('coordinator', 'integrator'):
        member(value[name])
        if any(v is not None and not isinstance(v,str) for k,v in value[name].items() if k != 'reference'):
            raise ValueError('后继成员文本字段无效。')
    fields(value['work_requirements'], {'title','goal','constraints_text','acceptance'})
    if any(not isinstance(v,str) for v in value['work_requirements'].values()):
        raise ValueError('后继原要求须为完整文本。')
    marker = value[MARKER_FIELD]
    fields(marker, {'protocol','plan_id','item_id','item_revision','project_id','project_root',
        'work_requirements_version','configuration_version','version'}); hashed(marker)
    task_revision(marker['item_revision'])
    for name in ('plan_id','item_id','project_id','project_root'): text(marker[name])
    for name in ('work_requirements_version','configuration_version'): sha(marker[name])
    if (marker['protocol'] != ROOT or str(Path(marker['project_root']).absolute()) != marker['project_root'] or
            marker['item_id'] != value['item_id'] or marker['project_id'] != value['project_id'] or
            marker['work_requirements_version'] != digest(value['work_requirements']) or
            value['mode'] not in {'leader','host_experts'} or type(value['max_parallel']) is not int or
            value['max_parallel'] != 1):
        raise ValueError('后继须保留准确原根配置边界。')
    if value['hard_cash_limit_cny'] is not None:
        finite(value['hard_cash_limit_cny'])
        if value['hard_cash_limit_cny'] > 1000000: raise ValueError('原硬费用上限范围无效。')
    from .work_file_handoff_config import definition
    if not value['file_handoffs'] or definition(value['file_handoffs'],value['units'],value['max_parallel']) != value['file_handoffs']:
        raise ValueError('后继须保留完整规范关系。')
    keys(value['historical_coordinators']); transition(value['transition'])
    link = value['coordinator_file_handoff_lineage']
    fields(link, {'protocol', 'root', 'parent_plan', 'candidate'})
    for name in ('root', 'parent_plan', 'candidate'): reference(link[name])
    if (link['protocol'] != LINEAGE or link['parent_plan'] != value['previous_plan_reference'] or
            value['previous_plan'] != link['parent_plan']['id'] or value['state'] != 'PREPARED' or
            value['id'] in {link['root']['id'], link['parent_plan']['id']} or
            not value['id'].startswith('teamwork:') or marker['plan_id'] != link['root']['id']):
        raise ValueError('后继须保持准确原根及唯一父方案身份。')
    return deepcopy(value)


@closed
def transferred_authorization(value):
    fields(value, {'id', 'revision', 'item_id', 'driver_generation', 'plan_id', 'plan_version',
        'max_attempts_total', 'dispatch_before', 'created', 'already_used', 'scope',
        'deadline_enforcement', 'cash_enforcement', 'native_permissions', 'command_id'} | AUTH_EXTRA)
    original_authorization({k: v for k, v in value.items() if k not in AUTH_EXTRA})
    for name in ('id','revision','item_id','driver_generation','plan_id','command_id'): text(value[name])
    sha(value['plan_version']); finite(value['created'])
    if (type(value['max_attempts_total']) is not int or not 1 <= value['max_attempts_total'] <= 1000 or
            type(value['already_used']) is not int or value['already_used'] != 0 or
            value['id'] != 'unit-auth:'+value['driver_generation'] or
            value['dispatch_before'] <= value['created'] or
            value['scope'] != 'ALL_COOPERATIVE_ATTEMPTS_FOR_BUSINESS_ITEM' or
            value['deadline_enforcement'] != 'NEW_DISPATCH_ONLY' or value['cash_enforcement'] != 'UNSUPPORTED' or
            value['native_permissions'] != 'VENDOR_DEFAULTS'):
        raise ValueError('后继须保留原累计授权完整范围。')
    auth_ref(value['previous_authorization']); reference(value['transfer_proposal'])
    finite(value['transferred_at']); usage(value['usage_at_transfer'])
    if (value['id'] != value['previous_authorization']['id'] or
            value['revision'] == value['previous_authorization']['revision'] or
            value['transferred_at'] < value['created']):
        raise ValueError('后继授权须沿用原id并新增准确revision。')
    return deepcopy(value)


@closed
def successor_driver(value):
    if not isinstance(value, dict) or not DRIVER_EXTRA <= set(value):
        raise ValueError('后继责任缺少准确父方案及提案。')
    original_driver({k: v for k, v in value.items() if k not in DRIVER_EXTRA})
    for name in ('id','item_id','generation','revision','kind','plan_id','state','reason','instance','scope'): text(value[name])
    task_revision(value['item_revision']); sha(value['plan_version'])
    finite(value['created']); finite(value['updated']); strings(value['task_ids'])
    if (value['kind'] != 'COOPERATIVE' or value['previous_generation'] is not None or
            value['updated'] < value['created'] or len(set(value['task_ids'])) != len(value['task_ids'])):
        raise ValueError('后继责任须保留原generation及完整任务身份。')
    if 'last_unit_run' in value: text(value['last_unit_run'])
    if 'released' in value: finite(value['released'])
    reference(value['previous_plan_reference']); reference(value['transition_proposal'])
    if value['state'] not in {'PAUSED', 'RESERVED', 'RELEASED'}:
        raise ValueError('后继责任状态无效。')
    return deepcopy(value)


@closed
def adjacent(parent, child, candidate, auth_before, auth_after, driver_before, driver_after):
    """Exact adjacent values only. Actual records/events/sources remain mandatory."""
    successor_plan(child); transferred_authorization(auth_after); successor_driver(driver_after)
    body = candidate['snapshot']
    expected = {**deepcopy(parent), **body['child_seed'], 'item_revision': driver_before['item_revision'],
        'units': deepcopy(body['candidate_units']), 'previous_plan': parent['id'],
        'previous_plan_reference': plan_ref(parent), 'result_lineage': deepcopy(body['carry_sources']),
        'historical_coordinators': deepcopy(body['historical_coordinators']),
        'transition': deepcopy(body['transition']), 'coordinator_file_handoff_lineage': {
            'protocol': LINEAGE, 'root': body['root'], 'parent_plan': body['parent_plan'],
            'candidate': {k: candidate[k] for k in ('id', 'version')}}}
    expected.pop('version'); expected['version'] = digest(expected)
    if child != expected or body['parent_plan'] != plan_ref(parent) or child[MARKER_FIELD] != parent[MARKER_FIELD]:
        raise ValueError('后继必须为完整准确候选body，marker仍绑定原根配置。')
    before_units = {u['key']: u for u in parent['units']}
    if (list(before_units) != [u['key'] for u in child['units']] or
            any({k:v for k,v in u.items() if k != 'depends'} !=
                {k:v for k,v in before_units[u['key']].items() if k != 'depends'} for u in child['units']) or
            any(child[k] != parent[k] for k in CONFIG_FIELDS if k != 'units')):
        raise ValueError('依赖调整只能更改原声明普通单元depends。')
    if any(u != before_units[u['key']] for u in child['units'] if u['role'] == 'coordinate'):
        raise ValueError('协调单元定义必须保持原字节内容。')
    changed_auth = {'revision', 'plan_id', 'plan_version', 'command_id'} | AUTH_EXTRA
    if (auth_reference(auth_before) != body['transition']['authorization_from'] or
            auth_after['previous_authorization'] != auth_reference(auth_before) or
            auth_after['id'] != auth_before['id'] or auth_after['revision'] == auth_before['revision'] or
            {k:v for k,v in auth_after.items() if k not in changed_auth} !=
            {k:v for k,v in auth_before.items() if k not in changed_auth} or
            auth_after['transfer_proposal'] != body['proposal'] or
            auth_after['usage_at_transfer'] != body['transition']['usage_at_transition'] or
            auth_after['transferred_at'] < child['created']):
        raise ValueError('转移授权不得增加原累计、最初截止或改变真实转移来源。')
    changed_driver = {'revision', 'updated', 'plan_id', 'plan_version', 'state', 'reason'} | DRIVER_EXTRA
    if ({k:v for k,v in driver_after.items() if k not in changed_driver} !=
            {k:v for k,v in driver_before.items() if k not in changed_driver} or
            driver_after['revision'] == driver_before['revision'] or
            driver_after['updated'] < child['created'] or driver_after['state'] != 'PAUSED' or
            driver_after['reason'] != PAUSE_REASON or
            driver_after['previous_plan_reference'] != plan_ref(parent) or
            driver_after['transition_proposal'] != body['proposal']):
        raise ValueError('保存责任须同generation按准确原因暂停，保留原绑定历史。')
    for value in (auth_after, driver_after):
        if value['plan_id'] != child['id'] or value['plan_version'] != child['version']:
            raise ValueError('保存后授权与责任必须指向准确后继。')
    if auth_after['command_id'] != body['transition']['command_id']:
        raise ValueError('转移必须属于准确保存command。')
    return deepcopy({'plan': child, 'authorization': auth_after, 'driver': driver_after,
        'saved_edge_verified': False, 'public_apply': False, 'no_dispatch': True})
