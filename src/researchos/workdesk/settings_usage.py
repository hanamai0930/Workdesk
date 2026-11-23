"""Read saved Work usage for the desktop summary."""
import time

from .usage_report import period_records

PROVIDERS = {'codex': 'Codex', 'deepseek': 'DeepSeek', 'qwen': 'Qwen'}


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
        for row in period_records(db, start, end):
            total = usage.setdefault(row['provider'], {'input_tokens': None, 'output_tokens': None})
            for key, source in (('input_tokens','input'), ('output_tokens','output')):
                if row[source] is not None:
                    total[key] = (total[key] or 0) + row[source]
        quotas = list(_quotas(service, service.resources.catalog(db=db), usage))

    totals = {}
    for key in ('input_tokens', 'output_tokens', 'reserved_cny'):
        values = [row[key] for row in records if row[key] is not None]
        totals[key] = sum(values) if values else None

    # Removed records retain their accounting, without reappearing as links.
    rows = [dict(row) for row in records if row['visible']]
    return {'days': days, 'totals': totals, 'tasks': rows[:6], 'task_count': len(rows), 'quotas': quotas}
