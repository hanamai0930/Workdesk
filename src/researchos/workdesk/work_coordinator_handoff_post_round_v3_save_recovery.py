"""Read-only evidence for explicitly clearing one obsolete local save request."""
import uuid

from .store import digest
from .work_coordinator_handoff_records import fields
from .work_coordinator_handoff_post_round_v3_saved_component import COMMAND
from .work_coordinator_handoff_post_round_v3_saved_records import request_shape


def inspect(service, command):
    fields(command, {'id', 'kind', 'payload'})
    if not isinstance(command['id'], str):
        raise ValueError('命令必须携带有效UUID。')
    uuid.UUID(command['id'])
    if command['kind'] != COMMAND:
        raise ValueError('仅核对原V3保存命令。')
    request_shape(command['payload'])
    result = {'command': command, 'state': 'BLOCKED', 'can_clear_local': False,
        'current_request': None, 'problems': [], 'no_dispatch': True}
    # Same ordering as catalog and commands; one actual read-only DB snapshot.
    with service.lock, service.store.lock, service.store.read() as db:
        if service.retiring or (service.locations.data/'retired-target.json').exists():
            result['problems'].append('控制服务正在切换，请保留原命令并核对当前实例。')
            return result
        prior = db.execute('SELECT kind,payload_hash FROM commands WHERE id=?', (command['id'],)).fetchone()
        if prior:
            if prior['kind'] == COMMAND and prior['payload_hash'] == digest(command['payload']):
                result['state'] = 'RECORDED'
                result['problems'].append('原命令已有提交记录，请核对原保存回执。')
            else:
                result['problems'].append('该命令身份已有不同请求记录，请保留原命令核对。')
            return result
        item = service.work_items.item(db, command['payload']['id'])
        current = (service.work_units.view(db, item) or {}).get('post_round_v3_save')
        if not current or current.get('can_save') is not True or not current.get('request'):
            result['problems'] = (current or {}).get('problems') or ['当前来源尚未通过完整V3保存审计。']
            return result
        request_shape(current['request'])
        result['current_request'] = current['request']
        if command['payload'] == current['request']:
            result['state'] = 'CURRENT_REQUEST'
            result['problems'].append('原请求仍是当前准确请求，请继续使用原命令核对。')
        else:
            result.update(state='STALE_LOCAL_REQUEST', can_clear_local=True)
        return result
