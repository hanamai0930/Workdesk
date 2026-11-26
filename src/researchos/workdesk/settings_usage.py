"""Read saved Work usage for the desktop summary."""
import time

from .usage_report import period_records
from .partner_usage import account_overview, catalog as partner_catalog, work_report as partner_work_report

PROVIDERS = {'codex': 'Codex', 'deepseek': 'DeepSeek', 'qwen': 'Qwen'}


def model_summaries(records, partners, providers):
    """Compact display over already deduplicated receipts, across accounts/runs."""
    names = {row['id']: row['name'] for row in (*partners, *providers)}
    groups = {}
    for row in records:
        provider = row.get('api_provider') or row['provider']
        route = 'api' if row.get('api_provider') else 'account' if row.get('run_id') else 'legacy'
        model = row.get('model') or '未知型号'
        # An absent model identity cannot establish that two partners used the
        # same model. Keep those observations separate until it is reported.
        unknown = row.get('partner_id') if model == '未知型号' else None
        identity = (provider, route, model, unknown)
        name = names.get(provider, provider)
        source = name + (' API' if route == 'api' else ' 官方账号' if route == 'account' else ' 历史连接')
        group = groups.setdefault(identity, {
            'id': ':'.join(str(value or '') for value in identity), 'provider': provider,
            'model': model, 'source_label': source, 'tokens': None, 'partial': False,
        })
        if row.get('tokens') is not None:
            group['tokens'] = (group['tokens'] or 0) + row['tokens']
        group['partial'] |= row.get('tokens') is None or row.get('usage_complete') is False
    return list(groups.values())


def _quotas(service, resources, usage):
    from .quota_observations import qwen_cli

    sources = {row['id']: row for row in resources['quota_sources']}
    connections = {row['provider']: row for row in resources['connections'] if row['is_default']}
    for provider, label in PROVIDERS.items():
        connection = connections.get(provider, {})
        source = sources.get(connection.get('quota_id'), {})
        observation = next((row for row in source.get('official_observations', [])
                            if row['connection_id'] == connection['id'] and row['binding_status'] == 'MATCH'), {})
        official = (observation.get('last_success') or {}
                    if observation and observation['last_success_connection_revision'] == connection['revision'] else {})
        refresh = ({'connection_id': connection['id'], 'connection_revision': connection['revision']}
                   if connection and service.resources.quota_readable(connection) else None)
        yield {'provider': provider, 'label': label, 'kind': source.get('kind'),
               'legacy': True, 'partner_id': provider, 'api_provider': None, 'account_id': None, 'account_label': None, 'route': None,
               'remaining': source.get('remaining'), 'unit': source.get('unit'),
               'official_at': official.get('observed_at'),
               'manual_at': source.get('observed_at'),
               'buckets': official.get('buckets', []), 'balances': official.get('balances', []),
               'balance_available': official.get('is_available'),
               'refresh_status': observation.get('status'), 'refresh': refresh,
               **({'console_sources': [kind for kind in ('free','cash') if qwen_cli(service.locations, kind)],
                   'console_snapshots': list(service.resources.qwen_quota_snapshots.copy().values())}
                  if provider=='qwen' else {}),
               'usage': usage.get(provider, {'input_tokens': None, 'output_tokens': None})}


