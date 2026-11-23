"""Read-only original UUID/receipt recovery under real Service/store locks."""
from copy import deepcopy

from .store import digest
from .work_coordinator_handoff_historical_assets import _json
from .work_coordinator_handoff_text_rework_parent import TextReworkParent
from .work_coordinator_handoff_text_rework_runtime import present
from .work_coordinator_handoff_text_rework_public import _read
from . import work_coordinator_handoff_text_rework_contracts as c


def inspect(service, command):
    c.fields(command, {'id', 'kind', 'payload'}); c._uuid(command['id'])
    allowed = {c.SEND, c.APPLY, c.DRIVER_CHANGE, 'work-item.unit-send', 'work-item.proposal-check'}
    if command['kind'] not in allowed or not isinstance(command['payload'], dict):
        raise ValueError('文字返工恢复只接受原准确命令信封。')
    result = {'command': deepcopy(command), 'state': 'BLOCKED', 'receipt': None,
        'can_clear_local': False, 'problems': [], 'no_dispatch': True}
    with service.lock, service.store.lock, service.store.read() as db:
        if service.retiring or (service.locations.data / 'retired-target.json').exists():
            result['problems'].append('控制服务正在切换，保留原命令核对当前实例。')
            return result
        changes = db.total_changes
        item = service.work_items.item(db, command['payload']['id']); plan = service.teamwork.current(db, item['id'])
        try:
            if present(service, db, plan):
                _read(service, db, plan)
            else:
                TextReworkParent.current(service, db, plan)
        except (ValueError, OSError) as exc:
            result['problems'].append(str(exc))
            return result
        prior = db.execute('SELECT * FROM commands WHERE id=?', (command['id'],)).fetchone()
        if prior is not None:
            if prior['kind'] != command['kind'] or prior['payload_hash'] != digest(command['payload']):
                result['problems'].append('原UUID已有不同命令记录。')
                return result
            receipt = _json(prior['receipt'])
            witnesses = []
            for protocol in (c.RECEIPT, c.PROPOSAL):
                for row in service.collaboration.all(db, c.KINDS[protocol]):
                    if row.get('command_id') == command['id']:
                        body = row['snapshot']
                        expected = body['receipt'] if protocol == c.RECEIPT else body['response']
                        if body['request'] == command['payload'] and expected == receipt:
                            witnesses.append(row)
            if len(witnesses) != 1:
                result['problems'].append('原命令缺少独立完整历史已消费的唯一全文回执见证。')
                return result
            result.update(state='RECORDED', receipt=receipt)
        else:
            # Complete current reconstruction rejected every orphan carrier.
            # Clearing remains an explicit local UI action after this read.
            result.update(state='NOT_COMMITTED_CURRENT_VERIFIED', can_clear_local=True)
        if changes != db.total_changes:
            raise ValueError('文字返工只读恢复期间发生实际SQL写入。')
    return result
