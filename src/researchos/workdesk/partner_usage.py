"""Read-only accounting from the same v2 partner runs and native receipts."""
from __future__ import annotations

import json
from pathlib import Path
from decimal import Decimal

from .deepseek_pricing import estimate

TOKEN_KEYS = ('input', 'cache_hit', 'cache_miss', 'output', 'tokens')


def catalog():
    value = json.loads(Path(__file__).with_name('partners').joinpath('api_providers.json').read_text(encoding='utf-8'))
    return value['partners'], value['api_providers']


def has_partner_tables(db):
    return db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='core_partner_objects'").fetchone() is not None


def objects(db, kind):
    if not has_partner_tables(db):
        return []
    return [json.loads(row[0]) for row in db.execute('SELECT data FROM core_partner_objects WHERE kind=?', (kind,))]


def count(value):
    return value if type(value) is int and 0 <= value <= 2**53 - 1 else None


def _get(value, *path):
    for key in path:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def normalized(value, partner, protocol=None):
    """Keep reported zero and unknown distinct; never add cumulative snapshots."""
    value = value if isinstance(value, dict) else {}
    if isinstance(value.get('total'), dict):
        value = value['total']
    incoming = hit = miss = outgoing = total = None
    derive = False
    if 'prompt_tokens' in value or 'completion_tokens' in value:
        incoming, outgoing, total = (count(value.get(key)) for key in ('prompt_tokens', 'completion_tokens', 'total_tokens'))
        hit = count(value.get('prompt_cache_hit_tokens'))
        if hit is None:
            hit = count(_get(value, 'prompt_tokens_details', 'cached_tokens'))
        miss = count(value.get('prompt_cache_miss_tokens'))
        # DeepSeek sometimes omits prompt_tokens but reports both partitions.
        if incoming is None and hit is not None and miss is not None:
            incoming = hit + miss
        derive = True
    elif 'promptTokenCount' in value or 'candidatesTokenCount' in value:
        incoming, outgoing, total = (count(value.get(key)) for key in ('promptTokenCount', 'candidatesTokenCount', 'totalTokenCount'))
        hit = count(value.get('cachedContentTokenCount'))
        thoughts = count(value.get('thoughtsTokenCount'))
        if outgoing is not None and thoughts is not None:
            outgoing += thoughts
        # A missing thought count is not a reported zero.
        derive = thoughts is not None
    elif 'input_tokens' in value or 'output_tokens' in value:
        incoming, outgoing, total = (count(value.get(key)) for key in ('input_tokens', 'output_tokens', 'total_tokens'))
        additive_cache = protocol == 'messages' or (protocol is None and partner == 'claude')
        if additive_cache:
            hit, written = count(value.get('cache_read_input_tokens')), count(value.get('cache_creation_input_tokens'))
            if incoming is not None and hit is not None and written is not None:
                miss, incoming = incoming + written, incoming + hit + written
            else:
                incoming = None
            derive = True
        else:
            hit = count(value.get('cache_read_input_tokens'))
            if hit is None:
                hit = count(_get(value, 'input_tokens_details', 'cached_tokens'))
            derive = protocol == 'responses' or partner == 'codex'
    else:
        incoming, outgoing, total = (count(value.get(key)) for key in ('inputTokens', 'outputTokens', 'totalTokens'))
        if partner == 'deepseek' and protocol != 'chat_completions':
            hit, written = count(value.get('cacheReadTokens')), count(value.get('cacheWriteTokens'))
            if written is None and total is not None and incoming is not None and hit is not None and outgoing is not None and total >= incoming + hit + outgoing:
                written = total - incoming - hit - outgoing
            if incoming is not None and hit is not None and written is not None:
                miss, incoming = incoming + written, incoming + hit + written
            else:
                incoming = None
        else:
            hit = count(value.get('cachedInputTokens'))
            if hit is None:
                hit = count(value.get('cacheReadTokens'))
            if hit is None:
                hit = count(value.get('cachedReadTokens'))
        derive = partner == 'codex'
    if incoming is not None and hit is not None and miss is None and hit <= incoming:
        miss = incoming - hit
    if incoming is not None and miss is not None and hit is None and miss <= incoming:
        hit = incoming - miss
    if incoming is not None and ((hit is not None and hit > incoming) or (miss is not None and miss > incoming)
            or (hit is not None and miss is not None and hit + miss != incoming)):
        hit = miss = None
    basis = 'REPORTED' if total is not None else None
    consistent = True
    if incoming is not None and outgoing is not None:
        if total is not None and total != incoming + outgoing:
            consistent = False
            incoming = hit = miss = total = None
            basis = None
        elif total is None and derive:
            total, basis = incoming + outgoing, 'INPUT_PLUS_OUTPUT'
    return {**dict(zip(TOKEN_KEYS, (incoming, hit, miss, outgoing, total))), 'total_basis': basis,
            'usage_consistent': consistent}


