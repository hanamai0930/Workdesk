"""Typed routing and complete text-current projection for shared call sites."""
from copy import deepcopy

from .store import digest
from .work_transitions import source_ref
from .work_coordinator_handoff_text_rework_saved_records import ref
from .work_coordinator_handoff_text_rework_current import TextCurrent
from .work_coordinator_handoff_text_rework_review_replay import ReviewReplay
from . import work_coordinator_handoff_text_rework_child_values as v
from . import work_coordinator_handoff_text_rework_contracts as c


def child(plan):
    return bool(plan and c.LINEAGE_FIELD in plan)


def capability(service, db, plan):
    if plan is None:
        return None
    rows = [r for r in service.collaboration.all(db, c.KINDS[c.CAPABILITY]) if r.get('item_id') == plan['item_id']]
    if not rows:
        return None
    if len(rows) != 1:
        raise ValueError('文字返工业务只能包含唯一准确新能力。')
    row = c.validate_capability(rows[0])
    if not child(plan) and row['snapshot']['parent_plan'] != ref(plan):
        raise ValueError('文字返工能力不属于当前准确父方案。')
    return row


def present(service, db, plan):
    return child(plan) or capability(service, db, plan) is not None


class TextRuntime:
    def __init__(self, service):
        self.s = service

    ref = staticmethod(ref)
    prompt = staticmethod(v.prompt)

    def read(self, db, plan):
        if child(plan):
            result = TextCurrent(self.s).read(db, plan)
            h = deepcopy(result['history'])
            h.update(protocol=c.CURRENT, child_plan=deepcopy(plan), registration=deepcopy(result['registration']),
                application=deepcopy(result['application']), capability=deepcopy(result['capability']),
                text_rework_proposal=deepcopy(result['proposal']), saved_edge_verified=True,
                full_current_tree_verified=True, successor_runtime_open=True, **c.PERMISSIONS)
            return h
        cap = capability(self.s, db, plan)
        result = ReviewReplay(db, self.s.locations.data, ref(cap)).read()
        if result['history'] is None:
            raise ValueError('返工审阅已保留，须等待准确已知退出后读取完整文字返工current。')
        from .work_coordinator_handoff_text_rework_review_live import require_live
        require_live(self.s, db, cap, result['driver'])
        h = deepcopy(result['history']); h.update(protocol=c.CURRENT, capability=cap,
            text_rework_proposal=result['proposal'], full_current_tree_verified=True, **c.PERMISSIONS)
        return h

    def input_for(self, db, item, plan, unit, rows):
        result = TextCurrent(self.s).read(db, plan)
        if item != self.s.work_items.item(db, plan['item_id']) or rows != self.s.work_units.rows(db, plan['item_id']):
            raise ValueError('返工后继输入须使用实际当前业务及完整运行集合。')
        proposal = result['proposal'] if unit['role'] != 'coordinate' else None
        return v.input_value(result, unit, proposal)

    def resolve(self, db, plan, key, rows):
        result = TextCurrent(self.s).read(db, plan)
        unit = next((u for u in plan['units'] if u['key'] == key), None)
        if unit is None or rows != self.s.work_units.rows(db, plan['item_id']):
            raise ValueError('返工后继来源须使用实际原定义及完整运行集合。')
        frame = v.available(result).get(key)
        return {'run': deepcopy(frame['run']) if frame else None,
            'evidence': deepcopy(frame['source_evidence']) if frame else None,
            'inherited': key in plan['result_lineage'], 'source': deepcopy(plan['result_lineage'].get(key)),
            'consumer_plan': ref(plan), 'problems': []}

    def target(self, db, item, plan, unit, pending_source=None):
        result = TextCurrent(self.s).read(db, plan)
        if item != self.s.work_items.item(db, plan['item_id']) or pending_source is not None:
            raise ValueError('返工后继普通目标只接受真实完整已退出前沿。')
        return v.ordinary_target(result, unit)

    def guard_capture(self, db, plan, run):
        raise ValueError('本批文字返工保留原A/B历史，后继没有文件捕获入口。')

    def reservation(self, db, plan, run):
        from .work_coordinator_handoff_text_rework_child_scope import _pending_send
        owner = _pending_send.get()
        if owner is None:
            raise ValueError('后继最终保留只属于本次准确SEND scope。')
        checked = owner.replay(before_receipt=True)
        if checked['history']['frames'][-1]['run'] != run:
            raise ValueError('后继最终保留不等于最后真实运行。')

    def deliver(self, db, plan, run):
        return None

    complete = deliver
