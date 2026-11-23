"""Comparable cost references and observed native-turn durations, without dispatch."""
from statistics import median
import json
import math
import time

from .model_catalog import reference


def token_count(value, label):
    if type(value) is not int or not 0 <= value <= 100_000_000:
        raise ValueError(label + '须为0至1亿之间的整数。')
    return value


def cost_estimate(entry, input_tokens, output_tokens):
    price = entry.get('price') or {}
    result = {'status': 'UNKNOWN', 'currency': price.get('currency'),
              'checked': entry.get('checked'), 'sources': entry.get('sources', []),
              'basis': 'REGISTERED_REFERENCE; ACTUAL_CHARGE_SEPARATE'}
    if price.get('kind') == 'SUBSCRIPTION':
        return {**result, 'status': 'SUBSCRIPTION', 'currency': None,
                'included_usage_multipliers': price.get('included_usage_multipliers', {})}
    terms = []
    if 'peak' in price and 'off_peak' in price:
        for period in ('peak', 'off_peak'):
            rate = price[period]
            terms.extend((rate[key], rate['output']) for key in ('cache_hit_input', 'cache_miss_input'))
    for region in ('beijing_and_global_regions', 'singapore_and_us_scope'):
        if region in price:
            rate = price[region]
            terms.extend((rate[key], rate['output']) for key in ('input', 'implicit_cache_input'))
    if not terms:
        return result
    amounts = [(input_tokens * input_rate + output_tokens * output_rate) / price['unit_tokens']
               for input_rate, output_rate in terms]
    return {**result, 'status': 'REFERENCE_RANGE', 'minimum': round(min(amounts), 8),
            'maximum': round(max(amounts), 8),
            'conditions': 'CACHE_REGION_AND_TIME_NOT_RESOLVED'}


def context(service, db, member, connection, quota):
    entry = reference(member['provider'], member['model'])
    samples = []
    for row in db.execute("""SELECT a.started,a.result FROM attempts a JOIN tasks t ON a.task_id=t.id
            WHERE a.state='COMPLETED' AND json_extract(t.spec,'$.provider')=?
              AND json_extract(t.spec,'$.model')=? ORDER BY a.started DESC LIMIT 10""",
            (member['provider'], member['model'])):
        result = json.loads(row['result']) if row['result'] else {}
        end = result.get('ended_at')
        if (type(end) not in (int, float) or not math.isfinite(end) or end < row['started']
                or not result.get('known_tools_exited')
                or (result.get('owned_job') or {}).get('active_processes') != 0):
            continue
        samples.append(end - row['started'])
    return {'reference_checked': entry.get('checked'), 'price_reference': entry.get('price'),
            'price_sources': entry.get('sources', []),
            'latency': {'status': 'OBSERVED_SAMPLES' if samples else 'UNKNOWN',
                        'sample_count': len(samples),
                        'median_seconds': round(median(samples), 3) if samples else None,
                        'basis': 'WHOLE_NATIVE_TURNS; TASKS_DIFFER; NOT_A_RUNTIME_GUARANTEE'},
            'connection_available': connection['available'], 'quota_source_id': quota['id'],
            'quota_unit': quota['unit'], 'quota_remaining': quota['remaining'],
            'quota_observed_at': quota['observed_at'], 'quota_enforcement': 'INFORMATION_ONLY'}


def preview(resources, db, payload):
    if set(payload) - {'member_ids', 'input_tokens', 'output_tokens', 'deadline_seconds'}:
        raise ValueError('成员比较字段不支持。')
    inputs = token_count(payload.get('input_tokens', 1000), '预计输入token')
    outputs = token_count(payload.get('output_tokens', 1000), '预计输出token')
    deadline = payload.get('deadline_seconds')
    if deadline is not None and (type(deadline) not in (int, float) or not math.isfinite(deadline) or deadline <= 0):
        raise ValueError('参考时长须为正数或留空。')
    catalog = resources.public_catalog(db)
    ids = payload.get('member_ids', [m['id'] for m in catalog['members']])
    if (not isinstance(ids, list) or len(ids) > 100
            or any(not isinstance(identity, str) for identity in ids)
            or len(set(ids)) != len(ids)):
        raise ValueError('请选择最多100个不同成员。')
    rows = []
    for identity in ids:
        member = next((m for m in catalog['members'] if m['id'] == identity), None)
        if member is None:
            raise ValueError('成员已不存在，请刷新目录。')
        connection = next((c for c in catalog['connections'] if c['id'] == member['connection_id']), None)
        quota = next((q for q in catalog['quota_sources'] if connection and q['id'] == connection['quota_id']), None)
        if connection is None or quota is None:
            rows.append({'member_id': identity, 'name': member['name'], 'available': False,
                         'cost': {'status': 'UNKNOWN'}, 'latency': {'status': 'UNKNOWN'}})
            continue
        evidence = context(resources.s, db, member, connection, quota)
        entry = reference(member['provider'], member['model'])
        elapsed = evidence['latency']['median_seconds']
        relation = ('UNKNOWN' if elapsed is None or deadline is None else
                    'WITHIN_OBSERVED_MEDIAN' if elapsed <= deadline else 'ABOVE_OBSERVED_MEDIAN')
        rows.append({'member_id': identity, 'name': member['name'], 'role': member['role'],
                     'provider': member['provider'], 'model': member['model'], 'available': member['available'],
                     'cost': cost_estimate(entry, inputs, outputs),
                     'latency': evidence['latency'], 'deadline_relation': relation,
                     'quota': {k: evidence[k] for k in ('quota_source_id', 'quota_unit',
                                                       'quota_remaining', 'quota_observed_at')}})
    groups = {}
    for row in rows:
        key = row['cost'].get('currency') or row['cost']['status']
        groups.setdefault(key, []).append(row['member_id'])
    ranked = {currency: sorted(ids, key=lambda identity: next(
        (r['cost'].get('maximum', math.inf), r['latency'].get('median_seconds') or math.inf, r['name'])
        for r in rows if r['member_id'] == identity)) for currency, ids in groups.items()}
    return {'members': rows, 'comparable_groups': ranked, 'input_tokens': inputs,
            'output_tokens': outputs, 'deadline_seconds': deadline,
            'computed_at': time.time(), 'no_dispatch': True, 'automatic_selection': False}