def _provider(run):
    connection = run.get('connection_snapshot') or {}
    if connection.get('route') != 'api':
        return None
    return connection.get('api_provider') or connection.get('driver_provider') or run.get('api_provider') or 'deepseek'


def _record(run, account, value, at, source, identity, *, protocol=None, model=None, intents=0, received=False, scope=None):
    partner, api = run.get('partner_id'), _provider(run)
    connection = run.get('connection_snapshot') or {}
    model = model or run.get('resolved_model') or connection.get('model') or (run.get('result') or {}).get('model') or '未知型号'
    numbers = normalized(value, partner, protocol)
    price = estimate(api, model, numbers, at, 'response_observed') if api == 'deepseek' and connection.get('route') == 'api' else None
    return {'id': identity, 'provider': api or partner, 'partner_id': partner, 'api_provider': api,
            'account_id': run.get('account_id'), 'account_label': account.get('label'), 'route': connection.get('route') or 'account',
            'run_id': run['id'], 'task_id': run.get('parent_task_id') or run['id'], 'attempt_id': run.get('parent_attempt_id') or run['id'],
            'parent_task_id':run.get('parent_task_id'),'parent_attempt_id':run.get('parent_attempt_id'), 'model': model, 'source': source,
            'at': at, 'status': run.get('state'), **numbers, 'estimate': price,
            'request_intents': intents, 'has_request_receipt': received, 'usage_scope': scope,
            'usage_complete': numbers['tokens'] is not None and scope != 'UNIDENTIFIED_NATIVE_MESSAGE',
            'usage_note': '仅最后原生用量观测；缺少消息身份，不能证明全程计费总量' if scope == 'UNIDENTIFIED_NATIVE_MESSAGE' else None}


