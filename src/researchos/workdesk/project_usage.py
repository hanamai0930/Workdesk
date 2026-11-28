"""Project accounting built from the original execution and provider receipts."""
from collections import Counter
from decimal import Decimal
import json
import time

from . import bridge
from .deepseek_pricing import RATE_BASIS
from .partner_usage import run_records
from .usage_report import TOKEN_KEYS, codex_records, gateway_records, sdk_records


def check_native_budget(db, task):
    # A native partner has a separate Go-owned run. It reserves exactly once there.
    if bridge.enabled() and task['spec'].get('execution') != 'partner_native':
        bridge.call('db.check_attempt_budget', {'session': db.session, 'project_id': task['project_id']})


def report_project(store, args):
    project = args.get('project_id', [''])[0]
    days = args.get('days', ['30'])[0]
    if str(days) not in {'1', '7', '30', '90', '365'}:
        raise ValueError('用量区间可选 1、7、30、90 或 365 天。')
    page = args.get('page', ['1'])[0]
    if not str(page).isdigit() or not 1 <= int(page) <= 1000000:
        raise ValueError('用量页码无效。')
    page = int(page)
    end = time.time()
    start = end - int(days) * 86400
    filters = {k: args[k][0] for k in ('task_id','attempt_id','tool_version','partner_id','account_id') if args.get(k)}
    rows = []
    seen = set()
    def add(row):
        if any(row.get(k) != value for k, value in filters.items()):
            return
        identity = row['source'], row['id']
        if identity not in seen:
            seen.add(identity)
            rows.append(row)

    with store.read() as db:
        if not db.execute('SELECT 1 FROM projects WHERE id=?', (project,)).fetchone():
            raise ValueError('未找到用量项目。')
        tasks = {r[0] for r in db.execute('SELECT id FROM tasks WHERE project_id=?', (project,))}
        measured_attempts = set()
        for reader in (gateway_records, sdk_records, codex_records):
            for row in reader(db, start, end):
                if row.get('task_id') in tasks:
                    measured_attempts.add(row.get('attempt_id'))
                    add({**row, 'project_id': project, 'meter': 'PROVIDER_RECEIPT'})
        for row in run_records(db, start, end, project_id=project):
            add({**row, 'project_id': project, 'meter': 'PROVIDER_RECEIPT'})
        for row in db.execute("SELECT a.id,a.task_id,a.started,a.state,t.spec FROM attempts a JOIN tasks t ON t.id=a.task_id "
                              "WHERE t.project_id=? AND a.started>=? AND a.started<? "
                              "AND coalesce(json_extract(t.spec,'$.execution'),'')!='partner_native'", (project,start,end)):
            if row['id'] not in measured_attempts:
                spec = json.loads(row['spec'])
                add({'id': row['id'], 'source':'attempt_without_receipt','project_id':project,'task_id':row['task_id'],
                     'attempt_id':row['id'],'at':row['started'],'status':row['state'],'provider':spec.get('provider'),
                     'meter':'NO_USAGE_RECEIPT', **dict.fromkeys(TOKEN_KEYS), 'estimate':None})
        for row in db.execute("SELECT id,state,data FROM core_tool_jobs WHERE project_id=? "
                              "AND json_extract(data,'$.created')>=? AND json_extract(data,'$.created')<?", (project,start,end)):
            job=json.loads(row['data'])
            external=job.get('action')=='terminal.run'
            done=row['state'] in ('COMPLETED','CANCELLED','FAILED')
            add({'id':row['id'],'source':'local_tool','project_id':project,'task_id':job.get('task_id'),
                 'attempt_id':job.get('attempt_id'),'tool_version':job.get('tool_version'),'action':job.get('action'),
                 'account_id':None,'schedule_id':job.get('schedule_id'),'round_id':job.get('round_id'),
                 'at':job['created'],'observed_at':job.get('updated'),'status':row['state'],
                 'elapsed_seconds':max(0,job['updated']-job['created']) if done else None,
                 'elapsed_basis':'JOB_ADMISSION_TO_TERMINAL' if done else None,
                 'meter':'UNPRICED_EXECUTION' if external else 'LOCAL_NO_MODEL',
                 **dict.fromkeys(TOKEN_KEYS, None if external else 0), 'estimate':None,
                 'model_cost_cny':None if external else '0'})
    rows.sort(key=lambda row:(row['at'],row['id']),reverse=True)
    model_fields = {key:{'known':sum(r[key] for r in rows if r.get(key) is not None),
                         'known_records':sum(r.get(key) is not None for r in rows),
                         'unknown_records':sum(r.get(key) is None for r in rows)} for key in TOKEN_KEYS}
    estimates=[r['estimate'] for r in rows if r.get('estimate')]
    cost_types=Counter('known' if r.get('model_cost_cny') is not None else
                       'estimated' if r.get('estimate') else 'unknown' for r in rows)
    dimensions = ('task_id','attempt_id','tool_version','partner_id','account_id')
    group_by = args.get('group_by',['tool_version'])[0]
    if group_by not in dimensions:
        raise ValueError('不支持此用量分组。')
    grouped = {}
    for row in rows:
        key = row.get(group_by)
        values = grouped.setdefault(key, {'id':key,'records':0,'known_tokens':0,'unknown_token_records':0})
        values['records'] += 1
        if row.get('tokens') is None:
            values['unknown_token_records'] += 1
        else:
            values['known_tokens'] += row['tokens']
    offset=(page-1)*100
    return {'project_id':project, 'from':start,'to':end,'observed_at':end,'days':int(days),'filters':filters,
            'record_count':len(rows),'tokens':model_fields,'cost':{
                'known_model_cny':'0' if cost_types['known'] else None,'known_records':cost_types['known'],
                'estimated_cny':format(sum((Decimal(e['amount']) for e in estimates),Decimal(0)),'f') if estimates else None,
                'estimated_records':cost_types['estimated'],'unknown_records':cost_types['unknown'],
                'currency':'CNY','price_basis':RATE_BASIS},'rows':rows[offset:offset+100], 'page':page,
            'group_by':group_by,'groups':sorted(grouped.values(),key=lambda g:(-g['records'],g['id'] or '')),
            'has_more':len(rows)>offset+100,'source_quota_route':'/api/v1/settings-usage'}
