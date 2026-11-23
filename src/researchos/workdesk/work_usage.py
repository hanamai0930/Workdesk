"""Accounting by business task and recorded execution step."""

from collections import defaultdict
import json
import time

from .usage_report import add, codex_records, empty, gateway_records, sdk_records, summary


def model_rows(models):
    return [{'provider': provider, 'model': model, **summary(totals)}
            for (provider, model), totals in models.items()]


def report(service, identity=None):
    item_visible = service.work_records.visible('item', 'i.id')
    task_visible = service.work_records.visible('task', 't.id')
    dialogue_visible = service.work_records.visible('dialogue', 'd.id')
    with service.store.read() as db:
        # Frozen unit ownership or an unambiguous dispatch binds the costs.
        # Manually linked references can belong to a different business task.
        rows = db.execute(f"""
            WITH dispatches AS (
                SELECT json_extract(data,'$.task_id') AS task_id,
                    min(json_extract(data,'$.item_id')) AS item_id
                FROM collaboration WHERE kind='work_dispatch' GROUP BY task_id
                HAVING count(DISTINCT json_extract(data,'$.item_id'))=1
            ), owned AS (
                SELECT t.id,title,project_id,updated,
                    coalesce(json_extract(spec,'$.cooperative.item_id'),dispatches.item_id) AS item_id,
                    json_extract(spec,'$.cooperative.plan_id') AS plan_id,
                    json_extract(spec,'$.cooperative.unit_key') AS unit_key,
                    json_extract(spec,'$.cooperative.discussion') AS discussion,
                    json_extract(spec,'$.cooperative.preparation_id') AS preparation_id,
                    json_extract(spec,'$.work_mode') AS work_mode,
                    json_extract(spec,'$.provider') AS provider,json_extract(spec,'$.model') AS model
                FROM tasks AS t LEFT JOIN dispatches ON dispatches.task_id=t.id
                WHERE coalesce(json_extract(spec,'$.interaction'),'')!='chat'
            )
            SELECT t.*,coalesce(i.id,d.id,t.id) AS group_id,
                coalesce(nullif(json_extract(display.data,'$.title'),''),i.title,json_extract(d.data,'$.title'),t.title) AS group_title,
                p.name AS project,COALESCE(i.updated,t.updated) AS group_updated,
                json_extract(conversation.data,'$.mode') AS conversation_mode
            FROM owned t LEFT JOIN work_items i ON i.id=t.item_id
            LEFT JOIN collaboration conversation ON conversation.kind='work_cooperation_conversation'
                AND conversation.id='cooperation-conversation:'||i.id
            LEFT JOIN collaboration link ON link.id=t.id AND link.kind='link'
                AND json_extract(link.data,'$.kind')='dialogue'
            LEFT JOIN collaboration d ON d.id=json_extract(link.data,'$.dialogue_id') AND d.kind='dialogue'
            LEFT JOIN collaboration display ON display.kind='display' AND display.id=CASE
                WHEN i.id IS NOT NULL THEN 'display:item:'||i.id
                WHEN d.id IS NOT NULL THEN 'display:dialogue:'||d.id ELSE 'display:task:'||t.id END
            LEFT JOIN projects p ON p.id=coalesce(i.project_id,json_extract(d.data,'$.project_id'),t.project_id)
            WHERE (i.id IS NULL OR {item_visible})
              AND (i.id IS NOT NULL OR (d.id IS NOT NULL AND {dialogue_visible})
                   OR (d.id IS NULL AND {task_visible}))
              AND (EXISTS (SELECT 1 FROM attempts WHERE task_id=t.id) OR :identity IN (t.id,i.id,d.id))
            ORDER BY t.updated DESC,t.id
        """, {'identity': identity}).fetchall()
        if identity is not None:
            selected = next((row['group_id'] for row in rows if identity in (row['id'],row['group_id'])),None)
            if selected is None:
                item = db.execute(f"""
                    SELECT i.id,i.title,i.updated,p.name AS project,
                           json_extract(c.data,'$.mode') AS mode
                    FROM work_items i LEFT JOIN projects p ON p.id=i.project_id
                    LEFT JOIN collaboration c ON c.id='cooperation-conversation:'||i.id
                         AND c.kind='work_cooperation_conversation'
                    WHERE i.id=? AND {item_visible}
                """, (identity,)).fetchone()
                if not item:
                    raise ValueError('找不到这项工作的用量记录。')
                return {'task': {**dict(item), 'models': [], 'steps': []}}
            rows = [row for row in rows if row['group_id']==selected]
        if not rows:
            return {'tasks': []}

        plans = {}
        for row in db.execute("SELECT id,data FROM collaboration WHERE kind='teamwork_plan'"):
            plan = json.loads(row['data'])
            plans[row['id']] = (plan.get('mode'), {unit['key']:unit['title'] for unit in plan.get('units',[])})
        tasks = {row['id']: row for row in rows}
        groups = {}
        for row in rows:
            plan_mode = plans.get(row['plan_id'],(None,{}))[0]
            group = groups.setdefault(row['group_id'], {'id':row['group_id'],'title':row['group_title'],
                'project':row['project'],'mode':row['conversation_mode'] or plan_mode or row['work_mode'] or 'independent',
                'updated':row['group_updated'],'models':defaultdict(empty),'steps':[]})
            group['updated'] = max(group['updated'], row['updated'])

        by_attempt = defaultdict(lambda: defaultdict(empty))
        end = time.time()
        for reader in (gateway_records, sdk_records, codex_records):
            for receipt in reader(db, 0, end):
                task = tasks.get(receipt['task_id'])
                if task is None:
                    continue
                key = (receipt['provider'], receipt['model'])
                add(by_attempt[receipt['attempt_id']][key], receipt)
                add(groups[task['group_id']]['models'][key], receipt)

        # A versioned execution reservation preserves the requested model when
        # a run has no token receipt or the current task model later changed.
        for attempt in db.execute("""
            SELECT a.id,a.task_id,a.started,a.state,
                json_extract(a.result,'$.ended_at') AS ended,
                (SELECT json_extract(e.payload,'$.configuration_snapshot.model') FROM events e
                 WHERE e.task_id=a.task_id AND e.attempt_id=a.id AND e.kind='execution_reserved'
                 ORDER BY e.seq LIMIT 1) AS model,
                (SELECT SUM(r.reserved_cny) FROM requests r WHERE r.attempt_id=a.id) AS reserved
            FROM attempts a ORDER BY a.started,a.id
        """):
            task = tasks.get(attempt['task_id'])
            if task is None:
                continue
            group = groups[task['group_id']]
            models = by_attempt.get(attempt['id'])
            if not models:
                key = (task['provider'], attempt['model'] or task['model'] or '未知型号')
                models = {key: empty()}
                group['models'].setdefault(key, empty())
            if identity is not None:
                units = plans.get(task['plan_id'], (None,{}))[1]
                discussion = {'expert':'第 0 轮 · 专家讨论','host':'第 0 轮 · 需求澄清'}.get(task['discussion'])
                started, ended = attempt['started'], attempt['ended']
                ended = ended if type(ended) in (int,float) and ended >= started else None
                group['steps'].append({'id': attempt['id'], 'task_id': task['id'],
                    'title': discussion or ('方案准备' if task['preparation_id'] else None)
                    or units.get(task['unit_key']) or (task['title'] if task['unit_key'] else None),
                    'started': started, 'ended': ended, 'state': attempt['state'],
                    'reserved': attempt['reserved'],
                    'models': model_rows(models)})
        for group in groups.values():
            group['models'] = model_rows(group['models'])
        ordered = sorted(groups.values(), key=lambda group: (-group['updated'], group['id']))
        return {'task': ordered[0]} if identity is not None else {'tasks': ordered}
