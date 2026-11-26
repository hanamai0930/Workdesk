"""Token and call reporting from persisted Workdesk receipts."""

from collections import defaultdict
from datetime import datetime, timedelta
from decimal import Decimal
import json

from .deepseek_pricing import LOCAL, RATE_BASIS, estimate
from .partner_usage import catalog as partner_catalog

PROVIDERS = tuple(dict.fromkeys(row['id'] for group in partner_catalog() for row in group))
TOKEN_KEYS = ('input', 'cache_hit', 'cache_miss', 'output', 'tokens')
PAGE_SIZE = 40


def count(value):
    return value if type(value) is int and 0 <= value <= 2**53 - 1 else None


def usage(value):
    """OpenAI-compatible input tokens include the cached portion."""
    value = value if isinstance(value, dict) else {}
    incoming, output = count(value.get('prompt_tokens')), count(value.get('completion_tokens'))
    hit, miss = count(value.get('prompt_cache_hit_tokens')), count(value.get('prompt_cache_miss_tokens'))
    if hit is None:
        details = value.get('prompt_tokens_details')
        if isinstance(details, dict):
            hit = count(details.get('cached_tokens'))
    return split(incoming, output, hit, miss, count(value.get('total_tokens')))


def split(incoming, output, hit, miss=None, total=None):
    if incoming is not None:
        if hit is not None and miss is None and hit <= incoming:
            miss = incoming - hit
        if hit is None and miss is not None and miss <= incoming:
            hit = incoming - miss
        if ((hit is not None and hit > incoming) or (miss is not None and miss > incoming)
                or (hit is not None and miss is not None and hit + miss != incoming)):
            hit = miss = None
    if incoming is not None and output is not None:
        total = incoming + output
    return dict(zip(TOKEN_KEYS, (incoming, hit, miss, output, total)))


def qwen_usage(value):
    value = value if isinstance(value, dict) else {}
    return split(count(value.get('input_tokens')), count(value.get('output_tokens')),
                 count(value.get('cache_read_input_tokens')), total=count(value.get('total_tokens')))


def deepseek_usage(value):
    miss, hit, written, output, total = (count(value.get(key)) for key in
        ('inputTokens', 'cacheReadTokens', 'cacheWriteTokens', 'outputTokens', 'totalTokens'))
    # The Harness' Messages usage reports uncached input separately. Some older
    # snapshots omit cache-write counts. Only infer that component from totals
    # when all other counts are reported, so absent data does not become zero.
    if miss is not None and hit is not None and output is not None:
        if written is None and total is not None and total >= hit + miss + output:
            written = total - hit - miss - output
        if written is not None:
            return split(hit + miss + written, output, hit, miss + written, total)
    return split(total - output if total is not None and output is not None and total >= output else None,
                 output, None, total=total)


def record(provider, model, source, at, status, numbers, *, time_basis=None, **metadata):
    return {'provider':provider, 'model':model or '未知型号', 'source':source, 'at':at, 'status':status,
            **numbers, 'estimate':estimate(provider, model, numbers, at, time_basis) if source in ('api_chat','gateway') else None,
            'partner_id': provider, 'api_provider': provider if source in ('api_chat','gateway') else None,
            'account_id': None, 'account_label': None, 'route': 'api' if source in ('api_chat','gateway') else 'account', 'run_id': None,
            **metadata}


