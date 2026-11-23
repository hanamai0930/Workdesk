"""Private complete child namespace; only actual semantic reconstruction supplies carriers."""
from copy import deepcopy
import json
from .store import digest
from .work_coordinator_handoff_post_round_v3_responsibility_history import _mentions
from .work_coordinator_handoff_post_round_v3_successor_prefix import reconstruct_prefix
from .work_coordinator_handoff_post_round_v3_successor_child import reconstruct
from . import work_coordinator_handoff_post_round_v3_responsibility_records as r


def read_subchain(service, db, original, facts):
    prefix = reconstruct_prefix(service, db, original)
    extension = reconstruct(service, db, original, facts, prefix)
    plan = original['child_plan']; item = plan['item_id']
    raw_rows = list(db.execute('SELECT * FROM collaboration'))
    raw_events = list(db.execute('SELECT * FROM events ORDER BY seq'))
    transitions = prefix['transitions']; versions = [v for _,v,_ in extension['versions']]
    tail = extension['driver']; expected_events = prefix['events'] + extension['events']
    commands = prefix['commands'] + extension['commands']
    identities = {v for row in transitions for v in (row['id'],row['version'])}
    identities.update(v['revision'] for v in versions[1:])
    revisions = [{**v, 'id': 'driver-version:' + v['revision'], 'driver_id': v['id']} for v in versions]
    relevant_revisions = [json.loads(v['data']) for v in raw_rows if v['kind'] == 'work_driver_revision'
                          and json.loads(v['data']).get('plan_id') == plan['id']]
    same = lambda a, b: sorted(map(digest, a)) == sorted(map(digest, b))
    if not same(relevant_revisions, revisions): raise ValueError('V3责任子链完整revision集合不符。')
    expected_rows = {(r.KIND, v['id']): v for v in transitions}
    expected_rows.update({('work_driver_revision', v['id']): v for v in revisions[1:]})
    expected_rows[('work_driver', tail['id'])] = tail
    expected_rows.update({(k,v['id']):v for k,v,_ in extension['carriers']})
    expected_rows.update({(k,v['id']):v for k,v in extension['extra_rows']})
    expected_rows.update({('work_dispatch',v['id']):v for v in extension['dispatches']})
    for raw in raw_rows:
        value = json.loads(raw['data'])
        if _mentions(value, identities, item) and expected_rows.get((raw['kind'], raw['id'])) != value:
            raise ValueError('V3责任存在嵌套、改名、别名或未重建载体。')
        if raw['kind'] == 'work_driver_revision' and value in revisions and raw['id'] != value['id']:
            raise ValueError('V3责任revision数据库主键不符。')
    expected_event_map = {(e['kind'], e['seq']): (e['payload'], None, None) for e in expected_events}
    expected_event_map.update({(e['kind'],e['seq']):(v,e.get('task_id'),e.get('attempt_id')) for _,v,e in extension['carriers']})
    expected_event_map.update(extension['task_events'])
    child_driver_events = []
    for raw in raw_events:
        value = json.loads(raw['payload'])
        if _mentions(value, identities, item) or (raw['kind'] == r.EVENT and value.get('item_id') == item):
            if expected_event_map.get((raw['kind'], raw['seq'])) != (value, raw['task_id'], raw['attempt_id']):
                raise ValueError('V3责任存在孤立、嵌套、迟到或错归属事件：' + raw['kind'] + ':' + str(raw['seq']))
        if raw['kind'].startswith('work_driver_') and value.get('plan_id') == plan['id']:
            child_driver_events.append(value)
        if ((raw['kind'] == 'cooperative_unit_reserved') and
                (value.get('plan_id') == plan['id'] or value.get('cooperative', {}).get('plan_id') == plan['id'])):
            if expected_event_map.get((raw['kind'],raw['seq'])) != (value,raw['task_id'],raw['attempt_id']):
                raise ValueError('V3后继reservation不等于准确语义重建。')
    if not same(child_driver_events, [{**v, 'no_dispatch': True} for v in versions]):
        raise ValueError('V3责任子链driver事件全集不符。')
    expected_commands = {v['id']: v for v in commands}
    for raw in db.execute('SELECT * FROM commands'):
        value = json.loads(raw['receipt'])
        if _mentions(value, identities, item) and expected_commands.get(raw['id']) != dict(raw):
            raise ValueError('V3责任存在未重建或别名命令回执。')
    drivers = [json.loads(v['data']) for v in raw_rows if v['kind'] == 'work_driver'
               and json.loads(v['data']).get('item_id') == item]
    if drivers != [tail] or service.work_drivers.current(db, item) != tail:
        raise ValueError('V3责任当前指针未准确指向子链尾。')
    auth = original['authorization']
    expected_auth_revision = {**auth, 'id': 'unit-auth-version:' + auth['revision']}
    child_auth = [(v['kind'], v['id'], json.loads(v['data'])) for v in raw_rows
        if v['kind'] in {'work_unit_authorization', 'work_unit_authorization_revision'}
        and json.loads(v['data']).get('plan_id') == plan['id']]
    if sorted(child_auth, key=lambda v: v[:2]) != sorted([
            ('work_unit_authorization', auth['id'], auth),
            ('work_unit_authorization_revision', expected_auth_revision['id'], expected_auth_revision)], key=lambda v: v[:2]):
        raise ValueError('V3责任原完整authorization集合不符。')
    if not same([v for v in service.collaboration.all(db,'work_unit_run') if v.get('plan_id')==plan['id']], extension['runs']):
        raise ValueError('V3后继运行全集不等于准确首运行。')
    return {'transitions': deepcopy(transitions), 'versions': deepcopy(versions), 'driver': deepcopy(tail),
        'events': expected_events, 'commands': commands, 'extension': extension, 'subchain_verified': True,
        'full_current_tree_verified': False, 'successor_runtime_open': False, **r.PERMISSIONS}