def run_records(db, start=0, end=None, *, partner_id='all', api_provider='all', account_id='all', route='all', run_id=None,parent_attempt_ids=None,project_id=None):
    if not has_partner_tables(db):
        return
    def inside(at):
        return start <= at and (end is None or at < end)

    accounts = {row['id']: row for row in objects(db, 'account')}
    event_table = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='core_partner_events'").fetchone()
    for run in objects(db, 'run'):
        if run.get('mode') != 'run' or (run_id is not None and run['id'] != run_id):
            continue
        if project_id is not None and run.get('project_id') != project_id:
            continue
        if parent_attempt_ids is not None and run.get('parent_attempt_id') not in parent_attempt_ids:
            continue
        connection = run.get('connection_snapshot') or {}
        if any(selected != 'all' and selected != actual for selected, actual in ((partner_id, run.get('partner_id')),
                (api_provider, _provider(run)), (account_id, run.get('account_id')), (route, connection.get('route') or 'account'))):
            continue
        # An unbounded read has no SQL upper parameter. Infinity is not
        # representable in the managed Python-to-Go JSON protocol.
        query = 'SELECT seq,at,data FROM core_partner_events WHERE run_id=?'
        params = (run['id'],)
        if end is not None:
            query += ' AND at<?'
            params += (end,)
        events = [(row[0], row[1], json.loads(row[2])) for row in db.execute(query + ' ORDER BY seq', params)] if event_table else []
        account = accounts.get(run.get('account_id'), {})
        intents, receipts = {}, {}
        for seq, at, event in events:
            identity = event.get('request_id')
            binding = event.get('binding') or {}
            if any(key in binding and binding[key] != expected for key,expected in
                   (('run_id',run['id']),('account_id',run.get('account_id')),('connection_id',run.get('connection_id')),('secret_version',run.get('secret_version')),('api_provider',_provider(run)))):
                continue
            if event.get('kind') == 'model_request_intent':
                intents.setdefault(identity or 'unidentified-intent:' + str(seq), (at, event))
            elif event.get('kind') == 'model_usage' and identity:
                if event.get('api_provider', event.get('provider', _provider(run))) != _provider(run):
                    continue
                receipts[identity] = (at, event)
        if receipts:
            # The presence of request receipts chooses the entire accounting
            # layer, including unanswered intents. Native totals cannot be added.
            for identity in dict.fromkeys((*intents, *receipts)):
                observed, receipt = receipts.get(identity, (None, {}))
                at = intents.get(identity, (observed, {}))[0]
                if at is None or not inside(at):
                    continue
                value = receipt.get('usage') if receipt.get('usage_complete', True) is not False else None
                yield _record(run, account, value, at, 'partner_gateway', run['id'] + ':' + identity,
                              protocol=receipt.get('protocol'), model=receipt.get('actual_model') or receipt.get('model'),
                              intents=int(identity in intents), received=identity in receipts, scope='REQUEST_RECEIPT')
            continue
        result = run.get('result') or {}
        result_at = next((at for seq,at,event in reversed(events) if event.get('type') == 'result'), None)
        value, observed, scope = result.get('usage'), result_at or run.get('updated') or run.get('created') or 0, 'NATIVE_CUMULATIVE'
        if not isinstance(value, dict) or not any(v is not None for k, v in normalized(value, run.get('partner_id')).items() if k in TOKEN_KEYS):
            value = None
            for seq, at, event in events:
                native = event.get('native') or {}
                update = _get(native, 'params', 'update') or {}
                cumulative = update.get('usage') if update.get('sessionUpdate') in (None, 'turn_completed') else None
                candidate = (cumulative or _get(native, '_meta', 'usage')
                             or _get(native, 'params', 'tokenUsage'))
                if isinstance(candidate, dict):
                    value, observed = candidate, at
        if isinstance(value, dict):
            if inside(observed):
                yield _record(run, account, value, observed, 'partner_native', run['id'] + ':native-summary', intents=len(intents), scope=scope)
                continue
            if observed < start:
                continue
        messages = {}
        for seq, at, event in events:
            native = event.get('native') or {}
            payload = _get(native, 'params', 'event') or {}
            data = payload.get('data') or {}
            if run.get('partner_id') == 'deepseek' and payload.get('type') == 'assistant/message' and isinstance(data.get('usage'), dict):
                identity = _get(data, 'message', 'id')
                if identity:
                    messages[identity] = (at, data['usage'], 'NATIVE_MESSAGE', _get(data, 'message', 'source', 'model'))
            update = _get(native, 'params', 'update') or {}
            if run.get('partner_id') == 'qwen' and update.get('sessionUpdate') == 'agent_message_chunk' and isinstance(_get(update, '_meta', 'usage'), dict):
                identity = update.get('messageId') or _get(update, '_meta', 'message_id')
                messages[identity or 'unidentified-last'] = (at, update['_meta']['usage'], 'NATIVE_MESSAGE' if identity else 'UNIDENTIFIED_NATIVE_MESSAGE', None)
        selected = [(identity, item) for identity, item in messages.items() if inside(item[0])]
        if selected:
            for index, (identity, (at, value, scope, model)) in enumerate(selected):
                yield _record(run, account, value, at, 'partner_native', run['id'] + ':native:' + identity,
                              intents=len(intents) if index == 0 else 0, scope=scope, model=model)
            continue
        if intents:
            for identity, (at, event) in intents.items():
                if inside(at):
                    yield _record(run, account, None, at, 'partner_request_unknown', run['id'] + ':' + identity, intents=1, scope='REQUEST_INTENT_ONLY')
        else:
            at = run.get('created') or observed
            if inside(at):
                yield _record(run, account, None, at, 'partner_native_unknown', run['id'] + ':unknown', scope='NO_USAGE_RECEIPT')


def model_rows(records):
    from .usage_report import add, empty, summary
    groups = {}
    for row in records:
        keys = ('provider', 'partner_id', 'api_provider', 'account_id', 'account_label', 'route', 'run_id', 'model')
        identity = tuple(row.get(key) for key in keys)
        group = groups.setdefault(identity, empty())
        add(group, row)
    return [{**dict(zip(keys, identity)), **summary(value)} for identity, value in groups.items()]


def work_report(db, identity=None, records=None):
    runs = {row['id']: row for row in objects(db, 'run') if row.get('mode') == 'run'}
    bounded = records is not None
    records = list(run_records(db, run_id=identity)) if records is None else records
    by_run = {}
    for row in records:
        by_run.setdefault(row.get('run_id'), []).append(row)
    groups = []
    for run in runs.values():
        if identity is not None and run['id'] != identity:
            continue
        if identity is None and run.get('parent_task_id'):
            continue
        rows = by_run.get(run['id'], [])
        if bounded and not rows:
            continue
        models = model_rows(rows)
        dimensions = {key: (rows[0].get(key) if rows else None) for key in ('partner_id', 'api_provider', 'account_id', 'account_label', 'route', 'run_id')}
        groups.append({'id': run['id'], 'kind': 'partner_run', 'title': str(run.get('prompt') or '伙伴工作')[:160],
                       'project_id': run.get('project_id'), 'project': run.get('project_id'), 'mode': 'independent',
                       'state': run.get('state'), 'updated': run.get('updated') or run.get('created') or 0,
                       **dimensions, 'models': models, 'steps': [{'id': run['id'], 'task_id': run['id'], **dimensions,
                       'title': '官方伙伴运行', 'started': run.get('created'), 'ended': run.get('updated') if run.get('local_exit_confirmed') else None,
                       'state': run.get('state'), 'reserved': None, 'models': models}] if identity is not None else []})
    return sorted(groups, key=lambda row: (-row['updated'], row['id']))