def overview(service, days=7):
    if days not in (7, 30, '7', '30'):
        raise ValueError('统计周期只能选择近 7 天或近 30 天。')
    days = int(days)
    end = time.time()
    start = end - days * 86400
    visible = service.work_records.visible('task', 't.id')
    with service.store.read() as db:
        # Read exact request receipts, plus Qwen's final per-attempt tally when
        # the run used its direct SDK and did not write to the request ledger.
        records = db.execute(f"""
            WITH ledger_attempts AS (SELECT DISTINCT attempt_id FROM requests), coverage AS (
                SELECT r.task_id, r.created AS activity_at, 0 AS attempts, r.reserved_cny,
                    CASE WHEN json_type(r.usage,'$.prompt_tokens')='integer'
                        THEN json_extract(r.usage,'$.prompt_tokens') END AS input_tokens,
                    CASE WHEN json_type(r.usage,'$.completion_tokens')='integer'
                        THEN json_extract(r.usage,'$.completion_tokens') END AS output_tokens
                FROM requests r WHERE r.created BETWEEN ? AND ?
                UNION ALL
                SELECT a.task_id, a.started, 1, NULL,
                    CASE WHEN json_extract(t.spec,'$.provider') = 'qwen'
                        AND ledger.attempt_id IS NULL
                        AND json_type(a.result,'$.protocol_result.usage.input_tokens')='integer'
                        THEN json_extract(a.result,'$.protocol_result.usage.input_tokens') END,
                    CASE WHEN json_extract(t.spec,'$.provider') = 'qwen'
                        AND ledger.attempt_id IS NULL
                        AND json_type(a.result,'$.protocol_result.usage.output_tokens')='integer'
                        THEN json_extract(a.result,'$.protocol_result.usage.output_tokens') END
                FROM attempts a JOIN tasks t ON t.id=a.task_id
                LEFT JOIN ledger_attempts ledger ON ledger.attempt_id=a.id
                WHERE a.started BETWEEN ? AND ?
            )
            SELECT t.id, t.title, json_extract(t.spec,'$.provider') AS provider,
                {visible} AS visible, MAX(c.activity_at) AS activity_at,
                SUM(c.attempts) AS attempts, SUM(c.reserved_cny) AS reserved_cny,
                SUM(CASE WHEN typeof(c.input_tokens)='integer' AND c.input_tokens>=0 THEN c.input_tokens END) AS input_tokens,
                SUM(CASE WHEN typeof(c.output_tokens)='integer' AND c.output_tokens>=0 THEN c.output_tokens END) AS output_tokens,
                json_extract(t.spec,'$.budget.cny') AS budget_cny
            FROM coverage c JOIN tasks t ON t.id=c.task_id
            WHERE coalesce(json_extract(t.spec,'$.interaction'),'')!='chat'
            GROUP BY t.id ORDER BY activity_at DESC, t.id
        """, (start, end, start, end)).fetchall()
        usage = {}
        receipts = list(period_records(db, start, end))
        records=[dict(row) for row in records]
        by_task={row['id']:row for row in records}
        parent_receipts={}
        for row in receipts:
            if row.get('parent_task_id'):parent_receipts.setdefault(row['parent_task_id'],[]).append(row)
        for task_id,observed in parent_receipts.items():
            task=by_task.get(task_id)
            if task is None:
                original=db.execute(f"SELECT t.id,t.title,json_extract(t.spec,'$.provider') AS provider,{visible} AS visible,json_extract(t.spec,'$.budget.cny') AS budget_cny FROM tasks t WHERE t.id=?",(task_id,)).fetchone()
                if original is None:continue
                task=dict(original);records.append(task);by_task[task_id]=task
            numbers=lambda key:[row[key] for row in observed if row[key] is not None]
            priced=[row['estimate']['amount'] for row in observed if row.get('estimate') and row.get('usage_complete')]
            task.update(activity_at=max(row['at'] for row in observed),attempts=len({row['attempt_id'] for row in observed}),
                        input_tokens=sum(numbers('input')) if numbers('input') else None,
                        output_tokens=sum(numbers('output')) if numbers('output') else None,
                        reserved_cny=sum(float(value) for value in priced) if len(priced)==len(observed) else None,
                        partner_run_ids=list(dict.fromkeys(row['run_id'] for row in observed)))
        for row in receipts:
            if row.get('run_id') is not None:
                continue
            total = usage.setdefault(row['provider'], {'input_tokens': None, 'output_tokens': None})
            for key, source in (('input_tokens','input'), ('output_tokens','output')):
                if row[source] is not None:
                    total[key] = (total[key] or 0) + row[source]
        quotas = list(_quotas(service, service.resources.catalog(db=db), usage)) + account_overview(db, receipts)
        partner_tasks = []
        for group in partner_work_report(db, records=receipts):
            observed = [row for row in receipts if row.get('run_id') == group['id']]
            if not observed:
                continue
            values = lambda key: [row[key] for row in observed if row[key] is not None]
            partner_tasks.append({**group, 'activity_at': max(row['at'] for row in observed), 'attempts': 1,
                'input_tokens': sum(values('input')) if values('input') else None,
                'output_tokens': sum(values('output')) if values('output') else None,
                'reserved_cny': None, 'budget_cny': None})

    totals = {}
    for key in ('input_tokens', 'output_tokens', 'reserved_cny'):
        values = [row[key] for row in (*records,*partner_tasks) if row[key] is not None]
        totals[key] = sum(values) if values else None

    # Removed records retain their accounting, without reappearing as links.
    rows = [dict(row) for row in records if row['visible']] + partner_tasks
    rows.sort(key=lambda row: (-row['activity_at'], row['id']))
    partners, api_providers = partner_catalog()
    return {'days': days, 'partners': partners, 'api_providers': api_providers,
            'model_summaries': model_summaries(receipts, partners, api_providers),
            'totals': totals, 'tasks': rows[:6], 'task_count': len(rows), 'quotas': quotas}