def chat_records(db, start, end):
    for row in db.execute("""
        WITH receipts AS (
            SELECT data AS value FROM collaboration WHERE kind='ui_chat_turn'
            UNION ALL
            SELECT item.value FROM collaboration, json_each(data,'$.usage') AS item WHERE kind='ui_chat_deleted'
        )
        SELECT json_extract(value,'$.provider') AS provider, json_extract(value,'$.id') AS id,
            json_extract(value,'$.state') AS state, json_extract(value,'$.usage') AS usage,
            coalesce(json_extract(value,'$.config.model'),json_extract(value,'$.model')) AS model,
            json_extract(value,'$.created') AS created, json_extract(value,'$.model_requested_at') AS sent,
            json_extract(value,'$.provider_requested_at') AS official_at,
            json_extract(value,'$.search_requested_at') AS searched, json_extract(value,'$.search_state') AS search_state
        FROM receipts
        WHERE (coalesce(official_at,sent,created)>=? AND coalesce(official_at,sent,created)<?)
           OR (searched>=? AND searched<?)
    """, (start,end,start,end)):
        provider = row['provider'] or ('qwen' if (row['model'] or '').startswith('qwen') else 'deepseek')
        numbers = usage(json.loads(row['usage']) if row['usage'] else None)
        if row['sent'] is not None or row['official_at'] is not None or any(numbers[key] is not None for key in TOKEN_KEYS):
            at = row['official_at'] or row['sent'] or row['created']
            basis = 'provider_created' if row['official_at'] else 'request_sent' if row['sent'] else 'turn_created'
            yield record(provider,row['model'],'api_chat',at,row['state'],numbers,id=row['id'],time_basis=basis)
        if row['searched'] is not None or row['search_state'] in ('REQUEST_INTENT','EMPTY','COMPLETED'):
            settled = row['search_state'] in ('EMPTY','COMPLETED')
            yield record('deepseek','联网搜索','search',row['searched'] or row['created'],
                         'COMPLETED' if settled else 'SEND_UNKNOWN',usage(None),id=row['id']+':search')


def gateway_records(db,start,end):
    for row in db.execute("""
        SELECT r.id,r.task_id,r.attempt_id,r.provider,r.created,r.state,r.usage,coalesce(
            (SELECT json_extract(e.payload,'$.requested_model') FROM events e
             WHERE e.task_id=r.task_id AND e.attempt_id=r.attempt_id AND e.kind='model_wire_configuration_intent'
               AND json_extract(e.payload,'$.request_id')=r.id ORDER BY e.seq LIMIT 1),
            json_extract(a.result,'$.model'),json_extract(t.spec,'$.model')) AS model
        FROM requests r JOIN tasks t ON t.id=r.task_id JOIN attempts a ON a.id=r.attempt_id
        WHERE r.created>=? AND r.created<?
    """, (start,end)):
        yield record(row['provider'],row['model'],'gateway',row['created'],row['state'],
                     usage(json.loads(row['usage']) if row['usage'] else None),id=row['id'],time_basis='request_created',
                     task_id=row['task_id'],attempt_id=row['attempt_id'])


def sdk_records(db,start,end):
    for row in db.execute("""
        SELECT a.id,a.task_id,a.started,a.state,json_extract(a.result,'$.protocol_result.usage') AS usage,
            coalesce((SELECT json_extract(e.payload,'$.configuration_snapshot.model') FROM events e
                      WHERE e.task_id=t.id AND e.attempt_id=a.id AND e.kind='execution_reserved'
                      ORDER BY e.seq LIMIT 1),json_extract(t.spec,'$.model')) AS model
        FROM attempts a JOIN tasks t ON t.id=a.task_id
        WHERE a.started>=? AND a.started<? AND json_extract(t.spec,'$.provider')='qwen'
          AND json_type(a.result,'$.protocol_result.usage')='object'
          AND NOT EXISTS (SELECT 1 FROM requests WHERE attempt_id=a.id)
    """,(start,end)):
        yield record('qwen',row['model'],'qwen_native',row['started'],row['state'],
                     qwen_usage(json.loads(row['usage'])),id=row['id'],task_id=row['task_id'],attempt_id=row['id'])
    seen = set()
    for row in db.execute("""
        SELECT e.seq,e.at,e.task_id,e.attempt_id,json_extract(e.payload,'$.payload.sessionId') AS session,
            json_extract(e.payload,'$.payload.event.data.message.id') AS message,
            json_extract(e.payload,'$.payload.event.data.usage') AS usage,
            json_extract(e.payload,'$.payload.event.data.message.source.model') AS served_model,
            json_extract(t.spec,'$.model') AS model
        FROM attempts a JOIN tasks t ON t.id=a.task_id
        JOIN events e ON e.task_id=t.id AND e.attempt_id=a.id
        WHERE e.at<? AND json_extract(t.spec,'$.provider')='deepseek'
          AND json_extract(t.spec,'$.runtime_mode')='official' AND e.kind='model_event'
          AND json_extract(e.payload,'$.payload.event.type')='assistant/message'
          AND json_type(e.payload,'$.payload.event.data.usage')='object'
        ORDER BY e.seq
    """,(end,)):
        key = (row['session'],row['message'])
        if not all(key) or key in seen:
            continue
        seen.add(key)
        if row['at'] < start:
            continue
        model = row['served_model'] or row['model']
        yield record('deepseek',model,'deepseek_native',row['at'],'COMPLETE',
                     deepseek_usage(json.loads(row['usage'])),id='dsh:'+str(row['seq']),time_basis='response_observed',
                     task_id=row['task_id'],attempt_id=row['attempt_id'])