def account_overview(db, records):
    """Quota ownership cannot be borrowed from a legacy default connection."""
    partners, providers = catalog()
    accounts = objects(db, 'account')
    rows = []
    for partner in partners:
        for route in ('account', 'api'):
            selected = [account for account in accounts if account.get('partner_id') == partner['id'] and
                        ((route == 'account' and account.get('kind') == 'official_login') or
                         (route == 'api' and account.get('kind') in ('api', 'deepseek_api')))]
            if not selected:
                own = next((provider['id'] for provider in providers if provider.get('partner_id') == partner['id']), None)
                selected = [{'id': None, 'api_provider': own if route == 'api' else None, 'state': 'NOT_CONFIGURED'}]
            for account in selected:
                api = account.get('api_provider') or ('deepseek' if account.get('kind') == 'deepseek_api' else None)
                observed = [row for row in records if row.get('run_id') is not None and row.get('account_id') == account.get('id') and row.get('partner_id') == partner['id'] and row.get('route') == route and row.get('api_provider') == api]
                totals = {'input_tokens': None, 'output_tokens': None}
                for row in observed:
                    for key, source in (('input_tokens','input'), ('output_tokens','output')):
                        if row[source] is not None:
                            totals[key] = (totals[key] or 0) + row[source]
                rows.append({'provider': api or partner['id'], 'partner_id': partner['id'], 'api_provider': api,
                    'account_id': account.get('id'), 'account_label': account.get('label'), 'route': route,
                    'label': partner['name'], 'state': account.get('state'), 'kind': 'official_account' if route == 'account' else 'api',
                    'remaining': None, 'unit': None, 'buckets': [], 'balances': [], 'balance_available': None,
                    'official_at': None, 'manual_at': None, 'refresh_status': None, 'refresh': None, 'usage': totals,
                    'request_intents': sum(row.get('request_intents', 0) for row in observed),
                    'unknown_usage_records': sum(not row['usage_complete'] for row in observed)})
    return rows


def parent_budget_usage(db, attempt_ids,task_by_attempt=None):
    """Additive budget view; no mirrored request or usage rows are written."""
    runs={run['id']:run for run in objects(db,'run') if run.get('mode')=='run' and run.get('parent_attempt_id') in attempt_ids
          and (task_by_attempt is None or run.get('parent_task_id')==task_by_attempt[run['parent_attempt_id']])}
    rows=[row for row in run_records(db,parent_attempt_ids=attempt_ids) if row['run_id'] in runs]
    rows=[row for row in rows if not (runs[row['run_id']].get('state') in {'FAILED_NO_SEND','CANCELLED'}
          and row.get('request_intents',0)==0 and not row.get('has_request_receipt') and row['tokens'] is None)]
    values=lambda key:[row[key] for row in rows if row[key] is not None]
    estimates=[Decimal(row['estimate']['amount']) for row in rows if row.get('estimate')]
    unknown_charge=sum(not row.get('estimate') or not row.get('usage_complete') for row in rows)
    pending=sum(run.get('state') not in {'COMPLETED','FAILED','FAILED_NO_SEND','CANCELLED','STOPPED_RUNTIME'} for run in runs.values())
    return {'model_requests':sum(max(row.get('request_intents',0),int(row.get('has_request_receipt') is True)) for row in rows),
            'request_intents':sum(row.get('request_intents',0) for row in rows),
            'api_known_input_tokens':sum(values('input')),'api_known_output_tokens':sum(values('output')),
            'api_requests_without_token_receipt':sum(row['tokens'] is None for row in rows),
            'unknown_usage_records':sum(not row.get('usage_complete') for row in rows),
            'unknown_requests':pending,'unknown_charge_records':unknown_charge,
            'known_estimated_cny':float(sum(estimates,Decimal(0))),
            'partner_run_ids':list(runs),'accounting_source':'CORE_PARTNER_RECEIPTS; INTENTS_ARE_NOT_BILLED_CALLS'}
