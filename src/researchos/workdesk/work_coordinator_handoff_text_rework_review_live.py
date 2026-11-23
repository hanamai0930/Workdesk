"""Actual resource/execution/material admission after the typed edge exists."""
import time

from .work_coordinator_handoff_historical_assets import _json
from .work_coordinator_handoff_text_rework_parent import require_service_db
from .work_readiness_observations import selected_resources, resource_changes
from . import work_coordinator_handoff_text_rework_contracts as c


def require_live(service, db, capability, driver, *, excluding=frozenset()):
    """Called after independent replay; it never calls or reuses old Current."""
    require_service_db(service, db)
    cap = c.validate_capability(capability); obs = cap['snapshot']['observation']
    plan = obs['parent_plan_snapshot']; s = service
    changes = db.total_changes
    item = s.work_items.item(db, cap['item_id']); s.work_items.project(db, item['project_id'])
    auth = s.work_units.authorization(db, driver)
    if (s.teamwork.current(db, item['id']) != plan or s.work_drivers.current(db, item['id']) != driver or
            auth != obs['authorization'] or driver['instance'] != s.instance or driver['state'] != 'RESERVED' or
            driver['generation'] != obs['driver']['generation'] or driver['item_revision'] != item['revision'] or
            any(driver[k] != auth[k] for k in ('item_id', 'plan_id', 'plan_version')) or
            auth['driver_generation'] != driver['generation'] or time.time() >= auth['dispatch_before']):
        raise ValueError('审阅当前实际责任、原授权、实例或最初截止已变化。')
    runs = s.work_units.rows(db, item['id'])
    if len(runs) > auth['max_attempts_total']:
        raise ValueError('审阅累计实际尝试超过原授权上限。')
    catalog = s.resources.catalog(db)
    if s.teamwork.view(db, item, plan, resource_catalog=catalog)['blockers']:
        raise ValueError('审阅实际业务或资源不可用。')
    selected = selected_resources(plan, catalog)
    if resource_changes(plan, selected):
        raise ValueError('审阅原成员资源引用已变化。')
    members = {m['id']: m for m in selected['members']}
    for choice in [plan['coordinator'], plan['integrator'], *(u['member'] for u in plan['units'])]:
        member = members.get(choice['reference']['member_id'])
        if member is None or any(member[k] != choice[k] for k in ('name', 'role', 'provider', 'model', 'effort', 'tier')):
            raise ValueError('审阅原成员完整配置已变化。')
    held = s.work_runtime.manual(db, item['id'])
    if held and held['state'] == 'MANUAL':
        raise ValueError('审阅业务当前由人工控制。')
    for task in sorted(s.work_drivers.tasks(db, item['id'])):
        manual = db.execute("SELECT data FROM collaboration WHERE kind='manual_control' AND id=?", ('manual:' + task,)).fetchone()
        if manual and _json(manual[0])['state'] == 'MANUAL':
            raise ValueError('审阅关联任务当前由人工控制。')
        for other in s.work_drivers.item_ids(db, task):
            if other == item['id']:
                continue
            manual = s.work_runtime.manual(db, other)
            if manual and manual['state'] == 'MANUAL' or s.work_drivers.held(db, other):
                raise ValueError('审阅共享执行被其它责任持有。')
    issues = s.work_drivers.execution_issues(db, item['id'], driver, excluding=excluding)
    if issues:
        raise ValueError('审阅真实执行受阻：' + '；'.join(i['message'] for i in issues))
    s.work_flows.require_stopped(db, item['id'])
    baseline = obs['material_observation']
    s.work_file_rework_applications.require_baseline(baseline)
    if s.work_file_rework_applications.observe(baseline['root'], baseline['paths']) != baseline:
        raise ValueError('审阅原本机材料已变化。')
    if changes != db.total_changes or s.resources.catalog(db) != catalog:
        raise ValueError('审阅准入读取期间实际SQL或资源变化。')
    return item
