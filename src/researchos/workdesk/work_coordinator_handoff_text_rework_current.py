"""Fresh text saved-current plus independent actual live admission."""
from copy import deepcopy
import time

from .store import digest
from .work_coordinator_handoff_historical_assets import _json
from .work_coordinator_handoff_text_rework_parent import require_service_db
from .work_coordinator_handoff_text_rework_saved_records import ref
from .work_coordinator_handoff_text_rework_child_history import TextChildHistory
from .work_readiness_observations import selected_resources, resource_changes
from . import work_coordinator_handoff_text_rework_contracts as c


def require_saved_live(service, db, result, *, reserving=None):
    require_service_db(service, db); s = service; plan = result['child_plan']; h = result['history']
    changes = db.total_changes; item = s.work_items.item(db, plan['item_id'])
    driver, auth = result['driver'], result['authorization']
    if (s.teamwork.current(db, item['id']) != plan or s.work_drivers.current(db, item['id']) != driver or
            s.work_units.authorization(db, driver) != auth or driver['instance'] != s.instance or
            driver['item_revision'] != item['revision'] or driver['generation'] != h['driver']['generation'] or
            item['revision'] != h['task']['revision'] or
            auth['max_attempts_total'] != h['authorization']['max_attempts_total'] or
            auth['dispatch_before'] != h['authorization']['dispatch_before'] or
            auth['driver_generation'] != driver['generation'] or time.time() >= auth['dispatch_before']):
        raise ValueError('文字返工当前方案、业务、同generation责任或原授权已变化。')
    actual_runs = s.work_units.rows(db, item['id']); expected_runs = [f['run'] for f in h['frames']]
    if result.get('pending_run') is not None:
        from .work_coordinator_handoff_text_rework_child_scope import _ChildSend
        if type(reserving) is not _ChildSend or result['pending_run']['command_id'] != reserving.identity:
            raise ValueError('后继未绑定保留仅属于本次准确SEND命令。')
        reserving.require(); expected_runs.append(result['pending_run'])
    usage_runs = [r for r in actual_runs if r != result.get('pending_run')]
    usage = s.work_units.usage(db, usage_runs)
    historical_usage = dict(h['usage']); historical_usage.pop('elapsed_observed_seconds', None)
    usage.pop('elapsed_observed_seconds', None)
    if (sorted((r['id'], digest(r)) for r in actual_runs) != sorted((r['id'], digest(r)) for r in expected_runs) or
            usage != historical_usage or h['usage']['unknown_requests'] or
            len(actual_runs) > auth['max_attempts_total']):
        raise ValueError('文字返工实际完整运行集合或原累计不等于独立重建。')
    s.work_items.project(db, item['project_id'])
    catalog = s.resources.catalog(db)
    if s.teamwork.view(db, item, plan, resource_catalog=catalog)['blockers']:
        raise ValueError('文字返工真实业务或资源受阻。')
    selected = selected_resources(plan, catalog)
    if resource_changes(plan, selected):
        raise ValueError('文字返工原成员、连接或额度引用已变化。')
    members = {m['id']: m for m in selected['members']}
    for choice in [plan['coordinator'], plan['integrator'], *(u['member'] for u in plan['units'])]:
        member = members.get(choice['reference']['member_id'])
        if member is None or any(member[k] != choice[k] for k in ('name', 'role', 'provider', 'model', 'effort', 'tier')):
            raise ValueError('文字返工原成员完整配置已变化。')
    manual = s.work_runtime.manual(db, item['id'])
    if manual and manual['state'] == 'MANUAL':
        raise ValueError('文字返工当前业务由人工控制。')
    for task in sorted(s.work_drivers.tasks(db, item['id'])):
        row = db.execute("SELECT data FROM collaboration WHERE kind='manual_control' AND id=?", ('manual:' + task,)).fetchone()
        if row and _json(row[0])['state'] == 'MANUAL':
            raise ValueError('文字返工关联任务由人工控制。')
        for other in s.work_drivers.item_ids(db, task):
            if other == item['id']: continue
            held = s.work_runtime.manual(db, other)
            if held and held['state'] == 'MANUAL' or s.work_drivers.held(db, other):
                raise ValueError('文字返工共享执行被其它责任持有。')
    reserved = {f['run']['attempt_id'] for f in h['frames'] if f['source_evidence']['state'] == 'RESERVED'}
    if result.get('pending_run'):
        reserved.add(result['pending_run']['attempt_id'])
    issues = s.work_drivers.execution_issues(db, item['id'], driver, excluding=reserved)
    if issues:
        raise ValueError('文字返工实际执行受阻：' + '；'.join(i['message'] for i in issues))
    s.work_flows.require_stopped(db, item['id'])
    baseline = h['material_observation']; s.work_file_rework_applications.require_baseline(baseline)
    if s.work_file_rework_applications.observe(baseline['root'], baseline['paths']) != baseline:
        raise ValueError('文字返工原本机材料已变化。')
    if db.total_changes != changes or s.resources.catalog(db) != catalog:
        raise ValueError('文字返工当前读取期间真实SQL或资源变化。')
    return {'item': deepcopy(item), 'resources': selected, 'material': deepcopy(baseline)}


class TextCurrent:
    def __init__(self, service):
        self.s = service

    @c.closed
    def read(self, db, plan):
        require_service_db(self.s, db)
        link = plan.get(c.LINEAGE_FIELD)
        if not isinstance(link, dict) or link.get('protocol') != c.LINEAGE:
            raise ValueError('文字返工Current须为准确新typed后继方案。')
        rows = [r for r in self.s.collaboration.all(db, c.KINDS[c.REGISTRATION])
            if r.get('snapshot', {}).get('child_plan') == {'id': plan['id'], 'version': plan['version']}]
        if len(rows) != 1:
            raise ValueError('文字返工Current须有唯一实际registration。')
        candidate = rows[0]['snapshot']['candidate']; changes = db.total_changes
        first = TextChildHistory(db, self.s.locations.data, candidate).read()
        live = require_saved_live(self.s, db, first)
        second = TextChildHistory(db, self.s.locations.data, candidate).read()
        live2 = require_saved_live(self.s, db, second)
        keys = ('candidate', 'application', 'registration', 'receipt', 'child_plan', 'driver',
            'authorization', 'history', 'capability', 'proposal', 'response', 'responsibility_receipts', 'send_receipts', 'proposal_receipts')
        def comparable(value):
            value = deepcopy(value)
            value['history']['usage'].pop('elapsed_observed_seconds', None)
            return value
        if (first['child_plan'] != plan or comparable({k: first[k] for k in keys}) != comparable({k: second[k] for k in keys}) or live != live2 or db.total_changes != changes):
            raise ValueError('文字返工两次独立历史、实际资源、完整current或材料字节不一致。')
        return second
