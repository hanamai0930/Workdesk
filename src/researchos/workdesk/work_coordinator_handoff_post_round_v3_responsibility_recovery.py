"""Read-only binding of one original responsibility command to complete Current."""
from copy import deepcopy
import json
import time
import uuid

from .store import digest
from .work_coordinator_handoff_records import fields
from . import work_coordinator_handoff_post_round_v3_responsibility_records as r
from .work_coordinator_handoff_post_round_v3_public import is_v3, responsibility
from .work_coordinator_handoff_post_round_v3_responsibility_current import WorkCoordinatorHandoffPostRoundV3ResponsibilityCurrent as Current

PROTOCOL = 'COORDINATOR_FILE_HANDOFF_RESPONSIBILITY_RECOVERY_V3'


def inspect(service, command):
    fields(command, {'id', 'kind', 'payload'})
    if not isinstance(command['id'], str) or str(uuid.UUID(command['id'])) != command['id']:
        raise ValueError('责任命令须携带规范UUID。')
    if command['kind'] != r.COMMAND:
        raise ValueError('仅核对原V3责任命令。')
    r.request_shape(command['payload'])
    command = deepcopy(command)
    result = {'protocol': PROTOCOL, 'command': command, 'state': 'BLOCKED',
        'can_clear_local': False, 'full_current_tree_verified': False,
        'current': None, 'receipt': None, 'problems': [],
        'checked_at': time.time(), 'no_dispatch': True}
    with service.lock, service.store.lock, service.store.read() as db:
        if service.retiring or (service.locations.data/'retired-target.json').exists():
            result['problems'].append('控制服务正在切换，请保留原责任命令核对。')
            return result
        prior = db.execute('SELECT * FROM commands WHERE id=?', (command['id'],)).fetchone()
        if prior and (prior['kind'] != r.COMMAND or prior['payload_hash'] != digest(command['payload'])):
            result['problems'].append('原命令身份已有不同请求记录，请保留原命令核对。')
            return result
        try:
            item = service.work_items.item(db, command['payload']['id'])
            plan = service.teamwork.current(db, item['id'])
            if not is_v3(plan):
                raise ValueError('当前方案尚无完整V3责任证明。')
            history = Current(service).read(db, plan)
            current = responsibility(service, item, history)
            entries = [e for e in current['entries'] if e['command'] and e['command']['id'] == command['id']]
            if prior:
                receipt = json.loads(prior['receipt'])
                if (len(entries) != 1 or entries[0]['command'] != command or
                        entries[0]['receipt'] != receipt):
                    raise ValueError('原责任命令未准确绑定完整Current与完整提交回执。')
                result.update(state='RECORDED', can_clear_local=True, receipt=receipt)
            else:
                if entries:
                    raise ValueError('原责任链与命令提交记录不一致。')
                if command['payload'] == current['actions'][command['payload']['action']]['request']:
                    result.update(state='CURRENT_REQUEST')
                    result['problems'].append('原请求仍准确对应当前责任，请显式重放原命令核对。')
                else:
                    result.update(state='STALE_LOCAL_REQUEST', can_clear_local=True)
            result.update(current=current, full_current_tree_verified=True)
        except (ValueError, OSError, LookupError, TypeError) as exc:
            result.update(state='BLOCKED', can_clear_local=False, receipt=None, current=None,
                full_current_tree_verified=False, problems=[str(exc)])
        return deepcopy(result)