def codex_vector(value):
    if not isinstance(value,dict):
        return None
    result=tuple(count(value.get(key)) for key in ('inputTokens','cachedInputTokens','outputTokens'))
    return result if None not in result and result[1] <= result[0] else None


def codex_records(db,start,end):
    observations=defaultdict(list)
    for row in db.execute("""
        SELECT a.id,a.task_id,a.session_id,a.started,a.state,e.kind,e.at,
            json_extract(e.payload,'$.total') AS total,json_extract(e.payload,'$.last') AS last,
            coalesce((SELECT json_extract(r.payload,'$.configuration_snapshot.model') FROM events r
                      WHERE r.task_id=t.id AND r.attempt_id=a.id AND r.kind='execution_reserved'
                      ORDER BY r.seq LIMIT 1),json_extract(t.spec,'$.model')) AS model
        FROM attempts a JOIN tasks t ON t.id=a.task_id
        JOIN events e ON e.task_id=t.id AND e.attempt_id=a.id
        WHERE a.started<? AND json_extract(t.spec,'$.provider')='codex' AND a.session_id IS NOT NULL
          AND e.kind IN ('turn_send_intent','native_token_usage_observation')
        ORDER BY a.started,a.id,e.seq
    """,(end,)):
        observations[row['id']].append(row)
    previous={}
    for identity, events in observations.items():
        head=events[0]
        prior=previous.get(head['session_id'])
        intent=None
        initial=None
        after=[]
        for event in events:
            if event['kind']=='turn_send_intent':
                intent=event['at']
                continue
            total=codex_vector(json.loads(event['total']) if event['total'] else None)
            if total is None:
                continue
            if intent is None:
                initial=total
            else:
                latest=codex_vector(json.loads(event['last']) if event['last'] else None)
                after.append((total,latest))
        if not after:
            continue
        first,recent=after[0]
        final=after[-1][0]
        previous[head['session_id']]=final
        if not start <= intent < end:
            continue
        origin=initial if initial is not None else prior
        if origin is None and first==recent:
            origin=(0,0,0)
        # A comparison across local executions needs corroboration from the
        # first observed request. Otherwise count only increases during this run.
        if origin is None or (first!=origin and
                (recent is None or tuple(n-p for n,p in zip(first,origin))!=recent)):
            origin=first
            if final==first:
                continue
        incoming,hit,output=(n-p for n,p in zip(final,origin))
        if min(incoming,hit,output)<0 or hit>incoming or incoming==output==0:
            continue
        yield record('codex',head['model'],'codex_native',intent,head['state'],
                     split(incoming,output,hit),id=identity,task_id=head['task_id'],attempt_id=identity)


def empty():
    return {**dict.fromkeys(TOKEN_KEYS), 'calls':0, 'native':0, 'records':0,
            'cache_receipts':0, 'estimated_records':0, 'amount':Decimal(0),
            'request_intents':0, 'unknown_usage_records':0}


def add(target,row):
    for key in TOKEN_KEYS:
        if row[key] is not None:
            target[key]=(target[key] or 0)+row[key]
    target['records']+=1
    if row['source'] in ('api_chat','gateway','search'):
        target['calls']+=1
    elif row['source'] == 'partner_gateway':
        target['calls']+=int(row.get('has_request_receipt') is True)
    elif row['source'] != 'partner_request_unknown':
        target['native']+=1
    target['request_intents']+=row.get('request_intents',0)
    target['unknown_usage_records']+=int(row.get('usage_complete') is False or row['tokens'] is None)
    if row['cache_hit'] is not None and row['cache_miss'] is not None:
        target['cache_receipts']+=1
    if row['estimate']:
        target['estimated_records']+=1
        target['amount']+=Decimal(row['estimate']['amount'])


def summary(value):
    amount = format(value['amount'], 'f') if value['estimated_records'] else None
    return {**value, 'amount':amount, 'low':amount, 'high':amount,
            'price_complete':value['records'] > 0 and value['estimated_records']==value['records']}


def period_records(db, start, end, provider='all', partner_id='all', api_provider='all', account_id='all', route='all'):
    readers = [chat_records, gateway_records]
    if provider in ('all', 'deepseek', 'qwen'):
        readers.append(sdk_records)
    if provider in ('all', 'codex'):
        readers.append(codex_records)
    for read in readers:
        for row in read(db, start, end):
            if (row['provider'] in PROVIDERS and (provider == 'all' or row['provider'] == provider)
                    and (partner_id == 'all' or row.get('partner_id') == partner_id)
                    and (api_provider == 'all' or row.get('api_provider') == api_provider)
                    and (account_id == 'all' or row.get('account_id') == account_id)
                    and (route == 'all' or row.get('route') == route)
                    and start <= row['at'] < end):
                yield row
    from .partner_usage import run_records
    for row in run_records(db,start,end,partner_id=partner_id,api_provider=api_provider,account_id=account_id,route=route):
        if provider == 'all' or row['provider'] == provider:
            yield row


def report(service,provider='all',days='365',page='1',partner_id='all',api_provider='all',account_id='all',route='all'):
    if provider not in ('all',*PROVIDERS) or str(days) not in ('7','30','90','365'):
        raise ValueError('请选择支持的厂商和日期范围。')
    if not str(page).isdigit() or not 1<=int(page)<=10000:
        raise ValueError('页码无效。')
    partners, api_providers = partner_catalog()
    if partner_id not in ('all',*(row['id'] for row in partners)) or api_provider not in ('all',*(row['id'] for row in api_providers)) or route not in ('all','api','account'):
        raise ValueError('请选择支持的伙伴、API 厂商和接入方式。')
    days,page=int(days),int(page)
    from .network_time import pricing_now
    end=datetime.fromtimestamp(pricing_now(),LOCAL)
    start=end.replace(hour=0,minute=0,second=0,microsecond=0)-timedelta(days=days-1)
    total=empty()
    by_day=defaultdict(empty)
    rows=[]
    with service.store.read() as db:
        for row in period_records(db, start.timestamp(), end.timestamp(), provider,partner_id,api_provider,account_id,route):
            day=datetime.fromtimestamp(row['at'],LOCAL).date().isoformat()
            add(total,row)
            add(by_day[day],row)
            rows.append(row)
    rows.sort(key=lambda row:(row['at'],row['id']),reverse=True)
    from .partner_usage import model_rows
    return {'rate_basis': RATE_BASIS, 'provider':provider,'partner_id':partner_id,'api_provider':api_provider,'account_id':account_id,'route':route,
            'calls_basis':'RECORDED_REQUEST_RECEIPTS_NOT_BILLED_COUNT',
            'partners':partners,'api_providers':api_providers,'days':days,'page':page,'page_size':PAGE_SIZE,'count':len(rows),
            'date_first':start.date().isoformat(),'date_last':end.date().isoformat(),
            'totals':summary(total),
            'daily':[{'date':day,**summary(value)} for day,value in sorted(by_day.items())],
            'models':model_rows(rows),
            'rows':rows[(page-1)*PAGE_SIZE:page*PAGE_SIZE]}
